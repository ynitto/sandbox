import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_project as ap


class ProjectPlanReviewGateTest(unittest.TestCase):
    def test_human_wait_blocks_no_consumable_replan_trigger(self):
        tasks = [ap.Task(id="T-plan", title="UI backlog proposal", status="proposed")]
        self.assertTrue(ap._has_project_human_wait(tasks))
        self.assertFalse(any(t.consumable() for t in tasks))

    def test_charter_scope_ignores_other_charters(self):
        tasks = [ap.Task(id="T-plan", title="Other proposal", status="proposed", extra=[("charter", "v2")])]
        self.assertFalse(ap._has_project_human_wait(tasks, "v1"))
        self.assertTrue(ap._has_project_human_wait(tasks, "v2"))


if __name__ == "__main__":
    unittest.main()
