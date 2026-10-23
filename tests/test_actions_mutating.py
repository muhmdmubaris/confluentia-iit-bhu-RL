"""Tests for mutating tools, power/damage, measurement, and bin catalog in common.circuit.actions."""

import copy
import sys
import types
import pytest

from common.circuit.actions import apply_tool
from tests.test_actions import small_board


@pytest.fixture
def fake_cc1(monkeypatch):
    """Inject fake common.circuit.parts and common.circuit.solver for isolated actions testing."""
    parts_mod = types.ModuleType("common.circuit.parts")
    PARTS_BIN = {
        "R-150": {"part_no": "R-150", "kind": "resistor", "value": 150.0, "rating_w": 0.25},
        "R-1K5": {"part_no": "R-1K5", "kind": "resistor", "value": 1500.0, "rating_w": 0.25},
        "R-10K": {"part_no": "R-10K", "kind": "resistor", "value": 10000.0, "rating_w": 0.25},
        "LED-RED": {"part_no": "LED-RED", "kind": "led", "vf": 2.0, "rd": 20.0, "imax": 0.02, "rating_w": 0.1},
        "REG-3V3": {"part_no": "REG-3V3", "kind": "regulator", "vout": 3.3, "dropout": 0.3, "ilim": 0.5, "rating_w": 0.5},
    }

    def lookup(part_no):
        if part_no in PARTS_BIN:
            return PARTS_BIN[part_no]
        raise KeyError(part_no)

    parts_mod.PARTS_BIN = PARTS_BIN
    parts_mod.lookup = lookup
    monkeypatch.setitem(sys.modules, "common.circuit.parts", parts_mod)

    solver_mod = types.ModuleType("common.circuit.solver")

    def solve(board):
        return {
            "v": {"5V": 5.0, "3V3": 3.287, "GND": 0.0, "GPIO1": 3.3, "LED1_A": 2.0, "SENSE": 1.65},
            "i": {"V1": 0.02, "U1": 0.015, "R1": 0.008, "D1": 0.00538, "R3": 0.000165},
            "p": {"V1": 0.1, "U1": 0.025, "R1": 0.0096, "D1": 0.01076, "R3": 0.00027},
            "converged": True,
            "notes": [],
        }

    solver_mod.solve = solve
    monkeypatch.setitem(sys.modules, "common.circuit.solver", solver_mod)


def test_finish():
    board = small_board()
    new_b, out, is_err = apply_tool(board, "finish", {})
    assert is_err is False
    assert out == "finish received"
    assert new_b.get("history") == []


def test_power_off_and_history():
    board = small_board()
    board["powered"] = True
    new_b, out, is_err = apply_tool(board, "power_off", {})
    assert is_err is False
    assert out == "board powered off"
    assert new_b["powered"] is False
    assert new_b["history"] == [{"tool": "power_off", "args": {}}]


def test_power_on_already_powered():
    board = small_board()
    board["powered"] = True
    new_b, out, is_err = apply_tool(board, "power_on", {})
    assert is_err is False
    assert out == "board already powered"
    assert new_b["powered"] is True
    assert new_b["history"] == [{"tool": "power_on", "args": {}}]


def test_power_on_history_when_already_powered():
    board = small_board()
    board["powered"] = True
    board["history"] = [{"tool": "power_on", "args": {}}]
    new_b, out, is_err = apply_tool(board, "power_on", {})
    assert is_err is False
    assert len(new_b["history"]) == 2
    assert new_b["history"][-1] == {"tool": "power_on", "args": {}}


def test_power_on_marks_damage_sorted_irreversible_fake(fake_cc1, monkeypatch):
    import common.circuit.solver as solver_mod

    board = small_board()
    # R1: rating_w 0.25 -> 1.5 * 0.25 = 0.375 W
    # D1: imax 0.02 A -> 1.5 * 0.02 = 0.03 A
    def overloaded_solve(b):
        return {
            "v": {},
            "i": {"D1": 0.035, "R1": 0.05},
            "p": {"R1": 0.40, "D1": 0.01},
            "converged": True,
            "notes": [],
        }

    monkeypatch.setattr(solver_mod, "solve", overloaded_solve)

    # First power on: damages D1 and R1 (natural sorted: D1, R1)
    new_b, out, is_err = apply_tool(board, "power_on", {})
    assert is_err is False
    assert "board powered" in out
    assert "smoke from D1" in out
    assert "smoke from R1" in out
    lines = out.split("\n")
    assert lines == ["board powered", "smoke from D1", "smoke from R1"]
    assert new_b["damaged"] == ["D1", "R1"]
    assert new_b["powered"] is True

    # Power off
    off_b, _, _ = apply_tool(new_b, "power_off", {})
    assert off_b["powered"] is False
    assert off_b["damaged"] == ["D1", "R1"]

    # Power on again: D1 and R1 remain damaged, but no new smoke
    on_again, out2, _ = apply_tool(off_b, "power_on", {})
    assert on_again["damaged"] == ["D1", "R1"]
    assert out2 == "board powered"


def test_power_on_damage_real():
    pytest.importorskip("common.circuit.solver")
    board = small_board()
    new_b, out, is_err = apply_tool(board, "power_on", {})
    assert isinstance(out, str)
    assert isinstance(is_err, bool)


def test_measure_requires_power():
    board = small_board()
    board["powered"] = False

    _, out_v, is_err_v = apply_tool(board, "measure_voltage", {"net": "3V3"})
    assert is_err_v is True
    assert out_v == "error: board is not powered; call power_on"

    _, out_i, is_err_i = apply_tool(board, "measure_current", {"ref": "R1"})
    assert is_err_i is True
    assert out_i == "error: board is not powered; call power_on"


def test_measure_unknown_net_and_ref():
    board = small_board()
    board["powered"] = True

    _, out_v, is_err_v = apply_tool(board, "measure_voltage", {"net": "UNKNOWN_NET"})
    assert is_err_v is True
    assert out_v == "error: no net 'UNKNOWN_NET'. Use read_schematic to see nets."

    _, out_i, is_err_i = apply_tool(board, "measure_current", {"ref": "R9"})
    assert is_err_i is True
    assert out_i == "error: no part 'R9'. Use list_parts to see refs."


def test_probe_budget_exhausted_and_errors_cost_no_probe():
    board = small_board()
    board["powered"] = True
    board["probe_budget"] = 14
    board["probes_used"] = 14

    new_b, out, is_err = apply_tool(board, "measure_voltage", {"net": "3V3"})
    assert is_err is True
    assert out == "error: probe budget used up (14/14)"
    assert new_b["probes_used"] == 14

    # Precondition errors cost no probe
    new_b2, out2, is_err2 = apply_tool(board, "measure_voltage", {"net": "UNKNOWN"})
    assert is_err2 is True
    assert new_b2["probes_used"] == 14


def test_measure_unlimited_budget_null_fake(fake_cc1):
    board = small_board()
    board["powered"] = True
    board["probe_budget"] = None
    board["probes_used"] = 0

    new_b, out, is_err = apply_tool(board, "measure_voltage", {"net": "3V3"})
    assert is_err is False
    assert out == "V(3V3) = 3.287 V"
    assert new_b["probes_used"] == 1
    assert "history" not in new_b or new_b["history"] == []


def test_measure_voltage_real():
    pytest.importorskip("common.circuit.solver")
    board = small_board()
    board["powered"] = True
    new_b, out, is_err = apply_tool(board, "measure_voltage", {"net": "3V3"})
    assert isinstance(out, str)


def test_measure_current_unpopulated_part(fake_cc1, monkeypatch):
    import common.circuit.solver as solver_mod

    # Part R4 is unpopulated/missing from solve()["i"]
    def sparse_solve(b):
        return {"v": {}, "i": {}, "p": {}, "converged": True, "notes": []}

    monkeypatch.setattr(solver_mod, "solve", sparse_solve)

    board = small_board()
    board["powered"] = True
    board["parts"]["R4"] = {
        "ref": "R4",
        "kind": "resistor",
        "value": 100.0,
        "part_no": "R-100",
        "nets": ["A", "B"],
        "populated": False,
    }
    _, out, is_err = apply_tool(board, "measure_current", {"ref": "R4"})
    assert is_err is False
    assert out == "I(R4) = 0.00 mA"


def test_exception_in_solver_costs_no_probe(fake_cc1, monkeypatch):
    import common.circuit.solver as solver_mod

    def boom(b):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(solver_mod, "solve", boom)

    board = small_board()
    board["powered"] = True
    board["probes_used"] = 5

    new_b, out, is_err = apply_tool(board, "measure_voltage", {"net": "3V3"})
    assert is_err is True
    assert "error: internal error in measure_voltage: RuntimeError: solver exploded" in out
    assert new_b["probes_used"] == 5
    assert new_b == board


def test_replace_requires_power_off():
    board = small_board()
    board["powered"] = True
    _, out, is_err = apply_tool(board, "replace_part", {"ref": "R1", "part_no": "R-150"})
    assert is_err is True
    assert out == "error: power off the board first"


def test_replace_errors(fake_cc1):
    board = small_board()
    board["powered"] = False

    # Unknown ref
    _, out1, is_err1 = apply_tool(board, "replace_part", {"ref": "R9", "part_no": "R-150"})
    assert is_err1 is True
    assert out1 == "error: no part 'R9'. Use list_parts to see refs."

    # Source or bridge cannot be replaced
    _, out2, is_err2 = apply_tool(board, "replace_part", {"ref": "V1", "part_no": "SRC-5V"})
    assert is_err2 is True
    assert out2 == "error: V1 cannot be replaced (sources and bridges have no bin part)"

    # Part not in bin
    _, out3, is_err3 = apply_tool(board, "replace_part", {"ref": "R1", "part_no": "NONEXISTENT"})
    assert is_err3 is True
    assert out3 == "error: part_no 'NONEXISTENT' is not in the parts bin. Use list_bin(kind)."

    # Kind mismatch
    _, out4, is_err4 = apply_tool(board, "replace_part", {"ref": "R1", "part_no": "LED-RED"})
    assert is_err4 is True
    assert out4 == "error: part_no 'LED-RED' is a led, but R1 is a resistor"


def test_replace_success_and_clears_damage_fake(fake_cc1):
    board = small_board()
    board["powered"] = False
    board["damaged"] = ["R1", "D1"]

    new_b, out, is_err = apply_tool(board, "replace_part", {"ref": "R1", "part_no": "R-1K5"})
    assert is_err is False
    assert out == "R1 replaced: 150 Ω (R-150) -> 1.5 kΩ (R-1K5)"
    assert new_b["parts"]["R1"]["part_no"] == "R-1K5"
    assert new_b["parts"]["R1"]["value"] == 1500.0
    assert new_b["damaged"] == ["D1"]
    assert new_b["history"] == [{"tool": "replace_part", "args": {"ref": "R1", "part_no": "R-1K5"}}]


def test_replace_clears_damage_then_power_on_ok(fake_cc1):
    board = small_board()
    board["damaged"] = ["R1"]
    board["powered"] = False

    replaced_b, _, _ = apply_tool(board, "replace_part", {"ref": "R1", "part_no": "R-150"})
    assert "R1" not in replaced_b["damaged"]

    on_b, out, is_err = apply_tool(replaced_b, "power_on", {})
    assert is_err is False
    assert "smoke from R1" not in out


def test_replace_matches_bin_values_fake(fake_cc1):
    board = small_board()
    board["powered"] = False

    # Replace regulator
    new_b, out, is_err = apply_tool(board, "replace_part", {"ref": "U1", "part_no": "REG-3V3"})
    assert is_err is False
    assert "U1 replaced:" in out
    u1 = new_b["parts"]["U1"]
    assert u1["vout"] == 3.3
    assert u1["dropout"] == 0.3
    assert u1["ilim"] == 0.5
    assert u1["rating_w"] == 0.5


def test_replace_matches_bin_values_real():
    pytest.importorskip("common.circuit.parts")
    board = small_board()
    board["powered"] = False
    # If parts.py exists, test lookup works without crash
    from common.circuit.parts import PARTS_BIN
    assert PARTS_BIN is not None


def test_remove_bridge_all_paths():
    board = small_board()
    board["parts"]["JB1"] = {
        "ref": "JB1",
        "kind": "bridge",
        "value": 0.01,
        "part_no": "BR-0",
        "nets": ["NETA", "NETB"],
        "populated": True,
    }

    # Powered error
    board["powered"] = True
    _, out1, is_err1 = apply_tool(board, "remove_bridge", {"ref": "JB1"})
    assert is_err1 is True
    assert out1 == "error: power off the board first"

    board["powered"] = False
    # Unknown ref
    _, out2, is_err2 = apply_tool(board, "remove_bridge", {"ref": "JB9"})
    assert is_err2 is True
    assert out2 == "error: no part 'JB9'. Use list_parts to see refs."

    # Successful removal
    new_b, out3, is_err3 = apply_tool(board, "remove_bridge", {"ref": "JB1"})
    assert is_err3 is False
    assert out3 == "JB1 removed"
    assert new_b["parts"]["JB1"]["populated"] is False
    assert new_b["history"] == [{"tool": "remove_bridge", "args": {"ref": "JB1"}}]

    # Already removed error
    _, out4, is_err4 = apply_tool(new_b, "remove_bridge", {"ref": "JB1"})
    assert is_err4 is True
    assert out4 == "error: JB1 is already removed"


def test_remove_bridge_on_non_bridge():
    board = small_board()
    board["powered"] = False
    _, out, is_err = apply_tool(board, "remove_bridge", {"ref": "R1"})
    assert is_err is True
    assert out == "error: R1 is not a bridge"


def test_set_pin_mode_all_paths():
    board = small_board()

    # Powered error
    board["powered"] = True
    _, out1, is_err1 = apply_tool(board, "set_pin_mode", {"pin": "P1", "mode": "input"})
    assert is_err1 is True
    assert out1 == "error: power off the board first"

    board["powered"] = False
    # Unknown pin with natural sorted pin list
    _, out2, is_err2 = apply_tool(board, "set_pin_mode", {"pin": "P9", "mode": "input"})
    assert is_err2 is True
    assert out2 == "error: no pin 'P9'. Pins: P1, P2."

    # Invalid mode
    _, out3, is_err3 = apply_tool(board, "set_pin_mode", {"pin": "P1", "mode": "floating"})
    assert is_err3 is True
    assert out3 == "error: invalid mode 'floating'. Use one of: output_high, output_low, input, adc"

    # Successful transition
    new_b, out4, is_err4 = apply_tool(board, "set_pin_mode", {"pin": "P1", "mode": "input"})
    assert is_err4 is False
    assert out4 == "P1 mode: output_high -> input"
    assert new_b["pins"]["P1"]["mode"] == "input"
    assert new_b["history"] == [{"tool": "set_pin_mode", "args": {"pin": "P1", "mode": "input"}}]


def test_set_pin_mode_same_mode():
    board = small_board()
    board["powered"] = False
    board["pins"]["P1"]["mode"] = "input"

    new_b, out, is_err = apply_tool(board, "set_pin_mode", {"pin": "P1", "mode": "input"})
    assert is_err is False
    assert out == "P1 mode: input -> input"
    assert new_b["history"] == [{"tool": "set_pin_mode", "args": {"pin": "P1", "mode": "input"}}]


def test_list_bin_each_kind_and_cap_60_fake(fake_cc1):
    board = small_board()

    # Invalid kind
    _, out_err, is_err = apply_tool(board, "list_bin", {"kind": "transistor"})
    assert is_err is True
    assert out_err == "error: invalid kind 'transistor'. Use one of: resistor, led, regulator"

    # Resistors
    _, out_r, is_err_r = apply_tool(board, "list_bin", {"kind": "resistor"})
    assert is_err_r is False
    lines_r = out_r.split("\n")
    assert "R-150  150 Ω  0.25 W" in lines_r
    assert "R-1K5  1.5 kΩ  0.25 W" in lines_r
    assert "R-10K  10 kΩ  0.25 W" in lines_r

    # LED
    _, out_led, is_err_led = apply_tool(board, "list_bin", {"kind": "led"})
    assert is_err_led is False
    assert "LED-RED  Vf 2.00 V  imax 20.00 mA" in out_led

    # Regulator
    _, out_reg, is_err_reg = apply_tool(board, "list_bin", {"kind": "regulator"})
    assert is_err_reg is False
    assert "REG-3V3  3.30 V out  ilim 500.00 mA" in out_reg


def test_list_bin_cap_60(fake_cc1, monkeypatch):
    import common.circuit.parts as parts_mod

    # Create 80 resistors
    eighty_resistors = {
        f"R-{i}": {"part_no": f"R-{i}", "kind": "resistor", "value": float(i * 10), "rating_w": 0.25}
        for i in range(1, 81)
    }
    monkeypatch.setattr(parts_mod, "PARTS_BIN", eighty_resistors)

    board = small_board()
    _, out, is_err = apply_tool(board, "list_bin", {"kind": "resistor"})
    assert is_err is False
    lines = out.split("\n")
    assert len(lines) == 60


def test_list_bin_real():
    pytest.importorskip("common.circuit.parts")
    board = small_board()
    _, out, is_err = apply_tool(board, "list_bin", {"kind": "resistor"})
    assert isinstance(out, str)


def test_history_only_for_mutating_success(fake_cc1):
    board = small_board()
    board["powered"] = False

    # Read-only tools add no history
    b1, _, _ = apply_tool(board, "read_spec", {})
    assert b1.get("history") == []

    b2, _, _ = apply_tool(board, "inspect", {"ref": "R1"})
    assert b2.get("history") == []

    b3, _, _ = apply_tool(board, "finish", {})
    assert b3.get("history") == []

    # Measurement adds no history
    board_on, _, _ = apply_tool(board, "power_on", {})
    # power_on added 1 entry
    assert len(board_on["history"]) == 1

    b_meas, _, _ = apply_tool(board_on, "measure_voltage", {"net": "3V3"})
    assert len(b_meas["history"]) == 1


def test_errors_do_not_mutate_board(fake_cc1):
    board = small_board()
    board["powered"] = True
    board_snap = copy.deepcopy(board)

    # Error call
    new_b, out, is_err = apply_tool(board, "replace_part", {"ref": "R1", "part_no": "R-150"})
    assert is_err is True
    assert new_b == board_snap


def test_all_errors_start_with_error():
    board = small_board()
    # Tool not found
    _, out1, err1 = apply_tool(board, "foo", {})
    assert err1 and out1.startswith("error:")

    # Args not dict
    _, out2, err2 = apply_tool(board, "finish", "not_dict")
    assert err2 and out2.startswith("error:")

    # Missing arg
    _, out3, err3 = apply_tool(board, "inspect", {})
    assert err3 and out3.startswith("error:")


def test_purity_all_tools_all_branches(fake_cc1):
    board = small_board()
    board_snap = copy.deepcopy(board)

    tools_to_test = [
        ("finish", {}),
        ("power_off", {}),
        ("power_on", {}),
        ("measure_voltage", {"net": "3V3"}),
        ("measure_current", {"ref": "R1"}),
        ("replace_part", {"ref": "R1", "part_no": "R-150"}),
        ("remove_bridge", {"ref": "R1"}),
        ("set_pin_mode", {"pin": "P1", "mode": "input"}),
        ("list_bin", {"kind": "resistor"}),
    ]

    for tool_name, args in tools_to_test:
        apply_tool(board, tool_name, args)
        assert board == board_snap
