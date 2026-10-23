"""
scripts/analyze_trials.py
Turn results/trials.csv and logs/*/episode.jsonl into a per-task statistics table.

Usage:
    python3 scripts/analyze_trials.py [--trials results/trials.csv]
                                      [--logs logs]
                                      [--out results/TRIALS.md]
"""
import argparse
import collections
import csv
import glob
import json
import os
import sys
from pathlib import Path

# set from the official GPT-Luna price sheet; never guess
PRICE_PER_MILLION_TOKENS_USD = None

_MARKER_BEGIN = "<!-- BEGIN GENERATED -->"
_MARKER_END = "<!-- END GENERATED -->"

CSV_COLUMNS = ["task", "seed", "termination", "total", "steps", "tokens", "seconds", "append_file"]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_float(s):
    """Return float or None; never treats empty string as 0."""
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_int(s):
    """Return int or None."""
    v = _parse_float(s)
    return int(v) if v is not None else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_trials(path):
    """Load trials CSV.  Returns list of row dicts.  Raises FileNotFoundError."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    rows = []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _load_errors(logs_dir, tasks_in_csv):
    """
    Walk logs_dir/*/episode.jsonl.  Skip directories whose summary.json has
    model starting with "mock/".  An unreadable summary.json keeps the log.
    Only tasks that appear in trials.csv are reported.

    Returns dict[task -> Counter[key -> int]].
    """
    task_set = set(tasks_in_csv)
    counts = {t: collections.Counter() for t in task_set}

    logs_path = Path(logs_dir)
    if not logs_path.is_dir():
        return counts

    for run_dir in sorted(logs_path.iterdir()):
        if not run_dir.is_dir():
            continue

        # Check summary.json for mock/ model — unreadable summary keeps the log
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            try:
                with open(summary_path, encoding="utf-8") as sf:
                    summary = json.loads(sf.read())
                model = summary.get("model", "")
                if isinstance(model, str) and model.startswith("mock/"):
                    continue
            except Exception:
                pass  # unreadable summary: keep the log

        episode_path = run_dir / "episode.jsonl"
        if not episode_path.exists():
            continue

        try:
            with open(episode_path, encoding="utf-8", errors="replace") as ef:
                for line in ef:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(record, dict):
                        continue
                    task = record.get("task")
                    if not isinstance(task, str) or task not in task_set:
                        continue
                    errors = record.get("errors")
                    if not isinstance(errors, list):
                        continue
                    for entry in errors:
                        if not isinstance(entry, dict):
                            continue
                        kind = entry.get("kind")
                        kind = str(kind) if kind is not None else "?"
                        detail = entry.get("detail")
                        detail = str(detail) if detail is not None else ""
                        # flatten \r and \n BEFORE cutting to 80 chars
                        detail_flat = detail.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
                        detail_cut = detail_flat[:80]
                        key = f"{kind}: {detail_cut}"
                        counts[task][key] += 1
        except Exception:
            continue

    return counts


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _compute_task_stats(rows):
    """
    Returns dict[task -> stats] where stats has:
        runs, solved, mean_steps (float|None), mean_tokens (float|None),
        mean_seconds (float|None), termination_counts (Counter),
        total_tokens (int)  -- sum of non-empty tokens cells
    """
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[row["task"]].append(row)

    stats = {}
    for task, task_rows in sorted(grouped.items()):
        runs = len(task_rows)
        solved = sum(1 for r in task_rows if r.get("termination") == "success")

        steps_vals = [v for r in task_rows if (v := _parse_float(r.get("steps"))) is not None]
        tokens_vals = [v for r in task_rows if (v := _parse_float(r.get("tokens"))) is not None]
        seconds_vals = [v for r in task_rows if (v := _parse_float(r.get("seconds"))) is not None]

        mean_steps = sum(steps_vals) / len(steps_vals) if steps_vals else None
        mean_tokens = sum(tokens_vals) / len(tokens_vals) if tokens_vals else None
        mean_seconds = sum(seconds_vals) / len(seconds_vals) if seconds_vals else None
        total_tokens = int(sum(tokens_vals))  # for spend; only non-empty cells

        term_counts = collections.Counter(r.get("termination", "") for r in task_rows)

        stats[task] = {
            "runs": runs,
            "solved": solved,
            "mean_steps": mean_steps,
            "mean_tokens": mean_tokens,
            "mean_seconds": mean_seconds,
            "total_tokens": total_tokens,
            "termination_counts": term_counts,
        }
    return stats


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_mean(val, decimals=1):
    if val is None:
        return "-"
    return f"{val:.{decimals}f}"


def _format_solve_rate(k, n):
    pct = round(100 * k / n) if n else 0
    return f"{k}/{n} ({pct}%)"


def _format_termination_counts(counts):
    """sorted by count desc, name asc."""
    pairs = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return " ".join(f"{k}:{v}" for k, v in pairs)


def _format_spend(total_tokens, price):
    if price is None:
        return "n/a (price not set)"
    return f"${total_tokens / 1e6 * price:.2f}"


def _gate2_line(rows_led):
    n = len(rows_led)
    k = sum(1 for r in rows_led if r.get("termination") == "success")
    header = f"Gate 2 (led_easy): {k}/{n} solved"
    if n < 5:
        verdict = "INCOMPLETE (need 5 runs)"
    elif k >= 4:
        verdict = "PASS (>= 4/5)"
    elif k == 3:
        verdict = "BELOW TARGET"
    else:  # k < 3
        verdict = "WARN: below 3/5, tell Pranav"
    return f"{header}\n{verdict}"


def _top3_errors(counter):
    """Return top-3 entries sorted by count desc, key asc as list of (key, count)."""
    if not counter:
        return []
    # sort by (-count, key) to get count-desc, name-asc tiebreak
    ranked = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    return ranked[:3]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_plain_table(task_stats, error_counts, all_rows, price):
    """Plain-text aligned table for stdout."""
    lines = []

    # Header
    col_w = [18, 6, 8, 16, 12, 12, 10, 26, 0]
    header = (
        f"{'task':<18}{'runs':>6}  {'solved':>6}  {'solve_rate':<16}"
        f"{'mean_steps':>12}  {'mean_tok':>12}  {'mean_s':>10}  "
        f"{'spend':<26}  terminations"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for task in sorted(task_stats):
        s = task_stats[task]
        spend = _format_spend(s["total_tokens"], price)
        terms = _format_termination_counts(s["termination_counts"])
        line = (
            f"{task:<18}{s['runs']:>6}  {s['solved']:>6}  "
            f"{_format_solve_rate(s['solved'], s['runs']):<16}"
            f"{_format_mean(s['mean_steps']):>12}  "
            f"{_format_mean(s['mean_tokens']):>12}  "
            f"{_format_mean(s['mean_seconds']):>10}  "
            f"{spend:<26}  {terms}"
        )
        lines.append(line)

    lines.append("")
    lines.append("Top tool errors (per task, skipping mock/ runs):")
    for task in sorted(task_stats):
        top = _top3_errors(error_counts.get(task, collections.Counter()))
        if top:
            parts = ", ".join(f"{k} ({c})" for k, c in top)
            lines.append(f"  {task}: {parts}")
        else:
            lines.append(f"  {task}: -")

    total_tokens_all = sum(s["total_tokens"] for s in task_stats.values())
    lines.append("")
    lines.append(
        "Note: errors aggregated per task across all episode.jsonl logs found; "
        "only tasks in trials.csv are reported. "
        f"Spend uses sum of non-empty tokens column ({total_tokens_all} tok total)."
    )
    return "\n".join(lines)


def _render_md_table(task_stats, error_counts, all_rows, price):
    """GitHub pipe table for TRIALS.md."""
    lines = []
    lines.append("| task | runs | solved | solve_rate | mean_steps | mean_tok | mean_s | spend | terminations |")
    lines.append("|------|-----:|-------:|:-----------|----------:|---------:|-------:|:------|:-------------|")

    for task in sorted(task_stats):
        s = task_stats[task]
        spend = _format_spend(s["total_tokens"], price)
        terms = _format_termination_counts(s["termination_counts"])
        lines.append(
            f"| {task} | {s['runs']} | {s['solved']} | "
            f"{_format_solve_rate(s['solved'], s['runs'])} | "
            f"{_format_mean(s['mean_steps'])} | "
            f"{_format_mean(s['mean_tokens'])} | "
            f"{_format_mean(s['mean_seconds'])} | "
            f"{spend} | {terms} |"
        )

    lines.append("")
    lines.append("**Top tool errors (per task, skipping mock/ runs):**")
    lines.append("")
    for task in sorted(task_stats):
        top = _top3_errors(error_counts.get(task, collections.Counter()))
        if top:
            parts = ", ".join(f"{k} ({c})" for k, c in top)
            lines.append(f"- **{task}**: {parts}")
        else:
            lines.append(f"- **{task}**: -")

    total_tokens_all = sum(s["total_tokens"] for s in task_stats.values())
    lines.append("")
    lines.append(
        "_Note: errors aggregated per task across all episode.jsonl logs found; "
        "only tasks in trials.csv are reported. "
        f"Spend uses sum of non-empty tokens column ({total_tokens_all} tok total)._"
    )
    return "\n".join(lines)


def _render_section(task_stats, error_counts, all_rows, price, n_rows):
    """Full content that goes between the markers (for the .md file)."""
    lines = []
    lines.append(f"Generated from results/trials.csv ({n_rows} rows)")
    lines.append("")
    lines.append(_render_md_table(task_stats, error_counts, all_rows, price))
    lines.append("")

    # Gate 2
    led_rows = [r for r in all_rows if r.get("task") == "led_easy"]
    if led_rows:
        lines.append(_gate2_line(led_rows))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def _write_md(out_path, section_body):
    """
    Write section_body between markers.

    Rules (NO truncation of existing content):
    - File does not exist: create with `# Trials` heading + marker-wrapped section.
    - File exists, has BOTH markers (BEGIN before END): replace only that section.
    - File exists, lacks markers: APPEND the marker-wrapped section to the end.
    """
    wrapped = f"{_MARKER_BEGIN}\n{section_body}\n{_MARKER_END}\n"
    p = Path(out_path)

    if not p.exists():
        content = f"# Trials\n\n{wrapped}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return

    existing = p.read_text(encoding="utf-8")
    bi = existing.find(_MARKER_BEGIN)
    if bi == -1:
        # No markers at all — append
        sep = "" if existing.endswith("\n") else "\n"
        p.write_text(existing + sep + wrapped, encoding="utf-8")
        return

    ei = existing.find(_MARKER_END, bi + len(_MARKER_BEGIN))
    if ei == -1:
        # BEGIN found but no END after it — append
        sep = "" if existing.endswith("\n") else "\n"
        p.write_text(existing + sep + wrapped, encoding="utf-8")
        return

    # Both markers present — replace only the section between them (inclusive)
    end_of_end = ei + len(_MARKER_END)
    # skip one trailing newline if present
    if end_of_end < len(existing) and existing[end_of_end] == "\n":
        end_of_end += 1
    new_content = existing[:bi] + wrapped + existing[end_of_end:]
    p.write_text(new_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(prog="analyze_trials.py")
    parser.add_argument("--trials", default="results/trials.csv")
    parser.add_argument("--logs", default="logs")
    parser.add_argument("--out", default="results/TRIALS.md")
    args = parser.parse_args(argv)

    # Load trials
    try:
        rows = _load_trials(args.trials)
    except FileNotFoundError:
        print(f"error: {args.trials} not found", file=sys.stderr)
        return 3

    if not rows:
        print("no trials yet")
        return 0

    # Compute stats
    task_stats = _compute_task_stats(rows)
    tasks_in_csv = list(task_stats.keys())

    # Load errors (price constant is module-level so tests can monkeypatch)
    price = PRICE_PER_MILLION_TOKENS_USD
    error_counts = _load_errors(args.logs, tasks_in_csv)

    # Render plain-text table → stdout
    plain = _render_plain_table(task_stats, error_counts, rows, price)
    print(plain)

    # Gate 2 on stdout
    led_rows = [r for r in rows if r.get("task") == "led_easy"]
    if led_rows:
        print()
        print(_gate2_line(led_rows))

    # Write TRIALS.md
    section = _render_section(task_stats, error_counts, rows, price, len(rows))
    _write_md(args.out, section)

    return 0


if __name__ == "__main__":
    sys.exit(main())
