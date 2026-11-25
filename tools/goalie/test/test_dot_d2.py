"""D2: 手の空いた時間の調べもの（読み取りだけ）と、Copilot の preToolUse で範囲の外のツールを止めるフックを確かめる。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import goalie as dot  # noqa: E402

T0 = 1_800_000_000.0
JOB = '"C:\\Python\\python.exe" "C:\\tools\\copilot-app-jobs\\copilot_app_job.py" done --dir "C:\\work\\dots\\x" --id 1 --status ok'


def run(*argv: str, stdin: str = "") -> "tuple[int, str]":
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
            mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        code = dot.main(list(argv))
    return code, out.getvalue().strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.d = self.root / "estimate"
        self.assertEqual(run("init", "--dir", str(self.d), "--name", "見積り")[0], 0)

    def tearDown(self):
        self.tmp.cleanup()

    def set_policy(self, **kw) -> None:
        policy = json.loads((self.d / "policy.json").read_text(encoding="utf-8"))
        policy.update(kw)
        (self.d / "policy.json").write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")

    def state(self) -> dict:
        return json.loads((self.d / "state.json").read_text(encoding="utf-8"))

    def tick(self, at: float) -> str:
        code, out = run("tick", "--dir", str(self.d), "--now", str(at))
        self.assertEqual(code, 0)
        return out

    def act(self, at: float) -> str:
        code, out = run("act", "--dir", str(self.d), "--now", str(at))
        self.assertEqual(code, 0)
        return Path(out).read_text(encoding="utf-8") if out else ""

    def propose(self, *props: dict) -> None:
        for i, prop in enumerate(props, 1):
            (self.d / "outbox" / f"{i:02d}.json").write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")

    def first_wake(self) -> None:
        self.assertEqual(self.tick(T0), "wake: start")
        self.propose({"kind": "note", "body": "始めた"})
        self.act(T0)


class ProactiveTest(Base):
    def setUp(self):
        super().setUp()
        self.set_policy(proactive_per_day=2, proactive_idle_min=60)
        self.first_wake()

    def test_off_by_default(self):
        self.set_policy(proactive_per_day=0)
        self.assertEqual(self.tick(T0 + 3 * 3600), "")

    def test_wakes_after_idle_with_budget(self):
        self.assertEqual(self.tick(T0 + 1800), "")             # まだ 1 時間たっていない
        self.assertEqual(self.tick(T0 + 3601), "wake: proactive")
        prompt = (self.d / ".goalie" / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("今回は調べものの回", prompt)
        self.propose({"kind": "note", "body": "仕様書の 3 章に前提がある"})
        self.act(T0 + 3601)
        self.assertEqual(self.tick(T0 + 3601 + 3601), "wake: proactive")
        self.propose({"kind": "note", "body": "x"})
        self.act(T0 + 7300)
        self.assertEqual(self.tick(T0 + 3 * 3601 + 100), "")   # 1 日 2 回まで

    def test_recent_reply_delays(self):
        run("event", "--dir", str(self.d), "--text", "続けて")
        self.assertEqual(self.tick(T0 + 60), "wake: events")
        self.propose({"kind": "note", "body": "x"})
        self.act(T0 + 60)
        with mock.patch.object(dot, "now_ts", return_value=T0 + 3000):
            run("event", "--dir", str(self.d), "--text", "もう一つ")
        state = self.state()
        state["last_event_ts"] = T0 + 3000
        (self.d / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.tick(T0 + 3100), "wake: events")  # 返事は返事として起こす

    def test_quiet_hours_and_upcoming_wait_block(self):
        self.set_policy(quiet_hours="00:00-23:59")
        self.assertEqual(self.tick(T0 + 3601), "")
        self.set_policy(quiet_hours="")
        state = self.state()
        state["next_wake"] = T0 + 3601 + 600                    # もうすぐ起きる予定
        (self.d / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.tick(T0 + 3601), "")

    def test_only_read_only_proposals(self):
        self.assertEqual(self.tick(T0 + 3601), "wake: proactive")
        self.propose({"kind": "note", "body": "読んだ"},
                     {"kind": "ask", "question": "A ですか"},
                     {"kind": "act", "action": "hub.comment", "body": "勝手な報告"},
                     {"kind": "done", "evidence": []})
        text = self.act(T0 + 3601)
        self.assertIn("質問 q1", text)
        self.assertNotIn("勝手な報告", text)
        results = [json.loads(x)["result"] for x in (self.d / "journal.jsonl").read_text(encoding="utf-8").splitlines()
                   if json.loads(x).get("kind") == "proposal"][-4:]
        self.assertEqual(results, ["done", "done", "denied", "denied"])
        self.assertEqual(self.state()["status"], "active")


class HookTest(Base):
    def hook(self, name: str, args, cwd: "Path | None" = None) -> "str | None":
        payload = {"toolName": name, "toolArgs": json.dumps(args) if not isinstance(args, str) else args,
                   "cwd": str(cwd or self.d)}
        code, out = run("hook", stdin=json.dumps(payload))
        self.assertEqual(code, 0)
        if not out:
            return None
        data = json.loads(out)
        self.assertEqual(data["permissionDecision"], "deny")
        return data["permissionDecisionReason"]

    def test_outside_a_dot_does_nothing(self):
        self.assertIsNone(self.hook("bash", {"command": "rm -rf /"}, cwd=self.root))

    def test_writes_inside_only(self):
        self.assertIsNone(self.hook("create", {"path": "drafts/plan.md", "file_text": "x"}))
        self.assertIsNone(self.hook("edit", {"path": str(self.d / "goal.md"), "old_str": "a", "new_str": "b"}))
        self.assertIn("外には書けません", self.hook("create", {"path": str(self.root / "x.md"), "file_text": "x"}))
        self.assertIn("外には書けません", self.hook("create", {"path": "../x.md", "file_text": "x"}))
        for protected in ("policy.json", "state.json", "journal.jsonl", ".dot/prompt.md", ".goalie/prompt.md",
                          ".github/hooks/agent-dots.json", ".copilot-jobs/pending/a.md"):
            self.assertIn("変えられません", self.hook("edit", {"path": protected, "new_str": "x"}), protected)
        self.assertIsNone(self.hook("create", {"path": ".copilot-jobs/tmp/1.report.md", "file_text": "ok"}))

    def test_shell_allows_new_job_entry_points(self):
        for command in (
            r'"C:\Python\python.exe" "C:\tools\goalie\runner.py" done --dir "C:\work\dots\x" --id 1 --status ok',
            r'"C:\Python\python.exe" "C:\tools\goalie\goalie.py" job next --dir "C:\work\dots\x"',
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.hook("powershell", {"command": command}))

    def test_shell_rejects_job_script_name_suffixes(self):
        for command in (
            "python3 /x/evilrunner.py next --dir /w",
            "python3 x/my_goalie.py job next --dir /w",
        ):
            with self.subTest(command=command):
                self.assertIn("許していません", self.hook("bash", {"command": command}))

    def test_str_replace_editor_and_patch(self):
        self.assertIsNone(self.hook("str_replace_editor", {"command": "view", "path": "goal.md"}))
        self.assertIn("変えられません", self.hook("str_replace_editor", {"command": "create", "path": "policy.json"}))
        patch = "*** Begin Patch\n*** Update File: notes/a.md\n*** Add File: ../../evil.txt\n*** End Patch"
        self.assertIn("外には書けません", self.hook("apply_patch", {"input": patch}))
        self.assertIn("書き込み先が分からない", self.hook("write", {"content": "x"}))

    def test_reads(self):
        self.assertIsNone(self.hook("view", {"path": "goal.md"}))
        self.assertIsNone(self.hook("grep", {"pattern": "x"}))                    # 道が無ければこのフォルダ
        outside = self.root / "specs"
        outside.mkdir()
        self.assertIn("外は読めません", self.hook("view", {"path": str(outside / "a.md")}))
        self.set_policy(tools={"read_allow": [str(outside)]})
        self.assertIsNone(self.hook("view", {"path": str(outside / "a.md")}))

    def test_shell_allowlist(self):
        self.assertIsNone(self.hook("powershell", {"command": JOB}))
        self.assertIsNone(self.hook("powershell", {"command": "& " + JOB}))
        self.assertIsNone(self.hook("bash", {"command": "python3 /x/copilot_app_job.py next --dir /w"}))
        self.assertIn("許していません", self.hook("bash", {"command": "curl https://evil.example"}))
        self.assertIn("許していません", self.hook("bash", {"command": JOB + "; curl x"}))
        self.assertIn("許していません", self.hook("bash", {"command": JOB + " | tee x"}))
        self.assertIn("許していません", self.hook("bash", {"command": "python3 x/copilot_app_job.py done $(whoami)"}))
        self.set_policy(tools={"shell_allow": [r"^git (status|log)\b"]})
        self.assertIsNone(self.hook("bash", {"command": "git status"}))
        self.assertIn("許していません", self.hook("bash", {"command": "git push"}))
        self.set_policy(tools={"shell": "allow"})
        self.assertIsNone(self.hook("bash", {"command": "git push"}))
        self.set_policy(tools={"shell": "deny"})
        self.assertIn("許していません", self.hook("bash", {"command": JOB}))

    def test_web(self):
        self.assertIn("Web は許していません", self.hook("web_fetch", {"url": "https://example.com/a"}))
        self.set_policy(tools={"web_allow": ["example.com"]})
        self.assertIsNone(self.hook("web_fetch", {"url": "https://docs.example.com/a"}))
        self.assertIn("Web は許していません", self.hook("web_fetch", {"url": "https://example.com.evil.net/"}))
        self.assertIn("Web は許していません", self.hook("web_fetch", {"url": "https://user@evil.net/"}))
        self.set_policy(tools={"web_allow": ["*"]})
        self.assertIsNone(self.hook("web_fetch", {"url": "https://any.net/"}))

    def test_other_tools(self):
        self.assertIsNone(self.hook("report_intent", {"intent": "x"}))
        self.assertIn("ツールは許していません", self.hook("github-mcp-server/create_issue", {"title": "x"}))
        self.assertIn("ツールは許していません", self.hook("mystery", {}))
        self.set_policy(tools={"allow": ["mystery"], "deny": ["view"]})
        self.assertIsNone(self.hook("mystery", {}))
        self.assertIn("止めています", self.hook("view", {"path": "goal.md"}))

    def test_proactive_narrows_writes_and_shell(self):
        self.set_policy(proactive_per_day=1, proactive_idle_min=1, tools={"shell_allow": [r"^git status$"]})
        self.first_wake()
        self.assertEqual(self.tick(T0 + 120), "wake: proactive")
        self.assertIsNone(self.hook("create", {"path": "notes/x.md", "file_text": "x"}))
        self.assertIsNone(self.hook("create", {"path": "outbox/01.json", "file_text": "{}"}))
        self.assertIn("調べものの回", self.hook("create", {"path": "drafts/plan.md", "file_text": "x"}))
        self.assertIn("許していません", self.hook("bash", {"command": "git status"}))
        self.assertIsNone(self.hook("powershell", {"command": JOB}))

    def test_snake_case_payload_and_log(self):
        payload = {"tool_name": "bash", "tool_input": {"command": "curl x"}, "cwd": str(self.d / "drafts")}
        (self.d / "drafts").mkdir()
        code, out = run("hook", stdin=json.dumps(payload))
        self.assertIn("deny", out)
        log = [json.loads(x) for x in (self.d / ".goalie" / "hook.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((log[-1]["tool"], log[-1]["decision"]), ("bash", "deny"))

    def test_broken_state_denies(self):
        with mock.patch.object(dot, "decide", side_effect=RuntimeError("壊れた")):
            self.assertIn("判定に失敗", self.hook("view", {"path": "goal.md"}))

    def test_hooks_files(self):
        code, out = run("hooks", "--dir", str(self.d), "--python", "C:/Python/python.exe")
        self.assertEqual(code, 0)
        data = json.loads((self.d / ".github" / "hooks" / "goalie.json").read_text(encoding="utf-8"))
        entry = data["hooks"]["preToolUse"][0]
        self.assertEqual((data["version"], entry["type"]), (1, "command"))
        self.assertTrue(entry["powershell"].startswith("& 'C:/Python/python.exe' "))
        self.assertTrue(entry["powershell"].endswith(" hook"))
        plugin = self.root / "plugin"
        self.assertEqual(run("hooks", "--plugin", str(plugin))[0], 0)
        self.assertTrue((plugin / "plugins" / "goalie" / "hooks.json").is_file())
        market = json.loads((plugin / ".github" / "plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(market["plugins"][0]["source"], "./plugins/goalie")


if __name__ == "__main__":
    unittest.main()
