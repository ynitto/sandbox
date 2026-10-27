"""Moltbook の振り返り投稿（retro）の回帰テスト — フック・書き込み・CI のファイル化。

設計: docs/designs/moltbook-retro-design.md
計画: docs/plans/2026-10-26-moltbook-retro-plan.md（P1・P2・P5）

GitLab へは一切繋がず（クライアントをスタブに差し替え）、状態は一時フォルダ（MOLTBOOK_HOME）に置く。
実行: python3 -m unittest discover -s .github/skills/moltbook-use/tests
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load("moltbook_hook", SKILL / "scripts" / "moltbook_hook.py")
sys.modules["moltbook_hook"] = hook
mb = _load("moltbook", SKILL / "scripts" / "moltbook.py")
sys.modules["moltbook"] = mb
harvest = _load("moltbook_ci_harvest", SKILL / "ci" / "moltbook_ci_harvest.py")
from gitlab_api import GitLabError  # noqa: E402

MOJIBAKE = {"iid": 123, "kind": "bug", "status": "open", "name": "kiro-wsl-mojibake",
            "title": "kiro-cli: WSL で日本語が文字化けする", "keywords": ["kiro-cli", "文字化け", "UnicodeDecodeError"],
            "sigs": [], "fix": "chcp 65001 を実行してから起動する", "goods": 3, "hint": True, "path": ""}


class _Client:
    """呼ばれた操作を記録するだけのスタブ。GitLab へは繋がない。"""

    def __init__(self, notes=None, fail=None):
        self.calls: list = []
        self.notes = notes or {}
        self.fail = fail

    def _maybe_fail(self):
        if self.fail:
            raise GitLabError(self.fail)

    def create_issue(self, title, description, labels):
        self._maybe_fail()
        self.calls.append(("issue", title, description, labels))
        return {"iid": 500}

    def create_note(self, iid, body):
        self._maybe_fail()
        self.calls.append(("note", iid, body))
        self.notes.setdefault(iid, []).append({"body": body})
        return {"id": 1}

    def update_issue(self, iid, **kw):
        self.calls.append(("update", iid, kw))
        return {}

    def list_notes(self, iid):
        return self.notes.get(iid, [])

    def search(self, scope, term, **kw):
        self.calls.append(("search", term))
        return []


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"MOLTBOOK_HOME": str(self.home), "MOLTBOOK_NODE_ID": "pc-a"})
        self.env.start()
        self.popen = mock.patch.object(hook.subprocess, "Popen")   # send を裏で起動しない
        self.popen.start()

    def tearDown(self):
        self.popen.stop()
        self.env.stop()
        self.tmp.cleanup()

    def index(self, *items):
        (self.home / "index.json").write_text(json.dumps({"items": list(items)}, ensure_ascii=False), encoding="utf-8")


class HookTest(_Home):
    def test_no_events_means_no_retro(self):
        self.assertEqual(hook.run_hook("stop", "copilot", {"sessionId": "s"}), ("", 0))

    def test_copilot_failure_then_stop_blocks_once(self):
        hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "bash", "error": "Error: boom"}, True)
        out, _ = hook.run_hook("stop", "copilot", {"sessionId": "s"})
        reason = json.loads(out)
        self.assertEqual(reason["decision"], "block")
        self.assertIn("ツールの失敗: bash", reason["reason"])
        self.assertIn("retro --session s", reason["reason"])
        # 振り返りのターンの終わり（stop_hook_active）と、新しい出来事の無い終わりでは止めない
        self.assertEqual(hook.run_hook("stop", "copilot", {"sessionId": "s", "stop_hook_active": True}), ("", 0))
        self.assertEqual(hook.run_hook("stop", "copilot", {"sessionId": "s"}), ("", 0))

    def test_retro_prompts_are_capped(self):
        for i in range(5):
            hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": f"t{i}", "error": "Error"}, True)
            hook.run_hook("stop", "copilot", {"sessionId": "s"})
        self.assertEqual(hook.load_session("s")["retro_prompts"], 3)

    def test_kiro_v2_prefixes_next_prompt(self):
        fail = {"session_id": "k", "tool_name": "execute_bash", "tool_input": {"command": "x"},
                "tool_response": {"success": False, "error": ["fatal: boom"]}}
        hook.run_hook("tool", "kiro-v2", fail)
        self.assertEqual(hook.run_hook("stop", "kiro-v2", {"session_id": "k"}), ("", 0))
        out, _ = hook.run_hook("prompt", "kiro-v2", {"session_id": "k", "prompt": "続けて"})
        self.assertTrue(out.startswith("（返事の前に、前のターンについて）MOLTBOOK RETRO"))
        out, _ = hook.run_hook("prompt", "kiro-v2", {"session_id": "k", "prompt": "次"})
        self.assertNotIn("MOLTBOOK RETRO", out)

    def test_kiro_nonzero_exit_status_is_a_failure(self):
        resp = {"success": True, "result": [{"exit_status": "1", "stderr": "error: no such file"}]}
        hook.run_hook("tool", "kiro-v2", {"session_id": "k", "tool_name": "execute_bash", "tool_response": resp})
        self.assertEqual(hook.load_session("k")["events"][0]["what"], "ツールの失敗: execute_bash")

    def test_prompt_words_are_recorded_and_copilot_submitted_prints_nothing(self):
        out, _ = hook.run_hook("prompt", "copilot", {"sessionId": "s", "prompt": "違う、そうじゃなくて"})
        self.assertEqual(out, "")
        hook.run_hook("prompt", "copilot", {"sessionId": "s", "prompt": "これはみんなに共有して"})
        self.assertEqual([e["what"] for e in hook.load_session("s")["events"]], ["言い直し", "共有の依頼"])

    def test_copilot_transformed_prompt_gets_hints(self):
        self.index(MOJIBAKE)
        payload = {"sessionId": "s", "prompt": "p", "transformedPrompt": "kiro-cli の出力が文字化けする"}
        out, _ = hook.run_hook("prompt", "copilot", payload)
        text = json.loads(out)["modifiedTransformedPrompt"]
        self.assertTrue(text.startswith("kiro-cli の出力が文字化けする\n\n[Moltbook]"))
        self.assertIn("#123 [bug]", text)
        # 同じ投稿は 1 セッションに 1 回だけ
        self.assertEqual(hook.run_hook("prompt", "copilot", payload), ("", 0))

    def test_match_needs_two_keywords_or_a_signature(self):
        items = [MOJIBAKE, {**MOJIBAKE, "iid": 9, "keywords": ["review", "差分"], "sigs": ["abcd1234"]}]
        self.assertEqual(hook.match(items, "文字化けだけ"), [])
        self.assertEqual([i["iid"] for i in hook.match(items, "KIRO-CLI で 文字化け")], [123])
        self.assertEqual([i["iid"] for i in hook.match(items, "", ["abcd1234"])], [9])
        self.assertEqual(hook.match(items, "kiro-clix で文字化け"), [], "英数字は語の境目で見る")

    def test_hint_line_does_not_repeat_the_kind(self):
        line = hook.hint_line({**MOJIBAKE, "title": "[bug] kiro-cli: WSL で日本語が文字化けする"})
        self.assertTrue(line.startswith("- #123 [bug] kiro-cli: WSL"))
        self.assertEqual(harvest.record_path({"iid": 5, "status": "filed", "topic": "general", "title": "[tip] 題"}),
                         "knowledge/general/5-題.md")

    def test_error_sig_ignores_numbers_and_paths(self):
        a = hook.error_sig("Traceback\nUnicodeDecodeError: 'cp932' codec at byte 0x83 in /home/a/x.py line 12")
        b = hook.error_sig("Traceback\nUnicodeDecodeError: 'cp932' codec at byte 0x9f in /tmp/y.py line 99")
        self.assertEqual(a, b)
        self.assertNotEqual(a, hook.error_sig("PermissionError: denied"))

    def test_worked_and_opened_signals_are_queued_at_end(self):
        err = "UnicodeDecodeError: 'cp932' codec can't decode"
        self.index({**MOJIBAKE, "sigs": [hook.error_sig(err)]})
        out, code = hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "bash", "error": err}, True)
        self.assertEqual(code, 2)
        self.assertIn("#123", out)
        hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "bash",
                                          "toolArgs": {"command": 'python "moltbook.py" show --iid 123'}})
        hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "bash", "toolArgs": {"command": "ok"}})
        self.assertEqual(hook.load_session("s")["verdict"], {"123": "worked"})
        hook.run_hook("end", "copilot", {"sessionId": "s"})
        self.assertEqual(sorted(p.name for p in (self.home / "pending").iterdir()),
                         ["sig-123-opened.json", "sig-123-worked.json"])
        hook.run_hook("end", "copilot", {"sessionId": "s"})   # 二度は置かない
        self.assertEqual(len(list((self.home / "pending").iterdir())), 2)

    def test_same_failure_after_opening_is_failed(self):
        err = "fatal: unable to access remote"
        self.index({**MOJIBAKE, "sigs": [hook.error_sig(err)]})
        hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "git", "error": err}, True)
        hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "bash",
                                          "toolArgs": "moltbook.py show --iid 123"})
        hook.run_hook("tool", "copilot", {"sessionId": "s", "toolName": "git", "error": err}, True)
        self.assertEqual(hook.load_session("s")["verdict"], {"123": "failed"})

    def test_ignored_repository(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / ".moltbook-ignore").write_text("", encoding="utf-8")
        payload = {"sessionId": "s", "cwd": str(repo), "toolName": "bash", "error": "Error"}
        hook.run_hook("tool", "copilot", payload, True)
        self.assertEqual(hook.run_hook("stop", "copilot", {"sessionId": "s"}), ("", 0))

    def test_start_gives_the_two_line_guide(self):
        out, _ = hook.run_hook("start", "copilot", {"sessionId": "s", "cwd": str(self.home)})
        self.assertIn("search --query", json.loads(out)["additionalContext"])

    def test_main_never_fails_on_bad_input(self):
        with mock.patch.object(sys, "stdin") as stdin, mock.patch.object(sys, "stdout"):
            stdin.isatty.return_value = False
            stdin.read.return_value = "{not json"
            self.assertEqual(hook.main(["stop", "--client", "copilot"]), 0)


def _args(**kw):
    base = dict(kind="bug", name="kiro-wsl-mojibake", title="kiro-cli: WSL で日本語が文字化けする",
                cause="コードページが 932 のまま（根拠: UnicodeDecodeError）", fix="chcp 65001 を実行してから起動する",
                keywords="kiro-cli,文字化け,UnicodeDecodeError", tools=None, same=None, answers=None, topic=None,
                session=None, list=False, limit=20, label="default", dry_run=False)
    base.update(kw)
    return Namespace(**base)


class RetroTest(_Home):
    def run_retro(self, client, **kw):
        self.index()   # 新しい索引（GitLab の検索に落ちない）
        with mock.patch.object(mb, "_client", return_value=client), mock.patch("builtins.print") as p:
            code = mb.cmd_retro(_args(**kw))
        return code, " ".join(str(c.args[0]) for c in p.call_args_list)

    def test_validation(self):
        self.assertIn("--name", mb.validate_retro(_args(name="Bad Name")))
        self.assertIn("--title", mb.validate_retro(_args(title="あ" * 41)))
        self.assertIn("3〜5", mb.validate_retro(_args(keywords="a,b")))
        self.assertIn("3 字以上", mb.validate_retro(_args(keywords="設定,差分,修正")))
        self.assertIn("--fix", mb.validate_retro(_args(fix="")))
        self.assertIsNone(mb.validate_retro(_args(kind="question", fix="")))
        self.assertIsNone(mb.validate_retro(_args(answers=5, name=None, title=None)))

    def test_body_is_one_line_per_field(self):
        title, body = mb.build_retro({"kind": "bug", "name": "x", "title": "題", "cause": "原因", "fix": "回避",
                                      "env": "kiro-cli 2.34.0 / WSL", "keywords": ["a", "bcd", "e"],
                                      "date": "2026-10-26", "sig": "3f2a91c0"})
        self.assertEqual(title, "[bug] 題")
        self.assertEqual(body, "- 原因: 原因\n- 回避: 回避\n- 環境: kiro-cli 2.34.0 / WSL\n- キーワード: a, bcd, e\n"
                               "- 起きた回: 2026-10-26\n\n<!-- moltbook:retro:v1 name=x sig=3f2a91c0 -->")
        back = mb.parse_retro({"description": body})
        self.assertEqual((back["name"], back["sigs"], back["keywords"], back["fix"]), ("x", ["3f2a91c0"], ["a", "bcd", "e"], "回避"))

    def test_duplicate_order(self):
        item = {"kind": "bug", "name": "n", "sig": "s1", "keywords": ["a", "b", "c"], "same": None}
        items = [{"iid": 1, "kind": "bug", "name": "other", "sigs": ["s1"], "keywords": []},
                 {"iid": 2, "kind": "bug", "name": "n", "sigs": [], "keywords": []},
                 {"iid": 3, "kind": "bug", "name": "", "sigs": [], "keywords": ["a", "b", "x"]}]
        self.assertEqual(mb.find_duplicate(items, item)["iid"], 2)
        self.assertEqual(mb.find_duplicate(items[:1] + items[2:], item)["iid"], 1)
        self.assertEqual(mb.find_duplicate(items[2:], {**item, "sig": ""})["iid"], 3)
        self.assertIsNone(mb.find_duplicate(items[2:], {**item, "sig": "", "kind": "knowledge"}))

    def test_new_post(self):
        client = _Client()
        code, out = self.run_retro(client)
        self.assertEqual((code, out), (0, "OK #500 を立てました"))
        _, title, desc, labels = client.calls[0]
        self.assertEqual(title, "[bug] kiro-cli: WSL で日本語が文字化けする")
        self.assertIn("name=kiro-wsl-mojibake", desc)
        self.assertEqual(labels, ["moltbook:post", "moltbook:bug", "moltbook:topic:general"])

    def test_tools_line(self):
        client = _Client()
        self.run_retro(client, tools="kiro-cli, gitlab-idd,")
        self.assertIn("- 関連ツール: kiro-cli, gitlab-idd\n- キーワード:", client.calls[0][2])
        self.assertEqual(mb.with_tools("本文\n", "github-mcp"), "本文\n\n関連ツール: github-mcp")
        self.assertEqual(mb.with_tools("本文", " , "), "本文")

    def test_same_name_is_plus_one_and_fixed_bug_is_recurrence(self):
        client = _Client()
        with mock.patch.object(hook, "load_index", return_value=[MOJIBAKE]):
            _, out = self.run_retro(client)
        self.assertEqual(out, "OK #123 に +1 しました")
        self.assertIn("<!-- moltbook:plusone:pc-a -->", client.calls[0][2])
        client = _Client()
        with mock.patch.object(hook, "load_index", return_value=[{**MOJIBAKE, "status": "fixed"}]):
            self.run_retro(client)
        self.assertIn("- 再発: #123", client.calls[0][2])

    def test_answer(self):
        client = _Client()
        _, out = self.run_retro(client, kind="question", answers=7, name=None, title=None)
        self.assertEqual(out, "OK #7 に答えました")
        self.assertEqual(client.calls[1], ("update", 7, {"add_labels": ["moltbook:answered"],
                                                         "remove_labels": ["moltbook:open"]}))

    def test_secret_is_never_posted(self):
        client = _Client()
        fake_token = "glpat" + "-" + "x" * 24   # 実物に見えないよう、実行時に組み立てる
        _, out = self.run_retro(client, fix=f"{fake_token} を使う")
        self.assertTrue(out.startswith("SKIP"))
        self.assertEqual(client.calls, [])

    def test_offline_goes_to_pending(self):
        _, out = self.run_retro(_Client(fail="GitLab API への接続に失敗しました: timeout"))
        self.assertEqual(out, "OK 送れなかったので後で送ります")
        self.assertEqual(len(list((self.home / "pending").glob("retro-*.json"))), 1)

    def test_posts_per_session_are_capped(self):
        sess = hook.load_session("s")
        sess["posts"] = 3
        hook.save_session("s", sess)
        client = _Client()
        _, out = self.run_retro(client, session="s")
        self.assertTrue(out.startswith("SKIP"))
        self.assertEqual(client.calls, [])

    def test_send_posts_signals_once(self):
        hook._write_json(self.home / "pending" / "sig-123-worked.json", {"iid": 123, "kind": "worked", "at": "2026-10-26T10:00:00"})
        self.index()
        client = _Client()
        fake = mock.MagicMock()
        fake.from_config.return_value = client
        with mock.patch.dict(sys.modules, {"gitlab_api": mock.MagicMock(GitLabClient=fake, GitLabError=GitLabError)}), \
                mock.patch.object(hook, "_record_versions"):
            hook.send(False)
            hook._write_json(self.home / "pending" / "sig-123-worked.json", {"iid": 123, "kind": "worked", "at": "x"})
            hook.send(False)
        notes = [c for c in client.calls if c[0] == "note"]
        self.assertEqual(len(notes), 1)
        self.assertIn("<!-- moltbook:signal:worked:pc-a -->", notes[0][2])
        self.assertEqual(list((self.home / "pending").iterdir()), [])


NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)


def _issue(iid, kind="knowledge", hours=30, state="opened", labels=(), up=0, desc=""):
    lab = {"bug": [mb.L_POST, mb.L_BUG], "knowledge": [mb.L_POST, mb.L_KNOWLEDGE],
           "question": [mb.L_POST, mb.L_QUESTION]}[kind] + list(labels)
    created = (NOW - dt.timedelta(hours=hours)).isoformat()
    return {"iid": iid, "title": f"題{iid}", "labels": lab, "state": state, "upvotes": up,
            "created_at": created, "description": desc or "- 原因: x\n- キーワード: abc, def, ghi\n\n"
            f"<!-- moltbook:retro:v1 name=n{iid} sig=s{iid} -->"}


def _a(issue, notes=()):
    return harvest.analyze(issue, list(notes), NOW, dwell_hours=24, unanswered_days=14, stale_days=90)


class HarvestTest(unittest.TestCase):
    def test_filed_by_time_without_goods(self):
        self.assertTrue(_a(_issue(1))["eligible"])
        self.assertFalse(_a(_issue(1, hours=2))["eligible"])

    def test_negative_signals_block_filing(self):
        self.assertFalse(_a(_issue(1, labels=["moltbook:flagged"]))["eligible"])
        notes = [{"body": f"<!-- moltbook:signal:failed:pc{i} -->"} for i in range(3)]
        self.assertTrue(_a(_issue(1), notes)["negative"])
        notes.append({"body": "<!-- moltbook:plusone:pc9 -->"})
        self.assertTrue(_a(_issue(1), notes)["negative"], "効かなかった 3 − (+1 1) = 2 はまだ否定")
        notes.append({"body": "<!-- moltbook:signal:worked:pc8 -->"})
        self.assertFalse(_a(_issue(1), notes)["negative"], "3 − 2 = 1 なら否定ではない")

    def test_questions(self):
        self.assertFalse(_a(_issue(1, "question"))["eligible"])
        self.assertTrue(_a(_issue(1, "question", labels=[mb.L_ANSWERED]))["eligible"])
        a = _a(_issue(1, "question", hours=15 * 24))
        self.assertEqual((a["status"], a["eligible"]), ("unanswered", True))
        self.assertEqual(harvest.record_path(a), "knowledge/unanswered/1-題1.md")

    def test_bug_status(self):
        self.assertEqual(_a(_issue(1, "bug", state="closed"))["status"], "fixed")
        self.assertEqual(_a(_issue(1, "bug"))["status"], "filed")

    def test_score_counts_distinct_nodes(self):
        notes = [{"body": "<!-- moltbook:plusone:pc1 -->"}, {"body": "<!-- moltbook:plusone:pc1 -->"},
                 {"body": "<!-- moltbook:signal:worked:pc2 -->"}, {"body": "<!-- moltbook:signal:opened:pc2 -->"}]
        self.assertEqual(_a(_issue(1, up=1), notes)["goods"], 1 + 1 + 2 + 0.5)

    def test_stale_items_leave_hints(self):
        self.assertFalse(_a(_issue(1, hours=91 * 24))["hint"])
        self.assertTrue(_a(_issue(1, hours=91 * 24, up=1))["hint"])

    def test_run_is_idempotent(self):
        issues = [_issue(1), _issue(2, "bug"), _issue(3, "question"), _issue(4, hours=1)]
        client = _Client()
        client.list_issues = lambda **kw: issues
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tally = harvest.run(client, repo, dwell_hours=24, unanswered_days=14, stale_days=90,
                                max_items=50, dry_run=False, now=NOW)
            self.assertEqual(tally["harvested"], 2)
            closes = [c for c in client.calls if c[0] == "update"]
            self.assertEqual([c[1] for c in closes], [1], "バグ報告は開けておく")
            first = {p.relative_to(repo).as_posix(): p.read_text(encoding="utf-8") for p in repo.rglob("*") if p.is_file()}
            self.assertIn("knowledge/general/1-題1.md", first)
            self.assertIn("knowledge/general/2-題2.md", first)
            self.assertNotIn("knowledge/general/4-題4.md", first)
            index = json.loads(first["knowledge/index.json"])
            self.assertEqual([i["iid"] for i in index["items"]], [1, 2, 3, 4])
            client.calls.clear()
            harvest.run(client, repo, dwell_hours=24, unanswered_days=14, stale_days=90,
                        max_items=50, dry_run=False, now=NOW)
            second = {p.relative_to(repo).as_posix(): p.read_text(encoding="utf-8") for p in repo.rglob("*") if p.is_file()}
            self.assertEqual(first, second)
            self.assertEqual(client.calls, [], "2 回目は印も close も付けない")
            issues[0]["labels"].append("moltbook:wrong")   # ファイル化のあとで誤りと分かった
            tally = harvest.run(client, repo, dwell_hours=24, unanswered_days=14, stale_days=90,
                                max_items=50, dry_run=False, now=NOW)
            self.assertEqual(tally["withdrawn"], 1)
            self.assertFalse((repo / "knowledge/general/1-題1.md").exists())
            self.assertNotIn("題1", (repo / "knowledge/INDEX.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
