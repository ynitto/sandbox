#!/usr/bin/env python3
"""agent-loop → ハーネスの**委譲**が繋がっていること（継ぎ目のテスト）。

ハーネスの中身（限定ツール契約・ステートマシン）は agentcore 側の実装であり、その振る舞いは
`agentcore/tests/test_harness_*.py` が見る。ここが見るのは agent-loop との継ぎ目だけ:

1. 共有名前空間へ `_tl_*` / `_sm_*` を張り直していないこと——張ると
   `mock.patch.object(agent_loop, "_tl_run_agent")` が「成功したのに効かない」静かな失敗になる
2. `run` / `statemachine` サブコマンドがハーネスのモジュールへ落ちること
3. 記帳と control 解決のフックが agent-loop の実装に繋がっており、**呼び出し時に**引かれること
4. その結果として、headless で回した CLI の実測トークンが agent-loop の台帳へ着くこと
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402
from agentcore import agentcli  # noqa: E402
from agentcore.harness import _borrowed  # noqa: E402
from agentcore.harness import statemachine as sm  # noqa: E402
from agentcore.harness import toolloop as tl  # noqa: E402


class NoAliasesTest(unittest.TestCase):
    """委譲層は名前を張り直さない（静かに効かないパッチを作らないため）。"""

    def test_the_harness_names_are_not_re_exported(self):
        for name in ("_tl_run_agent", "_sm_run_agent", "run_prompt", "run_goal",
                     "run_statemachine", "cmd_run", "cmd_statemachine", "ToolLoopError"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(al, name),
                                 f"agent_loop が {name} を張り直している。"
                                 "差し替えは agentcore.harness 側へ当てること")

    def test_the_delegation_layer_holds_the_modules(self):
        self.assertIs(al._harness_toolloop, tl)
        self.assertIs(al._harness_statemachine, sm)


class SubcommandDispatchTest(unittest.TestCase):
    """`agent-loop run` / `statemachine` の実体はハーネスの cmd_*。"""

    def _main(self, argv, target, module):
        with mock.patch.object(sys, "argv", ["agent-loop"] + argv), \
                mock.patch.object(module, target) as cmd:
            al.main()
        self.assertEqual(cmd.call_count, 1, f"{target} が呼ばれていない")
        return cmd.call_args

    def test_run_goes_to_the_harness(self):
        self._main(["run", "やって"], "cmd_run", tl)

    def test_statemachine_goes_to_the_harness(self):
        self._main(["statemachine", "--workflow", "x.yaml"], "cmd_statemachine", sm)


class HookWiringTest(unittest.TestCase):
    """継ぎ目のフックは agent-loop の実装へ繋がり、**呼ぶたびに**引かれる。"""

    def test_the_hooks_are_not_the_agentcore_defaults(self):
        self.assertIsNot(_borrowed.node_budget_record, _borrowed._noop_budget_record)
        self.assertIsNot(_borrowed.control_policy_decision, _borrowed._no_control_policy)

    def test_the_ledger_hook_is_looked_up_at_call_time(self):
        """関数オブジェクトを渡していると、ここで差し替えても届かない。"""
        seen = []
        with mock.patch.object(al, "_node_budget_record",
                               side_effect=lambda *a, **kw: seen.append((a, kw))):
            _borrowed.node_budget_record(1.5, agent_cli="fake")
        self.assertEqual(len(seen), 1, "記帳フックが agent-loop の実装を引いていない")

    def test_the_control_hook_is_looked_up_at_call_time(self):
        with mock.patch.object(al, "_control_policy_decision",
                               return_value={"cli": "fake"}):
            self.assertEqual(_borrowed.control_policy_decision(""), {"cli": "fake"})


class UsageLedgerTest(unittest.TestCase):
    """headless で呼んだ CLI の実測トークン（`@agent-usage`）をノード予算の台帳へ記帳する。

    ここが抜けると、aider / ollama のように実測を出す CLI を回しても台帳は空のままで、
    モデルの格付け（C9）を推定でしか語れなくなる。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-usage-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._budget = tempfile.TemporaryDirectory(prefix="agent-loop-budget-")
        self.addCleanup(self._budget.cleanup)
        os.environ["AGENT_BUDGET_DIR"] = self._budget.name
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        self.log_file = os.path.join(self.repo, "run.jsonl")

    def _agent(self, stderr_text):
        fake = pathlib.Path(self.repo, "fake-cli.py")
        fake.write_text("import sys\nprint('OK')\nsys.stderr.write({!r})\n".format(stderr_text),
                        encoding="utf-8")
        spec = agentcli.normalize("fake-usage", {
            "name": "fake-usage",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "timeout": 10,
        }, pathlib.Path(self.repo, "fake-usage.json"))
        return {"cli": "fake-usage", "spec": spec, "model": "m", "agentcli": agentcli}

    def _run(self, stderr_text):
        return tl._tl_run_agent(self._agent(stderr_text), "やって", cwd=self.repo,
                                readonly=False, read_files=[], files=[], log_file=self.log_file)

    def _ledger_rows(self):
        led = pathlib.Path(self._budget.name, "ledger")
        return [json.loads(line) for path in sorted(led.glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                ] if led.is_dir() else []

    def test_measured_usage_is_recorded_with_cli_attribution(self):
        self.assertEqual(self._run("@agent-usage tokens_in=12 tokens_out=34\n"), "OK")
        rows = self._ledger_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["tokens_in"], rows[0]["tokens_out"]), (12.0, 34.0))
        self.assertEqual((rows[0]["agent_cli"], rows[0]["model"]), ("fake-usage", "m"))
        self.assertEqual(rows[0]["seconds"], 0,
                         "実行時間はスロット側で記帳済み——ここで足すと二重計上になる")
        events = [json.loads(line) for line
                  in pathlib.Path(self.log_file).read_text(encoding="utf-8").splitlines()]
        self.assertIn({"cli": "fake-usage", "tokensIn": 12, "tokensOut": 34},
                      [{k: e[k] for k in ("cli", "tokensIn", "tokensOut")}
                       for e in events if e.get("event") == "usage"])

    def test_silent_cli_is_not_filled_with_an_estimate(self):
        self.assertEqual(self._run("aider warning\n"), "OK")
        self.assertEqual(self._ledger_rows(), [])

    def test_quota_failure_is_recorded_for_collection(self):
        fake = pathlib.Path(self.repo, "fake-quota.py")
        fake.write_text(
            "import sys\nsys.stderr.write('too many requests; retry after 5 minutes\\n')\n"
            "raise SystemExit(1)\n", encoding="utf-8")
        spec = agentcli.normalize("fake-quota", {
            "name": "fake-quota",
            "command": [sys.executable, str(fake)],
            "prompt_via": "argv",
            "prompt_flag": "--message",
            "timeout": 10,
            "errors": [{
                "match": "too many requests", "class": "quota",
                "quota_kind": "rate_limit", "hint": "一時制限です",
            }],
        }, pathlib.Path(self.repo, "fake-quota.json"))
        agent = {"cli": "fake-quota", "spec": spec, "model": "m", "agentcli": agentcli}

        with self.assertRaisesRegex(tl.ToolLoopError, "一時制限です"):
            tl._tl_run_agent(agent, "やって", cwd=self.repo, readonly=False,
                             read_files=[], files=[], log_file=self.log_file)

        rows = self._ledger_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["event"], "quota")
        self.assertEqual(rows[0]["quota_kind"], "rate_limit")
        self.assertEqual(rows[0]["agent_cli"], "fake-quota")
        self.assertGreater(__import__("datetime").datetime.fromisoformat(
            rows[0]["reset_at"].replace("Z", "+00:00")).timestamp(), __import__("time").time())


if __name__ == "__main__":
    unittest.main()
