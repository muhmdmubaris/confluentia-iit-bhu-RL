"""Tests for the sandbox HTTP service and apply_tool integration."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import threading
import urllib.error
import urllib.request
import pytest

import sandbox.server as server


def make_valid_board():
    return {
        "board_id": "test_board_1",
        "parts": {
            "R1": {
                "ref": "R1",
                "kind": "resistor",
                "nets": ["VCC", "NET_A"],
                "value": 150.0,
                "part_no": "R-150",
                "populated": True,
                "rating_w": 0.25,
            }
        },
        "pins": {
            "P1": {"net": "NET_A", "mode": "input"}
        },
        "powered": False,
        "damaged": [],
        "history": [],
        "spec": [],
        "probe_budget": 10,
        "probes_used": 0,
    }


@pytest.fixture(scope="module")
def server_url():
    """Start sandbox server on an ephemeral port in a daemon thread."""
    httpd = server.make_server("127.0.0.1", 0)
    host, port = httpd.server_address
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://{host}:{port}"
    yield url
    httpd.shutdown()
    httpd.server_close()
    t.join(timeout=2.0)


def do_request(url: str, path: str, method: str = "GET", body=None, headers=None):
    target = f"{url}{path}"
    req_headers = dict(headers or {})
    data = None

    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        elif isinstance(body, bytes):
            data = body
        if "Content-Length" not in req_headers and data is not None:
            req_headers["Content-Length"] = str(len(data))

    req = urllib.request.Request(target, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp_bytes = resp.read()
            try:
                payload = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else None
            except Exception:
                payload = resp_bytes.decode("utf-8", errors="replace")
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        resp_bytes = exc.read()
        try:
            payload = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else None
        except Exception:
            payload = resp_bytes.decode("utf-8", errors="replace")
        return exc.code, payload, dict(exc.headers)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_health(server_url):
    code, payload, _ = do_request(server_url, "/health")
    assert code == 200
    assert payload == {"ok": True}


def test_reset_then_state_roundtrip(server_url):
    board = make_valid_board()
    code, payload, _ = do_request(server_url, "/reset", method="POST", body={"board": board})
    assert code == 200
    assert payload == {"ok": True}

    code, payload, _ = do_request(server_url, "/state", method="GET")
    assert code == 200
    assert "board" in payload
    # Stored board matches reset input
    assert payload["board"]["board_id"] == "test_board_1"
    assert payload["board"]["parts"]["R1"]["part_no"] == "R-150"
    assert payload["board"]["powered"] is False


def test_tool_before_reset_400():
    # Fresh server instance without reset
    httpd = server.make_server("127.0.0.1", 0)
    host, port = httpd.server_address
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    fresh_url = f"http://{host}:{port}"
    try:
        # Reset server's module-level board to None
        with server._state_lock:
            server._current_board = None
        code, payload, _ = do_request(fresh_url, "/tool", method="POST", body={"name": "finish", "args": {}})
        assert code == 400
        assert "no board loaded; POST /reset first" in payload.get("error", "")

        code, payload, _ = do_request(fresh_url, "/state", method="GET")
        assert code == 400
        assert "no board loaded; POST /reset first" in payload.get("error", "")
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2.0)


def test_tool_finish_example(server_url):
    board = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": board})

    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"name": "finish", "args": {}})
    assert code == 200
    assert payload == {"output": "finish received", "is_error": False}


def test_tool_error_is_200_with_is_error(server_url):
    board = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": board})

    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"name": "inspect", "args": {"refx": "R1"}})
    assert code == 200
    assert payload["is_error"] is True
    assert "missing argument 'ref' for inspect" in payload["output"]


def test_invalid_json_400_and_server_survives(server_url):
    code, payload, _ = do_request(
        server_url, "/tool", method="POST", body="{bad", headers={"Content-Type": "application/json"}
    )
    assert code == 400
    assert "invalid JSON" in payload.get("error", "")

    # Server survives and answers next health check
    code, payload, _ = do_request(server_url, "/health")
    assert code == 200
    assert payload == {"ok": True}


def test_body_not_object_400(server_url):
    code, payload, _ = do_request(server_url, "/tool", method="POST", body=[1, 2, 3])
    assert code == 400
    assert payload.get("error") == "body must be an object"


def test_missing_name_400(server_url):
    board = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": board})

    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"args": {}})
    assert code == 400
    assert payload.get("error") == "missing or invalid 'name'"

    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"name": 123, "args": {}})
    assert code == 400
    assert payload.get("error") == "missing or invalid 'name'"


def test_args_not_object_400(server_url):
    board = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": board})

    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"name": "finish", "args": "bad"})
    assert code == 400
    assert payload.get("error") == "'args' must be an object"

    # Missing args defaults to {}
    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"name": "finish"})
    assert code == 200
    assert payload == {"output": "finish received", "is_error": False}


def test_oversize_body_413(server_url):
    parsed = urllib.parse.urlparse(server_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.putrequest("POST", "/reset")
    conn.putheader("Content-Length", "1000001")
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    assert resp.status == 413
    data = json.loads(body.decode("utf-8"))
    assert data.get("error") == "payload too large"


def test_reset_rejects_hidden_keys(server_url):
    for key in ("faults", "reference_actions", "intent", "verifier"):
        # Hidden key at top level of body
        body = {"board": make_valid_board(), key: "secret"}
        code, payload, _ = do_request(server_url, "/reset", method="POST", body=body)
        assert code == 400
        assert f"forbidden key '{key}'" in payload.get("error", "")

        # Hidden key at top level of board
        board = make_valid_board()
        board[key] = "secret"
        code, payload, _ = do_request(server_url, "/reset", method="POST", body={"board": board})
        assert code == 400
        assert f"forbidden key '{key}'" in payload.get("error", "")


def test_reset_rejects_nested_hidden_keys(server_url):
    # Hidden key inside parts.R1
    board1 = make_valid_board()
    board1["parts"]["R1"]["faults"] = ["overheated"]
    code, payload, _ = do_request(server_url, "/reset", method="POST", body={"board": board1})
    assert code == 400
    assert "forbidden key 'faults'" in payload.get("error", "")

    # Hidden key inside a list in spec
    board2 = make_valid_board()
    board2["spec"] = [{"id": "C1", "reference_actions": [{"tool": "finish"}]}]
    code, payload, _ = do_request(server_url, "/reset", method="POST", body={"board": board2})
    assert code == 400
    assert "forbidden key 'reference_actions'" in payload.get("error", "")

    # Hidden key inside history args
    board3 = make_valid_board()
    board3["history"] = [{"tool": "replace_part", "args": {"ref": "R1", "intent": "hack"}}]
    code, payload, _ = do_request(server_url, "/reset", method="POST", body={"board": board3})
    assert code == 400
    assert "forbidden key 'intent'" in payload.get("error", "")


def test_reset_depth_or_node_limit_400(server_url):
    nested = {"val": 1}
    for _ in range(25):
        nested = {"sub": nested}
    board = make_valid_board()
    board["spec"] = [nested]
    code, payload, _ = do_request(server_url, "/reset", method="POST", body={"board": board})
    assert code == 400
    assert "maximum depth or node limit" in payload.get("error", "")


def test_reset_rejects_bad_board_shapes(server_url):
    # parts missing or not dict of dicts
    b1 = make_valid_board()
    b1["parts"] = "not_a_dict"
    assert do_request(server_url, "/reset", method="POST", body={"board": b1})[0] == 400

    b2 = make_valid_board()
    b2["parts"]["R1"] = "not_a_dict"
    assert do_request(server_url, "/reset", method="POST", body={"board": b2})[0] == 400

    # pins missing or not dict of dicts
    b3 = make_valid_board()
    b3["pins"] = []
    assert do_request(server_url, "/reset", method="POST", body={"board": b3})[0] == 400

    # powered must be bool, reject 0 and 1
    b4 = make_valid_board()
    b4["powered"] = 1
    assert do_request(server_url, "/reset", method="POST", body={"board": b4})[0] == 400
    b4["powered"] = 0
    assert do_request(server_url, "/reset", method="POST", body={"board": b4})[0] == 400

    # damaged must be list of strings
    b5 = make_valid_board()
    b5["damaged"] = [123]
    assert do_request(server_url, "/reset", method="POST", body={"board": b5})[0] == 400

    # history must be list
    b6 = make_valid_board()
    b6["history"] = {}
    assert do_request(server_url, "/reset", method="POST", body={"board": b6})[0] == 400

    # spec must be list if present
    b7 = make_valid_board()
    b7["spec"] = {}
    assert do_request(server_url, "/reset", method="POST", body={"board": b7})[0] == 400

    # probe_budget invalid
    b8 = make_valid_board()
    b8["probe_budget"] = -5
    assert do_request(server_url, "/reset", method="POST", body={"board": b8})[0] == 400
    b8["probe_budget"] = True
    assert do_request(server_url, "/reset", method="POST", body={"board": b8})[0] == 400

    # probes_used invalid
    b9 = make_valid_board()
    b9["probes_used"] = -1
    assert do_request(server_url, "/reset", method="POST", body={"board": b9})[0] == 400
    b9["probes_used"] = False
    assert do_request(server_url, "/reset", method="POST", body={"board": b9})[0] == 400


def test_reset_rejects_nan_and_infinity(server_url):
    raw_nan = '{"board": {"parts": {}, "pins": {}, "powered": false, "damaged": [], "history": [], "val": NaN}}'
    code, payload, _ = do_request(
        server_url, "/reset", method="POST", body=raw_nan, headers={"Content-Type": "application/json"}
    )
    assert code == 400
    assert "invalid JSON" in payload.get("error", "")

    raw_inf = '{"board": {"parts": {}, "pins": {}, "powered": false, "damaged": [], "history": [], "val": Infinity}}'
    code, payload, _ = do_request(
        server_url, "/reset", method="POST", body=raw_inf, headers={"Content-Type": "application/json"}
    )
    assert code == 400
    assert "invalid JSON" in payload.get("error", "")


def test_chunked_rejected(server_url):
    code, payload, _ = do_request(
        server_url, "/reset", method="POST", body=b"{}", headers={"Transfer-Encoding": "chunked"}
    )
    assert code == 400
    assert "chunked transfer encoding is not supported" in payload.get("error", "")


def test_short_body_does_not_hang(server_url):
    parsed = urllib.parse.urlparse(server_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    # Claim Content-Length is 50, but send only 10 bytes and close write side
    conn.putrequest("POST", "/reset")
    conn.putheader("Content-Length", "50")
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()
    conn.send(b'{"short":1')
    conn.sock.shutdown(socket.SHUT_WR)
    # Read response; server should reject with 400 without hanging
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    assert resp.status == 400
    data = json.loads(body.decode("utf-8"))
    assert "unexpected end of stream" in data.get("error", "")


def test_removed_endpoints_404(server_url):
    removed = ["/verify", "/run_python", "/list_files", "/read_file", "/write_file"]
    for path in removed:
        # Correct method from template
        assert do_request(server_url, path, method="POST", body={})[0] == 404
        # Wrong method
        assert do_request(server_url, path, method="GET")[0] == 404


def test_wrong_method_405(server_url):
    # GET on POST-only endpoint
    code, payload, headers = do_request(server_url, "/tool", method="GET")
    assert code == 405
    assert headers.get("Allow") == "POST"

    code, payload, headers = do_request(server_url, "/reset", method="GET")
    assert code == 405
    assert headers.get("Allow") == "POST"

    code, payload, headers = do_request(server_url, "/reset", method="PUT")
    assert code == 405
    assert headers.get("Allow") == "POST"

    # POST on GET-only endpoint
    code, payload, headers = do_request(server_url, "/health", method="POST", body={})
    assert code == 405
    assert headers.get("Allow") == "GET"

    code, payload, headers = do_request(server_url, "/state", method="POST", body={})
    assert code == 405
    assert headers.get("Allow") == "GET"

    # DELETE on /health
    code, payload, headers = do_request(server_url, "/health", method="DELETE")
    assert code == 405
    assert headers.get("Allow") == "GET"


def test_unicode_output_roundtrip(server_url):
    board = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": board})

    code, payload, headers = do_request(
        server_url, "/tool", method="POST", body={"name": "list_parts", "args": {}}
    )
    assert code == 200
    assert payload["is_error"] is False
    assert "Ω" in payload["output"], f"Expected 'Ω' in output, got: {payload['output']}"
    assert "utf-8" in headers.get("Content-Type", "").lower()


def test_concurrent_tool_calls_serialized(server_url):
    board = make_valid_board()
    # P1 is an input pin
    board["pins"] = {"P1": {"net": "NET_A", "mode": "input"}}
    board["powered"] = False
    do_request(server_url, "/reset", method="POST", body={"board": board})

    def worker(worker_id):
        results = []
        for i in range(10):
            mode = "output_high" if i % 2 == 0 else "input"
            code, payload, _ = do_request(
                server_url, "/tool", method="POST",
                body={"name": "set_pin_mode", "args": {"pin": "P1", "mode": mode}}
            )
            results.append((code, payload["is_error"]))
        return results

    # 8 threads, 10 calls each = 80 calls total
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, w) for w in range(8)]
        for f in futures:
            res = f.result(timeout=30.0)
            for code, is_error in res:
                assert code == 200
                assert is_error is False

    # Check state has exactly 80 history entries
    code, payload, _ = do_request(server_url, "/state", method="GET")
    assert code == 200
    history = payload["board"]["history"]
    assert len(history) == 80
    for h in history:
        assert h["tool"] == "set_pin_mode"


def test_state_does_not_leak_between_resets(server_url):
    b1 = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": b1})
    do_request(server_url, "/tool", method="POST", body={"name": "set_pin_mode", "args": {"pin": "P1", "mode": "output_low"}})

    # Reset with board 2
    b2 = make_valid_board()
    b2["board_id"] = "test_board_2"
    do_request(server_url, "/reset", method="POST", body={"board": b2})

    code, payload, _ = do_request(server_url, "/state", method="GET")
    assert code == 200
    assert payload["board"]["board_id"] == "test_board_2"
    assert len(payload["board"]["history"]) == 0
    assert payload["board"]["pins"]["P1"]["mode"] == "input"


def test_internal_error_returns_500_and_server_survives(server_url, monkeypatch):
    board = make_valid_board()
    do_request(server_url, "/reset", method="POST", body={"board": board})

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(server, "apply_tool", boom)

    code, payload, _ = do_request(server_url, "/tool", method="POST", body={"name": "finish", "args": {}})
    assert code == 500
    assert payload.get("error") == "internal error: RuntimeError"

    # Server remains alive
    code, payload, _ = do_request(server_url, "/health")
    assert code == 200
    assert payload == {"ok": True}


def test_no_forbidden_calls_in_source():
    server_path = Path(__file__).parent.parent / "sandbox" / "server.py"
    lines = server_path.read_text(encoding="utf-8").splitlines()

    # Filter out the constant line defining FORBIDDEN_KEYS
    filtered_lines = [line for line in lines if "FORBIDDEN_KEYS" not in line]
    clean_text = "\n".join(filtered_lines)

    forbidden_tokens = [
        "open(",
        "eval(",
        "exec(",
        "subprocess",
        "os.system",
        "__import__",
        "verifier",
        "reference_actions",
    ]
    for token in forbidden_tokens:
        assert token not in clean_text, f"Forbidden call/string '{token}' found in sandbox/server.py"
