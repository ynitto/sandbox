import io
from unittest import mock

from _shared import *  # noqa: F401,F403

from agent_audit import reconcile


class ReconcileTests(AuditTestCase):
    def setUp(self):
        super().setUp()
        self.sessions = os.path.join(self.tmp, "sessions")
        self.spec = {"session_log": {"format": "jsonl-dir", "paths": [self.sessions],
                                     "usage": True}}

    def test_detects_missing_orphan_and_coverage(self):
        path = os.path.join(self.sessions, "one.jsonl")
        claude_session_jsonl(path, sid="source-only")
        st = self.make_store()
        orphan_id = store.record_id("cli-native:fake", self.sessions, "store-only")
        st.append_record({"id": orphan_id, "_epoch": time.time(), "ts": util.now_iso(),
                          "kind": "session", "source": "fake-native",
                          "session_id": "store-only"})
        with mock.patch.object(reconcile, "agent_defs_with_session_log",
                               return_value=[("fake", self.spec)]):
            rows = reconcile.reconcile(st)
        self.assertEqual(rows[0]["discovered"], 1)
        self.assertEqual(rows[0]["collected"], 0)
        self.assertEqual(rows[0]["missing_session_ids"], ["source-only"])
        self.assertEqual(rows[0]["orphaned_session_ids"], ["store-only"])
        self.assertEqual(rows[0]["coverage"], 0.0)

    def test_cli_json_is_machine_readable(self):
        claude_session_jsonl(os.path.join(self.sessions, "one.jsonl"), sid="missing-json")
        with mock.patch("agent_audit.reconcile.agent_defs_with_session_log",
                        return_value=[("fake", self.spec)]), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli_main(["--audit-dir", self.audit_dir, "reconcile", "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["sources"][0]["missing_session_ids"], ["missing-json"])
        self.assertEqual(payload["sources"][0]["coverage"], 0.0)

    def test_doctor_warns_from_the_same_reconcile_result(self):
        args = self.make_args()
        args._config_path = None
        args._config = {}
        with mock.patch("agent_audit.reconcile.reconcile", return_value=[{
                "source": "fake-native", "missing": 1, "orphaned": 0
        }]), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            from agent_audit.doctor import cmd_doctor
            self.assertEqual(cmd_doctor(args), 0)
        self.assertIn("reconcile coverage 異常", out.getvalue())


if __name__ == "__main__":
    unittest.main()
