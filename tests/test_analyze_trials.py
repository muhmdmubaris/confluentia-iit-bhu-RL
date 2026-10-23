"""
tests/test_analyze_trials.py
Tests for scripts/analyze_trials.py.
All I/O uses tmp_path fixtures; no files are created in the repo.
"""
import csv
import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the script as a module
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "analyze_trials.py"

def _load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("analyze_trials", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

AT = _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
HEADER = ["task", "seed", "termination", "total", "steps", "tokens", "seconds", "append_file"]


def write_csv(path, rows):
    """Write a trials CSV with the standard header + given rows (list of lists)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r)


def make_episode(path, records):
    """Write a JSONL episode log."""
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def make_summary(path, model="gpt-luna"):
    path.write_text(json.dumps({"model": model}), encoding="utf-8")


def run_main(tmp_path, rows, logs_dirs=None, out_path=None, monkeypatch_price=None):
    """
    Write rows to tmp_path/trials.csv, optionally create log dirs,
    call main(), return (returncode, stdout_str, md_content_or_None).
    """
    trials = tmp_path / "trials.csv"
    write_csv(trials, rows)

    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    if logs_dirs:
        for run_name, (model, episode_records) in logs_dirs.items():
            rdir = logs / run_name
            rdir.mkdir()
            if model is not None:
                make_summary(rdir / "summary.json", model)
            if episode_records is not None:
                make_episode(rdir / "episode.jsonl", episode_records)

    out = out_path or (tmp_path / "TRIALS.md")

    # Capture stdout
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        if monkeypatch_price is not None:
            old_price = AT.PRICE_PER_MILLION_TOKENS_USD
            AT.PRICE_PER_MILLION_TOKENS_USD = monkeypatch_price
        try:
            rc = AT.main([
                "--trials", str(trials),
                "--logs", str(logs),
                "--out", str(out),
            ])
        finally:
            if monkeypatch_price is not None:
                AT.PRICE_PER_MILLION_TOKENS_USD = old_price
    finally:
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout

    md = out.read_text(encoding="utf-8") if out.exists() else None
    return rc, output, md


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_solve_rate_and_means_skip_empty(tmp_path):
    rows = [
        ["led_easy", "0", "success", "1.0", "12", "20000", "60.0", ""],
        ["led_easy", "1", "crash",   "",    "",   "",      "",     ""],
    ]
    rc, out, md = run_main(tmp_path, rows)
    assert rc == 0
    assert "1/2 (50%)" in out
    # mean_steps = 12.0 (crash row skipped)
    assert "12.0" in out
    # mean_seconds = 60.0 (only the success row)
    assert "60.0" in out
    # mean tokens: only one row with 20000
    assert "20000.0" in out


def test_termination_counts_order(tmp_path):
    rows = [
        ["led_easy", "0", "success", "1.0", "10", "1000", "30.0", ""],
        ["led_easy", "1", "success", "1.0", "10", "1000", "30.0", ""],
        ["led_easy", "2", "success", "1.0", "10", "1000", "30.0", ""],
        ["led_easy", "3", "failure", "0.0", "10", "1000", "30.0", ""],
        ["led_easy", "4", "crash",   "",    "",   "",      "",     ""],
    ]
    rc, out, md = run_main(tmp_path, rows)
    # success(3) > failure(1) == crash(1) alphabetically failure < crash — so failure before crash
    assert "success:3 crash:1 failure:1" in out or "success:3 failure:1 crash:1" in out
    # Actually: count desc → success:3 first; then failure and crash both have 1,
    # name-asc tiebreak: "crash" < "failure"
    assert "success:3 crash:1 failure:1" in out


def test_top3_errors_ties_alphabetical(tmp_path):
    rows = [["led_easy", "0", "success", "1.0", "10", "1000", "30.0", ""]]
    # 4 errors all with count 2; alphabetical top-3 should be first three
    error_recs = []
    for kind in ["alpha_err", "beta_err", "gamma_err", "delta_err"]:
        for _ in range(2):
            error_recs.append({
                "task": "led_easy", "step": 1,
                "errors": [{"kind": kind, "detail": "detail"}]
            })
    logs_dirs = {"run0": ("gpt-luna", error_recs)}
    rc, out, md = run_main(tmp_path, rows, logs_dirs=logs_dirs)
    # alphabetical tiebreak: alpha, beta, delta, gamma → top3 are alpha, beta, delta
    assert "alpha_err" in out
    assert "beta_err" in out
    assert "delta_err" in out
    # gamma should NOT be in the top-3 (4th alphabetically)
    assert "gamma_err" not in out


def test_mock_logs_excluded(tmp_path):
    rows = [["led_easy", "0", "success", "1.0", "10", "1000", "30.0", ""]]
    mock_records = [
        {"task": "led_easy", "step": 1,
         "errors": [{"kind": "unknown_tool", "detail": "from_mock"}]}
    ]
    real_records = [
        {"task": "led_easy", "step": 1,
         "errors": [{"kind": "no_tool_call", "detail": "from_real"}]}
    ]
    logs_dirs = {
        "mock_run": ("mock/solver", mock_records),
        "real_run": ("gpt-luna",   real_records),
    }
    rc, out, md = run_main(tmp_path, rows, logs_dirs=logs_dirs)
    assert "from_mock" not in out
    assert "from_real" in out or "no_tool_call" in out


def test_bad_jsonl_lines_skipped(tmp_path):
    rows = [["led_easy", "0", "success", "1.0", "10", "1000", "30.0", ""]]
    bad_lines_records = [
        "NOT JSON AT ALL",
        {"task": "led_easy", "step": 1,
         "errors": [{"kind": "real_error", "detail": "counts"}]},
        "{broken",
        "",
    ]
    # Write manually since mix of str and dict
    logs = tmp_path / "logs" / "run0"
    logs.mkdir(parents=True)
    make_summary(logs / "summary.json", "gpt-luna")
    with open(logs / "episode.jsonl", "w") as f:
        for r in bad_lines_records:
            if isinstance(r, dict):
                f.write(json.dumps(r) + "\n")
            else:
                f.write(r + "\n")
    trials = tmp_path / "trials.csv"
    write_csv(trials, rows)
    out_path = tmp_path / "TRIALS.md"
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = AT.main(["--trials", str(trials), "--logs", str(tmp_path / "logs"), "--out", str(out_path)])
    finally:
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
    assert rc == 0
    assert "real_error" in output


def test_price_none_prints_na(tmp_path):
    rows = [["led_easy", "0", "success", "1.0", "10", "20000", "30.0", ""]]
    old_price = AT.PRICE_PER_MILLION_TOKENS_USD
    AT.PRICE_PER_MILLION_TOKENS_USD = None
    try:
        rc, out, md = run_main(tmp_path, rows)
    finally:
        AT.PRICE_PER_MILLION_TOKENS_USD = old_price
    assert "n/a (price not set)" in out
    assert "n/a (price not set)" in md


def test_price_set_computes_spend(tmp_path):
    # 20000 tokens, $15.0 / 1M => 0.02 * 15 = $0.30
    rows = [["led_easy", "0", "success", "1.0", "10", "20000", "30.0", ""]]
    rc, out, md = run_main(tmp_path, rows, monkeypatch_price=15.0)
    assert "$0.30" in out
    assert "$0.30" in md


def test_missing_trials_exit_3(tmp_path):
    out_path = tmp_path / "TRIALS.md"
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = AT.main([
            "--trials", str(tmp_path / "nonexistent.csv"),
            "--logs", str(tmp_path / "logs"),
            "--out", str(out_path),
        ])
        err = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr
        sys.stdout = old_stdout
    assert rc == 3
    assert "error:" in err


def test_header_only_no_report(tmp_path):
    trials = tmp_path / "trials.csv"
    trials.write_text("task,seed,termination,total,steps,tokens,seconds,append_file\n", encoding="utf-8")
    out_path = tmp_path / "TRIALS.md"
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        rc = AT.main(["--trials", str(trials), "--logs", str(tmp_path / "logs"), "--out", str(out_path)])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    assert rc == 0
    assert "no trials yet" in output
    assert not out_path.exists()


def test_markers_preserve_handwritten_text(tmp_path):
    rows = [["led_easy", "0", "success", "1.0", "10", "1000", "30.0", ""]]
    out_path = tmp_path / "TRIALS.md"
    # Pre-write a file with both markers and hand-written content before/after
    existing = (
        "# Trials\n\n"
        "Hand-written budget tally: $12.50 spent so far.\n\n"
        "<!-- BEGIN GENERATED -->\n"
        "OLD GENERATED CONTENT\n"
        "<!-- END GENERATED -->\n\n"
        "## Notes\n"
        "This is a hand-written note after the generated section.\n"
    )
    out_path.write_text(existing, encoding="utf-8")
    rc, out, md = run_main(tmp_path, rows, out_path=out_path)
    assert rc == 0
    assert "Hand-written budget tally" in md
    assert "This is a hand-written note" in md
    assert "OLD GENERATED CONTENT" not in md
    assert "<!-- BEGIN GENERATED -->" in md
    assert "<!-- END GENERATED -->" in md
    # The generated section is present
    assert "led_easy" in md


def test_existing_file_without_markers_is_appended_not_overwritten(tmp_path):
    rows = [["led_easy", "0", "success", "1.0", "10", "1000", "30.0", ""]]
    out_path = tmp_path / "TRIALS.md"
    original = "# Trials\n\nBudget: $5.00 (hand-written).\n"
    out_path.write_text(original, encoding="utf-8")
    rc, out, md = run_main(tmp_path, rows, out_path=out_path)
    assert rc == 0
    # Original content must be intact
    assert "Budget: $5.00 (hand-written)." in md
    # New markers appended
    assert "<!-- BEGIN GENERATED -->" in md
    assert "<!-- END GENERATED -->" in md
    assert "led_easy" in md
    # File starts with the original content
    assert md.startswith("# Trials")


@pytest.mark.parametrize("k,n,expected", [
    (5, 5, "PASS (>= 4/5)"),
    (4, 5, "PASS (>= 4/5)"),
    (3, 5, "BELOW TARGET"),
    (2, 5, "WARN: below 3/5, tell Pranav"),
    (0, 5, "WARN: below 3/5, tell Pranav"),
    (3, 3, "INCOMPLETE (need 5 runs)"),
])
def test_gate2_lines(tmp_path, k, n, expected):
    rows = []
    for i in range(k):
        rows.append(["led_easy", str(i), "success", "1.0", "10", "1000", "30.0", ""])
    for i in range(n - k):
        rows.append(["led_easy", str(k + i), "failure", "0.0", "10", "1000", "30.0", ""])
    rc, out, md = run_main(tmp_path, rows)
    assert rc == 0
    assert expected in out
    assert expected in md


def test_stdout_and_md_agree(tmp_path):
    """The same numbers (solve rate, means) must appear in both stdout and TRIALS.md."""
    rows = [
        ["led_easy", "0", "success", "1.0", "12", "20000", "60.0", ""],
        ["led_easy", "1", "failure", "0.5",  "8", "15000", "45.0", ""],
        ["led_easy", "2", "crash",   "",     "",   "",      "",     ""],
    ]
    rc, out, md = run_main(tmp_path, rows)
    assert rc == 0
    # Solve rate 1/3
    assert "1/3" in out
    assert "1/3" in md
    # mean_steps = (12 + 8) / 2 = 10.0
    assert "10.0" in out
    assert "10.0" in md
    # n/a (price not set)
    assert "n/a (price not set)" in out
    assert "n/a (price not set)" in md
