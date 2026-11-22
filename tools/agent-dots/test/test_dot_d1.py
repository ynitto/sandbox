"""D1: 作用ごとの承認（auto / ask / deny）、登録したコマンドの実行、質問への返事、夜間の溜め置き、ロックを確かめる。"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dot  # noqa: E402

T0 = 1_800_000_000.0


def run(*argv: str) -> "tuple[int, str]":
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = dot.main(list(argv))
    return code, out.getvalue().strip()


def at_clock(hour: int, minute: int = 0) -> float:
    """この PC の時刻で、T0 の日の hour:minute。"""
    base = dt.datetime.fromtimestamp(T0)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


class D1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.d = self.root / "estimate"
        self.assertEqual(run("init", "--dir", str(self.d), "--name", "見積り")[0], 0)
        self.script = self.root / "publish.py"
        self.script.write_text(
            "import sys\n"
            "data = sys.stdin.buffer.read().decode('utf-8')\n"
            "open('published.txt', 'w', encoding='utf-8').write(data)\n"
            "print('置きました: ' + data)\n"
            "sys.exit(1 if 'fail' in data else 0)\n", encoding="utf-8")
        self.set_policy(commands={
            "publish": {"run": [sys.executable, str(self.script)], "level": "ask", "description": "下書きを共有に置く"},
            "lint": {"run": [sys.executable, str(self.script)], "level": "auto", "description": "検査"},
            "nuke": {"run": [sys.executable, "-c", "print(1)"], "level": "deny"},
        })
        self.assertTrue(self.tick().startswith("wake:"))

    def tearDown(self):
        self.tmp.cleanup()

    # -- 道具

    def set_policy(self, **kw) -> None:
        policy = json.loads((self.d / "policy.json").read_text(encoding="utf-8"))
        policy.update(kw)
        (self.d / "policy.json").write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    def state(self) -> dict:
        return json.loads((self.d / "state.json").read_text(encoding="utf-8"))

    def journal(self) -> "list[dict]":
        return [json.loads(x) for x in (self.d / "journal.jsonl").read_text(encoding="utf-8").splitlines()]

    def inbox(self) -> "list[str]":
        return [json.loads(p.read_text(encoding="utf-8"))["text"] for p in sorted((self.d / "inbox").iterdir())]

    def tick(self, at: float = T0) -> str:
        code, out = run("tick", "--dir", str(self.d), "--now", str(at))
        self.assertEqual(code, 0)
        return out

    def act(self, at: float = T0) -> str:
        code, out = run("act", "--dir", str(self.d), "--now", str(at))
        self.assertEqual(code, 0)
        return Path(out).read_text(encoding="utf-8") if out else ""

    def propose(self, *props: dict) -> None:
        for i, prop in enumerate(props, 1):
            (self.d / "outbox" / f"{i:02d}.json").write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")

    def reply(self, body: str, author: str = "me") -> str:
        change = json.dumps({"kind": "appeared", "key": "1", "record": {
            "id": 9, "body": body, "system": False, "author": {"username": author}}}, ensure_ascii=False)
        code, out = run("event", "--root", str(self.root), "--change", change)
        self.assertEqual(code, 0)
        return out

    def approval(self, aid: str) -> dict:
        return next(x for x in self.state()["approvals"] if x["id"] == aid)

    # -- 登録したコマンド

    def test_ask_command_waits_for_approval_then_runs(self):
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "計画 v1", "why": "共有のため"})
        text = self.act()
        self.assertIn("承認待ち a1: コマンド publish（下書きを共有に置く）", text)
        self.assertIn("計画 v1", text)
        self.assertIn("【見積り】承認 a1", text)
        self.assertFalse((self.d / "published.txt").exists())   # 承認まで動かさない
        self.assertEqual(self.approval("a1")["status"], "pending")

        self.assertEqual(self.reply("【見積り】承認 a1"), "受けました: approved")
        text = self.act(T0 + 60)
        self.assertEqual((self.d / "published.txt").read_text(encoding="utf-8"), "計画 v1")
        self.assertIn("a1（コマンド publish（下書きを共有に置く））を動かしました（終了コード 0）", text)
        self.assertEqual(self.approval("a1")["status"], "done")
        self.assertIn("動かしました", self.inbox()[-1])          # 結果は次に起きたときに読める
        self.assertEqual(self.tick(T0 + 120), "wake: events")

    def test_reject(self):
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "x"})
        self.act()
        self.assertEqual(self.reply("【見積り】却下 a1 まだ早い"), "受けました: rejected")
        self.assertEqual(self.act(T0 + 60), "")
        self.assertFalse((self.d / "published.txt").exists())
        self.assertEqual(self.approval("a1")["status"], "rejected")
        self.assertIn("却下されました。理由: まだ早い", self.inbox()[-1])

    def test_decision_twice_or_unknown_is_event(self):
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "x"})
        self.act()
        self.reply("【見積り】承認 a1")
        self.assertEqual(self.reply("【見積り】却下 a1"), "受けました: unknown-approval")
        self.assertEqual(self.reply("【見積り】承認 a9"), "受けました: unknown-approval")
        self.assertEqual(self.approval("a1")["status"], "approved")

    def test_approvers(self):
        self.set_policy(approvers=["boss"])
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "x"})
        self.act()
        self.assertEqual(self.reply("【見積り】承認 a1", author="someone"), "受けました: not-approver")
        self.assertEqual(self.approval("a1")["status"], "pending")
        self.assertEqual(self.reply("【見積り】承認 a1", author="boss"), "受けました: approved")

    def test_auto_command_runs_quietly_unless_it_fails(self):
        self.propose({"kind": "act", "action": "run", "name": "lint", "input": "ok"})
        self.assertEqual(self.act(), "")                      # 成功は inbox にだけ残す
        self.assertIn("lint", self.inbox()[-1])
        self.assertEqual(self.tick(T0 + 60), "wake: events")
        self.propose({"kind": "act", "action": "run", "name": "lint", "input": "fail"})
        self.assertIn("失敗しました（終了コード 1）", self.act(T0 + 60))

    def test_denied_commands(self):
        self.propose({"kind": "act", "action": "run", "name": "nuke"},
                     {"kind": "act", "action": "run", "name": "unknown"},
                     {"kind": "act", "action": "run"})
        self.assertEqual(self.act(), "")
        results = [e["result"] for e in self.journal() if e.get("kind") == "proposal"]
        self.assertEqual(results, ["denied"] * 3)
        self.assertEqual(self.state()["approvals"], [])

    def test_hub_comment_can_require_approval(self):
        self.set_policy(actions={"hub.comment": "ask"})
        self.propose({"kind": "act", "action": "hub.comment", "body": "先方に送る文案です"})
        self.assertIn("承認待ち a1: ハブへの報告: 先方に送る文案です", self.act())
        self.reply("【見積り】承認 a1")
        text = self.act(T0 + 60)
        self.assertIn("先方に送る文案です", text)
        self.assertNotIn("承認待ち", text)

    def test_expire(self):
        self.set_policy(approval_expire_days=1)
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "x"})
        self.act()
        self.act(T0 + 86400 + 1)
        self.assertEqual(self.approval("a1")["status"], "expired")
        self.assertIn("取り下げました", self.inbox()[-1])
        self.assertEqual(self.reply("【見積り】承認 a1"), "受けました: unknown-approval")

    def test_prompt_lists_commands_and_pending(self):
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "x"})
        self.act()
        run("event", "--dir", str(self.d), "--text", "続けて")
        self.tick(T0 + 60)
        prompt = (self.d / ".dot" / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("publish: 下書きを共有に置く（利用者が承認してから動く", prompt)
        self.assertIn("lint: 検査（すぐ動く", prompt)
        self.assertNotIn("nuke", prompt)
        self.assertIn("a1 コマンド publish", prompt)

    def test_lost_run_is_reported_not_rerun(self):
        self.propose({"kind": "act", "action": "run", "name": "publish", "input": "x"})
        self.act()
        state = self.state()
        state["approvals"][0].update(status="running", started_ts=T0)   # 前の act が実行中に落ちた
        (self.d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.act(T0 + 60), "")                         # まだ止まったとはみなさない
        self.assertIn("途中で止まったようです", self.act(T0 + dot.RUNNING_STALE_SEC + 1))
        self.assertEqual(self.approval("a1")["status"], "failed")
        self.assertFalse((self.d / "published.txt").exists())

    # -- 質問への返事

    def test_answer_is_tied_to_question(self):
        self.propose({"kind": "ask", "question": "前提は A と B のどちらですか", "options": ["A", "B"]})
        self.assertIn("質問 q1", self.act())
        self.assertEqual(self.reply("【見積り】q1 B で"), "受けました: answer")
        self.assertIn("q1 への返事: B で（質問: 前提は A と B のどちらですか）", self.inbox()[-1])
        self.tick(T0 + 60)
        prompt = (self.d / ".dot" / "prompt.md").read_text(encoding="utf-8")
        self.assertNotIn("返事を待っている質問", prompt)
        self.assertEqual(self.reply("【見積り】ついでに C も"), "受けました: event")

    # -- 夜間の溜め置き

    def test_quiet_hours_hold_then_release(self):
        self.set_policy(quiet_hours="22:00-07:00")
        self.propose({"kind": "act", "action": "hub.comment", "body": "夜の報告"})
        self.assertEqual(self.act(at_clock(23)), "")
        self.assertEqual(self.state()["held"], ["夜の報告"])
        self.assertEqual(self.act(at_clock(23, 30)), "")
        text = self.act(at_clock(23) + 9 * 3600)             # 翌朝 8:00
        self.assertIn("夜の報告", text)
        self.assertEqual(self.state()["held"], [])

    def test_in_quiet(self):
        self.assertTrue(dot.in_quiet("22:00-07:00", at_clock(23)))
        self.assertTrue(dot.in_quiet("22:00-07:00", at_clock(6, 59)))
        self.assertFalse(dot.in_quiet("22:00-07:00", at_clock(7)))
        self.assertTrue(dot.in_quiet("12:00-13:00", at_clock(12, 30)))
        self.assertFalse(dot.in_quiet("", at_clock(23)))
        self.assertFalse(dot.in_quiet("夜", at_clock(23)))

    # -- ロック

    def test_busy_lock_returns_3(self):
        lock = self.d / ".dot" / "lock"
        lock.write_text("", encoding="utf-8")
        with unittest.mock.patch.object(dot, "LOCK_WAIT_SEC", 0.3):
            code, _ = run("event", "--dir", str(self.d), "--text", "x")
        self.assertEqual(code, dot.EXIT_BUSY)
        self.assertEqual(self.inbox(), [])

    def test_stale_lock_is_taken(self):
        lock = self.d / ".dot" / "lock"
        lock.write_text("", encoding="utf-8")
        import os
        import time
        old = time.time() - 3600
        os.utime(lock, (old, old))
        code, _ = run("event", "--dir", str(self.d), "--text", "x")
        self.assertEqual(code, 0)
        self.assertFalse(lock.exists())


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()


class DecodeTest(unittest.TestCase):
    def test_decode_output_falls_back_to_cp932(self):
        self.assertEqual(dot.decode_output("置きました".encode("utf-8")), "置きました")
        self.assertEqual(dot.decode_output("置きました".encode("cp932")), "置きました")

    def test_command_writes_japanese_in_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 子の Python は PYTHONIOENCODING を見る。Windows の既定（cp1252 など）でも日本語を書ける
            code, out = dot.run_command(Path(tmp), [sys.executable, "-c", "print('置きました')"])
        self.assertEqual((code, out), (0, "置きました"))
