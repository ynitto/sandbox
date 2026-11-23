"""業務係: 手持ちのステートマシンを選んで回す・無ければ作って試運転して登録する・記録から直して版を上げる・戻す。

statemachine-use はリポジトリの本物を一時フォルダへ写して使う（下書きの検証は本物の run_machine.py --dry-run）。
ステートを回すアプリ役はテストが演じる（outbox に結果を書く）。
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import dot  # noqa: E402
import routines  # noqa: E402

REPO = HERE.parents[1]
T0 = 1_800_000_000.0

WORKFLOW = """\
name: 月次の報告
description: 月次の売上の報告を作って送る
initial_state: make
inputs:
  month:
    label: 対象の月
    type: month
states:
  make:
    action_file: actions/make.md
  send:
    action_file: actions/send.md
  complete:
    terminal: true
transitions:
  - from: make
    to: send
  - from: send
    to: complete
"""


def run(*argv: str, stdin: str = "") -> "tuple[int, str]":
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
            mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        code = dot.main(list(argv))
    return code, out.getvalue().strip()


def write_machine(folder: Path, body: str = WORKFLOW) -> None:
    (folder / "actions").mkdir(parents=True, exist_ok=True)
    (folder / "workflow.yaml").write_text(body, encoding="utf-8")
    (folder / "actions" / "make.md").write_text("`report-maker` スキルで {{month}} の報告を作る。\n", encoding="utf-8")
    (folder / "actions" / "send.md").write_text("`mailer` スキルで報告を送る。\n", encoding="utf-8")


class RoutineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skills = self.root / "skills"
        shutil.copytree(REPO / ".github" / "skills" / "statemachine-use", self.skills / "statemachine-use",
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        for name in ("report-maker", "mailer", "skill-selector"):
            (self.skills / name / "scripts").mkdir(parents=True)
            (self.skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
            (self.skills / name / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        self.source = self.root / "repo" / ".statemachine"
        write_machine(self.source / "monthly-report")
        self.d = self.root / "dots" / "work"
        self.assertEqual(run("init", "--dir", str(self.d), "--name", "業務")[0], 0)
        self.set_policy(routines={"skills_dirs": [str(self.skills)], "sources": [str(self.source)],
                                  "write_skills": ["mailer"]})
        self.at = T0
        self.assertEqual(self.tick(), "wake: start")
        self.propose({"kind": "note", "body": "始めた"})
        self.act()

    def tearDown(self):
        self.tmp.cleanup()

    # -- 道具

    def set_policy(self, **kw) -> None:
        policy = json.loads((self.d / "policy.json").read_text(encoding="utf-8"))
        policy.update(kw)
        (self.d / "policy.json").write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    def state(self) -> dict:
        return json.loads((self.d / "state.json").read_text(encoding="utf-8"))

    def registry(self) -> dict:
        return routines.load_registry(self.d)

    def step(self) -> float:
        self.at += 60
        return self.at

    def tick(self) -> str:
        code, out = run("tick", "--dir", str(self.d), "--now", str(self.step()))
        self.assertEqual(code, 0)
        return out

    def act(self) -> str:
        code, out = run("act", "--dir", str(self.d), "--now", str(self.step()))
        self.assertEqual(code, 0)
        return Path(out).read_text(encoding="utf-8") if out else ""

    def propose(self, *props: dict) -> None:
        for i, prop in enumerate(props, 1):
            (self.d / "outbox" / f"{i:02d}.json").write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")

    def reply(self, text: str) -> str:
        change = json.dumps({"kind": "appeared", "key": "1", "record": {
            "id": 1, "body": f"【業務】{text}", "system": False, "author": {"username": "me"}}}, ensure_ascii=False)
        code, out = run("event", "--root", str(self.root / "dots"), "--change", change)
        self.assertEqual(code, 0)
        return out

    def prompt(self) -> str:
        return (self.d / ".dot" / "prompt.md").read_text(encoding="utf-8")

    def hook(self, name: str, args: dict) -> "str | None":
        payload = {"toolName": name, "toolArgs": json.dumps(args), "cwd": str(self.d)}
        code, out = run("hook", stdin=json.dumps(payload))
        self.assertEqual(code, 0)
        return json.loads(out)["permissionDecisionReason"] if out else None

    def script(self, skill: str) -> str:
        return f'python "{self.skills / skill / "scripts" / "main.py"}" --x 1'

    def job(self) -> dict:
        return self.state()["routine"]

    def approve_last(self, text: str) -> str:
        aid = text.split("承認待ち ")[1].split(":")[0]
        self.assertEqual(self.reply(f"承認 {aid}"), "受けました: approved")
        return self.act()

    def run_existing(self) -> str:
        """手持ちの monthly-report を初めて使う（承認 → 実行の開始）。回しているジョブの id を返す。"""
        self.reply("10 月の報告を作って送って")
        self.assertEqual(self.tick(), "wake: events")
        self.assertIn("monthly-report: 月次の報告", self.prompt())
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {"month": "2026-10"},
                      "request": "10 月の報告"})
        text = self.act()
        self.assertIn("業務 monthly-report を使えるようにする（使うスキル: mailer（外へ書く）、report-maker）", text)
        self.approve_last(text)
        self.assertEqual(self.job()["mode"], "run")
        return self.job()["id"]

    def finish(self, rid: str, ok: bool = True, final: str = "complete", summary: str = "送りました") -> str:
        self.assertEqual(self.tick(), "wake: events")
        self.propose({"kind": "routine", "action": "result", "id": rid, "ok": ok, "final_state": final,
                      "summary": summary, "states": [{"state": "make", "output": "OK"}]})
        return self.act()

    # -- 手持ちのものを使う

    def test_existing_machine_needs_one_approval_then_runs(self):
        rid = self.run_existing()
        self.assertEqual(self.tick(), "wake: events")
        prompt = self.prompt()
        self.assertIn(f"今回は業務 {rid} の実行", prompt)
        self.assertIn('"month": "2026-10"', prompt)
        self.assertIn(str(self.source / "monthly-report" / "workflow.yaml"), prompt)
        # 承認したスキルのスクリプトと statemachine-use は通し、ほかは止める
        self.assertIsNone(self.hook("powershell", {"command": self.script("report-maker")}))
        self.assertIsNone(self.hook("powershell", {"command": self.script("mailer")}))
        ns = self.skills / "statemachine-use" / "scripts" / "next_state.py"
        self.assertIsNone(self.hook("bash", {"command": f'python3 "{ns}" x.yaml --initial-state'}))
        self.assertIn("許していません", self.hook("bash", {"command": self.script("skill-selector")}))
        self.assertIn("許していません", self.hook("bash", {"command": self.script("report-maker") + " | tee x"}))
        self.assertIsNone(self.hook("view", {"path": str(self.source / "monthly-report" / "workflow.yaml")}))
        self.propose({"kind": "routine", "action": "result", "id": rid, "ok": True, "final_state": "complete",
                      "summary": "10 月の報告を送りました", "states": [{"state": "make", "output": "OK"}]})
        text = self.act()
        self.assertIn(f"業務 {rid}（monthly-report）: できました", text)
        rec = self.registry()["monthly-report"]
        self.assertEqual((rec["status"], rec["runs"], rec["ok"]), ("ready", 1, 1))
        record = json.loads((self.d / ".dot" / "runs" / "monthly-report" / f"{rid}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["inputs"], {"month": "2026-10"})
        self.assertIsNone(self.state()["routine"])
        # 2 回目は聞かずに始まる
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {"month": "2026-11"}})
        self.assertEqual(self.act(), "")
        self.assertEqual(self.job()["inputs"], {"month": "2026-11"})

    def test_missing_inputs_are_asked(self):
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {}})
        text = self.act()
        self.assertIn("入力が足りません: 対象の月（month）", text)
        self.assertIsNone(self.state().get("routine"))

    def test_changed_machine_needs_approval_again(self):
        rid = self.run_existing()
        self.finish(rid)
        (self.source / "monthly-report" / "actions" / "send.md").write_text("`mailer` スキルで全員に送る。\n", encoding="utf-8")
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {"month": "2026-11"}})
        self.assertIn("承認待ち", self.act())

    def test_busy_and_unknown(self):
        self.run_existing()
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {"month": "x"}},
                     {"kind": "routine", "action": "run", "machine": "nothing"},
                     {"kind": "routine", "action": "result", "id": "r99", "ok": True})
        self.act()
        why = [json.loads(x).get("why", "") for x in (self.d / "journal.jsonl").read_text(encoding="utf-8").splitlines()][-3:]
        self.assertIn("進行中", why[0])
        self.assertIn("進行中", why[1])
        self.assertIn("id が違う", why[2])

    def test_disabled_without_skills_dirs(self):
        self.set_policy(routines={})
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report"})
        self.act()
        last = json.loads((self.d / "journal.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertIn("使えるようにしていない", last["why"])

    # -- 新しく作る

    def create_and_trial(self, name: str = "weekly-digest") -> str:
        self.reply("毎週の進捗のまとめを作って送って")
        self.tick()
        self.propose({"kind": "routine", "action": "create", "name": name, "purpose": "週次の進捗のまとめ",
                      "inputs": {"month": "2026-W47"}, "request": "週次のまとめ"})
        self.assertEqual(self.act(), "")
        job = self.job()
        self.assertEqual(job["mode"], "create")
        self.assertEqual(self.tick(), "wake: events")
        prompt = self.prompt()
        self.assertIn("作成モード", prompt)
        self.assertIn(f"--dir {self.d / 'routines'}", prompt)
        # 書けるのは下書きと notes / outbox だけ
        self.assertIsNone(self.hook("create", {"path": f"routines/{name}/workflow.yaml", "file_text": "x"}))
        self.assertIsNone(self.hook("create", {"path": "outbox/01.json", "file_text": "{}"}))
        self.assertIn("下書き", self.hook("create", {"path": "drafts/x.md", "file_text": "x"}))
        self.assertIn("書けません", self.hook("create", {"path": ".statemachine/x/workflow.yaml", "file_text": "x"}))
        scaffold = self.skills / "statemachine-use" / "scripts" / "scaffold.py"
        self.assertIsNone(self.hook("bash", {"command": f'python "{scaffold}" {name} --dir routines'}))
        self.assertIsNone(self.hook("bash", {"command": self.script("skill-selector")}))
        # 1 回目は壊れた下書き → 直させる
        (self.d / "routines" / name).mkdir(parents=True, exist_ok=True)
        (self.d / "routines" / name / "workflow.yaml").write_text("name: x\nstates: {}\n", encoding="utf-8")
        self.propose({"kind": "routine", "action": "created", "id": job["id"], "writes": []})
        self.assertEqual(self.act(), "")
        self.assertEqual(self.job()["tries"], 1)
        self.assertEqual(self.tick(), "wake: events")
        self.assertIn("前回の検証で落ちた理由", self.prompt())
        # 2 回目は正しい下書き → 試運転へ
        write_machine(self.d / "routines" / name)
        self.propose({"kind": "routine", "action": "created", "id": job["id"], "writes": [], "summary": "2 ステートで作った"})
        self.assertEqual(self.act(), "")
        trial = self.job()
        self.assertEqual((trial["mode"], trial["inputs"]), ("trial", {"month": "2026-W47"}))
        self.assertEqual(self.registry()[name]["draft"]["status"], "trial")
        self.assertEqual(self.tick(), "wake: events")
        self.assertIn("試運転なので、外へ書くスキル（mailer）は動かさない", self.prompt())
        self.assertIn("外へ書くスキルは動かしません", self.hook("bash", {"command": self.script("mailer")}))
        self.assertIsNone(self.hook("bash", {"command": self.script("report-maker")}))
        return trial["id"]

    def test_create_trial_publish(self):
        rid = self.create_and_trial()
        self.propose({"kind": "routine", "action": "result", "id": rid, "ok": True, "final_state": "complete",
                      "summary": "まとめの下書きを作った（送信は止めた）"})
        text = self.act()
        self.assertIn("業務 weekly-digest を登録する（使うスキル: mailer（外へ書く）、report-maker）", text)
        self.assertIn("試運転の結果: まとめの下書きを作った", text)
        self.assertIn("登録しました", self.approve_last(text))
        published = self.d / ".statemachine" / "weekly-digest"
        self.assertTrue((published / "workflow.yaml").is_file())
        self.assertFalse((self.d / "routines" / "weekly-digest").exists())
        rec = self.registry()["weekly-digest"]
        self.assertEqual((rec["status"], rec["version"]), ("ready", 1))
        self.assertNotIn("draft", rec)
        entry = routines.find_entry(self.d, json.loads((self.d / "policy.json").read_text(encoding="utf-8")), "weekly-digest")
        self.assertEqual(entry["status"], "ready")
        # 登録したものはそのまま回せる
        self.propose({"kind": "routine", "action": "run", "machine": "weekly-digest", "inputs": {"month": "2026-W48"}})
        self.assertEqual(self.act(), "")
        self.assertEqual(self.job()["mode"], "run")

    def test_rejected_trial_goes_to_improve(self):
        rid = self.create_and_trial()
        self.propose({"kind": "routine", "action": "result", "id": rid, "ok": True, "summary": "まとめを作った"})
        text = self.act()
        aid = text.split("承認待ち ")[1].split(":")[0]
        self.assertEqual(self.reply(f"却下 {aid} 宛先が違う"), "受けました: rejected")
        self.act()
        job = self.job()
        self.assertEqual((job["mode"], job["reason"]), ("improve", "宛先が違う"))

    def test_create_rejects_bad_or_taken_name(self):
        self.propose({"kind": "routine", "action": "create", "name": "Bad Name"},
                     {"kind": "routine", "action": "create", "name": "monthly-report"})
        self.act()
        why = [json.loads(x).get("why", "") for x in (self.d / "journal.jsonl").read_text(encoding="utf-8").splitlines()][-2:]
        self.assertIn("英小文字", why[0])
        self.assertIn("既にある", why[1])

    def test_create_gives_up_after_tries(self):
        self.propose({"kind": "routine", "action": "create", "name": "broken-one", "purpose": "x"})
        self.act()
        rid = self.job()["id"]
        text = ""
        for _ in range(routines.MAX_CREATE_TRIES):
            self.propose({"kind": "routine", "action": "created", "id": rid})
            text = self.act()
        self.assertIn("作成できませんでした", text)
        self.assertIsNone(self.state()["routine"])

    # -- 直す・戻す

    def publish_monthly_to_dot(self) -> None:
        """登録済みの monthly-report（目標のフォルダの .statemachine/）を用意する。"""
        rid = self.run_existing()
        self.finish(rid)

    def test_fix_request_improves_with_failed_inputs_and_versions(self):
        self.publish_monthly_to_dot()
        self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {"month": "2026-12"}})
        self.act()
        rid = self.job()["id"]
        text = self.finish(rid, ok=False, final="send", summary="宛先が無い")
        self.assertIn("できませんでした", text)
        self.assertIn(f"直す {rid} 理由", text)
        self.assertEqual(self.registry()["monthly-report"]["state_failures"], {"send": 1})
        self.assertEqual(self.reply(f"直す {rid} 宛先を設定から読む"), "受けました: improve")
        self.act()
        job = self.job()
        self.assertEqual((job["mode"], job["inputs"]), ("improve", {"month": "2026-12"}))
        draft = self.d / "routines" / "monthly-report"
        self.assertTrue((draft / "workflow.yaml").is_file())
        self.assertEqual(self.tick(), "wake: events")
        prompt = self.prompt()
        self.assertIn("直す理由: 宛先を設定から読む", prompt)
        self.assertIn("宛先が無い", prompt)
        # 直して試運転 → 登録 → 版 2
        (draft / "actions" / "send.md").write_text("`mailer` スキルで、設定の宛先へ送る。\n", encoding="utf-8")
        self.propose({"kind": "routine", "action": "created", "id": job["id"], "summary": "宛先を設定から読む"})
        self.act()
        trial = self.job()
        self.assertEqual((trial["mode"], trial["inputs"]), ("trial", {"month": "2026-12"}))
        text = self.finish(trial["id"], summary="宛先を読めた")
        self.assertIn("直した版で差し替える", text)
        self.assertIn("版 2", self.approve_last(text))
        rec = self.registry()["monthly-report"]
        self.assertEqual((rec["version"], len(rec["history"])), (2, 1))
        published = self.d / ".statemachine" / "monthly-report"
        self.assertIn("設定の宛先", (published / "actions" / "send.md").read_text(encoding="utf-8"))
        # 戻す
        self.assertEqual(self.reply("戻す monthly-report"), "受けました: rollback")
        self.assertIn("版 1 に戻しました", self.act())
        self.assertNotIn("設定の宛先", (published / "actions" / "send.md").read_text(encoding="utf-8"))
        self.assertEqual(self.registry()["monthly-report"]["version"], 1)
        self.assertEqual(self.reply("戻す monthly-report"), "受けました: no-history")

    def test_repeated_failures_improve_without_asking(self):
        self.publish_monthly_to_dot()
        for i in range(2):
            self.propose({"kind": "routine", "action": "run", "machine": "monthly-report", "inputs": {"month": f"m{i}"}})
            self.act()
            text = self.finish(self.job()["id"], ok=False, final="make", summary="失敗")
        self.assertIn("失敗が続いているので", text)
        self.assertEqual(self.job()["mode"], "improve")
        self.assertIn("直近 3 回で 2 回失敗", self.job()["reason"])

    def test_fix_unknown(self):
        self.assertEqual(self.reply("直す nothing 理由"), "受けました: unknown-routine")

    def test_stale_job_is_dropped(self):
        self.run_existing()
        self.at += routines.JOB_STALE_SEC + 1
        self.assertIn("取り下げました", self.act())
        self.assertIsNone(self.state()["routine"])

    def test_status_lists_routines(self):
        code, out = run("status", "--dir", str(self.d))
        self.assertIn("業務の進行: なし", out)
        self.assertIn("monthly-report（unapproved・版 0・未実行）", out)


class HelperTest(unittest.TestCase):
    def test_script_of(self):
        self.assertEqual(routines.script_of('"C:\\Py\\python.exe" "C:\\s\\a.py" x'), "C:\\s\\a.py")
        self.assertEqual(routines.script_of("python3 /s/a.py"), "/s/a.py")
        self.assertEqual(routines.script_of("node x.js"), "x.js")
        self.assertIsNone(routines.script_of("curl x.py"))
        self.assertIsNone(routines.script_of("python -c print(1)"))

    def test_rank_prefers_matching_words(self):
        entries = [{"name": "a", "title": "請求書の発行", "description": "", "inputs": {}},
                   {"name": "b", "title": "月次の売上報告", "description": "", "inputs": {}}]
        self.assertEqual(routines.rank("10 月の売上の報告をお願い", entries)[0]["name"], "b")

    def test_digest_ignores_recordings_and_newlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_machine(folder)
            before = routines.digest(folder)
            (folder / "recordings").mkdir()
            (folder / "recordings" / "x.md").write_text("x", encoding="utf-8")
            (folder / "workflow.yaml").write_bytes(WORKFLOW.replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(routines.digest(folder), before)


if __name__ == "__main__":
    unittest.main()
