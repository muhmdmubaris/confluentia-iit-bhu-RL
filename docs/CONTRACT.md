# BringUp: Interface Contract v1.1  (changes only via Pranav)

## Product
RL environment. An agent debugs a faulty circuit board with simulated bench tools.
The verifier re-simulates the FINAL board inside the harness container. No LLM in scoring.

## Layout (fork of RL-ENV-TEMPLATE)
common/circuit/        shared, copied into BOTH images, contains NO secrets
  parts.py             PARTS_BIN, lookup(part_no)
  board.py             canonical_json(board), state_hash(board), validate_board(board)
  solver.py            solve(board) -> solution
  checks.py            check_pass(check, board, sol), evaluate(spec, board) -> {id: bool}
  actions.py           apply_tool(board, name, args) -> (new_board, output, is_error)
harness/
  families/common.py   TaskInstance dataclass + helpers
  families/led.py, sensor.py, power.py, mixed.py   generators
  tasks.py             TASKS registry, DEMO_TASK = "led_easy", build_task(task_id, seed)
  verifier.py          score(task, start_board, final_board, trace) -> components dict
  exploits.py          flags(step_record, task) -> list of flag dicts
  parsing.py           parse_text_tool_calls(text) -> (calls, errors)
  env.py runner.py run.py agents.py tools.py   (template files, adapted)
sandbox/server.py      HTTP wrapper around common.circuit.actions.apply_tool
tests/                 pytest (+ hypothesis), local only
redteam/               attack mock clients, append/*.txt, run_attacks.sh, results.csv
scripts/               luna_trials.sh, build_timing.sh, drills.sh, make_zip.sh
requirements-dev.txt   pinned pytest + hypothesis; NEVER installed in a Docker image

## Board JSON
{
  "board_id": str,
  "parts": { ref: Part },
  "pins":  { pin: {"net": str, "mode": "output_high"|"output_low"|"input"|"adc"} },
  "powered": bool,
  "damaged": [ref, ...]  (sorted),
  "probe_budget": int | null   (null = unlimited),
  "probes_used": int,
  "spec": [check, ...]   (public copy; env.reset copies task.spec here for read_spec; the verifier ignores it),
  "history": [ {"tool": str, "args": dict}, ... ]   (mutating tools only)
}
Part = {
  "ref": "R3", "kind": "resistor"|"led"|"source"|"regulator"|"bridge",
  "nets": [a, b]   (regulator: [vin, vout, gnd]; source: [plus, minus]),
  "value": float   (ohms for resistor, volts for source),
  "part_no": str, "populated": bool, "rating_w": float,
  led: "vf", "rd", "imax";   regulator: "vout", "dropout", "ilim"
}
Ground net is "GND" (0 V). Canonical JSON = json.dumps(b, sort_keys=True, separators=(",",":"))
with floats rounded to 6 significant digits. state_hash = sha256(canonical)[:8].

## Solver: solve(board) -> {"v": {net: V}, "i": {ref: A}, "p": {ref: W}, "converged": bool, "notes": [str]}
Pure, deterministic, numpy only, < 50 ms for 40 parts. Unpowered board -> all zeros.
Damaged or unpopulated parts = open. bridge = 0.01 ohm.
MCU pins: output_high = 3.3 V via 25 ohm, output_low = 0 V via 25 ohm, input/adc = 1 Mohm to GND.
LED: piecewise-linear (off = open, on = vf + rd series). Regulator: regulating / dropout / current-limit.

## Spec (public, shown to agent) and intent (hidden, harness only)
check = {"id": "C1", "desc": str, "type": "voltage"|"current"|"pin_mode"|"populated",
         "target": net|ref|pin, "min": float|null, "max": float|null, "mode": str|null}
task.spec   = list of checks (success criteria, shown verbatim to the agent)
task.intent = list of checks the agent never sees (e.g. load current stays within design
              range, so a rail cannot be "fixed" by removing its load)

## TaskInstance (harness/families/common.py)
task_id, title, difficulty ("easy"|"medium"|"hard"), instructions (str), board (dict, faulty start),
spec (list), intent (list), faults (list[str], hidden), reference_actions (list[[tool, args]]),
max_changes (int)
Determinism: rng = random.Random(f"{task_id}:{seed}"). No time, uuid, set order or global random.

## Tools (schemas in harness/tools.py, semantics in common/circuit/actions.py)
read_spec() | list_parts(page:int) | inspect(ref) | read_schematic(page:int) | list_bin(kind)
power_on() | power_off() | measure_voltage(net) | measure_current(ref)
replace_part(ref, part_no) | remove_bridge(ref) | set_pin_mode(pin, mode) | finish()
Rules: outputs <= 2000 chars; errors start with "error:"; measuring needs power on and uses
one probe; replace/remove/set_pin_mode need power off; power_on damages any part with
p > 1.5 x rating_w or LED current > 1.5 x imax (irreversible, output "smoke from R5");
replace_part needs a part_no from PARTS_BIN of the same kind. No file access, no code execution.

## Model output parsing (harness/parsing.py + runner.py + agents.py)
1. Native tool_calls are used whenever present; text is then ignored for actions.
2. No native calls -> look in the reply text for tool calls: a ```json fenced block, a bare
   JSON object {"name"|"tool": str, "arguments"|"args": object|json-string}, a list of
   those, or {"tool_calls": [...]}. stdlib json only. The ONLY repair allowed: strip code fences.
3. Arguments that are not valid JSON -> "error: arguments are not valid JSON (<reason>)";
   the tool is not run. Never guess what the model meant.
4. More than 10 calls in one reply -> first 10 run, the rest get
   "error: max 10 tool calls per reply". Still 1 step.
5. Text mode: if the endpoint rejects the `tools` parameter (HTTP 400 whose message mentions
   tools or functions), retry that request once without `tools`; from then on the system
   prompt = base prompt + rendered tool list and text protocol + APPEND_FILE text
   (the appended text stays LAST). Text-mode results return as ONE user message
   "Tool results:\n[1] <tool>: <output>". Same code path for every model: never check model names.
6. The JSONL model_input is exactly what was sent.

## Sandbox HTTP
POST /reset {"board"} | POST /tool {"name","args"} -> {"output","is_error"} | GET /state -> {"board"} | GET /health
The sandbox NEVER receives faults, reference_actions or intent. Board state lives in memory only.

## Container hardening (docker-compose.yml)
sandbox: network "isolated" only (internal: true), user 1000, read_only: true,
tmpfs /tmp:size=16m,noexec,nosuid, cap_drop ALL, no-new-privileges, pids_limit 64,
mem_limit 512m, PYTHONDONTWRITEBYTECODE=1. harness: isolated + internet networks.

## Reward (harness/verifier.py, re-solves the final board itself)
failing0 = spec checks failing on the start board
fixed    = checks in failing0 that pass on the final board
regress  = spec checks passing at start that fail at end
extra    = max(0, parts_changed - task.max_changes)
base     = len(fixed)/len(failing0) x max(0, 1 - 0.25*regress - 0.05*extra)
total    = 0 if (any intent check fails OR any part damaged OR any high-severity flag) else base
success  <=> finish called AND total >= 0.95.  No-Op total is exactly 0 by construction.
Logged components: fixed_frac, regress, extra, intent_ok, damaged, flags_high, total, reasons

## Flags (harness/exploits.py), added to each step record as "flags": [...]
{"type": str, "severity": "info"|"high", "detail": str}
high: unknown_tool | suspicious_args ("../", "verifier", "reward", "__", "import", "eval")
      | out_of_bin_part | intent_violation
info: repeated_errors | finish_without_changes | text_tool_call | text_mode
stdout prints every flag as: [FLAG high] unknown_tool: get_reward

## Git
Branches pranav/*, a/*, b/*, c/*. PR -> Pranav merges. python -m pytest -q must pass.
