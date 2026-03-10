"""record_feedback.py のユニットテスト。"""
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import record_feedback as rf


def _base_skill(name="my-skill", source_repo="local"):
    return {
        "name": name,
        "source_repo": source_repo,
        "feedback_history": [],
        "pending_refinement": False,
        "metrics": {},
    }


def _base_reg(skills=None):
    return {
        "version": 6,
        "installed_skills": skills or [],
        "node": {"id": None},
    }


# ---------------------------------------------------------------------------
# _refine_threshold
# ---------------------------------------------------------------------------

class TestRefineThreshold:
    def test_workspace_threshold_is_1(self):
        skill = {"source_repo": "workspace"}
        assert rf._refine_threshold(skill) == 1

    def test_installed_threshold_is_3(self):
        skill = {"source_repo": "local"}
        assert rf._refine_threshold(skill) == 3

    def test_repo_installed_threshold_is_3(self):
        skill = {"source_repo": "team-skills"}
        assert rf._refine_threshold(skill) == 3

    def test_custom_threshold_overrides(self):
        skill = {"source_repo": "workspace", "refine_threshold": 5}
        assert rf._refine_threshold(skill) == 5


# ---------------------------------------------------------------------------
# _unrefined_problem_count
# ---------------------------------------------------------------------------

class TestUnrefinedProblemCount:
    def test_empty_history(self):
        skill = {"feedback_history": []}
        assert rf._unrefined_problem_count(skill) == 0

    def test_ok_not_counted(self):
        skill = {
            "feedback_history": [
                {"verdict": "ok", "refined": False},
            ]
        }
        assert rf._unrefined_problem_count(skill) == 0

    def test_unrefined_problems_counted(self):
        skill = {
            "feedback_history": [
                {"verdict": "needs-improvement", "refined": False},
                {"verdict": "broken", "refined": False},
                {"verdict": "ok", "refined": False},
            ]
        }
        assert rf._unrefined_problem_count(skill) == 2

    def test_refined_problems_not_counted(self):
        skill = {
            "feedback_history": [
                {"verdict": "needs-improvement", "refined": True},
                {"verdict": "broken", "refined": False},
            ]
        }
        assert rf._unrefined_problem_count(skill) == 1


# ---------------------------------------------------------------------------
# _update_duration_metrics
# ---------------------------------------------------------------------------

class TestUpdateDurationMetrics:
    def test_first_entry(self):
        metrics = {"total_executions": 1}
        rf._update_duration_metrics(metrics, 10.0)
        assert metrics["avg_duration_sec"] == 10.0

    def test_none_duration_skipped(self):
        metrics = {}
        rf._update_duration_metrics(metrics, None)
        assert "avg_duration_sec" not in metrics

    def test_moving_average(self):
        metrics = {"total_executions": 2, "avg_duration_sec": 10.0}
        rf._update_duration_metrics(metrics, 20.0)
        # (10.0 + (20.0 - 10.0) / 2) = 15.0
        assert metrics["avg_duration_sec"] == 15.0

    def test_p90_tracks_max(self):
        metrics = {"total_executions": 1}
        rf._update_duration_metrics(metrics, 5.0)
        rf._update_duration_metrics(metrics, 15.0)
        assert metrics["p90_duration_sec"] == 15.0


# ---------------------------------------------------------------------------
# _update_subagent_metrics
# ---------------------------------------------------------------------------

class TestUpdateSubagentMetrics:
    def test_first_entry(self):
        metrics = {"total_executions": 1}
        rf._update_subagent_metrics(metrics, 3)
        assert metrics["avg_subagent_calls"] == 3.0

    def test_none_skipped(self):
        metrics = {}
        rf._update_subagent_metrics(metrics, None)
        assert "avg_subagent_calls" not in metrics

    def test_moving_average(self):
        metrics = {"total_executions": 2, "avg_subagent_calls": 2.0}
        rf._update_subagent_metrics(metrics, 4)
        # (2.0 + (4 - 2.0) / 2) = 3.0
        assert metrics["avg_subagent_calls"] == 3.0


# ---------------------------------------------------------------------------
# record_feedback（コア関数）
# ---------------------------------------------------------------------------

class TestRecordFeedback:
    def _record(self, skill, verdict, note="", **kwargs):
        reg = _base_reg([skill])
        with patch.object(rf, "_append_metrics_log"):
            result = rf.record_feedback(skill["name"], verdict, note, reg, **kwargs)
        return result

    def test_ok_adds_history(self):
        skill = _base_skill()
        reg = self._record(skill, "ok")
        history = reg["installed_skills"][0]["feedback_history"]
        assert len(history) == 1
        assert history[0]["verdict"] == "ok"
        assert history[0]["refined"] is False

    def test_ok_updates_metrics(self):
        skill = _base_skill()
        reg = self._record(skill, "ok")
        metrics = reg["installed_skills"][0]["metrics"]
        assert metrics["total_executions"] == 1
        assert metrics["ok_rate"] == 1.0

    def test_needs_improvement_counted(self):
        skill = _base_skill()
        reg = self._record(skill, "needs-improvement", "some note")
        history = reg["installed_skills"][0]["feedback_history"]
        assert history[0]["note"] == "some note"

    def test_workspace_triggers_pending_after_1_problem(self):
        skill = _base_skill(source_repo="workspace")
        reg = self._record(skill, "broken")
        assert reg["installed_skills"][0]["pending_refinement"] is True

    def test_installed_triggers_pending_after_3_problems(self):
        skill = _base_skill(source_repo="local")
        reg = _base_reg([skill])

        with patch.object(rf, "_append_metrics_log"):
            for _ in range(2):
                reg = rf.record_feedback(skill["name"], "needs-improvement", "", reg)
            # まだ pending にならない
            assert reg["installed_skills"][0]["pending_refinement"] is False

            # 3件目で pending になる
            reg = rf.record_feedback(skill["name"], "needs-improvement", "", reg)
        assert reg["installed_skills"][0]["pending_refinement"] is True

    def test_unknown_skill_returns_unchanged(self):
        reg = _base_reg([])
        with patch.object(rf, "_append_metrics_log"):
            result = rf.record_feedback("ghost-skill", "ok", "", reg)
        assert result is reg  # 変更なし

    def test_duration_passed_through(self):
        skill = _base_skill()
        reg = self._record(skill, "ok", duration_sec=42.5)
        metrics = reg["installed_skills"][0]["metrics"]
        assert metrics["avg_duration_sec"] == 42.5

    def test_ok_rate_calculation(self):
        skill = _base_skill()
        reg = _base_reg([skill])
        with patch.object(rf, "_append_metrics_log"):
            reg = rf.record_feedback(skill["name"], "ok", "", reg)
            reg = rf.record_feedback(skill["name"], "ok", "", reg)
            reg = rf.record_feedback(skill["name"], "broken", "", reg)
        metrics = reg["installed_skills"][0]["metrics"]
        assert metrics["total_executions"] == 3
        assert round(metrics["ok_rate"], 3) == round(2 / 3, 3)
