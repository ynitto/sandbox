"""agent-flow human interaction contract tests."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403
from _shared import _Args, _park_args  # noqa: E402


class InteractionBusTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kf-interaction-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.bus = kf.Bus(self.root, "run1")
        self.bus.ensure_run("request")

    def test_request_is_created_once(self):
        request = kf._interaction.build_request(
            "run1", "review", {"mode": "approval", "prompt": "進めますか"})
        self.assertTrue(self.bus.write_interaction_request(request))
        self.assertFalse(self.bus.write_interaction_request(request))
        self.assertEqual(self.bus.read_interaction_request(request["interaction_id"]), request)

    def test_responses_are_append_only(self):
        request = kf._interaction.build_request(
            "run1", "review", {"mode": "approval", "prompt": "進めますか"})
        self.bus.write_interaction_request(request)
        response = kf._interaction.validate_response(request, {
            "version": 1, "interaction_id": request["interaction_id"],
            "response_id": "01-first", "actor": "user",
            "answer": {"decision": "approved"}, "submitted_at": "2026-08-10T01:00:00Z",
        })
        self.assertTrue(self.bus.write_interaction_response(response))
        self.assertFalse(self.bus.write_interaction_response(response))
        self.assertEqual(self.bus.list_interaction_responses(request["interaction_id"]), [response])

    def test_human_claim_parks_and_releases_worker(self):
        node = {
            "id": "review", "goal": "人の確認を待つ", "kind": "human", "deps": [],
            "interaction": {"mode": "approval", "prompt": "進めますか", "timeout_seconds": 60},
        }
        self.bus.write_graph({"nodes": {"review": node}, "iteration": 0})
        self.bus.write_task(node)
        self.assertTrue(self.bus.try_claim("review", "worker", 60))
        request = kf.park_human_interaction(self.bus, "review", node, "worker", 30)
        self.assertEqual(self.bus.read_wait("review")["interaction_id"], request["interaction_id"])
        self.assertEqual(self.bus.node_state("review"), "waiting")
        self.assertIsNone(self.bus._winner("review"))

    def test_worker_parks_human_without_calling_executor(self):
        node = {
            "id": "review", "goal": "人の確認を待つ", "kind": "human", "deps": [],
            "interaction": {"mode": "approval", "prompt": "進めますか", "timeout_seconds": 60},
        }
        self.bus.write_graph({"nodes": {"review": node}, "iteration": 0})
        self.bus.write_task(node)
        self.bus.set_status("executing")
        args = _Args(self.root, run_id="run1", node_id="worker", executor="stub",
                     poll=0, keep_alive=False, idle_exit=True, lease=60,
                     cleanup_per_node=False, model=None)
        execute = mock.Mock(side_effect=AssertionError("executor called"))
        with mock.patch.object(kf, "make_executor", return_value=execute):
            self.assertEqual(kf.cmd_work(args), 0)
        execute.assert_not_called()
        self.assertEqual(self.bus.node_state("review"), "waiting")

    def test_service_waits_resolves_human_response_without_executor_poll(self):
        node = {
            "id": "review", "goal": "人の確認を待つ", "kind": "human", "deps": [],
            "interaction": {"mode": "approval", "prompt": "進めますか", "timeout_seconds": 60},
        }
        request = kf.park_human_interaction(self.bus, "review", node, "worker", 30)
        response = kf._interaction.validate_response(request, {
            "version": 1, "interaction_id": request["interaction_id"],
            "response_id": "01-first", "actor": "user",
            "answer": {"decision": "approved"}, "submitted_at": request["created_at"],
        })
        self.bus.write_interaction_response(response)
        self.assertEqual(kf.service_waits(
            self.bus, _park_args(executor="stub"), only_runs=["run1"], daemon_id="test"), 1)
        result = self.bus.read_result("review")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["data"]["outcome"], "approved")
        self.assertIsNone(self.bus.read_wait("review"))


class InteractionContinuationTests(unittest.TestCase):
    def test_failed_human_is_not_retried(self):
        decision, tasks, _ = kf.continue_stub(
            "request",
            {"review": {"id": "review", "goal": "確認", "kind": "human", "deps": []}},
            {"review": {"status": "failed", "data": {"outcome": "rejected"}}},
            0,
        )
        self.assertEqual(tasks, [])
        self.assertEqual(decision, "failed")


if __name__ == "__main__":
    unittest.main()
