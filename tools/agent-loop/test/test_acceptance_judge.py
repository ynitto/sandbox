#!/usr/bin/env python3
"""判定層: パスを含まない受入条件を検証エージェントに判定させる（opt-in・fail-closed）。"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class AcceptanceProseTests(unittest.TestCase):
    """機械層が触れる基準と触れない基準の切り分け。"""

    def test_path_criteria_are_left_to_the_machine_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                al.acceptance_prose(["`reports/digest.md` が更新されている"], tmp), [])

    def test_prose_criteria_are_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                al.acceptance_prose(["レポートに前週比が含まれている"], tmp),
                ["レポートに前週比が含まれている"])

    def test_command_names_are_not_paths(self):
        """地の文のコマンド名（`agent-audit`）はパスではないので判定層へ回る。"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                al.acceptance_prose(["`agent-audit` の出力に警告が無い"], tmp),
                ["`agent-audit` の出力に警告が無い"])

    def test_mixed_criteria_split_by_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            criteria = ["`a/b.md` がある", "文章が日本語である"]
            self.assertEqual(al.acceptance_prose(criteria, tmp), ["文章が日本語である"])
            self.assertEqual(len(al.acceptance_paths(criteria, tmp)), 1)


def _agent(stdout="", status=0, error="", variant=None):
    mod = types.SimpleNamespace(
        headless_cmd=lambda spec, model, prompt, **kw: {
            "argv": ["judge-cli", "--json"], "env": {}, "stdin": prompt,
            "output_file": None, "timeout": 30},
        resolve_variant=lambda name, purpose, cwd=None: variant,
        load_cli=lambda name, project_dir=None: {"name": name},
        AgentCliError=RuntimeError,
    )
    return {"cli": "worker", "spec": {}, "model": None, "agentcli": mod}


class JudgeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = str(Path(self.tmp) / "log.jsonl")

    def _run(self, criteria, exec_result, variant=None):
        with mock.patch.object(al, "_tl_exec_argv", return_value=exec_result):
            return al.judge_acceptance(criteria, cwd=self.tmp, agent=_agent(variant=variant),
                                       log_file=self.log, output="やりました", files=[])

    def _ok(self, stdout):
        return {"status": 0, "stdout": stdout, "stderr": "", "error": ""}

    def test_all_pass_returns_no_errors(self):
        out = json.dumps({"verdicts": [{"n": 1, "pass": True, "reason": "確認した"}]})
        self.assertEqual(self._run(["前週比がある"], self._ok(out)), [])

    def test_fail_verdict_carries_the_reason(self):
        out = json.dumps({"verdicts": [{"n": 1, "pass": False, "reason": "前週比が無い"}]})
        errors = self._run(["前週比がある"], self._ok(out))
        self.assertEqual(len(errors), 1)
        self.assertIn("前週比が無い", errors[0])

    def test_missing_verdict_is_a_failure(self):
        """基準 2 件に判定 1 件しか返らなければ、残りは fail。黙って通さない。"""
        out = json.dumps({"verdicts": [{"n": 1, "pass": True}]})
        errors = self._run(["A である", "B である"], self._ok(out))
        self.assertEqual(len(errors), 1)
        self.assertIn("判定されなかった受入条件", errors[0])

    def test_unparseable_output_is_a_failure(self):
        errors = self._run(["A である"], self._ok("たぶん大丈夫です"))
        self.assertEqual(len(errors), 1)
        self.assertIn("読めませんでした", errors[0])

    def test_nonzero_exit_is_a_failure(self):
        errors = self._run(["A である"],
                           {"status": 1, "stdout": "", "stderr": "no model", "error": ""})
        self.assertEqual(len(errors), 1)
        self.assertIn("判定できませんでした", errors[0])

    def test_no_criteria_is_a_noop(self):
        with mock.patch.object(al, "_tl_exec_argv") as ex:
            self.assertEqual(al.judge_acceptance([], cwd=self.tmp, agent=_agent(),
                                                 log_file=self.log, output="", files=[]), [])
        ex.assert_not_called()

    def test_launch_failure_is_a_failure(self):
        agent = _agent()

        def _boom(*a, **kw):
            raise RuntimeError("定義が壊れています")

        agent["agentcli"].headless_cmd = _boom
        errors = al.judge_acceptance(["A である"], cwd=self.tmp, agent=agent,
                                     log_file=self.log, output="", files=[])
        self.assertEqual(len(errors), 1)
        self.assertIn("起動できませんでした", errors[0])


class JudgeAgentSelectionTests(unittest.TestCase):
    def test_verify_variant_is_used_when_declared(self):
        """作業した当人に採点させない。どのモデルで検証するかは定義側が決める。"""
        agent = _agent(variant={"agent_cli": "ollama-verify", "model": "gemma4:12b"})
        with mock.patch.object(al, "_tl_resolve_agent",
                               return_value={"cli": "ollama-verify"}) as resolve:
            judge = al._tl_judge_agent(agent, "/tmp")
        self.assertEqual(judge["cli"], "ollama-verify")
        resolve.assert_called_once_with("ollama-verify", "gemma4:12b", "/tmp")

    def test_no_variant_keeps_the_worker(self):
        agent = _agent(variant=None)
        self.assertIs(al._tl_judge_agent(agent, "/tmp"), agent)

    def test_broken_variant_declaration_does_not_kill_the_run(self):
        agent = _agent()
        agent["agentcli"].resolve_variant = mock.Mock(side_effect=AttributeError("x"))
        self.assertIs(al._tl_judge_agent(agent, "/tmp"), agent)


class ApplyJudgeTests(unittest.TestCase):
    """opt-in の境界と、`verified` へのつながり。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _apply(self, criteria, judge, errors=()):
        with mock.patch.object(al, "judge_acceptance", return_value=list(errors)) as j:
            out = al._tl_apply_judge(criteria, cwd=self.tmp, agent=_agent(),
                                     log_file="/dev/null", output="", files=[], judge=judge)
        return out, j

    def test_off_by_default(self):
        out, j = self._apply(["前週比がある"], judge=False)
        self.assertEqual(out, {"errors": [], "judged": False})
        j.assert_not_called()

    def test_no_prose_criteria_is_not_counted_as_judged(self):
        """全部パス付きなら判定する対象が無い。何もしていないことを「判定した」と書かない。"""
        out, j = self._apply(["`a/b.md` がある"], judge=True)
        self.assertFalse(out["judged"])
        j.assert_not_called()

    def test_enabled_with_prose_calls_the_judge(self):
        out, j = self._apply(["前週比がある"], judge=True)
        self.assertTrue(out["judged"])
        j.assert_called_once()

    def test_errors_are_passed_through(self):
        out, _ = self._apply(["前週比がある"], judge=True, errors=["だめ"])
        self.assertEqual(out["errors"], ["だめ"])


class VerifiedByTests(unittest.TestCase):
    """機械照合と自然文判定を同じ `verified: true` に潰さない。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_machine_only(self):
        self.assertEqual(al._tl_verified_by(["`a/b.md` がある"], self.tmp, False), "machine")

    def test_judge_only(self):
        self.assertEqual(al._tl_verified_by(["前週比がある"], self.tmp, True), "judge")

    def test_both(self):
        self.assertEqual(
            al._tl_verified_by(["`a/b.md` がある", "前週比がある"], self.tmp, True),
            "machine+judge")

    def test_neither(self):
        self.assertEqual(al._tl_verified_by(["前週比がある"], self.tmp, False), "")


class SchedulerOptInTests(unittest.TestCase):
    def _scheduler(self, tool_config):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._tool_config = dict(tool_config)
        return s

    def test_default_off(self):
        s = self._scheduler({})
        self.assertFalse(s._acceptance_judge_enabled({"acceptance_judge": None}))

    def test_top_level_on(self):
        s = self._scheduler({"acceptance_judge": True})
        self.assertTrue(s._acceptance_judge_enabled({"acceptance_judge": None}))

    def test_entry_overrides_the_top_level(self):
        s = self._scheduler({"acceptance_judge": True})
        self.assertFalse(s._acceptance_judge_enabled({"acceptance_judge": False}))
        s = self._scheduler({})
        self.assertTrue(s._acceptance_judge_enabled({"acceptance_judge": True}))


class EntryNormalizationTests(unittest.TestCase):
    def _one(self, **extra):
        return al.validate_entries([{
            "name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True, **extra,
        }])[0]

    def test_non_boolean_is_rejected_at_startup(self):
        """壊れた宣言は起動で落とす（他の entry 検証と同じ fail fast）。"""
        with self.assertRaises(ValueError):
            self._one(acceptance_judge="yes")

    def test_unset_stays_none(self):
        """未指定は None のまま——トップレベルの既定に従わせるため false と区別する。"""
        self.assertIsNone(self._one()["acceptance_judge"])

    def test_explicit_false_survives(self):
        self.assertIs(self._one(acceptance_judge=False)["acceptance_judge"], False)


class RunGoalIntegrationTests(unittest.TestCase):
    """判定層が run_goal の結果まで届くこと。"""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.log = os.path.join(self.dir, "log.jsonl")
        self.real_run_agent = al._tl_run_agent
        self.addCleanup(setattr, al, "_tl_run_agent", self.real_run_agent)

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            if not readonly and files:
                for f in files:
                    Path(f).write_text("generated\n", encoding="utf-8")
                return "wrote"
            return '{"type":"write_files","paths":["out.md"]}'

        al._tl_run_agent = fake

    def _run(self, *, judge, judge_errors=()):
        with mock.patch.object(al, "judge_acceptance", return_value=list(judge_errors)):
            return al.run_goal(
                goal="out.md を書く", cwd=self.dir, agent={}, log_file=self.log,
                acceptance=["`out.md` が更新されている", "本文が日本語である"],
                judge=judge)

    def test_disabled_leaves_prose_criteria_unjudged(self):
        """既定の挙動は変わらない——自然文の基準は誰も見ないまま ok になる。"""
        result = self._run(judge=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verifiedBy"], "machine")

    def test_enabled_and_passing_records_both_layers(self):
        result = self._run(judge=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verifiedBy"], "machine+judge")

    def test_failed_judgment_fails_the_run(self):
        """機械層が pass でも、判定層が落ちたら done の根拠にしない。"""
        result = self._run(judge=True, judge_errors=["受入条件を満たしていません: 本文が日本語である"])
        self.assertFalse(result["ok"])
        self.assertIn("本文が日本語である", " ".join(result["evidenceErrors"]))


if __name__ == "__main__":
    unittest.main()
