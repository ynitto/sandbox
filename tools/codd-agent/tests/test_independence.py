"""codd-agent は、特定のテストの道具や、画面・スクリーンショットのある成果物を前提にしない。

画面の仕様書やスクリーンショットが無いプロジェクトもある。テストの道具とは evidence の共通の形だけでつながり、
道具の名前・設定を知る処理や、画面・画像に限った処理を本体に持たない（テストが作るファイルは種類を問わず同じに扱う）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 本体（ステートマシンの定義・スクリプト・エージェントへの指示）と置き方のスクリプト
TARGETS = [ROOT / "init.py", ROOT / "install.py",
           *sorted(p for p in (ROOT / "machine").rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".yaml", ".json"))]
# 特定の道具の名前と、画面・画像を前提にした言い回し
FORBIDDEN = re.compile(r"webui|web-test|screenshot|スクリーンショット|スクショ|撮|画面|画像", re.I)
# evidence の項目を種類の名前で画像として見分ける処理（種類の名前は道具が決める）
IMAGE_KIND = re.compile(r"""["']image["']""")


class IndependenceTest(unittest.TestCase):
    def test_no_tool_or_screen_specific_wording(self) -> None:
        hits = [f"{p.relative_to(ROOT)}:{i}: {line.strip()}"
                for p in TARGETS for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if FORBIDDEN.search(line)]
        self.assertEqual(hits, [])

    def test_outputs_are_not_told_apart_by_kind(self) -> None:
        code = (ROOT / "machine" / "codd.py").read_text(encoding="utf-8")
        hits = [f"codd.py:{i}: {line.strip()}" for i, line in enumerate(code.splitlines(), 1)
                if IMAGE_KIND.search(line) and "FILE_TYPES" not in line]   # graphify のノードの種類は別物
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
