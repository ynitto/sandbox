"""codd_gate_wiring の単体テスト（標準ライブラリ unittest）。

regression_cmd/intake_cmd の結線判定（regression_wired/intake_wired）、推奨コマンド組み立て
（recommend_regression_cmd/recommend_intake_cmd）、実測配線（probe_wiring）の3ケース
（未検出・検出済み未結線・検出済み結線済み）を、subprocess を起動せず `which=`/`run=` の
依存性注入で決定的に検証する（test_codd_gate_detect.py と同じパターン）。

加えて、このモジュールが本体（agent_project）へどう繋がるかを検証する（TestHookResolution）。
本体は実行時に cfg を書き換える自動配線を持たず、設定 `hooks:` の明示指定 → sibling の能力
スキャン、の順で「能力を満たす module」を引き当てるだけ。codd_gate_wiring は前者でだけ当選し、
後者（零設定の自動検出）では当選しないこと——利用者が名前を書いたときにだけ繋がること——を
両方向から押さえる。

    python -m unittest discover -s tools/agent-project/tests
"""
import re
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_detect as detect
import codd_gate_status as status
import codd_gate_wiring as wiring

_SIBLING_DIR = Path(__file__).resolve().parent.parent


def _fake_run(returncode=0, stdout="", stderr=""):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
    return run


def _load_hooks_fragment():
    """`agent_project/hooks.py` を単体の名前空間へ exec して返す。

    この断片は `agent_project/__init__.py` が共有名前空間へ順に exec して合成する前提で書かれて
    おり、単体 import できない（自前の import 文を持たない）。合成に必要な最小の globals だけを
    与えて読み込む——`import agent_project` は全断片を exec するため cwd 上の設定ファイル探索や
    watch/state-git の副作用を伴い、単体テストから触るには重い（test_codd_gate_routing.py の
    TestAgentProjectYamlWiring と同じ理由）。

    `__file__` は合成後と同じく `agent_project/__init__.py` を指させる。`_hook_sibling_dir()` が
    その1階層上を sibling の置き場として解決するため、ここがずれると走査先が変わる。
    """
    pkg_init = _SIBLING_DIR / "agent_project" / "__init__.py"
    src = (_SIBLING_DIR / "agent_project" / "hooks.py").read_text(encoding="utf-8")
    mod = types.ModuleType("agent_project_hooks_under_test")
    mod.__dict__.update({
        "__file__": str(pkg_init), "Path": Path, "sys": sys, "re": re,
        "append_journal": lambda *a, **k: None,   # journal 書き込みは本テストの対象外
    })
    exec(compile(src, str(_SIBLING_DIR / "agent_project" / "hooks.py"), "exec"), mod.__dict__)
    return mod


class _Cfg:
    """`hooks` と `journal` だけを持つ Config の代役（本体の Config は import しない）。"""

    def __init__(self, hooks=None):
        self.hooks = hooks
        self.journal = None


class TestWiredDetection(unittest.TestCase):
    """regression_wired/intake_wired — 手書き文字列が codd-gate を指しているかの判定。"""

    def test_regression_wired_matches_hand_written_config(self):
        cmd = 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
        self.assertTrue(wiring.regression_wired(cmd))

    def test_regression_wired_false_when_missing(self):
        self.assertFalse(wiring.regression_wired(None))
        self.assertFalse(wiring.regression_wired(""))
        self.assertFalse(wiring.regression_wired("pytest -q"))

    def test_regression_wired_false_when_base_flag_absent(self):
        self.assertFalse(wiring.regression_wired("codd-gate verify --strict"))

    def test_intake_wired_matches_hand_written_config(self):
        cmd = "codd-gate tasks --debt --repos .agent-project/repos.json"
        self.assertTrue(wiring.intake_wired(cmd))

    def test_intake_wired_false_when_debt_flag_absent(self):
        self.assertFalse(wiring.intake_wired("codd-gate tasks"))


class TestRecommendedCommands(unittest.TestCase):
    def test_recommend_regression_cmd_keeps_shell_var_literal(self):
        cmd = wiring.recommend_regression_cmd(".agent-project/repos.json")
        self.assertEqual(
            cmd, 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json')
        self.assertTrue(wiring.regression_wired(cmd))  # 自己無矛盾: 推奨コマンド自身が結線判定を満たす

    def test_recommend_intake_cmd(self):
        cmd = wiring.recommend_intake_cmd(".agent-project/repos.json")
        self.assertEqual(cmd, "codd-gate tasks --debt --repos .agent-project/repos.json")
        self.assertTrue(wiring.intake_wired(cmd))


class TestJudgeWiringPure(unittest.TestCase):
    """judge_wiring — I/O なしの純粋関数として、実測値を渡すだけで判定できることを検証する。"""

    def test_usable_and_unwired_recommends_both(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result, regression_cmd=None, intake_cmd=None,
            capabilities={"verify": True, "tasks": True, "debt": True},
            repos_path=".agent-project/repos.json")
        self.assertTrue(judgment.usable)
        self.assertFalse(judgment.fully_wired)
        self.assertTrue(judgment.actionable)
        self.assertEqual(judgment.recommended_regression_cmd,
                          'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json')
        self.assertEqual(judgment.recommended_intake_cmd,
                          "codd-gate tasks --debt --repos .agent-project/repos.json")

    def test_usable_and_already_wired_recommends_nothing(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result,
            regression_cmd='codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json',
            intake_cmd="codd-gate tasks --debt --repos .agent-project/repos.json",
            repos_path=".agent-project/repos.json")
        self.assertTrue(judgment.fully_wired)
        self.assertFalse(judgment.actionable)
        self.assertIsNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)

    def test_not_usable_recommends_nothing_even_when_unwired(self):
        judgment = wiring.judge_wiring(
            status.build_status(None), regression_cmd=None, intake_cmd=None,
            repos_path=".agent-project/repos.json")
        self.assertFalse(judgment.usable)
        self.assertFalse(judgment.actionable)
        self.assertIsNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)

    def test_missing_repos_path_recommends_nothing(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(result, regression_cmd=None, intake_cmd=None,
                                        repos_path=None)
        self.assertIsNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)

    def test_capability_gate_suppresses_recommendation(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result, regression_cmd=None, intake_cmd=None,
            capabilities={"verify": True, "tasks": True, "debt": False},
            repos_path=".agent-project/repos.json")
        self.assertIsNotNone(judgment.recommended_regression_cmd)
        self.assertIsNone(judgment.recommended_intake_cmd)  # --debt 非対応なら intake は推奨しない


class TestDetectWiringIntegrated(unittest.TestCase):
    """probe_wiring — resolve/get_version/check_repos_schema_compat/detect_capabilities を
    一気通貫で実測配線し、WiringJudgment まで組み立てる（依存性注入で subprocess は起動しない）。"""

    def test_binary_absent_degrades_to_noop(self):
        which = lambda _name: None
        # 同梱パス（tools/codd-gate/codd-gate.py）も無い状態を再現する
        # （test_codd_gate_detect.py の test_resolve_codd_gate_absent_when_path_and_bundled_both_missing と同じ手法）
        with mock.patch.object(detect.Path, "exists", return_value=False):
            judgment = wiring.probe_wiring(which=which)
        self.assertFalse(judgment.usable)
        self.assertFalse(judgment.actionable)
        self.assertEqual(len(judgment.status.findings), 1)

    def test_binary_present_and_unwired_recommends_commands(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None

        def run(argv, **kwargs):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, stdout="codd-gate 1.2.0\n")
            if argv[-1] == "--help" and len(argv) == 2:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="usage: codd-gate {verify,tasks} ...\n")
            return subprocess.CompletedProcess(argv, 0, stdout="--debt\n")

        with tempfile.TemporaryDirectory() as d:
            repos_path = Path(d) / "repos.json"
            repos_path.write_text('{"svc": {"url": "https://example/svc.git"}}', encoding="utf-8")

            judgment = wiring.probe_wiring(
                regression_cmd=None, intake_cmd=None, repos_path=repos_path,
                which=which, run=run)

        self.assertTrue(judgment.usable)
        self.assertTrue(judgment.actionable)
        self.assertIn("codd-gate verify --base", judgment.recommended_regression_cmd)
        self.assertIn("codd-gate tasks --debt", judgment.recommended_intake_cmd)

    def test_explicit_binary_bypasses_path_lookup(self):
        # 明示指定（`--codd-gate` 相当）は PATH 解決に勝つ。PATH に無い環境でも、実体を教えれば
        # 検出が成立して推奨文字列まで出る——doctor が「入っているのに見つけられない」で
        # 黙る状態を、設定だけで抜けられることの担保。
        def which(_name):
            self.fail("explicit 指定時に PATH 解決を呼んではいけない")

        probed = []

        def run(argv, **kwargs):
            probed.append(argv)
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, stdout="codd-gate 1.2.0\n")
            if argv[-1] == "--help" and len(argv) == 2:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="usage: codd-gate {verify,tasks} ...\n")
            return subprocess.CompletedProcess(argv, 0, stdout="--debt\n")

        judgment = wiring.probe_wiring(
            repos_path=".agent-project/repos.json", explicit="/opt/tools/codd-gate",
            which=which, run=run)

        self.assertTrue(judgment.usable)
        self.assertEqual(judgment.status.binary, ["/opt/tools/codd-gate"])
        # 実測プローブの argv も明示指定の実体を叩く（PATH 上の別実体に化けていない）。
        self.assertTrue(probed)
        self.assertTrue(all(argv[0] == "/opt/tools/codd-gate" for argv in probed))
        self.assertIn("codd-gate verify --base", judgment.recommended_regression_cmd)

    def test_schema_incompatible_repos_json_degrades_to_noop(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None
        run = _fake_run(0, stdout="codd-gate 1.2.0\n")

        with tempfile.TemporaryDirectory() as d:
            repos_path = Path(d) / "repos.json"
            repos_path.write_text("[]", encoding="utf-8")  # トップレベルが object でない = 非互換

            judgment = wiring.probe_wiring(repos_path=repos_path, which=which, run=run)

        self.assertFalse(judgment.usable)
        self.assertFalse(judgment.actionable)

    def test_missing_repos_json_file_treated_as_schema_unknown_not_incompatible(self):
        which = lambda name: "/usr/local/bin/codd-gate" if name == detect.BINARY_NAME else None
        run = _fake_run(0, stdout="codd-gate 1.2.0\n")

        judgment = wiring.probe_wiring(
            repos_path="/nonexistent/repos.json", which=which, run=run)

        self.assertTrue(judgment.usable)


class TestDoctorFindings(unittest.TestCase):
    def test_not_usable_reuses_status_findings(self):
        judgment = wiring.judge_wiring(status.build_status(None), None, None)
        findings = wiring.render_findings(judgment)
        self.assertEqual(findings, judgment.status.findings)

    def test_fully_wired_has_no_findings(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result,
            regression_cmd='codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json',
            intake_cmd="codd-gate tasks --debt --repos repos.json",
            repos_path="repos.json")
        self.assertEqual(wiring.render_findings(judgment), [])

    def test_actionable_reports_info_findings_with_fix_suggestion(self):
        result = status.build_status(["codd-gate"], version=(1, 0, 0), schema_ok=True)
        judgment = wiring.judge_wiring(
            result, regression_cmd=None, intake_cmd=None,
            capabilities={"verify": True, "tasks": True, "debt": True},
            repos_path="repos.json")
        findings = wiring.render_findings(judgment)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f["severity"] == "info" for f in findings))
        self.assertIn(judgment.recommended_regression_cmd, findings[0]["fix"])
        self.assertIn(judgment.recommended_intake_cmd, findings[1]["fix"])


class TestHookResolution(unittest.TestCase):
    """本体 → codd_gate_wiring の結線が「能力による解決」だけで成立することの検証。

    本体側に `codd_gate_wiring` という固有名は無く、`.agent/agent-project.yaml` が実行時に
    書き換わることもない。本体が持つ解決経路は (1) `hooks:` の明示指定、(2) 未指定時の sibling
    走査、の2つだが、このモジュールが載るのは (1) だけ（(2) は module docstring の理由で意図的に
    切ってある）。零設定で使いたい利用者向けの経路は `python3 codd_gate_wiring.py` の CLI。
    """

    def setUp(self):
        self.hooks = _load_hooks_fragment()
        # _HOOK_CACHE は None もキャッシュする。cfg を差し替えるたびに消さないと前ケースの
        # 解決結果を引き継ぐ。
        self.hooks._HOOK_CACHE.clear()

    def tearDown(self):
        self.hooks._HOOK_CACHE.clear()

    def test_explicit_hooks_setting_selects_this_module(self):
        cfg = _Cfg({"wiring": "codd_gate_wiring"})
        for capability in ("wiring.detect", "wiring.findings"):
            with self.subTest(capability=capability):
                self.hooks._HOOK_CACHE.clear()
                self.assertIs(self.hooks._hook_provider(capability, cfg), wiring)

    def test_full_capability_key_overrides_prefix_key(self):
        # 系統キー（wiring）でまとめつつ、片方の能力だけフルキーで振り替えられる。
        cfg = _Cfg({"wiring": "codd_gate_wiring", "wiring.findings": "no_such_module"})
        self.assertIs(self.hooks._hook_provider("wiring.detect", cfg), wiring)
        self.hooks._HOOK_CACHE.clear()
        self.assertIsNone(self.hooks._hook_provider("wiring.findings", cfg))

    def test_unresolvable_explicit_name_does_not_fall_back_to_autodetect(self):
        # 人が名前を書いた以上、解決できなくても自動検出で別物へ差し替えない（黙って別の
        # プロバイダが動く方が、配線ミスとして気づけない分たちが悪い）。
        cfg = _Cfg({"wiring": "no_such_module"})
        self.assertIsNone(self.hooks._hook_provider("wiring.detect", cfg))

    def test_unresolvable_explicit_name_is_reported_as_error(self):
        cfg = _Cfg({"wiring": "no_such_module"})
        reason = self.hooks._hook_resolution_error("wiring.detect", cfg)
        self.assertIsNotNone(reason)
        self.assertIn("no_such_module", reason)

    def test_absent_hooks_setting_is_not_an_error(self):
        # 未指定での不在は「任意機能が無い」だけ。所見にしない。
        self.assertIsNone(self.hooks._hook_resolution_error("wiring.detect", _Cfg(None)))

    def test_sibling_scan_does_not_select_this_module_when_unconfigured(self):
        # 零設定では繋がらない（切り離しの本体）。sibling 走査はソーステキストの前置フィルタ
        # `^def <属性名>(` でプロバイダを選ぶため、契約名を別名で公開している本モジュールは
        # 候補に上がらない。この検出レイヤが doctor に現れるのは `hooks:` を書いたときだけ。
        for capability in ("wiring.detect", "wiring.findings"):
            with self.subTest(capability=capability):
                self.hooks._HOOK_CACHE.clear()
                self.assertIsNone(self.hooks._hook_provider(capability, _Cfg(None)))

    def test_no_sibling_provides_the_capabilities_by_autodetect(self):
        # 上のケースを codd_gate_wiring 以外へも広げる。sibling のどれかが `def detect_wiring(`
        # を持てば自動配線が復活するため、走査そのものが空振ることを固定する。
        for capability, required in self.hooks.HOOK_CAPABILITIES.items():
            with self.subTest(capability=capability):
                self.assertIsNone(self.hooks._hook_scan_siblings(required))

    def test_this_module_satisfies_the_declared_capability_contract(self):
        # 本体が求める属性名（HOOK_CAPABILITIES）と、このモジュールの公開 API の対応。
        # 別名を消せば `hooks:` の明示指定まで黙って切れるため、契約そのものをテストで固定する。
        for capability, required in self.hooks.HOOK_CAPABILITIES.items():
            with self.subTest(capability=capability):
                for attr in required:
                    self.assertTrue(callable(getattr(wiring, attr, None)))

    def test_contract_names_are_aliases_of_the_modules_own_functions(self):
        # 別名は改名の痕跡ではなく結線点。実体（probe_wiring/render_findings）と同一である
        # ことを押さえておかないと、片方だけ直して契約名が古い実装を指す事故が起きる。
        self.assertIs(wiring.detect_wiring, wiring.probe_wiring)
        self.assertIs(wiring.doctor_findings, wiring.render_findings)


if __name__ == "__main__":
    unittest.main()
