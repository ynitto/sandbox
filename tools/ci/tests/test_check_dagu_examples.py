"""`tools/ci/check_dagu_examples.py` の単体テスト。最後の 1 本は今ある例そのものを見る。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_dagu_examples as checker   # noqa: E402

GOOD = {
    "description": "毎晩点検する",
    "schedule": "0 2 * * *",
    "steps": [
        {"name": "pull", "action": "exec", "with": {"command": "git", "args": ["pull"]}},
        {"name": "lint", "depends": ["pull"], "action": "exec", "with": {"command": "/home/you/.local/bin/x"}},
    ],
}


class CheckDoc(unittest.TestCase):
    def test_good_example_passes(self):
        self.assertEqual(checker.check_doc(GOOD), [])

    def test_missing_description_and_bad_schedule(self):
        doc = dict(GOOD, description="", schedule="@daily")
        errors = checker.check_doc(doc)
        self.assertEqual(len(errors), 2, errors)

    def test_depends_must_point_to_an_earlier_step(self):
        doc = dict(GOOD, steps=[GOOD["steps"][1], GOOD["steps"][0]])
        self.assertTrue(any("depends" in e for e in checker.check_doc(doc)))

    def test_exec_does_not_expand_tilde(self):
        doc = dict(GOOD, steps=[{"name": "a", "action": "exec", "with": {"command": "~/.local/bin/x"}}])
        self.assertTrue(any("~" in e for e in checker.check_doc(doc)))

    def test_exec_needs_command(self):
        doc = dict(GOOD, steps=[{"name": "a", "action": "exec", "with": {}}])
        self.assertTrue(any("command" in e for e in checker.check_doc(doc)))


class RepositoryExamples(unittest.TestCase):
    def test_nightly_examples_exist(self):
        names = {p.relative_to(checker.ROOT).as_posix() for p in checker.example_files()}
        for expected in ("tools/codd-agent/dags/codd-lint.example.yaml",
                         "tools/testcase-lint/dags/testcase-lint.example.yaml",
                         "tools/agent-audit/dags/agent-audit.example.yaml",
                         "tools/gitlab-repo-sync/dags/gitlab-repo-sync.example.yaml"):
            self.assertIn(expected, names)

    def test_all_examples_are_well_formed(self):
        self.assertEqual(checker.main(), 0)


if __name__ == "__main__":
    unittest.main()
