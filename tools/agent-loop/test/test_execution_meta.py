#!/usr/bin/env python3
"""Phase 2A execution metadata / launch spec / validation."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class ExecutionMetaTests(unittest.TestCase):
    def test_missing_defaults_to_phase1(self):
        meta = al.normalize_execution(None, request_id="r1")
        self.assertEqual(meta["kind"], "normal")
        self.assertEqual(meta["session_policy"], "persistent")
        self.assertEqual(meta["target_kind"], "managed")
        self.assertEqual(meta["root_id"], "r1")
        self.assertEqual(meta["step"], 1)
        self.assertEqual(meta["max_steps"], 1)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            al.normalize_execution({"execution": {"kind": "workflow"}}, request_id="x")

    def test_unknown_policy_raises(self):
        with self.assertRaises(ValueError):
            al.normalize_execution({"execution": {"session_policy": "temp"}}, request_id="x")

    def test_ralph_prompt_n1_and_continue_final(self):
        p1 = al.build_ralph_prompt("作業して", 1, 1)
        self.assertIn("iteration 1/1", p1)
        self.assertIn("作業して", p1)
        self.assertIn("最終反復", p1)
        p2 = al.build_ralph_prompt("作業して", 2, 5)
        self.assertIn("iteration 2/5", p2)
        self.assertIn("続けて", p2)
        p5 = al.build_ralph_prompt("作業して", 5, 5)
        self.assertIn("最終反復", p5)

    def test_launch_fingerprint_ignores_env(self):
        a = al.launch_fingerprint("cli", ["bin", "--m", "x"], "/tmp")
        b = al.launch_fingerprint("cli", ["bin", "--m", "x"], "/tmp")
        self.assertEqual(a, b)
        c = al.launch_fingerprint("cli", ["bin", "--m", "y"], "/tmp")
        self.assertNotEqual(a, c)

    def test_make_launch_spec_copy(self):
        argv = ["cli", "chat"]
        spec = al.make_launch_spec(
            argv=argv, cwd="/w", profile_name="cli", effective_model="m",
            ownership="managed-ephemeral",
        )
        argv.append("mutated")
        self.assertEqual(spec["argv"], ["cli", "chat"])
        self.assertEqual(spec["ownership"], "managed-ephemeral")

    def test_validate_entries_ralph_requires_max(self):
        with self.assertRaises(ValueError):
            al.validate_entries([{
                "name": "r", "prompt": "p", "mode": "ralph", "interval_minutes": 1,
            }])

    def test_validate_entries_ralph_oneshot_invalid(self):
        with self.assertRaises(ValueError):
            al.validate_entries([{
                "name": "r", "prompt": "p", "mode": "ralph",
                "max_iterations": 3, "oneshot": True, "interval_minutes": 1,
            }])

    def test_validate_entries_oneshot_clean_invalid(self):
        with self.assertRaises(ValueError):
            al.validate_entries([{
                "name": "r", "prompt": "p", "oneshot": True,
                "clean_session": 5, "interval_minutes": 1,
            }])

    def test_validate_entries_external_oneshot_invalid(self):
        with self.assertRaises(ValueError):
            al.validate_entries([{
                "name": "r", "prompt": "p", "target": "ext",
                "oneshot": True, "interval_minutes": 1,
            }])

    def test_validate_external_panes(self):
        panes = al.validate_external_panes([
            {"name": "a", "tmux_target": "s:0.1"},
            {"name": "b", "tmux_target": "%3", "agent_cli": "codex"},
        ])
        self.assertEqual(len(panes), 2)
        with self.assertRaises(ValueError):
            al.validate_external_panes([
                {"name": "a", "tmux_target": "x"},
                {"name": "a", "tmux_target": "y"},
            ])

    def test_write_send_response_phase2_fields(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            al.write_send_response(
                "r1", "processing", pane_id="%1",
                step=2, max_steps=5, sandbox_path="/s", reason=None,
                base_dir=root,
            )
            data = al.read_send_response("r1", root)
            self.assertEqual(data["step"], 2)
            self.assertEqual(data["max_steps"], 5)
            self.assertEqual(data["sandbox_path"], "/s")
            al.write_send_response("r1", "completed", base_dir=root)
            data2 = al.read_send_response("r1", root)
            self.assertEqual(data2["step"], 2)  # preserved
            self.assertEqual(data2["status"], "completed")


if __name__ == "__main__":
    unittest.main()
