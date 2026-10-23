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


# ---------------------------------------------------------------------------
# Private bin adapters: the ONLY places that touch PARTS_BIN / lookup
# ---------------------------------------------------------------------------
def _bin_get(part_no: str) -> dict | None:
    """Look up a single part from the bin. Handles dict-of-parts and list-of-parts."""
    from common.circuit.parts import PARTS_BIN, lookup

    try:
        item = lookup(part_no)
        if item is not None:
            d = dict(item) if isinstance(item, dict) else dict(getattr(item, "__dict__", {}))
            d.setdefault("part_no", part_no)
            return d
    except (KeyError, LookupError):
        pass

    if isinstance(PARTS_BIN, dict):
        if part_no in PARTS_BIN:
            item = PARTS_BIN[part_no]
            d = dict(item) if isinstance(item, dict) else dict(getattr(item, "__dict__", {}))
            d.setdefault("part_no", part_no)
            return d
    elif isinstance(PARTS_BIN, (list, tuple)):
        for item in PARTS_BIN:
            pno = item.get("part_no") if isinstance(item, dict) else getattr(item, "part_no", None)
            if pno == part_no:
                d = dict(item) if isinstance(item, dict) else dict(getattr(item, "__dict__", {}))
                return d

    return None


def _bin_all(kind: str) -> list[dict]:
    """Retrieve all parts of the specified kind from PARTS_BIN."""
    from common.circuit.parts import PARTS_BIN

    items: list[dict] = []
    if isinstance(PARTS_BIN, dict):
        for k, v in PARTS_BIN.items():
            d = dict(v) if isinstance(v, dict) else dict(getattr(v, "__dict__", {}))
            d.setdefault("part_no", k)
            items.append(d)
    elif isinstance(PARTS_BIN, (list, tuple)):
        for v in PARTS_BIN:
            d = dict(v) if isinstance(v, dict) else dict(getattr(v, "__dict__", {}))
            items.append(d)

    return [d for d in items if d.get("kind") == kind]


# ---------------------------------------------------------------------------
# Read-only tools (B1.1)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Mutating, power, measurement and catalog tools (B1.2)
# ---------------------------------------------------------------------------
def _tool_finish(board: dict, args: dict) -> tuple[str, bool]:
    return ("finish received", False)


def _tool_power_off(board: dict, args: dict) -> tuple[str, bool]:
    board["powered"] = False
    board.setdefault("history", []).append({"tool": "power_off", "args": dict(args)})
    return ("board powered off", False)


def _tool_power_on(board: dict, args: dict) -> tuple[str, bool]:
    if board.get("powered", False):
        board.setdefault("history", []).append({"tool": "power_on", "args": dict(args)})
        return ("board already powered", False)

    from common.circuit.solver import solve

    sol = solve(board)
    sol_p = sol.get("p", {})
    sol_i = sol.get("i", {})

    parts_dict = board.get("parts") or {}
    damaged_set = set(board.get("damaged") or [])
    newly_damaged: list[str] = []

    for ref, part in parts_dict.items():
        if not part.get("populated", True):
            continue
        if ref in damaged_set:
            continue

        rating_w = part.get("rating_w", float("inf"))
        p = sol_p.get(ref, 0.0)
        is_damaged = False

        if p > 1.5 * rating_w:
            is_damaged = True
        elif part.get("kind") == "led":
            imax = part.get("imax", float("inf"))
            i = sol_i.get(ref, 0.0)
            if abs(i) > 1.5 * imax:
                is_damaged = True

        if is_damaged:
            newly_damaged.append(ref)

    newly_damaged.sort(key=natural_sort_key)

    current_damaged = list(board.get("damaged") or [])
    board["damaged"] = sorted(list(set(current_damaged + newly_damaged)), key=natural_sort_key)
    board["powered"] = True
    board.setdefault("history", []).append({"tool": "power_on", "args": dict(args)})

    lines = ["board powered"]
    for ref in newly_damaged:
        lines.append(f"smoke from {ref}")
    return ("\n".join(lines), False)


def _tool_measure_voltage(board: dict, args: dict) -> tuple[str, bool]:
    if not board.get("powered", False):
        return ("error: board is not powered; call power_on", True)

    net = args["net"]
    all_nets = set()
    for part in (board.get("parts") or {}).values():
        all_nets.update(part.get("nets", []))
    for pin_info in (board.get("pins") or {}).values():
        if pin_info.get("net"):
            all_nets.add(pin_info["net"])

    if net not in all_nets:
        return (f"error: no net '{net}'. Use read_schematic to see nets.", True)

    budget = board.get("probe_budget")
    used = board.get("probes_used", 0)
    if budget is not None and used >= budget:
        return (f"error: probe budget used up ({used}/{budget})", True)

    # Call solve() FIRST before mutating probes_used (failure atomicity)
    from common.circuit.solver import solve

    sol = solve(board)

    board["probes_used"] = used + 1
    v = sol.get("v", {}).get(net, 0.0)
    return (f"V({net}) = {fmt_volts(v)}", False)


def _tool_measure_current(board: dict, args: dict) -> tuple[str, bool]:
    if not board.get("powered", False):
        return ("error: board is not powered; call power_on", True)

    ref = args["ref"]
    parts_dict = board.get("parts") or {}
    if ref not in parts_dict:
        return (f"error: no part '{ref}'. Use list_parts to see refs.", True)

    budget = board.get("probe_budget")
    used = board.get("probes_used", 0)
    if budget is not None and used >= budget:
        return (f"error: probe budget used up ({used}/{budget})", True)

    # Call solve() FIRST before mutating probes_used (failure atomicity)
    from common.circuit.solver import solve

    sol = solve(board)

    board["probes_used"] = used + 1
    i = sol.get("i", {}).get(ref, 0.0)
    return (f"I({ref}) = {fmt_current(i)}", False)


def _tool_replace_part(board: dict, args: dict) -> tuple[str, bool]:
    if board.get("powered", False):
        return ("error: power off the board first", True)

    ref = args["ref"]
    part_no = args["part_no"]

    parts_dict = board.get("parts") or {}
    if ref not in parts_dict:
        return (f"error: no part '{ref}'. Use list_parts to see refs.", True)

    part = parts_dict[ref]
    part_kind = part.get("kind", "")
    if part_kind in ("source", "bridge"):
        return (f"error: {ref} cannot be replaced (sources and bridges have no bin part)", True)

    bin_part = _bin_get(part_no)
    if bin_part is None:
        return (f"error: part_no '{part_no}' is not in the parts bin. Use list_bin(kind).", True)

    bin_kind = bin_part.get("kind", "")
    if bin_kind != part_kind:
        return (f"error: part_no '{part_no}' is a {bin_kind}, but {ref} is a {part_kind}", True)

    if part_kind == "resistor":
        old_desc = fmt_ohms(part.get("value", 0.0))
        new_desc = fmt_ohms(bin_part.get("value", 0.0))
    elif part_kind == "led":
        old_desc = f"Vf {part.get('vf', 2.0):.2f} V"
        new_desc = f"Vf {bin_part.get('vf', 2.0):.2f} V"
    elif part_kind == "regulator":
        old_desc = f"{part.get('vout', 0.0):.2f} V out"
        new_desc = f"{bin_part.get('vout', 0.0):.2f} V out"
    else:
        old_desc = str(part.get("value", ""))
        new_desc = str(bin_part.get("value", ""))

    old_pno = part.get("part_no", "")

    part["part_no"] = part_no
    part["populated"] = True
    if "value" in bin_part:
        part["value"] = bin_part["value"]
    if "rating_w" in bin_part:
        part["rating_w"] = bin_part["rating_w"]

    for k in ("vf", "rd", "imax", "vout", "dropout", "ilim"):
        if k in bin_part:
            part[k] = bin_part[k]
        elif k in part and bin_kind not in ("led", "regulator"):
            del part[k]

    if "damaged" in board and board["damaged"]:
        board["damaged"] = [d for d in board["damaged"] if d != ref]

    board.setdefault("history", []).append({"tool": "replace_part", "args": dict(args)})
    return (f"{ref} replaced: {old_desc} ({old_pno}) -> {new_desc} ({part_no})", False)


def _tool_remove_bridge(board: dict, args: dict) -> tuple[str, bool]:
    if board.get("powered", False):
        return ("error: power off the board first", True)

    ref = args["ref"]
    parts_dict = board.get("parts") or {}
    if ref not in parts_dict:
        return (f"error: no part '{ref}'. Use list_parts to see refs.", True)

    part = parts_dict[ref]
    if part.get("kind") != "bridge":
        return (f"error: {ref} is not a bridge", True)

    if not part.get("populated", True):
        return (f"error: {ref} is already removed", True)

    part["populated"] = False
    board.setdefault("history", []).append({"tool": "remove_bridge", "args": dict(args)})
    return (f"{ref} removed", False)


def _tool_set_pin_mode(board: dict, args: dict) -> tuple[str, bool]:
    if board.get("powered", False):
        return ("error: power off the board first", True)

    pin = args["pin"]
    mode = args["mode"]

    pins_dict = board.get("pins") or {}
    if pin not in pins_dict:
        sorted_pins = sorted(pins_dict.keys(), key=natural_sort_key)
        return (f"error: no pin '{pin}'. Pins: {', '.join(sorted_pins)}.", True)

    valid_modes = ("output_high", "output_low", "input", "adc")
    if mode not in valid_modes:
        return (f"error: invalid mode '{mode}'. Use one of: output_high, output_low, input, adc", True)

    old_mode = pins_dict[pin].get("mode", "")
    pins_dict[pin]["mode"] = mode
    board.setdefault("history", []).append({"tool": "set_pin_mode", "args": dict(args)})
    return (f"{pin} mode: {old_mode} -> {mode}", False)


def _tool_list_bin(board: dict, args: dict) -> tuple[str, bool]:
    kind = args["kind"]
    if kind not in ("resistor", "led", "regulator"):
        return (f"error: invalid kind '{kind}'. Use one of: resistor, led, regulator", True)

    items = _bin_all(kind)
    lines: list[str] = []

    if kind == "resistor":
        items.sort(key=lambda x: (x.get("value", 0.0), natural_sort_key(str(x.get("part_no", "")))))
        for it in items:
            pno = it.get("part_no", "")
            val_str = fmt_ohms(it.get("value", 0.0))
            rw = it.get("rating_w")
            rw_str = f"{_fmt_sig3(rw)} W" if rw is not None else ""
            lines.append(f"{pno}  {val_str}  {rw_str}".rstrip())
    elif kind == "led":
        items.sort(key=lambda x: natural_sort_key(str(x.get("part_no", ""))))
        for it in items:
            pno = it.get("part_no", "")
            vf = it.get("vf", 0.0)
            imax = it.get("imax", 0.0)
            lines.append(f"{pno}  Vf {vf:.2f} V  imax {imax * 1000:.2f} mA")
    elif kind == "regulator":
        items.sort(key=lambda x: natural_sort_key(str(x.get("part_no", ""))))
        for it in items:
            pno = it.get("part_no", "")
            vout = it.get("vout", 0.0)
            ilim = it.get("ilim", 0.0)
            lines.append(f"{pno}  {vout:.2f} V out  ilim {ilim * 1000:.2f} mA")

    lines = lines[:60]
    return ("\n".join(lines), False)


HANDLERS: dict[str, Any] = {
    "read_spec": _tool_read_spec,
    "list_parts": _tool_list_parts,
    "inspect": _tool_inspect,
    "read_schematic": _tool_read_schematic,
    "list_bin": _tool_list_bin,
    "power_on": _tool_power_on,
    "power_off": _tool_power_off,
    "measure_voltage": _tool_measure_voltage,
    "measure_current": _tool_measure_current,
    "replace_part": _tool_replace_part,
    "remove_bridge": _tool_remove_bridge,
    "set_pin_mode": _tool_set_pin_mode,
    "finish": _tool_finish,
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

    # 5. Execute handler with outermost exception guard and failure atomicity
    try:
        handler = HANDLERS[name]
        output, is_error = handler(board_copy, args)
    except Exception as exc:
        return (copy.deepcopy(board), f"error: internal error in {name}: {type(exc).__name__}: {exc}", True)

    if is_error:
        return (copy.deepcopy(board), clip(output), True)

    return (board_copy, clip(output), False)
