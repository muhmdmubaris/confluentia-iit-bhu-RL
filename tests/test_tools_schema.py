"""Tests for BringUp tool schemas and check_args validation."""

import json
import pytest

from harness.tools import TOOLS, SPECS, TOOL_NAMES, check_args
import common.circuit.actions as actions


CONTRACT_TOOLS = [
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


def test_thirteen_tools_and_names_match_contract():
    assert len(TOOLS) == 13
    assert len(SPECS) == 13
    assert len(TOOL_NAMES) == 13
    assert set(TOOL_NAMES) == set(CONTRACT_TOOLS)


def test_tool_order_matches_actions_tool_names():
    assert TOOL_NAMES == actions.TOOL_NAMES


def test_schemas_are_valid_objects():
    for t in TOOLS:
        assert t.get("type") == "function"
        fn = t.get("function", {})
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        params = fn["parameters"]
        assert params.get("type") == "object"
        assert params.get("additionalProperties") is False
        assert isinstance(params.get("properties"), dict)
        assert isinstance(params.get("required"), list)


def test_schemas_json_serializable():
    dumped = json.dumps(TOOLS)
    reloaded = json.loads(dumped)
    assert reloaded == TOOLS


def test_required_and_properties_match_actions_arg_schema():
    assert set(SPECS.keys()) == set(actions.ARG_SCHEMA.keys())
    for name, schema in SPECS.items():
        act_schema = actions.ARG_SCHEMA[name]
        expected_required = [arg for arg, (t, req) in act_schema.items() if req]
        expected_properties = set(act_schema.keys())
        assert schema["required"] == expected_required
        assert set(schema["properties"].keys()) == expected_properties


def test_descriptions_two_lines_max():
    for t in TOOLS:
        desc = t["function"]["description"]
        assert desc.count("\n") <= 1, f"Tool {t['function']['name']} has more than 2 lines in description"


def test_description_length():
    for t in TOOLS:
        desc = t["function"]["description"]
        assert len(desc) <= 200, f"Tool {t['function']['name']} description exceeds 200 characters: {len(desc)}"
        assert desc.count("\n") <= 1


def test_precondition_wording():
    desc_map = {t["function"]["name"]: t["function"]["description"] for t in TOOLS}
    assert "Requires power off" in desc_map["replace_part"]
    assert "Requires power off" in desc_map["remove_bridge"]
    assert "Requires power off" in desc_map["set_pin_mode"]
    assert "Requires power on" in desc_map["measure_voltage"]
    assert "Requires power on" in desc_map["measure_current"]


def test_enum_values_match_actions():
    assert SPECS["list_bin"]["properties"]["kind"]["enum"] == ["resistor", "led", "regulator"]
    assert SPECS["set_pin_mode"]["properties"]["mode"]["enum"] == ["output_high", "output_low", "input", "adc"]


def test_check_args_valid_calls_for_each_tool():
    valid_calls = {
        "read_spec": {},
        "list_parts": {},
        "inspect": {"ref": "R1"},
        "read_schematic": {},
        "list_bin": {"kind": "resistor"},
        "power_on": {},
        "power_off": {},
        "measure_voltage": {"net": "VCC"},
        "measure_current": {"ref": "R1"},
        "replace_part": {"ref": "R1", "part_no": "R-100"},
        "remove_bridge": {"ref": "SB1"},
        "set_pin_mode": {"pin": "P1", "mode": "input"},
        "finish": {},
    }
    for name, args in valid_calls.items():
        assert check_args(name, args) is None

    # Extra valid parameter variations
    assert check_args("list_parts", {"page": 1}) is None
    assert check_args("list_parts", {"page": 5}) is None
    assert check_args("read_schematic", {"page": 2}) is None
    assert check_args("list_bin", {"kind": "led"}) is None
    assert check_args("list_bin", {"kind": "regulator"}) is None
    assert check_args("set_pin_mode", {"pin": "P2", "mode": "output_high"}) is None
    assert check_args("set_pin_mode", {"pin": "P3", "mode": "output_low"}) is None
    assert check_args("set_pin_mode", {"pin": "P4", "mode": "adc"}) is None


def test_check_args_missing():
    assert check_args("inspect", {}) == "missing argument 'ref' for inspect"
    assert check_args("replace_part", {"part_no": "R-100"}) == "missing argument 'ref' for replace_part"
    assert check_args("replace_part", {"ref": "R1"}) == "missing argument 'part_no' for replace_part"
    assert check_args("set_pin_mode", {"mode": "input"}) == "missing argument 'pin' for set_pin_mode"
    assert check_args("set_pin_mode", {"pin": "P1"}) == "missing argument 'mode' for set_pin_mode"
    assert check_args("measure_voltage", {}) == "missing argument 'net' for measure_voltage"
    assert check_args("measure_current", {}) == "missing argument 'ref' for measure_current"
    assert check_args("remove_bridge", {}) == "missing argument 'ref' for remove_bridge"
    assert check_args("list_bin", {}) == "missing argument 'kind' for list_bin"


def test_check_args_unexpected_sorted():
    assert check_args("read_spec", {"foo": 1}) == "unexpected argument 'foo' for read_spec"
    assert check_args("inspect", {"ref": "R1", "zebra": 1, "apple": 2}) == "unexpected argument 'apple' for inspect"
    assert check_args("finish", {"extra": "foo"}) == "unexpected argument 'extra' for finish"


def test_check_args_types_bool_rejected():
    assert check_args("list_parts", {"page": True}) == "argument 'page' must be an integer"
    assert check_args("list_parts", {"page": False}) == "argument 'page' must be an integer"
    assert check_args("inspect", {"ref": 123}) == "argument 'ref' must be a string"
    assert check_args("inspect", {"ref": True}) == "argument 'ref' must be a string"
    assert check_args("inspect", {"ref": ["R1"]}) == "argument 'ref' must be a string"
    assert check_args("measure_voltage", {"net": {"name": "VCC"}}) == "argument 'net' must be a string"


def test_float_page_rejected():
    assert check_args("list_parts", {"page": 1.0}) == "argument 'page' must be an integer"
    assert check_args("read_schematic", {"page": 2.5}) == "argument 'page' must be an integer"


def test_check_args_enum():
    assert check_args("list_bin", {"kind": "capacitor"}) == "argument 'kind' must be one of: resistor, led, regulator"
    assert check_args("set_pin_mode", {"pin": "P1", "mode": "pwm"}) == "argument 'mode' must be one of: output_high, output_low, input, adc"


def test_check_args_page_minimum():
    assert check_args("list_parts", {"page": 0}) == "argument 'page' must be >= 1"
    assert check_args("list_parts", {"page": -1}) == "argument 'page' must be >= 1"
    assert check_args("read_schematic", {"page": 0}) == "argument 'page' must be >= 1"


def test_check_args_never_raises_on_garbage():
    assert check_args(None, None) == "unknown tool 'None'"
    assert check_args(123, None) == "unknown tool '123'"
    assert check_args("unknown_tool", {}) == "unknown tool 'unknown_tool'"
    assert check_args("inspect", None) == "arguments must be an object"
    assert check_args("inspect", "not a dict") == "arguments must be an object"
    assert check_args("inspect", [1, 2, 3]) == "arguments must be an object"
    assert check_args("inspect", 42) == "arguments must be an object"

    # Hostile object raising on __str__
    class Hostile:
        def __str__(self):
            raise RuntimeError("hostile __str__")
    res = check_args(Hostile(), {})
    assert "unknown tool" in res


def test_error_strings_agree_with_actions():
    board = {"parts": {}, "pins": {}}
    # Missing argument cases
    for name, args in [
        ("inspect", {}),
        ("replace_part", {"ref": "R1"}),
        ("set_pin_mode", {"pin": "P1"}),
    ]:
        tools_err = check_args(name, args)
        _, act_msg, act_err = actions.apply_tool(board, name, args)
        assert act_err is True
        assert "error: " + tools_err == act_msg

    # Unexpected argument cases
    for name, args in [
        ("read_spec", {"foo": 1}),
        ("inspect", {"ref": "R1", "zebra": 1, "alpha": 2}),
        ("finish", {"bar": "baz"}),
    ]:
        tools_err = check_args(name, args)
        _, act_msg, act_err = actions.apply_tool(board, name, args)
        assert act_err is True
        assert "error: " + tools_err == act_msg

    # Integer type cases
    for name, args in [
        ("list_parts", {"page": "first"}),
        ("list_parts", {"page": True}),
        ("list_parts", {"page": 1.0}),
        ("read_schematic", {"page": [1]}),
    ]:
        tools_err = check_args(name, args)
        _, act_msg, act_err = actions.apply_tool(board, name, args)
        assert act_err is True
        assert "error: " + tools_err == act_msg

    # String type cases
    for name, args in [
        ("inspect", {"ref": 123}),
        ("inspect", {"ref": True}),
        ("measure_voltage", {"net": ["VCC"]}),
        ("measure_current", {"ref": None}),
        ("replace_part", {"ref": 1, "part_no": "R-100"}),
    ]:
        tools_err = check_args(name, args)
        _, act_msg, act_err = actions.apply_tool(board, name, args)
        assert act_err is True
        assert "error: " + tools_err == act_msg


def test_no_error_prefix_in_check_args():
    invalid_calls = [
        ("unknown_tool", {}),
        ("inspect", None),
        ("inspect", {}),
        ("inspect", {"extra": 1}),
        ("list_parts", {"page": "abc"}),
        ("list_parts", {"page": 1.5}),
        ("list_parts", {"page": 0}),
        ("list_bin", {"kind": "invalid"}),
        ("set_pin_mode", {"pin": "P1", "mode": "invalid"}),
    ]
    for name, args in invalid_calls:
        msg = check_args(name, args)
        assert msg is not None
        assert not msg.lower().startswith("error"), f"Message '{msg}' unexpectedly starts with 'error'"


def test_template_callers_still_import():
    from harness.tools import TOOLS as T, SPECS as S, TOOL_NAMES as N, check_args as C
    assert isinstance(T, list)
    assert isinstance(S, dict)
    assert isinstance(N, list)
    assert callable(C)


def test_check_args_internal_error_is_not_none(monkeypatch):
    import harness.tools

    class BadSpec(dict):
        def get(self, key, default=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(harness.tools, "SPECS", {"inspect": BadSpec()})
    res = check_args("inspect", {"ref": "R1"})
    assert isinstance(res, str)
    assert res.startswith("internal validation error: RuntimeError")

