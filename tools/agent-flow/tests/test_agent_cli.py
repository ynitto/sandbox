"""agent-flow の単体テスト — agent_cli（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class StructuredResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-data-")
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def test_result_data_roundtrip(self):
        self.bus.write_result("t1", "w", "done", "txt", data={"items": [1, 2, 3]})
        r = self.bus.read_result("t1")
        self.assertEqual(r["output"], "txt")
        self.assertEqual(r["data"], {"items": [1, 2, 3]})

    def test_result_without_data_has_no_key(self):
        self.bus.write_result("t1", "w", "done", "txt")
        self.assertNotIn("data", self.bus.read_result("t1"))

    def test_result_receipt_fields_roundtrip(self):
        # 実行 receipt v2（execution_decision）と処理契約の機械判定結果は result が正典。
        block = {"agent_cli": "ollama", "model": "gemma4:e4b",
                 "selection_source": "qualified-candidate", "rank": 1,
                 "reason": "r", "fallback_from": None}
        self.bus.write_result("t1", "w", "done", "txt", execution_decision=block,
                              operation_class="existing-test-repair",
                              local_patch_blockers=["書込 scope は 1 ファイル（2 件）"])
        rec = self.bus.read_result("t1")
        self.assertEqual(rec["execution_decision"], block)
        self.assertEqual(rec["operation_class"], "existing-test-repair")
        self.assertEqual(len(rec["local_patch_blockers"]), 1)
        self.bus.write_result("t2", "w", "done", "txt")
        for key in ("execution_decision", "operation_class", "local_patch_blockers"):
            self.assertNotIn(key, self.bus.read_result("t2"))

    def test_collect_dep_results_sees_through_gate(self):
        # planner が work→gate→synth と直列にしても、集約役は gate が検証した
        # 上流（t2,t3）の成果を受け取れる（gate 経由でも入力が空にならない）
        self.bus.write_graph({"nodes": {
            "t2": {"deps": [], "kind": "work"},
            "t3": {"deps": [], "kind": "work"},
            "gate": {"deps": ["t2", "t3"], "kind": "verify"},
            "synth": {"deps": ["gate"], "kind": "synthesize"},
        }})
        self.bus.write_result("t2", "w", "done", "out2")
        self.bus.write_result("t3", "w", "done", "out3")
        self.bus.write_result("gate", "w", "done", "verify=pass", data={"ok": True})
        node = {"deps": ["gate"], "kind": "synthesize"}
        dep, _ctx = kf._collect_dep_results(self.bus, node, "synthesize")
        self.assertEqual(set(dep), {"gate", "t2", "t3"})  # 上流が透過された
        self.assertEqual(dep["t2"]["output"], "out2", "集約役は全文を受ける")

    def test_dependency_digest_reduces_input_and_full_is_explicit(self):
        self.bus.write_result("a", "w", "done", "x" * 5000, data={"items": list(range(100))})
        digest_node = {"deps": ["a"], "kind": "work"}
        digest, ctx = kf._collect_dep_results(self.bus, digest_node, "work")
        self.assertGreater(ctx["saved_chars"], 3000)
        self.assertIn("omitted details", digest["a"]["output"])
        full_node = {"deps": ["a"], "kind": "work", "dependency_input": "full"}
        full, full_ctx = kf._collect_dep_results(self.bus, full_node, "work")
        self.assertEqual(full["a"]["output"], "x" * 5000)
        self.assertEqual(full_ctx["saved_chars"], 0)

    def test_judging_kinds_receive_full_dependencies_by_default(self):
        """依存成果そのものが判断対象の役割へ要約を渡さない。

        verify が 600 字の要約で pass/fail を決めると、品質ゲートが成果物を見ていない
        ことになる（C5・C10）。集約・裁定も依存の中身を突き合わせるのが仕事なので同じ。
        """
        self.bus.write_result("a", "w", "done", "y" * 5000)
        for kind in ("verify", "reduce", "synthesize", "judge", "filter"):
            dep, ctx = kf._collect_dep_results(self.bus, {"deps": ["a"], "kind": kind}, kind)
            self.assertEqual(dep["a"]["output"], "y" * 5000, kind)
            self.assertEqual(ctx["mode"], "full", kind)
        # 明示宣言は既定より強い（要約でよいと分かっている verify は落とせる）
        dep, ctx = kf._collect_dep_results(
            self.bus, {"deps": ["a"], "kind": "verify", "dependency_input": "digest"}, "verify")
        self.assertEqual(ctx["mode"], "digest")

    def test_digest_reports_the_whole_body_as_omitted_when_summary_is_declared(self):
        """依存が自前の summary を持つとき、本文は 1 文字も渡していない。"""
        self.bus.write_result("a", "w", "done", "z" * 900, data={"summary": "短い要約"})
        digest, _ctx = kf._collect_dep_results(self.bus, {"deps": ["a"], "kind": "work"}, "work")
        self.assertEqual(digest["a"]["data"]["omitted"]["output_chars"], 900)

    def test_collect_dep_results_no_passthrough_for_work(self):
        # 非集約ノードは透過しない（gate をそのまま受ける）
        self.bus.write_graph({"nodes": {
            "a": {"deps": [], "kind": "work"},
            "gate": {"deps": ["a"], "kind": "verify"},
        }})
        self.bus.write_result("a", "w", "done", "oa")
        self.bus.write_result("gate", "w", "done", "verify=pass", data={"ok": True})
        dep, _ctx = kf._collect_dep_results(self.bus, {"deps": ["gate"], "kind": "work"}, "work")
        self.assertEqual(set(dep), {"gate"})

    def test_executor_returns_text_and_data(self):
        text, data = kf.execute_stub("classify", "backend のバグ", {}, None)
        self.assertEqual(text, "class=backend")
        self.assertEqual(data, {"label": "backend"})
        text, data = kf.execute_stub("work", "ふつうの仕事", {}, None)
        self.assertIsNone(data)

    def test_readonly_fails_closed_for_legacy_executor(self):
        def legacy_executor(kind, goal, dep_results, model, art_dir, dep_arts):
            return "書き込み権限のある旧 executor", None

        with self.assertRaisesRegex(RuntimeError, "readonly 契約がありません"):
            kf.call_executor(
                legacy_executor, "work", "設計する", {}, None, None, None,
                readonly=True,
            )

    def test_extract_and_retrieve_repair_invalid_contracts(self):
        valid = {
            "extract": {"records": [{"fields": {"name": "A"}, "evidence": [{
                "source_id": "s1", "locator": "L1", "excerpt": "A"}]}], "warnings": []},
            "retrieve": {"sources": [{"id": "s1", "uri": "file:///tmp/a", "title": "A",
                "locator": "L1", "excerpt": "A", "digest": "sha256:x"}], "warnings": []},
        }
        for kind, expected in valid.items():
            with self.subTest(kind=kind), mock.patch.object(
                    kf, "run_agent", side_effect=["{}", json.dumps(expected)]) as run:
                _text, data = kf.execute_agent(kind, "根拠付きで処理", {}, None)
            self.assertEqual(data, expected)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].kwargs["purpose"], kind)
            self.assertIn('"records"' if kind == "extract" else '"sources"',
                          run.call_args_list[0].args[0])

    def test_reduce_aggregates_dependency_data(self):
        deps = {
            "a": {"output": "oa", "data": ["x", "y"]},
            "b": {"output": "ob", "data": ["z"]},
            "c": {"output": "oc"},  # data 無し → output を要素化
        }
        text, data = kf.execute_stub("reduce", "集約", deps, None)
        self.assertEqual(data["count"], 4)
        self.assertEqual(sorted(str(i) for i in data["items"]), ["oc", "x", "y", "z"])


class OutputSanitizeTests(unittest.TestCase):
    def test_strip_ansi(self):
        raw = "\x1b[38;5;141m> \x1b[0mhello\x1b[1mX\x1b[22m"
        self.assertEqual(kf.strip_ansi(raw), "> helloX")
        self.assertEqual(kf.strip_ansi(""), "")

    def test_reconcile_count_fixes_mismatch(self):
        d = kf._reconcile_count({"primes": [2, 3, 5], "count": 99, "range": {"min": 2}})
        self.assertEqual(d["count"], 3)

    def test_reconcile_count_skips_when_ambiguous(self):
        # count 無し / 複数リスト / 非 dict は変更しない
        self.assertEqual(kf._reconcile_count({"primes": [2, 3]}), {"primes": [2, 3]})
        self.assertEqual(kf._reconcile_count({"a": [1], "b": [1, 2], "count": 5})["count"], 5)
        self.assertEqual(kf._reconcile_count([1, 2, 3]), [1, 2, 3])


class AgentFailureTests(unittest.TestCase):
    """エージェント CLI の失敗を、人が原因に辿り着ける形で表に出すこと。

    CLI は起動バナー（workdir / model / プロンプト全文）を stderr に流す。以前は stderr の
    「先頭」だけを切り取っていたため、肝心のエラーがバナーに埋もれて消えた。実際 codex の
    「利用上限に達した」を取り逃し、全ノードが理由不明の failed になった。"""

    # 実物に近い形：バナーが先頭を埋め、本当のエラーは末尾に出る
    BANNER = ("OpenAI Codex v0.144.1\n--------\nworkdir: /x\nmodel: gpt-5.6-sol\n"
              + "プロンプト全文 " * 80)

    def test_usage_limit_is_surfaced(self):
        err = self.BANNER + "\nERROR: You've hit your usage limit. Upgrade to Pro ... try again at 9:44 PM."
        msg = kf._agent_failure("codex", 1, "", err)
        self.assertIn("利用上限", msg.split("\n")[0])   # 見出しで分かる
        self.assertIn("usage limit", msg)               # 原文も残る（末尾を拾う）

    def test_hint_comes_from_the_cli_that_actually_ran(self):
        """同じ語（usage limit）を拾う規則を複数の CLI が持つとき、走った CLI の文言を出す。

        規則を全定義から混ぜて先頭一致で採ると、どれが当たるかは「プラグインキャッシュに
        何が載っているか」＝実行順で決まる。同じ入力で違う案内が出ると、人は自分が使って
        いない CLI の対処法を読まされる。
        """
        err = "ERROR: You've hit your usage limit."
        for name in ("codex", "cursor", "copilot"):
            kf.load_agent_plugin(name)      # キャッシュを汚してから引く
        self.assertIn("Codex", kf._agent_failure("codex", 1, "", err).split("\n")[0])
        self.assertIn("Cursor", kf._agent_failure("cursor", 1, "", err).split("\n")[0])

    def test_auth_failure_is_surfaced(self):
        msg = kf._agent_failure("kiro-cli", 0, "", "SendMessageError: AccessDeniedException")
        self.assertIn("認証", msg.split("\n")[0])

    def test_bad_model_is_surfaced(self):
        msg = kf._agent_failure("claude", 1, "", "There's an issue with the selected model (claude-opus).")
        self.assertIn("モデル", msg.split("\n")[0])

    def test_unknown_failure_keeps_the_tail(self):
        # 既知パターンに当たらなくても、末尾（＝エラーが出る場所）は必ず残す
        err = self.BANNER + "\nsomething exploded at line 42"
        msg = kf._agent_failure("codex", 1, "", err)
        self.assertIn("something exploded", msg)

    def test_empty_response_with_rc0_is_a_failure(self):
        # kiro-cli は認証が切れるとバナーだけ出して rc=0 で終わる。空を成功として扱うと
        # worker は「空の成果物で done」、planner は stub へ黙って落ちる（沈黙した失敗）。
        proc = types.SimpleNamespace(returncode=0, stdout="  \n", stderr="AccessDeniedException")
        with mock.patch.object(kf.subprocess, "run", return_value=proc):
            with self.assertRaises(RuntimeError) as cm:
                kf.run_agent("p", None)
        self.assertIn("空の応答", str(cm.exception))
        self.assertIn("認証", str(cm.exception))


class EmptyOutputRetryTests(unittest.TestCase):
    """JSON 契約の役割の空応答は「内容の失敗」でなく形式違反として、有界に言い直す。

    ツールループ型の CLI（agent-ollama --tools 等）を split / planner に振ると、本文の
    代わりに制御語だけを返して空応答で落ちる。1 発 fail にすると再計画の予算だけが焼け、
    同じ所で毎回転ぶ（柱3 / C10）。回数は format_retries で有界（C7）。"""

    def setUp(self):
        self.calls = []

    def _proc(self, stdout):
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def _run(self, purpose, outputs):
        def fake_run(cmd, **kw):
            # プロンプトの渡し方（stdin / argv）は CLI 定義次第なので両方から拾う。
            self.calls.append((kw.get("input") or "") + " ".join(str(c) for c in cmd))
            return self._proc(outputs[len(self.calls) - 1])
        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            return kf.run_agent("元のプロンプト", None, purpose=purpose)

    def test_json_contract_role_retries_once_with_the_contract_restated(self):
        text = self._run("split", ["  \n", '[{"id": "t1"}]'])
        self.assertEqual(str(text), '[{"id": "t1"}]')
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("前回の出力は空でした", self.calls[0])
        self.assertIn("前回の出力は空でした", self.calls[1])   # 2 回目だけ契約を言い直す

    def test_retry_budget_is_bounded_and_the_failure_says_so(self):
        with mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            with self.assertRaises(RuntimeError) as cm:
                self._run("split", ["", "  \n"])
        self.assertEqual(len(self.calls), 2)                  # 1 + format_retries で止まる
        self.assertIn("再要求後", str(cm.exception))

    def test_free_text_role_still_fails_immediately(self):
        # work は本文が成果物。空を言い直しても意味が無く、その場で上位へ返す。
        with self.assertRaises(RuntimeError):
            self._run("work", ["  \n"])
        self.assertEqual(len(self.calls), 1)

    def test_unclassified_empty_response_is_carried_as_transient(self):
        """分類の付かない空応答は「内容の失敗」ではなく transient として運ぶ。

        内容の失敗として上げると、評価役がこれを実装の失敗と読んで計画を作り直す
        （実際 agent-ollama の空応答から push 待機タスクが捏造された）。transient なら
        run 単位で打ち切り、cooldown 後の auto-heal が done を温存して再開する。"""
        with self.assertRaises(RuntimeError) as cm:
            self._run("work", ["  \n"])
        self.assertEqual(kf.classify_agent_failure(str(cm.exception))[0], "transient")

    def test_known_classification_wins_over_transient(self):
        # 認証切れの空応答（kiro-cli のバナーだけ）は env 側の分類を保つ——
        # transient で上書きすると、直らない環境不良を再開ループで叩き続ける。
        def fake_run(cmd, **kw):
            self.calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="  \n",
                                         stderr="SendMessageError: AccessDeniedException")
        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as cm:
                kf.run_agent("p", None, purpose="work")
        self.assertEqual(kf.classify_agent_failure(str(cm.exception))[0], "auth")


class JsonVariantRoutingTests(unittest.TestCase):
    """JSON 契約の役割は、CLI 定義が申告する用途別の変種へ自動で振り替わる（柱3 / C9）。

    人が役割ごとに `agents:` を書き並べる運用にすると、節約のための設定を人の時間で払う。
    variant は「1 つのエージェント（ollama）を用途で使い分ける」実体なので、振り替え後の
    モデルも変種自身の調整（default_model）へ寄せる——base 側で選んだモデルは、振り替わら
    ない用途（work 等・素の ollama をそのまま使う）にだけ効く。"""

    def setUp(self):
        self._cli, self._ov = kf._AGENT_CLI, kf._AGENT_OVERRIDES
        self._run_ov = dict(kf._EXECUTION_OVERRIDES)
        kf._AGENT_CLI, kf._AGENT_OVERRIDES, kf._EXECUTION_OVERRIDES = "ollama", {}, {}

    def tearDown(self):
        kf._AGENT_CLI, kf._AGENT_OVERRIDES = self._cli, self._ov
        kf._EXECUTION_OVERRIDES = self._run_ov

    def test_json_contract_roles_swap_to_the_variant(self):
        for purpose in ("planner", "evaluator", "filter", "judge", "reduce"):
            self.assertEqual(kf._agent_for(purpose)[0], "ollama-json", purpose)

    def test_list_contract_role_swaps_to_the_array_variant(self):
        # split はトップレベル配列でないと fan-out が展開されない。ollama の JSON モードは
        # 配列を表現できないので、配列用の起動形（--format array）へ振り替える。
        self.assertEqual(kf._agent_for("split")[0], "ollama-list")

    def test_aider_split_swaps_to_the_thinking_list_variant(self):
        kf._AGENT_CLI = "aider"
        self.assertEqual(kf._agent_for("split")[0], "ollama-list-thinking")

    def test_verify_swaps_to_its_own_tuned_variant(self):
        # ollama-verify は他の変種から辿られない——variants 経由がこの用途への唯一の入口。
        self.assertEqual(kf._agent_for("verify"), ("ollama-verify", "gemma4:12b"))

    def test_retrieve_swaps_to_the_read_capable_variant(self):
        # ollama-json へ寄せると read tool を失うため、根拠を読める定義へ振り替える。
        self.assertEqual(kf._agent_for("retrieve")[0], "ollama-read")

    def test_free_text_roles_keep_the_declared_cli(self):
        # work / map はワークスペースの本文や自由記述を返すので振り替えない。
        for purpose in ("work", "map", "synthesize", ""):
            self.assertEqual(kf._agent_for(purpose)[0], "ollama", purpose)

    def test_cli_without_a_declared_variant_is_untouched(self):
        kf._AGENT_CLI = "codex"
        self.assertEqual(kf._agent_for("split")[0], "codex")

    def test_configured_model_survives_the_swap(self):
        # 変種は同じエンジンの起動形違い。設定 agents: で人が明示したモデルは、
        # tier/agent-control が自動選択したモデルとは違い、振り替えても持ち越す。
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides(
            {"split": {"agent_cli": "ollama", "model": "qwen3.5:9b"}})
        self.assertEqual(kf._agent_for("split"), ("ollama-list", "qwen3.5:9b"))

    def test_explicit_run_level_model_survives_the_swap(self):
        # run 単位の明示指定（この実行だけの固定）は自動選択層より優先するという既存の
        # 不変条件を、variant の既定モデルにも適用する——ここだけは変種で上書きしない。
        kf._EXECUTION_OVERRIDES = kf._normalize_execution_overrides({
            "version": 1, "kinds": {"verify": {"agent_cli": "ollama", "model": "custom-model"}},
        })
        self.assertEqual(kf._agent_for("verify"), ("ollama-verify", "custom-model"))


class ExplicitAgentOverrideTests(unittest.TestCase):
    """呼び出し 1 回だけの明示指定（検証計画の `policy.agent` 等）が最優先で効くこと。

    ノード全体の設定（agent-control）に負けると、詰まったタスク 1 件のために全プロジェクトの
    検証を高いモデルへ寄せることになる（柱2 / C4）。"""

    def setUp(self):
        self._cli, self._ov = kf._AGENT_CLI, kf._AGENT_OVERRIDES
        kf._AGENT_CLI, kf._AGENT_OVERRIDES = "ollama", {}
        self.addCleanup(self._restore)

    def _restore(self):
        kf._AGENT_CLI, kf._AGENT_OVERRIDES = self._cli, self._ov

    def test_explicit_agent_beats_the_role_override(self):
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides(
            {"verify": {"agent_cli": "ollama", "model": "qwen3.5:9b"}})
        self.assertEqual(kf._effective_agent("verify", None,
                                             {"agent_cli": "codex", "model": "opus"}),
                         ("codex", "opus"))

    def test_partial_override_keeps_the_rest(self):
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides({"verify": {"model": "qwen3"}})
        self.assertEqual(kf._effective_agent("verify", None, {"agent_cli": "codex"}),
                         ("codex", "qwen3"))
        # cli は verify 用の変種（ollama-verify）へ構造的に振り替わるが、設定で明示した
        # モデルは変種の既定（gemma4:12b）に置き換わらず持ち越す。
        self.assertEqual(kf._effective_agent("verify", None, None), ("ollama-verify", "qwen3"))

    def test_explicit_timeout_is_used_for_the_call(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["timeout"] = kw.get("timeout")
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(kf, "_agent_timeout", return_value=600.0):
            kf.run_agent("p", None, purpose="verify", agent={"timeout_sec": 1800})
        self.assertEqual(seen["timeout"], 1800.0)

    def test_broken_timeout_falls_back_to_the_setting(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["timeout"] = kw.get("timeout")
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(kf, "_agent_timeout", return_value=600.0):
            kf.run_agent("p", None, purpose="verify", agent={"timeout_sec": "x"})
        self.assertEqual(seen["timeout"], 600.0)

    def test_no_override_leaves_resolution_untouched(self):
        # 設定・run いずれも明示していないので、verify は自身の変種（gemma4:12b
        # チューニング）へ振り替わる——呼び出し値 "m" は変種の既定に置き換わる。
        self.assertEqual(kf._effective_agent("verify", "m", None), ("ollama-verify", "gemma4:12b"))
        self.assertEqual(kf._effective_agent("verify", "m", {}), ("ollama-verify", "gemma4:12b"))


class AgentTimeoutTests(unittest.TestCase):
    """エージェント CLI のハングがタイムアウトで失敗化され、run が無限停止しないこと。"""

    def test_run_agent_timeout_raises_runtimeerror(self):
        import subprocess
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="kiro-cli", timeout=k.get("timeout"))
        with mock.patch.object(kf.subprocess, "run", side_effect=boom), \
                mock.patch.object(kf, "_TRANSIENT_RETRIES", 0):   # レイヤ1 は別テストで検証
            with self.assertRaises(RuntimeError) as ctx:
                kf.run_agent("素数を列挙", None)
        self.assertIn("タイムアウト", str(ctx.exception))
        # ハングは transient タグ付き＝レイヤ1（in-place 再試行）の対象になる
        self.assertEqual(kf.classify_agent_failure(str(ctx.exception))[0], "transient")

    def test_agent_timeout_env_override(self):
        with mock.patch.dict(os.environ, {"AGENT_FLOW_TIMEOUT": "0"}, clear=False):
            os.environ.pop("AGENT_FLOW_KIRO_TIMEOUT", None)
            self.assertIsNone(kf._agent_timeout())   # 0/負で無効化
        with mock.patch.dict(os.environ, {"AGENT_FLOW_TIMEOUT": "120"}, clear=False):
            os.environ.pop("AGENT_FLOW_KIRO_TIMEOUT", None)
            self.assertEqual(kf._agent_timeout(), 120.0)

    def test_agent_timeout_legacy_env_still_honored(self):
        # 後方互換: 旧名 AGENT_FLOW_KIRO_TIMEOUT も受理する（新名未設定時）
        with mock.patch.dict(os.environ, {"AGENT_FLOW_KIRO_TIMEOUT": "90"}, clear=False):
            os.environ.pop("AGENT_FLOW_TIMEOUT", None)
            self.assertEqual(kf._agent_timeout(), 90.0)

    def test_agent_timeout_new_env_beats_legacy(self):
        # 新名が旧名より優先される
        with mock.patch.dict(os.environ,
                             {"AGENT_FLOW_TIMEOUT": "30", "AGENT_FLOW_KIRO_TIMEOUT": "120"}):
            self.assertEqual(kf._agent_timeout(), 30.0)

    def test_agent_timeout_config_beats_env(self):
        # 設定ファイル（_configure_thresholds 経由）が環境変数より優先される
        with mock.patch.object(kf, "_AGENT_TIMEOUT", 300.0), \
             mock.patch.dict(os.environ, {"AGENT_FLOW_TIMEOUT": "120"}):
            self.assertEqual(kf._agent_timeout(), 300.0)
        with mock.patch.object(kf, "_AGENT_TIMEOUT", 0.0):
            self.assertIsNone(kf._agent_timeout())   # 設定の 0/負も無効化として尊重

    def test_control_flow_timeout_applies_to_the_next_agent_call(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs.get("timeout"))
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        control = {"workloads": {"flow": {"timeout_sec": 90}}}
        with mock.patch.object(kf, "_load_control", return_value=control), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent("verify", None, purpose="verify")
        self.assertEqual(calls, [90.0])

    def test_control_purpose_timeout_beats_the_flow_default(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs.get("timeout"))
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        control = {"workloads": {"flow": {
            "timeout_sec": 90,
            "agents": {"verify": {"timeout_sec": 30}},
        }}}
        with mock.patch.object(kf, "_load_control", return_value=control), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent("verify", None, purpose="verify")
        self.assertEqual(calls, [30.0])

    def test_control_worker_timeout_is_the_default_for_node_kinds(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs.get("timeout"))
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        control = {"workloads": {"flow": {
            "timeout_sec": 90,
            "agents": {"worker": {"timeout_sec": 45}},
        }}}
        with mock.patch.object(kf, "_load_control", return_value=control), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent("work", None, purpose="work")
        self.assertEqual(calls, [45.0])

    def test_stub_sleep_max_config_beats_env(self):
        # stub_sleep_max も設定が環境変数より優先される（0 で即時）
        calls = []
        with mock.patch.object(kf, "_STUB_SLEEP_MAX", 0.0), \
             mock.patch.dict(os.environ, {"AGENT_FLOW_STUB_SLEEP_MAX": "5"}), \
             mock.patch.object(kf.time, "sleep", side_effect=lambda s: calls.append(s)):
            kf._stub_sleep()
        self.assertEqual(calls, [])   # 設定 0 → sleep されない

    def test_configure_thresholds_pins_config_values(self):
        # resolve_config 済みの args から agent_timeout / stub_sleep_max が確定すること
        import argparse
        args = argparse.Namespace(argv_limit=None, executor_dir=None,
                                  agent_timeout=45.0, stub_sleep_max=0.0)
        with mock.patch.object(kf, "_AGENT_TIMEOUT", None), \
             mock.patch.object(kf, "_STUB_SLEEP_MAX", None):
            kf._configure_thresholds(args)
            self.assertEqual(kf._AGENT_TIMEOUT, 45.0)
            self.assertEqual(kf._STUB_SLEEP_MAX, 0.0)


class AgentCliTests(unittest.TestCase):
    """agent_cli 設定による LLM 実行 CLI の切替（kiro-cli / Claude Code）。"""

    @staticmethod
    def _capture_run():
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = list(cmd)
            calls["input"] = kw.get("input")
            calls["cwd"] = kw.get("cwd")
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return calls, fake_run

    def test_default_is_kiro_cli_with_argv_prompt(self):
        calls, fake = self._capture_run()
        with mock.patch.object(kf.subprocess, "run", side_effect=fake):
            out = kf.run_agent("プロンプト", "m1")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][:4],
                         ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["cmd"][-1], "プロンプト")   # 従来どおり argv 渡し
        self.assertIsNone(calls["input"])

    def test_run_agent_uses_requested_cwd(self):
        calls, fake = self._capture_run()
        with mock.patch.object(kf.subprocess, "run", side_effect=fake):
            kf.run_agent("プロンプト", None, cwd="/tmp/agent-flow-workspace")
        self.assertEqual(calls["cwd"], "/tmp/agent-flow-workspace")

    def test_claude_uses_headless_stdin(self):
        calls, fake = self._capture_run()
        with mock.patch.object(kf, "_AGENT_CLI", "claude"), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake):
            out = kf.run_agent("プロンプト", "claude-sonnet")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][0], "claude")
        self.assertIn("-p", calls["cmd"])
        self.assertIn("--output-format", calls["cmd"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["input"], "プロンプト")     # stdin 渡し
        self.assertNotIn("プロンプト", calls["cmd"])       # argv には載せない

    def test_claude_large_prompt_skips_spill(self):
        # stdin 渡しは ARG_MAX に当たらないため、argv_limit 超過でも一時ファイルへ退避しない
        calls, fake = self._capture_run()
        big = "x" * (kf._agent_argv_limit() + 10)
        with mock.patch.object(kf, "_AGENT_CLI", "claude"), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake):
            kf.run_agent(big, None)
        self.assertEqual(calls["input"], big)
        self.assertTrue(all("ファイル" not in str(a) for a in calls["cmd"]))

    def test_copilot_uses_prompt_flag(self):
        calls, fake = self._capture_run()
        with mock.patch.object(kf, "_AGENT_CLI", "copilot"), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake):
            out = kf.run_agent("プロンプト", "gpt-5")
        self.assertEqual(out, "ok")
        self.assertEqual(calls["cmd"][0], "copilot")
        self.assertIn("-s", calls["cmd"])                  # 応答本文のみ
        self.assertIn("--allow-all-tools", calls["cmd"])   # 非対話モードの必須フラグ
        i = calls["cmd"].index("-p")
        self.assertEqual(calls["cmd"][i + 1], "プロンプト")  # -p の引数で渡す
        self.assertIn("--model", calls["cmd"])
        self.assertIsNone(calls["input"])

    def test_copilot_large_prompt_spills_to_file(self):
        # copilot は argv（-p）渡しのため、kiro と同じスピル退避が効く
        calls, fake = self._capture_run()
        big = "x" * (kf._agent_argv_limit() + 10)
        with mock.patch.object(kf, "_AGENT_CLI", "copilot"), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake):
            kf.run_agent(big, None)
        i = calls["cmd"].index("-p")
        self.assertNotEqual(calls["cmd"][i + 1], big)       # 全文は argv に載せない
        self.assertIn("ファイル", calls["cmd"][i + 1])       # 参照渡しの短い指示に置換
        self.assertIsNone(calls["input"])

    def test_codex_uses_exec_stdin_and_last_message_file(self):
        calls = {}
        def fake_run(cmd, **kw):
            calls["cmd"] = list(cmd)
            calls["input"] = kw.get("input")
            # codex は最終応答を --output-last-message のファイルへ書く
            i = cmd.index("--output-last-message")
            with open(cmd[i + 1], "w", encoding="utf-8") as f:
                f.write("最終応答")
            return types.SimpleNamespace(returncode=0, stdout="イベントログ...", stderr="")
        with mock.patch.object(kf, "_AGENT_CLI", "codex"), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            out = kf.run_agent("プロンプト", "gpt-5-codex")
        self.assertEqual(out, "最終応答")                   # stdout のログではなくファイルの中身
        self.assertEqual(calls["cmd"][:2], ["codex", "exec"])
        self.assertIn("--skip-git-repo-check", calls["cmd"])
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", calls["cmd"])
        self.assertIn("--model", calls["cmd"])
        self.assertEqual(calls["cmd"][-1], "-")             # プロンプトは stdin（"-"）
        self.assertEqual(calls["input"], "プロンプト")
        i = calls["cmd"].index("--output-last-message")
        self.assertFalse(os.path.exists(calls["cmd"][i + 1]))   # 一時ファイルは掃除される

    def test_codex_falls_back_to_stdout_when_last_message_empty(self):
        calls, fake = self._capture_run()                   # ファイルへ何も書かない
        with mock.patch.object(kf, "_AGENT_CLI", "codex"), \
             mock.patch.object(kf.subprocess, "run", side_effect=fake):
            out = kf.run_agent("プロンプト", None)
        self.assertEqual(out, "ok")                         # stdout へフォールバック

    def test_configure_thresholds_sets_agent_cli(self):
        orig = kf._AGENT_CLI
        try:
            kf._configure_thresholds(types.SimpleNamespace(agent_cli="claude"))
            self.assertEqual(kf._AGENT_CLI, "claude")
        finally:
            kf._AGENT_CLI = orig

    def test_child_base_forwards_agent_cli_and_parses(self):
        ns = types.SimpleNamespace(lease=1800.0, git=None, agent_cli="cursor")
        base = kf._child_base(ns, "/tmp/bus")
        i = base.index("--agent-cli")
        self.assertEqual(base[i + 1], "cursor")
        # 子プロセスの argv として parser が受理する（usage エラーで即死しない）
        args = kf.build_parser().parse_args(base[2:] + ["status"])
        self.assertEqual(args.agent_cli, "cursor")


class StructuredExtractionTests(unittest.TestCase):
    """自由記述 kind の本文に紛れた JSON 風断片を data に誤昇格させないこと。"""

    def test_work_does_not_extract_incidental_json(self):
        # 本文に "issues": [] を含む work 出力でも data は None（誤抽出の事故防止）
        txt = 'verify=pass（修正不要）。t2の検査で問題なし（{"ok": true, "issues": []}）。通過。'
        with mock.patch.object(kf, "run_agent", return_value=txt):
            _, data = kf.execute_agent("work", "修正し通過", {}, None)
        self.assertIsNone(data)

    def test_generate_does_not_extract_incidental_json(self):
        with mock.patch.object(kf, "run_agent", return_value="例: [1, 2] のような配列を返す関数"):
            _, data = kf.execute_agent("generate", "関数を書く", {}, None)
        self.assertIsNone(data)

    def test_split_still_extracts_list(self):
        with mock.patch.object(kf, "run_agent", return_value='["1-100", "101-200"]'):
            _, data = kf.execute_agent("split", "分割", {}, None)
        self.assertEqual(data, ["1-100", "101-200"])

    def test_reduce_still_extracts_and_reconciles(self):
        with mock.patch.object(kf, "run_agent",
                               return_value='{"primes": [2, 3, 5], "count": 99}'):
            _, data = kf.execute_agent("reduce", "集約", {}, None)
        self.assertEqual(data["count"], 3)  # 実リスト長へ補正


class FlowWorkerSkillTests(unittest.TestCase):
    """flow-worker スキル連携: 実行規律入りプロンプトの利用と組み込みフォールバック。"""

    REPO_ROOT = HERE.parents[2]

    def setUp(self):
        # スキル検索はワークスペース（cwd）起点なのでリポジトリルートへ移動する
        self._cwd = os.getcwd()
        os.chdir(self.REPO_ROOT)
        # 解決メモをテスト毎にリセット（他テストの cwd の影響を受けない）
        kf._worker_skill_script.clear()

    def tearDown(self):
        os.chdir(self._cwd)
        kf._worker_skill_script.clear()

    def _capture_prompt(self, fn, *args, **kwargs):
        reply = kwargs.pop("_reply", "ok")
        seen = {}

        def fake_run(prompt, model, purpose="", **_kw):
            seen["prompt"] = prompt
            return reply

        with mock.patch.object(kf, "run_agent", side_effect=fake_run):
            fn(*args, **kwargs)
        return seen["prompt"]

    def test_find_skill_script_locates_flow_worker(self):
        path = kf._find_skill_script("flow-worker", "prompt.py")
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith(os.path.join("flow-worker", "scripts", "prompt.py")))

    def test_execute_agent_uses_skill_discipline_prompt(self):
        prompt = self._capture_prompt(
            kf.execute_agent, "work", "ログイン画面を追加", {"t0": {"output": "依存成果"}}, None,
            repo_instruction="【ワークスペース】/tmp/ws", request="EC サイトを作る")
        self.assertIn("三つの約束", prompt)        # スキル由来の規律ブロック
        self.assertIn("ログイン画面を追加", prompt)  # goal 維持
        self.assertIn("【ワークスペース】/tmp/ws", prompt)  # インターフェース情報の伝搬
        self.assertIn("EC サイトを作る", prompt)     # run の元要求（全体文脈）
        self.assertIn("[t0] 依存成果", prompt)

    def test_work_terminal_ok_false_is_structured_without_promoting_body_json(self):
        reply = '本文の例 {"sample": 1}\n\n{"ok": false, "issues": ["未完了"]}'
        with mock.patch.object(kf, "run_agent", return_value=reply):
            text, data = kf.execute_agent("work", "g", {}, None)
        self.assertEqual(text, reply)
        self.assertEqual(data, {"ok": False, "issues": ["未完了"]})

    def test_generate_terminal_ok_false_is_structured_too(self):
        # プロンプトは実行系の全 kind へ「未完了なら {"ok": false}」と指示している。
        # work だけ読んでいた間、generate の自己申告した未完了が done で通っていた。
        reply = '書けたところまで\n\n{"ok": false, "issues": ["テスト未実行"]}'
        with mock.patch.object(kf, "run_agent", return_value=reply):
            text, data = kf.execute_agent("generate", "g", {}, None)
        self.assertEqual(text, reply)
        self.assertEqual(data, {"ok": False, "issues": ["テスト未実行"]})

    def test_agent_ollama_incomplete_output_is_read_as_not_ok(self):
        """agent-ollama が打ち切りで出す封筒を、そのままの本文で未完了と判定できること。

        文字列の形を両側で別々に決めると、片方の書式が変わった日に「途中経過が done」へ
        黙って戻る。実物の stdout を作って読ませ、契約を 1 本に縛る（柱2 / C5）。"""
        from agentcore import ollama_adapter
        out = io.StringIO()
        with mock.patch.object(ollama_adapter.ollama_loop, "run_loop", return_value={
                "text": "調べ始めたところまでの報告", "tokens_in": 1, "tokens_out": 2,
                "rounds": 1, "status": "no_command"}), \
                mock.patch.object(ollama_adapter, "load_profile_env", return_value={}), \
                mock.patch.object(ollama_adapter.ollama_context, "resolve_limit",
                                  return_value=(8192, "server")), \
                mock.patch.object(ollama_adapter.sys, "stdin", io.StringIO("やって")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ollama_adapter.main(["qwen3", "--tools", "--no-log"]), 0)
        body = out.getvalue()
        for kind in ("work", "generate"):
            with mock.patch.object(kf, "run_agent", return_value=body):
                _, data = kf.execute_agent(kind, "g", {}, None)
            self.assertIs(data.get("ok"), False, kind)
        # verify は fail クローズ側で受ける（打ち切った検証を pass にしない）。
        with mock.patch.object(kf, "run_agent", return_value=body):
            _, data = kf.execute_agent("verify", "g", {}, None)
        self.assertIs(data.get("ok"), False)

    def test_envelope_kinds_match_the_prompt_side_exec_kinds(self):
        """封筒を指示する kind と読む kind を一致させる（片方だけ増えると黙って done になる）。"""
        script = kf._find_skill_script("flow-worker", "prompt.py")
        spec = importlib.util.spec_from_file_location("flow_worker_prompt_kinds", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # EXEC_KINDS のうち JSON 抽出をしない kind = 封筒を読むべき kind。
        want = {k for k in module.EXEC_KINDS if k not in kf.STRUCTURED_KINDS}
        self.assertEqual(set(kf._ENVELOPE_KINDS), want)

    def test_execute_agent_verify_skill_prompt_keeps_contract(self):
        prompt = self._capture_prompt(kf.execute_agent, "verify", "検証する", {}, None)
        self.assertIn("再導出", prompt)
        self.assertIn("verify=pass", prompt)
        self.assertIn('{"ok": true|false, "issues": ["..."]}', prompt)
        # 証跡規律: 実行したコマンドと終了コードを引用せずに pass させない
        # （同じ安いモデルが verify も担うと、存在確認だけで「要件を満たす」と作文する）。
        self.assertIn("証跡", prompt)
        self.assertIn("終了コード", prompt)

    def test_execute_agent_falls_back_when_skill_disabled(self):
        with mock.patch.object(kf, "_WORKER_SKILL", "none"):
            prompt = self._capture_prompt(kf.execute_agent, "work", "g", {}, None)
        self.assertNotIn("三つの約束", prompt)
        self.assertIn("成果物を簡潔に直接出力してください", prompt)

    def test_execute_agent_falls_back_when_script_broken(self):
        # 解決メモに壊れたパスを注入 → subprocess 失敗 → 組み込みプロンプトで続行
        with mock.patch.dict(kf._worker_skill_script,
                             {"flow-worker": "/nonexistent/prompt.py"}, clear=True):
            prompt = self._capture_prompt(kf.execute_agent, "work", "g", {}, None)
        self.assertIn("成果物を簡潔に直接出力してください", prompt)

    def test_continue_agent_uses_skill_evaluator_prompt(self):
        nodes = {"t1": {"goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "済",
                          "data": {"guidance": "APIはv2で"}}}
        prompt = self._capture_prompt(
            kf.continue_agent, "req", nodes, results, 0,
            _reply='{"decision":"done","reason":"ok","new_tasks":[]}')
        self.assertIn("評価規律", prompt)
        self.assertIn('"decision":"done"|"replan"', prompt)  # 出力契約は従来と同一
        self.assertIn("APIはv2で", prompt)                    # 人フィードバックの伝搬

    def test_worker_skill_config_normalization(self):
        args = types.SimpleNamespace(worker_skill="  None ")
        kf._configure_thresholds(args)
        try:
            self.assertIsNone(kf._flow_worker_prompt({"role": "worker", "kind": "work",
                                                      "goal": "g"}))
        finally:
            kf._configure_thresholds(types.SimpleNamespace(
                worker_skill=kf.CONFIG_DEFAULTS["worker_skill"]))


class AgentOverrideTests(unittest.TestCase):
    """役割（planner/evaluator/worker/kind）毎のエージェント上書き（設定 agents:）。"""

    def setUp(self):
        self._cli, self._ov = kf._AGENT_CLI, dict(kf._AGENT_OVERRIDES)
        self._run_ov = dict(kf._EXECUTION_OVERRIDES)

    def tearDown(self):
        kf._AGENT_CLI, kf._AGENT_OVERRIDES = self._cli, self._ov
        kf._EXECUTION_OVERRIDES = self._run_ov

    def test_run_override_beats_saved_node_and_kind_beats_role(self):
        kf._AGENT_CLI = "kiro"
        kf._EXECUTION_OVERRIDES = kf._normalize_execution_overrides({
            "version": 1,
            "roles": {"worker": {"agent_cli": "claude", "model": "sonnet", "tier": "medium"}},
            "kinds": {"verify": {"agent_cli": "codex", "model": "gpt-5", "tier": "large"}},
        })
        self.assertEqual(
            kf._effective_agent("verify", None, {"agent_cli": "ollama", "model": "qwen"}),
            ("codex", "gpt-5"),
        )
        self.assertEqual(kf._effective_agent("work", None, None), ("claude", "sonnet"))
        self.assertEqual(kf._selection_meta("verify", {"agent_cli": "ollama"}), {
            "tier": "large", "selection_source": "run-kind", "selection_reason": "",
            "pinned": True,
        })

    def test_normalize_accepts_roles_and_kinds_only(self):
        raw = {"planner": {"agent_cli": "Claude", "model": "opus"},
               "verify": {"model": "haiku"},
               "worker": {"agent_cli": "copilot"},
               "unknown": {"agent_cli": "x"},       # 未知キーは落とす
               "evaluator": "not-a-dict",           # 不正な値も落とす
               "judge": {}}                          # 空も落とす
        out = kf._normalize_agent_overrides(raw)
        self.assertEqual(set(out), {"planner", "verify", "worker"})
        self.assertEqual(out["planner"], {"agent_cli": "claude", "model": "opus"})
        self.assertEqual(kf._normalize_agent_overrides(None), {})
        self.assertEqual(kf._normalize_agent_overrides("x"), {})

    def test_agent_for_resolution_order(self):
        kf._AGENT_CLI = "kiro"
        kf._AGENT_OVERRIDES = {"planner": {"agent_cli": "claude", "model": "opus"},
                               "worker": {"agent_cli": "copilot"}}
        self.assertEqual(kf._agent_for("planner"), ("claude", "opus"))
        self.assertEqual(kf._agent_for("verify"), ("copilot", None))   # kind → worker へ
        self.assertEqual(kf._agent_for("evaluator"), ("kiro", None))   # 未指定 → グローバル
        self.assertEqual(kf._agent_for(""), ("kiro", None))

    def test_run_agent_uses_purpose_override(self):
        kf._AGENT_CLI = "kiro"
        kf._AGENT_OVERRIDES = {"planner": {"agent_cli": "claude", "model": "opus"}}
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw.get("input")))
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent("プロンプト", "global-model", purpose="planner")
            kf.run_agent("プロンプト", "global-model", purpose="work")
        cmd1, stdin1 = calls[0]
        self.assertEqual(cmd1[0], "claude")                      # 上書き CLI
        self.assertIn("opus", cmd1)                              # 上書き model が勝つ
        self.assertEqual(stdin1, "プロンプト")                   # claude は stdin 渡し
        cmd2, _ = calls[1]
        self.assertEqual(cmd2[0], "kiro-cli")                    # 未指定はグローバル
        self.assertIn("global-model", cmd2)

    def test_readonly_is_declared_per_role(self):
        """権限は役割の性質で決まる。kind は worker へフォールバックし、既定は
        READONLY_ROLES（planner / evaluator）だけ readonly・他は write。"""
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides({
            "planner": {"agent_cli": "claude", "readonly": True},
            "worker": {"readonly": True},
            "work": {"readonly": False},            # kind の明示が worker より優先
            "evaluator": {"readonly": "yes"}})      # bool 以外は落とす
        self.assertTrue(kf._agent_readonly("planner"))
        self.assertTrue(kf._agent_readonly("judge"))     # kind → worker の宣言を継ぐ
        self.assertFalse(kf._agent_readonly("work"))
        # bool 以外は落ちて既定へ。evaluator は「読まない系」なので既定 readonly
        self.assertTrue(kf._agent_readonly("evaluator"))

    def test_readonly_defaults_to_the_role_nature(self):
        """宣言が無いときの既定: planner / evaluator は readonly、実務系は write。

        agent-control が agent_cli をツールループ型（agent-ollama の --tools bash 等）へ
        差し替えても、契約どおりの JSON 応答が「規約から外れています」と蹴られないため。"""
        kf._AGENT_OVERRIDES = {}
        self.assertTrue(kf._agent_readonly("planner"))
        self.assertTrue(kf._agent_readonly("evaluator"))
        self.assertFalse(kf._agent_readonly("worker"))
        self.assertFalse(kf._agent_readonly("work"))
        self.assertFalse(kf._agent_readonly("verify"))
        # 明示すれば既定を覆せる（従来どおり道具付きで計画させたい場合）
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides({"planner": {"readonly": False}})
        self.assertFalse(kf._agent_readonly("planner"))

    def test_readonly_role_drops_the_write_args(self):
        """受け入れ基準: readonly 宣言した役割の argv に write_args が乗らない。"""
        kf._AGENT_CLI = "claude"
        kf._AGENT_OVERRIDES = {"planner": {"readonly": True}}
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.run_agent("プロンプト", None, purpose="planner")
            kf.run_agent("プロンプト", None, purpose="work")
        self.assertNotIn("--dangerously-skip-permissions", calls[0])
        self.assertIn("--dangerously-skip-permissions", calls[1])

    def test_node_readonly_overrides_the_write_role_and_uses_reference_cwd(self):
        repo = tempfile.mkdtemp(prefix="agent-flow-readonly-reference-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        with mock.patch.object(kf, "run_agent", return_value="設計書") as run:
            kf.execute_agent("work", "設計する", {}, None,
                             references=[{"url": "https://example.invalid/repo.git",
                                          "local": repo}], readonly=True)
        self.assertEqual(run.call_args.kwargs["cwd"], repo)
        self.assertIs(run.call_args.kwargs["readonly"], True)


class TestAgentPluginAndTriage(unittest.TestCase):
    """エージェント CLI プラグイン（agents/<name>.json）と失敗トリアージ。
    環境要因（quota/auth/env）の失敗はどのノードをリトライしても同じ理由で落ちるため、
    run を即座に打ち切って人に環境を直させる（完了済みノードは温存＝再開で続きから）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-agents-")
        self._old = os.environ.get("KIRO_AGENTS_DIR")
        os.environ["KIRO_AGENTS_DIR"] = self.tmp
        kf._AGENT_PLUGIN_CACHE.clear()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KIRO_AGENTS_DIR", None)
        else:
            os.environ["KIRO_AGENTS_DIR"] = self._old
        kf._AGENT_PLUGIN_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, spec):
        with open(os.path.join(self.tmp, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f)

    def test_plugin_command_built_and_invoked(self):
        self._write("myllm", {"command": ["my-cli", "run", "{model}"],
                              "default_model": "base-7b"})
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw.get("input")))
            return types.SimpleNamespace(returncode=0, stdout="応答です", stderr="")
        with mock.patch.object(kf.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(kf, "_AGENT_CLI", "myllm"):
            out = kf.run_agent("こんにちは", None)
        self.assertEqual(out, "応答です")
        cmd, stdin_text = calls[0]
        self.assertEqual(cmd, ["my-cli", "run", "base-7b"])
        self.assertEqual(stdin_text, "こんにちは")

    def test_unknown_agent_cli_is_explicit_error(self):
        with mock.patch.object(kf, "_AGENT_CLI", "nosuchcli"):
            with self.assertRaises(RuntimeError) as cm:
                kf.run_agent("x", None)
        self.assertIn("agents/nosuchcli.json", str(cm.exception))

    def test_agent_failure_carries_triage_tag(self):
        msg = kf._agent_failure("codex", 1, "", "usage limit reached")
        self.assertTrue(msg.startswith("[agent-error:quota]"), msg)
        self.assertIsNone(kf.classify_agent_failure("テストが 3 件落ちた"))

    def test_env_failure_fails_run_fast_instead_of_replanning(self):
        # 認証切れタグ付きの失敗ノードが 1 つでもあれば、再計画（リトライ生成）せず打ち切る
        nodes = {"t1": {"goal": "a", "deps": [], "kind": "work"},
                 "t2": {"goal": "b", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"},
                   "t2": {"status": "failed",
                          "output": "実行エラー: [agent-error:auth] kiro-cli 失敗 (rc=0): 認証切れ"}}
        args = types.SimpleNamespace(executor="stub", max_fanout=50, review=False,
                                     exemplar_first=False, max_retries=3)
        bus = types.SimpleNamespace(meta_path="/nonexistent/meta.json")
        decision, new_tasks, reason = kf._continue(args, bus, "req", nodes, results, 0)
        self.assertEqual(decision, "failed")
        self.assertEqual(new_tasks, [])
        self.assertIn("[agent-error:auth]", reason)
        self.assertIn("温存", reason)                  # 完了済みは捨てない、と言い切る

    def test_env_failure_preserves_control_and_budget_classes(self):
        """管理停止と利用上限を別クラスのまま run まで運ぶ。"""
        nodes = {"t1": {"goal": "a", "deps": [], "kind": "work"}}
        args = types.SimpleNamespace(executor="stub", max_fanout=50, review=False,
                                     exemplar_first=False, max_retries=3)
        for source, error_class in (("agent-control", "control"), ("node-budget", "quota")):
            with self.subTest(source=source, error_class=error_class):
                results = {
                    "t1": {
                        "status": "failed",
                        "output": (f"実行エラー: [agent-error:{error_class}] [{source}] "
                                   "管理面により実行できません"),
                    },
                }
                bus = types.SimpleNamespace(meta_path="/nonexistent/meta.json")
                decision, _, reason = kf._continue(args, bus, "req", nodes, results, 0)
                self.assertEqual(decision, "failed")
                self.assertIn(f"[agent-error:{error_class}]", reason)
                self.assertIn(f"[{source}]", reason)
                self.assertNotIn("プラン・クレジット", reason)

    def test_content_failure_still_replans(self):
        # タグ無し（内容の問題）は従来どおり retry タスクを生成する
        nodes = {"t1": {"goal": "a", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "failed", "output": "実行エラー: テストが落ちた"}}
        args = types.SimpleNamespace(executor="stub", max_fanout=50, review=False,
                                     exemplar_first=False, max_retries=3)
        bus = types.SimpleNamespace(meta_path="/nonexistent/meta.json")
        decision, new_tasks, _ = kf._continue(args, bus, "req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual(len(new_tasks), 1)


class FormatRepairTests(unittest.TestCase):
    """レイヤ2（形式修復リトライ）: 出力契約違反（JSON 崩れ・配列でない）を
    「前回の出力はこう契約違反だった」の指摘付き再呼び出しで 1 回修復する。"""

    def test_split_repaired_to_list(self):
        outs = ["リストにできませんでした。ごめんなさい。", '["a-m", "n-z"]']
        with mock.patch.object(kf, "run_agent", side_effect=outs), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            text, data = kf.execute_agent("split", "五十音を2分割", {}, None)
        self.assertEqual(data, ["a-m", "n-z"])           # 修復で fan-out 可能になる

    def test_split_repair_prompt_carries_violation(self):
        prompts = []
        def capture(prompt, model, purpose="", **_kw):
            prompts.append(prompt)
            return "だめでした" if len(prompts) == 1 else '["x"]'
        with mock.patch.object(kf, "run_agent", side_effect=capture), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            kf.execute_agent("split", "分割", {}, None)
        self.assertEqual(len(prompts), 2)
        self.assertIn("契約違反", prompts[1])            # 違反の指摘が修復プロンプトに載る
        self.assertIn("だめでした", prompts[1])          # 前回出力も見せる

    def test_split_unrepairable_falls_back_to_none(self):
        with mock.patch.object(kf, "run_agent", return_value="常に散文"), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            text, data = kf.execute_agent("split", "分割", {}, None)
        self.assertIsNone(data)                          # 従来のフォールバック（評価役が判断）

    def test_split_accepts_json_mode_wrapper_without_repair(self):
        """ollama の JSON モードは配列を必ずオブジェクトで包む。器を剥がして受け、
        原理的に空振りする修復リトライを焼かない（C9・C10）。"""
        calls = []
        def once(prompt, model, purpose="", **_kw):
            calls.append(1)
            return '{"data": ["a", "b"]}'
        with mock.patch.object(kf, "run_agent", side_effect=once), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            text, data = kf.execute_agent("split", "分割", {}, None)
        self.assertEqual(data, ["a", "b"])
        self.assertEqual(len(calls), 1)                  # 修復リトライを呼ばない

    def test_split_accepts_a_sequence_of_string_arrays_without_repair(self):
        """Thinking出力の外側配列だけが欠けても、4グループの意味を決定的に保つ。"""
        calls = []

        def once(prompt, model, purpose="", **_kw):
            calls.append(1)
            return '["a.py", "b.py"], ["c.py", "d.py"]'

        with mock.patch.object(kf, "run_agent", side_effect=once), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            _text, data = kf.execute_agent("split", "4ファイルを2分割", {}, None)
        self.assertEqual(data, ["a.py,b.py", "c.py,d.py"])
        self.assertEqual(len(calls), 1)

    def test_split_does_not_coerce_mixed_nested_values(self):
        """文字列グループ以外は意味が決まらないため、決め打ちせず従来の修復へ回す。"""
        outs = ['[["a.py", 1], ["b.py"]]', '["a.py", "b.py"]']
        with mock.patch.object(kf, "run_agent", side_effect=outs), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            _text, data = kf.execute_agent("split", "分割", {}, None)
        self.assertEqual(data, ["a.py", "b.py"])

    def test_split_wrapper_with_two_lists_is_not_unwrapped(self):
        """配列が 2 本ある器はどれが答えか決まらない。剥がさず修復へ回す。"""
        outs = ['{"items": ["a"], "rejected": ["b"]}', '["a"]']
        with mock.patch.object(kf, "run_agent", side_effect=outs), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            text, data = kf.execute_agent("split", "分割", {}, None)
        self.assertEqual(data, ["a"])                    # 修復側の配列が採用される

    def test_format_retries_zero_disables_repair(self):
        calls = []
        def count(prompt, model, purpose="", **_kw):
            calls.append(1)
            return "散文"
        with mock.patch.object(kf, "run_agent", side_effect=count), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 0):
            kf.execute_agent("split", "分割", {}, None)
        self.assertEqual(len(calls), 1)

    def test_evaluator_json_repaired(self):
        outs = ["判定: 完了です", '{"decision":"done","reason":"ok","new_tasks":[]}']
        nodes = {"t1": {"goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", side_effect=outs), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            decision, new, reason = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(reason, "ok")                   # 修復後の JSON が採用される

    def test_evaluator_transient_fails_run_with_tag(self):
        # 評価役の呼び出し自体が transient 失敗 → fallback（内容推定）でなくタグ付き failed 終端
        # ＝ auto-heal / 環境復旧が拾う。
        def boom(prompt, model, purpose="", **_kw):
            raise RuntimeError("[agent-error:transient] ETIMEDOUT（3 回試行後）")
        nodes = {"t1": {"goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}   # 全 done でも failed に倒す
        with mock.patch.object(kf, "run_agent", side_effect=boom):
            decision, new, reason = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "failed")
        self.assertIn("[agent-error:transient]", reason)

    def test_planner_json_repaired(self):
        outs = ["こんなグラフはどうでしょう",
                '{"patterns":["fan-out-and-synthesize"],"parallelism":2,'
                '"tasks":[{"id":"t1","goal":"g","deps":[],"kind":"work"}]}']
        with mock.patch.object(kf, "run_agent", side_effect=outs), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            strategy, tasks = kf.plan_strategy_agent("req", None)
        self.assertEqual(strategy["patterns"], ["fan-out-and-synthesize"])
        self.assertEqual([t["id"] for t in tasks], ["t1"])

    def test_planner_unrepairable_falls_back_to_stub(self):
        with mock.patch.object(kf, "run_agent", return_value="散文"), \
                mock.patch.object(kf, "_FORMAT_RETRIES", 1):
            strategy, tasks = kf.plan_strategy_agent("req", None)
        self.assertTrue(strategy["patterns"])            # stub 戦略に倒れて run は続行
        self.assertTrue(tasks)


class AgentControlTests(unittest.TestCase):
    """agent-control 契約（control.json 上書き・lifecycle・status ハートビート）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kf-control-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        _prev_control = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        # pop すると**モジュール既定の隔離先ごと消え**、以降のテストが開発者の実
        # `~/.agents/control` を読む（テスト順で agent_cli 系が落ちる原因だった）。
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", _prev_control)
        os.environ["AGENT_BUDGET_DIR"] = self.dir       # 予算は無設定（None）
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        kf._CONTROL_CACHE["mtime"] = None               # mtime キャッシュを毎テストで無効化

    def _control(self, ctl):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump(ctl, f)
        kf._CONTROL_CACHE["mtime"] = None

    def test_override_resolution_order(self):
        # agents[purpose] > workload > defaults
        self._control({"version": 1, "defaults": {"model": "sonnet"},
                       "workloads": {"flow": {"model": "opus",
                                              "agents": {"planner": {"agent_cli": "cursor"}}}}})
        self.assertEqual(kf._control_override("planner"), ("cursor", "opus"))
        self.assertEqual(kf._control_override("worker"), (None, "opus"))

    def test_control_overrides_agent_for(self):
        self._control({"version": 1,
                       "workloads": {"flow": {"agents": {"verify": {"model": "opus"}}}}})
        cli, model = kf._agent_for("verify")
        self.assertEqual(model, "opus")                 # control が最優先（kiro は変種を持たない）

    def test_variant_default_model_overrides_a_tier_selected_model(self):
        # tier/agent-control（dashboard の自動割り当て）が選んだ agent_cli + model は
        # 「その CLI を用途を問わずそのまま使う」という明示ではない——用途が variant を
        # 要求すれば、そちらの調整済みモデルへ寄せる（agents: の明示設定とは異なる層）。
        self._control({"version": 1,
                       "workloads": {"flow": {"agents": {
                           "verify": {"agent_cli": "ollama", "model": "qwen3:8b"}}}}})
        self.assertEqual(kf._agent_for("verify"), ("ollama-verify", "gemma4:12b"))

    def test_lifecycle_pause_blocks_run_agent(self):
        self._control({"version": 1, "workloads": {"flow": {"lifecycle": "pause"}}})
        with self.assertRaises(RuntimeError) as ctx:
            kf.run_agent("x", None, purpose="worker")
        self.assertIn("[agent-control]", str(ctx.exception))
        self.assertIn("[agent-error:control]", str(ctx.exception))
        self.assertEqual(kf.classify_agent_failure(str(ctx.exception))[0], "control")

    # -- 候補ベース（version 2 selection_policy → Resolver。設計 2026-08-15 §6.6） ----

    _POLICY = {
        "strategy": "economy", "retry_limit": 1, "no_candidate": "park",
        "qualification_revision": 12,
        "candidates": [
            {"agent_cli": "ollama", "model": "gemma4:e4b", "rank": 1,
             "qualification_refs": ["ollama-gemma4-e4b-extract-v1"]},
            {"agent_cli": "kiro", "model": "sonnet", "rank": 2},
        ],
    }

    def _policy_control(self, **overrides):
        ctl = {"version": 2, "revision": 42,
               "workloads": {"flow": {"agent_cli": "cursor", "model": "legacy-model",
                                      "selection_policy": json.loads(json.dumps(self._POLICY))}}}
        ctl.update(overrides)
        return ctl

    def test_selection_policy_replaces_legacy_override(self):
        # workload 直下（legacy dual-write）は cursor/legacy-model だが、policy がある限り
        # 再解釈しない——Resolver の rank1 が実効になる。
        self._control(self._policy_control())
        cli, model = kf._agent_for("work")
        self.assertEqual((cli, model), ("ollama", "gemma4:e4b"))

    def test_selection_policy_keeps_json_variant_swap(self):
        # 候補ベースで決めた CLI にも JSON 契約役の変種振替（同エンジンの起動形）は効く。
        self._control(self._policy_control())
        self.assertEqual(kf._agent_for("judge")[0], "ollama-json")

    def test_expired_policy_parks_run_agent(self):
        ctl = self._policy_control(valid_until="2000-01-01T00:00:00Z")
        self._control(ctl)
        with self.assertRaises(RuntimeError) as ctx:
            kf.run_agent("x", None, purpose="work")
        message = str(ctx.exception)
        self.assertIn("[agent-error:control]", message)
        self.assertIn("park", message)
        self.assertEqual(kf.classify_agent_failure(message)[0], "control")

    def test_broken_policy_parks_instead_of_legacy(self):
        ctl = self._policy_control()
        ctl["workloads"]["flow"]["selection_policy"]["candidates"] = []
        self._control(ctl)
        with self.assertRaises(RuntimeError) as ctx:
            kf.run_agent("x", None, purpose="work")
        self.assertIn("invalid-selection-policy", str(ctx.exception))

    def test_percall_pin_skips_park_guard(self):
        # 呼び出し 1 回の明示指定（検証計画の「これで確かめてくれ」）は人の承認済み指定。
        # policy が park でも止めない（弱い候補への黙った降格ではなく明示指定の実行）。
        self._control(self._policy_control(valid_until="2000-01-01T00:00:00Z"))
        with mock.patch.object(kf, "_run_agent_once", return_value="ok"):
            out = kf.run_agent("x", None, purpose="verify",
                               agent={"agent_cli": "kiro", "model": "sonnet"})
        self.assertEqual(out, "ok")

    def test_selection_meta_carries_execution_decision(self):
        self._control(self._policy_control())
        meta = kf._selection_meta("work")
        self.assertEqual(meta["selection_source"], "qualified-candidate")
        block = meta["execution_decision"]
        self.assertEqual(block["agent_cli"], "ollama")
        self.assertEqual(block["control_revision"], 42)
        self.assertEqual(block["qualification_revision"], 12)
        self.assertEqual(block["qualification_id"], "ollama-gemma4-e4b-extract-v1")
        # receipt v2 の共通ブロックとして形が合う（1 実装の検証で確認）
        from agentcore.executioncontract import execution_receipt_errors
        self.assertEqual(execution_receipt_errors(
            {"attempt_id": "n1:ollama-gemma4-e4b:1", "execution_decision": block}), [])

    def test_selection_meta_pin_stays_legacy_labels(self):
        self._control(self._policy_control())
        meta = kf._selection_meta("verify", {"agent_cli": "ollama"})
        self.assertEqual(meta["selection_source"], "pinned-agent")
        self.assertNotIn("execution_decision", meta)

    # -- Execution Envelope（agent-project の承認済み snapshot → 明示固定。E2↔U3 結合） --
    # fixture の形の正典は agent-project/agent_project/envelope.py build_execution_envelope。

    def _envelope(self, perms, approved=True):
        kf._set_execution_envelope({"execution_envelope": {
            "version": 1, "task_id": "t-1",
            "approval": {"status": "approved" if approved else "proposed",
                         "actor": "human", "reason": "test"},
            "candidate_permissions": perms}})
        self.addCleanup(kf._set_execution_envelope, None)

    def test_envelope_pin_selects_policy_candidate(self):
        self._control(self._policy_control())
        self._envelope({"pins": [{"agent_cli": "kiro", "model": "sonnet"}]})
        self.assertEqual(kf._agent_for("work"), ("kiro", "sonnet"))  # rank2 を明示固定
        meta = kf._selection_meta("work")
        self.assertEqual(meta["selection_source"], "explicit-pin")
        self.assertTrue(meta["pinned"])
        self.assertEqual(meta["execution_decision"]["selection_source"], "explicit-pin")

    def test_envelope_trial_entry_runs_policy_trial_candidate(self):
        # trial 裏付けのみの候補（Compiler が status: trial を明記）は通常 run で選ばれず、
        # Envelope の trials に載った run でだけ走る——E5 昇格ループの入口。
        ctl = self._policy_control()
        ctl["workloads"]["flow"]["selection_policy"]["candidates"].append(
            {"agent_cli": "ollama-verify", "model": "gemma4:12b", "rank": 3,
             "status": "trial", "qualification_refs": ["ollama-12b-review-trial"]})
        self._control(ctl)
        normal = kf._agent_for("work")
        self.assertEqual(normal[0], "ollama")            # 通常 run は rank1 のまま
        self._envelope({"trials": [{"agent_cli": "ollama-verify", "model": "gemma4:12b"}]})
        self.assertEqual(kf._agent_for("work"), ("ollama-verify", "gemma4:12b"))
        meta = kf._selection_meta("work")
        self.assertEqual(meta["selection_source"], "trial-candidate")
        self.assertTrue(meta["pinned"])

    def test_unapproved_envelope_is_ignored(self):
        self._control(self._policy_control())
        self._envelope({"pins": [{"agent_cli": "kiro", "model": "sonnet"}]}, approved=False)
        decision = kf._control_policy_decision("work")
        self.assertEqual(decision["selection_source"], "qualified-candidate")

    def test_envelope_pin_outside_policy_parks_run_agent(self):
        self._control(self._policy_control())
        self._envelope({"pins": [{"agent_cli": "codex", "model": "gpt-6"}]})
        with self.assertRaises(RuntimeError) as ctx:
            kf.run_agent("x", None, purpose="work")
        self.assertIn("pin-not-qualified", str(ctx.exception))

    def test_envelope_tier_ceiling_override(self):
        ctl = self._policy_control()
        ctl["workloads"]["flow"]["tier"] = "medium"
        self._control(ctl)
        pin = {"agent_cli": "codex", "model": "gpt-6", "tier": "large"}
        self._envelope({"pins": [pin], "trials": [pin]})
        blocked = kf._control_policy_decision("work")
        self.assertEqual(blocked["park_reason"], "pin-exceeds-tier")
        self._envelope({"pins": [pin], "trials": [pin], "tier_ceiling_override": "large"})
        allowed = kf._control_policy_decision("work")
        self.assertEqual(allowed["selected"], {"agent_cli": "codex", "model": "gpt-6"})
        self.assertEqual(allowed["selection_source"], "trial-candidate")

    def test_envelope_purpose_scope(self):
        self._control(self._policy_control())
        self._envelope({"pins": [{"agent_cli": "kiro", "model": "sonnet", "purpose": "verify"}]})
        self.assertEqual(kf._agent_for("work")[0], "ollama")    # 対象外ロールには効かない
        self.assertEqual(kf._agent_for("verify"), ("kiro", "sonnet"))

    def test_status_heartbeat_written(self):
        self._control({"version": 1, "revision": 7, "workloads": {"flow": {}}})
        with mock.patch.object(kf, "_run_agent_once", return_value="ok"):
            kf.run_agent("x", None, purpose="worker")
        status_dir = os.path.join(self.dir, "status")
        files = [n for n in os.listdir(status_dir) if n.endswith(".json")]
        self.assertTrue(files)
        with open(os.path.join(status_dir, files[0]), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["tool"], "agent-flow")
        self.assertEqual(rec["workload"], "flow")
        self.assertEqual(rec["revision_applied"], 7)
        self.assertEqual(rec["lifecycle"], "run")

    # -- 同時実行数（workloads.flow.concurrency） -------------------------------------
    # 「この PC で同時にどれだけ走らせてよいか」を管理面（dashboard の全体設定）から
    # 1 か所で宣言する。宣言が無ければ従来どおり CLI 引数 → 設定ファイル → 既定。

    def test_concurrency_absent_keeps_caller_value(self):
        self._control({"version": 1, "workloads": {"flow": {}}})
        self.assertEqual(kf.control_max_runs(8), 8)
        self.assertEqual(kf.control_workers(2), 2)

    def test_concurrency_overrides_caller_value(self):
        self._control({"version": 1, "workloads":
                       {"flow": {"concurrency": {"max_runs": 2, "workers": 1}}}})
        self.assertEqual(kf.control_max_runs(8), 2)
        self.assertEqual(kf.control_workers(2), 1)

    def test_concurrency_zero_max_runs_is_unlimited(self):
        # 0 は「無制限」（agent-flow 設定と同じ語彙）。既定へ戻したいならキーごと消す。
        self._control({"version": 1, "workloads": {"flow": {"concurrency": {"max_runs": 0}}}})
        self.assertEqual(kf.control_max_runs(8), 0)

    def test_broken_concurrency_is_ignored(self):
        # 負数・数値でない・workers=0（ワーカー無し）は宣言なし扱い。GUI の入力ミスで
        # run が誰にも進められなくなる方が、上書きが効かないより高くつく。
        for bad in ({"max_runs": -1, "workers": 0}, {"max_runs": "2", "workers": True},
                    ["max_runs"], None):
            self._control({"version": 1, "workloads": {"flow": {"concurrency": bad}}})
            self.assertEqual(kf.control_max_runs(8), 8)
            self.assertEqual(kf.control_workers(2), 2)

    def test_other_workload_concurrency_is_not_read(self):
        self._control({"version": 1, "workloads":
                       {"amigos": {"concurrency": {"max_runs": 1, "workers": 1}}}})
        self.assertEqual(kf.control_max_runs(8), 8)
        self.assertEqual(kf.control_workers(2), 2)


class GlobalInstructionsTests(unittest.TestCase):
    """グローバル指示（agent-instructions 契約）: 描画・meta スナップショット・ワーカー注入・status。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kf-instr-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.instr = tempfile.mkdtemp(prefix="kf-instr-home-")
        self.addCleanup(shutil.rmtree, self.instr, ignore_errors=True)
        os.environ["AGENT_INSTRUCTIONS_DIR"] = self.instr
        self.addCleanup(os.environ.pop, "AGENT_INSTRUCTIONS_DIR", None)
        kf._INSTRUCTIONS_REV_APPLIED = None
        self.addCleanup(setattr, kf, "_INSTRUCTIONS_REV_APPLIED", None)

    def _write_instructions(self, obj):
        with open(os.path.join(self.instr, "instructions.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_render_matches_canonical_format(self):
        block = kf.render_instructions_block({
            "revision": 5, "enabled": True, "text": "回答は日本語。",
            "skills": ["karpathy-guidelines", {"name": "self-checking", "note": "提出前に自己評価"}],
            "tools": {"allow": ["fs_read"], "deny_note": "push は人の確認"}, "max_chars": 2000,
        })
        self.assertTrue(block.startswith("<!-- agent-instructions rev:5 -->\n"))
        self.assertIn("## 共通指示（agent-dashboard 管理・全ノード共通）", block)
        self.assertIn("- self-checking — 提出前に自己評価", block)
        self.assertIn("ツール（許可）: fs_read", block)
        self.assertEqual(kf.render_instructions_block({"enabled": False, "text": "x", "revision": 1}), "")
        self.assertEqual(kf.render_instructions_block(None), "")

    def test_render_truncates_but_keeps_marker(self):
        block = kf.render_instructions_block({"revision": 3, "enabled": True, "text": "あ" * 500}, 80)
        self.assertLessEqual(len(block), 80)
        self.assertTrue(block.startswith("<!-- agent-instructions rev:3 -->"))
        self.assertTrue(block.endswith("…"))

    def test_prepend_dedups_on_marker(self):
        block = "<!-- agent-instructions rev:1 -->\nX"
        merged = kf.prepend_instructions("本文", block)
        self.assertTrue(merged.startswith(block))
        self.assertIn("本文", merged)
        self.assertEqual(kf.prepend_instructions(merged, block), merged)  # 二重注入しない

    def test_snapshot_writes_meta_and_propagates(self):
        self._write_instructions({"version": 1, "revision": 2, "enabled": True, "text": "共通指示X"})
        bus = kf.Bus(self.dir, "run-gi")
        bus.ensure_run("元要求")
        self.assertTrue(bus.snapshot_instructions())
        meta = bus.run_meta("run-gi")
        self.assertEqual(meta["instructions"]["revision"], 2)
        self.assertIn("共通指示X", meta["instructions"]["text"])
        self.assertTrue(meta["instructions"]["text"].startswith("<!-- agent-instructions rev:2 -->"))
        # 冪等: 既にスナップショット済みなら再書き込みしない
        self.assertFalse(bus.snapshot_instructions())

    def test_snapshot_skips_when_disabled_or_empty(self):
        self._write_instructions({"version": 1, "revision": 1, "enabled": False, "text": "x"})
        bus = kf.Bus(self.dir, "run-off")
        bus.ensure_run("req")
        self.assertFalse(bus.snapshot_instructions())
        self.assertNotIn("instructions", bus.run_meta("run-off"))

    def test_snapshot_skips_when_request_has_marker(self):
        self._write_instructions({"version": 1, "revision": 1, "enabled": True, "text": "x"})
        bus = kf.Bus(self.dir, "run-mk")
        bus.ensure_run("<!-- agent-instructions rev:9 --> 既に注入済みの要求")
        self.assertFalse(bus.snapshot_instructions())

    def test_execute_agent_injects_block_via_builtin_prompt(self):
        block = "<!-- agent-instructions rev:4 -->\n## 共通指示（agent-dashboard 管理・全ノード共通）\n回答は日本語。"
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "ok"

        # flow-worker スキルを無効化して組み込み fallback プロンプトを通す
        with mock.patch.object(kf, "_flow_worker_prompt", return_value=None), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "タスクG", {}, None, instructions=block)
        self.assertTrue(captured["prompt"].startswith(block))
        self.assertIn("タスクG", captured["prompt"])

    def test_execute_agent_no_double_inject_when_prompt_has_marker(self):
        block = "<!-- agent-instructions rev:4 -->\n共通指示"
        # スキルが既にブロックを前置したプロンプトを返す → 外側は二重注入しない
        skill_prompt = block + "\n\nタスク本文"
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "ok"

        with mock.patch.object(kf, "_flow_worker_prompt", return_value=skill_prompt), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "g", {}, None, instructions=block)
        self.assertEqual(captured["prompt"].count("agent-instructions"), 1)

    def test_status_carries_instructions_revision_applied(self):
        _prev_control = os.environ["AGENT_CONTROL_DIR"]
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        # pop すると**モジュール既定の隔離先ごと消え**、以降のテストが開発者の実
        # `~/.agents/control` を読む（テスト順で agent_cli 系が落ちる原因だった）。
        self.addCleanup(os.environ.__setitem__, "AGENT_CONTROL_DIR", _prev_control)
        kf._CONTROL_CACHE["mtime"] = None
        kf._note_instructions_applied(3)
        with mock.patch.object(kf, "_run_agent_once", return_value="ok"):
            kf.run_agent("x", None, purpose="worker")
        status_dir = os.path.join(self.dir, "status")
        files = [n for n in os.listdir(status_dir) if n.endswith(".json")]
        with open(os.path.join(status_dir, files[0]), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["instructions_revision_applied"], 3)
