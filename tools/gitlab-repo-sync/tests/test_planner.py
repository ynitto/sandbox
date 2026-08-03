#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定コア（planner）の単体テスト。git もネットワークも使わない。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_repo_sync import planner  # noqa: E402

A, B, C = "a" * 40, "b" * 40, "c" * 40


class TestRefScope(unittest.TestCase):
    def setUp(self):
        self.inc = ["refs/heads/main", "refs/heads/release/*", "refs/tags/*"]
        self.exc = ["refs/heads/tmp/*", "refs/tags/nightly-*"]

    def test_included(self):
        self.assertTrue(planner.ref_in_scope("refs/heads/main", self.inc, self.exc))
        self.assertTrue(planner.ref_in_scope("refs/heads/release/1.0", self.inc, self.exc))

    def test_not_included(self):
        self.assertFalse(planner.ref_in_scope("refs/heads/feature/x", self.inc, self.exc))

    def test_exclude_wins_over_include(self):
        self.assertFalse(planner.ref_in_scope("refs/tags/nightly-1", self.inc, self.exc))

    def test_empty_include_matches_nothing(self):
        self.assertFalse(planner.ref_in_scope("refs/heads/main", [], []))


class TestClassifyRef(unittest.TestCase):
    def test_noop_when_equal(self):
        self.assertEqual(planner.classify_ref(A, A, A, A, False), planner.NOOP)

    def test_both_absent_is_noop(self):
        self.assertEqual(planner.classify_ref(None, None, None, None, True), planner.NOOP)

    def test_a_only_creates_on_b(self):
        self.assertEqual(planner.classify_ref(A, None, None, None, False), planner.CREATE_ON_B)

    def test_b_only_creates_on_a(self):
        self.assertEqual(planner.classify_ref(None, B, None, None, False), planner.CREATE_ON_A)

    def test_both_present_needs_merge_base(self):
        self.assertEqual(planner.classify_ref(A, B, None, None, False), planner.COMPARE)

    def test_delete_is_not_propagated_by_default(self):
        self.assertEqual(planner.classify_ref(A, None, A, A, False), planner.CREATE_ON_B)

    def test_delete_is_propagated_when_enabled(self):
        # 前回は両側に在り、残っている a は前回から動いていない → b で消された
        self.assertEqual(planner.classify_ref(A, None, A, A, True), planner.DELETE_ON_A)
        self.assertEqual(planner.classify_ref(None, B, B, B, True), planner.DELETE_ON_B)

    def test_delete_is_not_propagated_when_the_survivor_moved(self):
        # 削除の後に a が進んでいる → 消さずに b へ作り直す
        self.assertEqual(planner.classify_ref(B, None, A, A, True), planner.CREATE_ON_B)

    def test_delete_needs_a_previous_snapshot_on_both_sides(self):
        # 前回 b を観測していない（＝初回）なら、削除ではなく未作成として扱う
        self.assertEqual(planner.classify_ref(A, None, A, None, True), planner.CREATE_ON_B)


class TestResolveCompare(unittest.TestCase):
    def test_a_ahead_pushes_to_b(self):
        self.assertEqual(planner.resolve_compare(A, B, B), planner.PUSH_TO_B)

    def test_b_ahead_pushes_to_a(self):
        self.assertEqual(planner.resolve_compare(A, B, A), planner.PUSH_TO_A)

    def test_diverged(self):
        self.assertEqual(planner.resolve_compare(A, B, C), planner.DIVERGED)

    def test_unrelated_histories_are_diverged(self):
        self.assertEqual(planner.resolve_compare(A, B, None), planner.DIVERGED)

    def test_moved_tag_is_never_auto_synced(self):
        self.assertEqual(planner.resolve_compare(A, B, B, is_tag=True), planner.DIVERGED)


class TestApplyStrategy(unittest.TestCase):
    def test_bidirectional_passes_everything_through(self):
        for action in (planner.PUSH_TO_A, planner.PUSH_TO_B, planner.CREATE_ON_A,
                       planner.CREATE_ON_B, planner.DELETE_ON_A, planner.DIVERGED):
            self.assertEqual(planner.apply_strategy(action, planner.BIDIRECTIONAL), action)

    def test_bidirectional_never_forces(self):
        self.assertEqual(
            planner.apply_strategy(planner.DIVERGED, planner.BIDIRECTIONAL, allow_force=True),
            planner.DIVERGED)

    def test_a_to_b_allows_only_writes_to_b(self):
        self.assertEqual(planner.apply_strategy(planner.PUSH_TO_B, planner.A_TO_B),
                         planner.PUSH_TO_B)
        self.assertEqual(planner.apply_strategy(planner.PUSH_TO_A, planner.A_TO_B),
                         planner.SKIPPED)

    def test_a_to_b_leaves_diverged_alone_without_force(self):
        self.assertEqual(planner.apply_strategy(planner.DIVERGED, planner.A_TO_B),
                         planner.SKIPPED)

    def test_a_to_b_forces_when_allowed(self):
        for action in (planner.PUSH_TO_A, planner.DIVERGED):
            self.assertEqual(planner.apply_strategy(action, planner.A_TO_B, allow_force=True),
                             planner.FORCE_TO_B)

    def test_a_to_b_restores_a_ref_deleted_on_b(self):
        self.assertEqual(planner.apply_strategy(planner.DELETE_ON_A, planner.A_TO_B),
                         planner.CREATE_ON_B)

    def test_a_to_b_prunes_only_when_mirroring(self):
        self.assertEqual(planner.apply_strategy(planner.CREATE_ON_A, planner.A_TO_B),
                         planner.SKIPPED)
        self.assertEqual(
            planner.apply_strategy(planner.CREATE_ON_A, planner.A_TO_B,
                                   allow_force=True, propagate_deletes=True),
            planner.DELETE_ON_B)

    def test_b_to_a_is_the_mirror_image(self):
        self.assertEqual(planner.apply_strategy(planner.PUSH_TO_B, planner.B_TO_A),
                         planner.SKIPPED)
        self.assertEqual(planner.apply_strategy(planner.PUSH_TO_A, planner.B_TO_A),
                         planner.PUSH_TO_A)
        self.assertEqual(planner.apply_strategy(planner.DELETE_ON_B, planner.B_TO_A),
                         planner.CREATE_ON_A)
        self.assertEqual(planner.apply_strategy(planner.DIVERGED, planner.B_TO_A,
                                                allow_force=True), planner.FORCE_TO_A)

    def test_unknown_strategy_raises(self):
        with self.assertRaises(ValueError):
            planner.apply_strategy(planner.NOOP, "sideways")


class TestRefPlan(unittest.TestCase):
    def test_target_side_and_sha(self):
        plan = planner.RefPlan("refs/heads/main", planner.PUSH_TO_B, A, B)
        self.assertEqual(plan.target_side(), planner.SIDE_B)
        self.assertEqual(plan.target_sha(), A)     # b へ書くので a の内容が乗る

        plan = planner.RefPlan("refs/heads/main", planner.PUSH_TO_A, A, B)
        self.assertEqual(plan.target_side(), planner.SIDE_A)
        self.assertEqual(plan.target_sha(), B)

    def test_delete_has_no_target_sha(self):
        plan = planner.RefPlan("refs/heads/x", planner.DELETE_ON_B, A, B)
        self.assertEqual(plan.target_side(), planner.SIDE_B)
        self.assertIsNone(plan.target_sha())

    def test_non_write_actions_have_no_side(self):
        for action in (planner.NOOP, planner.DIVERGED, planner.SKIPPED, planner.CONFLICT):
            self.assertIsNone(planner.RefPlan("refs/heads/x", action, A, B).target_side())


class TestDetectWriteConflicts(unittest.TestCase):
    def test_same_target_same_sha_is_not_a_conflict(self):
        writes = [("repo-b", "refs/heads/main", A, "pair1"),
                  ("repo-b", "refs/heads/main", A, "pair2")]
        self.assertEqual(planner.detect_write_conflicts(writes), {})

    def test_same_target_different_sha_is_a_conflict(self):
        writes = [("repo-b", "refs/heads/main", A, "pair1"),
                  ("repo-b", "refs/heads/main", C, "pair2")]
        conflicts = planner.detect_write_conflicts(writes)
        self.assertEqual(sorted(conflicts[("repo-b", "refs/heads/main")]), ["pair1", "pair2"])

    def test_delete_versus_write_is_a_conflict(self):
        writes = [("repo-b", "refs/heads/main", None, "pair1"),
                  ("repo-b", "refs/heads/main", A, "pair2")]
        self.assertIn(("repo-b", "refs/heads/main"), planner.detect_write_conflicts(writes))

    def test_different_repos_do_not_collide(self):
        writes = [("repo-b", "refs/heads/main", A, "pair1"),
                  ("repo-c", "refs/heads/main", C, "pair2")]
        self.assertEqual(planner.detect_write_conflicts(writes), {})


if __name__ == "__main__":
    unittest.main()
