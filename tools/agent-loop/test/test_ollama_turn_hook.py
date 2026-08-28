#!/usr/bin/env python3
"""自前 CLI（ollama）のターン完了をネイティブイベントで受ける（設計 §7.3 A / 段 10）。

完了検知は「画面を見る」より「本人が言う」ほうが確かである。クラウド CLI は自分の
プラグイン機構で `hook-event` を呼んでいたが、`ollama` は**前面が我々のもの**なので
資産（プラグイン・設定ファイル）が要らない——TUI がターンの終わりに同じコマンドを
叩けばよい。これで `busy_pattern`（画面から「経過 N 秒」を読む）は fallback に降りる。

ここが見るのは 3 つ。①資産なしで launch が組めること、②封筒の検証が他の adapter と
同じに効くこと、③定義が申告していること。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class OllamaNeedsNoHookAssetTests(unittest.TestCase):
    def test_the_launch_only_adds_env(self):
        """資産のコピーも argv の書き換えもしない（前面が我々のものなので要らない）。"""
        with tempfile.TemporaryDirectory() as tmp:
            argv, env, cleanup = al.prepare_turn_hook_launch(
                adapter="ollama",
                argv=["agent-herd", "ollama", "--tui"],
                env={"HOME": "/real/home", "EXISTING": "yes"},
                instance_id="instance-1",
                hook_token="secret",
                executable="/opt/bin/agent-loop",
                assets_dir=Path(tmp) / "no-assets",
                runtime_root=Path(tmp),
            )
        self.assertEqual(argv, ["agent-herd", "ollama", "--tui"], "argv は素通し")
        self.assertIsNone(cleanup, "後始末する一時物を作らない")
        self.assertEqual(env["HOME"], "/real/home")
        self.assertEqual(env["EXISTING"], "yes")
        self.assertEqual(env["AGENT_LOOP_AGENT_CLI"], "ollama")
        self.assertEqual(env["AGENT_LOOP_HOOK_TOKEN"], "secret")
        self.assertEqual(env["AGENT_LOOP_EXECUTABLE"], "/opt/bin/agent-loop")

    def test_doctor_says_no_asset_is_required(self):
        findings = al._check_turn_hook_assets("ollama", Path("/nonexistent"))
        self.assertEqual([f["id"] for f in findings], ["turn_hook.assets_not_required"])


class TheEnvelopeIsVerifiedTheSameWayTests(unittest.TestCase):
    """HMAC・instance・pane・generation の検証は adapter 共通の 1 実装。"""

    def _managed(self, tmp, **overrides):
        active = {"instance_id": "i1", "pane_id": "%1", "agent_cli": "ollama",
                  "hook_token": "tok", "dispatch_id": "d1", "generation": 3}
        active.update(overrides)
        root = Path(tmp) / "i1" / "active"
        root.mkdir(parents=True, exist_ok=True)
        (root / "%1.json").write_text(json.dumps(active), encoding="utf-8")
        return {"AGENT_LOOP_INSTANCE_ID": "i1", "TMUX_PANE": "%1",
                "AGENT_LOOP_HOOK_TOKEN": "tok", "AGENT_LOOP_AGENT_CLI": "ollama"}

    def test_a_complete_event_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._managed(tmp)
            with mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
                    mock.patch.dict(os.environ, env, clear=False):
                self.assertTrue(al.record_turn_hook_event(
                    adapter="ollama", status="complete", native_event="turn_end"))
            events = list((Path(tmp) / "i1" / "events").glob("*.json"))
            self.assertEqual(len(events), 1)
            got = json.loads(events[0].read_text(encoding="utf-8"))
        self.assertEqual((got["adapter"], got["status"]), ("ollama", "complete"))
        self.assertEqual((got["dispatch_id"], got["generation"]), ("d1", 3))
        self.assertEqual(got["native_event"], "turn_end")

    def test_a_wrong_token_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._managed(tmp)
            env["AGENT_LOOP_HOOK_TOKEN"] = "wrong"
            with mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
                    mock.patch.dict(os.environ, env, clear=False):
                self.assertFalse(al.record_turn_hook_event(
                    adapter="ollama", status="complete"))

    def test_another_adapter_cannot_speak_for_this_pane(self):
        """ペインが ollama なら、他の adapter を名乗る hook は通らない。"""
        with tempfile.TemporaryDirectory() as tmp:
            env = self._managed(tmp)
            with mock.patch.object(al, "_TURN_HOOKS_DIR", Path(tmp)), \
                    mock.patch.dict(os.environ, env, clear=False):
                self.assertFalse(al.record_turn_hook_event(
                    adapter="claude", status="complete"))


class TheDefinitionDeclaresItTests(unittest.TestCase):
    def test_ollama_declares_the_adapter_so_the_screen_becomes_fallback(self):
        from agentcore import agentcli
        spec = agentcli.load_cli("ollama")
        self.assertEqual(spec["interactive"]["turn_completion"], "ollama")
        # 画面判定は残す（hook が届かない環境の fallback）。
        self.assertTrue(spec["interactive"].get("busy_pattern"))

    def test_the_loader_refuses_an_unknown_adapter(self):
        from agentcore import agentcli
        with self.assertRaises(agentcli.AgentCliError):
            agentcli.normalize("x", {"command": ["x"], "interactive": {
                "command": ["x"], "turn_completion": "nope"}}, "<test>")


if __name__ == "__main__":
    unittest.main()
