"""headless 経路の slash → スキル対応の回帰。

以前は headless（per-run）実行で entry の `slash` を黙って捨てていた（対話ペインへ
send-keys する前提の機能だったため）。aider 等の層3 でスキルを使う定期プロンプトが
「設定したのに効かない」「スキルの実体が無くても気づけない」になっていた。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


def _agent(autonomy):
    return {"cli": "x", "model": "", "spec": {"headless_autonomy": autonomy}}


class RunPromptSlashTests(unittest.TestCase):
    def test_tool_loop_cli_gets_native_slash_lines(self):
        with mock.patch.object(al, "run_cli_loop", return_value={"ok": True}) as run:
            al.run_prompt(goal="本文", cwd="/tmp", agent=_agent("tool-loop"),
                          log_file="/tmp/x.jsonl", slash=["summarize-logs", "report --lang ja"])
        goal = run.call_args.kwargs["goal"]
        self.assertTrue(goal.startswith("/summarize-logs\n/report --lang ja\n\n本文"))

    def test_single_shot_cli_gets_skills_and_note(self):
        with mock.patch.object(al, "run_goal", return_value={"ok": True}) as run:
            al.run_prompt(goal="本文", cwd="/tmp", agent=_agent("single-shot"),
                          log_file="/tmp/x.jsonl", slash=["tech-harvester ニュースをまとめて"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["skills"], ["tech-harvester"])
        self.assertIn("`tech-harvester` スキルの手順に従って実行してください。", kwargs["goal"])
        self.assertIn("引数: ニュースをまとめて", kwargs["goal"])
        self.assertIn("本文", kwargs["goal"])

    def test_no_slash_keeps_goal_unchanged(self):
        with mock.patch.object(al, "run_goal", return_value={"ok": True}) as run:
            al.run_prompt(goal="本文", cwd="/tmp", agent=_agent("single-shot"),
                          log_file="/tmp/x.jsonl")
        self.assertEqual(run.call_args.kwargs["goal"], "本文")
        self.assertEqual(run.call_args.kwargs["skills"], [])


class RunGoalSkillResolutionTests(unittest.TestCase):
    def test_declared_missing_skill_fails_with_search_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(al.ToolLoopError) as ctx:
                al.run_goal(goal="g", cwd=tmp, agent=_agent("single-shot"),
                            log_file=os.path.join(tmp, "x.jsonl"),
                            skills=["no-such-skill"])
        message = str(ctx.exception)
        self.assertIn("スキルが見つかりません: no-such-skill", message)
        self.assertIn("探索先:", message)
        self.assertIn("install.py", message)

    def test_goal_mentioned_missing_skill_stays_lenient(self):
        # 本文の `名前` スキル表記は推測なので、見つからなくても実行は続ける（従来どおり）。
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(al, "_tl_run_control",
                                   return_value='{"type":"final","output":"done"}'):
                result = al.run_goal(goal="`no-such-skill` スキルで処理して", cwd=tmp,
                                     agent=_agent("single-shot"),
                                     log_file=os.path.join(tmp, "x.jsonl"))
        self.assertTrue(result["ok"] or "output" in result)

    def test_declared_skill_resolves_from_project_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp, ".github", "skills", "myskill")
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# myskill\n手順。\n", encoding="utf-8")
            with mock.patch.object(al, "_tl_run_control",
                                   return_value='{"type":"final","output":"done"}') as run:
                al.run_goal(goal="g", cwd=tmp, agent=_agent("single-shot"),
                            log_file=os.path.join(tmp, "x.jsonl"), skills=["myskill"])
            # 解決した SKILL.md は読み取り材料としてモデルへ渡る
            read_files = run.call_args.kwargs["read_files"]
            self.assertTrue(any(str(f).endswith(os.path.join("myskill", "SKILL.md"))
                                for f in read_files))


class StartupSlashSkillCheckTests(unittest.TestCase):
    """層3 entry の slash スキルは起動時に fail fast（設定ミスを初回 dispatch まで隠さない）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        agents = Path(self.dir, "agents")
        agents.mkdir(parents=True)
        (agents / "plain.json").write_text(
            '{"command": ["plain", "run"], "headless_autonomy": "single-shot"}',
            encoding="utf-8")
        self.ctl = tempfile.mkdtemp()
        os.environ["AGENT_CONTROL_DIR"] = self.ctl
        al._CONTROL_CACHE["mtime"] = None
        al._CONTROL_CACHE["data"] = {}

    def _check(self, entry):
        return al.check_headless_entries({"agent_cli": "plain"}, [entry],
                                         project_dir=self.dir)

    def test_missing_slash_skill_is_fatal(self):
        problems = self._check({"name": "n", "prompt": "p", "slash": ["no-such-skill"]})
        self.assertTrue(any("スキルが見つかりません: no-such-skill" in p for p in problems))

    def test_existing_slash_skill_passes(self):
        skill = Path(self.dir, ".github", "skills", "myskill")
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# myskill\n", encoding="utf-8")
        problems = self._check({"name": "n", "prompt": "p", "slash": ["myskill"]})
        self.assertEqual([p for p in problems if "スキル" in p], [])


if __name__ == "__main__":
    unittest.main()
