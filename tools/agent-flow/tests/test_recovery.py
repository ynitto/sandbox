#!/usr/bin/env python3
"""remote 公開失敗の手動復旧。"""
from _shared import *  # noqa: F401,F403


class ForceCompletePublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_id = "run-publish"
        self.bus = kf.Bus(self.tmp, self.run_id)
        self.bus.ensure_run("publish result")
        self.bus.write_graph({
            "nodes": {"work": {"id": "work", "goal": "g", "deps": [], "kind": "work"}},
            "iteration": 0,
        })
        self.bus.write_task({"id": "work", "goal": "g", "deps": [], "kind": "work"})
        self.bus.write_result("work", "worker", "failed", "実行エラー: push failed", {
            "error_class": "workspace_publish",
            "publication": {
                "state": "failed",
                "url": "git@example.invalid:repo.git",
                "branch": "af/run-publish",
                "commit": "a" * 40,
                "recovery": {"repository": "/repo", "ref": "refs/agent-flow/recovery/run-publish"},
            },
        })
        self.bus.set_status("failed")

    def test_verified_manual_push_completes_a_publication_only_run(self):
        meta = self.bus.run_meta(self.run_id)
        meta["knowledge"] = {"summary": "published insight"}
        kf.write_json_atomic(self.bus.meta_path, meta)
        result = kf.force_complete_publication(
            self.bus,
            self.run_id,
            "認証復旧後に手動 push",
            verifier=lambda publication: {"remote_tip": publication["commit"]},
        )

        self.assertEqual(result["status"], "done")
        repaired = self.bus.read_result("work")
        self.assertEqual(repaired["status"], "done")
        self.assertEqual(repaired["data"]["publication"]["state"], "published-manually")
        self.assertEqual(self.bus.get_status(), "done")
        final = kf.read_json(self.bus.final_path)
        self.assertEqual(final["results"]["work"]["status"], "done")
        self.assertEqual(final["knowledge"]["summary"], "published insight")

    def test_force_complete_is_exposed_as_a_cli_command(self):
        args = kf.build_parser().parse_args([
            "--bus", self.tmp, "force-complete", self.run_id,
            "--reason", "認証復旧後に手動 push",
        ])

        self.assertIs(args.func, kf.cmd_force_complete)
        self.assertEqual(args.run_id, self.run_id)

    def test_verified_publication_resumes_when_a_downstream_node_is_pending(self):
        self.bus.write_graph({
            "nodes": {
                "work": {"id": "work", "goal": "g", "deps": [], "kind": "work"},
                "verify": {"id": "verify", "goal": "v", "deps": ["work"], "kind": "verify"},
            },
            "iteration": 0,
        })
        self.bus.write_task({"id": "verify", "goal": "v", "deps": ["work"], "kind": "verify"})

        result = kf.force_complete_publication(
            self.bus, self.run_id, "手動 push 済み",
            verifier=lambda publication: {"remote_tip": publication["commit"]},
        )

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["remaining"], ["verify"])
        self.assertEqual(self.bus.node_state("work"), "done")
        self.assertEqual(self.bus.node_state("verify"), "pending")
        self.assertFalse(os.path.exists(self.bus.final_path))

    def test_non_publication_failure_cannot_be_forced_complete(self):
        self.bus.write_result("work", "worker", "failed", "tests failed", {
            "error_class": "content",
        })

        with self.assertRaisesRegex(kf.PublicationRecoveryError, "publication failure ではない"):
            kf.force_complete_publication(
                self.bus, self.run_id, "手動 push 済み",
                verifier=lambda publication: {"remote_tip": publication["commit"]},
            )

        self.assertEqual(self.bus.get_status(), "failed")

    def test_safety_preconditions_reject_invalid_force_completion(self):
        meta = self.bus.run_meta(self.run_id)
        meta["knowledge"] = {}
        kf.write_json_atomic(self.bus.meta_path, meta)
        self.assertNotIn("knowledge", kf._recovered_final(self.bus, {}))

        with self.assertRaisesRegex(kf.PublicationRecoveryError, "理由"):
            kf.force_complete_publication(self.bus, self.run_id, "")

        meta = self.bus.run_meta(self.run_id)
        meta["status"] = "done"
        kf.write_json_atomic(self.bus.meta_path, meta)
        with self.assertRaisesRegex(kf.PublicationRecoveryError, "failed の run"):
            kf.force_complete_publication(self.bus, self.run_id, "reason")

        meta["status"] = "failed"
        kf.write_json_atomic(self.bus.meta_path, meta)
        with self.assertRaisesRegex(kf.PublicationRecoveryError, "確認できません"):
            kf.force_complete_publication(
                self.bus, self.run_id, "reason", verifier=lambda _publication: {})

        self.bus.write_result("work", "worker", "done", "already repaired", {})
        with self.assertRaisesRegex(kf.PublicationRecoveryError, "復旧可能"):
            kf.force_complete_publication(self.bus, self.run_id, "reason")

    def test_launch_and_cli_paths_resume_or_finish_safely(self):
        args = types.SimpleNamespace(bus=self.tmp, run_id=self.run_id, reason="manual")
        process = types.SimpleNamespace(pid=4242)
        with mock.patch.object(kf, "_child_base", return_value=["agent-flow"]), \
                mock.patch.object(kf.subprocess, "Popen", return_value=process) as popen:
            pid = kf._launch_recovered_run(args, self.bus, self.run_id)
        self.assertEqual(pid, 4242)
        self.assertEqual(popen.call_args.args[0], [
            "agent-flow", "--run-id", self.run_id, "run", "--from-inbox"])

        cases = [
            ({"status": "running", "remaining": ["verify"]}, 0, True),
            ({"status": "done", "remaining": []}, 0, False),
        ]
        for result, expected_rc, should_launch in cases:
            with self.subTest(status=result["status"]), \
                    mock.patch.object(kf, "make_bus", return_value=self.bus), \
                    mock.patch.object(kf, "force_complete_publication", return_value=dict(result)), \
                    mock.patch.object(kf, "_launch_recovered_run", return_value=99) as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(kf.cmd_force_complete(args), expected_rc)
                self.assertEqual(launch.called, should_launch)

        with mock.patch.object(kf, "make_bus", return_value=self.bus), \
                mock.patch.object(kf, "force_complete_publication",
                                  side_effect=kf.PublicationRecoveryError("unsafe")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(kf.cmd_force_complete(args), 2)


class VerifyRemotePublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.local = os.path.join(self.tmp, "local")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "-b", "main", self.local], check=True)
        pathlib.Path(self.local, "result.txt").write_text("result\n")
        subprocess.run(["git", "-C", self.local, "add", "result.txt"], check=True)
        subprocess.run(
            ["git", "-C", self.local, "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-q", "-m", "result"],
            check=True,
        )
        self.expected = subprocess.run(
            ["git", "-C", self.local, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        subprocess.run(
            ["git", "-C", self.local, "push", "-q", self.remote, "HEAD:refs/heads/af/run-publish"],
            check=True,
        )

    def publication(self, commit=None):
        return {
            "state": "failed", "url": self.remote, "branch": "af/run-publish",
            "commit": commit or self.expected,
            "recovery": {"repository": self.local,
                         "ref": "refs/agent-flow/recovery/run-publish"},
        }

    def test_remote_branch_containing_expected_commit_is_verified(self):

        receipt = kf.verify_remote_publication(self.publication())

        self.assertEqual(receipt["remote_tip"], self.expected)

    def test_remote_branch_without_expected_commit_is_rejected(self):
        with self.assertRaisesRegex(kf.PublicationRecoveryError, "含まれていません"):
            kf.verify_remote_publication(self.publication("f" * 40))

    def test_invalid_publication_coordinates_and_fetch_failure_are_rejected(self):
        invalid_cases = [
            ({}, "URL・branch・commit"),
            ({"url": self.remote, "branch": "af/run-publish", "commit": self.expected,
              "recovery": {"repository": os.path.join(self.tmp, "missing")}}, "ローカルリポジトリ"),
            ({**self.publication(), "branch": "bad branch"}, "branch が不正"),
            ({**self.publication(), "url": os.path.join(self.tmp, "missing.git")}, "確認できません"),
        ]
        for publication, message in invalid_cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                    kf.PublicationRecoveryError, message):
                kf.verify_remote_publication(publication)


if __name__ == "__main__":
    unittest.main()
