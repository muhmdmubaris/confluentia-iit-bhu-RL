"""Sandbox HTTP service: runs the agent's circuit actions in memory."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

from common.circuit.actions import apply_tool

FORBIDDEN_KEYS = ("faults", "reference_actions", "intent", "verifier")
KNOWN_ROUTES = {
    "/health": {"GET"},
    "/state": {"GET"},
    "/reset": {"POST"},
    "/tool": {"POST"},
}

_current_board: dict | None = None
_state_lock = threading.Lock()


def _reject_constant(c):
    raise ValueError(f"invalid JSON constant: {c}")


def _check_forbidden_keys(obj, depth=0, count=0):
    if depth > 20 or count > 100000:
        raise ValueError("payload exceeds maximum depth or node limit")
    count += 1
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_KEYS:
                raise KeyError(k)
            count = _check_forbidden_keys(v, depth + 1, count)
    elif isinstance(obj, list):
        for item in obj:
            count = _check_forbidden_keys(item, depth + 1, count)
    return count


def _validate_and_store_board(body: dict) -> tuple[int, dict]:
    global _current_board
    if not isinstance(body, dict):
        return 400, {"error": "body must be an object"}

    try:
        _check_forbidden_keys(body)
    except KeyError as exc:
        key_name = exc.args[0] if exc.args else "unknown"
        return 400, {"error": f"board contains forbidden key '{key_name}'"}
    except ValueError as exc:
        return 400, {"error": str(exc)}

    if "board" not in body or not isinstance(body["board"], dict):
        return 400, {"error": "missing or invalid 'board'"}

    board = body["board"]

    # Validate parts
    parts = board.get("parts")
    if not isinstance(parts, dict) or not all(isinstance(v, dict) for v in parts.values()):
        return 400, {"error": "'parts' must be a dict of dicts"}

    # Validate pins
    pins = board.get("pins")
    if not isinstance(pins, dict) or not all(isinstance(v, dict) for v in pins.values()):
        return 400, {"error": "'pins' must be a dict of dicts"}

    # Validate powered (strict bool, reject 0/1)
    if type(board.get("powered")) is not bool:
        return 400, {"error": "'powered' must be a boolean"}

    # Validate damaged (list of strings)
    damaged = board.get("damaged")
    if not isinstance(damaged, list) or not all(isinstance(x, str) for x in damaged):
        return 400, {"error": "'damaged' must be a list of strings"}

    # Validate history
    history = board.get("history")
    if not isinstance(history, list):
        return 400, {"error": "'history' must be a list"}

    # Validate spec (optional, but must be list if present)
    if "spec" in board and not isinstance(board["spec"], list):
        return 400, {"error": "'spec' must be a list"}

    # Validate probe_budget (None or non-bool int >= 0)
    probe_budget = board.get("probe_budget")
    if probe_budget is not None and (type(probe_budget) is not int or probe_budget < 0):
        return 400, {"error": "'probe_budget' must be null or a non-negative integer"}

    # Validate probes_used (non-bool int >= 0)
    probes_used = board.get("probes_used")
    if probes_used is not None and (type(probes_used) is not int or probes_used < 0):
        return 400, {"error": "'probes_used' must be a non-negative integer"}

    # Prepare storage copy with defaults
    storing_board = copy.deepcopy(board)
    if "probe_budget" not in storing_board:
        storing_board["probe_budget"] = None
    if "probes_used" not in storing_board:
        storing_board["probes_used"] = 0

    with _state_lock:
        _current_board = storing_board

    return 200, {"ok": True}


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: dict, headers: list[tuple[str, str]] | None = None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_raw(code, data, headers=headers)

    def _send_raw(self, code: int, data: bytes, headers: list[tuple[str, str]] | None = None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if headers:
            for k, v in headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _drain_body(self):
        try:
            cl = self.headers.get("Content-Length")
            if cl:
                length = int(cl.strip())
                if 0 < length <= 1_000_000:
                    self.rfile.read(length)
        except (ValueError, OSError):
            pass

    def _check_route_and_method(self, method: str) -> bool:
        if self.path not in KNOWN_ROUTES:
            self._drain_body()
            self._send(404, {"error": "not found"})
            return False
        allowed = KNOWN_ROUTES[self.path]
        if method not in allowed:
            self._drain_body()
            allowed_header = ", ".join(sorted(allowed))
            self._send(405, {"error": "method not allowed"}, headers=[("Allow", allowed_header)])
            return False
        return True

    def _read_body_json(self) -> tuple[dict | None, bool]:
        te = self.headers.get("Transfer-Encoding", "")
        if "chunked" in te.lower():
            self._drain_body()
            self._send(400, {"error": "chunked transfer encoding is not supported"})
            return None, False

        cl_header = self.headers.get("Content-Length")
        if cl_header is None:
            self._send(400, {"error": "missing or invalid Content-Length"})
            return None, False

        try:
            length = int(cl_header.strip())
            if length < 0:
                self._send(400, {"error": "missing or invalid Content-Length"})
                return None, False
        except ValueError:
            self._send(400, {"error": "missing or invalid Content-Length"})
            return None, False

        if length > 1_000_000:
            self._send(413, {"error": "payload too large"}, headers=[("Connection", "close")])
            self.close_connection = True
            return None, False

        raw = self.rfile.read(length)
        if len(raw) < length:
            self._send(400, {"error": "unexpected end of stream"})
            return None, False

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._send(400, {"error": "body must be valid UTF-8"})
            return None, False

        try:
            body = json.loads(text, parse_constant=_reject_constant)
        except ValueError as exc:
            self._send(400, {"error": f"invalid JSON: {exc}"})
            return None, False

        if not isinstance(body, dict):
            self._send(400, {"error": "body must be an object"})
            return None, False

        return body, True

    def _guarded(self, fn):
        try:
            fn()
        except Exception as exc:
            try:
                self._send(500, {"error": f"internal error: {type(exc).__name__}"})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _handle_GET(self):
        if not self._check_route_and_method("GET"):
            return
        if self.path == "/health":
            self._send(200, {"ok": True})
        elif self.path == "/state":
            with _state_lock:
                if _current_board is None:
                    self._send(400, {"error": "no board loaded; POST /reset first"})
                    return
                # Snapshot under lock
                data = json.dumps({"board": _current_board}, ensure_ascii=False).encode("utf-8")
            self._send_raw(200, data)

    def _handle_POST(self):
        if not self._check_route_and_method("POST"):
            return

        body, ok = self._read_body_json()
        if not ok:
            return

        if self.path == "/reset":
            status, payload = _validate_and_store_board(body)
            self._send(status, payload)

        elif self.path == "/tool":
            name = body.get("name")
            if not isinstance(name, str) or not name:
                self._send(400, {"error": "missing or invalid 'name'"})
                return

            if "args" in body:
                args = body["args"]
                if not isinstance(args, dict):
                    self._send(400, {"error": "'args' must be an object"})
                    return
            else:
                args = {}

            global _current_board
            with _state_lock:
                if _current_board is None:
                    self._send(400, {"error": "no board loaded; POST /reset first"})
                    return

                new_board, output, is_error = apply_tool(_current_board, name, args)
                _current_board = new_board

            self._send(200, {"output": output, "is_error": is_error})

    def do_GET(self):
        self._guarded(self._handle_GET)

    def do_POST(self):
        self._guarded(self._handle_POST)

    def do_PUT(self):
        self._guarded(lambda: self._check_route_and_method("PUT"))

    def do_DELETE(self):
        self._guarded(lambda: self._check_route_and_method("DELETE"))

    def do_PATCH(self):
        self._guarded(lambda: self._check_route_and_method("PATCH"))

    def do_HEAD(self):
        self._guarded(lambda: self._check_route_and_method("HEAD"))

    def do_OPTIONS(self):
        self._guarded(lambda: self._check_route_and_method("OPTIONS"))


def make_server(host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main():
    host = os.environ.get("BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server = make_server(host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
