"""セッション継続（設計 2026-08-27 §4・未決 1 の決着）。

実体は 2 つある。**ネイティブのセッション機能を持つ CLI**は定義が argv 断片を宣言し、
**持たない自前 CLI**は前回の会話を材料として組み直す。どちらも無い定義は明示エラー
——黙って新規セッションとして走らせない（出力から見分けが付かないため）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import agentcli, herdcli, ollama_events  # noqa: E402

BUNDLED = Path(__file__).resolve().parents[5] / "agents"


class _RepoDefinitions(unittest.TestCase):
    """定義は**このリポジトリの agents/** を見る。

    `~/.agents/agents/` の配布物は入れ直すまで古いままなので、そちらを読むと
    「宣言を足したのにテストが知らない」が起きる（2026-08-29 に踏んだ）。
    """

    def setUp(self):
        patch = mock.patch.dict(os.environ, {"KIRO_AGENTS_DIR": str(BUNDLED)})
        patch.start()
        self.addCleanup(patch.stop)
        agentcli.clear_cache()
        self.addCleanup(agentcli.clear_cache)


class DeclarationTests(_RepoDefinitions):
    """宣言した CLI だけが argv で継続する。綴りは実機の --help で確かめたもの。"""

    def test_flag_style_cli_declares_continue_and_resume(self):
        for name in ("claude", "copilot", "cursor"):
            raw = json.loads((BUNDLED / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["continue_args"], ["--continue"], name)
            self.assertEqual(raw["resume_args"], ["--resume", "{session}"], name)

    def test_codex_declares_its_subcommand_form(self):
        raw = json.loads((BUNDLED / "codex.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["continue_args"], ["resume", "--last"])

    def test_a_cli_without_native_sessions_declares_nothing(self):
        for name in ("ollama", "aider", "kiro"):
            spec = agentcli.load_cli(name)
            self.assertEqual(agentcli.session_args(spec), [], name)


class ArgvAssemblyTests(_RepoDefinitions):
    def test_the_fragment_lands_after_the_subcommand_not_at_the_end(self):
        """codex の継続はサブコマンド。オプション列の後ろへ置くと別の意味になる。"""
        spec = agentcli.load_cli("codex")
        argv = agentcli.headless_cmd(spec, "", "P", session_continue=True)["argv"]
        self.assertEqual(argv[:4], ["codex", "exec", "resume", "--last"])

    def test_a_flag_style_cli_gets_the_flag(self):
        spec = agentcli.load_cli("claude")
        argv = agentcli.headless_cmd(spec, "", "P", session_continue=True)["argv"]
        self.assertEqual(argv[:2], ["claude", "--continue"])

    def test_resume_substitutes_the_session_id(self):
        spec = agentcli.load_cli("claude")
        argv = agentcli.headless_cmd(spec, "", "P", session_id="abc123")["argv"]
        self.assertIn("--resume", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], "abc123")

    def test_nothing_changes_without_the_flags(self):
        spec = agentcli.load_cli("claude")
        self.assertNotIn("--continue", agentcli.headless_cmd(spec, "", "P")["argv"])


class MaterialReconstructionTests(_RepoDefinitions):
    """自前 CLI の継続は材料の再構築。読むのは自分のログだけ。"""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self._tmp.name)
        self._patch = mock.patch.dict(
            os.environ, {"AGENT_OLLAMA_LOG_DIR": str(self.logs)})
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_log(self, name: str, messages) -> Path:
        path = self.logs / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "run_start", "model": "m"}) + "\n")
            for role, content in messages:
                fh.write(json.dumps({"kind": "message", "role": role,
                                     "content": content}, ensure_ascii=False) + "\n")
        return path

    def test_messages_are_read_in_order(self):
        path = self._write_log("s1", [("user", "調べて"), ("assistant", "調べました")])
        self.assertEqual(ollama_events.read_messages(path),
                         [("user", "調べて"), ("assistant", "調べました")])

    def test_only_conversation_events_count(self):
        path = self.logs / "s2.jsonl"
        path.write_text(
            json.dumps({"kind": "llm_end", "role": "assistant", "content": "内部"}) + "\n"
            + json.dumps({"kind": "message", "role": "system", "content": "規約"}) + "\n"
            + json.dumps({"kind": "message", "role": "user", "content": "本文"}) + "\n",
            encoding="utf-8")
        self.assertEqual(ollama_events.read_messages(path), [("user", "本文")])

    def test_a_session_id_is_the_log_name(self):
        self._write_log("20260829T101010-1-m", [("user", "あ")])
        self.assertIsNotNone(ollama_events.log_path_for("20260829T101010-1-m"))
        self.assertIsNone(ollama_events.log_path_for("知らない"))

    def test_continue_prepends_the_previous_conversation(self):
        self._write_log("s3", [("user", "前回の依頼"), ("assistant", "前回の答え")])
        built = {}
        rc = herdcli.cmd_toplevel(
            ["--agent", "ollama", "--continue", "-p", "続きをやって"],
            runner=lambda b: built.update(b) or 0)
        self.assertEqual(rc, 0)
        body = built["stdin"]
        self.assertIn("前回の依頼", body)
        self.assertIn("前回の答え", body)
        self.assertTrue(body.rstrip().endswith("続きをやって"), "本文は最後に置く")
        self.assertNotIn("--continue", built["argv"], "自前 CLI に argv の継続は無い")

    def test_a_missing_log_is_an_explicit_error(self):
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(["--agent", "ollama", "--continue", "-p", "続き"],
                                  err=err, runner=lambda b: 0)
        self.assertEqual(rc, 2)
        self.assertIn("継続の材料", err.getvalue())

    def test_an_unknown_session_id_is_an_explicit_error(self):
        self._write_log("s4", [("user", "あ")])
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(
            ["--agent", "ollama", "--resume", "存在しない", "-p", "続き"],
            err=err, runner=lambda b: 0)
        self.assertEqual(rc, 2)

    def test_resume_needs_a_value(self):
        err = io.StringIO()
        self.assertEqual(herdcli.cmd_toplevel(["--resume"], err=err), 2)
        self.assertIn("セッション ID", err.getvalue())

    def test_interactive_continuation_says_where_it_works(self):
        """材料は本文と一緒に渡すものなので、対話起動では受けない。"""
        self._write_log("s5", [("user", "あ")])
        err = io.StringIO()
        rc = herdcli.cmd_toplevel(["--agent", "ollama", "--continue"],
                                  err=err, launcher=lambda argv: 0)
        self.assertEqual(rc, 2)
        self.assertIn("-p", err.getvalue())


if __name__ == "__main__":
    unittest.main()
