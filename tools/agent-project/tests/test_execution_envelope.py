import json

from _shared import *  # noqa: F401,F403


class ExecutionEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kp-envelope-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = cfg_for(self.tmp)

    def test_plan_approval_persists_scope_acceptance_and_policy_revision(self):
        control_dir = self.tmp / "control"
        control_dir.mkdir()
        (control_dir / "control.json").write_text(json.dumps({
            "version": 2, "revision": 42,
        }), encoding="utf-8")
        task = km.Task(id="T1", title="限定修正", status="proposed", extra=[
            ("paths", "src/format.py, tests/test_format.py"),
            ("protected_paths", "schemas/, docs/architecture/"),
            ("task_acceptance_criteria", "pytest tests/test_format.py が成功する"),
            ("verification_commands", "pytest tests/test_format.py"),
            ("candidate_pins", '[{"agent_cli":"aider","model":"gemma4:e4b"}]'),
            ("external_execution_allowed", "false"),
        ])
        with mock.patch.dict(os.environ, {"AGENT_CONTROL_DIR": str(control_dir)}):
            envelope = km.approve_execution_envelope(self.cfg, task, "計画を確認")

        saved = json.loads(km.execution_envelope_path(self.cfg, task.id).read_text(encoding="utf-8"))
        self.assertEqual(saved, envelope)
        self.assertEqual(saved["policy_snapshot"]["control_revision"], 42)
        self.assertEqual(saved["scope"]["paths"], ["src/format.py", "tests/test_format.py"])
        self.assertEqual(saved["scope"]["protected"], ["schemas/", "docs/architecture/"])
        self.assertEqual(saved["acceptance"], ["pytest tests/test_format.py が成功する"])
        self.assertFalse(saved["external_execution"]["allowed"])
        self.assertEqual(saved["candidate_permissions"]["pins"][0]["agent_cli"], "aider")
        self.assertEqual(task.get("execution_envelope_digest"), saved["digest"])

    def test_plan_approve_freezes_envelope_before_task_becomes_ready(self):
        task = km.Task(id="T2", title="承認対象", status="proposed", verify="pytest -q", extra=[
            ("paths", "src/only.py"),
        ])

        km._plan_approve(self.cfg, task, "CLIで承認")

        saved = json.loads(km.execution_envelope_path(self.cfg, task.id).read_text(encoding="utf-8"))
        persisted = km.load_tasks(self.cfg.backlog)[0]
        self.assertEqual(persisted.status, "ready")
        self.assertEqual(persisted.get("execution_envelope_digest"), saved["digest"])
        self.assertEqual(saved["approval"]["reason"], "CLIで承認")
        self.assertEqual(saved["scope"]["paths"], ["src/only.py"])

    def test_run_meta_keeps_first_approved_envelope_snapshot_immutable(self):
        task = km.Task(id="T3", title="runへ転記", verify="true", extra=[
            ("paths", "src/original.py"),
        ])
        approved = km.approve_execution_envelope(self.cfg, task, "承認")
        run_dir = self.cfg.bus / "runs" / "run-T3"
        run_dir.mkdir(parents=True)
        meta_path = run_dir / "meta.json"
        meta_path.write_text(json.dumps({"status": "running", "request": "keep"}), encoding="utf-8")

        self.assertTrue(km.snapshot_execution_envelope_to_run(self.cfg, task, "run-T3"))
        first = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(first["execution_envelope"], approved)
        self.assertEqual(first["execution_envelope_digest"], approved["digest"])
        self.assertEqual(first["request"], "keep")

        changed = dict(approved)
        changed["scope"] = {"paths": ["src/changed.py"]}
        km.execution_envelope_path(self.cfg, task.id).write_text(
            json.dumps(changed), encoding="utf-8")
        self.assertFalse(km.snapshot_execution_envelope_to_run(self.cfg, task, "run-T3"))
        second = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(second["execution_envelope"]["scope"]["paths"], ["src/original.py"])


if __name__ == "__main__":
    unittest.main()
