"""書き出すファイルの改行。Windows のテキストモードは既定で \\n を CRLF にするので、改行を決めて書く。"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import init  # noqa: E402


def load_codd():
    spec = importlib.util.spec_from_file_location("codd_machine", TOOL / "machine" / "codd.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclass が自分のモジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


codd = load_codd()


class NewlineTest(unittest.TestCase):
    def test_new_file_is_lf(self) -> None:
        for mod in (codd, init):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "a.md"
                mod.write_text(path, "一\n二\n")
                self.assertEqual(path.read_bytes(), "一\n二\n".encode("utf-8"))

    def test_existing_crlf_file_stays_crlf(self) -> None:
        for mod in (codd, init):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "a.md"
                path.write_bytes(b"old\r\n")
                mod.write_text(path, path.read_text(encoding="utf-8") + "new\n")
                self.assertEqual(path.read_bytes(), b"old\r\nnew\r\n")

    def test_append_line_keeps_lf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".gitignore"
            path.write_bytes(b"node_modules/")
            init.append_line(path, ".codd/")
            self.assertEqual(path.read_bytes(), b"node_modules/\n.codd/\n")

    def test_no_platform_newline_writes(self) -> None:
        """Path.write_text / open(..., "w") は Windows で CRLF になるので、write_text を通す。"""
        for src in (TOOL / "machine" / "codd.py", TOOL / "init.py"):
            text = src.read_text(encoding="utf-8")
            self.assertNotIn(".write_text(", text, src.name)
            for m in re.finditer(r'\.open\("[wa]"[^)]*\)', text):
                self.assertIn("newline=", m.group(0), f"{src.name}: {m.group(0)}")


if __name__ == "__main__":
    unittest.main()
