"""Tool definitions shown to the model, plus argument checking."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_spec",
            "description": "Read the task specification and board requirements.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_parts",
            "description": "List all components on the board.\nOptional page argument.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Page number (1-based, 20 parts per page).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "description": "Inspect detailed properties and pins of a component.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Component designator (e.g. R1, D1, U1).",
                    },
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_schematic",
            "description": "Read the ASCII schematic of the circuit.\nOptional page argument.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Page number (1-based).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_bin",
            "description": "List available spare parts of a given kind from the parts bin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["resistor", "led", "regulator"],
                        "description": "Part kind: resistor, led, or regulator.",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "power_on",
            "description": "Apply power to the circuit.\nRuns solver; damages overstressed parts.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "power_off",
            "description": "Remove power from the circuit.\nAllows safe modification of parts.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "measure_voltage",
            "description": "Measure a net voltage.\nRequires power on; uses 1 probe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "net": {
                        "type": "string",
                        "description": "Net name to measure voltage at.",
                    },
                },
                "required": ["net"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "measure_current",
            "description": "Measure current through a component.\nRequires power on; uses 1 probe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Component designator to measure current through.",
                    },
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_part",
            "description": "Replace a part with a bin part of the same kind.\nRequires power off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Component designator to replace.",
                    },
                    "part_no": {
                        "type": "string",
                        "description": "Part number from the parts bin.",
                    },
                },
                "required": ["ref", "part_no"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_bridge",
            "description": "Remove an accidental solder bridge.\nRequires power off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "Bridge designator to remove.",
                    },
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_pin_mode",
            "description": "Configure an MCU pin mode.\nRequires power off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {
                        "type": "string",
                        "description": "MCU pin name (e.g. P1, P2).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["output_high", "output_low", "input", "adc"],
                        "description": "Operating mode for the pin.",
                    },
                },
                "required": ["pin", "mode"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the episode. Call when the board is fixed.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

SPECS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
TOOL_NAMES = list(SPECS)


def check_args(name, args):
    """Return None if valid, else a message WITHOUT the "error: " prefix (the caller adds it)."""
    try:
        if not isinstance(name, str) or name not in SPECS:
            try:
                safe_name = str(name)[:60]
            except Exception:
                safe_name = "<unprintable>"
            return f"unknown tool '{safe_name}'"

        if not isinstance(args, dict):
            return "arguments must be an object"

        spec = SPECS[name]
        for req in spec.get("required", []):
            if req not in args:
                return f"missing argument '{req}' for {name}"

        props = spec.get("properties", {})
        unexpected = sorted([k for k in args.keys() if k not in props], key=lambda k: str(k))
        if unexpected:
            return f"unexpected argument '{unexpected[0]}' for {name}"

        for prop_name, prop_spec in props.items():
            if prop_name in args:
                val = args[prop_name]
                expected_type = prop_spec.get("type")
                if expected_type == "integer":
                    if type(val) is not int:
                        return f"argument '{prop_name}' must be an integer"
                elif expected_type == "string":
                    if not isinstance(val, str):
                        return f"argument '{prop_name}' must be a string"

                if "enum" in prop_spec:
                    if val not in prop_spec["enum"]:
                        return f"argument '{prop_name}' must be one of: {', '.join(prop_spec['enum'])}"

                if prop_name == "page" and val < 1:
                    return "argument 'page' must be >= 1"

        return None
    except Exception as exc:
        return f"internal validation error: {type(exc).__name__}: {str(exc)[:200]}"
