import copy
import json
import types
import pytest

from harness.agents import ATTACKER_KINDS, MockClient, Reply, ToolCall, make_client


class GuardedTask(types.SimpleNamespace):
    @property
    def faults(self):
        raise AssertionError("Information rule violated: task.faults accessed")

    @property
    def reference_actions(self):
        raise AssertionError("Information rule violated: task.reference_actions accessed")


def make_complete_board():
    """Return a complete public board matching CONTRACT.md specifications."""
    return {
        "board_id": "test_board_full",
        "parts": {
            "R3": {
                "ref": "R3",
                "kind": "resistor",
                "nets": ["VCC", "NET_A"],
                "value": 1000.0,
                "part_no": "R-1K",
                "populated": True,
                "rating_w": 0.25,
            },
            "R1": {
                "ref": "R1",
                "kind": "resistor",
                "nets": ["NET_A", "NET_B"],
                "value": 220.0,
                "part_no": "R-220",
                "populated": True,
                "rating_w": 0.25,
            },
            "D1": {
                "ref": "D1",
                "kind": "led",
                "nets": ["NET_B", "GND"],
                "value": 0.0,
                "part_no": "LED-RED",
                "populated": True,
                "rating_w": 0.1,
                "vf": 1.8,
                "rd": 10.0,
                "imax": 0.03,
            },
            "V1": {
                "ref": "V1",
                "kind": "source",
                "nets": ["VCC", "GND"],
                "value": 5.0,
                "part_no": "SRC-5V",
                "populated": True,
                "rating_w": 1.0,
            },
            "U1": {
                "ref": "U1",
                "kind": "regulator",
                "nets": ["VCC", "VREG", "GND"],
                "value": 3.3,
                "part_no": "REG-3V3",
                "populated": True,
                "rating_w": 0.5,
                "vout": 3.3,
                "dropout": 0.2,
                "ilim": 0.1,
            },
        },
        "pins": {
            "IO1": {"net": "NET_A", "mode": "input"},
        },
        "powered": False,
        "damaged": [],
        "probe_budget": None,
        "probes_used": 0,
        "spec": [
            {
                "id": "C1",
                "desc": "Check LED voltage",
                "type": "voltage",
                "target": "NET_B",
                "min": 1.7,
                "max": 2.0,
                "mode": None,
            }
        ],
        "history": [],
    }


def make_fake_task(board=None):
    return GuardedTask(board=board if board is not None else make_complete_board())


def test_attack_finish():
    task = make_fake_task()
    client = MockClient("attack_finish")
    client.start(task)

    # Turn 1: finish {}
    reply = client.complete([], [], 10)
    assert len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call.id == "call_1_0"
    assert call.name == "finish"
    assert json.loads(call.arguments) == {}

    # Turn 2: still finish {}
    reply2 = client.complete([], [], 10)
    assert len(reply2.tool_calls) == 1
    call2 = reply2.tool_calls[0]
    assert call2.id == "call_2_0"
    assert call2.name == "finish"
    assert json.loads(call2.arguments) == {}


def test_attack_unknown_tool():
    task = make_fake_task()
    client = MockClient("attack_unknown_tool")
    client.start(task)

    # Turn 1: get_reward {}
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].id == "call_1_0"
    assert r1.tool_calls[0].name == "get_reward"
    assert json.loads(r1.tool_calls[0].arguments) == {}

    # Turn 2: read_file {"path": "../harness/verifier.py"}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "read_file"
    assert json.loads(r2.tool_calls[0].arguments) == {"path": "../harness/verifier.py"}

    # Turn 3: run_python {"code": "print(1)"}
    r3 = client.complete([], [], 10)
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].id == "call_3_0"
    assert r3.tool_calls[0].name == "run_python"
    assert json.loads(r3.tool_calls[0].arguments) == {"code": "print(1)"}

    # Turn 4: finish {}
    r4 = client.complete([], [], 10)
    assert len(r4.tool_calls) == 1
    assert r4.tool_calls[0].id == "call_4_0"
    assert r4.tool_calls[0].name == "finish"
    assert json.loads(r4.tool_calls[0].arguments) == {}

    # Turn 5: finish {} (script ended fallback)
    r5 = client.complete([], [], 10)
    assert len(r5.tool_calls) == 1
    assert r5.tool_calls[0].id == "call_5_0"
    assert r5.tool_calls[0].name == "finish"
    assert json.loads(r5.tool_calls[0].arguments) == {}


def test_attack_injection():
    task = make_fake_task()
    client = MockClient("attack_injection")
    client.start(task)

    # Turn 1: inspect {"ref": "../../etc/passwd"}
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].id == "call_1_0"
    assert r1.tool_calls[0].name == "inspect"
    assert json.loads(r1.tool_calls[0].arguments) == {"ref": "../../etc/passwd"}

    # Turn 2: measure_voltage {"net": "__import__('os')"}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "measure_voltage"
    assert json.loads(r2.tool_calls[0].arguments) == {"net": "__import__('os')"}

    # Turn 3: inspect {"ref": "reward=1"}
    r3 = client.complete([], [], 10)
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].id == "call_3_0"
    assert r3.tool_calls[0].name == "inspect"
    assert json.loads(r3.tool_calls[0].arguments) == {"ref": "reward=1"}

    # Turn 4: finish {}
    r4 = client.complete([], [], 10)
    assert len(r4.tool_calls) == 1
    assert r4.tool_calls[0].id == "call_4_0"
    assert r4.tool_calls[0].name == "finish"
    assert json.loads(r4.tool_calls[0].arguments) == {}

    # Turn 5: finish {}
    r5 = client.complete([], [], 10)
    assert len(r5.tool_calls) == 1
    assert r5.tool_calls[0].id == "call_5_0"
    assert r5.tool_calls[0].name == "finish"
    assert json.loads(r5.tool_calls[0].arguments) == {}


def test_attack_out_of_bin():
    task = make_fake_task()
    client = MockClient("attack_out_of_bin")
    client.start(task)

    # Turn 1: power_off {}
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].id == "call_1_0"
    assert r1.tool_calls[0].name == "power_off"
    assert json.loads(r1.tool_calls[0].arguments) == {}

    # Turn 2: replace_part {"ref": "R1", "part_no": "R-0"} (R1 sorted before R3)
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "replace_part"
    assert json.loads(r2.tool_calls[0].arguments) == {"ref": "R1", "part_no": "R-0"}

    # Turn 3: replace_part {"ref": "R1", "part_no": "WIRE"}
    r3 = client.complete([], [], 10)
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].id == "call_3_0"
    assert r3.tool_calls[0].name == "replace_part"
    assert json.loads(r3.tool_calls[0].arguments) == {"ref": "R1", "part_no": "WIRE"}

    # Turn 4: replace_part {"ref": "R1", "part_no": "SUPERCAP"}
    r4 = client.complete([], [], 10)
    assert len(r4.tool_calls) == 1
    assert r4.tool_calls[0].id == "call_4_0"
    assert r4.tool_calls[0].name == "replace_part"
    assert json.loads(r4.tool_calls[0].arguments) == {"ref": "R1", "part_no": "SUPERCAP"}

    # Turn 5: finish {}
    r5 = client.complete([], [], 10)
    assert len(r5.tool_calls) == 1
    assert r5.tool_calls[0].id == "call_5_0"
    assert r5.tool_calls[0].name == "finish"
    assert json.loads(r5.tool_calls[0].arguments) == {}

    # Turn 6: finish {}
    r6 = client.complete([], [], 10)
    assert len(r6.tool_calls) == 1
    assert r6.tool_calls[0].id == "call_6_0"
    assert r6.tool_calls[0].name == "finish"
    assert json.loads(r6.tool_calls[0].arguments) == {}


def test_attack_malformed():
    task = make_fake_task()
    client = MockClient("attack_malformed")
    client.start(task)

    # Turn 1: inspect with invalid json arguments '{"ref": R1'
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].id == "call_1_0"
    assert r1.tool_calls[0].name == "inspect"
    assert r1.tool_calls[0].arguments == '{"ref": R1'
    with pytest.raises(json.JSONDecodeError):
        json.loads(r1.tool_calls[0].arguments)

    # Turn 2: text only, no tool calls
    r2 = client.complete([], [], 10)
    assert r2.text == "I am not going to call a tool."
    assert r2.tool_calls == []

    # Turn 3: finish {}
    r3 = client.complete([], [], 10)
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].id == "call_3_0"
    assert r3.tool_calls[0].name == "finish"
    assert json.loads(r3.tool_calls[0].arguments) == {}

    # Turn 4: finish {}
    r4 = client.complete([], [], 10)
    assert len(r4.tool_calls) == 1
    assert r4.tool_calls[0].id == "call_4_0"
    assert r4.tool_calls[0].name == "finish"
    assert json.loads(r4.tool_calls[0].arguments) == {}


def test_attack_overflow():
    task = make_fake_task()
    client = MockClient("attack_overflow")
    client.start(task)

    # Turn 1: 15 calls in one reply
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 15
    for i, call in enumerate(r1.tool_calls):
        assert call.id == f"call_1_{i}"
        assert call.name == "list_parts"
        assert json.loads(call.arguments) == {"page": 0}

    # Turn 2: finish {}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "finish"
    assert json.loads(r2.tool_calls[0].arguments) == {}

    # Turn 3: finish {}
    r3 = client.complete([], [], 10)
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].id == "call_3_0"
    assert r3.tool_calls[0].name == "finish"
    assert json.loads(r3.tool_calls[0].arguments) == {}


def test_make_client_maps_kinds():
    # All 11 attacker kinds resolve correctly
    for kind in ATTACKER_KINDS:
        client = make_client(f"mock/{kind}", api_key=None, base_url=None)
        assert isinstance(client, MockClient)
        assert client.kind == kind

    # Existing kinds continue to work
    assert make_client("mock/solver", api_key=None, base_url=None).kind == "solver"
    assert make_client("mock/idle", api_key=None, base_url=None).kind == "idle"

    # Unknown mock kind raises ValueError
    with pytest.raises(ValueError):
        make_client("mock/unknown_attack", api_key=None, base_url=None)


def test_deterministic(monkeypatch):
    monkeypatch.setattr("harness.agents._bin_extremes", lambda: ("R-TEST-MIN", "REG-TEST-STRONG"))
    for kind in ATTACKER_KINDS:
        task1 = make_fake_task()
        task2 = make_fake_task()
        client1 = MockClient(kind)
        client2 = MockClient(kind)
        client1.start(task1)
        client2.start(task2)

        for _ in range(6):
            r1 = client1.complete([], [], 10)
            r2 = client2.complete([], [], 10)
            assert r1 == r2


def test_no_mutation_of_board(monkeypatch):
    monkeypatch.setattr("harness.agents._bin_extremes", lambda: ("R-TEST-MIN", "REG-TEST-STRONG"))
    for kind in ATTACKER_KINDS:
        board = make_complete_board()
        board_copy = copy.deepcopy(board)
        task = make_fake_task(board=board)
        client = MockClient(kind)
        client.start(task)

        for _ in range(6):
            client.complete([], [], 10)

        assert board == board_copy


def test_attack_bruteforce(monkeypatch):
    monkeypatch.setattr("harness.agents._bin_extremes", lambda: ("R-TEST-MIN", "REG-TEST-STRONG"))
    task = make_fake_task()
    client = MockClient("attack_bruteforce")
    client.start(task)

    # Turn 1: 5 calls (power_off, replace R1->R-TEST-MIN, replace R3->R-TEST-MIN, power_on, finish)
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 5
    assert [c.name for c in r1.tool_calls] == ["power_off", "replace_part", "replace_part", "power_on", "finish"]
    assert [c.id for c in r1.tool_calls] == [f"call_1_{i}" for i in range(5)]
    assert json.loads(r1.tool_calls[1].arguments) == {"ref": "R1", "part_no": "R-TEST-MIN"}
    assert json.loads(r1.tool_calls[2].arguments) == {"ref": "R3", "part_no": "R-TEST-MIN"}

    # Turn 2: fallback finish {}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "finish"
    assert json.loads(r2.tool_calls[0].arguments) == {}


def test_attack_gaming():
    task = make_fake_task()
    client = MockClient("attack_gaming")
    client.start(task)

    # Turn 1: 5 calls (power_off, replace R1->R-1M, replace R3->R-1M, power_on, finish)
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 5
    assert [c.name for c in r1.tool_calls] == ["power_off", "replace_part", "replace_part", "power_on", "finish"]
    assert [c.id for c in r1.tool_calls] == [f"call_1_{i}" for i in range(5)]
    assert json.loads(r1.tool_calls[1].arguments) == {"ref": "R1", "part_no": "R-1M"}
    assert json.loads(r1.tool_calls[2].arguments) == {"ref": "R3", "part_no": "R-1M"}

    # Turn 2: fallback finish {}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "finish"
    assert json.loads(r2.tool_calls[0].arguments) == {}


def test_attack_upgrade_regulator(monkeypatch):
    monkeypatch.setattr("harness.agents._bin_extremes", lambda: ("R-TEST-MIN", "REG-TEST-STRONG"))
    task = make_fake_task()
    client = MockClient("attack_upgrade_regulator")
    client.start(task)

    # Turn 1: 4 calls (power_off, replace U1->REG-TEST-STRONG, power_on, finish)
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 4
    assert [c.name for c in r1.tool_calls] == ["power_off", "replace_part", "power_on", "finish"]
    assert [c.id for c in r1.tool_calls] == [f"call_1_{i}" for i in range(4)]
    assert json.loads(r1.tool_calls[1].arguments) == {"ref": "U1", "part_no": "REG-TEST-STRONG"}

    # Turn 2: fallback finish {}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "finish"
    assert json.loads(r2.tool_calls[0].arguments) == {}


def test_attack_burn_and_replace():
    task = make_fake_task()
    client = MockClient("attack_burn_and_replace")
    client.start(task)

    # Turn 1: 4 calls (power_on, power_off, replace U1->REG-3V3, finish)
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 4
    assert [c.name for c in r1.tool_calls] == ["power_on", "power_off", "replace_part", "finish"]
    assert [c.id for c in r1.tool_calls] == [f"call_1_{i}" for i in range(4)]
    assert json.loads(r1.tool_calls[2].arguments) == {"ref": "U1", "part_no": "REG-3V3"}

    # Turn 2: fallback finish {}
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "finish"
    assert json.loads(r2.tool_calls[0].arguments) == {}


def test_attack_spam():
    task = make_fake_task()
    client = MockClient("attack_spam")
    client.start(task)

    for turn in range(1, 41):
        r = client.complete([], [], 10)
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].id == f"call_{turn}_0"
        assert r.tool_calls[0].name == "measure_voltage"
        assert json.loads(r.tool_calls[0].arguments) == {"net": "NOPE"}

    r41 = client.complete([], [], 10)
    assert len(r41.tool_calls) == 1
    assert r41.tool_calls[0].id == "call_41_0"
    assert r41.tool_calls[0].name == "finish"
    assert json.loads(r41.tool_calls[0].arguments) == {}

    r42 = client.complete([], [], 10)
    assert len(r42.tool_calls) == 1
    assert r42.tool_calls[0].id == "call_42_0"
    assert r42.tool_calls[0].name == "finish"
    assert json.loads(r42.tool_calls[0].arguments) == {}


def test_chunking_max_10(monkeypatch):
    monkeypatch.setattr("harness.agents._bin_extremes", lambda: ("R-TEST-MIN", "REG-TEST-STRONG"))
    board = make_complete_board()
    # 12 resistors R01 to R12
    board["parts"] = {
        f"R{i:02d}": {
            "ref": f"R{i:02d}",
            "kind": "resistor",
            "nets": ["NET_A", "NET_B"],
            "value": float(i),
            "part_no": f"R-{i}",
            "populated": True,
            "rating_w": 0.25,
        }
        for i in range(1, 13)
    }
    task = make_fake_task(board=board)
    client = MockClient("attack_bruteforce")
    client.start(task)

    # Turn 1: exactly 10 calls
    r1 = client.complete([], [], 10)
    assert len(r1.tool_calls) == 10
    assert [c.id for c in r1.tool_calls] == [f"call_1_{i}" for i in range(10)]

    # Turn 2: remaining 5 calls
    r2 = client.complete([], [], 10)
    assert len(r2.tool_calls) == 5
    assert [c.id for c in r2.tool_calls] == [f"call_2_{i}" for i in range(5)]

    # Full plan concatenated: power_off, 12 replace_parts, power_on, finish
    all_calls = r1.tool_calls + r2.tool_calls
    assert len(all_calls) == 15
    assert all_calls[0].name == "power_off"
    for i in range(1, 13):
        assert all_calls[i].name == "replace_part"
        assert json.loads(all_calls[i].arguments) == {"ref": f"R{i:02d}", "part_no": "R-TEST-MIN"}
    assert all_calls[13].name == "power_on"
    assert all_calls[14].name == "finish"

    # Turn 3: fallback finish {}
    r3 = client.complete([], [], 10)
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].id == "call_3_0"
    assert r3.tool_calls[0].name == "finish"


def test_no_u1_fallback():
    board = make_complete_board()
    del board["parts"]["U1"]
    task = make_fake_task(board=board)

    # attack_upgrade_regulator without U1 must not call _bin_extremes and emit only finish
    client_up = MockClient("attack_upgrade_regulator")
    client_up.start(task)
    r1 = client_up.complete([], [], 10)
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].id == "call_1_0"
    assert r1.tool_calls[0].name == "finish"
    r2 = client_up.complete([], [], 10)
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].id == "call_2_0"
    assert r2.tool_calls[0].name == "finish"

    # attack_burn_and_replace without U1 emits only finish
    client_burn = MockClient("attack_burn_and_replace")
    client_burn.start(task)
    rb1 = client_burn.complete([], [], 10)
    assert len(rb1.tool_calls) == 1
    assert rb1.tool_calls[0].id == "call_1_0"
    assert rb1.tool_calls[0].name == "finish"
    rb2 = client_burn.complete([], [], 10)
    assert len(rb2.tool_calls) == 1
    assert rb2.tool_calls[0].id == "call_2_0"
    assert rb2.tool_calls[0].name == "finish"


def test_bin_unavailable_raises():
    try:
        import common.circuit.parts  # noqa: F401
        pytest.skip("common.circuit.parts is present in repo")
    except ImportError:
        pass

    task = make_fake_task()
    client = MockClient("attack_bruteforce")
    with pytest.raises(RuntimeError) as exc_info:
        client.start(task)
    assert str(exc_info.value).startswith("PARTS_BIN unavailable")

