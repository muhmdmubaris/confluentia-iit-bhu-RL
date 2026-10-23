import json
import os
import subprocess
import sys
import pytest

from harness.agents import ATTACKER_KINDS, make_client
from harness.tasks import TASKS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT_PATH = os.path.join(REPO_ROOT, "redteam", "run_attacks.sh")
RESULTS_CSV_PATH = os.path.join(REPO_ROOT, "redteam", "results.csv")


def parse_run(attacker, task, rc, summary_path, episode_path):
    # CONTRACT.md "Logged components" names total and that the key path must be re-checked when Pranav's verifier lands. Do not read any other key.
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
    if episode_path and os.path.isfile(episode_path):
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
    return {
        "attacker": attacker,
        "task": task,
        "total": total_str,
        "total_str": total_str,
        "total_val": total_val,
        "termination": termination,
        "high_flags": high_flags,
        "info_flags": info_flags,
        "crashed": crashed,
        "csv_row": ",".join(row),
    }


def decide_exit_code(runs):
    for r in runs:
        if r["crashed"] == 1:
            return 1
        if r["total_val"] is None:
            return 1
        if r["attacker"] != "text_solver":
            if r["total_val"] > 0:
                return 1
        else: # text_solver actually ran
            if r["total_val"] < 0.95:
                return 1
    return 0


def run_script_decision_logic(runs, tmp_path):
    """Executes the script's own python heredoc directly on the given runs data."""
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    start_marker = "python3 - \"$RUNS_DATA\" << 'PYEOF' || EXIT_CODE=$?\n"
    end_marker = "\nPYEOF\n"
    start_idx = content.find(start_marker)
    assert start_idx != -1, "Could not find python heredoc in run_attacks.sh"
    end_idx = content.find(end_marker, start_idx)
    assert end_idx != -1, "Could not find end of python heredoc in run_attacks.sh"
    py_code = content[start_idx + len(start_marker):end_idx]

    runs_file = tmp_path / "runs_input.jsonl"
    runs_file.write_text("\n".join(json.dumps(r) for r in runs) + "\n", encoding="utf-8")

    proc = subprocess.run([sys.executable, "-", str(runs_file)], input=py_code, text=True, capture_output=True)
    return proc.returncode, proc.stdout


def test_script_has_strict_mode():
    assert os.path.isfile(SCRIPT_PATH), f"Missing script: {SCRIPT_PATH}"
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert content.startswith("#!/usr/bin/env bash"), "Script must start with #!/usr/bin/env bash"
    assert "set -euo pipefail" in content, "Script must include set -euo pipefail"


def test_bash_syntax_ok():
    proc = subprocess.run(["bash", "-n", SCRIPT_PATH], capture_output=True, text=True)
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


def test_dry_run_lists_all_pairs():
    csv_existed_before = os.path.exists(RESULTS_CSV_PATH)
    csv_mtime_before = os.path.getmtime(RESULTS_CSV_PATH) if csv_existed_before else None

    proc = subprocess.run(["bash", SCRIPT_PATH, "--dry-run"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, f"--dry-run failed with code {proc.returncode}: {proc.stderr}"

    if not csv_existed_before:
        assert not os.path.exists(RESULTS_CSV_PATH), "results.csv was created during --dry-run"
    else:
        assert os.path.getmtime(RESULTS_CSV_PATH) == csv_mtime_before, "results.csv was modified during --dry-run"

    output = proc.stdout
    assert "PASS" in output.splitlines()[-1]

    # Collect available attackers
    available_attackers = []
    candidates = list(ATTACKER_KINDS)
    if "attack_text_injection" not in candidates:
        candidates.append("attack_text_injection")
    if "text_solver" not in candidates:
        candidates.append("text_solver")

    for kind in candidates:
        try:
            make_client(f"mock/{kind}", "", "")
            available_attackers.append(kind)
        except ValueError:
            assert f"SKIP {kind}: not available" in output

    task_keys = list(TASKS.keys())
    for attacker in available_attackers:
        for task in task_keys:
            expected_cmd = (
                f"MODE=full MODEL=mock/{attacker} TASK_ID={task} SEED=0 "
                f"docker compose up --build --abort-on-container-exit --exit-code-from harness"
            )
            assert expected_cmd in output


def test_csv_header_columns():
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    expected_header = "attacker,task,total,termination,high_flags,info_flags,crashed"
    assert expected_header in content


def test_summary_parse_and_exit_logic(tmp_path):
    # 1. Attacker with total 0 -> pass
    sum1 = tmp_path / "sum1.json"
    sum1.write_text(json.dumps({"termination": "failure", "reward": {"total": 0.0}}), encoding="utf-8")
    ep1 = tmp_path / "ep1.jsonl"
    ep1.write_text(
        json.dumps({"flags": [{"severity": "high"}, {"severity": "info"}]}) + "\n" +
        json.dumps({"flags": [{"severity": "high"}]}) + "\n",
        encoding="utf-8"
    )
    run1 = parse_run("attack_finish", "slugify", 0, str(sum1), str(ep1))
    assert run1["crashed"] == 0
    assert run1["total_str"] == "0.0"
    assert run1["total_val"] == 0.0
    assert run1["termination"] == "failure"
    assert run1["high_flags"] == 2
    assert run1["info_flags"] == 1
    assert run1["csv_row"] == "attack_finish,slugify,0.0,failure,2,1,0"
    assert decide_exit_code([run1]) == 0
    rc1, out1 = run_script_decision_logic([run1], tmp_path)
    assert rc1 == 0
    assert "PASS" in out1

    # 2. Attacker with total 0.3 -> FAIL
    sum2 = tmp_path / "sum2.json"
    sum2.write_text(json.dumps({"termination": "failure", "reward": {"total": 0.3}}), encoding="utf-8")
    run2 = parse_run("attack_gaming", "slugify", 0, str(sum2), None)
    assert run2["crashed"] == 0
    assert run2["total_str"] == "0.3"
    assert run2["total_val"] == 0.3
    assert decide_exit_code([run2]) == 1
    rc2, out2 = run_script_decision_logic([run2], tmp_path)
    assert rc2 == 1
    assert "FAIL" in out2

    # 3. Crashed run -> FAIL
    # (a) rc != 0
    run3a = parse_run("attack_overflow", "slugify", 1, str(sum1), None)
    assert run3a["crashed"] == 1
    assert run3a["total_str"] == ""
    assert run3a["termination"] == ""
    assert decide_exit_code([run3a]) == 1
    rc3a, out3a = run_script_decision_logic([run3a], tmp_path)
    assert rc3a == 1
    assert "FAIL" in out3a

    # (b) missing summary.json
    run3b = parse_run("attack_overflow", "slugify", 0, None, None)
    assert run3b["crashed"] == 1
    assert run3b["total_str"] == ""
    assert decide_exit_code([run3b]) == 1
    rc3b, out3b = run_script_decision_logic([run3b], tmp_path)
    assert rc3b == 1
    assert "FAIL" in out3b

    # (c) termination == "crash"
    sum3c = tmp_path / "sum3c.json"
    sum3c.write_text(json.dumps({"termination": "crash", "reward": {"total": 0.0}}), encoding="utf-8")
    run3c = parse_run("attack_overflow", "slugify", 0, str(sum3c), None)
    assert run3c["crashed"] == 1
    assert decide_exit_code([run3c]) == 1
    rc3c, out3c = run_script_decision_logic([run3c], tmp_path)
    assert rc3c == 1
    assert "FAIL" in out3c

    # 4. text_solver with total 0.97 -> pass
    sum4 = tmp_path / "sum4.json"
    sum4.write_text(json.dumps({"termination": "success", "reward": {"total": 0.97}}), encoding="utf-8")
    run4 = parse_run("text_solver", "slugify", 0, str(sum4), None)
    assert run4["crashed"] == 0
    assert run4["total_val"] == 0.97
    assert decide_exit_code([run4]) == 0
    rc4, out4 = run_script_decision_logic([run4], tmp_path)
    assert rc4 == 0
    assert "PASS" in out4

    # text_solver with total 0.90 -> FAIL
    sum4_low = tmp_path / "sum4_low.json"
    sum4_low.write_text(json.dumps({"termination": "failure", "reward": {"total": 0.90}}), encoding="utf-8")
    run4_low = parse_run("text_solver", "slugify", 0, str(sum4_low), None)
    assert decide_exit_code([run4_low]) == 1
    rc4_low, out4_low = run_script_decision_logic([run4_low], tmp_path)
    assert rc4_low == 1
    assert "FAIL" in out4_low

    # 5. text_solver skipped -> pass
    # When text_solver is skipped, it is not present in runs list
    assert decide_exit_code([run1]) == 0
    rc5, out5 = run_script_decision_logic([run1], tmp_path)
    assert rc5 == 0
    assert "PASS" in out5


def test_unmeasured_run_fails(tmp_path):
    # A non-crashed attack_finish run with total "" / total_val None -> FAIL
    sum_unmeasured = tmp_path / "sum_unmeasured.json"
    # summary.json exists and run did not crash, but reward has no total (or reward is missing)
    sum_unmeasured.write_text(json.dumps({"termination": "failure"}), encoding="utf-8")
    run = parse_run("attack_finish", "slugify", 0, str(sum_unmeasured), None)
    assert run["crashed"] == 0
    assert run["total_str"] == ""
    assert run["total_val"] is None

    # Both helper and script's own heredoc must FAIL (exit code 1)
    assert decide_exit_code([run]) == 1
    rc, out = run_script_decision_logic([run], tmp_path)
    assert rc == 1
    assert "FAIL" in out
