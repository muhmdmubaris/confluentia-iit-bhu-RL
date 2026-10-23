import pytest
from harness.runner import show


def test_header_format(capsys):
    record = {
        "step": 7,
        "max_steps": 40,
        "latency_ms": 12400,
        "tokens": 1830,
        "parsed_actions": [],
    }
    show(record)
    captured = capsys.readouterr().out
    assert captured.splitlines()[0] == "step 7/40 | 12.4 s | 1,830 tok"


def test_action_line_and_result_cut_to_100(capsys):
    long_result = "x" * 150
    record = {
        "step": 1,
        "max_steps": 10,
        "latency_ms": 1000,
        "tokens": 500,
        "parsed_actions": [
            {"tool": "measure_voltage", "args": {"net": "SENSE1"}},
            {"tool": "inspect", "args": {"ref": "R1"}},
        ],
        "tool_result": [long_result],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    # First action: result cut to 100 chars + "..."
    expected_result_1 = ("x" * 100) + "..."
    assert f"=> {expected_result_1}" in lines[1]
    # Check padding in line 1: tool call has column padding to 36
    assert lines[1].startswith('  -> measure_voltage(net="SENSE1")    => ')
    # Second action: missing tool_result -> "=> (no result)"
    assert lines[2] == '  -> inspect(ref="R1")                => (no result)'


def test_newlines_flattened(capsys):
    record = {
        "step": 1,
        "parsed_actions": [{"tool": "read_spec", "args": {}}],
        "tool_result": ["line1\nline2\r\nline3\rline4"],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "=> line1 line2 line3 line4" in lines[1]


def test_flags_printed_in_contract_format(capsys):
    record = {
        "step": 7,
        "max_steps": 40,
        "latency_ms": 12400,
        "tokens": 1830,
        "parsed_actions": [{"tool": "measure_voltage", "args": {"net": "SENSE1"}}],
        "tool_result": ["V(SENSE1) = 0.412 V"],
        "flags": [
            {"severity": "high", "type": "unknown_tool", "detail": "get_reward"},
            {"severity": "info", "type": "repeated_errors", "detail": "5 identical errors"},
        ],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "step 7/40 | 12.4 s | 1,830 tok"
    assert lines[1] == '  -> measure_voltage(net="SENSE1")    => V(SENSE1) = 0.412 V'
    assert lines[2] == "  [FLAG high] unknown_tool: get_reward"
    assert lines[3] == "  [FLAG info] repeated_errors: 5 identical errors"


def test_missing_flags_and_max_steps_do_not_raise(capsys):
    # Empty record: prints header with ? only
    show({})
    out = capsys.readouterr().out.strip()
    assert out == "step ?/? | ? s | ? tok"

    # Non-dict record: prints header with ? only
    show(None)
    out = capsys.readouterr().out.strip()
    assert out == "step ?/? | ? s | ? tok"

    # Missing max_steps, latency_ms, tokens
    record = {"step": 2}
    show(record)
    out = capsys.readouterr().out.strip()
    assert out == "step 2/? | ? s | ? tok"

    # tool_result shorter than parsed_actions
    record2 = {
        "step": 1,
        "parsed_actions": [
            {"tool": "power_on", "args": {}},
            {"tool": "power_off", "args": {}},
        ],
        "tool_result": ["Powered on."],
    }
    show(record2)
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "  -> power_on()                       => Powered on."
    assert lines[2] == "  -> power_off()                      => (no result)"


def test_no_tool_call_error_line(capsys):
    record = {
        "step": 3,
        "errors": [
            {"kind": "no_tool_call", "detail": "reply contained no tool call"},
            {"kind": "model_call_failed", "detail": "Connection error"},
            {"kind": "tool_error", "detail": "smoke from R5"},
            {"kind": "invalid_arguments", "detail": "extra arg"},
        ],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "step 3/? | ? s | ? tok"
    assert lines[1] == "  [ERROR] no_tool_call: reply contained no tool call"
    assert lines[2] == "  [ERROR] model_call_failed: Connection error"
    # tool_error and invalid_arguments must not be printed
    assert len(lines) == 3


def test_empty_args(capsys):
    record = {
        "step": 1,
        "parsed_actions": [
            {"tool": "finish", "args": {}},
            {"tool": "power_off", "args": None},
        ],
        "tool_result": ["done", "off"],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "  -> finish()                         => done"
    assert lines[2] == "  -> power_off()                      => off"


def test_output_order(capsys):
    record = {
        "step": 5,
        "max_steps": 20,
        "latency_ms": 3500,
        "tokens": 1200,
        "parsed_actions": [
            {"tool": "power_on", "args": {}},
            {"tool": "measure_voltage", "args": {"net": "OUT"}},
        ],
        "tool_result": ["OK", "3.3V"],
        "flags": [
            {"severity": "high", "type": "out_of_bin_part", "detail": "R-0"},
        ],
        "errors": [
            {"kind": "no_tool_call", "detail": "no tool call"},
        ],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("step 5/20")
    assert lines[1].startswith("  -> power_on()")
    assert lines[2].startswith('  -> measure_voltage(net="OUT")')
    assert lines[3] == "  [FLAG high] out_of_bin_part: R-0"
    assert lines[4] == "  [ERROR] no_tool_call: no tool call"


def test_non_string_result_and_odd_args(capsys):
    class CustomObj:
        def __str__(self):
            return "custom_str"

    record = {
        "step": 1,
        "parsed_actions": [
            {"tool": "run_python", "args": [1, 2, 3]},
            {"tool": "custom", "args": {"num": 42, "obj": CustomObj(), "active": True}},
        ],
        "tool_result": [12345, {"status": "ok"}],
    }
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("  -> run_python([1, 2, 3])")
    assert "=> 12345" in lines[1]
    assert 'num=42' in lines[2]
    assert 'obj="custom_str"' in lines[2] or 'obj=custom_str' in lines[2]
    assert 'active=true' in lines[2]
    assert "=> {'status': 'ok'}" in lines[2]


def test_show_failed_on_exception(capsys, monkeypatch):
    # If something inside show() raises an unexpected exception
    record = {"step": 1}

    def broken_format(args):
        raise RuntimeError("boom")

    monkeypatch.setattr("harness.runner._format_args", broken_format)
    # Give record parsed_actions so _format_args is triggered
    record["parsed_actions"] = [{"tool": "broken", "args": {}}]
    show(record)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "step ?/? | ? s | ? tok"
    assert lines[1] == "  [ERROR] show_failed: RuntimeError"
