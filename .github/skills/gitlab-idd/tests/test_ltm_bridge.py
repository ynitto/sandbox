"""CLI-level learning-loop tests with local skill scripts and mocked GitLab."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    import gl
    import ltm_bridge as bridge
finally:
    sys.path.pop(0)


FAKE = """
import json
import os
from pathlib import Path
import sys

name = Path(__file__).stem
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([name, args]) + "\\n")
responses = json.loads(os.environ["FAKE_RESPONSES"])
code, output, error = responses.get(name + ":" + args[0], responses.get(name, (0, "", "")))
print(output)
print(error, file=sys.stderr)
sys.exit(code)
"""


class LearningLoopTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = root
        self.log = root / "calls.jsonl"
        for name in ("recall_memory", "rate_memory", "save_memory", "wiki_query"):
            (root / f"{name}.py").write_text(FAKE, encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {
            "GITLAB_IDD_LTM_SCRIPTS": str(root),
            "GITLAB_IDD_WIKI_SCRIPTS": str(root),
            "GITLAB_NODE_ID": "node-a",
            "FAKE_LOG": str(self.log),
            "FAKE_RESPONSES": "{}",
        }))
        self.sleep = self.enterContext(patch.object(bridge, "time")).sleep
        self.notes, self.posts, self.issues = [], [], []
        self.state = "opened"
        self.api = self.enterContext(patch.object(gl, "api", side_effect=self.fake_api))
        self.api_list = self.enterContext(patch.object(gl, "api_list", side_effect=self.fake_list))

    def fake_api(self, host, token, method, path, data=None):
        if method == "POST":
            self.posts.append(data["body"])
            self.notes.append({"body": data["body"]})
            return {}
        self.assertEqual(method, "GET")
        return {"state": self.state}

    def fake_list(self, host, token, path, params=None):
        if path.endswith("/notes"):
            return list(self.notes)
        return [issue for issue in self.issues if issue["state"] == params["state"]]

    def invoke(self, *argv):
        args = gl.build_parser().parse_args(argv)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = gl.COMMANDS[args.command](args, "gitlab.invalid", "project", "token")
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                if isinstance(exc.code, str):
                    print(exc.code, file=stderr)
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    def output(self):
        return json.loads(self.stdout)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def responses(self, **values):
        os.environ["FAKE_RESPONSES"] = json.dumps(values)

    def file(self, data):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=self.root, encoding="utf-8", delete=False,
        ) as stream:
            json.dump(data, stream)
            return stream.name

    def note(self, body):
        self.notes.append({"body": body})

    def event(self, kind, cycle=1, node="node-a", **fields):
        self.note(bridge.marker(dict(v=1, event=kind, cycle=cycle, node=node,
                                     at="2026-11-19T03:00:00Z", **fields)))

    def history(self):
        return bridge.History(self.notes, "node-a", self.state)

    def posted_events(self):
        return [event for body in self.posts for event in bridge.parse_markers(body)[0]]

    @staticmethod
    def memory(mid, score=0.5):
        return dict(id=mid, title=f"記憶 {mid}", score=score)

    def recall_event(self, *ids, node="node-a", cycle=1):
        self.event("recall", cycle, node, status="ok", trigger="start", queries=["q"],
                   memories=[self.memory(mid) for mid in ids], wiki_status="empty", wiki=[])

    def used(self, applied=("a",), referenced=("b",), *, cycle=1):
        self.recall_event(*applied, *referenced, cycle=cycle)
        self.event("usage", cycle, memories=[
            *[dict(id=mid, use="applied", how="根拠") for mid in applied],
            *[dict(id=mid, use="referenced", how="") for mid in referenced],
        ])

    def approve(self):
        self.note("<!-- gitlab-idd:requester-approved:reviewer -->")

    def rework(self, cause="spec-misread"):
        suffix = f"\n<!-- gitlab-idd:rework-cause:{cause} -->" if cause else ""
        self.note("## ❌ 差し戻し\n期限は30分です" + suffix)

    def save_file(self):
        return self.file(dict(title="保存する題", summary="要約", conclusion="保存する結論",
                              memory_type="episodic"))

    def run_save(self):
        return self.invoke("ltm-save", "42", "--kind", "execution", "--args-file", self.save_file())

    def auto(self):
        return self.invoke("ltm-rate", "42", "--outcome", "approved", "--auto")

    def plan(self, *items, outcome="rework"):
        return self.invoke("ltm-rate", "42", "--plan-file",
                           self.file(dict(outcome=outcome, items=list(items))))

    @staticmethod
    def correction(mid="a", evidence="差し戻し: 「期限は30分です」"):
        return dict(id=mid, rating="correction", evidence=evidence, note="期限を30分にする")

    def test_marker_roundtrip(self):
        event = {"title": "題 --> --\n日本語"}
        body = bridge.marker(event)
        self.assertEqual(body.count("-->"), 1)
        self.assertEqual(bridge.parse_markers(body), ([event], 0))

    def test_malformed_marker_is_counted_not_fatal(self):
        self.note("<!-- gitlab-idd:ltm:v1 {broken} -->")
        self.event("usage", memories=[])
        self.assertEqual(self.invoke("ltm-trail", "42"), 0)
        self.assertEqual(self.output()["malformed"], 1)
        self.assertTrue(self.output()["cycles"][0]["usage"])
        self.event("failure", step="recall")
        self.event("failure", step="sprint-review")
        self.recall_event("a", node="other")
        self.assertEqual(len(self.history().trail(42)["open_failures"]), 2)
        self.recall_event("a")
        self.assertEqual([row["step"] for row in self.history().trail(42)["open_failures"]],
                         ["sprint-review"])

    def test_cycle_counts_rework_comments(self):
        self.note("差し戻し\n<!-- gitlab-idd:rework-cause:scope,quality -->")
        self.rework(None)
        self.assertEqual(self.history().cycle, 3)
        self.assertEqual(self.history().causes, [["scope", "quality"], ["unknown"]])

    def test_outcome_resolution(self):
        self.rework()
        self.approve()
        self.event("usage", cycle=2, memories=[])
        self.assertEqual(self.history().outcome(1), "rework")
        self.assertEqual(self.history().outcome(2), "pending")
        self.state = "closed"
        self.assertEqual(self.history().outcome(2), "closed-unknown")
        self.approve()
        self.assertEqual(self.history().outcome(2), "approved")

    def test_trail_filters_by_node(self):
        self.recall_event("a", node="other")
        self.event("usage", node="other", memories=[])
        self.event("rate", node="other", target_cycle=1, applied=[])
        self.rework()
        self.event("retro", cycle=2, node="other", trigger="rework", target_cycle=1)
        cycle = self.history().trail(42)["cycles"][0]
        self.assertEqual(cycle, dict(cycle=1, recall=False, usage=False, outcome="rework",
                                     rated=False, retro=True))

    def test_recall_merges_queries_and_posts_marker(self):
        self.responses(**{
            "recall_memory:q1": (0, json.dumps({"results": [self.memory("a", 0.1)]}), ""),
            "recall_memory:q2": (0, json.dumps({"results": [
                self.memory("a", 0.8), self.memory("b", 0.4),
            ]}), ""),
        })
        self.assertEqual(self.invoke("ltm-recall", "42", "--query", "q1", "--query", "q2"), 0)
        self.assertEqual(self.output()["memories"], [
            dict(id="a", title="記憶 a", relevance=0.8),
            dict(id="b", title="記憶 b", relevance=0.4),
        ])
        for _, args in self.calls():
            self.assertIn("--no-track", args)
            self.assertEqual(args[args.index("--scope") + 1], "all")
        self.assertEqual(self.posted_events()[0]["memories"][0]["score"], 0.8)

    def test_recall_is_idempotent_per_cycle(self):
        self.responses(recall_memory=(0, '{"results":[]}', ""))
        argv = ("ltm-recall", "42", "--query", "q")
        self.assertEqual(self.invoke(*argv), 0)
        initial = self.output()
        self.assertEqual(self.invoke(*argv), 0)
        self.assertEqual(self.output(), initial)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.invoke(*argv, "--force"), 0)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(len(self.calls()), 2)

    def test_recall_unavailable_when_scripts_missing(self):
        (self.root / "recall_memory.py").unlink()
        self.assertEqual(self.invoke("ltm-recall", "42", "--query", "q"), 0)
        self.assertEqual(self.output()["status"], "unavailable")
        self.assertIn("記憶の検索は未導入のため行っていません", self.posts[0])
        self.assertFalse(self.calls())

    def test_recall_failure_retries_then_records(self):
        self.responses(recall_memory=(7, "", "failed"))
        self.assertEqual(self.invoke("ltm-recall", "42", "--query", "q"), 3)
        self.assertEqual(len(self.calls()), 2)
        self.sleep.assert_called_once_with(2)
        self.assertEqual(self.output()["status"], "error")
        event = self.posted_events()[1]
        self.assertEqual((event["event"], event["attempts"], event["exit_code"]), ("failure", 2, 7))

    def test_recall_partial_failure_keeps_successful_results(self):
        for rows in ([self.memory("a")], []):
            with self.subTest(rows=rows):
                self.responses(**{
                    "recall_memory:q1": (7, "", "failed"),
                    "recall_memory:q2": (0, json.dumps({"results": rows}), ""),
                })
                argv = ("ltm-recall", "42", "--query", "q1", "--query", "q2")
                self.assertEqual(self.invoke(*argv, "--force"), 0)
                self.assertEqual(self.output()["status"], "ok" if rows else "empty")
                self.assertEqual(self.output()["failed_queries"], ["q1"])
                self.assertEqual([row["id"] for row in self.output()["memories"]],
                                 [row["id"] for row in rows])
                self.assertEqual(self.posted_events()[-1]["failed_queries"], ["q1"])
                self.assertEqual(self.invoke(*argv), 0)
                self.assertEqual(self.output()["failed_queries"], ["q1"])
        self.assertEqual([args[0] for _, args in self.calls()], ["q1", "q1", "q2"] * 2)
        self.assertEqual([event["event"] for event in self.posted_events()], ["recall", "recall"])

    def test_recall_all_queries_fail_after_retry(self):
        self.responses(**{"recall_memory:q1": (0, "invalid JSON", ""),
                          "recall_memory:q2": (7, "", "failed")})
        self.assertEqual(self.invoke("ltm-recall", "42", "--query", "q1", "--query", "q2"), 3)
        self.assertEqual([args[0] for _, args in self.calls()], ["q1", "q1", "q2", "q2"])
        self.assertEqual(self.output()["status"], "error")
        self.assertEqual(self.output()["failed_queries"], ["q1", "q2"])
        self.assertEqual([event["event"] for event in self.posted_events()], ["recall", "failure"])

    def test_wiki_failure_does_not_fail_recall(self):
        self.responses(recall_memory=(0, '{"results":[]}', ""), wiki_query=(1, "", "wiki missing"))
        self.assertEqual(self.invoke("ltm-recall", "42", "--query", "q", "--wiki-query", "w"), 0)
        self.assertEqual(self.output()["wiki_status"], "error")
        self.assertEqual([event["event"] for event in self.posted_events()], ["recall"])
        self.assertIn("wiki missing", self.posts[0])

    def test_usage_rejects_unknown_id_and_missing_how(self):
        self.recall_event("a")
        for item in (dict(id="other", use="referenced"), dict(id="a", use="applied")):
            with self.subTest(item=item):
                self.assertEqual(self.invoke("ltm-log", "42", "--event", "usage", "--data-file",
                                             self.file({"memories": [item]})), 2)
        self.assertFalse(self.posts)
        self.assertFalse(self.calls())

    def test_usage_tracks_only_applied(self):
        self.recall_event("a", "b", "c")
        path = self.file({"memories": [
            dict(id="a", use="applied", how="根拠"), dict(id="b", use="referenced"),
        ]})
        self.assertEqual(self.invoke("ltm-log", "42", "--event", "usage", "--data-file", path), 0)
        self.assertEqual(self.calls(), [["recall_memory", ["--ids", "a", "--json"]]])
        self.assertEqual(self.posted_events()[0]["memories"][-1],
                         dict(id="c", use="unused", how=""))

    def test_save_success_posts_event(self):
        self.responses(save_memory=(0, "✅ 保存しました [home]: /home/user/memory.md", ""))
        self.assertEqual(self.run_save(), 0)
        self.assertEqual(self.output(), dict(status="ok", file="memory.md", attempts=1))
        self.assertEqual(self.posted_events()[0]["event"], "save")
        self.assertIn("--memory-type", self.calls()[0][1])
        self.rework("quality")
        self.assertEqual(self.invoke("ltm-save", "42", "--kind", "retro", "--trigger", "rework",
                                     "--args-file", self.save_file()), 0)
        retro = self.posted_events()[-1]
        self.assertEqual((retro["cycle"], retro["target_cycle"], retro["causes"]),
                         (2, 1, ["quality"]))
        self.assertEqual(self.history().trail(42)["pending"]["retro_cycles"], [])

    def test_save_failure_keeps_lost_learning(self):
        self.responses(save_memory=(2, "", "write failed"))
        self.assertEqual(self.run_save(), 3)
        human = self.posts[0].split("<!--")[0]
        self.assertIn("保存する題", human)
        self.assertIn("保存する結論", human)
        self.assertEqual(self.output()["status"], "failed")
        self.assertEqual(self.posted_events()[0]["lost"],
                         dict(title="保存する題", conclusion="保存する結論"))

    def test_save_failure_and_post_failure_exit_4(self):
        self.responses(save_memory=(2, "", "write failed"))
        self.api.side_effect = SystemExit("ERROR: HTTP 503")
        self.assertEqual(self.run_save(), 4)
        self.assertIn("ERROR: HTTP 503", self.stderr)
        self.assertIn("保存する題", self.stderr)
        self.assertIn("保存する結論", self.stderr)
        self.assertIn("gitlab-idd:ltm:v1", self.stderr)

    def test_rate_auto_good_only_applied(self):
        self.used()
        self.approve()
        self.assertEqual(self.auto(), 0)
        self.assertEqual(self.calls(), [["rate_memory", ["--id", "a", "--scope", "all", "--good"]]])
        self.assertEqual(self.output()["applied"][0]["rating"], "good")
        self.assertEqual(self.auto(), 0)
        self.assertEqual(self.output()["skipped"], [dict(id="a", reason="already-rated")])
        self.assertEqual(len(self.calls()), 1)

    def test_rate_auto_without_applied_does_not_post(self):
        self.used(applied=())
        self.approve()
        self.assertEqual(self.auto(), 0)
        self.assertEqual(self.output(), dict(status="nothing-to-rate"))
        trail = self.history().trail(42)
        self.assertTrue(trail["cycles"][0]["rated"])
        self.assertEqual(trail["pending"]["rate"], [])
        self.assertFalse(self.posts)
        self.assertFalse(self.calls())

    def test_rate_rejects_empty_and_incomplete_plans(self):
        self.used(applied=("a", "b"), referenced=())
        self.rework()
        self.event("rate", cycle=2, target_cycle=1, applied=[], held=[], skipped=[])
        self.assertFalse(self.history().trail(42)["cycles"][0]["rated"])
        for items in ((), (self.correction("a"),), (dict(id="a", rating="hold", reason="不明"),)):
            with self.subTest(items=items):
                self.assertEqual(self.plan(*items), 2)
                self.assertIn("評価計画に含まれていない applied の ID", self.stderr)
        self.assertFalse(self.posts)
        self.assertFalse(self.calls())

    def test_rate_retries_error_on_reexecution(self):
        self.used()
        self.rework()
        self.responses(rate_memory=(7, "", "failed"))
        self.assertEqual(self.plan(self.correction()), 3)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.output()["applied"][0]["result"], "error")
        trail = self.history().trail(42)
        self.assertFalse(trail["cycles"][0]["rated"])
        self.assertEqual(trail["pending"]["rate"], [dict(cycle=1, outcome="rework")])
        self.responses()
        self.assertEqual(self.plan(self.correction()), 0)
        self.assertEqual(len(self.calls()), 3)
        self.assertEqual(self.output()["applied"][0]["result"], "ok")
        trail = self.history().trail(42)
        self.assertTrue(trail["cycles"][0]["rated"])
        self.assertEqual(trail["pending"]["rate"], [])
        self.assertEqual(trail["open_failures"], [])

    def test_rate_retries_after_missing_script_is_installed(self):
        self.used()
        self.rework()
        script = self.root / "rate_memory.py"
        script.unlink()
        self.assertEqual(self.plan(self.correction()), 0)
        self.assertEqual(self.output()["skipped"], [dict(id="a", reason="unavailable")])
        self.assertFalse(self.calls())
        trail = self.history().trail(42)
        self.assertFalse(trail["cycles"][0]["rated"])
        self.assertEqual(trail["pending"]["rate"], [dict(cycle=1, outcome="rework")])

        script.write_text(FAKE, encoding="utf-8")
        self.assertEqual(self.plan(self.correction()), 0)
        self.assertEqual(self.output()["applied"][0]["result"], "ok")
        self.assertEqual(len(self.calls()), 1)
        trail = self.history().trail(42)
        self.assertTrue(trail["cycles"][0]["rated"])
        self.assertEqual(trail["pending"]["rate"], [])

    def test_rate_retries_only_failed_id_after_partial_success(self):
        self.used(applied=("a", "b"), referenced=())
        self.approve()
        self.event("rate", target_cycle=1, applied=[
            dict(id="a", rating="good", result="ok"),
            dict(id="b", rating="good", result="error"),
        ])
        self.assertFalse(self.history().trail(42)["cycles"][0]["rated"])
        self.assertEqual(self.auto(), 0)
        self.assertEqual(self.calls(), [["rate_memory", ["--id", "b", "--scope", "all", "--good"]]])
        self.assertEqual(self.output()["skipped"], [dict(id="a", reason="already-rated")])
        self.assertTrue(self.history().trail(42)["cycles"][0]["rated"])

    def test_rate_requires_evidence_quote_of_at_least_four_characters(self):
        self.used()
        self.rework()
        for evidence in ("## ❌ 差し戻し", "期限は30分です", "「 」", "「は」", "「 期限は 」"):
            with self.subTest(evidence=evidence):
                self.assertEqual(self.plan(self.correction(evidence=evidence)), 2)
                self.assertIn("差し戻しコメントの引用が必要", self.stderr)
        self.assertFalse(self.posts)
        self.assertFalse(self.calls())
        self.assertEqual(self.plan(self.correction(evidence="「期限は3」")), 0)

    def test_rate_correction_requires_cause_and_evidence(self):
        for cause, evidence in (("ac-unmet", "「期限は30分です」"), (None, "『期限は30分です』"),
                                ("quality", ""), ("quality", '"書かれていない引用"')):
            with self.subTest(cause=cause, evidence=evidence):
                self.notes.clear()
                self.used()
                self.rework(cause)
                self.assertEqual(self.plan(self.correction(evidence=evidence)), 2)
        self.assertFalse(self.calls())
        self.assertFalse(self.posts)

    def test_rate_caps_and_idempotency(self):
        self.used(applied=("a", "b"), referenced=())
        self.rework()
        plan = (self.correction("a"), self.correction("b"))
        self.assertEqual(self.plan(*plan), 0)
        self.assertEqual([row["id"] for row in self.output()["applied"]], ["a"])
        self.assertEqual(self.output()["held"], [
            dict(id="b", reason="1 回の差し戻しにつき correction は 1 件までです"),
        ])
        self.assertEqual(self.plan(self.correction("a"), dict(id="b", rating="hold", reason="上限")), 0)
        self.assertEqual(self.output()["skipped"], [dict(id="a", reason="already-rated")])
        self.assertEqual(len(self.calls()), 1)
        self.used(applied=("a",), referenced=(), cycle=2)
        self.rework()
        self.assertEqual(self.plan(self.correction("a")), 0)
        self.assertEqual(self.output()["held"], [
            dict(id="a", reason="このイシューで correction 済みです"),
        ])
        self.assertEqual(len(self.calls()), 1)

    def test_rate_not_found_is_not_failure(self):
        self.used()
        self.approve()
        self.responses(rate_memory=(1, "", "ID が見つかりません"))
        self.assertEqual(self.auto(), 0)
        self.assertEqual(self.output()["applied"][0]["result"], "not-found")
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.posts), 1)

    def test_rate_never_uses_bad(self):
        self.used(applied=("a", "b", "c"), referenced=())
        self.rework()
        self.assertEqual(self.plan(dict(id="a", rating="bad")), 2)
        self.assertEqual(self.plan(self.correction(), dict(id="b", rating="hold", reason="不明"),
                                   dict(id="c", rating="none", reason="無関係")), 0)
        self.used(cycle=2)
        self.approve()
        self.assertEqual(self.auto(), 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(all("--bad" not in args for _, args in self.calls()))

    def test_rate_comment_includes_node_and_undo(self):
        self.used()
        self.rework()
        self.assertEqual(self.plan(self.correction()), 0)
        self.assertIn("`node-a`", self.posts[0])
        self.assertIn("取り消し方", self.posts[0])
        self.assertIn('--id a --good --note "取り消し:', self.posts[0])

    def test_retro_check_reasons(self):
        self.rework()
        self.rework()
        self.note("<!-- gitlab-idd:clarification-requested:24h-timeout -->")
        self.issues = [{"state": "closed"}] * 10
        self.assertEqual(self.invoke("ltm-trail", "42", "--retro-check", "--get", "retro_check"), 0)
        self.assertEqual(self.output(), dict(due=True, done_count=10, reasons=[
            "done-multiple-of-10", "rework>=2", "via-clarification",
        ]))

    def test_pending_skips_closed_unknown(self):
        self.used()
        self.issues = [dict(state="closed", iid=42)]
        self.assertEqual(self.invoke("ltm-pending"), 0)
        self.assertEqual(self.output(), dict(scanned=1, rate_pending=[], open_failures=[]))
        self.approve()
        self.assertEqual(self.invoke("ltm-pending"), 0)
        self.assertEqual(self.output()["rate_pending"], [dict(issue_id=42, cycle=1, outcome="approved")])

    def test_dry_run_calls_nothing(self):
        data_file = self.file({"memories": [dict(id="a", use="applied", how="根拠")]})
        for argv, text in (
            (("ltm-recall", "42", "--query", "q"), "参照した知見"),
            (("ltm-log", "42", "--event", "usage", "--data-file", data_file), "使い方"),
            (("ltm-log", "42", "--event", "failure", "--step", "sprint-review", "--reason", "失敗"),
             "学習ループの失敗"),
            (("ltm-save", "42", "--kind", "execution", "--args-file", self.save_file()), "実行知見"),
            (("ltm-rate", "42", "--outcome", "approved", "--auto"), "評価返し"),
        ):
            with self.subTest(command=argv[0]):
                self.assertEqual(self.invoke(*argv, "--dry-run"), 0)
                self.assertIn(text, self.stdout)
        self.api.assert_not_called()
        self.api_list.assert_not_called()
        self.assertFalse(self.calls())


if __name__ == "__main__":
    unittest.main()
