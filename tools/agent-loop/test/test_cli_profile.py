#!/usr/bin/env python3
"""エージェント CLI 差し替え（CliProfile）の単体テスト。

対象: agent_loop/cliprofile.py — ERE 変換、待機判定（ready/busy/静穏）、
slash 行の起動記号差し替え、agents/<name>.json 契約からのプロファイル解決。
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402


class CompileEreTest(unittest.TestCase):
    def test_posix_classes_translate(self):
        rx = al._compile_ere("^[[:space:]]*[>?❯›][[:space:]]*$")
        self.assertIsNotNone(rx)
        self.assertTrue(rx.search("  > "))
        self.assertTrue(rx.search("x\n❯\ny"))
        self.assertFalse(rx.search("no prompt here"))

    def test_case_insensitive_like_grep_i(self):
        rx = al._compile_ere("Esc to interrupt")
        self.assertTrue(rx.search("... (esc to interrupt)"))

    def test_broken_pattern_returns_none(self):
        self.assertIsNone(al._compile_ere("(["))

    def test_empty_returns_none(self):
        self.assertIsNone(al._compile_ere(""))


class LegacyProfileTest(unittest.TestCase):
    """agent_cli 未指定の既定プロファイルは従来の _PROMPT_RE 判定と同一。"""

    def setUp(self):
        self.p = al.CliProfile()

    def test_is_legacy(self):
        self.assertTrue(self.p.is_legacy)
        self.assertEqual(self.p.name, "kiro")
        self.assertEqual(self.p.clear_command, "/clear")

    def test_ready_matches_prompt_symbol(self):
        self.assertTrue(self.p.is_ready("output...\n> "))
        self.assertTrue(self.p.is_ready("!> "))

    def test_not_ready_without_prompt(self):
        self.assertFalse(self.p.is_ready("thinking hard"))
        self.assertFalse(self.p.is_ready(""))

    def test_prompt_only_in_tail_lines_counts(self):
        # 従来どおり空行を除く末尾 3 行だけを見る（スクロールした過去のプロンプトは無視）
        content = ">\n" + "\n".join(f"line{i}" for i in range(5))
        self.assertFalse(self.p.is_ready(content))

    def test_rewrite_slash_is_identity(self):
        self.assertEqual(self.p.rewrite_slash("/compact"), "/compact")


def _spec(**inter):
    base_inter = {"command": ["cli", "chat"]}
    base_inter.update(inter)
    return {"skill_command_prefix": "/", "interactive": base_inter}


class BusyPatternTest(unittest.TestCase):
    """入力欄を出したまま処理する TUI（claude 等）は busy_pattern が判定の正。"""

    def setUp(self):
        self.p = al.CliProfile("claude", _spec(
            ready_pattern="│[[:space:]]*[>❯›]|^[[:space:]]*[>?❯›][[:space:]]*$",
            busy_pattern="esc to interrupt"))

    def test_busy_wins_even_if_input_box_visible(self):
        content = "✻ Deliberating… (esc to interrupt)\n╭────╮\n│ > │\n╰────╯"
        self.assertEqual(self.p.classify(content), "busy")
        self.assertFalse(self.p.is_ready(content))
        self.assertFalse(self.p.is_idle("%1", content))

    def test_idle_when_busy_marker_gone(self):
        content = "done.\n╭────╮\n│ > │\n╰────╯"
        self.assertEqual(self.p.classify(content), "idle")
        self.assertTrue(self.p.is_idle("%1", content))

    def test_unknown_without_quiet_is_busy(self):
        self.assertFalse(self.p.is_idle("%1", "streaming output without markers"))


class QuietDetectionTest(unittest.TestCase):
    """パターンで判定できない CLI は「画面が N 秒変化しない」ことを待機とみなす。"""

    def setUp(self):
        self.now = [1000.0]
        self.p = al.CliProfile("plain", _spec(
            ready_pattern="ZZZ-never-matches-ZZZ", idle_quiet_sec=5),
            clock=lambda: self.now[0])

    def test_unchanged_content_becomes_idle_after_quiet_sec(self):
        self.assertFalse(self.p.is_idle("%1", "same screen"))
        self.now[0] += 3
        self.assertFalse(self.p.is_idle("%1", "same screen"))
        self.now[0] += 3
        self.assertTrue(self.p.is_idle("%1", "same screen"))

    def test_changing_content_resets_timer(self):
        self.assertFalse(self.p.is_idle("%1", "screen a"))
        self.now[0] += 6
        self.assertFalse(self.p.is_idle("%1", "screen b"))  # 変化した → 振り出し
        self.now[0] += 4
        self.assertFalse(self.p.is_idle("%1", "screen b"))
        self.now[0] += 2
        self.assertTrue(self.p.is_idle("%1", "screen b"))

    def test_forget_pane_drops_state(self):
        self.p.is_idle("%1", "same screen")
        self.now[0] += 10
        self.p.forget_pane("%1")
        self.assertFalse(self.p.is_idle("%1", "same screen"))  # 記憶が消えた → 振り出し


class RewriteSlashTest(unittest.TestCase):
    def setUp(self):
        spec = _spec()
        spec["skill_command_prefix"] = "$"
        self.p = al.CliProfile("codex", spec)

    def test_leading_slash_command_rewritten(self):
        self.assertEqual(self.p.rewrite_slash("/healthcheck"), "$healthcheck")
        self.assertEqual(self.p.rewrite_slash("/report --lang ja"), "$report --lang ja")

    def test_paths_untouched(self):
        self.assertEqual(self.p.rewrite_slash("/home/user/file"), "/home/user/file")


class ResolveProfileTest(unittest.TestCase):
    """設定の agent_cli → agents/<name>.json 契約からの解決（KIRO_AGENTS_DIR seam）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self._old_env = os.environ.get("KIRO_AGENTS_DIR")
        os.environ["KIRO_AGENTS_DIR"] = str(self.tmp)
        # agentcore の定義キャッシュを排除（プロセス内で他テストと共有されるため）
        if al._AGENTCLI_MOD is not None:
            al._AGENTCLI_MOD.clear_cache()
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_env is None:
            os.environ.pop("KIRO_AGENTS_DIR", None)
        else:
            os.environ["KIRO_AGENTS_DIR"] = self._old_env
        if al._AGENTCLI_MOD is not None:
            al._AGENTCLI_MOD.clear_cache()
        al._set_cli_profile(None)

    def _write(self, name, spec):
        (self.tmp / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")

    def test_unset_agent_cli_returns_none(self):
        self.assertIsNone(al._resolve_cli_profile({}))
        self.assertIsNone(al._resolve_cli_profile({"agent_cli": "  "}))

    def test_resolves_argv_and_detection(self):
        self._write("mycli", {
            "command": ["mycli", "run"],
            "model_flag": "--model",
            "skill_command_prefix": "$",
            "interactive": {
                "command": ["mycli", "chat"],
                "write_args": ["--go"],
                "ready_pattern": "^ready$",
                "busy_pattern": "working",
                "clear_command": "/new",
            },
        })
        profile = al._resolve_cli_profile({
            "agent_cli": "mycli",
            "agent_cli_options": {"model": "m1", "extra_args": ["-x"]},
        })
        self.assertEqual(profile.argv, ["mycli", "chat", "--go", "--model", "m1", "-x"])
        self.assertEqual(profile.name, "mycli")
        self.assertFalse(profile.is_legacy)
        self.assertEqual(profile.clear_command, "/new")
        self.assertEqual(profile.classify("working..."), "busy")
        self.assertEqual(profile.classify("ready"), "idle")
        self.assertEqual(profile.rewrite_slash("/compact"), "$compact")

    def test_unknown_cli_fails_explicitly(self):
        with self.assertRaises(al.CliProfileError):
            al._resolve_cli_profile({"agent_cli": "no-such-cli-xyz"})

    def test_definition_without_interactive_fails(self):
        self._write("headlessonly", {"command": ["h", "run"]})
        with self.assertRaises(al.CliProfileError):
            al._resolve_cli_profile({"agent_cli": "headlessonly"})

    def test_soft_init_falls_back_to_legacy_with_warning(self):
        before = al._CLI_PROFILE
        result = al._init_cli_profile_from_config({"agent_cli": "no-such-cli-xyz"}, strict=False)
        self.assertIsNone(result)
        self.assertIs(al._CLI_PROFILE, before)  # 失敗時はプロファイルを差し替えない

    def test_strict_init_raises(self):
        with self.assertRaises(al.CliProfileError):
            al._init_cli_profile_from_config({"agent_cli": "no-such-cli-xyz"}, strict=True)


if __name__ == "__main__":
    unittest.main()
