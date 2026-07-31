"""計画バージョン固有の制約・前提に関する契約テスト。"""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_project as ap


def config_for(root: Path):
    return ap.Config(
        backlog=root / "backlog",
        policy=root / "policy.md",
        decisions=root / "decisions",
        journal=root / "journal.md",
        needs=root / "needs",
        workdir=root,
        bus=root / "bus",
    )


class CharterVersionOverrideTests(unittest.TestCase):
    def _write_master(self, root: Path):
        (root / "charter.md").write_text(
            "# Charter: project\n## master\n## constraints\n- master constraint\n"
            "## assumptions\n- master assumption\n",
            encoding="utf-8",
        )
        (root / "charters").mkdir()

    def test_explicit_version_sections_override_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_master(root)
            (root / "charters" / "v2.md").write_text(
                "# Charter: v2\n## goal\nship v2\n## constraints\n- v2 only\n"
                "## assumptions\n- v2 premise\n",
                encoding="utf-8",
            )
            charter = ap._load_named_charter(config_for(root), "v2")
            self.assertEqual(charter.constraints, ["v2 only"])
            self.assertEqual(charter.assumptions, ["v2 premise"])

    def test_missing_version_sections_fall_back_to_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_master(root)
            (root / "charters" / "legacy.md").write_text(
                "# Charter: legacy\n## goal\nold format\n",
                encoding="utf-8",
            )
            charter = ap._load_named_charter(config_for(root), "legacy")
            self.assertEqual(charter.constraints, ["master constraint"])
            self.assertEqual(charter.assumptions, ["master assumption"])

    def test_explicit_empty_sections_clear_master_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_master(root)
            (root / "charters" / "empty.md").write_text(
                "# Charter: empty\n## goal\nclear inherited values\n## constraints\n\n## assumptions\n",
                encoding="utf-8",
            )
            charter = ap._load_named_charter(config_for(root), "empty")
            self.assertEqual(charter.constraints, [])
            self.assertEqual(charter.assumptions, [])

    def test_task_charter_tag_selects_version_specific_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_master(root)
            (root / "charters" / "v2.md").write_text(
                "# Charter: v2\n## goal\nship v2\n## constraints\n- v2 only\n"
                "## assumptions\n- v2 premise\n",
                encoding="utf-8",
            )
            cfg = config_for(root)
            task = ap.task_from_spec(cfg, {"id": "T1", "title": "version task", "charter": "v2"})
            self.assertEqual(task.get("charter"), "v2")
            charter = ap.charter_for_task(cfg, task)
            self.assertEqual(charter.goal, "ship v2")
            self.assertEqual(charter.constraints, ["v2 only"])
            self.assertEqual(charter.assumptions, ["v2 premise"])


if __name__ == "__main__":
    unittest.main()
