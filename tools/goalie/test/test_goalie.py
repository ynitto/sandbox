"""goalie.py の起こす判定（tick）・境界のゲート（act）・出来事の受け取り（event）を、アプリ役をテストが演じて確かめる。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import goalie  # noqa: E402

T0 = 1_800_000_000.0


def run(*argv: str) -> "tuple[int, str]":
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = goalie.main(list(argv))
    return code, out.getvalue().strip()


class GoalieTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.d = self.root / "estimate"
        code, _ = run("init", "--dir", str(self.d), "--name", "見積り")
        self.assertEqual(code, 0)

    def tearDown(self):
        self.tmp.cleanup()

    # -- 道具

    def state(self) -> dict:
        return json.loads((self.d / "state.json").read_text(encoding="utf-8"))

    def journal(self) -> "list[dict]":
        path = self.d / "journal.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]

    def tick(self, at: float = T0) -> str:
        code, out = run("tick", "--dir", str(self.d), "--now", str(at))
        self.assertEqual(code, 0)
        return out

    def act(self, at: float = T0) -> str:
        code, out = run("act", "--dir", str(self.d), "--now", str(at))
        self.assertEqual(code, 0)
        return out

    def propose(self, *props: dict) -> None:
        for i, prop in enumerate(props, 1):
            (self.d / "outbox" / f"{i:02d}.json").write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")

    def hub_note(self, body: str, kind: str = "appeared", system: bool = False) -> str:
        return json.dumps({"kind": kind, "key": "1", "record": {"id": 7, "body": body, "system": system,
                                                               "author": {"username": "alice"}}}, ensure_ascii=False)

    def finish_first_wake(self) -> None:
        self.assertTrue(self.tick().startswith("wake:"))
        self.propose({"kind": "note", "body": "始めた"})
        self.act()

    # -- init

    def test_init_makes_folder(self):
        for name in ("goal.md", "policy.json", "state.json", "inbox", "outbox", "notes"):
            self.assertTrue((self.d / name).exists(), name)
        self.assertEqual(self.state()["name"], "見積り")
        code, _ = run("init", "--dir", str(self.d))
        self.assertEqual(code, 2)                 # 二度は作らない

    def test_init_rejects_bracket_in_name(self):
        code, _ = run("init", "--dir", str(self.root / "x"), "--name", "【x】")
        self.assertEqual(code, 2)

    # -- tick

    def test_first_tick_wakes_and_writes_prompt(self):
        self.assertEqual(self.tick(), "wake: start")
        prompt = (self.d / ".goalie" / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("目標「見積り」", prompt)
        self.assertIn("outbox/", prompt)
        self.assertIn("【見積り】", prompt)

    def test_no_reason_no_wake(self):
        self.finish_first_wake()
        self.assertEqual(self.tick(T0 + 60), "")  # 出来事も期限も無ければ起こさない

    def test_inflight_blocks_second_wake(self):
        self.assertTrue(self.tick().startswith("wake:"))
        run("event", "--dir", str(self.d), "--text", "追加")
        self.assertEqual(self.tick(T0 + 60), "")  # act が片付けるまで起こさない

    def test_queued_job_blocks_wake(self):
        pending = self.d / ".copilot-jobs" / "pending"
        pending.mkdir(parents=True)
        (pending / f"20260101-000000-abc123-{goalie.JOB_LABEL}.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.tick(), "")
        self.assertEqual(self.act(), "")          # 終わっていない依頼の結果は待つ
        self.assertIsNone(self.state()["inflight"])

    def test_late_finish_is_picked_up_next_cycle(self):
        self.assertTrue(self.tick().startswith("wake:"))
        running = self.d / ".copilot-jobs" / "running"
        running.mkdir(parents=True)
        job = running / f"20260101-000000-abc123-{goalie.JOB_LABEL}.md"
        job.write_text("x", encoding="utf-8")
        self.assertEqual(self.act(), "")          # 取りかかり中は片付けない
        self.assertIsNotNone(self.state()["inflight"])
        job.unlink()                              # アプリが遅れて終えた
        self.propose({"kind": "act", "action": "hub.comment", "body": "遅れて終わりました"})
        self.assertEqual(self.tick(T0 + 900), "")  # 次の回は起こさず
        self.assertIn("遅れて終わりました", Path(self.act(T0 + 900)).read_text(encoding="utf-8"))
        self.assertIsNone(self.state()["inflight"])

    def test_idle_act_does_nothing(self):
        self.finish_first_wake()
        self.assertEqual(self.tick(T0 + 60), "")
        before = len(self.journal())
        self.assertEqual(self.act(T0 + 60), "")
        self.assertEqual(len(self.journal()), before)

    def test_stale_inflight_is_restored(self):
        self.finish_first_wake()
        run("event", "--dir", str(self.d), "--text", "追加")
        self.assertEqual(self.tick(T0 + 60), "wake: events")
        self.assertEqual(self.tick(T0 + 60 + goalie.INFLIGHT_STALE_SEC + 1), "wake: events")
        self.assertTrue(any(e.get("result") == "stale" for e in self.journal()))

    def test_events_are_in_prompt_and_consumed(self):
        self.finish_first_wake()
        run("event", "--dir", str(self.d), "--text", "前提は B で")
        self.assertEqual(self.tick(T0 + 60), "wake: events")
        self.assertIn("前提は B で", (self.d / ".goalie" / "prompt.md").read_text(encoding="utf-8"))
        self.assertEqual(list((self.d / "inbox").iterdir()), [])

    def test_wait_then_due(self):
        self.tick()
        until = goalie.iso(T0 + 3600)
        self.propose({"kind": "wait", "until": until})
        self.act()
        self.assertEqual(self.tick(T0 + 1800), "")
        self.assertEqual(self.tick(T0 + 3601), "wake: due")
        self.assertIsNone(self.state()["next_wake"])

    def test_budget_stops_waking(self):
        policy = json.loads((self.d / "policy.json").read_text(encoding="utf-8"))
        policy["wake_per_day"] = 1
        (self.d / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        self.finish_first_wake()
        run("event", "--dir", str(self.d), "--text", "追加")
        self.assertEqual(self.tick(T0 + 60), "")
        self.assertTrue(any(e.get("result") == "budget" for e in self.journal()))
        self.assertEqual(self.tick(T0 + 86400), "wake: events")   # 次の日はまた起きる

    # -- act

    def test_note_and_hub_comment(self):
        self.tick()
        self.propose({"kind": "note", "body": "仕様書の 3 章を読む"},
                     {"kind": "act", "action": "hub.comment", "body": "下書きを置きました"})
        path = self.act()
        text = Path(path).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("【見積りより】"))
        self.assertIn("下書きを置きました", text)
        notes = list((self.d / "notes").iterdir())
        self.assertEqual(len(notes), 1)
        self.assertIn("仕様書の 3 章を読む", notes[0].read_text(encoding="utf-8"))
        self.assertEqual(list((self.d / "outbox").iterdir()), [])
        self.assertIsNone(self.state()["inflight"])

    def test_unknown_and_denied_actions_do_nothing(self):
        policy = json.loads((self.d / "policy.json").read_text(encoding="utf-8"))
        policy["actions"]["hub.comment"] = "deny"
        policy["actions"]["git.push"] = "auto"    # 知らない作用は境界に書いても通さない
        (self.d / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        self.tick()
        self.propose({"kind": "act", "action": "hub.comment", "body": "x"},
                     {"kind": "act", "action": "git.push"},
                     {"kind": "dance"})
        self.assertEqual(self.act(), "")
        denied = [e for e in self.journal() if e.get("kind") == "proposal"]
        self.assertEqual([e["result"] for e in denied], ["denied"] * 3)

    def test_broken_json_is_denied(self):
        self.tick()
        (self.d / "outbox" / "01.json").write_text("{broken", encoding="utf-8")
        self.act()
        self.assertEqual(self.journal()[-1]["result"], "denied")

    def test_ask_posts_question(self):
        self.tick()
        self.propose({"kind": "ask", "question": "A と B のどちらで進めますか", "options": ["A", "B"]})
        text = Path(self.act()).read_text(encoding="utf-8")
        self.assertIn("質問 q1: A と B のどちらで進めますか", text)
        self.assertIn("【見積り】q1 A", text)
        self.assertIn("- B", text)
        self.assertEqual(self.state()["questions"][-1]["options"], ["A", "B"])

    def test_done_without_check_goes_to_review(self):
        self.tick()
        self.propose({"kind": "done", "evidence": ["plan.md を書いた"]})
        self.assertIn("確かめてください", Path(self.act()).read_text(encoding="utf-8"))
        self.assertEqual(self.state()["status"], "review")
        self.assertEqual(self.tick(T0 + 60), "")  # 返事が来るまで起きない
        run("event", "--dir", str(self.d), "--text", "まだ足りない")
        self.assertEqual(self.state()["status"], "active")
        self.assertEqual(self.tick(T0 + 120), "wake: events")

    def test_done_check(self):
        for code, status, word in ((1, "active", "通りませんでした"), (0, "done", "完了しました")):
            policy = json.loads((self.d / "policy.json").read_text(encoding="utf-8"))
            policy["done_check"] = [sys.executable, "-c", f"import sys; sys.exit({code})"]
            (self.d / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
            state = self.state()
            state["inflight"] = None
            (self.d / "state.json").write_text(json.dumps(state), encoding="utf-8")
            self.propose({"kind": "done", "evidence": []})
            self.assertIn(word, Path(self.act()).read_text(encoding="utf-8"))
            self.assertEqual(self.state()["status"], status)
        self.assertEqual(self.tick(T0 + 60), "")  # 終えた目標は起きない

    def test_empty_outbox_retries_then_drops(self):
        run("event", "--dir", str(self.d), "--text", "見て")
        at = T0
        for _ in range(goalie.MAX_RETRIES):
            self.assertTrue(self.tick(at).startswith("wake:"))
            self.act(at)                          # 提案が無い → 出来事を戻す
            self.assertEqual(len(list((self.d / "inbox").iterdir())), 1)
            at += 60
        self.assertTrue(self.tick(at).startswith("wake:"))
        self.act(at)
        self.assertEqual(list((self.d / "inbox").iterdir()), [])
        self.assertEqual(self.journal()[-1]["result"], "dropped")

    def test_empty_first_wake_retries_start(self):
        self.assertEqual(self.tick(), "wake: start")
        self.act()
        self.assertEqual(self.tick(T0 + 60), "wake: start")

    # -- event

    def test_hub_comment_routes_by_name(self):
        other = self.root / "other"
        run("init", "--dir", str(other), "--name", "別件")
        code, out = run("event", "--root", str(self.root), "--change", self.hub_note("【見積り】 B で進めて"))
        self.assertEqual(code, 0)
        files = list((self.d / "inbox").iterdir())
        self.assertEqual(len(files), 1)
        event = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((event["text"], event["author"]), ("B で進めて", "alice"))
        self.assertEqual(list((other / "inbox").iterdir()), [])

    def test_hub_comment_ignored(self):
        for change in (
            self.hub_note("【見積りより】\n\n報告"),             # 自分が書いたコメント
            self.hub_note("【依頼】[report] 集計"),              # ほかの用途
            self.hub_note("【見積り】x", kind="changed"),         # 書き直し
            self.hub_note("【見積り】x", system=True),            # GitLab の記録
            "",                                                  # 変化が無かった回
        ):
            code, _ = run("event", "--root", str(self.root), "--change", change)
            self.assertEqual(code, 0)
        self.assertEqual(list((self.d / "inbox").iterdir()), [])

    def test_status(self):
        code, out = run("status", "--dir", str(self.d))
        self.assertEqual(code, 0)
        self.assertIn("状態: active", out)


if __name__ == "__main__":
    unittest.main()
