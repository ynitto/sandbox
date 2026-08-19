"""regression_check.py の回帰テスト（K4: 整理の回帰ゲート）。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.5-4

「整理前に引けていた記憶が、整理の後も（統合されていれば統合先が）引けるか」だけを
決定的に確認する。ネットワーク・skill-registry.json 不要、ファイルシステムのみで完結する。

CI には未接続（このリポジトリの `.github/skills/*` は現状テスト対象外）。
実行: python3 -m unittest discover -s .github/skills/ltm-use/tests
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rc = _load("regression_check", "regression_check.py")
mu = _load("memory_utils", "memory_utils.py")


def _mem_text(*, mem_id: str, title: str, access_count: int = 0,
             status: str = "active", consolidated_to: str = "") -> str:
    fields = [
        f"id: {mem_id}", f'title: "{title}"', "created: 2026-01-01", "updated: 2026-01-01",
        "status: " + status, "scope: home", f"access_count: {access_count}",
    ]
    if consolidated_to:
        fields.append(f"consolidated_to: {consolidated_to}")
    return "---\n" + "\n".join(fields) + "\n---\n\n本文\n"


def _write(memory_dir: Path, rel_path: str, text: str) -> Path:
    p = memory_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class RegressionCheckTests(unittest.TestCase):
    def test_snapshot_ignores_never_accessed_memories(self):
        """access_count=0（一度も引かれていない＝退役候補）は回帰ゲートの対象にしない。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限", access_count=0))
            baseline = rc.build_snapshot(str(root))
            self.assertEqual(baseline, {})

    def test_snapshot_records_whether_self_title_finds_itself(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由", access_count=3))
            baseline = rc.build_snapshot(str(root))
            self.assertIn("mem-a", baseline)
            self.assertTrue(baseline["mem-a"]["found"])

    def test_compare_no_regression_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由", access_count=3))
            baseline = rc.build_snapshot(str(root))
            result = rc.compare(str(root), baseline)
            self.assertEqual(result["regressions"], [])
            self.assertEqual(result["checked"], 1)

    def test_compare_follows_consolidation_chain_and_finds_no_regression(self):
        """consolidate で archived + consolidated_to になっても、統合先が同じ問いで
        引ければ回帰ではない。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由", access_count=3))
            baseline = rc.build_snapshot(str(root))
            self.assertTrue(baseline["mem-a"]["found"])

            # consolidate 相当: 元記憶を archived + consolidated_to にし、
            # 統合先の新記憶に旧タイトルの語をそのまま引き継がせる（実務でも生じる形）。
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由",
                access_count=3, status="archived", consolidated_to="mem-b"))
            _write(root, "general/b.md", _mem_text(
                mem_id="mem-b", title="JWT の有効期限を15分にした理由（統合後）"))

            result = rc.compare(str(root), baseline)
            self.assertEqual(result["regressions"], [])

    def test_compare_flags_unfindable_consolidation_target(self):
        """統合先のタイトルが旧クエリと無関係になっていれば回帰として検知する。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由", access_count=3))
            baseline = rc.build_snapshot(str(root))

            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由",
                access_count=3, status="archived", consolidated_to="mem-b"))
            _write(root, "general/b.md", _mem_text(
                mem_id="mem-b", title="デプロイ手順の見直し"))

            result = rc.compare(str(root), baseline)
            self.assertEqual(len(result["regressions"]), 1)
            self.assertEqual(result["regressions"][0]["kind"], "unfindable")
            self.assertEqual(result["regressions"][0]["resolved_to"], "mem-b")

    def test_compare_flags_deletion_of_previously_found_memory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由", access_count=3))
            baseline = rc.build_snapshot(str(root))
            p.unlink()                                    # cleanup 相当の削除
            result = rc.compare(str(root), baseline)
            self.assertEqual(len(result["regressions"]), 1)
            self.assertEqual(result["regressions"][0]["kind"], "deleted")

    def test_revert_restores_archived_source_of_unfindable_consolidation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由", access_count=3))
            baseline = rc.build_snapshot(str(root))
            a_path = _write(root, "general/a.md", _mem_text(
                mem_id="mem-a", title="JWT の有効期限を15分にした理由",
                access_count=3, status="archived", consolidated_to="mem-b"))
            _write(root, "general/b.md", _mem_text(mem_id="mem-b", title="デプロイ手順の見直し"))

            regressions = rc.compare(str(root), baseline)["regressions"]
            outcome = rc.revert(str(root), regressions)
            self.assertEqual(len(outcome["reverted"]), 1)
            self.assertEqual(outcome["unrevertable"], [])

            meta, _ = mu.parse_frontmatter(a_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "active")
            self.assertEqual(meta.get("consolidated_to"), "")

    def test_revert_reports_deletions_as_unrevertable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            regressions = [{"mem_id": "mem-a", "title": "x", "kind": "deleted",
                           "resolved_to": None}]
            outcome = rc.revert(str(root), regressions)
            self.assertEqual(outcome["reverted"], [])
            self.assertEqual(len(outcome["unrevertable"]), 1)


if __name__ == "__main__":
    unittest.main()
