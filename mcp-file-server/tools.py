"""Tool definitions: single purpose, strict schema, readable errors (no stack traces)."""
import hashlib
import os
import time
from pathlib import Path

import config


class ToolError(Exception):
    """Expected failure. Returned to the agent with isError=true so it can recover."""


TOOLS = {}
HANDOFF_DIR = "handoff"
HANDOFF_FILES = ["PROJECT_STATE.md", "PLAN_LOG.md", "CHECKPOINTS.md", "DECISIONS.md"]


# ---------- registry + validation ----------
def tool(name, description, properties, required=()):
    def deco(fn):
        TOOLS[name] = {
            "fn": fn,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        }
        return fn
    return deco


_TYPES = {"string": str, "integer": int, "boolean": bool}


def validate(args, schema):
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    for key in schema["required"]:
        if key not in args:
            raise ToolError(f"Missing required argument: {key}")
    for key, val in args.items():
        spec = schema["properties"].get(key)
        if spec is None:
            raise ToolError(f"Unknown argument: {key}")
        py = _TYPES[spec["type"]]
        if not isinstance(val, py) or (py is int and isinstance(val, bool)):
            raise ToolError(f"Argument '{key}' must be of type {spec['type']}")
        if "enum" in spec and val not in spec["enum"]:
            raise ToolError(f"Argument '{key}' must be one of {spec['enum']}")


# ---------- sandbox ----------
def inside(p: Path) -> bool:
    return p == config.ROOT or config.ROOT in p.parents


def safe_path(rel) -> Path:
    """Resolve against ROOT (follows symlinks) and reject any escape."""
    if not isinstance(rel, str) or "\x00" in rel:
        raise ToolError("Invalid path")
    target = (config.ROOT / rel).resolve()
    if not inside(target):
        raise ToolError("Path escapes the workspace")
    return target


# ---------- helpers ----------
def version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:12]


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError("Not a UTF-8 text file")


def atomic_write(p: Path, data: bytes) -> None:
    if len(data) > config.MAX_WRITE_BYTES:
        raise ToolError(f"Content too large (limit {config.MAX_WRITE_BYTES} bytes)")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)  # a crash never leaves a half-written file


# ---------- tools ----------
@tool("list_files",
      "List files and folders in one workspace directory (non-recursive). Use first to explore. "
      "Paths are relative to the workspace root.",
      {"path": {"type": "string", "description": "Relative directory. Default '.'"}})
def list_files(a):
    rel = a.get("path", ".")
    p = safe_path(rel)
    if not p.is_dir():
        raise ToolError(f"Not a directory: {rel}")
    rows = [f"[DIR]  {c.name}/" if c.is_dir() else f"[FILE] {c.name} ({c.stat().st_size} bytes)"
            for c in sorted(p.iterdir())]
    return "\n".join(rows) or "(empty)"


@tool("read_file",
      "Read a UTF-8 text file in chunks. Returns a version hash (pass it as if_version to write_file) "
      "and tells you the next offset if more content exists. Read before you write.",
      {"path": {"type": "string"},
       "offset": {"type": "integer", "description": "Start character. Default 0"},
       "limit": {"type": "integer", "description": "Max characters (server caps it)"}},
      ["path"])
def read_file(a):
    p = safe_path(a["path"])
    if not p.is_file():
        raise ToolError(f"File not found: {a['path']}")
    raw = p.read_bytes()
    text = decode(raw)
    offset = a.get("offset", 0)
    limit = min(a.get("limit", config.MAX_READ_CHARS), config.MAX_READ_CHARS)
    if offset < 0 or limit < 1:
        raise ToolError("offset must be >= 0 and limit >= 1")
    chunk = text[offset:offset + limit]
    end = offset + len(chunk)
    more = f" MORE: call again with offset={end}" if end < len(text) else ""
    return f"version={version(raw)} chars={len(text)} range={offset}-{end}{more}\n---\n{chunk}"


@tool("write_file",
      "Create a file, or replace one. For an existing file you must pass if_version (from read_file) "
      "or overwrite=true. A stale if_version fails so you never clobber another agent's edit.",
      {"path": {"type": "string"},
       "content": {"type": "string"},
       "if_version": {"type": "string", "description": "Version from read_file"},
       "overwrite": {"type": "boolean", "description": "Replace without a version check"}},
      ["path", "content"])
def write_file(a):
    p = safe_path(a["path"])
    if p.is_dir():
        raise ToolError("Path is a directory")
    if p.exists():
        current = version(p.read_bytes())
        expected = a.get("if_version")
        if expected is not None:
            if expected != current:
                raise ToolError(f"Version conflict: file changed (current version={current}). Re-read it first.")
        elif not a.get("overwrite", False):
            raise ToolError("File exists. Pass if_version (preferred) or overwrite=true.")
    data = a["content"].encode("utf-8")
    atomic_write(p, data)
    return f"Wrote {a['path']} version={version(data)}"


@tool("str_replace",
      "Replace one exact occurrence of old_str in a file. old_str must appear exactly once or the call "
      "fails, so widen the match with surrounding text.",
      {"path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}},
      ["path", "old_str", "new_str"])
def str_replace(a):
    p = safe_path(a["path"])
    if not p.is_file():
        raise ToolError(f"File not found: {a['path']}")
    if not a["old_str"]:
        raise ToolError("old_str must not be empty")
    text = decode(p.read_bytes())
    n = text.count(a["old_str"])
    if n == 0:
        raise ToolError("old_str not found")
    if n > 1:
        raise ToolError(f"old_str matches {n} times. Include surrounding text to make it unique.")
    data = text.replace(a["old_str"], a["new_str"], 1).encode("utf-8")
    atomic_write(p, data)
    return f"Edited {a['path']} version={version(data)}"


@tool("create_folder",
      "Create a folder (and any missing parents) inside the workspace.",
      {"path": {"type": "string"}}, ["path"])
def create_folder(a):
    safe_path(a["path"]).mkdir(parents=True, exist_ok=True)
    return f"Folder ready: {a['path']}"


@tool("delete_file",
      "DESTRUCTIVE. Delete one file (not folders, not handoff files). Disabled unless the server "
      "owner sets MCP_ALLOW_DELETE=1.",
      {"path": {"type": "string"}}, ["path"])
def delete_file(a):
    if not config.ALLOW_DELETE:
        raise ToolError("Deletion is disabled on this server (MCP_ALLOW_DELETE is not 1).")
    p = safe_path(a["path"])
    if not p.is_file():
        raise ToolError(f"File not found: {a['path']}")
    if p.parent == safe_path(HANDOFF_DIR) and p.name in HANDOFF_FILES:
        raise ToolError("Handoff files cannot be deleted")
    p.unlink()
    return f"Deleted {a['path']}"


@tool("search_code",
      "Case-insensitive text search across workspace files. Returns file:line matches only, so use it "
      "instead of reading whole files.",
      {"query": {"type": "string"},
       "path": {"type": "string", "description": "Folder to search. Default '.'"},
       "max_results": {"type": "integer", "description": "Default 20"}},
      ["query"])
def search_code(a):
    q = a["query"].lower()
    if not q:
        raise ToolError("query must not be empty")
    base = safe_path(a.get("path", "."))
    limit = a.get("max_results", 20)
    hits = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            fp = Path(dirpath) / name
            try:
                if not inside(fp.resolve()) or fp.stat().st_size > 1_000_000:
                    continue
                lines = fp.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(lines, 1):
                if q in line.lower():
                    hits.append(f"{fp.relative_to(config.ROOT).as_posix()}:{i}: {line.strip()[:120]}")
                    if len(hits) >= limit:
                        return "\n".join(hits) + "\n(truncated at max_results)"
    return "\n".join(hits) or "No matches"


@tool("init_handoff",
      "Create handoff/PROJECT_STATE.md, PLAN_LOG.md, CHECKPOINTS.md and DECISIONS.md if missing. "
      "Safe to call at the start of every session.",
      {})
def init_handoff(a):
    made = []
    for name in HANDOFF_FILES:
        p = safe_path(f"{HANDOFF_DIR}/{name}")
        if not p.exists():
            atomic_write(p, f"# {name[:-3]}\n".encode())
            made.append(name)
    return "Created: " + ", ".join(made) if made else "Handoff files already exist."


@tool("append_handoff",
      "Append a timestamped entry to a handoff file (append-only, nothing is overwritten). Log your "
      "plan in PLAN_LOG, milestones in CHECKPOINTS, choices in DECISIONS, current status in PROJECT_STATE.",
      {"file": {"type": "string", "enum": HANDOFF_FILES}, "entry": {"type": "string"}},
      ["file", "entry"])
def append_handoff(a):
    p = safe_path(f"{HANDOFF_DIR}/{a['file']}")
    old = p.read_bytes() if p.exists() else f"# {a['file'][:-3]}\n".encode()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    atomic_write(p, old + f"\n## {stamp}\n{a['entry']}\n".encode("utf-8"))
    return f"Appended to {a['file']}"
