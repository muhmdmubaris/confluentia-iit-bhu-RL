#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -lt 2 ]; then
    echo "Usage: scripts/luna_trials.sh TASK N [--dry-run]" >&2
    exit 3
fi

TASK="$1"
N="$2"

if ! [[ "$N" =~ ^[1-9][0-9]*$ ]]; then
    echo "Usage: scripts/luna_trials.sh TASK N [--dry-run]" >&2
    exit 3
fi

DRY_RUN=0
if [ "$#" -ge 3 ]; then
    if [ "$3" = "--dry-run" ] && [ "$#" -eq 3 ]; then
        DRY_RUN=1
    else
        echo "Usage: scripts/luna_trials.sh TASK N [--dry-run]" >&2
        exit 3
    fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
    for ((seed=0; seed<N; seed++)); do
        if [ -n "${APPEND_FILE:-}" ]; then
            echo "MODE=full TASK_ID=\"$TASK\" SEED=\"$seed\" APPEND_FILE=\"$APPEND_FILE\" docker compose up --build --abort-on-container-exit --exit-code-from harness"
        else
            echo "MODE=full TASK_ID=\"$TASK\" SEED=\"$seed\" docker compose up --build --abort-on-container-exit --exit-code-from harness"
        fi
    done
    exit 0
fi

mkdir -p logs/trials results

TRIALS_CSV="results/trials.csv"
CSV_HEADER="task,seed,termination,total,steps,tokens,seconds,append_file"
RUNS_DATA=$(mktemp)
trap 'rm -f "$RUNS_DATA"' EXIT

for ((seed=0; seed<N; seed++)); do
    PRE_RUN_FILE=$(python3 - << 'PYEOF'
import glob, json
summaries = set(glob.glob("logs/*/summary.json"))
print(json.dumps(list(summaries)))
PYEOF
    )

    rc=0
    MODE=full TASK_ID="$TASK" SEED="$seed" docker compose up --build --abort-on-container-exit --exit-code-from harness > "logs/trials/${TASK}_seed${seed}.out" 2>&1 || rc=$?
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true

    POST_PARSE=$(python3 - "$TASK" "$seed" "$rc" "$PRE_RUN_FILE" "$TRIALS_CSV" << 'PARSE_PYEOF'
import sys, os, glob, json, csv

task = sys.argv[1]
seed = sys.argv[2]
rc = int(sys.argv[3])
pre_summaries = set(json.loads(sys.argv[4])) if sys.argv[4] else set()
trials_csv = sys.argv[5]

explicit_summary = sys.argv[6] if len(sys.argv) > 6 else ""
if explicit_summary:
    summary_path = explicit_summary if os.path.isfile(explicit_summary) else None
else:
    current_summaries = set(glob.glob("logs/*/summary.json"))
    new_summaries = list(current_summaries - pre_summaries)
    summary_path = None
    if new_summaries:
        new_summaries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        summary_path = new_summaries[0]

crashed = False
termination = ""
total = ""
steps = ""
tokens = ""
seconds = ""
append_file = ""

if rc != 0 or not summary_path or not os.path.isfile(summary_path):
    crashed = True
    termination = "crash"
else:
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.loads(f.read())

        termination = str(summary.get("termination", ""))
        if termination == "crash":
            crashed = True

        reward_obj = summary.get("reward")
        if isinstance(reward_obj, dict) and reward_obj.get("total") is not None:
            total = str(reward_obj["total"])

        if summary.get("steps") is not None:
            steps = str(summary["steps"])

        if summary.get("elapsed_s") is not None:
            seconds = str(summary["elapsed_s"])

        append_obj = summary.get("append")
        if isinstance(append_obj, dict) and append_obj.get("file") is not None:
            append_file = str(append_obj["file"])

        episode_path = os.path.join(os.path.dirname(summary_path), "episode.jsonl")
        token_count = 0
        if os.path.isfile(episode_path):
            with open(episode_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(rec, dict):
                        tok = rec.get("tokens")
                        if isinstance(tok, int) and not isinstance(tok, bool):
                            token_count += tok
        tokens = str(token_count)

    except Exception:
        crashed = True
        termination = "crash"

if crashed:
    termination = "crash"
    total = ""
    steps = ""
    tokens = ""
    seconds = ""
    append_file = ""

row = [task, seed, termination, total, steps, tokens, seconds, append_file]

if trials_csv:
    file_exists = os.path.isfile(trials_csv)
    with open(trials_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["task", "seed", "termination", "total", "steps", "tokens", "seconds", "append_file"])
        writer.writerow(row)

print(json.dumps({
    "task": task,
    "seed": seed,
    "termination": termination,
    "total": total,
    "steps": steps,
    "tokens": tokens,
    "seconds": seconds,
    "crashed": 1 if termination == "crash" else 0,
    "solved": 1 if termination == "success" else 0,
}))
PARSE_PYEOF
    )
    echo "$POST_PARSE" >> "$RUNS_DATA"
done

EXIT_CODE=0
python3 - "$RUNS_DATA" << 'EVAL_PYEOF' || EXIT_CODE=$?
import sys, json

runs_data_file = sys.argv[1]
runs = []
with open(runs_data_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            runs.append(json.loads(line))

header = f"{'seed':<6} {'termination':<14} {'total':<8} {'steps':<8} {'tokens':<10} {'seconds':<10}"
print(header)
print("-" * len(header))
for r in runs:
    tot = r["total"] if r["total"] != "" else "-"
    stp = r["steps"] if r["steps"] != "" else "-"
    tok = r["tokens"] if r["tokens"] != "" else "-"
    sec = r["seconds"] if r["seconds"] != "" else "-"
    print(f"{r['seed']:<6} {r['termination']:<14} {tot:<8} {stp:<8} {tok:<10} {sec:<10}")

solved_count = sum(1 for r in runs if r["termination"] == "success")
total_count = len(runs)
print(f"solved {solved_count}/{total_count}")

has_crash = any(r["termination"] == "crash" for r in runs)
if has_crash:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
EVAL_PYEOF

exit "$EXIT_CODE"
