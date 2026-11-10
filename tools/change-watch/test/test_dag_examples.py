"""dags/ の例の Dagu のワークフローを、Dagu と同じ順で本物の change-watch に流して動くことを確かめる。

例の DAG は手でコピーして使うので、引数・ステップの順・出力の受け渡し（${CHANGE}）が
いまの change-watch と食い違っていれば、ここで落ちる。"""
from __future__ import annotations

import sys
import unittest

import dagrun
from helpers import ROOT, Env

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

    def run_dag(self, name: str, handle=None) -> dict:
        return dagrun.run(DAGS / name, aw=[sys.executable, str(CHANGE_WATCH)], head=["--config", str(self.env.config_path)],
                          paths={"/home/you/.github/connections.yaml": str(self.env.conn_file)}, handle=handle)

    def test_every_example_has_the_right_shape(self):
        names = sorted(p.name for p in DAGS.glob("change-watch-*.example.yaml"))
        self.assertEqual(names, ["change-watch-get.example.yaml", "change-watch-maintenance.example.yaml"])
        for n in names:
            with self.subTest(n):
                dagrun.check_shape(self, dagrun.load(DAGS / n), n)

    def test_get_branches_in_dagu(self):
        """change-watch-get: 条件は Dagu が判定し、change-watch は get / call / commit だけ。"""
        handler = self.env.tmp / "h.py"
        handler.write_text("import json, sys\nc = json.loads(sys.argv[2])\n"
                           "open(sys.argv[1], 'a').write(c['key'] + ' ' + c['record']['status'] + '\\n')\n", encoding="utf-8")
        self.env.write_config({"items": self.env.source("/all", key="id")})
        self.env.api.put(1, status="open")

        import yaml
        dag = yaml.safe_load((DAGS / "change-watch-get.example.yaml").read_text(encoding="utf-8"))
        for s in dag["steps"]:
            if s["with"]["command"] == "python3":
                s["with"]["command"] = sys.executable
                s["with"]["args"] = [str(handler), str(self.log)] + s["with"]["args"][1:]
        path = self.env.tmp / "change-watch-get.yaml"
        path.write_text(yaml.safe_dump(dag, allow_unicode=True), encoding="utf-8")

        def run():
            return dagrun.run(path, aw=[sys.executable, str(CHANGE_WATCH)], head=["--config", str(self.env.config_path)],
                              paths={"/home/you/.github/connections.yaml": str(self.env.conn_file)})
        self.assertEqual(run(), {"get": 0, "urgent": None, "normal": None, "archive": None, "commit": None})  # 初回は覚えるだけ
        self.env.api.put(2, status="urgent")
        self.env.api.put(3, status="open")
        self.assertEqual(run(), {"get": 0, "urgent": 0, "normal": None, "archive": None, "commit": 0})
        self.assertEqual(run(), {"get": 0, "urgent": None, "normal": 0, "archive": None, "commit": 0})
        self.env.api.put(1, status="done")
        self.assertEqual(run(), {"get": 0, "urgent": None, "normal": None, "archive": 0, "commit": 0})
        self.assertEqual(self.env.api.items[1]["status"], "archived")                 # call で書き込んだ
        self.assertEqual(self.log.read_text(encoding="utf-8").split("\n")[:2], ["2 urgent", "3 open"])
        run()                                                                        # 書き込んだ分（archived）を見終える
        self.assertEqual(run()["commit"], None)

    def test_maintenance(self):
        self.env.write_config({"items": self.env.source("/all")})
        self.assertEqual(self.run_dag("change-watch-maintenance.example.yaml"), {"gc": 0})


if __name__ == "__main__":
    unittest.main()
