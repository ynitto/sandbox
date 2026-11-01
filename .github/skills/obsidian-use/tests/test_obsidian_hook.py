"""obsidian_hook.py（出来事を控え、終わりに OBSIDIAN NOTE を出す）のテスト。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import obsidian_hook as hook  # noqa: E402
import vault  # noqa: E402


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.env = {"OBSIDIAN_USE_AGENT_HOME": str(t / "home"), "OBSIDIAN_USE_HOME": str(t / "state"),
                    "OBSIDIAN_CLI": str(t / "none")}
        self._old = {k: os.environ.get(k) for k in [*self.env, "OBSIDIAN_MEMO_OFF"]}
        os.environ.update(self.env)
        os.environ.pop("OBSIDIAN_MEMO_OFF", None)
        (t / "vault").mkdir()
        vault.init(str(t / "vault"), "llm-wiki", "Main")
        self.cwd = t / "proj"
        self.cwd.mkdir()
        self.base = {"session_id": "abc", "cwd": str(self.cwd)}

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_hook(self, event, client="claude", failed=False, **payload):
        return hook.run_hook(event, client, {**self.base, **payload}, failed)

    def test_silent_without_events(self):
        self.run_hook("start")
        self.run_hook("tool", tool_name="Read", tool_input={"file_path": "a"})
        self.assertEqual(self.run_hook("stop"), "")

    def test_failure_and_recovery_trigger_block_once(self):
        self.run_hook("tool", failed=True, tool_name="Bash", tool_input={"command": "make"}, error="make: *** Error 2")
        self.run_hook("tool", tool_name="Bash", tool_input={"command": "make"})
        out = json.loads(self.run_hook("stop"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("OBSIDIAN NOTE", out["reason"])
        self.assertIn("ツールの失敗: Bash: make: *** Error 2", out["reason"])
        self.assertIn("失敗からの回復: Bash", out["reason"])
        self.assertIn("memo --session abc", out["reason"])
        self.assertEqual(self.run_hook("stop", stop_hook_active=True), "")
        self.assertEqual(self.run_hook("stop"), "")   # 新しい出来事が無ければ出さない

    def test_own_commands_are_not_events(self):
        self.run_hook("tool", failed=True, tool_name="Bash",
                      tool_input={"command": 'python "x/vault.py" memo --name a'}, error="NG")
        self.assertEqual(self.run_hook("stop"), "")

    def test_prompt_words(self):
        self.run_hook("prompt", prompt="この手順をメモしておいて")
        self.assertIn("メモの依頼", self.run_hook("stop"))

    def test_work_threshold(self):
        for i in range(hook.rules()["work_tools"]):
            self.run_hook("tool", tool_name="Edit", tool_input={"n": i})
        self.assertIn("まとまった作業", self.run_hook("stop"))

    def test_kiro_v2_prefixes_next_prompt(self):
        self.run_hook("tool", client="kiro-v2", tool_name="shell",
                      tool_response={"success": False, "error": "permission denied"})
        self.assertEqual(self.run_hook("stop", client="kiro-v2"), "")
        out = self.run_hook("prompt", client="kiro-v2", prompt="次へ")
        self.assertTrue(out.startswith("（返事の前に、前のターンについて）OBSIDIAN NOTE"))
        self.assertEqual(self.run_hook("prompt", client="kiro-v2", prompt="次へ"), "")

    def test_start_lists_project_notes_and_copilot_shape(self):
        cfg = vault.load_config()
        vault.write_note(cfg, "project", "テストは make check", "- 内容: make check で全部回る", cwd=str(self.cwd))
        out = self.run_hook("start", client="copilot", sessionId="c1")
        ctx = json.loads(out)["additionalContext"]
        self.assertIn("[[テストは make check]]", ctx)
        self.assertIn(f"このプロジェクト（{self.cwd.name}）", ctx)

    def test_note_limit_stops_prompts(self):
        sess = hook.load_session("abc", "claude", str(self.cwd))
        sess["notes"] = hook.rules()["max_notes"]
        hook.save_session("abc", sess)
        self.run_hook("prompt", prompt="メモして")
        self.assertEqual(self.run_hook("stop"), "")

    def test_ignore_file_and_off_switch(self):
        (self.cwd / ".obsidian-memo-ignore").write_text("", encoding="utf-8")
        self.run_hook("prompt", prompt="メモして")
        self.assertEqual(self.run_hook("stop"), "")
        os.environ["OBSIDIAN_MEMO_OFF"] = "1"
        self.assertEqual(self.run_hook("start"), "")

    def test_unconfigured_is_silent(self):
        os.environ["OBSIDIAN_USE_AGENT_HOME"] = str(Path(self.tmp.name) / "empty")
        self.assertEqual(self.run_hook("start"), "")

    def test_main_never_fails(self):
        import io
        sys_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("{not json")
            self.assertEqual(hook.main(["stop", "--client", "claude"]), 0)
        finally:
            sys.stdin = sys_stdin


if __name__ == "__main__":
    unittest.main()
