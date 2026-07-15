"""codd_gate_regression の単体テスト（標準ライブラリ unittest）。

regression_cmd の生成（codd-gate 検出結果に応じた no-op 縮退込み）と、
.agent/agent-project.yaml への冪等な行編集（挿入位置・更新・no-op）を検証する。

    python -m unittest discover -s tools/agent-project/tests
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_regression as regression
import codd_gate_status as status_mod

# 完了条件そのもの（grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base'）と同じパターン。
COMPLETION_RE = re.compile(r'^[ \t]*regression_cmd:.*codd-gate verify --base', re.MULTILINE)

REAL_FILE_SNIPPET = """\
root: .agent-project

# 一貫性ゲート（codd-gate 連携）: done 確定前の差分ゲート（regression）と
# 負債の修復タスク自動投入（intake）。repos.json は agent-project が charter から
# <root>/repos.json に自動生成する（tools/agent-project/README.md 参照）。
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'

# グローバル既定
agent_cli: claude
model: auto
"""


class TestBuildRegressionCmd(unittest.TestCase):
    def test_none_when_codd_gate_not_usable(self):
        unusable = status_mod.build_status(None)  # 未検出
        self.assertIsNone(regression.build_regression_cmd(unusable, "repos.json"))

    def test_matches_readme_canonical_value(self):
        usable = status_mod.build_status(["codd-gate"])
        cmd = regression.build_regression_cmd(usable, ".agent-project/repos.json")
        self.assertEqual(
            cmd,
            'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json',
        )

    def test_custom_base_and_repos_path(self):
        usable = status_mod.build_status(["codd-gate"])
        cmd = regression.build_regression_cmd(usable, "repos.json", base="HEAD~1")
        self.assertEqual(cmd, "codd-gate verify --base HEAD~1 --repos repos.json")

    def test_no_repo_dir_flag_present(self):
        # プロジェクトルート自身で実行される regression_cmd は --repo-dir を必要としない
        # （codd_gate_routing の runtime hook 用途とは別。repos.json の dir で足りる）。
        usable = status_mod.build_status(["codd-gate"])
        cmd = regression.build_regression_cmd(usable, "repos.json")
        self.assertNotIn("--repo-dir", cmd)

    def test_uses_portable_command_name_not_resolved_absolute_path(self):
        # status.binary が PATH 解決済みの絶対パス（例 /Users/x/.local/bin/codd-gate）や
        # 同梱パス実行用の [sys.executable, path] であっても、共有設定ファイルへ埋め込む文字列は
        # 常に固定の "codd-gate"（PATH 解決を実行時に委ねる）。環境固有の絶対パスを焼き込むと
        # 別マシン・別ワークスペースで壊れるため。
        usable = status_mod.build_status(["/Users/someone/.local/bin/codd-gate"])
        cmd = regression.build_regression_cmd(usable, "repos.json")
        self.assertEqual(
            cmd, 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json')

        bundled = status_mod.build_status([sys.executable, "/repo/tools/codd-gate/codd-gate.py"])
        cmd2 = regression.build_regression_cmd(bundled, "repos.json")
        self.assertEqual(
            cmd2, 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json')


class TestUpsertConfigText(unittest.TestCase):
    def test_insert_before_intake_cmd_when_present(self):
        text = (
            "root: .\n\n"
            "# 一貫性ゲート（codd-gate 連携）\n"
            "intake_cmd: 'codd-gate tasks --debt'\n\n"
            "agent_cli: claude\n"
        )
        new_text, changed = regression.upsert_config_text(text, "codd-gate verify --base X")
        self.assertTrue(changed)
        lines = new_text.splitlines()
        self.assertEqual(lines.index("regression_cmd: 'codd-gate verify --base X'") + 1,
                          lines.index("intake_cmd: 'codd-gate tasks --debt'"))
        self.assertEqual(len(re.findall(r"^regression_cmd:", new_text, re.MULTILINE)), 1)

    def test_insert_before_agent_cli_with_header_when_no_codd_gate_section(self):
        text = "root: .\n\nagent_cli: claude\nmodel: auto\n"
        new_text, changed = regression.upsert_config_text(text, "codd-gate verify --base X")
        self.assertTrue(changed)
        self.assertIn("# 一貫性ゲート（codd-gate 連携）", new_text)
        self.assertLess(new_text.index("regression_cmd:"), new_text.index("agent_cli: claude"))
        self.assertIn("regression_cmd: 'codd-gate verify --base X'", new_text)

    def test_append_when_no_anchors_found(self):
        text = "root: .\n"
        new_text, changed = regression.upsert_config_text(text, "codd-gate verify --base X")
        self.assertTrue(changed)
        self.assertTrue(new_text.rstrip("\n").endswith("regression_cmd: 'codd-gate verify --base X'"))

    def test_idempotent_second_call_no_change(self):
        text = "root: .\n\nagent_cli: claude\n"
        once, changed1 = regression.upsert_config_text(text, "codd-gate verify --base X")
        twice, changed2 = regression.upsert_config_text(once, "codd-gate verify --base X")
        self.assertTrue(changed1)
        self.assertFalse(changed2)
        self.assertEqual(once, twice)

    def test_updates_existing_differing_value_in_place(self):
        text = "regression_cmd: 'old value'\nintake_cmd: 'codd-gate tasks --debt'\n"
        new_text, changed = regression.upsert_config_text(text, "codd-gate verify --base NEW")
        self.assertTrue(changed)
        self.assertEqual(len(re.findall(r"^regression_cmd:", new_text, re.MULTILINE)), 1)
        self.assertIn("regression_cmd: 'codd-gate verify --base NEW'", new_text)
        self.assertIn("intake_cmd: 'codd-gate tasks --debt'", new_text)  # 無関係な行は不変

    def test_none_cmd_leaves_text_completely_untouched(self):
        # no-op 縮退: codd-gate 未検出/非互換のとき、既存の regression_cmd（手書きの独自コマンド
        # 含む）を消したり書き換えたりしない。
        text = "regression_cmd: 'make -s smoke'\nagent_cli: claude\n"
        new_text, changed = regression.upsert_config_text(text, None)
        self.assertFalse(changed)
        self.assertEqual(text, new_text)

    def test_preserves_surrounding_content_on_value_update(self):
        new_text, changed = regression.upsert_config_text(
            REAL_FILE_SNIPPET,
            'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json',
        )
        self.assertFalse(changed)  # 既に正準値と一致 → 無変更（実ファイルへの再適用が安全）
        self.assertEqual(new_text, REAL_FILE_SNIPPET)

    def test_single_quote_escaping_round_trips_via_yaml(self):
        import yaml
        cmd = "codd-gate verify --base X --note it's-fine"
        new_text, _ = regression.upsert_config_text("agent_cli: claude\n", cmd)
        loaded = yaml.safe_load(new_text)
        self.assertEqual(loaded["regression_cmd"], cmd)

    def test_generated_line_satisfies_completion_condition(self):
        cmd = regression.build_regression_cmd(
            status_mod.build_status(["codd-gate"]), ".agent-project/repos.json")
        new_text, _ = regression.upsert_config_text("agent_cli: claude\n", cmd)
        self.assertRegex(new_text, COMPLETION_RE)


class TestApplyToFile(unittest.TestCase):
    def test_writes_file_and_reports_changed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".agent" / "agent-project.yaml"
            cfg.parent.mkdir()
            cfg.write_text("agent_cli: claude\n", encoding="utf-8")

            changed = regression.apply_to_file(cfg, "codd-gate verify --base X")
            self.assertTrue(changed)
            self.assertRegex(cfg.read_text(encoding="utf-8"), COMPLETION_RE)

    def test_second_call_is_idempotent_no_write(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "agent-project.yaml"
            cfg.write_text("agent_cli: claude\n", encoding="utf-8")

            regression.apply_to_file(cfg, "codd-gate verify --base X")
            before = cfg.read_text(encoding="utf-8")
            before_mtime = cfg.stat().st_mtime_ns
            changed_again = regression.apply_to_file(cfg, "codd-gate verify --base X")

            self.assertFalse(changed_again)
            self.assertEqual(cfg.read_text(encoding="utf-8"), before)
            self.assertEqual(cfg.stat().st_mtime_ns, before_mtime)

    def test_missing_file_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".agent" / "agent-project.yaml"
            changed = regression.apply_to_file(cfg, "codd-gate verify --base X")
            self.assertTrue(changed)
            self.assertRegex(cfg.read_text(encoding="utf-8"), COMPLETION_RE)

    def test_not_usable_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "agent-project.yaml"
            changed = regression.apply_to_file(cfg, None)
            self.assertFalse(changed)
            self.assertFalse(cfg.exists())


class TestInferDefaultReposPath(unittest.TestCase):
    def test_from_root_key(self):
        self.assertEqual(
            regression.infer_default_repos_path("root: .agent-project\n"),
            ".agent-project/repos.json",
        )

    def test_fallback_when_root_absent(self):
        self.assertEqual(regression.infer_default_repos_path("agent_cli: claude\n"),
                          regression.DEFAULT_REPOS_PATH)


if __name__ == "__main__":
    unittest.main()
