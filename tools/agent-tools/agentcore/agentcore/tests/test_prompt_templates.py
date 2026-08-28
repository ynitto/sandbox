"""プロンプトの外出し（設計 2026-08-27 §3.5 / 段 13）。

受入条件は「現行のプロンプトを宣言へ移して**出力が変わらない**ことを確認してから、
調整を始める」。よってここの主役はゴールデン——既定のまま組んだプロンプトが、外出し前の
ハードコード文字列と 1 バイトも違わないことを縛る。宣言（`*-template:`）が差し替える
経路はその上に載る。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import ollama_loop, slashroute  # noqa: E402

# 外出し前のハードコード（bee5b89d9 時点の system_prompt / nudge / feedback を転記）。
_OLD_BASH_SYSTEM = (
    "あなたはローカル実行エージェント。道具はシェル（bash）1 つだけです。\n"
    "\n"
    "出力の規約（厳守）:\n"
    "1. コマンドを実行するときは bash のコードブロックを 1 つだけ出す。\n"
    "   実行結果は次のターンで渡されるので、結果を待たずに続きを書かない。\n"
    "2. 結果を見てから次の 1 手を決める。1 ターンに 1 ブロック。\n"
    "3. 完了したらコードブロックを出さず、成果を報告して最後の行に TASK_COMPLETE と書く。\n"
    "4. 人へ質問はできない。曖昧なら最も妥当な前提を選び、採用した前提を報告に明記する。\n"
    "5. 作業ディレクトリは /work。この範囲の外を変更しない。\n"
)
_OLD_READ_SYSTEM = (
    "あなたはローカル調査エージェント。道具は**読み取り専用のコマンド**だけです。\n"
    "\n"
    "出力の規約（厳守）:\n"
    "1. 調べるときはコードブロックを 1 つだけ出す。中身はコマンド 1 つ。\n"
    "   実行結果は次のターンで渡されるので、結果を待たずに続きを書かない。\n"
    f"2. 使えるのは {' '.join(sorted(ollama_loop._READ_COMMANDS))} と "
    f"git の {' '.join(sorted(ollama_loop._READ_GIT_SUBCOMMANDS))}。\n"
    "3. パイプ・リダイレクト・変数展開・ワイルドカード（`|` `>` `$` `*` 等）は"
    "使えません（`-name '*.py'` のように引用符で囲めば文字として渡せます）。"
    "ファイルの作成・変更・削除もできません。\n"
    "4. 完了したらコードブロックを出さず、成果を報告して最後の行に TASK_COMPLETE と書く。\n"
    "5. 人へ質問はできない。曖昧なら最も妥当な前提を選び、採用した前提を報告に明記する。\n"
    "6. 作業ディレクトリは /work。\n"
)
_OLD_NUDGE = ("規約から外れています。次の 1 手を bash のコードブロック 1 つで示すか、"
              "完了なら成果を報告して最後の行に TASK_COMPLETE と書いてください。")


class DefaultsAreByteIdenticalTests(unittest.TestCase):
    def test_bash_system_prompt_is_unchanged(self):
        self.assertEqual(ollama_loop.system_prompt("/work", "bash"), _OLD_BASH_SYSTEM)

    def test_read_system_prompt_is_unchanged(self):
        self.assertEqual(ollama_loop.system_prompt("/work", "read"), _OLD_READ_SYSTEM)

    def test_loop_messages_are_unchanged_by_default(self):
        """instance = task そのまま・nudge・観測の詰め方が外出し前と同一。"""
        replies = iter([
            {"text": "規約を外れた応答"},                       # → nudge
            {"text": "やります\n```bash\nls\n```"},             # → 実行 → feedback
            {"text": "できました\nTASK_COMPLETE"},
        ])
        seen = []

        def fake_chat(model, messages, **_kw):
            seen.append([dict(m) for m in messages])
            return dict(next(replies), tokens_in=0, tokens_out=0)

        with mock.patch.object(ollama_loop, "chat_once", side_effect=fake_chat), \
                mock.patch.object(ollama_loop, "run_command",
                                  return_value={"exit_code": 0, "output": "a.txt",
                                                "duration_sec": 0.0}):
            result = ollama_loop.run_loop("m", "調べて", cwd="/work")
        self.assertEqual(result["status"], "done")
        first = seen[0]
        self.assertEqual(first[0]["content"], _OLD_BASH_SYSTEM.strip())
        self.assertEqual(first[1]["content"], "調べて")
        self.assertEqual(seen[1][-1]["content"], _OLD_NUDGE)
        self.assertEqual(
            seen[2][-1]["content"],
            "実行結果（終了コード 0）:\n```\na.txt\n```\n"
            "続けてください（完了なら報告と TASK_COMPLETE）。")

    def test_declared_templates_replace_each_slot(self):
        replies = iter([
            {"text": "外れた応答"},
            {"text": "```bash\nls\n```"},
            {"text": "TASK_COMPLETE"},
        ])
        seen = []

        def fake_chat(model, messages, **_kw):
            seen.append([dict(m) for m in messages])
            return dict(next(replies), tokens_in=0, tokens_out=0)

        templates = {"system": "役割 1 行だけ（{cwd}）",
                     "instance": "手順:\n{task}",
                     "observation": "結果 {exit_code}: {output}",
                     "format_error": "言い直し（{done_marker}）"}
        with mock.patch.object(ollama_loop, "chat_once", side_effect=fake_chat), \
                mock.patch.object(ollama_loop, "run_command",
                                  return_value={"exit_code": 3, "output": "x",
                                                "duration_sec": 0.0}):
            ollama_loop.run_loop("m", "task 本文", cwd="/work", templates=templates)
        self.assertEqual(seen[0][0]["content"], "役割 1 行だけ（/work）")
        self.assertEqual(seen[0][1]["content"], "手順:\ntask 本文")
        self.assertEqual(seen[1][-1]["content"], "言い直し（TASK_COMPLETE）")
        self.assertEqual(seen[2][-1]["content"], "結果 3: x")


class RenderTemplateTests(unittest.TestCase):
    def test_unknown_braces_are_left_alone(self):
        """テンプレート本文に JSON の例を書ける（str.format ではないこと）。"""
        self.assertEqual(
            ollama_loop.render_template('出力は {"ok": true} の形。dir={cwd}', cwd="/w"),
            '出力は {"ok": true} の形。dir=/w')

    def test_values_are_not_rescanned(self):
        self.assertEqual(
            ollama_loop.render_template("{task}", task="literal {cwd}"),
            "literal {cwd}")


class DeclarationTemplateTests(unittest.TestCase):
    def _plan(self, tmp: Path, frontmatter: str) -> slashroute.Plan:
        (tmp / "verify.md").write_text(f"---\n{frontmatter}\n---\n本文\n",
                                       encoding="utf-8")
        with mock.patch.dict(os.environ, {"AGENT_COMMANDS_DIR": str(tmp)}):
            slashroute.clear_cache()
            try:
                return slashroute.plan("/verify\nやって")
            finally:
                slashroute.clear_cache()

    def test_template_file_is_loaded_relative_to_the_declaration(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "verify-system.md").write_text("判定役の規約\n", encoding="utf-8")
            launch = self._plan(tmp, "system-template: verify-system.md")
            self.assertEqual(dict(launch.templates), {"system": "判定役の規約\n"})

    def test_a_missing_template_file_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "verify.md").write_text(
                "---\nsystem-template: missing.md\n---\n本文\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"AGENT_COMMANDS_DIR": str(tmp)}):
                slashroute.clear_cache()
                try:
                    with self.assertRaises(slashroute.DeclarationError):
                        slashroute.load_declaration(tmp / "verify.md")
                finally:
                    slashroute.clear_cache()


if __name__ == "__main__":
    unittest.main()
