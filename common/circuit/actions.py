"""BringUp RL Environment: Tool actions, formatting helpers, and dispatcher."""

import copy
import math
import re
from typing import Any

TOOL_NAMES: list[str] = [
    "read_spec",
    "list_parts",
    "inspect",
    "read_schematic",
    "list_bin",
    "power_on",
    "power_off",
    "measure_voltage",
    "measure_current",
    "replace_part",
    "remove_bridge",
    "set_pin_mode",
    "finish",
]

ARG_SCHEMA: dict[str, dict[str, tuple[type, bool]]] = {
    "read_spec": {},
    "list_parts": {"page": (int, False)},
    "inspect": {"ref": (str, True)},
    "read_schematic": {"page": (int, False)},
    "list_bin": {"kind": (str, True)},
    "power_on": {},
    "power_off": {},
    "measure_voltage": {"net": (str, True)},
    "measure_current": {"ref": (str, True)},
    "replace_part": {"ref": (str, True), "part_no": (str, True)},
    "remove_bridge": {"ref": (str, True)},
    "set_pin_mode": {"pin": (str, True), "mode": (str, True)},
    "finish": {},
}

PIN_LABELS: dict[str, list[str]] = {
    "resistor": ["1", "2"],
    "bridge": ["1", "2"],
    "led": ["A", "K"],
    "regulator": ["vin", "vout", "gnd"],
    "source": ["+", "-"],
}

MAX_OUTPUT: int = 2000
CUT_MARKER: str = "  [cut] "


def natural_sort_key(s: str) -> list[tuple[int, int | str]]:
    """Split string into letter and numeric chunks for natural sorting (e.g. R2 before R10)."""
    parts = re.split(r"(\d+)", s)
    return [(0, int(p)) if p.isdigit() else (1, p) for p in parts]


def _fmt_sig3(val: float) -> str:
    """Format float with 3 significant digits, dropping trailing zeros, no exponent notation."""
    if val == 0:
        return "0"
    sign = "-" if val < 0 else ""
    abs_val = abs(val)
    d = math.floor(math.log10(abs_val))
    digits = 2 - d
    rounded = round(abs_val, digits)
    if rounded > 0:
        d_after = math.floor(math.log10(rounded))
        if d_after > d:
            digits = 2 - d_after
            rounded = round(abs_val, digits)
    decimals = max(0, digits)
    s = f"{rounded:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return sign + s


def fmt_ohms(x: float) -> str:
    """Format resistance in ohms without exponent notation, unit chosen after rounding."""
    if x == 0:
        return "0 Ω"
    abs_x = abs(x)
    d = math.floor(math.log10(abs_x))
    digits = 2 - d
    rounded = round(abs_x, digits)
    if rounded > 0:
        d_after = math.floor(math.log10(rounded))
        if d_after > d:
            rounded = round(abs_x, 2 - d_after)

    if rounded < 1000:
        return f"{_fmt_sig3(x)} Ω"
    elif rounded < 1e6:
        return f"{_fmt_sig3(x / 1e3)} kΩ"
    else:
        return f"{_fmt_sig3(x / 1e6)} MΩ"


def fmt_volts(x: float) -> str:
    """Format voltage with 3 decimal places."""
    return f"{x:.3f} V"


def fmt_current(a: float) -> str:
    """Format current in mA (2 decimals if < 1 A) or A (3 decimals if >= 1 A)."""
    if abs(a) < 1:
        return f"{a * 1000:.2f} mA"
    else:
        return f"{a:.3f} A"


def clip(text: str) -> str:
    """Clip text to at most 2000 characters ending with '  [cut] ' if clipped."""
    if len(text) > MAX_OUTPUT:
        return text[: MAX_OUTPUT - len(CUT_MARKER)] + CUT_MARKER
    return text


def _tool_read_spec(board: dict, args: dict) -> tuple[str, bool]:
    spec = board.get("spec")
    if not spec:
        return ("error: spec not loaded", True)

    lines: list[str] = []
    for c in spec:
        cid = c.get("id", "")
        ctype = c.get("type", "")
        target = c.get("target", "")
        desc = c.get("desc", "")
        cmin = c.get("min")
        cmax = c.get("max")

        if ctype == "voltage":
            if cmin is None and cmax is None:
                bounds = "any"
            elif cmin is not None and cmax is not None:
                bounds = f"{cmin:.3f}–{cmax:.3f} V"
            elif cmin is not None:
                bounds = f">= {cmin:.3f} V"
            else:
                bounds = f"<= {cmax:.3f} V"
            lines.append(f"{cid} voltage {target}: {bounds} ({desc})")
        elif ctype == "current":
            if cmin is None and cmax is None:
                bounds = "any"
            elif cmin is not None and cmax is not None:
                bounds = f"{cmin * 1000:.1f}–{cmax * 1000:.1f} mA"
            elif cmin is not None:
                bounds = f">= {cmin * 1000:.1f} mA"
            else:
                bounds = f"<= {cmax * 1000:.1f} mA"
            lines.append(f"{cid} current {target}: {bounds} ({desc})")
        elif ctype == "pin_mode":
            mode = c.get("mode", "")
            lines.append(f"{cid} pin_mode {target}: {mode}")
        elif ctype == "populated":
            lines.append(f"{cid} populated {target}: yes")
        else:
            lines.append(f"{cid} {ctype} {target}".strip())

    return ("\n".join(lines), False)


def _tool_list_parts(board: dict, args: dict) -> tuple[str, bool]:
    page = args.get("page", 1)
    parts_dict = board.get("parts") or {}
    sorted_refs = sorted(parts_dict.keys(), key=natural_sort_key)
    total_parts = len(sorted_refs)
    total_pages = max(1, math.ceil(total_parts / 15))

    if page < 1 or page > total_pages:
        return (f"error: page {page} out of range (1-{total_pages}). Use list_parts(page=1).", True)

    start_idx = (page - 1) * 15
    page_refs = sorted_refs[start_idx : start_idx + 15]

    lines: list[str] = []
    for ref in page_refs:
        p = parts_dict[ref]
        kind = p.get("kind", "")
        part_no = p.get("part_no", "")
        nets = "–".join(p.get("nets", []))

        if kind == "resistor":
            val_str = fmt_ohms(p.get("value", 0.0))
        elif kind == "source":
            val_str = fmt_volts(p.get("value", 0.0))
        elif kind == "led":
            val_str = f"Vf {p.get('vf', 2.0):.2f} V"
        elif kind == "regulator":
            val_str = f"{p.get('vout', 0.0):.2f} V out"
        elif kind == "bridge":
            val_str = "0.01 Ω"
        else:
            val_str = str(p.get("value", ""))

        lines.append(f"{ref}  {kind}  {val_str}  {part_no}  {nets}")

    footer = f"page {page}/{total_pages}"
    if lines:
        return ("\n".join(lines) + "\n" + footer, False)
    return (footer, False)


def _tool_inspect(board: dict, args: dict) -> tuple[str, bool]:
    ref = args["ref"]
    parts_dict = board.get("parts") or {}
    if ref not in parts_dict:
        return (f"error: no part '{ref}'. Use list_parts to see refs.", True)

    part = parts_dict[ref]
    kind = part.get("kind", "")
    lines: list[str] = [f"{ref}: {kind}"]

    if kind == "resistor":
        lines.append(f"value: {fmt_ohms(part.get('value', 0.0))}")
    elif kind == "source":
        lines.append(f"value: {fmt_volts(part.get('value', 0.0))}")
    elif kind == "bridge":
        lines.append(f"value: {fmt_ohms(part.get('value', 0.01))}")

    lines.append(f"part_no: {part.get('part_no', '')}")

    labels = PIN_LABELS.get(kind, [str(i + 1) for i in range(len(part.get("nets", [])))])
    net_pairs = [f"{lbl}={net}" for lbl, net in zip(labels, part.get("nets", []))]
    lines.append(f"nets: {' '.join(net_pairs)}")

    rating_w = part.get("rating_w")
    if rating_w is not None:
        lines.append(f"rating: {_fmt_sig3(rating_w)} W")

    pop = "yes" if part.get("populated", True) else "no"
    lines.append(f"populated: {pop}")

    damaged_list = board.get("damaged") or []
    dam = "yes" if ref in damaged_list else "no"
    lines.append(f"damaged: {dam}")

    if kind == "led":
        vf = part.get("vf", 0.0)
        rd = part.get("rd", 0.0)
        imax = part.get("imax", 0.0)
        lines.append(f"vf {vf:.3f} V, rd {fmt_ohms(rd)}, imax {imax * 1000:.2f} mA")
    elif kind == "regulator":
        vout = part.get("vout", 0.0)
        dropout = part.get("dropout", 0.0)
        ilim = part.get("ilim", 0.0)
        lines.append(f"vout {vout:.3f} V, dropout {dropout:.3f} V, ilim {ilim * 1000:.2f} mA")

    return ("\n".join(lines), False)


def _tool_read_schematic(board: dict, args: dict) -> tuple[str, bool]:
    page = args.get("page", 1)
    nets_map: dict[str, list[tuple[Any, ...]]] = {}

    parts_dict = board.get("parts") or {}
    for ref, part in parts_dict.items():
        kind = part.get("kind", "")
        labels = PIN_LABELS.get(kind, [str(i + 1) for i in range(len(part.get("nets", [])))])
        for label, net in zip(labels, part.get("nets", [])):
            if net not in nets_map:
                nets_map[net] = []
            nets_map[net].append(("part", ref, kind, label))

    pins_dict = board.get("pins") or {}
    for pin_name, pin_info in pins_dict.items():
        net = pin_info.get("net", "")
        mode = pin_info.get("mode", "")
        if net:
            if net not in nets_map:
                nets_map[net] = []
            nets_map[net].append(("pin", pin_name, mode))

    def member_sort_key(item: tuple[Any, ...]) -> tuple[Any, ...]:
        if item[0] == "part":
            _, ref, kind, label = item
            if kind == "source":
                priority = 0
            elif kind == "regulator":
                priority = 1
            else:
                priority = 2
            return (priority, natural_sort_key(ref), label)
        else:
            _, pin_name, mode = item
            return (3, natural_sort_key(pin_name), mode)

    sorted_net_names = sorted(nets_map.keys())
    total_nets = len(sorted_net_names)
    total_pages = max(1, math.ceil(total_nets / 10))

    if page < 1 or page > total_pages:
        return (f"error: page {page} out of range (1-{total_pages}). Use read_schematic(page=1).", True)

    start_idx = (page - 1) * 10
    page_nets = sorted_net_names[start_idx : start_idx + 10]

    lines: list[str] = []
    for net in page_nets:
        members = sorted(nets_map[net], key=member_sort_key)
        formatted: list[str] = []
        for m in members:
            if m[0] == "part":
                formatted.append(f"{m[1]}.{m[3]}")
            else:
                formatted.append(f"MCU.{m[1]}({m[2]})")
        lines.append(f"{net}: {', '.join(formatted)}")

    footer = f"page {page}/{total_pages}"
    if lines:
        return ("\n".join(lines) + "\n" + footer, False)
    return (footer, False)


HANDLERS: dict[str, Any] = {
    "read_spec": _tool_read_spec,
    "list_parts": _tool_list_parts,
    "inspect": _tool_inspect,
    "read_schematic": _tool_read_schematic,
}

IMPLEMENTED_TOOLS: set[str] = set(HANDLERS.keys())


def apply_tool(board: Any, name: Any, args: Any) -> tuple[dict, str, bool]:
    """Pure tool dispatcher. Validates input and schema, runs handler without mutating input."""
    if not isinstance(board, dict):
        return ({}, "error: board must be a dictionary", True)

    board_copy = copy.deepcopy(board)

    if not isinstance(name, str) or name not in ARG_SCHEMA:
        return (board_copy, f"error: unknown tool '{name}'", True)

    if not isinstance(args, dict):
        return (board_copy, "error: arguments must be a dictionary", True)

    schema = ARG_SCHEMA[name]

    # 1. Missing required keys in schema order
    for arg_name, (expected_type, required) in schema.items():
        if required and arg_name not in args:
            return (board_copy, f"error: missing argument '{arg_name}' for {name}", True)

    # 2. Unexpected keys sorted by str(key)
    unexpected = sorted([k for k in args.keys() if k not in schema], key=lambda k: str(k))
    if unexpected:
        return (board_copy, f"error: unexpected argument '{unexpected[0]}' for {name}", True)

    # 3. Type checking
    for arg_name, (expected_type, required) in schema.items():
        if arg_name in args:
            val = args[arg_name]
            if expected_type is int:
                if type(val) is not int:
                    return (board_copy, f"error: argument '{arg_name}' must be an integer", True)
            elif expected_type is str:
                if not isinstance(val, str):
                    return (board_copy, f"error: argument '{arg_name}' must be a string", True)
            else:
                if not isinstance(val, expected_type):
                    return (board_copy, f"error: argument '{arg_name}' must be a {expected_type.__name__}", True)

    # 4. Check if implemented in HANDLERS
    if name not in HANDLERS:
        return (board_copy, f"error: tool '{name}' is not implemented yet", True)

    # 5. Execute handler with outermost exception guard
    try:
        handler = HANDLERS[name]
        output, is_error = handler(board_copy, args)
    except Exception as exc:
        return (board_copy, f"error: internal error in {name}: {type(exc).__name__}: {exc}", True)

    return (board_copy, clip(output), is_error)
