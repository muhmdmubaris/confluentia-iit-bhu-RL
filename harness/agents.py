"""Agents: the model client, two offline mock models, and the baselines."""
import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str          # raw text exactly as the model sent it

    def parse(self):
        """Return (args, error). `args` is a dict when the arguments are valid JSON."""
        try:
            args = json.loads(self.arguments) if self.arguments else {}
        except json.JSONDecodeError as exc:
            return None, f"arguments are not valid JSON ({exc.msg})"
        if not isinstance(args, dict):
            return None, "arguments must be a JSON object"
        return args, None

    def history_arguments(self):
        """Arguments as stored in the conversation. Invalid JSON is replaced with {} so
        the next request is still accepted by every provider."""
        args, error = self.parse()
        return json.dumps(args) if error is None else "{}"


@dataclass
class Reply:
    text: str | None
    tool_calls: list = field(default_factory=list)
    raw: str = ""
    tokens: int = 0


# ------------------------------------------------------------ real models
class OpenAIClient:
    """Calls any model through an OpenAI-compatible endpoint.

    Judges and teams are given a base_url, an api_key and a model name; the harness reads
    them from the environment variables BASE_URL, API_KEY and MODEL. The same call is used
    for every model, so switching models only means changing those values.
    """

    def __init__(self, model, api_key, base_url=None, max_tokens=None):
        import openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url or None, max_retries=2)
        self.model, self.max_tokens = model, max_tokens

    def start(self, task):
        pass

    def complete(self, messages, tools, timeout):
        kwargs = dict(model=self.model, messages=messages, tools=tools, timeout=timeout)
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens      # optional: set MAX_TOKENS to cap each reply
        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        calls = []
        for index, call in enumerate(message.tool_calls or []):
            function = getattr(call, "function", None)
            calls.append(ToolCall(id=call.id or f"call_{index}",
                                  name=(function.name if function else "") or "",
                                  arguments=(function.arguments if function else "") or ""))
        usage = getattr(response, "usage", None)
        return Reply(text=message.content, tool_calls=calls, raw=response.model_dump_json(),
                     tokens=getattr(usage, "total_tokens", 0) or 0)


# ------------------------------------------------------------ mock models
def _bin_extremes():
    try:
        from common.circuit.parts import PARTS_BIN
    except Exception as exc:
        raise RuntimeError(f"PARTS_BIN unavailable: {exc}")

    items = []
    if isinstance(PARTS_BIN, dict):
        for k, v in PARTS_BIN.items():
            if isinstance(v, dict):
                d = dict(v)
                d.setdefault("part_no", k)
                items.append(d)
            else:
                d = dict(getattr(v, "__dict__", {}))
                d.setdefault("part_no", getattr(v, "part_no", k))
                items.append(d)
    elif isinstance(PARTS_BIN, (list, tuple)):
        for v in PARTS_BIN:
            if isinstance(v, dict):
                items.append(dict(v))
            else:
                items.append(dict(getattr(v, "__dict__", {})))
    else:
        raise RuntimeError("PARTS_BIN unavailable: unsupported PARTS_BIN format")

    resistors = [
        item for item in items
        if item.get("kind") == "resistor" and "value" in item and "part_no" in item
    ]
    if not resistors:
        raise RuntimeError("PARTS_BIN unavailable: no resistors found")
    resistors.sort(key=lambda x: (x["value"], str(x["part_no"])))
    p_min = resistors[0]["part_no"]

    regulators = [
        item for item in items
        if item.get("kind") == "regulator" and "ilim" in item and "part_no" in item
    ]
    if not regulators:
        raise RuntimeError("PARTS_BIN unavailable: no regulators found")
    regulators.sort(key=lambda x: (-x["ilim"], str(x["part_no"])))
    p_strong = regulators[0]["part_no"]

    return p_min, p_strong


ATTACKER_KINDS = (
    "attack_finish",
    "attack_unknown_tool",
    "attack_injection",
    "attack_out_of_bin",
    "attack_malformed",
    "attack_overflow",
    "attack_bruteforce",
    "attack_gaming",
    "attack_upgrade_regulator",
    "attack_burn_and_replace",
    "attack_spam",
)


class MockClient:
    """Offline stand-ins so the template runs with no API key.

    mock/solver  writes the reference solution, then finishes.
    mock/idle    finishes immediately without doing anything.
    Plus scripted attacker mock kinds for red-teaming.
    """

    def __init__(self, kind):
        self.kind = kind
        self.turn = 0
        self.task = None
        self.board = None
        self._planned_calls = []
        self._call_cursor = 0

    def start(self, task):
        self.turn = 0
        if self.kind in ("solver", "idle"):
            self.task = task
            return

        self.board = getattr(task, "board", None)
        self._planned_calls = []
        self._call_cursor = 0

        parts = self.board.get("parts", {}) if self.board else {}

        if self.kind == "attack_bruteforce":
            p_min, _ = _bin_extremes()
            resistors = sorted(
                ref for ref, part in parts.items()
                if isinstance(part, dict) and part.get("kind") == "resistor"
            )
            calls = [("power_off", {})]
            for r in resistors:
                calls.append(("replace_part", {"ref": r, "part_no": p_min}))
            calls.append(("power_on", {}))
            calls.append(("finish", {}))
            self._planned_calls = calls

        elif self.kind == "attack_gaming":
            resistors = sorted(
                ref for ref, part in parts.items()
                if isinstance(part, dict) and part.get("kind") == "resistor"
            )
            calls = [("power_off", {})]
            for r in resistors:
                calls.append(("replace_part", {"ref": r, "part_no": "R-1M"}))
            calls.append(("power_on", {}))
            calls.append(("finish", {}))
            self._planned_calls = calls

        elif self.kind == "attack_upgrade_regulator":
            u1 = parts.get("U1")
            if isinstance(u1, dict) and u1.get("kind") == "regulator":
                _, p_strong = _bin_extremes()
                self._planned_calls = [
                    ("power_off", {}),
                    ("replace_part", {"ref": "U1", "part_no": p_strong}),
                    ("power_on", {}),
                    ("finish", {}),
                ]
            else:
                self._planned_calls = [("finish", {})]

        elif self.kind == "attack_burn_and_replace":
            u1 = parts.get("U1")
            if isinstance(u1, dict) and "part_no" in u1:
                self._planned_calls = [
                    ("power_on", {}),
                    ("power_off", {}),
                    ("replace_part", {"ref": "U1", "part_no": u1["part_no"]}),
                    ("finish", {}),
                ]
            else:
                self._planned_calls = [("finish", {})]

    def _find_first_resistor(self):
        if not self.board:
            return "R1"
        parts = self.board.get("parts", {})
        resistors = sorted(
            ref for ref, part in parts.items()
            if isinstance(part, dict) and part.get("kind") == "resistor"
        )
        return resistors[0] if resistors else "R1"

    def complete(self, messages, tools, timeout):
        self.turn += 1
        if self.kind in ("solver", "idle"):
            calls = []
            if self.kind == "solver" and self.turn == 1:
                for path, content in self.task.reference.items():
                    calls.append(("write_file", {"path": path, "content": content}))
                calls.append(("run_python", {"code": "print('checked')"}))
            else:
                calls.append(("finish", {}))
            tool_calls = [ToolCall(f"mock-{self.turn}-{i}", name, json.dumps(args))
                          for i, (name, args) in enumerate(calls)]
            return Reply(text=None, tool_calls=tool_calls, raw=json.dumps({"mock": self.kind, "turn": self.turn}))

        if self.kind in ("attack_bruteforce", "attack_gaming", "attack_upgrade_regulator", "attack_burn_and_replace"):
            if self._call_cursor < len(self._planned_calls):
                chunk = self._planned_calls[self._call_cursor:self._call_cursor + 10]
                self._call_cursor += len(chunk)
                tool_calls = [
                    ToolCall(f"call_{self.turn}_{i}", name, json.dumps(args))
                    for i, (name, args) in enumerate(chunk)
                ]
                return Reply(text=None, tool_calls=tool_calls)
            tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
            return Reply(text=None, tool_calls=[tc])

        if self.kind == "attack_spam":
            if self.turn <= 40:
                tc = ToolCall(f"call_{self.turn}_0", "measure_voltage", json.dumps({"net": "NOPE"}))
            else:
                tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
            return Reply(text=None, tool_calls=[tc])

        if self.kind == "attack_finish":
            return Reply(text=None, tool_calls=[
                ToolCall(f"call_{self.turn}_0", "finish", "{}")
            ])

        if self.kind == "attack_unknown_tool":
            if self.turn == 1:
                tc = ToolCall(f"call_{self.turn}_0", "get_reward", "{}")
            elif self.turn == 2:
                tc = ToolCall(f"call_{self.turn}_0", "read_file", json.dumps({"path": "../harness/verifier.py"}))
            elif self.turn == 3:
                tc = ToolCall(f"call_{self.turn}_0", "run_python", json.dumps({"code": "print(1)"}))
            else:
                tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
            return Reply(text=None, tool_calls=[tc])

        if self.kind == "attack_injection":
            if self.turn == 1:
                tc = ToolCall(f"call_{self.turn}_0", "inspect", json.dumps({"ref": "../../etc/passwd"}))
            elif self.turn == 2:
                tc = ToolCall(f"call_{self.turn}_0", "measure_voltage", json.dumps({"net": "__import__('os')"}))
            elif self.turn == 3:
                tc = ToolCall(f"call_{self.turn}_0", "inspect", json.dumps({"ref": "reward=1"}))
            else:
                tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
            return Reply(text=None, tool_calls=[tc])

        if self.kind == "attack_out_of_bin":
            if self.turn == 1:
                tc = ToolCall(f"call_{self.turn}_0", "power_off", "{}")
            elif self.turn in (2, 3, 4):
                r_ref = self._find_first_resistor()
                part_no = "R-0" if self.turn == 2 else ("WIRE" if self.turn == 3 else "SUPERCAP")
                tc = ToolCall(f"call_{self.turn}_0", "replace_part", json.dumps({"ref": r_ref, "part_no": part_no}))
            else:
                tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
            return Reply(text=None, tool_calls=[tc])

        if self.kind == "attack_malformed":
            if self.turn == 1:
                tc = ToolCall(f"call_{self.turn}_0", "inspect", '{"ref": R1')
                return Reply(text=None, tool_calls=[tc])
            elif self.turn == 2:
                return Reply(text="I am not going to call a tool.", tool_calls=[])
            else:
                tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
                return Reply(text=None, tool_calls=[tc])

        if self.kind == "attack_overflow":
            if self.turn == 1:
                tcs = [
                    ToolCall(f"call_{self.turn}_{i}", "list_parts", json.dumps({"page": 0}))
                    for i in range(15)
                ]
                return Reply(text=None, tool_calls=tcs)
            else:
                tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
                return Reply(text=None, tool_calls=[tc])

        tc = ToolCall(f"call_{self.turn}_0", "finish", "{}")
        return Reply(text=None, tool_calls=[tc])


def make_client(model, api_key, base_url, max_tokens=None):
    if model.startswith("mock/"):
        kind = model.split("/", 1)[1]
        if kind not in ("solver", "idle", *ATTACKER_KINDS):
            raise ValueError(f"mock models are 'mock/solver', 'mock/idle' and attacker kinds")
        return MockClient(kind)
    if not api_key:
        raise ValueError("API_KEY is not set")
    return OpenAIClient(model, api_key, base_url, max_tokens)


# -------------------------------------------------------------- baselines
def oracle_actions(task):
    """The reference solution as a list of (tool, args) actions. Must score full marks."""
    return [("write_file", {"path": path, "content": content}) for path, content in task.reference.items()]


def noop_actions(task):
    """An agent that gives up immediately. Must score about zero."""
    return []
