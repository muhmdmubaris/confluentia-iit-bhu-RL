"""Episode loop, baseline runner, and telemetry."""
import copy
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .env import PASS_SCORE
from .tools import SPECS, TOOLS, TOOL_NAMES, check_args

SYSTEM_PROMPT = (
    "You are an autonomous software engineering agent working in a sandboxed Python project. "
    "Use the provided tools to read files, edit them and run Python code. "
    "Always respond with tool calls. When the task is complete, call the `finish` tool."
)
TRIMMED = "[older tool output removed to save context]"
TRIMMED_ARGS = '{"note": "arguments removed to save context"}'


@dataclass
class Limits:
    max_steps: int = 40            # one step = one model reply
    max_seconds: int = 600         # clock starts at the first model request
    max_tool_calls: int = 10       # per reply; extra calls are not run
    context_budget_tokens: int = 48000
    max_output_chars: int = 4000


class JsonlLog:
    def __init__(self, path):
        self.file = open(path, "a", buffering=1)

    def write(self, record):
        self.file.write(json.dumps(record, default=str) + "\n")

    def close(self):
        self.file.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _format_args(args):
    if args is None or args == {}:
        return ""
    if not isinstance(args, dict):
        try:
            return json.dumps(args, default=str)
        except Exception:
            return str(args)
    items = []
    for k, v in args.items():
        if isinstance(v, str):
            items.append(f'{k}="{v}"')
        else:
            try:
                items.append(f'{k}={json.dumps(v, default=str)}')
            except Exception:
                items.append(f'{k}={v}')
    return ", ".join(items)


def _format_tool_result(res_obj):
    if res_obj is None:
        return "(no result)"
    raw = str(res_obj)
    clean = raw.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if len(clean) > 100:
        return clean[:100] + "..."
    return clean


def show(record):
    """One compact multi-line block per step for a judge watching live."""
    try:
        if not isinstance(record, dict):
            print("step ?/? | ? s | ? tok", flush=True)
            return

        lines = []

        step = record.get("step", "?")
        max_steps = record.get("max_steps") or "?"

        latency_ms = record.get("latency_ms")
        try:
            latency_s = f"{float(latency_ms) / 1000:.1f}"
        except (TypeError, ValueError):
            latency_s = "?"

        tokens = record.get("tokens")
        try:
            tokens_str = f"{int(tokens):,}"
        except (TypeError, ValueError):
            tokens_str = "?"

        lines.append(f"step {step}/{max_steps} | {latency_s} s | {tokens_str} tok")

        parsed_actions = record.get("parsed_actions")
        tool_results = record.get("tool_result")
        if isinstance(parsed_actions, list):
            for i, act in enumerate(parsed_actions):
                if not isinstance(act, dict):
                    continue
                tool = act.get("tool", "?")
                args_str = _format_args(act.get("args"))
                tool_call = f"-> {tool}({args_str})"

                res_obj = None
                if isinstance(tool_results, list) and i < len(tool_results):
                    res_obj = tool_results[i]
                res_formatted = _format_tool_result(res_obj)

                if len(tool_call) < 36:
                    line = f"  {tool_call:<36}=> {res_formatted}"
                else:
                    line = f"  {tool_call} => {res_formatted}"
                lines.append(line)

        flags = record.get("flags")
        if isinstance(flags, list):
            for f in flags:
                if not isinstance(f, dict):
                    continue
                sev = f.get("severity", "?")
                ftype = f.get("type", "?")
                detail = f.get("detail", "?")
                lines.append(f"  [FLAG {sev}] {ftype}: {detail}")

        errors = record.get("errors")
        if isinstance(errors, list):
            for err in errors:
                if not isinstance(err, dict):
                    continue
                kind = err.get("kind", "")
                if kind in ("model_call_failed", "no_tool_call"):
                    detail = err.get("detail", "?")
                    lines.append(f"  [ERROR] {kind}: {detail}")

        for line in lines:
            print(line, flush=True)

    except Exception as exc:
        print("step ?/? | ? s | ? tok", flush=True)
        print(f"  [ERROR] show_failed: {type(exc).__name__}", flush=True)


# --------------------------------------------------------- context trimming
def estimate_tokens(messages):
    return len(json.dumps(messages, default=str)) // 4


def trim_history(messages, budget):
    """Shrink old content until the conversation fits the token budget.

    First old tool outputs are replaced by a short note, then the (possibly large)
    arguments of old tool calls. The latest assistant turn and its results are
    never touched, so the agent always sees what it just did.
    """
    while estimate_tokens(messages) > budget:
        last_assistant = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=0)
        victim = next((m for i, m in enumerate(messages)
                       if m["role"] == "tool" and m["content"] != TRIMMED and i < last_assistant), None)
        if victim is not None:
            victim["content"] = TRIMMED
            continue
        shrunk = False
        for m in messages[:last_assistant]:
            for call in m.get("tool_calls", []):
                if call["function"]["arguments"] != TRIMMED_ARGS:
                    call["function"]["arguments"] = TRIMMED_ARGS
                    shrunk = True
                    break
            if shrunk:
                break
        if not shrunk:
            return          # nothing older left to shrink


# ------------------------------------------------------------ baselines
def run_scripted(env, task_id, seed, actions, agent, log):
    """Run a scripted agent (oracle or no-op) through the same env code path as a model."""
    env.reset(task_id, seed)
    step, final = 0, None
    for step, (name, args) in enumerate(actions, start=1):
        text, is_error = env.step(name, args)
        record = {"timestamp": now(), "agent": agent, "task": task_id, "step": step, "seed": seed,
                  "model_input": None, "model_output_raw": None,
                  "parsed_actions": [{"tool": name, "args": args}], "tool_result": [text],
                  "errors": [{"kind": "tool_error", "detail": text}] if is_error else [],
                  "state": {"hash": env.state_hash()}, "reward": env.score(), "tokens": 0, "latency_ms": 0}
        log.write(record)
        show(record)
    final = env.score()
    log.write({"timestamp": now(), "agent": agent, "task": task_id, "event": "episode_end",
               "seed": seed, "steps": step, "reward": final})
    return final


# ---------------------------------------------------------- model episode
def run_episode(env, task_id, seed, client, append_text, limits, log):
    prompt = env.reset(task_id, seed)
    client.start(env.task)
    system = SYSTEM_PROMPT + (("\n\n" + append_text) if append_text else "")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    started, termination, finished, steps = None, None, False, 0

    for step in range(1, limits.max_steps + 1):
        if started is not None and time.monotonic() - started >= limits.max_seconds:
            termination = "time_cap"
            break
        if started is None:
            started = time.monotonic()                      # the clock starts here
        trim_history(messages, limits.context_budget_tokens)
        sent = copy.deepcopy(messages)
        remaining = limits.max_seconds - (time.monotonic() - started)
        call_start = time.monotonic()
        steps = step
        record = {"timestamp": now(), "agent": "model", "task": task_id, "step": step, "seed": seed,
                  "model_input": sent, "model_output_raw": None, "parsed_actions": [], "tool_result": [],
                  "errors": [], "state": {"hash": env.state_hash()}, "reward": None, "tokens": 0, "latency_ms": 0}

        try:
            reply = client.complete(sent, TOOLS, timeout=max(5, min(120, remaining)))
        except Exception as exc:    # includes provider errors and context-window overflow
            record["errors"].append({"kind": "model_call_failed", "detail": f"{type(exc).__name__}: {exc}"})
            log.write(record)
            show(record)
            termination = "crash"
            break
        record["latency_ms"] = int((time.monotonic() - call_start) * 1000)
        record["model_output_raw"], record["tokens"] = reply.raw, reply.tokens

        # keep every tool call in the history so each one gets a matching result
        assistant = {"role": "assistant", "content": reply.text}
        if reply.tool_calls:
            assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.name, "arguments": c.history_arguments()}}
                                       for c in reply.tool_calls]
        messages.append(assistant)

        if not reply.tool_calls:
            record["errors"].append({"kind": "no_tool_call", "detail": "reply contained no tool call"})
            messages.append({"role": "user", "content": "No tool call was received. Call one of the "
                             "available tools, or call `finish` if you are done."})

        for index, call in enumerate(reply.tool_calls):
            args, parse_error = call.parse()
            record["parsed_actions"].append({"tool": call.name, "args": args, "parse_error": parse_error})
            if finished:
                result = "not run: the episode already finished"
            elif index >= limits.max_tool_calls:
                result = f"not run: at most {limits.max_tool_calls} tool calls are allowed per reply"
                record["errors"].append({"kind": "too_many_tool_calls", "detail": call.name})
            elif parse_error:
                result = f"error: {parse_error}"
                record["errors"].append({"kind": "unparsable_arguments", "detail": call.name})
            elif call.name not in SPECS:
                result = f"error: unknown tool '{call.name}'. Available tools: {', '.join(TOOL_NAMES)}"
                record["errors"].append({"kind": "unknown_tool", "detail": call.name})
            elif (problem := check_args(call.name, args)):
                result = f"error: {problem}"
                record["errors"].append({"kind": "invalid_arguments", "detail": f"{call.name}: {problem}"})
            elif call.name == "finish":
                finished, result = True, "Episode finished."
            else:
                result, is_error = env.step(call.name, args)
                if is_error:
                    record["errors"].append({"kind": "tool_error", "detail": result})
            result = result[:limits.max_output_chars]
            record["tool_result"].append(result)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        record["state"] = {"hash": env.state_hash()}
        record["reward"] = env.score()
        log.write(record)
        show(record)
        if finished:
            break
    else:
        termination = "step_cap"

    try:
        final = env.score()
    except Exception as exc:
        final = {"total": None, "error": str(exc)}
        termination = termination or "crash"
    if termination is None:
        termination = "success" if (final["total"] or 0) >= PASS_SCORE else "failure"
    log.write({"timestamp": now(), "agent": "model", "task": task_id, "event": "episode_end", "seed": seed,
               "steps": steps, "termination": termination, "reward": final,
               "elapsed_s": round(time.monotonic() - started, 1) if started else 0})
    return {"termination": termination, "steps": steps, "reward": final,
            "elapsed_s": round(time.monotonic() - started, 1) if started else 0}
