"""投稿の中身で宛先を振る: 投稿の #名前 → フローの target → チャンネル → キーワード → ハブのセッション → 既定。

ハブがあるときは、本物の copilot_app_job.py でハブの受け箱に置き、選ぶ指示・route・振る指示までをたどる。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("teams_bridge", HERE / "teams_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
JOBS_PY = HERE.parents[2] / "copilot-app-jobs" / "copilot_app_job.py"
sys.path.insert(0, str(JOBS_PY.parent))
import copilot_app_job as jobs  # noqa: E402

REQUEST_ID = "req-1"


def call(module, *argv) -> "tuple[int, str, str]":
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class RoutingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()   # Windows の短い名前を正式な形にそろえる
        self.app, self.docs, self.ops, self.hub = (self.root / n for n in ("app", "docs", "ops", "hub"))
        for folder in (self.app, self.docs, self.ops, self.hub):
            folder.mkdir()
        self.path = self.root / f"{REQUEST_ID}.json"
        self.targets = self.root / "targets.json"
        self.ledger = self.root / "state" / "submitted.json"
        self.config = {
            "default": "app",
            "targets": {
                "app": {"dir": str(self.app), "gitlab_project": "g/app", "mode": "issue",
                        "description": "業務アプリのコードと不具合", "keywords": ["バグ", "画面"]},
                "docs": {"dir": str(self.docs), "gitlab_project": "g/docs", "mode": "commit",
                         "description": "手順書とマニュアル", "keywords": ["マニュアル", "手順書"],
                         "channels": ["ch-docs"]},
                "ops": {"dir": str(self.ops), "gitlab_project": "g/ops", "mode": "issue",
                        "description": "障害の対応と再発防止", "keywords": ["障害"]},
            },
        }
        self.write(text="お願いします")

    def write(self, text: str, **extra) -> None:
        request = {"request_id": REQUEST_ID, "team_id": "t", "channel_id": "ch-general", "message_id": "m-1",
                   "author": "依頼者", "posted_at": "2026-11-23T12:00:00Z", "text": text, **extra}
        self.path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        self.targets.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def dry(self) -> dict:
        code, out, err = call(bridge, "submit", "--path", str(self.path), "--targets", str(self.targets),
                              "--ledger", str(self.ledger), "--jobs-py", str(JOBS_PY), "--dry-run")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def route(self) -> "tuple[str, str]":
        r = self.dry()["route"]
        return r["target"], r["reason"]

    # -- 決まる順

    def test_explicit_tag_wins(self):
        self.write(text="#docs 障害の手順を直して")
        self.assertEqual(self.route(), ("docs", "投稿で指定（#docs）"))
        self.write(text="\n【ops】 マニュアルの件", target="app")
        self.assertEqual(self.route(), ("ops", "投稿で指定（【ops】）"))

    def test_unknown_tag_is_ignored(self):
        self.write(text="#nothing マニュアルを直して")
        self.assertEqual(self.route(), ("docs", "キーワード（マニュアル）"))

    def test_flow_target_before_rules(self):
        self.write(text="マニュアルを直して", target="ops")
        self.assertEqual(self.route(), ("ops", "フローで指定"))

    def test_channel(self):
        self.write(text="画面のバグ", channel_id="ch-docs")
        self.assertEqual(self.route(), ("docs", "投稿したチャンネル"))

    def test_keywords_unique_best(self):
        self.write(text="画面にバグがある。マニュアルにも書いて")
        self.assertEqual(self.route(), ("app", "キーワード（バグ・画面）"))

    def test_no_hub_falls_back_to_default(self):
        self.write(text="来週の打ち合わせの資料を作って")
        self.assertEqual(self.route(), ("app", "既定の宛先"))
        self.write(text="障害のマニュアル")                 # 同点も、ハブが無ければ既定
        self.assertEqual(self.route(), ("app", "既定の宛先"))

    def test_hub_gets_the_rest(self):
        self.config["hub"] = str(self.hub)
        self.write(text="来週の打ち合わせの資料を作って")
        out = self.dry()
        self.assertEqual(out["route"]["target"], "auto")
        self.assertEqual(out["route"]["choices"], ["app", "docs", "ops"])
        self.assertIn("--to", out["command"])
        self.assertEqual(out["command"][out["command"].index("--to") + 1], "auto")
        self.assertEqual(out["command"][out["command"].index("--dir") + 1], str(self.hub))
        self.assertNotIn("g/app", out["prompt"])             # 宛先ごとの続きは候補に持たせる
        self.write(text="障害のマニュアル")                  # 同点の 2 つだけを候補にする
        self.assertEqual(self.dry()["route"]["choices"], ["docs", "ops"])

    def test_hub_with_decided_target_dispatches(self):
        self.config["hub"] = str(self.hub)
        self.write(text="#ops ログを見て")
        out = self.dry()
        cmd = out["command"]
        self.assertEqual((cmd[cmd.index("--dir") + 1], cmd[cmd.index("--to") + 1]), (str(self.hub), str(self.ops)))
        self.assertIn("宛先: ops（投稿で指定（#ops））", out["prompt"])

    def test_relative_hub_is_invalid(self):
        self.config["hub"] = "hub"
        self.write(text="x")
        code, out, _ = call(bridge, "check-file", "--path", str(self.path), "--targets", str(self.targets),
                            "--ledger", str(self.ledger))
        self.assertEqual(out.strip(), "invalid")

    # -- ハブの受け箱をたどる

    def use_hub_file(self) -> None:
        """宛先表をハブの受け箱に置く（copilot-app-jobs の振り先の表と同じファイル）。hub は書かない。"""
        self.targets = self.hub / ".copilot-jobs" / "targets.json"
        self.targets.parent.mkdir(parents=True)
        self.config["targets"]["scratch"] = {"dir": str(self.ops)}   # Teams 以外から振るために登録した振り先
        self.write(text="お願いします")

    def test_hub_is_inferred_from_file_location(self):
        self.use_hub_file()
        self.write(text="来週の打ち合わせの資料を作って")
        out = self.dry()
        self.assertEqual(out["command"][out["command"].index("--dir") + 1], str(self.hub))
        self.assertEqual(out["route"]["choices"], ["app", "docs", "ops"])   # scratch は Teams の宛先ではない
        self.write(text="#scratch これは振れない")
        self.assertEqual(self.dry()["route"]["target"], "auto")

    def test_end_to_end_through_hub(self):
        self.use_hub_file()
        self.write(text="先月の障害の再発防止策を手順書にまとめて")   # 障害・手順書 → 同点で ops と docs
        code, out, err = call(bridge, "submit", "--path", str(self.path), "--targets", str(self.targets),
                              "--ledger", str(self.ledger), "--jobs-py", str(JOBS_PY), "--python", sys.executable)
        self.assertEqual(code, 0, err)
        submitted = json.loads(out)
        self.assertEqual((submitted["status"], submitted["target"]), ("submitted", "auto"))
        job_id = submitted["job"]
        text = call(jobs, "next", "--dir", str(self.hub))[1]
        self.assertIn("- docs: 手順書とマニュアル", text)
        self.assertIn("- ops: 障害の対応と再発防止", text)
        self.assertNotIn("- app:", text)
        code, text, err = call(jobs, "route", "--dir", str(self.hub), "--id", job_id, "--to", "docs", "--why", "手順書にまとめる依頼")
        self.assertEqual(code, 0, err)
        self.assertIn(f"ローカルフォルダ {self.docs}", text)
        self.assertIn("結果を GitLab のプロジェクト `g/docs` にコミットして push してください", text)
        self.assertIn("宛先: docs（ハブのセッションが選んだ）", text)
        self.assertIn("<!-- teams-bridge:request=req-1 teams-message=m-1 -->", text)
        # 台帳が消えても、ハブの受け箱（振った依頼）からマーカーで見つけ直す
        call(jobs, "dispatched", "--dir", str(self.hub), "--id", job_id)
        self.ledger.unlink()
        code, out, _ = call(bridge, "submit", "--path", str(self.path), "--targets", str(self.targets),
                            "--ledger", str(self.ledger), "--jobs-py", str(JOBS_PY), "--python", sys.executable)
        self.assertEqual(json.loads(out)["job"], job_id)
        # 振り先の表も同じファイル。target add は Teams の項目を残して説明だけを変える
        call(jobs, "target", "add", "--dir", str(self.hub), "--to", str(self.docs), "--name", "docs", "--description", "手順書")
        kept = json.loads(self.targets.read_text(encoding="utf-8"))["targets"]["docs"]
        self.assertEqual((kept["gitlab_project"], kept["description"]), ("g/docs", "手順書"))

if __name__ == "__main__":
    unittest.main()
