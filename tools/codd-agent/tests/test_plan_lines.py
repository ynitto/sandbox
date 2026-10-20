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
        self.assertEqual(codd.re.findall(codd._DEVIATION_TARGET, m.group(1)), ["tests/test_service.py", "tests/test_users.py"])
        # 全角の読点で並べても同じに読む（日本語の文で書いた申告を読み落とさない）
        for sep in ("、", "，", " 、 "):
            m = codd._DEVIATION.match(f"- tests/test_service.py{sep}`tests/test_users.py` — 変更不要: 辞書の項目名")
            self.assertIsNotNone(m, sep)
            self.assertEqual([t.strip("`") for t in codd.re.findall(codd._DEVIATION_TARGET, m.group(1))],
                             ["tests/test_service.py", "tests/test_users.py"])
        m = codd._DEVIATION.match("- `status` — 関係なし: 別の意味の項目名")
        self.assertEqual((m.group(1), m.group(2)), ("`status`", "関係なし"))
        # 空白を含む名前（新しい文書の題）も `…` で囲めば書ける
        m = codd._DEVIATION.match("- `返金 API` — 変更不要: 文書の題でテストは要らない")
        self.assertEqual(m.group(1), "`返金 API`")

    def test_issue_gist_drops_the_hint_and_keeps_paths_whole(self) -> None:
        # 振り返りの出来事は、直し方の添え書きを外し、パスの途中で切らない
        p = "自分の変更案のファイルをまだ変えていません（最後まで変えてください。「計画との違い」に書く）: src/app.py, src/b.py"
        self.assertEqual(codd.issue_gist(p, 200), "自分の変更案のファイルをまだ変えていません: src/app.py, src/b.py")
        self.assertEqual(codd.issue_gist(p, 35), "自分の変更案のファイルをまだ変えていません: src/app.py ほか")

    def test_caches_made_by_running_tests_are_not_changes(self) -> None:
        self.assertTrue(codd.is_cache("orders/__pycache__/models.cpython-313.pyc"))
        self.assertTrue(codd.is_cache(".pytest_cache/v/cache/lastfailed"))
        self.assertFalse(codd.is_cache("orders/models.py"))

    def test_unexported_functions_of_an_exporting_module_are_private(self) -> None:
        names = dict(codd.code_names("export function hit() {}\nfunction minuteOf(t) {}\nconst buckets = {}\n"))
        self.assertIn("hit", names)
        self.assertNotIn("minuteOf", names)
        self.assertNotIn("buckets", names)


if __name__ == "__main__":
    unittest.main()
