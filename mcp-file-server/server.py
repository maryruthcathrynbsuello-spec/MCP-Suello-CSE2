#!/usr/bin/env python3
"""MCP server (JSON-RPC 2.0). Transports: stdio (default) or local Streamable-HTTP-style POST. Stdlib only."""
import argparse
import hmac
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from tools import TOOLS, ToolError, validate

NAME, VERSION, DEFAULT_PROTOCOL = "local-file-mcp", "2.0.0", "2024-11-05"
INSTRUCTIONS = ("Start each session with init_handoff, then read handoff/PROJECT_STATE.md and PLAN_LOG.md. "
                "Read before you write. Log plans and decisions with append_handoff.")
LOCK = threading.Lock()  # one tool call at a time: no interleaved writes from concurrent agents


def log(msg):  # stderr only; stdout belongs to the protocol
    print(f"[{NAME}] {msg}", file=sys.stderr, flush=True)


def audit(name, args, ok):
    short = {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v) for k, v in args.items()} \
        if isinstance(args, dict) else str(args)[:80]
    try:
        with open(config.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%F %T"), "tool": name, "args": short, "ok": ok},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def call_tool(name, args):
    t = TOOLS[name]
    try:
        validate(args, t["inputSchema"])
        with LOCK:
            text, ok = t["fn"](args), True
    except ToolError as e:
        text, ok = f"Error: {e}", False
    except Exception as e:  # never leak a stack trace to the agent
        log(f"internal error in {name}: {e!r}")
        text, ok = "Error: internal failure (see server log).", False
    audit(name, args, ok)
    return {"content": [{"type": "text", "text": text}], "isError": not ok}


def handle(msg):
    """Return a response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None

    def ok(result): return {"jsonrpc": "2.0", "id": mid, "result": result}
    def err(code, text): return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": NAME, "version": VERSION},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                             for n, t in TOOLS.items()]})
    if method == "tools/call":
        if params.get("name") not in TOOLS:
            return err(-32602, f"Unknown tool: {params.get('name')}")
        return ok(call_tool(params["name"], params.get("arguments") or {}))
    return err(-32601, f"Method not found: {method}")


# ---------- transport 1: stdio ----------
def run_stdio():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    log(f"stdio ready, workspace={config.ROOT}")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ---------- transport 2: HTTP (bearer token, localhost bind) ----------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(405, {"error": "use POST"})

    def do_POST(self):
        auth = self.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(auth, f"Bearer {config.TOKEN}".encode()):
            return self._send(401, {"error": "unauthorized"})
        if self.path != "/mcp":
            return self._send(404, {"error": "not found"})
        size = int(self.headers.get("Content-Length", 0))
        if size > 2_000_000:
            return self._send(413, {"error": "too large"})
        try:
            resp = handle(json.loads(self.rfile.read(size)))
        except (ValueError, AttributeError):
            return self._send(400, {"error": "bad request"})
        self._send(202) if resp is None else self._send(200, resp)

    def log_message(self, *a):
        pass


def run_http(port):
    if not config.TOKEN:
        sys.exit("Refusing to start: set MCP_TOKEN before using --http.")
    log(f"http ready on 127.0.0.1:{port}/mcp, workspace={config.ROOT}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve over HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    opts = ap.parse_args()
    run_http(opts.port) if opts.http else run_stdio()
