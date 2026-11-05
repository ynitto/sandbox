"""dags/ の例の Dagu のワークフローを、Dagu と同じ順で本物の change-watch に流して動くことを確かめる。

例の DAG は手でコピーして使うので、引数・ステップの順・出力の受け渡し（${DELIVERY}・${INPUT}・${handle.exit_code}）が
いまの change-watch と食い違っていれば、ここで落ちる。"""
from __future__ import annotations

import json
import sys
import unittest

import dagrun
from helpers import FAKE_DAGU, ROOT, Env

DAGS = ROOT / "dags"
CHANGE_WATCH = ROOT / "change_watch.py"


def setUpModule():
    try:
        import yaml  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("例の DAG を読むのに PyYAML が要る")


class DagExampleTest(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.log = self.env.tmp / "handled.jsonl"

    def write(self, rules: dict, **extra):
        self.env.write_config({"items": self.env.source("/all")}, rules, **extra)

    def run_dag(self, name: str, handle=None) -> dict:
        return dagrun.run(DAGS / name, aw=[sys.executable, str(CHANGE_WATCH)], head=["--config", str(self.env.config_path)],
                          paths={"/home/you/.github/connections.yaml": str(self.env.conn_file)}, handle=handle)

    def test_every_example_has_the_right_shape(self):
        names = sorted(p.name for p in DAGS.glob("change-watch-*.example.yaml"))
        self.assertEqual(names, ["change-watch-dispatch.example.yaml", "change-watch-external.example.yaml",
                                 "change-watch-handler.example.yaml", "change-watch-maintenance.example.yaml",
                                 "change-watch-rule.example.yaml", "change-watch-source.example.yaml"])
        for n in names:
            with self.subTest(n):
                dagrun.check_shape(self, dagrun.load(DAGS / n), n)

    def test_source_dispatch_and_maintenance(self):
        self.write({"r": {"source": "items", "id": "id", "watch": ["status"], "targets": ["handler"]}})
        self.env.api.put(1, status="a")
        self.assertEqual(self.run_dag("change-watch-source.example.yaml"), {"fetch": 0, "evaluate": 0, "dispatch": 0})
        self.env.api.put(1, status="b")
        self.run_dag("change-watch-source.example.yaml")
        self.assertEqual([r["dag"] for r in self.env.enqueued()], ["handler"])
        self.assertEqual(self.run_dag("change-watch-dispatch.example.yaml"), {"evaluate": 0, "dispatch": 0, "reconcile": 0})
        self.assertEqual(self.run_dag("change-watch-maintenance.example.yaml"), {"gc": 0})

    def test_rule_runs_command_in_place(self):
        handler = self.env.tmp / "h.py"
        handler.write_text("import os, sys\nopen(sys.argv[1], 'a').write(os.environ['CHANGEWATCH_RECORD_ID'] + '\\n')\n",
                           encoding="utf-8")
        self.write({"triage": {"source": "items", "id": "id", "watch": ["status"], "initial": "emit",
                               "targets": [{"name": "job", "command": [sys.executable, str(handler), str(self.log)]}]}})
        self.env.api.put(1, status="a")
        self.assertEqual(self.run_dag("change-watch-rule.example.yaml"), {"fetch": 0, "evaluate": 0, "work": 0})
        self.assertEqual(self.log.read_text(encoding="utf-8").split(), ["1"])

    def test_external_claims_renders_handles_and_finishes(self):
        tpl = self.env.tmp / "prompt.md"
        tpl.write_text("レコード {{ event.record_id }} の状態は {{ event.after.status }}", encoding="utf-8")
        self.write({"review": {"source": "items", "id": "id", "watch": ["status"], "initial": "emit",
                               "targets": [{"name": "agent", "external": True, "input_template": str(tpl),
                                            "retry_backoff_sec": 0}]}})
        self.env.api.put(1, status="a")
        seen: list[str] = []
        exit_codes = [1, 0]                                     # 1 回目は失敗し、2 回目で通る

        def handle(step, vars):
            self.assertEqual(step["action"], "harness.run")
            self.assertEqual(step["with"]["stdin"], "${INPUT}")
            seen.append(vars["INPUT"])
            return exit_codes.pop(0)

        r = self.run_dag("change-watch-external.example.yaml", handle)
        self.assertEqual(r["handle"], 1)
        self.assertEqual(r["finish"], 0)                        # 失敗も finish で記録する
        self.assertEqual(seen, ["レコード 1 の状態は a"])
        self.assertEqual([d["status"] for d in self.env.deliveries()], ["send_failed"])   # あとでやり直す
        self.run_dag("change-watch-external.example.yaml", handle)
        self.assertEqual([d["status"] for d in self.env.deliveries()], ["done"])
        r = self.run_dag("change-watch-external.example.yaml", handle)
        self.assertEqual((r["render"], r["handle"], r["finish"]), (None, None, None))   # 取れるものが無ければ何もしない


if __name__ == "__main__":
    unittest.main()
