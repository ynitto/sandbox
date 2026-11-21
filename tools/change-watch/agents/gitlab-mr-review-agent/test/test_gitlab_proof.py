"""偽の GitLab・git worktree・検証エージェントで proof の入出力を確かめる。"""
import ast
from dataclasses import fields
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_gitlab_mr as mr

PLUGIN = mr.ROOT / "gitlab_proof.py"
DAG = mr.ROOT / "examples" / "change-watch-mr-review-proof.yaml.example"
spec = importlib.util.spec_from_file_location("gitlab_proof", PLUGIN)
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

FAKE = r'''
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

settings = json.loads(Path(__file__).with_name("behavior.json").read_text(encoding="utf-8"))
stdin = sys.stdin.read()
prompt = sys.argv[1] if len(sys.argv) > 1 else stdin
request = Path(prompt.split(" を読んで", 1)[0])
body = request.read_text(encoding="utf-8")
reply = Path(re.search(r"判定を (.+) に JSON", body)[1])
visible = dict(argv=sys.argv, stdin=stdin, env=dict(os.environ), input=body, request=str(request),
               temporary=sorted(p.name for p in request.parent.iterdir()),
               worktree=sorted(p.name for p in Path.cwd().iterdir()), cwd=os.getcwd())
Path(__file__).with_name("visible.json").write_text(json.dumps(visible), encoding="utf-8")
output = json.dumps(dict(results=settings["rows"]))
mode = settings["mode"]
if mode == "file":
    reply.write_text(output, encoding="utf-8")
    print('{"results": []}')  # ファイルが優先される
elif mode == "stdout":
    print('経過 {"results": []}\n```json\n' + output + '\n```\n完了')
elif mode == "broken":
    reply.write_text("{", encoding="utf-8")
elif mode == "wrong_shape":
    print('{"notes": []}')
elif mode == "timeout":
    reply.write_text(output, encoding="utf-8")
    subprocess.Popen([sys.executable, "-c",
                      "from pathlib import Path; import sys, time; time.sleep(2); "
                      "Path(sys.argv[1]).touch()", str(Path(__file__).with_name("child-survived"))])
    time.sleep(30)
sys.exit(settings["exit_code"])
'''


def finding(iid, line=2, claim=None):
    return dict(id=iid, target=dict(path="app.py", line=line), claim=claim or f"指摘 {iid}")


def judgment(iid, verdict="supported", evidence="app.py:2 の式を確認"):
    return dict(id=iid, verdict=verdict, evidence=evidence)


class StructureTest(unittest.TestCase):
    def test_input_is_built_from_three_arguments(self):
        self.assertEqual([f.name for f in fields(proof.Claim)], ["id", "path", "line", "claim"])
        self.assertTrue(proof.Claim.__dataclass_params__.frozen)
        self.assertEqual(list(inspect.signature(proof.build_verifier_input).parameters),
                         ["request", "claims", "reply_path"])
        tree = ast.parse(PLUGIN.read_text(encoding="utf-8"))
        self.assertFalse([n for n in ast.walk(tree) if
                          getattr(n, "id", None) == "self_assessment" or
                          getattr(n, "attr", None) == "self_assessment" or
                          getattr(n, "value", None) == "self_assessment"])
        request = proof.Request("title", "desc", "url", "branch", "main", "a", "b", "d" * 150001)
        claims = [proof.Claim("f1", "app.py", 2, "claim")]
        args = (request, claims, Path("result.json"))
        before = proof.build_verifier_input(*args)
        with mr.mock.patch.dict(os.environ, {"MR_REVIEW_WORKTREE": "/elsewhere", "GITLAB_TOKEN": "another"}):
            self.assertEqual(proof.build_verifier_input(*args), before)
        self.assertIn("git diff a b", before)
        self.assertNotIn(request.diff, before)

    def test_proof_example_is_not_installed_as_dag(self):
        with mr.mock.patch.object(sys, "path", [str(mr.CHANGE_WATCH.parent), *sys.path]):
            import install
        programs, _, dags = install.agent_files("gitlab-mr-review-agent")
        self.assertIn(PLUGIN, programs)
        self.assertNotIn(DAG.name, [p.name for p in dags])
        self.assertIn("change-watch-mr-review.yaml.example", [p.name for p in dags])


class ProofUnitTest(unittest.TestCase):
    def test_verifier_env_forces_utf8_io(self):
        # 検証エージェントの標準入出力は、親の環境や Windows のロケール文字コードに
        # 関わらず UTF-8 に固定される（日本語の受け渡しが壊れる CI 失敗の回帰防止）。
        with mr.mock.patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}):
            self.assertEqual(proof.verifier_env()["PYTHONIOENCODING"], "utf-8")
        with mr.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(proof.verifier_env()["PYTHONIOENCODING"], "utf-8")

    @unittest.skipUnless(os.name == "posix", "POSIX のプロセスグループが必要")
    def test_stop_group_falls_back_when_killpg_is_denied(self):
        process = mr.mock.Mock(pid=123)
        with mr.mock.patch.object(proof.os, "killpg", side_effect=PermissionError) as killpg:
            proof.stop_group(process)
        killpg.assert_called_once_with(process.pid, proof.signal.SIGKILL)
        self.assertEqual(process.mock_calls, [mr.mock.call.kill(), mr.mock.call.wait()])

    def test_read_reply_falls_back_when_file_has_no_valid_results(self):
        rows = [judgment("a")]
        stdout = json.dumps({"results": rows})
        with tempfile.TemporaryDirectory() as directory:
            reply = Path(directory) / "result.json"
            for invalid in ("{", '{"notes": []}', '{"results": null}'):
                with self.subTest(invalid=invalid):
                    reply.write_text(invalid, encoding="utf-8")
                    self.assertEqual(proof.read_reply(reply, stdout), rows)

    def test_findings_symlink_into_worktree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            inside = worktree / "findings.json"
            inside.write_text(json.dumps({"version": 1, "findings": [finding("a")]}), encoding="utf-8")
            link = root / "linked-findings.json"
            try:
                link.symlink_to(inside)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"シンボリックリンクを作れない: {error}")
            env = {**os.environ, "MR_REVIEW_REPO_DIR": "", "MR_REVIEW_WORKTREE": str(worktree)}
            result = subprocess.run([sys.executable, str(PLUGIN), "1", "--findings", str(link)],
                                    env=env, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("作業フォルダの外", result.stderr)


@unittest.skipUnless(shutil.which("git"), "git が無い")
class ProofTest(unittest.TestCase):
    def setUp(self):
        # 既存の試験と同じ bare リポジトリと偽の GitLab を使う。
        self.repo = mr.LocalRepoTest()
        self.addCleanup(self.repo.doCleanups)
        self.repo.setUp()
        self.tmp, self.gl, self.env = self.repo.tmp, self.repo.gl, self.repo.env
        self.gl.mrs["1"].update(title="Title-Marker", description="Description-Marker")
        self.findings = self.tmp / "private-findings.json"
        self.evidence = self.tmp / "evidence"
        self.visible = self.tmp / "visible.json"
        self.fake = self.tmp / "fake verifier.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.env.update(PYTHONIOENCODING="utf-8", NO_PROXY="localhost,127.0.0.1")
        self.behavior([judgment("a")])
        self.write_findings(finding("a"))
        self.context()

    def context(self):
        code, out = self.repo.cli("context", 1)
        self.assertEqual(code, 0, out)
        self.assertIn("今のコミットにしてあります", out)
        return out

    def behavior(self, rows=(), mode="file", exit_code=0, placeholder="{prompt}"):
        argv = [sys.executable, str(self.fake)] + ([placeholder] if placeholder else [])
        self.env["MR_REVIEW_PROOF_AGENT"] = json.dumps(argv)
        (self.tmp / "behavior.json").write_text(
            json.dumps(dict(rows=list(rows), mode=mode, exit_code=exit_code)), encoding="utf-8")
        self.visible.unlink(missing_ok=True)

    def write_findings(self, *rows):
        self.findings.write_text(json.dumps(dict(version=1, findings=rows)), encoding="utf-8")

    def call(self, *args, entry=(str(PLUGIN),), input=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MR_REVIEW_PROOF_")}
        return subprocess.run([sys.executable, *entry, *map(str, args)], env={**env, **self.env},
                              input=input, capture_output=True, encoding="utf-8", timeout=15)

    def result(self, *, stdin=False, entry=(str(PLUGIN),)):
        response = self.call("!1", "--findings", "-" if stdin else self.findings,
                             "--evidence-dir", self.evidence, entry=entry,
                             input=self.findings.read_text(encoding="utf-8") if stdin else None)
        self.assertEqual(response.returncode, 0, response.stderr)
        if self.visible.exists():
            record = json.loads(self.visible.read_text(encoding="utf-8"))
            self.assertFalse(Path(record["request"]).parent.exists(), record["request"])
        return json.loads(response.stdout)

    def test_only_original_request_and_claim_are_visible(self):
        row = finding("a", claim="Claim-Marker")
        row.update(self_assessment={"confidence": "Private-confidence", "reasoning": {"x": "Private-reason"},
                                    "log": ["Private-log"]},
                   score="Private-score", reasoning="Private-unknown")
        row["target"]["hint"] = "Private-target"
        self.write_findings(row)
        self.env.update(PROOF_TEST_ALLOWED="visible-env", PROOF_TEST_PRIVATE="Private-env",
                        MR_REVIEW_SECRET="Private-review-env",
                        MR_REVIEW_PROOF_ENV_PASS="PROOF_TEST_ALLOWED,GITLAB_TOKEN,MR_REVIEW_SECRET")
        for placeholder in ("{prompt}", "{input}", ""):
            with self.subTest(placeholder=placeholder):
                self.behavior([judgment("a")], placeholder=placeholder)
                result = self.result(stdin=not placeholder)
                self.assertEqual(result["counts"]["supported"], 1)
                record = json.loads(self.visible.read_text(encoding="utf-8"))
                visible = json.dumps(record, ensure_ascii=False)
                self.assertNotIn("Private-", visible + json.dumps(result))
                self.assertNotIn(str(self.findings), visible)
                self.assertNotIn(str(self.evidence), visible)
                self.assertNotIn(mr.TOKEN, visible)
                self.assertFalse(any(k.startswith("MR_REVIEW_") for k in record["env"]))
                self.assertEqual(record["env"]["PROOF_TEST_ALLOWED"], "visible-env")
                for marker in ("Title-Marker", "Description-Marker", "Claim-Marker", "+    y = x * 2"):
                    self.assertIn(marker, record["input"])
                self.assertEqual(record["temporary"], ["request.md"])
                self.assertIn("app.py", record["worktree"])
                self.assertEqual(Path(record["cwd"]).resolve(), self.repo.review.resolve())
                self.assertEqual(bool(record["stdin"]), not placeholder)
        self.assertFalse(self.gl.writes)

    def test_rejects_worktree_paths_and_bad_json(self):
        inside = self.repo.review / "findings.json"
        shutil.copy(self.findings, inside)
        for args in (("--findings", inside),
                     ("--findings", self.findings, "--evidence-dir", self.repo.review / "proof")):
            p = self.call(1, *args)
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertEqual(p.stdout, "")
            self.assertIn("作業フォルダの外", p.stderr)
        for value in ("{", '{"findings": {}}'):
            self.findings.write_text(value, encoding="utf-8")
            self.assertEqual(self.call(1, "--findings", self.findings).returncode, 2)
        self.assertFalse(self.visible.exists())

    def three_verdicts(self):
        self.write_findings(finding("a"), finding("b", 3), finding("c", 4))
        self.behavior([judgment("c", "inconclusive"), judgment("b", "refuted"), judgment("a")])

    def test_verdicts_evidence_and_file_priority(self):
        self.three_verdicts()
        evidence = mr.TOKEN + " " + mr.gm.finding_mark("x", "app.py", 2) + "\n" + "あ" * 5000
        self.behavior([judgment("c", "inconclusive"), judgment("b", "refuted"), judgment("a", evidence=evidence)])
        result = self.result()
        self.assertEqual(result["mr"], {"iid": "1", "head": self.repo.head})
        self.assertEqual(result["counts"], dict.fromkeys(proof.VERDICTS, 1))
        a, c = result["post"]
        b, = result["drop"]
        self.assertEqual([a["id"], c["id"], b["id"]], ["a", "c", "b"])
        self.assertEqual(c["reason"], "verifier")
        self.assertIn("app.py:2", b["detail"])
        for row in (b, c):
            self.assertEqual(row["evidence"], "")
            self.assertNotIn("evidence_file", row)
        body = a["evidence"]
        self.assertTrue(body.startswith("独立検証（proof）: 裏付けあり\n\n***"))
        self.assertNotIn(mr.TOKEN, body)
        self.assertNotIn("<!-- mr-review-agent:", body)
        self.assertEqual(len(body), 4000)
        self.assertTrue(body.endswith("（長いので途中までです）"))
        self.assertEqual(Path(a["evidence_file"]).read_text(encoding="utf-8"), body)

    def test_downgrades_and_stdout_with_nonzero_exit(self):
        self.write_findings(*(finding(i) for i in "abcdef"))
        self.behavior([judgment("a", evidence=" "), judgment("b", "refuted", ""),
                       judgment("c", {"unknown": True}), judgment("d"), judgment("d"),
                       judgment("outside"), {"verdict": "supported"}, judgment("f")],
                      mode="stdout", exit_code=7)
        result = self.result()
        self.assertEqual(result["verifier"]["status"], "ok")
        self.assertEqual(result["verifier"]["exit_code"], 7)
        self.assertEqual([(row["id"], row["reason"]) for row in result["post"]],
                         list(zip("abcdef", ["no_evidence", "no_evidence", "missing", "missing", "missing", ""])))
        self.assertEqual(result["drop"], [])

    def test_invalid_limit_and_empty(self):
        self.write_findings({"id": "missing"}, finding("dup"), finding("dup"), finding("../escape"),
                            finding("a"), finding("extra"))
        self.env["MR_REVIEW_PROOF_MAX_FINDINGS"] = "1"
        result = self.result()
        self.assertEqual([row["reason"] for row in result["post"]],
                         ["invalid_finding"] * 4 + ["", "not_checked"])
        visible = json.loads(self.visible.read_text(encoding="utf-8"))["input"]
        self.assertNotIn('"id": "extra"', visible)
        self.assertNotIn('"id": "dup"', visible)
        self.visible.unlink()
        self.write_findings()
        result = self.result()
        self.assertEqual(result["verifier"]["status"], "skipped")
        self.assertEqual(result["counts"], dict.fromkeys(proof.VERDICTS, 0))
        self.assertFalse(self.visible.exists())

    def assert_failed(self, status):
        result = self.result()
        self.assertEqual(result["verifier"]["status"], status)
        self.assertEqual(result["drop"], [])
        row, = result["post"]
        self.assertEqual((row["verdict"], row["reason"], row["evidence"]),
                         ("inconclusive", "verifier_failed", ""))
        self.assertNotIn("evidence_file", row)
        return row

    def test_configuration_and_unreadable_replies(self):
        for setting in ("", "{}", '["python", 5]'):
            self.env["MR_REVIEW_PROOF_AGENT"] = setting
            self.assert_failed("not_configured")
        self.env["MR_REVIEW_PROOF_AGENT"] = json.dumps([str(self.tmp / "missing-executable")])
        self.assert_failed("launch_failed")
        self.assertFalse(self.visible.exists())
        for mode in ("broken", "wrong_shape"):
            self.behavior(mode=mode)
            self.assert_failed("unparseable")

    def test_requires_mr_and_current_checkout(self):
        with mr.mock.patch.dict(self.env, {"MR_REVIEW_REPO_DIR": ""}):
            self.assert_failed("not_ready")
        with mr.mock.patch.dict(self.env, {"GITLAB_URL": self.gl.url + "/unavailable"}):
            self.assert_failed("request_unavailable")
        with mr.mock.patch.dict(self.gl.mrs["1"], {"state": "closed"}):
            self.assert_failed("request_unavailable")
        mr.git(self.repo.review, "checkout", "--detach", self.repo.base)
        self.assert_failed("not_ready")
        mr.git(self.repo.review, "checkout", "--detach", self.repo.head)
        self.gl.push(1, "b" * 40)
        self.assert_failed("not_ready")
        self.assertFalse(self.visible.exists())

    def test_timeout_discards_reply_and_stops_child(self):
        self.behavior([judgment("a")], mode="timeout")
        self.env["MR_REVIEW_PROOF_TIMEOUT_SEC"] = "1"
        row = self.assert_failed("timeout")
        self.assertTrue(self.visible.exists())
        self.post(row)
        self.assertIn('evidence="hypothesis"', self.gl.discussions["1"][0]["notes"][0]["body"])
        # 子が生きていれば、親の終了後にこのファイルを作る。
        self.assertFalse((self.tmp / "child-survived").exists())
        import time
        time.sleep(2)
        self.assertFalse((self.tmp / "child-survived").exists())

    def post(self, row, use_evidence=True):
        args = ("--evidence-file", row["evidence_file"]) if use_evidence and "evidence_file" in row else ()
        code, out = self.repo.cli("comment", 1, "--path", row["path"], "--line", row["line"],
                                  "--body", row["claim"], *args)
        self.assertEqual(code, 0, out)
        return out

    def assert_posts(self):
        verified, hypothesis = [t["notes"][0]["body"] for t in self.gl.discussions["1"]]
        self.assertIn('evidence="verified"', verified)
        self.assertIn("### 証拠\n\n独立検証（proof）: 裏付けあり", verified)
        self.assertIn('evidence="hypothesis"', hypothesis)
        self.assertIn(mr.gm.HYPOTHESIS_NOTICE, hypothesis)
        context = self.context()
        self.assertIn("〔検証済み〕", context)
        self.assertIn("〔仮説（未検証）〕", context)

    def test_posts_use_existing_evidence_and_deduplication(self):
        self.three_verdicts()
        result = self.result()
        self.assertFalse(self.gl.writes)
        for row in result["post"]:
            self.post(row)
        self.assert_posts()
        self.assertIn("同じ指摘が既にあります", self.post(result["post"][0], use_evidence=False))
        self.assertEqual(len(self.gl.discussions["1"]), 2)

    def test_plugin_and_direct_entries(self):
        direct = self.result()
        plugin = self.result(entry=(str(mr.CHANGE_WATCH), "--plugin", str(PLUGIN), "proof"))
        both = (str(mr.CHANGE_WATCH), "--plugin", "gitlab_mr", "--plugin", "gitlab_proof")
        combined = self.result(entry=(*both, "proof"))
        for result in (direct, plugin, combined):
            self.assertEqual(result["verifier"]["status"], "ok")
            result["verifier"].pop("elapsed_sec")
        self.assertEqual(direct, plugin)
        self.assertEqual(direct, combined)
        response = self.call("mr", "show", "#1", "app.py", entry=both)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertIn("y = x * 2", response.stdout)

    @unittest.skipUnless(mr.yaml, "PyYAML が無い")
    def test_dag_flow(self):
        with mr.mock.patch.object(sys, "path", [str(mr.CHANGE_WATCH.parent / "test"), *sys.path]):
            import dagrun
        dag = dagrun.load(DAG)
        dagrun.check_shape(self, dag, DAG.name)
        self.assertEqual(dag["steps"][1]["timeout_sec"], 3600)
        cfg = mr.yaml.safe_load((mr.ROOT / "change-watch.yaml.example").read_text(encoding="utf-8"))
        cfg["state_dir"] = str(self.tmp / "state")
        source = cfg["sources"]["gitlab-mrs"]
        for key in ("schedule", "jitter_sec", "busy_wait_sec"):
            source.pop(key)
        source.update(failure_backoff_sec=0, retry={"attempts": 1})
        cfg_path, conn = self.tmp / "change-watch.yaml", self.tmp / "connections.json"
        cfg_path.write_text(mr.yaml.safe_dump(cfg), encoding="utf-8")
        conn.write_text(json.dumps({"gitlab": [{"label": "default", "url": self.gl.url, "token": mr.TOKEN}]}),
                        encoding="utf-8")
        self.three_verdicts()

        def review(step, variables):
            self.assertEqual((step["action"], step["with"]["provider"]), ("harness.run", "kiro"))
            prompt = dagrun._expand(step["with"]["prompt"], variables)
            match = re.search(r"`(proof-cli \d+) --findings <そのファイル> --evidence-dir <一時フォルダ>`", prompt)
            self.assertIsNotNone(match, prompt)
            _, iid = match[1].split()
            self.assertEqual(iid, "1")
            self.context()
            p = self.call(iid, "--findings", self.findings, "--evidence-dir", self.evidence)
            self.assertEqual(p.returncode, 0, p.stderr)
            result = json.loads(p.stdout)
            for row in result["post"]:
                self.post(row)
            counts = result["counts"]
            code, out = self.repo.cli("summary", iid, "--body",
                                      f"検証済み {counts['supported']}・仮説 {counts['inconclusive']}・取下げ {counts['refuted']}")
            self.assertEqual(code, 0, out)
            self.assertFalse(Path(json.loads(self.visible.read_text(encoding="utf-8"))["request"]).parent.exists())
            return 0

        def run():
            return dagrun.run(DAG, aw=[sys.executable, str(mr.CHANGE_WATCH)], head=[], handle=review,
                              env={**os.environ, **self.env, "MR_REVIEW_PROOF_CLI": "proof-cli"},
                              paths={"/home/you/.github/connections.yaml": str(conn),
                                     "/home/you/.config/change-watch/gitlab-mr-review-agent/change-watch.yaml": str(cfg_path)})

        self.assertEqual(run(), {"get": 0, "review": 0, "commit": 0})
        self.assertEqual(run(), {"get": 0, "review": None, "commit": None})
        # summary 済みの context は差分を省くため、印は GitLab の本文で確かめる。
        verified, hypothesis = [t["notes"][0]["body"] for t in self.gl.discussions["1"]]
        self.assertIn('evidence="verified"', verified)
        self.assertIn('evidence="hypothesis"', hypothesis)
        self.assertIn("検証済み 1・仮説 1・取下げ 1", self.gl.notes["1"][0]["body"])


if __name__ == "__main__":
    unittest.main()
