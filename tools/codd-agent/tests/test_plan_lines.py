"""計画・変える段の申告の 1 行の読み方を固定する（シナリオで見つけた読み違い）。"""

from __future__ import annotations

import unittest

from test_classify import codd


class PlanLineTest(unittest.TestCase):
    def test_deleting_a_name_is_not_deleting_the_file(self) -> None:
        # 部品を消す行はファイルを消さない（文書の検査を変えたあとまで先送りしない）
        self.assertFalse(codd.deletes_file("app:src/Workspace.tsx — `ConfirmButton` を消し、選んだら進む"))
        self.assertTrue(codd.deletes_file("src/legacy/formatV1.js — 互換の関数ごとファイルを消す"))
        self.assertTrue(codd.deletes_file("src/old.py — 削除"))

    def test_waived_only_changes_change_nothing(self) -> None:
        # 設計書だけを直すときに、自分のファイルを変更不要と書いた変更案は、変える案に数えない
        self.assertTrue(codd.changes_nothing("なし"))
        self.assertTrue(codd.changes_nothing("- 変更不要: src/app.py — 実装は文書に合っている"))
        self.assertFalse(codd.changes_nothing("- src/app.py — `hello` を 2 を返すように変える"))

    def test_deviation_lines_accept_comma_separated_targets(self) -> None:
        m = codd._DEVIATION.match("- tests/test_service.py, tests/test_users.py — 変更不要: `status` は辞書の項目名")
        self.assertIsNotNone(m)
        self.assertEqual([t.strip() for t in m.group(1).split(",")], ["tests/test_service.py", "tests/test_users.py"])
        m = codd._DEVIATION.match("- `status` — 関係なし: 別の意味の項目名")
        self.assertEqual((m.group(1), m.group(2)), ("`status`", "関係なし"))


if __name__ == "__main__":
    unittest.main()
