"""Dagu の例（change-watch-teams-bridge）を、Dagu と同じ順で本物の change-watch・teams_bridge.py・copilot_app_job.py で流す。

受信フォルダの依頼は、Dagu に Windows の道（\\ を含む）を展開させず、--inbox と受信フォルダからの相対の名前
（${CHANGE.record.rel}）で渡す。依頼の名前は往路のフローと同じ tm- で始める（道に \\t が現れる形）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CHANGE_WATCH_DIR = HERE.parents[1]
sys.path.insert(0, str(CHANGE_WATCH_DIR / "test"))
import dagrun  # noqa: E402

CHANGE_WATCH = CHANGE_WATCH_DIR / "change_watch.py"
DAG = HERE / "change-watch-teams-bridge.yaml.example"
JOBS_PY = CHANGE_WATCH_DIR.parent / "copilot-app-jobs" / "copilot_app_job.py"
EXAMPLE = "/home/you"


class DagFlowTest(unittest.TestCase):
    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:                                      # pragma: no cover - CI は入れている
            self.skipTest("PyYAML が要る")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.inbox = root / "inbox"
        self.hub = root / "hub"
        self.app = root / "app"
        for folder in (self.inbox, self.hub / ".copilot-jobs", self.app):
            folder.mkdir(parents=True)
        self.state = root / "state"
        self.cfg = root / "change-watch.yaml"
        self.cfg.write_text(json.dumps({
            "version": 1, "state_dir": str(self.state),
            "sources": {"teams-requests": {"folder": {"path": str(self.inbox), "include": ["*.json"], "content": "json",
                                                      "settle_sec": 0, "hash": True}, "schedule": "* * * * *"}},
        }), encoding="utf-8")
        self.targets = self.hub / ".copilot-jobs" / "targets.json"
        self.targets.write_text(json.dumps({"default": "app", "targets": {
            "app": {"dir": str(self.app), "gitlab_project": "g/app", "mode": "issue", "description": "アプリ"}}}),
            encoding="utf-8")
        self.ledger = self.state / "submitted.json"
        self.paths = {
            f"{EXAMPLE}/.config/change-watch/teams-sharepoint-bridge-agent/change-watch.yaml": str(self.cfg),
            "teams_bridge": str(HERE / "teams_bridge.py"),
            f"{EXAMPLE}/work/hub/.copilot-jobs/targets.json": str(self.targets),
            f"{EXAMPLE}/.local/state/change-watch/teams-sharepoint-bridge-agent/submitted.json": str(self.ledger),
            f"{EXAMPLE}/.local/state/change-watch/teams-sharepoint-bridge-agent/invalid.jsonl": str(self.state / "invalid.jsonl"),
            "C:/TeamsBridge/inbox": str(self.inbox),
        }

    def request(self, request_id: str, **extra) -> None:
        body = {"request_id": request_id, "team_id": "t", "channel_id": "c", "message_id": "m-" + request_id,
                "author": "依頼者", "posted_at": "2026-11-23T12:00:00Z", "text": "画面のバグを直して", **extra}
        (self.inbox / f"{request_id}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    def run_dag(self) -> dict:
        return dagrun.run(DAG, aw=[sys.executable, str(CHANGE_WATCH)], head=[], paths=self.paths,
                          env={"TEAMS_BRIDGE_COPILOT_JOB": str(JOBS_PY)})

    def pending(self) -> "list[str]":
        return sorted(p.name for p in (self.hub / ".copilot-jobs" / "pending").glob("*.md"))

    def test_submits_each_request_once_and_moves_on(self):
        self.request("tm-1")
        self.request("tm-2", text="x" * 20001)                  # 様式違反
        first = self.run_dag()
        self.assertEqual((first["check"], first["submit"], first["commit"]), (0, 0, 0), first)
        self.assertEqual(len(self.pending()), 1)
        second = self.run_dag()                                  # 次の依頼（様式違反）へ進む
        self.assertEqual((second["submit"], second["record-invalid"], second["commit"]), (None, 0, 0), second)
        third = self.run_dag()                                   # もう何も残っていない
        self.assertIsNone(third["check"])
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(list(json.loads(self.ledger.read_text(encoding="utf-8"))), ["tm-1"])

    def test_example_passes_the_relative_name_not_the_path(self):
        dag = dagrun.load(DAG)
        for step in dag["steps"]:
            args = [str(a) for a in (step.get("with") or {}).get("args") or []]
            self.assertNotIn("${CHANGE.record.path}", args, step["name"])
            if "--name" in args:
                self.assertEqual(args[args.index("--name") + 1], "${CHANGE.record.rel}", step["name"])


if __name__ == "__main__":
    unittest.main()
