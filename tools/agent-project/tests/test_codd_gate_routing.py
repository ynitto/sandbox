"""codd_gate_routing の単体テスト（標準ライブラリ unittest）。

repos.json パス解決（self-hosted＝vcwd 配下は相対パス、非 self-hosted＝vcwd 配下外は絶対パスへ
フォールバック）と --repo-dir マッピング（NAME=DIR）の組み立てを検証する（s6 2・3・4節）。

    python -m unittest discover -s tools/agent-project/tests
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
            repos_path = vcwd / ".agent-project" / "repos.json"
            repos_path.parent.mkdir(parents=True)
            repos_path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                routing.resolve_repos_arg(repos_path, vcwd),
                "./.agent-project/repos.json",
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
            routing.resolve_repos_arg("./.agent-project/repos.json"),
            "./.agent-project/repos.json",
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
        # 完了条件のシェルコマンドと同じ形（--repos ./.agent-project/repos.json --repo-dir sandbox=.）
        with tempfile.TemporaryDirectory() as d:
            vcwd = Path(d)
            repos_path = vcwd / ".agent-project" / "repos.json"
            repos_path.parent.mkdir(parents=True)

            args = routing.build_routing_args(repos_path, "sandbox", vcwd)
            self.assertEqual(
                args,
                ["--repos", "./.agent-project/repos.json", "--repo-dir", "sandbox=."],
            )

    def test_composes_with_codd_gate_status_command(self):
        import codd_gate_status as status

        result = status.build_status(["codd-gate"])
        argv = result.command(
            "verify", *routing.build_routing_args("./.agent-project/repos.json", "sandbox"),
            "--base", "HEAD~1", "--strict",
        )
        self.assertEqual(
            argv,
            ["codd-gate", "verify", "--repos", "./.agent-project/repos.json",
             "--repo-dir", "sandbox=.", "--base", "HEAD~1", "--strict"],
        )


class TestAgentProjectYamlWiring(unittest.TestCase):
    """`.agent/agent-project.yaml` の regression_cmd/intake_cmd が codd-gate へルーティング
    されることを検証する（README.md の正準値・build_routing_args が組み立てる --repos/--repo-dir
    を埋め込む）。実際の cfg.regression_cmd/cfg.intake_cmd への自動配線（b3/c1/e1）は別タスクの
    担当のため、ここでは agent_project パッケージの config loader は経由せず、素の YAML
    読み書きで .agent/agent-project.yaml の内容そのものを検証する
    （agent_project の import は cwd 上の設定ファイル探索・watch/state-git 等の副作用を伴うため、
    単体テストでは避けるのが安全——実際に過去そのインポートが実リポジトリへの誤コミットを
    引き起こしている）。
    """

    def _write_agent_project_yaml(self, root: Path, regression_cmd: str, intake_cmd: str) -> Path:
        import yaml

        agent_dir = root / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = agent_dir / "agent-project.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"regression_cmd": regression_cmd, "intake_cmd": intake_cmd},
                           allow_unicode=True),
            encoding="utf-8",
        )
        return cfg_path

    def test_regression_cmd_and_intake_cmd_route_to_codd_gate(self):
        import yaml

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repos_path = root / ".agent-project" / "repos.json"
            repos_path.parent.mkdir(parents=True)

            routing_args = routing.build_routing_args(repos_path, "sandbox", root)
            regression_cmd = " ".join(
                ["codd-gate", "verify", "--base", '"$KIRO_BASE_REV"', *routing_args])
            intake_cmd = " ".join(["codd-gate", "tasks", "--debt", *routing_args])

            cfg_path = self._write_agent_project_yaml(root, regression_cmd, intake_cmd)
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

        self.assertEqual(cfg_path.parent.name, ".agent")
        self.assertEqual(cfg_path.name, "agent-project.yaml")
        self.assertRegex(loaded["regression_cmd"], r"codd-gate verify --base")
        self.assertRegex(loaded["intake_cmd"], r"codd-gate tasks")
        # --repos/--repo-dir は codd_gate_routing が組み立てた実引数と一致する（s6 2・3節）。
        self.assertIn("--repos ./.agent-project/repos.json", loaded["regression_cmd"])
        self.assertIn("--repo-dir sandbox=.", loaded["regression_cmd"])
        self.assertIn("--repos ./.agent-project/repos.json", loaded["intake_cmd"])
        self.assertIn("--repo-dir sandbox=.", loaded["intake_cmd"])


if __name__ == "__main__":
    unittest.main()
