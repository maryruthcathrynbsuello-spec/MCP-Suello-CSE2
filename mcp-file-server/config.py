"""All settings come from environment variables. No secrets or paths are hardcoded."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("MCP_ROOT", Path.home() / "mcp_workspace")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("MCP_TOKEN", "")                      # required for --http
ALLOW_DELETE = os.environ.get("MCP_ALLOW_DELETE", "0") == "1"  # destructive gate
MAX_READ_CHARS = int(os.environ.get("MCP_MAX_READ", "8000"))   # keeps results small
MAX_WRITE_BYTES = int(os.environ.get("MCP_MAX_WRITE", "1000000"))
LOG_FILE = Path(os.environ.get("MCP_LOG", Path(__file__).resolve().parent / "mcp_calls.log"))

{
  "mcpServers": {
    "file-connector": {
      "command": "python",
      "args": ["C:\\Users\\YourName\\mcp-file-server\\server.py"],
      "env": {
        "MCP_ROOT": "C:\\Users\\YourName\\mcp_workspace",
        "MCP_ALLOW_DELETE": "0"
      }
    }
  }
}