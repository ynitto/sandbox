"""codd_gate_regression の単体テスト（標準ライブラリ unittest）。

regression_cmd の生成（codd-gate 検出結果に応じた no-op 縮退込み）と、
.agent/agent-project.yaml への冪等な行編集（挿入位置・更新・no-op）を検証する。

本モジュールの CLI（`--config`）は codd-gate 連携を有効化する唯一の書き込み経路。実行時に
cfg を書き換える自動配線は存在しないため、「検出 → 推奨文字列の生成 → yaml への冪等注入」の
一気通貫を `main()` の呼び出しとして検証する（TestCliMain）。

    python -m unittest discover -s tools/agent-project/tests
"""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
import unittest.mock as mock
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


class TestCliMain(unittest.TestCase):
    """`python3 codd_gate_regression.py --config <yaml>` の一気通貫。

    codd-gate の実体は `--codd-gate` で明示指定する。既定の解決（PATH → 同梱パス）は実行環境に
    依存し、テストの成否が「その機械に codd-gate が入っているか」で変わってしまうため
    （`detect_status` は実在確認だけで subprocess を起動しないので、明示指定すればプローブ無しに
    決定的な usable=True を作れる）。
    """

    def _run_cli(self, argv: "list[str]") -> "tuple[int, dict]":
        """main() を呼び、終了コードと stdout の JSON を返す。"""
        buf = io.StringIO()
        # stderr も飲む（未検出ケースの診断メッセージがテスト出力に混ざるのを防ぐだけ。
        # その内容の検証は TestCliContract の担当）。
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = regression.main(argv)
        return rc, json.loads(buf.getvalue())

    def _config_with(self, d: str, text: str) -> Path:
        cfg = Path(d) / ".agent" / "agent-project.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(text, encoding="utf-8")
        return cfg

    def test_detects_generates_and_injects_in_one_pass(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(d, "root: .agent-project\n\nagent_cli: claude\n")

            rc, payload = self._run_cli(
                ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate"])

            self.assertEqual(rc, 0)
            self.assertTrue(payload["usable"])          # 検出
            self.assertEqual(                            # 推奨文字列の生成（README 正準値）
                payload["cmd"],
                'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json')
            self.assertTrue(payload["changed"])          # yaml 注入
            self.assertRegex(cfg.read_text(encoding="utf-8"), COMPLETION_RE)

    def test_repos_path_inferred_from_root_key_without_explicit_flag(self):
        # --repos 省略時は設定の root: から <root>/repos.json を推定する（README の規約）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(d, "root: custom-state\nagent_cli: claude\n")

            _, payload = self._run_cli(
                ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate"])

            self.assertIn("--repos custom-state/repos.json", payload["cmd"])
            self.assertIn("--repos custom-state/repos.json", cfg.read_text(encoding="utf-8"))

    def test_explicit_repos_and_base_flags_override_inference(self):
        # 明示指定は推定に勝つ。root: があっても --repos が優先され、--base も渡した値がそのまま
        # 埋まる（既定の "$KIRO_BASE_REV" 以外を使う運用——固定 rev での検証等——を塞がない）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(d, "root: .agent-project\nagent_cli: claude\n")

            _, payload = self._run_cli([
                "--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate",
                "--repos", "/srv/registry/repos.json", "--base", "origin/main"])

            self.assertEqual(
                payload["cmd"],
                "codd-gate verify --base origin/main --repos /srv/registry/repos.json")
            self.assertNotIn(".agent-project/repos.json", payload["cmd"])
            self.assertIn(payload["cmd"], cfg.read_text(encoding="utf-8"))

    def test_second_run_produces_no_diff(self):
        # 冪等性の本体: 同じ引数での再実行は changed=False で、ファイルの中身も mtime も動かない
        # （設定ファイルは人の編集物なので、再実行のたびに差分・mtime が出ると git 上で騒がしい）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(
                d,
                "root: .agent-project\n\n"
                "# 人が書いたコメント\n"
                "agent_cli: claude\nmodel: auto\n")
            argv = ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate"]

            _, first = self._run_cli(argv)
            text_after_first = cfg.read_text(encoding="utf-8")
            mtime_after_first = cfg.stat().st_mtime_ns

            _, second = self._run_cli(argv)

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(cfg.read_text(encoding="utf-8"), text_after_first)
            self.assertEqual(cfg.stat().st_mtime_ns, mtime_after_first)
            # 1回目の注入でも人の記述は失われない（load→dump のラウンドトリップをしない設計）。
            self.assertIn("# 人が書いたコメント", text_after_first)
            self.assertIn("model: auto", text_after_first)
            self.assertEqual(
                len(re.findall(r"^regression_cmd:", text_after_first, re.MULTILINE)), 1)

    def test_hand_written_canonical_config_is_left_untouched(self):
        # 冪等性のもう一方の端: 人が README どおりに手で書いた設定に対しては、CLI は最初の実行から
        # no-op になる。生成値と手書きの正準値が一致していることの検証でもある（両者がずれると
        # 「CLI を通すたびに人の記述が書き換わる」＝設定ファイルが git 上で往復する）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(d, REAL_FILE_SNIPPET)

            _, payload = self._run_cli(
                ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate"])

            self.assertFalse(payload["changed"])
            self.assertEqual(cfg.read_text(encoding="utf-8"), REAL_FILE_SNIPPET)

    def test_stale_value_is_updated_in_place_then_stable(self):
        # 更新経路（挿入ではなく置換）でも冪等性が成り立つこと。古い値が1回目で置き換わり、
        # 2回目は差分ゼロ——挿入経路（test_second_run_produces_no_diff）とは別の分岐を通る。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(
                d,
                "root: .agent-project\n"
                "regression_cmd: 'codd-gate verify --base HEAD~1 --repos old/repos.json'\n"
                "intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'\n")
            argv = ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate"]

            _, first = self._run_cli(argv)
            text_after_first = cfg.read_text(encoding="utf-8")
            _, second = self._run_cli(argv)

            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(cfg.read_text(encoding="utf-8"), text_after_first)
            self.assertNotIn("old/repos.json", text_after_first)
            self.assertEqual(
                len(re.findall(r"^regression_cmd:", text_after_first, re.MULTILINE)), 1)
            # 隣接する intake_cmd は本 CLI の対象外なので触らない（注入は1キーだけ）。
            self.assertIn(
                "intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'",
                text_after_first)

    def test_dry_run_reports_change_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(d, "root: .agent-project\nagent_cli: claude\n")
            before = cfg.read_text(encoding="utf-8")

            rc, payload = self._run_cli(
                ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate", "--dry-run"])

            self.assertEqual(rc, regression.EXIT_OK)
            self.assertTrue(payload["changed"])   # 「変わるはず」は報告する
            self.assertTrue(payload["dry_run"])
            self.assertEqual(cfg.read_text(encoding="utf-8"), before)  # が、書かない

    def test_noop_when_codd_gate_not_detected(self):
        # 未検出なら壊れたコマンドを書き込まず、既存の手書き設定にも触れない（no-op 縮退）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._config_with(d, "root: .\nregression_cmd: 'make -s smoke'\n")
            before = cfg.read_text(encoding="utf-8")

            with mock.patch.object(regression, "detect_status",
                                    return_value=status_mod.build_status(None)):
                _, payload = self._run_cli(["--config", str(cfg)])

            self.assertFalse(payload["usable"])
            self.assertIsNone(payload["cmd"])
            self.assertFalse(payload["changed"])
            self.assertEqual(cfg.read_text(encoding="utf-8"), before)


class TestCliContract(unittest.TestCase):
    """CLI としての外形（終了コード・stderr・ヘルプ）。

    JSON の中身は TestCliMain が見る。ここで固定するのは「シェルから使えるか」——
    呼び出し側が `$?` だけで分岐でき、失敗の理由が stderr に日本語で出ること。
    """

    def _run(self, argv: "list[str]") -> "tuple[int, str, str]":
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = regression.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_missing_config_errors_instead_of_creating_a_half_baked_file(self):
        # --config のパス誤りが「新規ファイル生成として成功」に化けないこと。regression_cmd
        # だけの yaml は root:/agent_cli: を欠き、本体が起動できない。
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / ".agent" / "agent-project.yaml"

            rc, out, err = self._run(
                ["--config", str(cfg), "--codd-gate", "/opt/bin/codd-gate"])

            self.assertEqual(rc, regression.EXIT_CONFIG_MISSING)
            self.assertFalse(cfg.exists())
            self.assertIn("設定ファイルがありません", err)
            self.assertIn(str(cfg), err)      # どのパスを見に行ったかを示す
            self.assertEqual(out, "")         # 成功時の JSON は出さない

    def test_missing_config_is_an_error_even_under_dry_run(self):
        # --dry-run は「書かない」だけで、対象不在の誤りを見逃す指定ではない。
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "nope.yaml"

            rc, _, err = self._run(["--config", str(cfg), "--dry-run"])

            self.assertEqual(rc, regression.EXIT_CONFIG_MISSING)
            self.assertIn("設定ファイルがありません", err)

    def test_unreadable_config_reports_the_os_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "as-a-directory.yaml"
            cfg.mkdir()                        # 読めない = FileNotFoundError 以外の OSError

            rc, _, err = self._run(["--config", str(cfg)])

            self.assertEqual(rc, regression.EXIT_CONFIG_MISSING)
            self.assertIn("読めません", err)

    def test_undetected_codd_gate_exits_distinctly_from_a_bad_path(self):
        # 「未導入だから飛ばす」と「パスを間違えている」を $? で区別できること。
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "agent-project.yaml"
            cfg.write_text("root: .\nagent_cli: claude\n", encoding="utf-8")

            with mock.patch.object(regression, "detect_status",
                                   return_value=status_mod.build_status(None)):
                rc, out, err = self._run(["--config", str(cfg)])

            self.assertEqual(rc, regression.EXIT_UNUSABLE)
            self.assertNotEqual(regression.EXIT_UNUSABLE, regression.EXIT_CONFIG_MISSING)
            self.assertNotEqual(regression.EXIT_UNUSABLE, 2)   # argparse の使用法エラーと衝突しない
            self.assertIsNone(json.loads(out)["cmd"])          # 機械可読な報告は出す
            self.assertIn("組み立てられません", err)
            self.assertIn("変更していません", err)

    def test_usage_error_exits_2_without_touching_anything(self):
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                regression.main(["--no-such-flag"])
        self.assertEqual(cm.exception.code, 2)

    def test_help_documents_every_flag_and_the_exit_codes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            regression.main(["--help"])
        text = buf.getvalue()

        self.assertEqual(cm.exception.code, 0)
        for flag in ("--config", "--codd-gate", "--repos", "--base", "--dry-run"):
            self.assertIn(flag, text)
        self.assertIn("終了コード", text)
        for code in (regression.EXIT_CONFIG_MISSING, regression.EXIT_UNUSABLE):
            self.assertRegex(text, rf"(?m)^\s+{code}\s")


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
