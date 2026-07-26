"""R10 検査（`tools/ci/check_user_docs.py`）の単体テスト。

検査そのものが「素朴な grep では成立しない」ことへの回答なので、**何を落として何を
見るか**を固定するのがこのテストの主目的。最後の 1 本だけは性格が違い、実際の
利用者向け文書が今この瞬間に違反していないことを見る（＝ CI の R10 ゲート本体）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_user_docs as r10   # noqa: E402


def names(text: str) -> "list[str]":
    return [n for _, n, _ in r10.check_text(text)]


class ProseExtraction(unittest.TestCase):
    def test_flags_internal_name_in_body_text(self):
        self.assertEqual(names("停止したら resident を上げ直します。"), ["resident"])

    def test_ignores_fenced_code_block(self):
        text = "起動します。\n\n```bash\nagent-project serve --node pc-a\n```\n\n以上。"
        self.assertEqual(names(text), [])

    def test_ignores_tilde_fence(self):
        self.assertEqual(names("~~~\nnode sync resident\n~~~"), [])

    def test_ignores_inline_code(self):
        """キー名・スキーマ名はインラインコードで書く（契約の語彙は隠す対象ではない）。"""
        self.assertEqual(names("`node_id` は PC ごとに一意にします。"), [])
        self.assertEqual(names("契約は `agent-node-command.schema.json` です。"), [])

    def test_ignores_link_target(self):
        self.assertEqual(names("手順は [セットアップ](single-resident-setup.md) を見る。"), [])

    def test_ignores_reference_style_link_definition(self):
        self.assertEqual(names("[setup]: ../guides/single-resident-setup.md"), [])

    def test_ignores_bare_path_and_filename(self):
        self.assertEqual(names("~/.agents/commands へ置かれます。"), [])
        self.assertEqual(names("multi-pc-operations.md に書いてあります。"), [])

    def test_ignores_autolink(self):
        self.assertEqual(names("参照: <https://example.com/node/sync>"), [])

    def test_ignores_html_comment_including_multiline(self):
        self.assertEqual(names("本文 <!-- node -->続き"), [])
        self.assertEqual(names("本文\n<!-- resident\nsync -->\n続き"), [])

    def test_nodejs_is_not_an_internal_name(self):
        self.assertEqual(names("dashboard の実行には Node.js が要ります。"), [])

    def test_hyphenated_identifier_is_not_flagged(self):
        """`node-id-cutover` のような識別子・ガイド名は本文でも語ではなく名前。"""
        self.assertEqual(names("node-id-cutover の手順に従います。"), [])

    def test_allow_mark_suppresses_the_line(self):
        text = "- 内部名（node / sync / resident）が出ていた <!-- r10-allow: R10 の説明そのもの -->"
        self.assertEqual(names(text), [])

    def test_reports_line_number_and_original_line(self):
        text = "1 行目\n2 行目に node が出る\n3 行目"
        self.assertEqual(r10.check_text(text), [(2, "node", "2 行目に node が出る")])

    def test_case_insensitive(self):
        self.assertEqual(names("Resident を再起動します。"), ["resident"])

    def test_word_boundary_does_not_match_substring(self):
        self.assertEqual(names("同期しています（syncing 中）。"), [])


class Targets(unittest.TestCase):
    def test_target_list_is_not_empty_and_excludes_designs(self):
        files = [str(p) for p in r10.target_files()]
        self.assertTrue(files, "検査対象が 0 件になっている（glob の綴りを確認）")
        self.assertTrue(any(f.endswith("single-resident-setup.md") for f in files),
                        "セットアップガイドは検査対象に含める（実装計画 R4）")
        self.assertTrue(any(f.endswith("single-resident-canary.md") for f in files),
                        "canary ランブックは検査対象に含める（実装計画 R4）")
        self.assertFalse([f for f in files if "/docs/designs/" in f or "/docs/plans/" in f],
                         "設計書・計画書は内部の設計語彙で書くので対象外")


class RepositoryDocuments(unittest.TestCase):
    """R10 ゲート本体: 実際の利用者向け文書が違反していないこと。"""

    def test_user_facing_documents_have_no_internal_names(self):
        bad = []
        for path in r10.target_files():
            for lineno, name, line in r10.check_file(path):
                bad.append(f"{path}:{lineno}: {name} — {line}")
        self.assertEqual(bad, [], "利用者向け文書の本文に内部名がある（R10）:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
