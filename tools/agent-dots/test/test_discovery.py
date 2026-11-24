"""Discovery の収集、評価、採否、登録を確認する。"""
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discovery
import dot


NOW = dt.datetime(2026, 11, 24, 12, tzinfo=dt.timezone.utc)


class DiscoveryTests(unittest.TestCase):
    def test_root_key_matches_resolved_path_key(self):
        root = self.root / "real"
        root.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        self.assertEqual(discovery._root_key(alias), discovery.path_key(root))
        self.assertEqual(discovery._root_key(alias / "missing"), discovery.path_key(root / "missing"))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.goal = self.root / "goal"
        self.write("goal/state.json", "{}")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def json(self, name, value):
        return self.write(name, json.dumps(value, ensure_ascii=False))

    def jsonl(self, name, values):
        return self.write(name, "".join(json.dumps(value) + "\n" for value in values))

    def kinds(self, collect, *paths, **kwargs):
        return {row["kind"] for row in collect(list(paths), NOW, **kwargs)}

    def harvest(self, now=NOW):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = dot.main(["harvest", "--dir", str(self.goal), "--now", str(now.timestamp())])
        return code, err.getvalue()

    def policy(self, collectors=None):
        return self.json("goal/policy.json", {"discovery": {"enabled": True, "collectors": collectors or {}}})

    def test_defaults_and_invalid_policy(self):
        config = discovery.parse_policy({}, self.goal)
        self.assertFalse(config["enabled"])
        self.assertEqual(config["proposals_per_day"], 5)
        self.assertEqual(config["auto_execute"], [])
        self.assertEqual(config["require_approval"], ["medium以上", "external"])
        self.assertEqual({name for name, source in config["collectors"].items() if source["enabled"]},
                         {"agent_dots", "plans", "skill_feedback"})
        self.assertEqual(config["collectors"]["agent_dots"]["paths"], [self.goal])
        self.assertEqual(self.harvest()[0], 0)
        self.assertFalse((self.goal / ".dot").exists())
        for change in (None, {"enabled": 1}, {"proposals_per_day": True}, {"proposals_per_day": -1},
                       {"auto_execute": ["unknown"]}, {"require_approval": [False]},
                       {"collectors": {"unknown": {}}}, {"collectors": {"plans": {"paths": "./"}}},
                       {"collectors": {"vault": {"mode": "wrong"}}}, {"routine_dir": []}):
            with self.subTest(change=change):
                self.json("goal/policy.json", {"discovery": change})
                self.assertEqual(self.harvest()[0], 2)
        (self.goal / "state.json").unlink()
        self.assertEqual(self.harvest()[0], 2)

    def test_agent_records_thresholds_and_safe_locations(self):
        note = self.write("goal/notes/today.md", "- [x] closed\n- [ ] confidential-text\n")
        runs = []
        for i, code in enumerate((1, 0, 0), 1):
            runs.append({"kind": "run", "action": "run", "proposal": {"name": "daily"}, "code": code, "at": i,
                         "output": "confidential-text"})
            self.json(f"goal/.dot/runs/daily/r{i}.json", {"mode": "run", "ok": code == 0, "finished": i})
        self.json("goal/.dot/runs/daily/r4.json", {"mode": "trial", "ok": False, "finished": 4})
        self.jsonl("goal/journal.jsonl", runs)
        self.json("goal/.dot/routines-review.json", {"findings": [{"action": "queued", "kind": "ignored"}]})
        self.assertEqual(self.kinds(discovery.agent_dots, self.goal), {"open_note"})
        runs.extend([{"kind": "run", "action": "run", "target": "other", "code": 1},
                     {"kind": "run", "action": "run", "proposal": {"name": "daily"}, "code": 1, "at": 4},
                     {"kind": "run", "action": "run", "proposal": {"name": "daily"}, "code": 1, "at": 5}])
        self.jsonl("goal/journal.jsonl", runs)
        for i in (5, 6):
            self.json(f"goal/.dot/runs/daily/r{i}.json", {"mode": "run", "ok": False, "finished": i})
        self.json("goal/.dot/routines-review.json", {"findings": [
            {"action": "needs_review", "kind": "contradiction", "machine": "daily", "runs": ["r5", "r6"],
             "detail": "confidential-text"},
        ]})
        rows = discovery.agent_dots([self.goal], NOW)
        self.assertEqual({r["kind"] for r in rows}, {"open_note", "repeated_action_failure", "routine_failure", "routine_review"})
        self.assertEqual([Path(r["source_path"]).stem for r in rows if r["kind"] == "routine_failure"], ["r5", "r6"])
        self.assertIn("contradiction / daily / r5 / r6", [r["locations"][0] for r in rows])
        self.assertNotIn("confidential-text", str(rows))
        note.write_text("- [x] completed", encoding="utf-8")
        self.assertNotIn("open_note", self.kinds(discovery.agent_dots, self.goal))

    def test_plans_require_an_explicit_current_state(self):
        for name, text in {
            "a": "# Plan\n\n> 状態：設計済み・未実装\n",
            "b": "**状態: 未着手**\n",
            "c": "状態: 未着手（中止）\n",
            "d": "状態: 設計案\n## 未着手\n",
            "e": "# Plan\n" + "\n" * 20 + "状態: 未着手",
            "f": "状態: 未着手\n状態: 完了\n",
        }.items():
            self.write(f"repo/docs/plans/section/{name}.md", text)
        self.write("repo/README.md", "状態: 未着手")
        rows = discovery.plans([self.root / "repo"], NOW)
        self.assertEqual([Path(r["source_path"]).stem for r in rows], ["a", "b"])

    def test_feedback_digest_threshold_pending_and_streak(self):
        def entry(day, verdict, **extra):
            return {"timestamp": f"2026-11-{day:02d}T00:00:00Z", "verdict": verdict, "note": "private", **extra}

        registry = self.json("skill-registry.json", {"installed_skills": [
            {"name": "below", "feedback_history": [entry(1, "broken"), entry(2, "ok")]},
            {"name": "threshold", "feedback_history": [entry(i, "broken") for i in (1, 2, 3)]},
            {"name": "pending", "pending_refinement": True},
            {"name": "streak", "feedback_history": [entry(i, "needs-improvement", refined=True) for i in (2, 1)]},
            {"name": "one", "feedback_history": [entry(1, "needs-improvement")]},
        ]})
        rows = discovery.skill_feedback([registry], NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual([loc.split(" / ")[0] for loc in rows[0]["locations"]], ["pending", "streak", "threshold"])
        self.assertNotIn("private", str(rows))
        with patch.object(discovery.digest_module(), "default_registry_path", return_value=registry):
            sources = discovery.parse_policy({"discovery": {"enabled": True}}, self.goal)["collectors"]
        self.assertEqual(sources["skill_feedback"]["paths"], [registry])

    def test_session_and_prompt_thresholds_across_provider_roots(self):
        prompt = self.write("snippets/answer.txt", "private    request\n")
        self.write("snippets/.github/skills/myskill/SKILL.md", "---\nname: myskill\naliases: [private request]\n---\n# Skill")
        self.jsonl("copilot/test/events.jsonl", [
            {"type": "session.start", "data": {"sessionId": "cp"}},
            {"type": "user.message", "data": {"content": "private request"}},
            {"type": "user.message", "data": {"content": "private  request"}},
            {"type": "tool.execution_complete", "data": {"success": False, "toolName": "reader", "error": {"code": "E1"}}},
        ])
        self.jsonl("codex/rollout-test.jsonl", [
            {"type": "session_meta", "payload": {"id": "cx"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "private\nrequest"}},
            {"type": "event_msg", "payload": {"type": "tool_error", "tool_name": "reader", "error_code": "E1"}},
        ])
        self.jsonl("unknown/unknown.jsonl", [{"role": "user", "content": "private request"}])
        sources = [self.root / provider for provider in ("copilot", "codex", "unknown")]
        self.assertEqual(self.kinds(discovery.session_logs, *sources), set())
        self.assertEqual(self.kinds(discovery.prompt_assets, prompt, session_paths=sources), set())
        self.jsonl("claude/cc.jsonl", [
            {"type": "user", "sessionId": "cc", "message": {"role": "user", "content": [{"type": "text", "text": "private request"}]}},
            {"type": "tool_error", "sessionId": "cc", "toolName": "reader", "errorCode": "E1"},
        ])
        sources.append(self.root / "claude")
        self.assertEqual(self.kinds(discovery.session_logs, *sources), {"repeated_request", "repeated_session_error"})
        rows = discovery.prompt_assets([self.root / "snippets"], NOW, session_paths=sources)
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["kind"] for r in rows}, {"reused_prompt"})
        self.assertTrue(all("skill:myskill" in r["locations"] for r in rows))
        self.assertNotIn("private request", str(rows))

    def test_knowledge_retro_memory_and_moltbook_thresholds(self):
        doc = self.write("docs/ref.md", "[ok](good.md#h)\n[external](https://example.com/404)\n"
                         "[b](a%20b.md)\n見直し期限: 2026-11-24\n")
        self.write("docs/good.md", "# h")
        self.assertEqual(self.kinds(discovery.knowledge, doc), {"broken_doc_link"})
        doc.write_text("[ok](good.md)\n見直し期限: 2026-11-23\n", encoding="utf-8")
        self.assertEqual(self.kinds(discovery.knowledge, doc), {"doc_review_due"})
        self.write("retro/one.md", "## Try\n- [ ] open\n## Other\n- [ ] skip\n問題 ID: P-1 未解決\n")
        self.write("retro/two.md", "問題 ID: P-1 完了\n")
        self.assertEqual(self.kinds(discovery.codd_retro, self.root / "retro"), {"open_retro_action"})
        self.write("retro/two.md", "問題 ID: P-1 未解決\n")
        rows = discovery.codd_retro([self.root / "retro"], NOW)
        self.assertEqual(sum(r["kind"] == "repeat_retro_issue" for r in rows), 2)

        self.json("memory/first.json", {"id": "m1", "ratings": ["good"], "key": "timezone", "value": "UTC",
                                        "review_due": "2026-11-24"})
        self.json("memory/second.json", {"id": "m2", "ratings": ["bad"], "key": "timezone", "value": "Other",
                                         "status": "archived"})
        self.assertEqual(self.kinds(discovery.ltm_use, self.root / "memory"), set())
        self.json("memory/second.json", {"id": "m1", "ratings": ["bad"], "review_due": "2026-11-23"})
        self.json("memory/third.json", {"id": "m3", "key": "timezone", "value": "Other"})
        rows = discovery.ltm_use([self.root / "memory"], NOW)
        self.assertEqual(sum(r["kind"] == "memory_conflict" for r in rows), 3)
        self.assertEqual(sum(r["kind"] == "memory_review_due" for r in rows), 1)
        self.assertNotIn("Other", str(rows))
        page = self.write("moltbook/topic.md", "## 無関係\n- [ ] skip\n## 適用候補\n- [x] done\n")
        self.assertEqual(self.kinds(discovery.moltbook, page), set())
        self.write("moltbook/topic.md", "## 次の作業\n- [ ] open\n## 無関係\n- [ ] skip\n")
        self.assertEqual(discovery.moltbook([page], NOW)[0]["locations"], ["L2"])

    def test_git_thresholds_through_read_only_command_double(self):
        root = self.root / "repo"
        self.write("repo/change.md", "plain")
        self.write("repo/todo.md", "# TODO detail\n")
        self.write("repo/changelog/unreleased/piece.md", "- [ ] follow up")
        commits, days = 9, 89

        def run(folder, command, *args):
            self.assertEqual(folder, root)
            if command == "rev-parse":
                return os.fsencode(root) + b"\n"
            if command == "ls-files":
                return b"change.md\0todo.md\0changelog/unreleased/piece.md\0"
            rev = b"a" * 40
            if command == "blame":
                at = int((NOW - dt.timedelta(days=days)).timestamp())
                return rev + f" 1 1 1\ncommitter-time {at}\n\tTODO\n".encode()
            self.assertEqual(command, "log")
            recent = int((NOW - dt.timedelta(days=30)).timestamp())
            expired = recent - 1
            fields = [f"{i:040x} {recent}\0\nchange.md\0" for i in range(commits)]
            fields.append(f"{999:040x} {expired}\0\nchange.md\0")
            return "\0".join(fields).encode()

        with patch.object(discovery, "_git", side_effect=run):
            self.assertEqual(self.kinds(discovery.git_todo, root), {"changelog_followup"})
            commits, days = 10, 90
            self.assertEqual(self.kinds(discovery.git_todo, root), {"change_hotspot", "old_todo", "changelog_followup"})

    def test_vault_direct_thresholds_links_ambiguity_and_no_writes(self):
        vault = self.root / "vault"
        old = (NOW.date() - dt.timedelta(days=180)).isoformat()
        self.write("vault/project.md", f"---\nupdated: {old}\ntags: [common]\n---\n# Project")
        self.write("vault/2026-11-01.md", "- [ ] task  secret\n[[project]]\n#common")
        self.write("vault/2026-11-02.md", "- [ ] task secret\n[project](project.md)\n#common")
        self.write("vault/x/duplicate.md", "# Common name\n#common")
        self.write("vault/y/duplicate.md", "# Common name")
        self.write("vault/link.md", "[[duplicate]]")
        self.write("vault/orphan.md", "# orphan")

        def snapshot():
            return {p: p.read_bytes() for p in vault.rglob("*.md")}

        before = snapshot()
        rows = discovery.vault([vault], NOW, mode="files")
        self.assertEqual({r["kind"] for r in rows}, {"vault_orphan"})
        self.assertNotIn("duplicate.md", str(rows))
        self.assertEqual(snapshot(), before)
        self.write("vault/2026-11-03.md", "- [ ] task secret\n[[project#Heading|Alias]]\n#common")
        before = snapshot()
        rows = discovery.vault([vault], NOW, mode="files")
        for kind, count in (("stale_linked_note", 1), ("repeated_daily_task", 3), ("vault_tag_cluster", 5)):
            self.assertEqual(sum(r["kind"] == kind for r in rows), count)
        self.assertEqual(snapshot(), before)
        self.assertNotIn("task secret", str(rows))
        date = (NOW.date() - dt.timedelta(days=179)).isoformat()
        self.write("vault/project.md", f"---\nupdated: {date}\n---")
        self.assertNotIn("stale_linked_note", self.kinds(discovery.vault, vault, mode="files"))

    def test_vault_cli_success_empty_malformed_and_timeout(self):
        vault = self.root / "vault"
        for i in range(1, 4):
            path = self.write(f"vault/2026-11-0{i}.md", "- [ ] task")
            os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        before = {p: p.read_bytes() for p in vault.iterdir()}
        fail, empty = None, False
        fallbacks, commands = [], []

        def run(args, **kwargs):
            self.assertEqual(args[:2], ["obsidian", "vault=Work Notes"])
            self.assertEqual(kwargs["timeout"], 15)
            command = args[2]
            commands.append(command)
            if command == "search" and fail == "timeout":
                raise subprocess.TimeoutExpired(args, 15)
            if command == "search" and fail == "malformed":
                return subprocess.CompletedProcess(args, 0, "{secret")
            if command == "search" and fail == "missing":
                raise FileNotFoundError("private")
            if command == "search" and fail == "nonzero":
                return subprocess.CompletedProcess(args, 7, "", "private")
            if command == "search":
                value = [] if empty else [{"path": p.name} for p in before] + ["../external.md"]
                out = json.dumps(value)
            else:
                out = {"orphans": "", "properties": "{}", "tags": "[]", "backlinks": "[]"}[command]
            return subprocess.CompletedProcess(args, 0, out)

        def collect():
            return discovery.vault([vault], NOW, vault_name="Work Notes", runner=run,
                                   on_fallback=lambda root, reason: fallbacks.append(reason))

        self.assertEqual([r["kind"] for r in collect()], ["repeated_daily_task"] * 3)
        self.assertTrue({"search", "orphans", "tags", "properties"} <= set(commands))
        empty = True
        self.assertEqual(collect(), [])
        self.assertEqual(fallbacks, [])
        for fail in ("malformed", "timeout", "missing", "nonzero"):
            self.assertIn("vault_orphan", {r["kind"] for r in collect()})
            self.assertNotIn("secret", fallbacks[-1])
            self.assertNotIn("private", fallbacks[-1])
        self.assertEqual(len(fallbacks), 4)
        self.assertEqual({p: p.read_bytes() for p in vault.iterdir()}, before)

    def test_vault_cli_backlinks_are_distinct_and_fallback_is_journaled(self):
        vault = self.root / "vault"
        old = (NOW.date() - dt.timedelta(days=180)).isoformat()
        project = self.write("vault/project.md", f"---\nupdated: {old}\ntags: [common]\n---")
        for i in range(4):
            path = self.write(f"vault/ref-{i}.md", "#common")
            os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        before = {path: path.read_bytes() for path in vault.iterdir()}
        third = False

        def run(args, **kwargs):
            values = {"search": [], "tags": [{"tag": "#common", "count": 5}], "properties": {}}
            command = args[2]
            if command == "backlinks":
                self.assertIn("path=project.md", args)
                values[command] = ["ref-0.md", "ref-0.md", "ref-1.md", "../outside.md"]
                if third:
                    values[command].append("ref-2.md")
            output = "ref-3.md\n" if command == "orphans" else json.dumps(values[command])
            return subprocess.CompletedProcess(args, 0, output)

        options = {"vault_name": "Work Notes", "runner": run}
        self.assertEqual(self.kinds(discovery.vault, vault, **options), {"vault_tag_cluster", "vault_orphan"})
        third = True
        rows = discovery.vault([vault], NOW, **options)
        self.assertEqual([r["source_path"] for r in rows if r["kind"] == "stale_linked_note"],
                         [discovery.path_key(project)])
        self.assertEqual(sum(r["kind"] == "vault_tag_cluster" for r in rows), 5)

        self.policy({"skill_feedback": {"enabled": False},
                     "vault": {"enabled": True, "vault": "Work Notes", "paths": ["../vault"]}})
        with patch.object(discovery.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.harvest()[0], 0)
        journal = json.loads((self.goal / "journal.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((journal["collector"], journal["result"]), ("vault", "fallback"))
        self.assertEqual({path: path.read_bytes() for path in vault.iterdir()}, before)

    def test_partial_failure_within_one_collector_keeps_other_root_active(self):
        self.write("good/one.md", "状態: 未着手")
        self.write("bad/two.md", "状態: 未着手")
        self.policy({"agent_dots": {"enabled": False}, "skill_feedback": {"enabled": False},
                     "plans": {"paths": ["../good", "../bad"]}})
        self.assertEqual(self.harvest()[0], 0)
        prior = {Path(row["source_path"]).name: row for row in discovery.active_signals(self.goal)}
        read = discovery.read_text

        def restricted(path):
            if path.name == "two.md":
                raise PermissionError("private")
            return read(path)

        with patch.object(discovery, "read_text", side_effect=restricted):
            code, err = self.harvest()
        self.assertEqual(code, 1)
        self.assertIn("bad", err)
        self.assertNotIn("private", err)
        active = discovery.active_signals(self.goal)
        self.assertEqual([row["id"] for row in active], [prior["one.md"]["id"]])
        self.assertEqual(active[0]["count"], 2)

    def test_index_merge_dedup_resolves_and_preserves_failed_coverage(self):
        note = self.write("goal/notes/today.md", "- [ ] one\n- [ ] two\n")
        plan = self.write("plans/one.md", "状態: 未着手\n")
        self.policy({"agent_dots": {"paths": [".", "."]}, "plans": {"paths": ["../plans"]},
                     "skill_feedback": {"enabled": False}})
        index = self.goal / ".dot/discovery"
        self.assertEqual(self.harvest()[0], 0)
        first = {row["kind"]: row for row in discovery.active_signals(self.goal)}
        expected = discovery.path_key(note) + "\0open_note"
        self.assertEqual(first["open_note"]["id"], hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(first["open_note"]["locations"], ["L1", "L2"])

        note.write_text("- [x] one\n- [ ] two", encoding="utf-8")
        plan.unlink()
        plan.parent.rename(self.root / "plans-gone")
        later = NOW + dt.timedelta(days=1)
        code, err = self.harvest(later)
        self.assertEqual(code, 1)
        self.assertIn("plans", err)
        active = discovery.active_signals(self.goal)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], first["open_note"]["id"])
        self.assertEqual(active[0]["count"], 2)
        self.assertEqual(active[0]["first_seen"], first["open_note"]["first_seen"])
        self.assertEqual(active[0]["last_seen"], later.astimezone().isoformat(timespec="seconds"))
        self.assertEqual(active[0]["locations"], ["L2"])
        coverage = json.loads((index / "coverage.json").read_bytes())
        raw = (index / "signals.jsonl").read_bytes()
        self.assertEqual(coverage["signals_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(coverage["roots"]["plans"][plan.parent.as_posix()],
                         {"active": False, "signal_ids": [first["unstarted_plan"]["id"]]})
        journal = (self.goal / "journal.jsonl").read_text(encoding="utf-8")
        self.assertEqual(json.loads(journal)["result"], "failed")
        (index / "signals.jsonl").write_bytes(raw + b"\n")
        self.assertEqual(discovery.active_signals(self.goal), [])

        plan.parent.mkdir()
        note.write_text("- [x] two", encoding="utf-8")
        self.assertEqual(self.harvest(later)[0], 0)
        self.assertEqual(discovery.active_signals(self.goal), [])
        self.assertEqual(len((index / "signals.jsonl").read_text(encoding="utf-8").splitlines()), 2)

    def test_root_escape_hidden_credentials_and_binary_are_excluded(self):
        outside = self.write("outside.md", "- [ ] outside")
        note = self.write("goal/notes/a.md", "- [ ] normal")
        self.write("goal/notes/credentials.md", "- [ ] credential")
        self.write("goal/notes/.private.md", "- [ ] hidden")
        self.write("goal/notes/image.md", "").write_bytes(b"- [ ] binary\0")
        self.write("goal/.dot/discovery/private.md", "- [ ] output")
        try:
            (note.parent / "external.md").symlink_to(outside)
            (note.parent / "alias.md").symlink_to(note)
            (note.parent / "discovery").symlink_to(self.goal / ".dot/discovery", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作成できない")
        rows = discovery.agent_dots([self.goal], NOW)
        self.assertEqual([r["source_path"] for r in rows], [discovery.path_key(note)])
        self.assertEqual(discovery.signal_id(note, "open_note"),
                         discovery.signal_id(note.parent / "alias.md", "open_note"))

    def test_policy_changes_lock_conflict_and_save_failure(self):
        self.policy({"skill_feedback": {"enabled": False}})
        self.write("goal/notes/a.md", "- [ ] open")
        collect = discovery.harvest_sources

        def changed(config, now):
            result = collect(config, now)
            self.json("goal/policy.json", {"discovery": {"enabled": False}})
            return result

        with patch.object(discovery, "harvest_sources", side_effect=changed):
            self.assertEqual(self.harvest()[0], 1)
        self.assertFalse((self.goal / ".dot/discovery/signals.jsonl").exists())
        self.policy({"skill_feedback": {"enabled": False}})
        with patch.object(dot, "locked", side_effect=dot.Busy):
            self.assertEqual(self.harvest()[0], 3)
        with patch.object(discovery, "_atomic_pair", side_effect=OSError("disk")):
            self.assertEqual(self.harvest()[0], 1)
        self.assertEqual(self.harvest()[0], 0)


class DiscoveryActTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.goal = self.root / "goal"
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.goals = self.root / "goals"
        self.goals.mkdir()
        self.ts = NOW.timestamp()
        dot.init_goal(self.goal, "goal", self.ts)
        self.folder = discovery.index_dir(self.goal)
        self.policy = {"proactive_per_day": 3, "proactive_idle_min": 0, "discovery": {
            "enabled": True, "goal_root": str(self.goals), "auto_execute": ["readonly"],
            "collectors": {"plans": {"paths": [str(self.sources)]}, "skill_feedback": {"enabled": False}},
        }}
        self.save_policy()
        self.idle()

    def save_policy(self):
        dot.write_json(self.goal / "policy.json", self.policy)

    def idle(self):
        state = dot.load_state(self.goal)
        state.update(last_wake=dot.iso(self.ts - 60), last_wake_ts=self.ts - 60, inflight=None)
        dot.save_state(self.goal, state)

    def call(self, command, ts=None, goal=None, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            argv = [command, "--dir", str(goal or self.goal)]
            if command in ("tick", "act", "harvest"):
                argv += ["--now", str(self.ts if ts is None else ts)]
            self.assertEqual(dot.main([*argv, *args]), 0)
        return out.getvalue()

    def harvest(self, *names):
        for name in names:
            (self.sources / name).write_text("# 設計\n\n状態: 設計済み・未実装\n\nSECRET ORIGINAL\n", encoding="utf-8")
        self.call("harvest")

    def evaluate(self):
        self.assertEqual(self.call("tick"), "wake: proactive\n")
        return dot.read_json(self.folder / "evaluation.json", {})

    def finish_evaluation(self, status="ok"):
        box = self.goal / dot.JOB_BOX
        name = dt.datetime.fromtimestamp(self.ts + 1).strftime("%Y%m%d-%H%M%S%f") + "-abcdef-dot.md"
        done = box / "done" / name
        done.parent.mkdir(parents=True, exist_ok=True)
        done.touch()
        if status is not None:
            result = box / "results" / name
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(f"status: {status}\n", encoding="utf-8")

    def outbox(self, evaluation, filename="01.json", goal=None, **change):
        label = next(iter(evaluation["sources"]))
        prop = {"kind": "propose", "title": "集計を実装する",
                "why": f"{label} の設計が未実装", "sources": [label], "size": "small",
                "risk": "readonly", "suggested_route": "goal", **change}
        path = (goal or self.goal) / "outbox" / filename
        dot.write_json(path, prop)
        return path, prop

    def decision(self, proposal, status):
        proposal.update(status=status, decision={"at": dot.iso(self.ts), "by": "tester", "reason": "確認済み"})
        dot.write_json(self.folder / "proposals" / f'{proposal["id"]}.json', proposal)

    def history(self):
        return list(dot.json_lines(self.folder / "history.jsonl"))

    def proposals(self, operation, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = dot.main(["proposals", operation, "--dir", str(self.goal), *args])
        return code, out.getvalue(), err.getvalue()

    def test_proposals_list_filters_and_order(self):
        self.assertEqual(self.proposals("list", "--json"), (0, "[]\n", ""))
        for ident, status, received in (
            ("p9", "pending", "2026-11-24T12:00:00+00:00"),
            ("p2", "pending", "2026-11-24T21:00:00+09:00"),
            ("p1", "pending", "2026-11-24T11:00:00+00:00"),
            ("p3", "approved", "2026-11-24T13:00:00+00:00"),
            ("p4", "rejected", "2026-11-24T10:00:00+00:00"),
        ):
            dot.write_json(self.folder / "proposals" / f"{ident}.json", {
                "id": ident, "status": status, "received_at": received, "title": "集計する",
                "size": "small", "risk": "readonly", "suggested_route": "goal",
            })
        for options, expected in (
            ((), ["p2", "p9", "p1"]),
            (("--status", "approved"), ["p3"]),
            (("--status", "rejected"), ["p4"]),
            (("--status", "all"), ["p3", "p2", "p9", "p1", "p4"]),
        ):
            with self.subTest(options=options):
                code, out, err = self.proposals("list", "--json", *options)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual([row["id"] for row in json.loads(out)], expected)
        code, out, err = self.proposals("list")
        self.assertEqual((code, err), (0, ""))
        header, first, *_ = out.splitlines()
        self.assertEqual(header.split(), ["ID", "状態", "規模", "リスク", "ルート", "題名"])
        self.assertEqual(first.split(), ["p2", "pending", "small", "readonly", "goal", "集計する"])

    def test_proposals_show_missing_and_queue_read_failure(self):
        code, out, err = self.proposals("show", "p999")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("p999", err)
        path = self.folder / "proposals" / "p999.json"
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")
        for options in (("list", "--json"), ("show", "p999")):
            code, out, err = self.proposals(*options)
            self.assertEqual((code, out), (1, ""))
            self.assertTrue(err)

    def test_proposals_decision_validation_and_replay(self):
        for ident, operation, status, opposite in (
            ("p1", "approve", "approved", "reject"),
            ("p2", "reject", "rejected", "approve"),
        ):
            with self.subTest(operation=operation):
                path = self.folder / "proposals" / f"{ident}.json"
                pending = {"id": ident, "status": "pending", "decision": None}
                dot.write_json(path, pending)
                self.assertEqual(self.proposals(operation, ident, "--reason", " \t　")[0], 2)
                with dot.locked(self.goal), patch.object(dot, "LOCK_WAIT_SEC", 0):
                    self.assertEqual(self.proposals(operation, ident, "--reason", "確認済み")[0], 3)
                with patch.object(dot.os, "replace", side_effect=OSError("保存失敗")):
                    code, _, err = self.proposals(operation, ident, "--reason", "確認済み")
                    self.assertEqual(code, 1)
                    self.assertIn("保存失敗", err)
                self.assertEqual(dot.read_json(path, None), pending)
                with patch.object(dot, "now_ts", return_value=self.ts), patch.object(
                        dot.getpass, "getuser", return_value="tester"):
                    self.assertEqual(self.proposals(operation, ident, "--reason", " 確認済み ")[0], 0)
                record = {**pending, "status": status, "decision": {
                    "at": dot.iso(self.ts), "by": "tester", "reason": "確認済み",
                }}
                self.assertEqual(dot.read_json(path, None), record)
                with patch.object(dot, "now_ts", return_value=self.ts + 60):
                    code, out, err = self.proposals(operation, ident, "--reason", "別の理由")
                self.assertEqual((code, err), (0, ""))
                for value in record["decision"].values():
                    self.assertIn(value, out)
                self.assertEqual(self.proposals(opposite, ident, "--reason", "別の判断")[0], 1)
                self.assertEqual(dot.read_json(path, None), record)

    def test_proposals_show_evidence_and_registration_wait(self):
        self.policy["discovery"].pop("goal_root")
        self.save_policy()
        self.harvest("plan.md", "other.md")
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.outbox(evaluation, sources=["plan.md", "other.md"], why="plan.md と other.md の候補")
        self.call("act")
        proposal, = dot.proposal_queue(self.goal)
        ident = proposal["id"]
        self.assertEqual(self.proposals("approve", ident, "--reason", "内容を確認した")[0], 0)
        self.call("act")
        proposal, = dot.proposal_queue(self.goal)
        self.assertIn("goal_root", proposal["registration_reason"])
        code, out, err = self.proposals("show", ident)
        self.assertEqual((code, err), (0, ""))
        for value in (proposal["why"], "small", "readonly", "goal", "approved", "内容を確認した",
                      "登録待ち", proposal["registration_reason"]):
            self.assertIn(value, out)
        self.assertEqual(len(proposal["evidence"]), 2)
        for source in proposal["evidence"]:
            self.assertIn(source["source_path"], out)
            for location in source["locations"]:
                self.assertIn(location, out)
        code, out, err = self.proposals("show", ident, "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), proposal)

    def test_regular_wake_and_evaluation_prompt(self):
        self.harvest("plan.md")
        dot.write_json(self.goal / "inbox" / "event.json", {"text": "通常の出来事"})
        self.assertEqual(self.call("tick"), "wake: events\n")
        self.assertFalse((self.folder / "evaluation.json").exists())
        dot.write_json(self.goal / "outbox" / "note.json", {"kind": "note", "body": "通常回"})
        self.call("act")
        evaluation = self.evaluate()
        prompt = (self.goal / ".dot/prompt.md").read_text(encoding="utf-8")
        self.assertIn("unstarted_plan / plan.md", prompt)
        self.assertIn("5 件まで", prompt)
        self.assertNotIn("evaluation_id", prompt)
        self.assertNotIn("SECRET ORIGINAL", prompt)
        self.assertNotIn(str(self.sources), prompt)
        self.assertEqual(dot.load_state(self.goal)["inflight"]["evaluation_id"], evaluation["id"])
        self.assertEqual(dot.load_state(self.goal)["proactive"], {dot.today(self.ts): 1})

    def test_evaluation_gates(self):
        self.harvest("plan.md")
        hour = dt.datetime.fromtimestamp(self.ts).hour
        for key, value in (("proactive_per_day", 0), ("quiet_hours", f"{hour}:00-{(hour + 1) % 24}:00"),
                           ("wake_per_day", 1)):
            with self.subTest(key=key):
                state = dot.load_state(self.goal)
                state["wakes"] = {dot.today(self.ts): 1}
                dot.save_state(self.goal, state)
                self.policy[key] = value
                self.save_policy()
                self.assertEqual(self.call("tick"), "")
                self.policy.pop(key)
                self.policy["proactive_per_day"] = 3
                self.save_policy()
        self.policy["discovery"]["proposals_per_day"] = 0
        self.save_policy()
        self.assertEqual(self.call("tick"), "")
        self.policy["discovery"]["proposals_per_day"] = 5
        self.save_policy()
        with (self.folder / "signals.jsonl").open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(self.call("tick"), "")
        self.assertFalse((self.folder / "evaluation.json").exists())

    def test_denials_and_acceptance(self):
        self.harvest("gone.md", "unreadable.md", "valid.md", "changed.md")
        evaluation = self.evaluate()
        self.finish_evaluation()
        (self.sources / "gone.md").unlink()
        (self.sources / "unreadable.md").chmod(0)
        (self.sources / "changed.md").write_text("replacement", encoding="utf-8")
        for i, source in enumerate(("gone.md", "unreadable.md", "changed.md", "missing.md", "https://invalid.test")):
            self.outbox(evaluation, f"{i:02}.json", sources=[source], why=f"{source} の候補")
        self.outbox(evaluation, "05.json", why="出典の表記がない候補")
        self.outbox(evaluation, "06.json", size=[])
        self.outbox(evaluation, "07.json", kind="note")
        self.outbox(evaluation, "08.json", sources=["valid.md", "gone.md"], why="valid.md と gone.md")
        _, prop = self.outbox(evaluation, "09.json", sources=["valid.md"], why="valid.md の候補")
        self.assertNotIn("evaluation_id", prop)
        self.call("act")
        rows = list(dot.json_lines(self.goal / "journal.jsonl"))
        self.assertEqual([row["result"] for row in rows if row["kind"] == "proposal"],
                         ["denied"] * 9 + ["accepted"])
        self.assertEqual(len(list((self.goal / ".dot/outbox-done").glob("*.json"))), 10)
        proposal, = dot.proposal_queue(self.goal)
        self.assertEqual(proposal["status"], "pending")
        self.assertIsNone(proposal["decision"])
        self.outbox(evaluation, "99.json")
        self.call("act")
        self.assertEqual(list(dot.json_lines(self.goal / "journal.jsonl"))[-1]["result"], "denied")

    def test_midnight_and_outbox_retry(self):
        self.policy["discovery"]["proposals_per_day"] = 2
        self.save_policy()
        self.harvest("first.md", "second.md", "third.md")
        dot.write_json(self.folder / "proposals" / "prior.json",
                       {"id": "prior", "received_at": dot.iso(self.ts), "status": "rejected", "signal_ids": []})
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.assertEqual(evaluation["remaining"], 1)
        for i, label in enumerate(evaluation["sources"]):
            self.outbox(evaluation, f"{i:02}.json", sources=[label], why=f"{label} の候補")
        tomorrow = self.ts + 86400
        replace = os.replace

        def crash(src, dst):
            if Path(src).parent == self.goal / "outbox":
                raise OSError("stopped before archive")
            return replace(src, dst)

        with patch.object(dot.os, "replace", side_effect=crash), self.assertRaises(OSError):
            self.call("act", tomorrow)
        self.call("act", tomorrow)
        first, = [row for row in dot.proposal_queue(self.goal) if row["id"] != "prior"]
        self.assertEqual(first["received_at"], dot.iso(tomorrow))
        self.assertEqual(len(dot.proposal_queue(self.goal)), 2)

    def test_mailbox_outcomes_and_late_result(self):
        self.harvest("plan.md")
        self.evaluate()
        box = self.goal / dot.JOB_BOX

        def job_name(ts):
            return dt.datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S%f") + "-abcdef-dot"

        withdrawn = box / "tmp" / (job_name(self.ts + 1) + ".withdrawn.md")
        withdrawn.parent.mkdir(parents=True)
        withdrawn.touch()
        self.call("act", self.ts + 2)
        row = list(dot.json_lines(self.goal / "journal.jsonl"))[-1]
        self.assertEqual((row["result"], row["code"]), ("evaluation-failed", 3))

        self.ts += 60
        evaluation = self.evaluate()
        name = job_name(self.ts + 1) + ".md"
        running = box / "running" / name
        running.parent.mkdir()
        running.touch()
        self.call("act", self.ts + 5401)
        self.assertEqual(dot.load_state(self.goal)["inflight"]["evaluation_id"], evaluation["id"])
        self.call("tick", self.ts + 5402)
        self.assertEqual(dot.load_state(self.goal)["inflight"]["evaluation_id"], evaluation["id"])

        done = box / "done" / name
        done.parent.mkdir()
        result = box / "results" / name
        result.parent.mkdir()
        result.write_text("status: ok\n", encoding="utf-8")
        running.rename(done)
        self.call("act", self.ts + 5403)
        self.assertIsNone(dot.load_state(self.goal)["inflight"])
        self.assertEqual(list(dot.json_lines(self.goal / "journal.jsonl"))[-1]["result"], "empty")

    def test_evaluation_without_result_cannot_submit_proposal(self):
        self.harvest("plan.md")
        evaluation = self.evaluate()
        self.finish_evaluation(status=None)
        path, _ = self.outbox(evaluation)
        self.call("act")
        self.assertEqual(dot.proposal_queue(self.goal), [])
        self.assertFalse(path.exists())
        self.assertIsNone(dot.load_state(self.goal)["inflight"])
        row = list(dot.json_lines(self.goal / "journal.jsonl"))[-1]
        self.assertEqual((row["result"], row["why"]), ("denied", "評価の終了コードが確認できない"))

    def test_empty_outbox_with_unknown_evaluation_result(self):
        self.harvest("plan.md")
        evaluation = self.evaluate()
        self.call("act")
        self.assertIsNone(dot.load_state(self.goal)["inflight"])
        row = list(dot.json_lines(self.goal / "journal.jsonl"))[-1]
        self.assertEqual((row["evaluation_id"], row["result"], row["code"]),
                         (evaluation["id"], "evaluation-unknown", None))

    def test_corrupt_queue_files_do_not_block_tick_or_act(self):
        self.harvest("plan.md")
        folder = self.folder / "proposals"
        partial = {"id": "partial", "status": "approved", "received_at": dot.iso(self.ts)}
        dot.write_json(folder / "partial.json", partial)
        dot.write_json(folder / "incomplete.json", {"id": "incomplete", "status": "pending"})
        dot.write_json(folder / "bad-date.json", {**partial, "id": "bad-date", "received_at": "invalid"})
        dot.write_json(folder / "scalar.json", [])
        (folder / "broken.json").write_text("{", encoding="utf-8")
        self.assertEqual(dot.proposal_queue(self.goal), [partial])
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.outbox(evaluation)
        self.call("act")
        self.assertEqual(sorted(row["status"] for row in dot.proposal_queue(self.goal)), ["approved", "pending"])

    def test_tick_journal_classifies_discovery_error_without_exception_text(self):
        with patch.object(discovery, "evaluation_material", side_effect=PermissionError("PRIVATE PATH")):
            self.assertEqual(self.call("tick"), "")
        journal = (self.goal / "journal.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE PATH", journal)
        row = json.loads(journal.splitlines()[-1])
        self.assertEqual((row["result"], row["why"]), ("discovery-error", "読み書きの権限がありません"))

    def test_evaluation_hook(self):
        self.harvest("plan.md")
        self.evaluate()
        state = dot.load_state(self.goal)
        policy = {"tools": {"allow": ["view", "web_fetch"], "web_allow": ["*"], "shell": "allow"}}

        def decide(name, **args):
            return dot.decide(self.goal, self.goal, {"toolName": name, "toolArgs": args}, state, policy)

        for name, args in (("view", {"path": str(self.sources / "plan.md")}), ("view", {"path": "goal.md"}),
                           ("view", {"path": ".dot/discovery/evaluation.json"}), ("view", {"path": "."}),
                           ("web_fetch", {"url": "https://example.test"}), ("write", {"path": "notes/log.md"}),
                           ("shell", {"command": "python -c pass"})):
            with self.subTest(name=name, args=args):
                self.assertIsNotNone(decide(name, **args))
        self.assertIsNone(decide("write", path="outbox/01.json"))
        self.assertIsNone(decide("view", path=".dot/prompt.md"))
        script = Path(dot.__file__).resolve().parent.parent / "copilot-app-jobs" / "copilot_app_job.py"
        command = f'python "{script}" next --dir "{self.goal}"'
        self.assertIsNone(decide("shell", command=command))
        self.assertIsNotNone(decide("shell", command=command + ' --box "/outside"'))

    def test_decision_goal_retry_and_results(self):
        self.harvest("yes.md", "no.md")
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.outbox(evaluation, "yes.json", sources=["yes.md"], why="yes.md の候補")
        self.outbox(evaluation, "no.json", sources=["no.md"], why="no.md の候補")
        self.call("act")
        for proposal in dot.proposal_queue(self.goal):
            if proposal["sources"] == ["yes.md"]:
                approved = proposal
                self.assertEqual(self.proposals("approve", proposal["id"], "--reason", "確認済み")[0], 0)
            else:
                rejected = proposal
                self.assertEqual(self.proposals("reject", proposal["id"], "--reason", "確認済み")[0], 0)
        register = dot.register_discovery

        def crash(*args):
            register(*args)
            raise KeyboardInterrupt

        with patch.object(dot, "register_discovery", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.call("act")
        self.call("act")
        self.call("act")
        target, = self.goals.iterdir()
        self.assertEqual(target.name, approved["id"])
        self.assertEqual([row["event"] for row in self.history() if row["proposal_id"] == approved["id"]],
                         ["decision", "registered"])
        self.assertEqual([row["event"] for row in self.history() if row["proposal_id"] == rejected["id"]], ["decision"])
        state = dot.load_state(target)
        self.assertEqual(state["status"], "active")
        self.assertEqual(self.call("tick", self.ts, target), "wake: start\n")
        for outcome in ("progress", "pending", "success"):
            policy = dot.load_policy(target)
            policy["done_check"] = [] if outcome == "pending" else ["check"]
            dot.write_json(target / "policy.json", policy)
            dot.write_json(target / "outbox" / "done.json", {"kind": "done"})
            with patch.object(dot, "run_command", return_value=(1 if outcome == "progress" else 0, "")):
                self.call("act", self.ts, target)
            self.call("act")
            self.assertEqual(self.history()[-1]["status"], outcome)
            self.assertEqual(self.history()[-1]["proposal_id"], approved["id"])
        self.call("act")
        self.assertEqual(len([row for row in self.history() if row["event"] == "result"]), 3)

    def test_routine_uses_existing_creation_and_records_linked_runs(self):
        worker = self.root / "worker"
        dot.init_goal(worker, "worker", self.ts)
        sm = self.root / "skills/statemachine-use/scripts/next_state.py"
        sm.parent.mkdir(parents=True)
        sm.touch()
        dot.write_json(worker / "policy.json", {"routines": {"skills_dirs": [str(self.root / "skills")]}})
        self.policy["discovery"]["routine_dir"] = str(worker)
        self.save_policy()
        self.harvest("plan.md")
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.outbox(evaluation, suggested_route="routine")
        self.call("act")
        proposal, = dot.proposal_queue(self.goal)
        self.decision(proposal, "approved")
        self.call("act")
        self.call("act")
        proposal, = dot.proposal_queue(self.goal)
        ref = proposal["execution_ref"]
        state = dot.load_state(worker)
        job = state["routine"]
        self.assertEqual((job["mode"], job["id"]), ("create", ref["job_id"]))
        self.assertEqual(len(list((worker / "inbox").glob("*.json"))), 1)
        (Path(job["draft"]) / "workflow.yaml").write_text("states: {}", encoding="utf-8")
        dot.write_json(worker / "outbox" / "created.json", {"kind": "routine", "action": "created", "id": job["id"]})
        with patch.object(dot.routines, "dry_run", return_value=(True, "")), patch.object(
                dot.routines, "describe", return_value={"inputs": {}}):
            self.call("act", self.ts, worker)
        trial = dot.load_state(worker)["routine"]
        self.assertEqual(trial["mode"], "trial")
        dot.write_json(worker / "outbox" / "result.json",
                       {"kind": "routine", "action": "result", "id": trial["id"], "ok": True})
        self.call("act", self.ts, worker)
        self.call("act")
        self.assertEqual(self.history()[-1]["status"], "progress")
        self.assertEqual(self.history()[-1]["execution_ref"]["run_id"], trial["id"])
        for escalate in (True, False):
            state = dot.load_state(worker)
            job = dot.routines.new_job(state, "run", self.ts, name=ref["machine"], path=str(worker),
                                       discovery_id=proposal["id"], discovery_job=ref["job_id"])
            dot.save_state(worker, state)
            dot.write_json(worker / "outbox" / "result.json",
                           {"kind": "routine", "action": "result", "id": job["id"], "ok": True, "escalate": escalate})
            self.call("act", self.ts, worker)
            self.call("act")
            self.assertEqual(self.history()[-1]["status"], "failed" if escalate else "success")
            self.assertEqual(self.history()[-1]["proposal_id"], proposal["id"])

    def test_receipt_recounts_destination_day_and_limits_outbox_replay(self):
        self.policy["discovery"]["proposals_per_day"] = 2
        self.save_policy()
        self.harvest("a.md", "b.md", "c.md")
        evaluation = self.evaluate()
        self.finish_evaluation()
        tomorrow = self.ts + 86400
        dot.write_json(self.folder / "proposals/prior.json", {
            "id": "prior", "received_at": dot.iso(tomorrow), "status": "rejected", "signal_ids": [],
        })
        for i, label in enumerate(evaluation["sources"]):
            self.outbox(evaluation, f"{i}.json", sources=[label], why=f"{label} の候補")
        self.call("act", tomorrow)
        self.assertEqual(evaluation["remaining"], 2)
        self.assertEqual(len(dot.proposal_queue(self.goal)), 2)
        self.call("act", tomorrow)
        self.assertEqual(len(dot.proposal_queue(self.goal)), 2)

    def test_goal_overlap_delivers_one_inbox_event(self):
        self.harvest("plan.md")
        target = self.goals / "existing"
        dot.init_goal(target, "existing", self.ts)
        (target / "goal.md").write_text(f'出典 `{self.sources / "plan.md"}`', encoding="utf-8")
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.outbox(evaluation)
        self.call("act")
        proposal, = dot.proposal_queue(self.goal)
        self.assertEqual(proposal["append_target"]["dir"], str(target))
        self.decision(proposal, "approved")
        with dot.locked(target):
            self.call("act")
        pending, = dot.proposal_queue(self.goal)
        self.assertIn("使用中", pending["registration_reason"])
        self.call("act")
        self.call("act")
        event, = (target / "inbox").glob("*.json")
        self.assertEqual(dot.read_json(event, {})["proposal_id"], proposal["id"])
        self.assertEqual(list(self.goals.iterdir()), [target])
        self.assertEqual(self.call("tick", self.ts, target), "wake: start,events\n")
        dot.write_json(target / "outbox" / "done.json", {"kind": "done"})
        self.call("act", self.ts, target)
        self.call("act")
        self.assertEqual(self.history()[-1]["status"], "pending")

    def test_existing_routine_checks_version_approval_and_tracks_real_run(self):
        worker = self.root / "worker"
        dot.init_goal(worker, "worker", self.ts)
        sm = self.root / "skills/statemachine-use/scripts/next_state.py"
        sm.parent.mkdir(parents=True)
        sm.touch()
        dot.write_json(worker / "policy.json", {"routines": {"skills_dirs": [str(self.root / "skills")]}})
        self.policy["discovery"]["routine_dir"] = str(worker)
        self.save_policy()
        self.harvest("plan.md")
        machine = worker / ".statemachine" / "daily"
        machine.mkdir(parents=True)
        (machine / "workflow.yaml").write_text("states: {}", encoding="utf-8")
        dot.routines.save_registry(worker, {"daily": {"source_paths": [str(self.sources / "plan.md")]}})
        evaluation = self.evaluate()
        self.finish_evaluation()
        self.outbox(evaluation, suggested_route="routine")
        self.call("act")
        proposal, = dot.proposal_queue(self.goal)
        self.assertEqual(proposal["append_target"]["machine"], "daily")
        self.decision(proposal, "approved")
        with patch.object(dot.routines, "read_workflow", return_value={}):
            self.call("act")
            state = dot.load_state(worker)
            approval, = state["approvals"]
            self.assertEqual(approval["action"], "routine.approve")
            self.assertNotIn("routine", state)
            self.call("act")
            self.assertEqual(len(dot.load_state(worker)["approvals"]), 1)
            for status in ("rejected", "expired"):
                with self.subTest(status=status):
                    approval["status"] = status
                    dot.save_state(worker, state)
                    self.call("act")
                    previous_id = approval["id"]
                    previous_count = len(state["approvals"])
                    state = dot.load_state(worker)
                    self.assertEqual(len(state["approvals"]), previous_count + 1)
                    approval = state["approvals"][-1]
                    self.assertNotEqual(approval["id"], previous_id)
                    self.assertEqual(approval["status"], "pending")
            approval["status"] = "approved"
            dot.save_state(worker, state)
            self.call("act", self.ts, worker)
            self.call("act")
        job = dot.load_state(worker)["routine"]
        self.assertEqual((job["mode"], job["name"]), ("run", "daily"))
        dot.write_json(worker / "outbox" / "result.json",
                       {"kind": "routine", "action": "result", "id": job["id"], "ok": True})
        self.call("act", self.ts, worker)
        self.call("act")
        self.assertEqual(self.history()[-1]["status"], "success")
        self.assertEqual(self.history()[-1]["execution_ref"]["run_id"], job["id"])

    def test_candidate_order_and_root_collisions(self):
        other = self.root / "other"
        other.mkdir()
        self.policy["discovery"]["collectors"]["plans"]["paths"].append(str(other))
        self.save_policy()
        (other / "plan.md").write_text("状態: 未着手\n", encoding="utf-8")
        self.harvest("plan.md", "first.md")
        (other / "plan.md").unlink()
        (self.sources / "plan.md").unlink()
        self.harvest("first.md")
        (self.sources / "plan.md").write_text("状態: 未着手\n", encoding="utf-8")
        (other / "plan.md").write_text("状態: 未着手\n", encoding="utf-8")
        self.call("harvest")
        evaluation = self.evaluate()
        labels = list(evaluation["sources"])
        self.assertEqual(labels[0], "first.md")
        self.assertEqual(len(labels), 3)
        self.assertTrue(all(label.startswith("root-") and label.endswith("/plan.md") for label in labels[1:]))
        self.assertEqual({evaluation["sources"][label][0]["source_path"] for label in labels[1:]},
                         {str(self.sources / "plan.md"), str(other / "plan.md")})

    def test_propose_without_matching_evaluation_is_denied(self):
        self.harvest("plan.md")
        evaluation = self.evaluate()
        self.finish_evaluation()
        dot.write_json(self.folder / "evaluation.json", {**evaluation, "id": "another"})
        self.outbox(evaluation)
        self.call("act")
        self.assertEqual(dot.proposal_queue(self.goal), [])
        row = list(dot.json_lines(self.goal / "journal.jsonl"))[-1]
        self.assertEqual(row["result"], "denied")
        self.assertEqual(row["why"], "対応する評価資料がありません")


if __name__ == "__main__":
    unittest.main()
