# Local MCP File Server (Python, stdlib only)

## Files
| File | Role |
|---|---|
| `config.py` | Settings from environment variables |
| `tools.py` | 9 tools, schema validation, sandbox (`safe_path`) |
| `server.py` | JSON-RPC protocol + stdio / HTTP transports |
| `tests/test_sandbox.py` | Path-traversal and tool tests |

## Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `MCP_ROOT` | `~/mcp_workspace` | Sandbox folder |
| `MCP_TOKEN` | none | Bearer token (required for `--http`) |
| `MCP_ALLOW_DELETE` | `0` | `1` enables `delete_file` |
| `MCP_MAX_READ` | `8000` | Max characters per read |
| `MCP_LOG` | `./mcp_calls.log` | Audit log of every tool call |

## Run
```bash
python tests/test_sandbox.py          # tests
python server.py                      # stdio (Claude Desktop, Gemini CLI)
MCP_TOKEN=secret python server.py --http --port 8765   # HTTP (for ngrok)
```

## Handoff protocol
Each session: `init_handoff` -> read `handoff/PROJECT_STATE.md` and `PLAN_LOG.md` -> work -> `append_handoff` to `PLAN_LOG.md`, `CHECKPOINTS.md`, `DECISIONS.md`, `PROJECT_STATE.md`.

## Security notes
- All paths resolve under `MCP_ROOT`; `..`, absolute paths and symlink escapes are rejected.
- HTTP binds to `127.0.0.1` only and requires `Authorization: Bearer <MCP_TOKEN>`.
- Deletion is off by default; handoff files can never be deleted.
- Tool calls are serialized by a lock and written to the audit log.
