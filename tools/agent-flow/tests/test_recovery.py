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
        self.assertEqual(kf.read_json(self.bus.final_path)["results"]["work"]["status"], "done")

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


if __name__ == "__main__":
    unittest.main()
