"""agent-flow の単体テスト — エンジン選択の指示文（selection: "engine"）。

run パラメータ（split_policy・granularity・tier・レビュー観点）が選ぶ「値 → プロンプト文面」を
手法カタログの id 引きへ寄せた汎用インターフェース。選択はエンジンが決定的に行い、カタログは
文面だけを差し替える。解決順は run tuning.json → repo .agents/methods → $AGENT_METHODS_DIR →
組み込み文言（フェイルセーフ: カタログが無い・壊れている環境でも指示は消えない）。

設計: docs/plans/2026-08-18-split-policy-catalog-unification-design.md

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）

REPO_ROOT = HERE.parent.parent.parent


def _methods_dir_with(*methods):
    """一時カタログディレクトリを作り、<id>.json で書く。"""
    d = tempfile.mkdtemp(prefix="kf-engine-methods-")
    for method in methods:
        with open(os.path.join(d, f"{method['id']}.json"), "w", encoding="utf-8") as f:
            json.dump(method, f, ensure_ascii=False)
    return d


def _tuning_dir_with(methods):
    d = tempfile.mkdtemp(prefix="kf-engine-tuning-")
    with open(os.path.join(d, "tuning.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "enabled": True, "methods": methods, "trials": []}, f)
    return d


def _entry(mid, role, text):
    return {"id": mid, "description": mid, "selection": "engine", "enabled": False,
            "fragments": [{"role": role, "text": text}]}


class EngineDirectiveTests(unittest.TestCase):
    def test_fallback_without_any_catalog(self):
        # _shared が AGENT_METHODS_DIR / AGENT_TUNING_DIR を実在しない場所へ隔離済み
        self.assertEqual(kf.engine_directive("split-policy-behavior", "planner", "組み込み"),
                         "組み込み")

    def test_methods_dir_overrides_the_builtin_text(self):
        d = _methods_dir_with(_entry("split-policy-file", "planner", "モジュール単位で切ること"))
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.split_policy_directive("file"), "モジュール単位で切ること")
            # 別の値は上書きされない（id ごとの差し替え）
            self.assertEqual(kf.split_policy_directive("behavior"),
                             kf.SPLIT_POLICY_DIRECTIVES["behavior"])

    def test_run_tuning_wins_over_methods_dir(self):
        # dashboard が run 作成時へ複製したスナップショットが最優先（run 単位の決定性）
        d = _methods_dir_with(_entry("split-policy-behavior", "planner", "カタログ側"))
        t = _tuning_dir_with([_entry("split-policy-behavior", "planner", "run 複製側")])
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d, "AGENT_TUNING_DIR": t}):
            self.assertEqual(kf.split_policy_directive("behavior"), "run 複製側")

    def test_repo_agents_methods_overrides_installed_catalog(self):
        installed = _methods_dir_with(_entry("split-policy-behavior", "planner", "導入カタログ"))
        repo = tempfile.mkdtemp(prefix="kf-engine-repo-")
        os.makedirs(os.path.join(repo, ".agents", "methods"))
        with open(os.path.join(repo, ".agents", "methods", "split-policy-behavior.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_entry("split-policy-behavior", "planner", "リポジトリ側"), f,
                      ensure_ascii=False)
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": installed}):
                self.assertEqual(kf.split_policy_directive("behavior"), "リポジトリ側")
        finally:
            os.chdir(cwd)

    def test_broken_json_and_id_mismatch_fall_back(self):
        d = tempfile.mkdtemp(prefix="kf-engine-broken-")
        with open(os.path.join(d, "split-policy-behavior.json"), "w", encoding="utf-8") as f:
            f.write("{ broken")
        with open(os.path.join(d, "split-policy-file.json"), "w", encoding="utf-8") as f:
            json.dump(_entry("some-other-id", "planner", "id 不一致"), f)
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.split_policy_directive("behavior"),
                             kf.SPLIT_POLICY_DIRECTIVES["behavior"])
            self.assertEqual(kf.split_policy_directive("file"),
                             kf.SPLIT_POLICY_DIRECTIVES["file"])

    def test_role_mismatch_and_empty_text_fall_back(self):
        # role の合う本文が無い・空文字の上書きは、指示の抑止にならず組み込みへ倒れる
        # （split_policy はエンジンの分解方針そのもの——無指定の run を黙って無方針にしない）
        d = _methods_dir_with(_entry("split-policy-behavior", "worker", "worker 向け"),
                              _entry("split-policy-file", "planner", "   "))
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.split_policy_directive("behavior"),
                             kf.SPLIT_POLICY_DIRECTIVES["behavior"])
            self.assertEqual(kf.split_policy_directive("file"),
                             kf.SPLIT_POLICY_DIRECTIVES["file"])

    def test_invalid_directive_id_returns_fallback_without_lookup(self):
        self.assertEqual(kf.engine_directive("../etc/passwd", "planner", "fb"), "fb")
        self.assertEqual(kf.engine_directive("", "planner", "fb"), "fb")

    def test_granularity_auto_is_not_customizable(self):
        # auto は「flow-planner が complexity から導出する」という構造的な意味を持つ。
        # カタログに granularity-auto を置いても文面は注入されない。
        d = _methods_dir_with(_entry("granularity-auto", "planner", "auto の上書き"),
                              _entry("granularity-fine", "planner", "fine の上書き"))
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.granularity_directive("auto"), "")
            self.assertEqual(kf.granularity_directive(None), "")
            self.assertEqual(kf.granularity_directive("fine"), "fine の上書き")

    def test_tier_vocabulary_is_open_via_catalog(self):
        # 組み込み辞書は basic しか知らないが、agent-control の tier 語彙は開いている。
        # カタログ定義を置けばエンジン改修なしで他の tier にも指示文を足せる。
        d = _methods_dir_with(_entry("tier-small", "planner", "small 向けの計画指示"))
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.tier_planner_directive("small"), "small 向けの計画指示")
            self.assertEqual(kf.tier_evaluator_directive("small"), "")  # role 不一致は空のまま
        self.assertEqual(kf.tier_planner_directive("small"), "")  # カタログが無ければ従来どおり
        self.assertEqual(kf.tier_planner_directive(""), "")

    def test_tier_split_reads_the_dedicated_split_id(self):
        d = _methods_dir_with(_entry("tier-basic-split", "worker", "split の上書き"),
                              _entry("tier-basic", "worker", "こちらは読まれない"))
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.tier_split_directive("basic"), "split の上書き")

    def test_review_lenses_text_is_replaceable_but_keys_are_not(self):
        d = _methods_dir_with(_entry("review-lenses", "evaluator", "観点の上書き"))
        with mock.patch.dict(os.environ, {"AGENT_METHODS_DIR": d}):
            self.assertEqual(kf.review_lens_directive(), "観点の上書き")
        # run 履歴へ記録する観点キーはエンジン側の構造のまま
        self.assertEqual([key for key, _label, _detail in kf.REVIEW_LENSES],
                         ["duplication", "divergence", "verbosity"])


class BundledEngineDirectiveCatalogTests(unittest.TestCase):
    """同梱カタログ（methods/*.json）と組み込み文言の錠前——両者は同じ正文でなければならない。

    導入済み環境ではカタログ側が読まれ、未導入・壊れた環境では組み込みが読まれる。
    片方だけ直すと環境によって指示文が食い違うため、このテストが乖離で落ちる。"""

    def _bundled(self, mid):
        path = REPO_ROOT / "methods" / f"{mid}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _text(self, mid, role):
        return "\n".join(f["text"] for f in self._bundled(mid)["fragments"]
                         if f["role"] == role)

    def test_bundled_texts_match_the_builtin_fallbacks(self):
        self.assertEqual(self._text("split-policy-behavior", "planner"),
                         kf.SPLIT_POLICY_DIRECTIVES["behavior"])
        self.assertEqual(self._text("split-policy-file", "planner"),
                         kf.SPLIT_POLICY_DIRECTIVES["file"])
        for level in ("coarse", "fine", "finest"):
            self.assertEqual(self._text(f"granularity-{level}", "planner"),
                             kf.GRANULARITY_SCOPE_DIRECTIVES[level])
        self.assertEqual(self._text("tier-basic", "planner"),
                         kf.TIER_PLANNER_DIRECTIVES["basic"])
        self.assertEqual(self._text("tier-basic", "evaluator"),
                         kf.TIER_EVALUATOR_DIRECTIVES["basic"])
        self.assertEqual(self._text("tier-basic-split", "worker"),
                         kf.TIER_SPLIT_DIRECTIVES["basic"])
        self.assertEqual(self._text("review-lenses", "evaluator"),
                         kf.builtin_review_lens_directive())

    def test_bundled_entries_declare_engine_selection_and_stay_off(self):
        for mid in ("split-policy-behavior", "split-policy-file", "granularity-coarse",
                    "granularity-fine", "granularity-finest", "tier-basic",
                    "tier-basic-split", "review-lenses"):
            method = self._bundled(mid)
            self.assertEqual(method.get("selection"), "engine", mid)
            self.assertIs(method.get("enabled"), False, mid)
            self.assertNotIn("when", method, mid)


if __name__ == "__main__":
    unittest.main()
