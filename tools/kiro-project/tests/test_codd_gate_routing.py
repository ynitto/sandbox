"""codd_gate_routing の単体テスト（標準ライブラリ unittest）。

repos.json パス解決（self-hosted＝vcwd 配下は相対パス、非 self-hosted＝vcwd 配下外は絶対パスへ
フォールバック）と --repo-dir マッピング（NAME=DIR）の組み立てを検証する（s6 2・3・4節）。

    python -m unittest discover -s tools/kiro-project/tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_routing as routing


class TestResolveReposArg(unittest.TestCase):
    def test_relative_when_repos_path_under_vcwd(self):
        with tempfile.TemporaryDirectory() as d:
            vcwd = Path(d)
            repos_path = vcwd / ".kiro-project" / "repos.json"
            repos_path.parent.mkdir(parents=True)
            repos_path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                routing.resolve_repos_arg(repos_path, vcwd),
                "./.kiro-project/repos.json",
            )

    def test_absolute_fallback_when_repos_path_outside_vcwd(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            vcwd = Path(d1)
            repos_path = Path(d2) / "repos.json"

            result = routing.resolve_repos_arg(repos_path, vcwd)
            self.assertEqual(result, str(repos_path.resolve()))
            self.assertTrue(Path(result).is_absolute())

    def test_no_vcwd_returns_repos_path_verbatim(self):
        self.assertEqual(
            routing.resolve_repos_arg("./.kiro-project/repos.json"),
            "./.kiro-project/repos.json",
        )

    def test_accepts_string_repos_path(self):
        with tempfile.TemporaryDirectory() as d:
            vcwd = Path(d)
            repos_path = vcwd / "repos.json"
            self.assertEqual(
                routing.resolve_repos_arg(str(repos_path), str(vcwd)),
                "./repos.json",
            )


class TestResolveRepoDirArg(unittest.TestCase):
    def test_default_dir_is_dot(self):
        self.assertEqual(routing.resolve_repo_dir_arg("sandbox"), "sandbox=.")

    def test_explicit_dir_overrides_default(self):
        self.assertEqual(routing.resolve_repo_dir_arg("sandbox", dir="/tmp/clone"), "sandbox=/tmp/clone")


class TestBuildRoutingArgs(unittest.TestCase):
    def test_matches_completion_condition_shape(self):
        # 完了条件のシェルコマンドと同じ形（--repos ./.kiro-project/repos.json --repo-dir sandbox=.）
        with tempfile.TemporaryDirectory() as d:
            vcwd = Path(d)
            repos_path = vcwd / ".kiro-project" / "repos.json"
            repos_path.parent.mkdir(parents=True)

            args = routing.build_routing_args(repos_path, "sandbox", vcwd)
            self.assertEqual(
                args,
                ["--repos", "./.kiro-project/repos.json", "--repo-dir", "sandbox=."],
            )

    def test_composes_with_codd_gate_status_command(self):
        import codd_gate_status as status

        result = status.build_status(["codd-gate"])
        argv = result.command(
            "verify", *routing.build_routing_args("./.kiro-project/repos.json", "sandbox"),
            "--base", "HEAD~1", "--strict",
        )
        self.assertEqual(
            argv,
            ["codd-gate", "verify", "--repos", "./.kiro-project/repos.json",
             "--repo-dir", "sandbox=.", "--base", "HEAD~1", "--strict"],
        )


if __name__ == "__main__":
    unittest.main()
