import os, sys, tempfile, unittest
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["MCP_ROOT"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools import TOOLS, ToolError, validate


def run(name, **args):
    validate(args, TOOLS[name]["inputSchema"])
    return TOOLS[name]["fn"](args)


class Sandbox(unittest.TestCase):
    def test_dotdot_blocked(self):
        with self.assertRaises(ToolError): run("read_file", path="../secret.txt")

    def test_absolute_blocked(self):
        with self.assertRaises(ToolError): run("read_file", path="/etc/passwd")

    def test_symlink_escape_blocked(self):
        outside = tempfile.mkdtemp()
        link = config.ROOT / "evil"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ToolError): run("list_files", path="evil")

    def test_unknown_argument_rejected(self):
        with self.assertRaises(ToolError): run("list_files", bogus="x")

    def test_str_replace_must_be_unique(self):
        run("write_file", path="a.txt", content="x x")
        with self.assertRaises(ToolError): run("str_replace", path="a.txt", old_str="x", new_str="y")

    def test_if_version_conflict(self):
        run("write_file", path="b.txt", content="one")
        v = run("read_file", path="b.txt").split()[0].split("=")[1]
        run("write_file", path="b.txt", content="two", if_version=v)
        with self.assertRaises(ToolError): run("write_file", path="b.txt", content="three", if_version=v)

    def test_existing_file_needs_flag(self):
        run("write_file", path="c.txt", content="one")
        with self.assertRaises(ToolError): run("write_file", path="c.txt", content="two")

    def test_delete_gated(self):
        run("write_file", path="d.txt", content="x")
        with self.assertRaises(ToolError): run("delete_file", path="d.txt")

    def test_handoff_and_search(self):
        run("init_handoff"); run("append_handoff", file="PLAN_LOG.md", entry="needle step")
        self.assertIn("PLAN_LOG.md", run("search_code", query="needle"))


if __name__ == "__main__":
    unittest.main()
