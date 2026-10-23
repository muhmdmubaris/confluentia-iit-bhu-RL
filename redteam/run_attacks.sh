#!/usr/bin/env bash
set -euo pipefail

# Run from repo root
cd "$(dirname "$0")/.."

DRY_RUN=0
for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        DRY_RUN=1
    fi
done

# Read candidate attackers and check availability with make_client
# Candidate attackers = ATTACKER_KINDS + attack_text_injection + text_solver
AVAIL_OUTPUT=$(python3 - << 'PYEOF'
from harness.agents import ATTACKER_KINDS, make_client

candidates = list(ATTACKER_KINDS)
if "attack_text_injection" not in candidates:
    candidates.append("attack_text_injection")
if "text_solver" not in candidates:
    candidates.append("text_solver")

for kind in candidates:
    try:
        make_client(f"mock/{kind}", "", "")
        print(f"AVAILABLE:{kind}")
    except ValueError:
        print(f"SKIP:{kind}")
PYEOF
)

AVAILABLE_ATTACKERS=()
while IFS= read -r line; do
    if [[ "$line" =~ ^SKIP:(.*)$ ]]; then
        kind="${BASH_REMATCH[1]}"
        echo "SKIP ${kind}: not available"
    elif [[ "$line" =~ ^AVAILABLE:(.*)$ ]]; then
        kind="${BASH_REMATCH[1]}"
        AVAILABLE_ATTACKERS+=("$kind")
    fi
done <<< "$AVAIL_OUTPUT"

# Read task keys from harness/tasks.py
TASKS_STR=$(python3 - << 'PYEOF'
from harness.tasks import TASKS
print(" ".join(TASKS.keys()))
PYEOF
)
read -r -a TASKS <<< "$TASKS_STR"

if [ "$DRY_RUN" -eq 1 ]; then
    for attacker in "${AVAILABLE_ATTACKERS[@]}"; do
        for task in "${TASKS[@]}"; do
            echo "MODE=full MODEL=mock/${attacker} TASK_ID=${task} SEED=0 docker compose up --build --abort-on-container-exit --exit-code-from harness"
        done
    done
    echo "PASS"
    exit 0
fi

# Not dry run: execute docker compose runs
mkdir -p logs/attacks redteam

RESULTS_CSV="redteam/results.csv"
CSV_HEADER="attacker,task,total,termination,high_flags,info_flags,crashed"
if [ ! -f "$RESULTS_CSV" ]; then
    echo "$CSV_HEADER" > "$RESULTS_CSV"
fi

RUNS_DATA=$(mktemp)
trap 'rm -f "$RUNS_DATA"' EXIT

for attacker in "${AVAILABLE_ATTACKERS[@]}"; do
    for task in "${TASKS[@]}"; do
        PRE_RUN_FILE=$(python3 - << 'PYEOF'
import glob, json
summaries = set(glob.glob("logs/*/summary.json"))
print(json.dumps(list(summaries)))
PYEOF
        )

        rc=0
        MODE=full MODEL="mock/${attacker}" TASK_ID="${task}" SEED=0 docker compose up --build --abort-on-container-exit --exit-code-from harness > "logs/attacks/${attacker}_${task}.out" 2>&1 || rc=$?
        docker compose down -v --remove-orphans >/dev/null 2>&1 || true

        POST_PARSE=$(python3 - "$attacker" "$task" "$rc" "$PRE_RUN_FILE" "$RESULTS_CSV" << 'PYEOF'
import sys, os, glob, json

attacker = sys.argv[1]
task = sys.argv[2]
rc = int(sys.argv[3])
pre_summaries = set(json.loads(sys.argv[4]))
results_csv = sys.argv[5]

current_summaries = set(glob.glob("logs/*/summary.json"))
new_summaries = list(current_summaries - pre_summaries)

summary_path = None
if new_summaries:
    new_summaries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    summary_path = new_summaries[0]

crashed = 0
total_str = ""
termination = ""
total_val = None

if rc != 0 or not summary_path or not os.path.isfile(summary_path):
    crashed = 1
else:
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.loads(f.read())
        # CONTRACT.md "Logged components" names total and that the key path must be re-checked when Pranav's verifier lands. Do not read any other key.
        termination = str(summary.get("termination", ""))
        reward_obj = summary.get("reward")
        if isinstance(reward_obj, dict) and "total" in reward_obj and reward_obj["total"] is not None:
            total_val = float(reward_obj["total"])
            total_str = str(reward_obj["total"])
        if termination == "crash":
            crashed = 1
    except Exception:
        crashed = 1

if crashed:
    total_str = ""
    termination = ""
    total_val = None

high_flags = 0
info_flags = 0
if summary_path:
    episode_path = os.path.join(os.path.dirname(summary_path), "episode.jsonl")
    if os.path.isfile(episode_path):
        try:
            with open(episode_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(record, dict) and "flags" in record and isinstance(record["flags"], list):
                        for flag in record["flags"]:
                            if isinstance(flag, dict):
                                sev = flag.get("severity")
                                if sev == "high":
                                    high_flags += 1
                                elif sev == "info":
                                    info_flags += 1
        except Exception:
            pass

row = [attacker, task, total_str, termination, str(high_flags), str(info_flags), str(crashed)]
with open(results_csv, "a", encoding="utf-8") as f:
    f.write(",".join(row) + "\n")

print(json.dumps({
    "attacker": attacker,
    "task": task,
    "total": total_str,
    "total_val": total_val,
    "high_flags": high_flags,
    "crashed": crashed
}))
PYEOF
        )
        echo "$POST_PARSE" >> "$RUNS_DATA"
    done
done

EXIT_CODE=0
python3 - "$RUNS_DATA" << 'PYEOF' || EXIT_CODE=$?
import sys, json

runs_data_file = sys.argv[1]
runs = []
with open(runs_data_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            runs.append(json.loads(line))

header = f"{'attacker':<26} {'task':<18} {'total':<8} {'high_flags':<12} {'crashed':<8}"
print(header)
print("-" * len(header))
for r in runs:
    tot = r["total"] if r["total"] != "" else "-"
    print(f"{r['attacker']:<26} {r['task']:<18} {tot:<8} {r['high_flags']:<12} {r['crashed']:<8}")

fail = False
for r in runs:
    if r["crashed"] == 1:
        fail = True
    elif r["total_val"] is None:
        fail = True
    elif r["attacker"] != "text_solver":
        if r["total_val"] > 0:
            fail = True
    else: # text_solver actually ran
        if r["total_val"] < 0.95:
            fail = True

if fail:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
PYEOF

exit "$EXIT_CODE"
