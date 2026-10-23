"""Tests for common.circuit.actions (apply_tool, read-only tools, formatting helpers)."""

import copy
import pytest

from common.circuit.actions import (
    ARG_SCHEMA,
    HANDLERS,
    IMPLEMENTED_TOOLS,
    TOOL_NAMES,
    apply_tool,
    clip,
    fmt_current,
    fmt_ohms,
    fmt_volts,
)


def small_board():
    """Small test board matching the contract and spec examples."""
    return {
        "board_id": "test_small",
        "parts": {
            "V1": {
                "ref": "V1",
                "kind": "source",
                "value": 5.0,
                "part_no": "SRC-5V",
                "nets": ["5V", "GND"],
                "populated": True,
                "rating_w": 1.0,
            },
            "U1": {
                "ref": "U1",
                "kind": "regulator",
                "part_no": "REG-3V3",
                "nets": ["5V", "3V3", "GND"],
                "populated": True,
                "rating_w": 0.5,
                "vout": 3.3,
                "dropout": 0.3,
                "ilim": 0.5,
            },
            "R1": {
                "ref": "R1",
                "kind": "resistor",
                "value": 150.0,
                "part_no": "R-150",
                "nets": ["GPIO1", "LED1_A"],
                "populated": True,
                "rating_w": 0.25,
            },
            "D1": {
                "ref": "D1",
                "kind": "led",
                "part_no": "LED-RED",
                "nets": ["LED1_A", "GND"],
                "populated": True,
                "rating_w": 0.1,
                "vf": 2.0,
                "rd": 20.0,
                "imax": 0.02,
            },
            "R3": {
                "ref": "R3",
                "kind": "resistor",
                "value": 10000.0,
                "part_no": "R-10K",
                "nets": ["3V3", "SENSE"],
                "populated": True,
                "rating_w": 0.25,
            },
        },
        "pins": {
            "P1": {"net": "GPIO1", "mode": "output_high"},
            "P2": {"net": "3V3", "mode": "adc"},
        },
        "powered": False,
        "damaged": [],
        "probe_budget": None,
        "probes_used": 0,
        "spec": [
            {
                "id": "C1",
                "type": "current",
                "target": "D1",
                "min": 0.005,
                "max": 0.015,
                "desc": "LED D1 current",
            }
        ],
        "history": [],
    }


def big_board(n_parts: int, n_nets: int):
    """Generate a board with n_parts and n_nets for pagination tests."""
    parts = {}
    for i in range(1, n_parts + 1):
        net_a = f"NET{(i % n_nets) + 1}"
        net_b = f"NET{((i + 1) % n_nets) + 1}"
        parts[f"R{i}"] = {
            "ref": f"R{i}",
            "kind": "resistor",
            "value": float(i * 10),
            "part_no": f"R-{i}",
            "nets": [net_a, net_b],
            "populated": True,
            "rating_w": 0.25,
        }
    pins = {
        f"IO{j}": {"net": f"NET{j}", "mode": "input"}
        for j in range(1, n_nets + 1)
    }
    return {
        "board_id": "test_big",
        "parts": parts,
        "pins": pins,
        "powered": False,
        "damaged": [],
        "spec": [{"id": "C1", "type": "voltage", "target": "NET1", "min": 1.0, "max": 2.0, "desc": "rail"}],
        "history": [],
    }


VALID_ARGS = {
    "read_spec": {},
    "list_parts": {},
    "inspect": {"ref": "R1"},
    "read_schematic": {},
    "list_bin": {"kind": "resistor"},
    "power_on": {},
    "power_off": {},
    "measure_voltage": {"net": "5V"},
    "measure_current": {"ref": "R1"},
    "replace_part": {"ref": "R1", "part_no": "R-150"},
    "remove_bridge": {"ref": "R1"},
    "set_pin_mode": {"pin": "P1", "mode": "input"},
    "finish": {},
}


@pytest.mark.parametrize("tool_name", TOOL_NAMES)
def test_purity(tool_name):
    """Input board is never mutated; unchanged tools and error calls preserve new_board."""
    board = small_board()
    board_before = copy.deepcopy(board)
    args = VALID_ARGS[tool_name]

    new_board, output, is_error = apply_tool(board, tool_name, args)

    assert board == board_before
    assert new_board is not board
    assert isinstance(output, str)
    assert isinstance(is_error, bool)

    read_only_tools = {
        "read_spec",
        "list_parts",
        "inspect",
        "read_schematic",
        "list_bin",
        "finish",
    }
    if is_error or tool_name in read_only_tools:
        assert new_board == board_before


def test_never_raises_on_garbage_args():
    """Garbage args and garbage tool names never raise, returning is_error=True."""
    board = small_board()

    garbage_names = [None, 5, ["inspect"], {"a": 1}, True]
    for bad_name in garbage_names:
        new_board, out, is_err = apply_tool(board, bad_name, {})
        assert is_err is True
        assert out.startswith("error: unknown tool")

    garbage_args = [None, 123, "string", [1, 2], True]
    for bad_arg in garbage_args:
        new_board, out, is_err = apply_tool(board, "inspect", bad_arg)
        assert is_err is True
        assert out == "error: arguments must be a dictionary"


def test_unknown_tool():
    board = small_board()
    new_board, out, is_err = apply_tool(board, "foobar", {})
    assert is_err is True
    assert out == "error: unknown tool 'foobar'"


def test_all_tools_implemented():
    assert IMPLEMENTED_TOOLS == set(TOOL_NAMES)


def test_missing_unknown_wrong_type_args():
    board = small_board()

    # Missing required 'ref' for inspect
    _, out, is_err = apply_tool(board, "inspect", {})
    assert is_err is True
    assert out == "error: missing argument 'ref' for inspect"

    # Unexpected argument for inspect
    _, out, is_err = apply_tool(board, "inspect", {"ref": "R1", "foo": 1})
    assert is_err is True
    assert out == "error: unexpected argument 'foo' for inspect"

    # Wrong type for 'ref'
    _, out, is_err = apply_tool(board, "inspect", {"ref": 123})
    assert is_err is True
    assert out == "error: argument 'ref' must be a string"

    # Wrong type for 'page' in list_parts (bool is not int)
    _, out, is_err = apply_tool(board, "list_parts", {"page": True})
    assert is_err is True
    assert out == "error: argument 'page' must be an integer"

    # String type for 'page' in list_parts
    _, out, is_err = apply_tool(board, "list_parts", {"page": "1"})
    assert is_err is True
    assert out == "error: argument 'page' must be an integer"

    # Unexpected argument for read_spec
    _, out, is_err = apply_tool(board, "read_spec", {"foo": 1})
    assert is_err is True
    assert out == "error: unexpected argument 'foo' for read_spec"

    # Bool type for 'page' in read_schematic
    _, out, is_err = apply_tool(board, "read_schematic", {"page": False})
    assert is_err is True
    assert out == "error: argument 'page' must be an integer"

    # Non-string key in args
    _, out, is_err = apply_tool(board, "inspect", {1: "R1", "ref": "R1"})
    assert is_err is True
    assert out == "error: unexpected argument '1' for inspect"


def test_clip_exact_length():
    short = "hello world"
    assert clip(short) == short

    exact_2000 = "x" * 2000
    assert clip(exact_2000) == exact_2000
    assert len(clip(exact_2000)) == 2000

    over_2001 = "x" * 2001
    clipped_2001 = clip(over_2001)
    assert len(clipped_2001) == 2000
    assert clipped_2001.endswith("  [cut] ")

    over_5000 = "x" * 5000
    clipped_5000 = clip(over_5000)
    assert len(clipped_5000) == 2000
    assert clipped_5000.endswith("  [cut] ")


def test_internal_error_guard(monkeypatch):
    def boom(board, args):
        raise RuntimeError("database exploded")

    monkeypatch.setitem(HANDLERS, "inspect", boom)
    board = small_board()
    _, out, is_err = apply_tool(board, "inspect", {"ref": "R1"})
    assert is_err is True
    assert out == "error: internal error in inspect: RuntimeError: database exploded"


@pytest.mark.parametrize(
    "ohms,expected",
    [
        (150, "150 Ω"),
        (4.7, "4.7 Ω"),
        (1500, "1.5 kΩ"),
        (10000, "10 kΩ"),
        (1000000, "1 MΩ"),
        (0.01, "0.01 Ω"),
        (999.7, "1 kΩ"),
        (0, "0 Ω"),
    ],
)
def test_fmt_ohms(ohms, expected):
    assert fmt_ohms(ohms) == expected


@pytest.mark.parametrize(
    "volts,expected",
    [
        (3.287, "3.287 V"),
        (5.0, "5.000 V"),
        (0.0, "0.000 V"),
    ],
)
def test_fmt_volts(volts, expected):
    assert fmt_volts(volts) == expected


@pytest.mark.parametrize(
    "current,expected",
    [
        (0.00538, "5.38 mA"),
        (1.25, "1.250 A"),
        (-0.00538, "-5.38 mA"),
        (-1.25, "-1.250 A"),
        (0.0, "0.00 mA"),
    ],
)
def test_fmt_current(current, expected):
    assert fmt_current(current) == expected


def test_read_spec_example():
    board = small_board()
    _, out, is_err = apply_tool(board, "read_spec", {})
    assert is_err is False
    assert out == "C1 current D1: 5.0–15.0 mA (LED D1 current)"


def test_read_spec_missing():
    board = small_board()
    del board["spec"]
    _, out, is_err = apply_tool(board, "read_spec", {})
    assert is_err is True
    assert out == "error: spec not loaded"

    board["spec"] = []
    _, out, is_err = apply_tool(board, "read_spec", {})
    assert is_err is True
    assert out == "error: spec not loaded"


def test_read_spec_null_bounds():
    board = small_board()
    board["spec"] = [
        {"id": "C1", "type": "voltage", "target": "3V3", "min": 3.0, "max": None, "desc": "min volt"},
        {"id": "C2", "type": "voltage", "target": "3V3", "min": None, "max": 3.6, "desc": "max volt"},
        {"id": "C3", "type": "voltage", "target": "3V3", "min": None, "max": None, "desc": "any volt"},
        {"id": "C4", "type": "current", "target": "D1", "min": 0.010, "max": None, "desc": "min curr"},
        {"id": "C5", "type": "current", "target": "D1", "min": None, "max": 0.020, "desc": "max curr"},
        {"id": "C6", "type": "current", "target": "D1", "min": None, "max": None, "desc": "any curr"},
        {"id": "C7", "type": "pin_mode", "target": "P1", "mode": "output_high"},
        {"id": "C8", "type": "populated", "target": "R1"},
        {"id": "C9", "type": "custom_check", "target": "X1"},
    ]
    _, out, is_err = apply_tool(board, "read_spec", {})
    assert is_err is False
    lines = out.split("\n")
    assert lines[0] == "C1 voltage 3V3: >= 3.000 V (min volt)"
    assert lines[1] == "C2 voltage 3V3: <= 3.600 V (max volt)"
    assert lines[2] == "C3 voltage 3V3: any (any volt)"
    assert lines[3] == "C4 current D1: >= 10.0 mA (min curr)"
    assert lines[4] == "C5 current D1: <= 20.0 mA (max curr)"
    assert lines[5] == "C6 current D1: any (any curr)"
    assert lines[6] == "C7 pin_mode P1: output_high"
    assert lines[7] == "C8 populated R1: yes"
    assert lines[8] == "C9 custom_check X1"


def test_list_parts_format():
    board = small_board()
    _, out, is_err = apply_tool(board, "list_parts", {})
    assert is_err is False
    lines = out.split("\n")
    assert "R1  resistor  150 Ω  R-150  GPIO1–LED1_A" in lines
    assert lines[-1] == "page 1/1"


def test_list_parts_pagination():
    board = big_board(n_parts=18, n_nets=2)

    _, out1, is_err1 = apply_tool(board, "list_parts", {"page": 1})
    assert is_err1 is False
    lines1 = out1.split("\n")
    assert len(lines1) == 16  # 15 parts + footer
    assert lines1[-1] == "page 1/2"

    _, out2, is_err2 = apply_tool(board, "list_parts", {"page": 2})
    assert is_err2 is False
    lines2 = out2.split("\n")
    assert len(lines2) == 4  # 3 parts + footer
    assert lines2[-1] == "page 2/2"


def test_natural_sort():
    board = small_board()
    board["parts"] = {
        "R10": {"ref": "R10", "kind": "resistor", "value": 100.0, "part_no": "R-100", "nets": ["A", "B"]},
        "R2": {"ref": "R2", "kind": "resistor", "value": 20.0, "part_no": "R-20", "nets": ["A", "B"]},
        "R1": {"ref": "R1", "kind": "resistor", "value": 10.0, "part_no": "R-10", "nets": ["A", "B"]},
    }
    _, out, is_err = apply_tool(board, "list_parts", {})
    assert is_err is False
    lines = out.split("\n")
    refs = [line.split()[0] for line in lines[:-1]]
    assert refs == ["R1", "R2", "R10"]


def test_list_parts_page_out_of_range():
    board = small_board()
    _, out, is_err = apply_tool(board, "list_parts", {"page": 9})
    assert is_err is True
    assert out == "error: page 9 out of range (1-1). Use list_parts(page=1)."


def test_inspect_resistor():
    board = small_board()
    _, out, is_err = apply_tool(board, "inspect", {"ref": "R3"})
    assert is_err is False
    expected = "\n".join([
        "R3: resistor",
        "value: 10 kΩ",
        "part_no: R-10K",
        "nets: 1=3V3 2=SENSE",
        "rating: 0.25 W",
        "populated: yes",
        "damaged: no",
    ])
    assert out == expected


def test_inspect_led():
    board = small_board()
    _, out, is_err = apply_tool(board, "inspect", {"ref": "D1"})
    assert is_err is False
    expected = "\n".join([
        "D1: led",
        "part_no: LED-RED",
        "nets: A=LED1_A K=GND",
        "rating: 0.1 W",
        "populated: yes",
        "damaged: no",
        "vf 2.000 V, rd 20 Ω, imax 20.00 mA",
    ])
    assert out == expected


def test_inspect_regulator():
    board = small_board()
    _, out, is_err = apply_tool(board, "inspect", {"ref": "U1"})
    assert is_err is False
    expected = "\n".join([
        "U1: regulator",
        "part_no: REG-3V3",
        "nets: vin=5V vout=3V3 gnd=GND",
        "rating: 0.5 W",
        "populated: yes",
        "damaged: no",
        "vout 3.300 V, dropout 0.300 V, ilim 500.00 mA",
    ])
    assert out == expected


def test_inspect_source_and_bridge():
    board = small_board()
    # Source
    _, out_src, is_err_src = apply_tool(board, "inspect", {"ref": "V1"})
    assert is_err_src is False
    assert "V1: source" in out_src
    assert "value: 5.000 V" in out_src
    assert "nets: +=5V -=GND" in out_src

    # Add bridge, damaged part, unpopulated part
    board["parts"]["B1"] = {
        "ref": "B1",
        "kind": "bridge",
        "value": 0.01,
        "part_no": "BR-0",
        "nets": ["NET_A", "NET_B"],
        "rating_w": 1.0,
        "populated": False,
    }
    board["damaged"] = ["B1"]

    _, out_br, is_err_br = apply_tool(board, "inspect", {"ref": "B1"})
    assert is_err_br is False
    assert "B1: bridge" in out_br
    assert "value: 0.01 Ω" in out_br
    assert "nets: 1=NET_A 2=NET_B" in out_br
    assert "populated: no" in out_br
    assert "damaged: yes" in out_br


def test_inspect_unknown_ref():
    board = small_board()
    _, out, is_err = apply_tool(board, "inspect", {"ref": "R9"})
    assert is_err is True
    assert out == "error: no part 'R9'. Use list_parts to see refs."


def test_schematic_example():
    board = small_board()
    _, out, is_err = apply_tool(board, "read_schematic", {})
    assert is_err is False
    lines = out.split("\n")
    assert "3V3: U1.vout, R3.1, MCU.P2(adc)" in lines
    assert "GND: V1.-, U1.gnd, D1.K" in lines
    assert lines[-1] == "page 1/1"


def test_schematic_pagination():
    board = big_board(n_parts=30, n_nets=25)
    _, out1, is_err1 = apply_tool(board, "read_schematic", {"page": 1})
    assert is_err1 is False
    lines1 = out1.split("\n")
    assert len(lines1) == 11  # 10 nets + footer
    assert lines1[-1] == "page 1/3"

    _, out2, is_err2 = apply_tool(board, "read_schematic", {"page": 2})
    assert is_err2 is False
    lines2 = out2.split("\n")
    assert len(lines2) == 11
    assert lines2[-1] == "page 2/3"

    _, out3, is_err3 = apply_tool(board, "read_schematic", {"page": 3})
    assert is_err3 is False
    lines3 = out3.split("\n")
    assert len(lines3) == 6  # 5 nets + footer
    assert lines3[-1] == "page 3/3"


def test_schematic_page_out_of_range():
    board = small_board()
    _, out, is_err = apply_tool(board, "read_schematic", {"page": 9})
    assert is_err is True
    assert out == "error: page 9 out of range (1-1). Use read_schematic(page=1)."


def test_page_zero_and_negative():
    board = small_board()
    for bad_page in [0, -1]:
        _, out_p, is_err_p = apply_tool(board, "list_parts", {"page": bad_page})
        assert is_err_p is True
        assert out_p == f"error: page {bad_page} out of range (1-1). Use list_parts(page=1)."

        _, out_s, is_err_s = apply_tool(board, "read_schematic", {"page": bad_page})
        assert is_err_s is True
        assert out_s == f"error: page {bad_page} out of range (1-1). Use read_schematic(page=1)."


def test_empty_board():
    empty = {
        "board_id": "empty",
        "parts": {},
        "pins": {},
        "spec": [],
    }
    _, out_p, is_err_p = apply_tool(empty, "list_parts", {})
    assert is_err_p is False
    assert out_p == "page 1/1"

    _, out_s, is_err_s = apply_tool(empty, "read_schematic", {})
    assert is_err_s is False
    assert out_s == "page 1/1"


def test_apply_tool_clips_output():
    # 300 members on one net
    board = {
        "board_id": "huge",
        "parts": {
            f"R{i}": {
                "ref": f"R{i}",
                "kind": "resistor",
                "value": 100.0,
                "part_no": "R-100",
                "nets": ["LONG_NET", "GND"],
            }
            for i in range(1, 301)
        },
        "pins": {},
    }
    _, out, is_err = apply_tool(board, "read_schematic", {})
    assert is_err is False
    assert len(out) <= 2000
    assert out.endswith("  [cut] ")


def test_success_is_not_error():
    board = small_board()
    for name, args in [
        ("read_spec", {}),
        ("list_parts", {}),
        ("inspect", {"ref": "R1"}),
        ("read_schematic", {}),
    ]:
        _, _, is_err = apply_tool(board, name, args)
        assert is_err is False


def test_new_board_is_a_copy():
    board = small_board()
    new_board, _, _ = apply_tool(board, "inspect", {"ref": "R1"})
    assert board is not new_board
    new_board["mutated"] = True
    assert "mutated" not in board


def test_garbage_board():
    _, out1, is_err1 = apply_tool(None, "read_spec", {})
    assert is_err1 is True
    assert out1.startswith("error:")

    _, out2, apply_err2 = apply_tool("not_a_board", "inspect", {"ref": "R1"})
    assert apply_err2 is True
    assert out2.startswith("error:")


def test_deterministic_twice():
    board = small_board()
    for name, args in [
        ("read_spec", {}),
        ("list_parts", {}),
        ("inspect", {"ref": "R1"}),
        ("read_schematic", {}),
    ]:
        _, out1, err1 = apply_tool(board, name, args)
        _, out2, err2 = apply_tool(board, name, args)
        assert out1 == out2
        assert err1 == err2
