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
# ハーネスの実装は agentcore（agent-herd と共有する 1 実装）。agent_loop は
# 委譲するだけなので、差し替えも参照もそちらへ向ける。sys.path は
# agent_loop の import が通してくれる。
from agentcore.harness import toolloop as tl  # noqa: E402
from agentcore.tests.harnesspatch import patch_harness  # noqa: E402


def _agent(autonomy, slash_native=None):
    """ローダ（`agentcli.normalize`）が返すのと同じ形の spec を持つエージェント。

    `slash_native` は未宣言なら `headless_autonomy` から導く——ローダと同じ規則で、
    以前この判定がその代理で書かれていたことの後方互換（設計 2026-08-27 §3.2）。
    """
    if slash_native is None:
        slash_native = autonomy == "tool-loop"
    return {"cli": "x", "model": "",
            "spec": {"headless_autonomy": autonomy, "slash_native": slash_native,
                     "skill_command_prefix": "/"}}


class RunPromptSlashTests(unittest.TestCase):
    def test_tool_loop_cli_gets_native_slash_lines(self):
        with patch_harness("run_cli_loop", return_value={"ok": True}) as run:
            tl.run_prompt(goal="本文", cwd="/tmp", agent=_agent("tool-loop"),
                          log_file="/tmp/x.jsonl", slash=["summarize-logs", "report --lang ja"])
        goal = run.call_args.kwargs["goal"]
        self.assertTrue(goal.startswith("/summarize-logs\n/report --lang ja\n\n本文"))

    def test_single_shot_cli_gets_skills_and_note(self):
        with patch_harness("run_goal", return_value={"ok": True}) as run:
            tl.run_prompt(goal="本文", cwd="/tmp", agent=_agent("single-shot"),
                          log_file="/tmp/x.jsonl", slash=["tech-harvester ニュースをまとめて"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["skills"], ["tech-harvester"])
        self.assertIn("`tech-harvester` スキルの手順に従って実行してください。", kwargs["goal"])
        self.assertIn("引数: ニュースをまとめて", kwargs["goal"])
        self.assertIn("本文", kwargs["goal"])

    def test_the_declaration_decides_the_line_not_the_layer(self):
        """コマンド行を渡すか消費するかは `slash_native`、runner は `headless_autonomy`。

        以前は `headless_autonomy == "tool-loop"` の 1 点が両方を決めていたので、
        「自分でツールを回せるが、スラッシュは解釈しない」CLI を言い表せなかった
        （設計 2026-08-27 §3.2）。runner の選択は層のまま、行の扱いだけが宣言で変わる。
        """
        with patch_harness("run_cli_loop", return_value={"ok": True}) as run:
            tl.run_prompt(goal="本文", cwd="/tmp",
                          agent=_agent("tool-loop", slash_native=False),
                          log_file="/tmp/x.jsonl", slash=["tech-harvester ニュース"])
        goal = run.call_args.kwargs["goal"]        # runner は層のとおり tool-loop 側
        self.assertNotIn("/tech-harvester", goal)  # 行は消費された（残して渡していない）
        self.assertIn("`tech-harvester` スキルの手順に従って実行してください。", goal)

    def test_single_shot_with_a_native_slash_keeps_the_line(self):
        """逆向きも言える: 層3 でもネイティブのスラッシュを持つなら行を残して渡す。"""
        with patch_harness("run_goal", return_value={"ok": True}) as run:
            tl.run_prompt(goal="本文", cwd="/tmp",
                          agent=_agent("single-shot", slash_native=True),
                          log_file="/tmp/x.jsonl", slash=["compact"])
        self.assertTrue(run.call_args.kwargs["goal"].startswith("/compact\n\n本文"))
        self.assertEqual(run.call_args.kwargs["skills"], [])

    def test_native_prefix_comes_from_the_definition(self):
        """行頭記号も定義のもの（codex は `$`）。ヘッドレスだけ `/` 固定だった。"""
        agent = _agent("tool-loop")
        agent["spec"]["skill_command_prefix"] = "$"
        with patch_harness("run_cli_loop", return_value={"ok": True}) as run:
            tl.run_prompt(goal="本文", cwd="/tmp", agent=agent,
                          log_file="/tmp/x.jsonl", slash=["compact"])
        self.assertTrue(run.call_args.kwargs["goal"].startswith("$compact\n\n本文"))

    def test_no_slash_keeps_goal_unchanged(self):
        with patch_harness("run_goal", return_value={"ok": True}) as run:
            tl.run_prompt(goal="本文", cwd="/tmp", agent=_agent("single-shot"),
                          log_file="/tmp/x.jsonl")
        self.assertEqual(run.call_args.kwargs["goal"], "本文")
        self.assertEqual(run.call_args.kwargs["skills"], [])


class RunGoalSkillResolutionTests(unittest.TestCase):
    def test_declared_missing_skill_fails_with_search_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(tl.ToolLoopError) as ctx:
                tl.run_goal(goal="g", cwd=tmp, agent=_agent("single-shot"),
                            log_file=os.path.join(tmp, "x.jsonl"),
                            skills=["no-such-skill"])
        message = str(ctx.exception)
        self.assertIn("スキルが見つかりません: no-such-skill", message)
        self.assertIn("探索先:", message)
        self.assertIn("install.py", message)

    def test_goal_mentioned_missing_skill_stays_lenient(self):
        # 本文の `名前` スキル表記は推測なので、見つからなくても実行は続ける（従来どおり）。
        with tempfile.TemporaryDirectory() as tmp:
            with patch_harness("_tl_run_control",
                               return_value='{"type":"final","output":"done"}'):
                result = tl.run_goal(goal="`no-such-skill` スキルで処理して", cwd=tmp,
                                     agent=_agent("single-shot"),
                                     log_file=os.path.join(tmp, "x.jsonl"))
        self.assertTrue(result["ok"] or "output" in result)

    def test_declared_skill_resolves_from_project_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp, ".github", "skills", "myskill")
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# myskill\n手順。\n", encoding="utf-8")
            with patch_harness("_tl_run_control",
                               return_value='{"type":"final","output":"done"}') as run:
                tl.run_goal(goal="g", cwd=tmp, agent=_agent("single-shot"),
                            log_file=os.path.join(tmp, "x.jsonl"), skills=["myskill"])
            # 解決した SKILL.md は読み取り材料としてモデルへ渡る
            read_files = run.call_args.kwargs["read_files"]
            self.assertTrue(any(str(f).endswith(os.path.join("myskill", "SKILL.md"))
                                for f in read_files))


class ActionSkillNameTests(unittest.TestCase):
    def test_bare_mention_without_backticks_is_picked_up(self):
        # 「wiki-useスキルを使って」の素の表記。拾わないとモデルはスキル名を
        # コマンドとして実行し「PATH 上に実行ファイルがありません」の却下を繰り返す（実測）。
        self.assertEqual(tl._tl_action_skill_names("wiki-useスキルを使って取り込んで"),
                         ["wiki-use"])

    def test_backticked_mention_still_works(self):
        self.assertEqual(tl._tl_action_skill_names("`tech-harvester` スキルの手順で"),
                         ["tech-harvester"])


class SkillNameAsCommandTests(unittest.TestCase):
    def test_reject_names_the_skill_and_its_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "wiki-use")
            (root / "scripts").mkdir(parents=True)
            (root / "scripts" / "ingest.py").write_text("", encoding="utf-8")
            with self.assertRaises(tl.ToolLoopError) as ctx:
                tl._tl_validate_command("wiki-use", tmp, [str(root)])
        message = str(ctx.exception)
        self.assertIn("wiki-use はスキル名であり実行ファイルではありません", message)
        self.assertIn("ingest.py", message)

    def test_unknown_command_keeps_the_path_error(self):
        with self.assertRaises(tl.ToolLoopError) as ctx:
            tl._tl_validate_command("no-such-cmd", "/tmp", [])
        self.assertIn("PATH 上に実行ファイルがありません", str(ctx.exception))


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
