import csv
import json
import os
import subprocess
import sys
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "luna_trials.sh")


def run_parse_heredoc(task, seed, rc, pre_summaries_json, csv_path, explicit_summary_path=""):
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    start_marker = "<< 'PARSE_PYEOF'\n"
    end_marker = "\nPARSE_PYEOF\n"
    start_idx = content.find(start_marker)
    assert start_idx != -1, "Could not find PARSE_PYEOF in luna_trials.sh"
    end_idx = content.find(end_marker, start_idx)
    assert end_idx != -1, "Could not find end of PARSE_PYEOF in luna_trials.sh"
    py_code = content[start_idx + len(start_marker):end_idx]

    args = [sys.executable, "-", str(task), str(seed), str(rc), pre_summaries_json, str(csv_path)]
    if explicit_summary_path:
        args.append(str(explicit_summary_path))

    proc = subprocess.run(args, input=py_code, text=True, capture_output=True)
    assert proc.returncode == 0, f"Parser failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def test_strict_mode():
    assert os.path.isfile(SCRIPT_PATH), f"Missing script: {SCRIPT_PATH}"
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert content.startswith("#!/usr/bin/env bash"), "Script must start with #!/usr/bin/env bash"
    assert "set -euo pipefail" in content, "Script must include set -euo pipefail"


def test_bash_syntax_ok():
    proc = subprocess.run(["bash", "-n", SCRIPT_PATH], capture_output=True, text=True)
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


def test_usage_errors_exit_3():
    # 0 args
    p0 = subprocess.run(["bash", SCRIPT_PATH], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p0.returncode == 3
    assert "Usage:" in p0.stderr

    # 1 arg
    p1 = subprocess.run(["bash", SCRIPT_PATH, "led_easy"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p1.returncode == 3
    assert "Usage:" in p1.stderr

    # N is 0
    p2 = subprocess.run(["bash", SCRIPT_PATH, "led_easy", "0"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p2.returncode == 3
    assert "Usage:" in p2.stderr

    # N is negative
    p3 = subprocess.run(["bash", SCRIPT_PATH, "led_easy", "-1"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p3.returncode == 3
    assert "Usage:" in p3.stderr

    # N is not integer
    p4 = subprocess.run(["bash", SCRIPT_PATH, "led_easy", "abc"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p4.returncode == 3
    assert "Usage:" in p4.stderr

    # Invalid 3rd arg
    p5 = subprocess.run(["bash", SCRIPT_PATH, "led_easy", "2", "--not-dry-run"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p5.returncode == 3
    assert "Usage:" in p5.stderr

    # Extra 4th arg
    p6 = subprocess.run(["bash", SCRIPT_PATH, "led_easy", "2", "--dry-run", "extra"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert p6.returncode == 3
    assert "Usage:" in p6.stderr


def test_dry_run_prints_one_line_per_seed_and_creates_no_files(tmp_path):
    results_path = os.path.join(REPO_ROOT, "results")
    trials_log_path = os.path.join(REPO_ROOT, "logs", "trials")
    results_existed = os.path.exists(results_path)
    trials_log_existed = os.path.exists(trials_log_path)

    # Dry run without APPEND_FILE
    env = os.environ.copy()
    env.pop("APPEND_FILE", None)
    proc = subprocess.run(
        ["bash", SCRIPT_PATH, "led_easy", "2", "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env
    )
    assert proc.returncode == 0
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 2
    assert lines[0] == 'MODE=full TASK_ID="led_easy" SEED="0" docker compose up --build --abort-on-container-exit --exit-code-from harness'
    assert lines[1] == 'MODE=full TASK_ID="led_easy" SEED="1" docker compose up --build --abort-on-container-exit --exit-code-from harness'

    # Dry run with APPEND_FILE
    env["APPEND_FILE"] = "inputs/append.txt"
    proc_append = subprocess.run(
        ["bash", SCRIPT_PATH, "led_easy", "2", "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env
    )
    assert proc_append.returncode == 0
    lines_app = proc_append.stdout.strip().splitlines()
    assert len(lines_app) == 2
    assert lines_app[0] == 'MODE=full TASK_ID="led_easy" SEED="0" APPEND_FILE="inputs/append.txt" docker compose up --build --abort-on-container-exit --exit-code-from harness'
    assert lines_app[1] == 'MODE=full TASK_ID="led_easy" SEED="1" APPEND_FILE="inputs/append.txt" docker compose up --build --abort-on-container-exit --exit-code-from harness'

    # Verify no directories or files were created by dry-run
    if not results_existed:
        assert not os.path.exists(results_path)
    if not trials_log_existed:
        assert not os.path.exists(trials_log_path)


def test_csv_header_exact():
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    expected_header = "task,seed,termination,total,steps,tokens,seconds,append_file"
    assert expected_header in content


def test_row_building_from_fixtures(tmp_path):
    csv_file = tmp_path / "trials.csv"

    # Case 1: Success row
    sum_dir1 = tmp_path / "run1"
    sum_dir1.mkdir()
    sum_file1 = sum_dir1 / "summary.json"
    sum_file1.write_text(json.dumps({
        "termination": "success",
        "reward": {"total": 1.0},
        "steps": 5,
        "elapsed_s": 12.5,
        "append": {"file": "inputs/append.txt"}
    }), encoding="utf-8")
    ep_file1 = sum_dir1 / "episode.jsonl"
    ep_file1.write_text(
        json.dumps({"tokens": 100}) + "\n" +
        json.dumps({"tokens": 200}) + "\n",
        encoding="utf-8"
    )

    r1 = run_parse_heredoc("led_easy", 0, 0, "[]", str(csv_file), str(sum_file1))
    assert r1["termination"] == "success"
    assert r1["total"] == "1.0"
    assert r1["steps"] == "5"
    assert r1["tokens"] == "300"
    assert r1["seconds"] == "12.5"

    # Case 2: Crash row (rc != 0 or missing summary)
    r2 = run_parse_heredoc("led_easy", 0, 1, "[]", str(csv_file), str(sum_file1))
    assert r2["termination"] == "crash"
    assert r2["total"] == ""
    assert r2["steps"] == ""
    assert r2["tokens"] == ""
    assert r2["seconds"] == ""

    # Case 3: Null append
    sum_dir3 = tmp_path / "run3"
    sum_dir3.mkdir()
    sum_file3 = sum_dir3 / "summary.json"
    sum_file3.write_text(json.dumps({
        "termination": "success",
        "reward": {"total": 1.0},
        "steps": 5,
        "elapsed_s": 12.5,
        "append": None
    }), encoding="utf-8")
    ep_file3 = sum_dir3 / "episode.jsonl"
    ep_file3.write_text(json.dumps({"tokens": 300}) + "\n", encoding="utf-8")

    r3 = run_parse_heredoc("led_easy", 0, 0, "[]", str(csv_file), str(sum_file3))
    assert r3["termination"] == "success"

    # Case 4: Token summing resilience:
    # one bad JSON line, one bool, and one non-int tokens value skipped
    sum_dir4 = tmp_path / "run4"
    sum_dir4.mkdir()
    sum_file4 = sum_dir4 / "summary.json"
    sum_file4.write_text(json.dumps({
        "termination": "failure",
        "reward": {"total": 0.0},
        "steps": 4,
        "elapsed_s": 8.0,
        "append": None
    }), encoding="utf-8")
    ep_file4 = sum_dir4 / "episode.jsonl"
    ep_file4.write_text(
        json.dumps({"tokens": 100}) + "\n" +
        "{not valid json\n" +
        json.dumps({"tokens": True}) + "\n" +
        json.dumps({"tokens": "200"}) + "\n" +
        json.dumps({"tokens": 250}) + "\n" +
        json.dumps({"other": "value"}) + "\n",
        encoding="utf-8"
    )

    r4 = run_parse_heredoc("led_easy", 1, 0, "[]", str(csv_file), str(sum_file4))
    assert r4["tokens"] == "350"

    # Verify the CSV file contents written by python's csv.writer
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))

    assert reader[0] == ["task", "seed", "termination", "total", "steps", "tokens", "seconds", "append_file"]
    assert reader[1] == ["led_easy", "0", "success", "1.0", "5", "300", "12.5", "inputs/append.txt"]
    assert reader[2] == ["led_easy", "0", "crash", "", "", "", "", ""]
    assert reader[3] == ["led_easy", "0", "success", "1.0", "5", "300", "12.5", ""]
    assert reader[4] == ["led_easy", "1", "failure", "0.0", "4", "350", "8.0", ""]
