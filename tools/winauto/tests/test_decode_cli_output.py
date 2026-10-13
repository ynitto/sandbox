# -*- coding: utf-8 -*-
"""decode_cli_output（winauto.py にある原本）の単体テスト。bytes を食わせるだけで、Windows は要らない。

JS 版 decodeCliOutput（tools/agent-dashboard/src/features/cowork/main/loopProvider.js:51、
テストは tools/agent-dashboard/test/cowork.test.js）と同じ段取りであることを固定する。
写しが原本と同じ本体であることは tools/ci/tests/test_check_subprocess_encoding.py が見る。

    python -m unittest discover -s tools/winauto/tests
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

_WINAUTO_PY = Path(__file__).resolve().parent.parent / "winauto.py"
_spec = importlib.util.spec_from_file_location("winauto_decode_under_test", _WINAUTO_PY)
winauto = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(winauto)
decode = winauto.decode_cli_output


class DecodeCliOutputTest(unittest.TestCase):
    def test_cp932(self):
        self.assertEqual(decode(b"\x82\xa0"), "あ")

    def test_utf8(self):
        self.assertEqual(decode(b"ok"), "ok")
        self.assertEqual(decode("日本語".encode("utf-8")), "日本語")

    def test_empty(self):
        self.assertEqual(decode(b""), "")

    def test_none(self):
        self.assertEqual(decode(None), "")

    def test_str_passes_through(self):
        self.assertEqual(decode("そのまま"), "そのまま")

    def test_unreadable_returns_utf8_with_replacement(self):
        """CP932 でも読めない（先行バイトで切れている）なら例外を投げず置換文字つきで返す。"""
        self.assertEqual(decode(b"ok\x82"), "ok�")

    def test_real_process_emitting_cp932(self):
        """cmd.exe が日本語のユーザー名を返すのと同じバイト列を、実際のプロセスから受ける。"""
        raw = "日本語ユーザー".encode("cp932")
        prog = f"import os; os.write(1, {raw!r})"
        r = subprocess.run([sys.executable, "-c", prog], capture_output=True)
        self.assertEqual(decode(r.stdout), "日本語ユーザー")


if __name__ == "__main__":
    unittest.main()
