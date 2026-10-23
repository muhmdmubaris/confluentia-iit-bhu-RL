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
    # All 6 attacker kinds resolve correctly
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


def test_deterministic():
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


def test_no_mutation_of_board():
    for kind in ATTACKER_KINDS:
        board = make_complete_board()
        board_copy = copy.deepcopy(board)
        task = make_fake_task(board=board)
        client = MockClient(kind)
        client.start(task)

        for _ in range(6):
            client.complete([], [], 10)

        assert board == board_copy
