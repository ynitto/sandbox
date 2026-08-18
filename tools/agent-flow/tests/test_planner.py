"""agent-flow の単体テスト — planner（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class PlannerTests(unittest.TestCase):
    def test_plan_changes_uses_one_contract_for_add_and_replace(self):
        changes = kf._plan_changes([
            {"id": "new", "replaces": "old"},
            {"id": "extra"},
        ])
        self.assertEqual(changes, {
            "added": ["new", "extra"],
            "replaced": [{"old": "old", "next": "new"}],
            "updated": [],
            "removed": ["old"],
        })

    def test_parallel_split(self):
        tasks = kf.plan_stub("a; b; c")
        self.assertEqual([t["id"] for t in tasks], ["t1", "t2", "t3"])
        self.assertTrue(all(t["deps"] == [] for t in tasks))

    def test_sequential_chain_deps(self):
        tasks = kf.plan_stub("setup -> build -> test; docs")
        by_id = {t["id"]: t for t in tasks}
        self.assertEqual(by_id["t1"]["deps"], [])         # setup
        self.assertEqual(by_id["t2"]["deps"], ["t1"])     # build after setup
        self.assertEqual(by_id["t3"]["deps"], ["t2"])     # test after build
        self.assertEqual(by_id["t4"]["deps"], [])         # docs independent

    def test_simple_newline_list_still_splits(self):
        # 空行の無いフラットなリストは従来どおり改行を区切りとして扱う
        tasks = kf.plan_stub("task1\ntask2\ntask3")
        self.assertEqual([t["goal"] for t in tasks], ["task1", "task2", "task3"])

    # 回帰: 構造化された複数行の要求（charter 文脈＝対象リポジトリ一覧つき）を、行ごとの
    # 細切れタスクへ分割しないこと。さもないと 1 行 1 行が別イシューになり、gitlab の
    # タイトル/本文が repos 行で埋まる（報告された不具合）。
    _STRUCTURED_REQ = (
        "ログイン画面のバグを修正する\n\n"
        "完了条件: pytest\n\n"
        "対象リポジトリ:\n"
        "- web = https://gitlab.com/acme/web（base=main）\n"
        "    説明: フロントエンド\n"
        "- api = https://gitlab.com/acme/api（base=main）\n"
        "制約:\n- 既存テストを壊さない\n"
    )

    def test_structured_request_not_shredded_per_line(self):
        tasks = kf.plan_stub(self._STRUCTURED_REQ)
        # repos 行や charter 見出しが個別タスクの goal になっていないこと
        for t in tasks:
            self.assertNotIn("gitlab.com", t["goal"])
            self.assertNotIn("対象リポジトリ", t["goal"])
            self.assertNotIn("制約:", t["goal"])
        # 見出しは本来の目的（先頭行）から始まる
        self.assertTrue(all(t["goal"].startswith("ログイン画面のバグを修正する") for t in tasks))

    def test_structured_request_semicolons_and_arrows_not_split(self):
        # 構造化要求の本文には ';'（verify コマンド）や '->'（誘導・レビュー記述の文中）が
        # 普通に混ざる。区切りのミニ言語はフラット要求専用で、構造化要求では解釈しない。
        req = (
            "リファクタする\n\n"
            "完了条件: 次のシェルコマンドが終了コード 0 で成功すること:\n"
            "  cd app; pytest -q\n\n"
            "やらないこと（スコープ外）:\n  設定 -> 環境変数の整理\n"
        )
        tasks = kf.plan_stub(req)
        for t in tasks:
            self.assertNotIn("pytest", t["goal"])   # verify の断片が別タスクにならない
            self.assertNotIn("環境変数", t["goal"])  # '->' を依存チェーンとして解釈しない
            self.assertEqual(t["deps"], [])
        self.assertTrue(all(t["goal"].startswith("リファクタする") for t in tasks))

    def test_structured_request_strategy_goals_have_no_repos(self):
        # plan_stub を使う既定パターン（fan-out-and-synthesize）でも repos が goal に出ない
        strat, tasks = kf.plan_strategy_stub(self._STRUCTURED_REQ)
        for t in tasks:
            self.assertNotIn("gitlab.com", t["goal"])
            self.assertNotIn("対象リポジトリ", t["goal"])
        # タイトル相当（先頭行）が本来の目的であること
        heads = [t["goal"] for t in tasks if t["kind"] in ("work", "generate", "synthesize")]
        self.assertTrue(any("ログイン画面のバグを修正する" in g for g in heads))

    def test_first_line_helper(self):
        self.assertEqual(kf._first_line("\n\n  目的の行  \n詳細\n"), "目的の行")
        self.assertEqual(kf._first_line("x" * 60), "x" * 48)   # limit で切る


class DataDrivenFanoutTests(unittest.TestCase):
    def test_split_executor_returns_list(self):
        text, data = kf.execute_stub("split", "5 件に分解", {}, None)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)

    def test_split_expands_to_map_and_reduce(self):
        nodes = {"split1": {"goal": "分解", "deps": [], "kind": "split"}}
        results = {"split1": {"status": "done", "data": ["x", "y", "z"]}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=50)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new],
                         ["split1-m1", "split1-m2", "split1-m3", "split1-reduce"])
        red = next(t for t in new if t["kind"] == "reduce")
        self.assertEqual(red["deps"], ["split1-m1", "split1-m2", "split1-m3"])

    def test_exemplar_first_stages_pilot_then_rest(self):
        # Stage 1: split 完了直後は pilot map 1件＋検証ゲートだけ（残りは出さない）
        nodes = {"s": {"goal": "各件を移行", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b", "c"]}}
        _, new, _ = kf.continue_stub("各件を移行", nodes, results, 0, exemplar_first=True)
        ids = [t["id"] for t in new]
        self.assertEqual(ids, ["s-m1", "s-pilot"])               # 先行1件＋ゲートのみ
        self.assertEqual(next(t for t in new if t["id"] == "s-pilot")["kind"], "verify")
        self.assertNotIn("s-reduce", ids)

        # pilot ゲート未了の間は残りを展開しない
        nodes.update({"s-m1": {"goal": "", "deps": [], "kind": "map"},
                      "s-pilot": {"goal": "", "deps": ["s-m1"], "kind": "verify"}})
        results.update({"s-m1": {"status": "done"}, "s-pilot": {"status": "running"}})
        _, new2, _ = kf.continue_stub("各件を移行", nodes, results, 1, exemplar_first=True)
        self.assertEqual([t for t in new2 if t["id"].startswith("s-")], [])

        # Stage 2: pilot ゲート done → 残り map（pilot＋ゲートに依存）＋ reduce を展開
        results["s-pilot"] = {"status": "done"}
        _, new3, _ = kf.continue_stub("各件を移行", nodes, results, 2, exemplar_first=True)
        ids3 = [t["id"] for t in new3]
        self.assertEqual(ids3, ["s-m2", "s-m3", "s-reduce"])
        self.assertEqual(next(t for t in new3 if t["id"] == "s-m2")["deps"], ["s-m1", "s-pilot"])
        self.assertEqual(set(next(t for t in new3 if t["id"] == "s-reduce")["deps"]),
                         {"s-m1", "s-m2", "s-m3"})

    def test_default_fanout_unchanged_without_exemplar_first(self):
        # exemplar_first 無し（既定）は従来どおり一括 fan-out
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        _, new, _ = kf.continue_stub("g", nodes, results, 0)
        self.assertEqual([t["id"] for t in new], ["s-m1", "s-m2", "s-reduce"])

    def test_fanout_respects_max(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": list(range(100))}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, max_fanout=5)
        self.assertEqual(len([t for t in new if t["kind"] == "map"]), 5)

    def test_fanout_clamp_is_visible(self):
        # クランプで黙って要素を捨てない: replan 理由・ログ・reduce goal に切り捨てを残す
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": list(range(100))}}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _, new, reason = kf.continue_stub("req", nodes, results, 0, max_fanout=5)
        self.assertIn("fan-out クランプ", reason)
        self.assertIn("s: 100 件中 5 件のみ", reason)
        self.assertIn("fan-out クランプ", buf.getvalue())      # ログにも出る
        red = next(t for t in new if t["kind"] == "reduce")
        self.assertIn("元 100 件のうち先頭 5 件のみ", red["goal"])  # 集約結果を全件と読ませない
        self.assertIn("残り 95 件", red["goal"])

    def test_fanout_clamp_reason_in_continue_agent(self):
        # LLM 継続経路（continue_agent）でも fan-out は先に機械展開され、同じ注記が理由へ載る
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": list(range(100))}}
        with contextlib.redirect_stdout(io.StringIO()):
            decision, new, reason = kf.continue_agent("req", nodes, results, 0, max_fanout=5)
        self.assertEqual(decision, "replan")
        self.assertIn("data-driven fan-out: +6", reason)
        self.assertIn("s: 100 件中 5 件のみ", reason)

    def test_fanout_without_clamp_keeps_wording_and_ids(self):
        # 切り捨てが無いときは従来と完全に同じ文言・同じノード id（回帰確認）
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        _, new, reason = kf.continue_stub("req", nodes, results, 0, max_fanout=50)
        self.assertEqual(reason, "3 件追加")
        self.assertEqual([t["id"] for t in new], ["s-m1", "s-m2", "s-reduce"])
        self.assertNotIn("クランプ", next(t for t in new if t["kind"] == "reduce")["goal"])
        _, _, reason2 = kf.continue_agent("req", nodes, results, 0, max_fanout=50)
        self.assertEqual(reason2, "data-driven fan-out: +3")

    def test_fanout_nodes_carry_no_tier(self):
        # 動的生成ノード（map / reduce / gate・中間 reduce）は tier キーを持たない＝workload の
        # 段を継承する。人が固定した静的ノードだけが段を保ち（retry の維持は
        # RetryLadderTests.test_retry_nodes_inherit_pinned_tier）、「補償が届く部分だけが
        # 段を下げる」という設計上の分離が実装上も自動的に成り立つことを固定する。
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split",
                       "tier": "large", "agent": {"agent_cli": "codex"}}}
        results = {"s": {"status": "done", "data": [str(i) for i in range(20)]}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0, review=True)
        kinds = {t["kind"] for t in new}
        self.assertEqual(kinds, {"map", "reduce", "verify"})  # gate（verify）・中間 reduce 含む
        for t in new:
            self.assertNotIn("tier", t, t["id"])
            self.assertNotIn("agent", t, t["id"])   # 固定 agent も動的ノードへは伝播しない
            self.assertNotIn("tier", kf._node_entry(t), t["id"])  # graph entry へも入らない

    def test_map_goal_carries_request_intent(self):
        # map ゴールに元の要求（intent）が埋め込まれ、各要素に本来のタスクが適用される
        nodes = {"t1": {"id": "t1", "goal": "分解", "deps": [], "kind": "split"}}
        results = {"t1": {"status": "done", "data": ["1-100", "101-200"]}}
        _, new, _ = kf.continue_stub("1-1000まで素数を出して", nodes, results, 0)
        m1 = next(t for t in new if t["id"] == "t1-m1")
        self.assertIn("素数", m1["goal"])
        self.assertIn("1-100", m1["goal"])
        # reduce ゴールも intent を保持（並べ替え・集約条件を失わない）
        red = next(t for t in new if t["id"] == "t1-reduce")
        self.assertIn("素数", red["goal"])

    def test_collapse_static_split_successors(self):
        # planner が split→work→reduce を静的に焼き込んでも fan-out 前に後段を除去
        g = {"t1": {"id": "t1", "goal": "分割", "deps": [], "kind": "split"},
             "t2": {"id": "t2", "goal": "work", "deps": ["t1"], "kind": "work"},
             "t3": {"id": "t3", "goal": "reduce", "deps": ["t2"], "kind": "reduce"}}
        kf._sanitize_graph(g)
        self.assertEqual(sorted(g), ["t1"])

    def test_collapse_skipped_after_fanout(self):
        # 既に fan-out 済み（<split>-reduce 生成済み）なら除去しない
        g = {"t1": {"id": "t1", "goal": "s", "deps": [], "kind": "split"},
             "t1-m1": {"id": "t1-m1", "goal": "m", "deps": [], "kind": "map"},
             "t1-reduce": {"id": "t1-reduce", "goal": "r", "deps": ["t1-m1"], "kind": "reduce"}}
        kf._sanitize_graph(g)
        self.assertEqual(sorted(g), ["t1", "t1-m1", "t1-reduce"])

    def test_split_not_reexpanded(self):
        nodes = {"s": {"goal": "g", "deps": [], "kind": "split"},
                 "s-reduce": {"goal": "集約", "deps": ["s-m1"], "kind": "reduce"}}
        results = {"s": {"status": "done", "data": ["a", "b"]}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        # 既に展開済み（s-reduce あり）→ 追加しない
        self.assertFalse(any(t["id"].startswith("s-m") for t in new))

    def test_strategy_map_reduce_starts_with_split(self):
        strat, tasks = kf.plan_strategy_stub("ファイルをそれぞれ処理して集約")
        self.assertIn("map-reduce", strat["patterns"])
        self.assertEqual([t["kind"] for t in tasks], ["split"])
        # 集約パターンは既定（auto）で検証 gate が有効
        self.assertTrue(strat["review"])
        self.assertIn("adversarial-verification", strat["patterns"])


class CoerceTasksTests(unittest.TestCase):
    def test_unknown_kind_coerced_to_work(self):
        out = kf._coerce_tasks([{"id": "a", "goal": "g", "kind": "bogus"}])
        self.assertEqual(out[0]["kind"], "work")

    def test_valid_kinds_preserved(self):
        out = kf._coerce_tasks([{"id": "a", "kind": "split"}, {"id": "b", "kind": "reduce"}])
        self.assertEqual([t["kind"] for t in out], ["split", "reduce"])

    def test_duplicate_and_existing_ids_dropped(self):
        out = kf._coerce_tasks(
            [{"id": "x"}, {"id": "x"}, {"id": "y"}], existing={"y"})
        self.assertEqual([t["id"] for t in out], ["x"])  # 重複 x は 1 つ、既存 y は除外

    def test_deps_stringified(self):
        out = kf._coerce_tasks([{"id": "a", "deps": [1, "b"]}])
        self.assertEqual(out[0]["deps"], ["1", "b"])

    def test_operation_contract_passthrough(self):
        # 形が契約（§3.4）に合う処理契約だけ運ぶ。壊れた宣言は無いのと同じ。
        good = {"operation_class": "existing-test-repair",
                "scope": {"write": ["src/a.py"]},
                "deliverables": ["src/a.py"],
                "verification": {"commands": [["pytest", "-q"]]}}
        out = kf._coerce_tasks([{"id": "a", "operation": good},
                                {"id": "b", "operation": {"scope": "broken"}},
                                {"id": "c"}])
        self.assertEqual(out[0]["operation"], good)
        self.assertNotIn("operation", out[1])
        self.assertNotIn("operation", out[2])

    def test_replacement_metadata_preserved(self):
        out = kf._coerce_tasks([{"id": "a", "replaces": "old", "retries": "2"}])
        self.assertEqual(out[0]["replaces"], "old")
        self.assertEqual(out[0]["retries"], 2)


class RetryLadderTests(unittest.TestCase):
    def test_first_content_retry_moves_to_declared_costlier_agent(self):
        old = kf._AGENT_OVERRIDES
        self.addCleanup(setattr, kf, "_AGENT_OVERRIDES", old)
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides({
            "worker": {"agent_cli": "ollama", "fallbacks": [{"agent_cli": "claude"}]}})
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        decision, new, _ = kf.continue_stub("req", nodes, {"t1": {"status": "failed"}}, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["agent"]["agent_cli"], "claude")
        self.assertGreater(new[0]["agent"]["to_relative_cost"],
                           new[0]["agent"]["from_relative_cost"])

    def test_retry_nodes_inherit_pinned_tier(self):
        # 固定実行レベル（tier）は作り直しでも維持する——固定は迂回されない契約。
        nodes = {
            "gen": {"id": "gen", "goal": "g", "deps": [], "kind": "generate",
                    "tier": "medium", "agent": {"agent_cli": "codex"}},
            "chk": {"id": "chk", "goal": "検証", "deps": ["gen"], "kind": "verify",
                    "tier": "medium"},
        }
        results = {"gen": {"status": "done", "output": "ok"},
                   "chk": {"status": "done", "output": "fail", "data": {"ok": False}}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        by_id = {t["id"]: t for t in new}
        self.assertEqual(by_id["gen-r1"]["tier"], "medium")
        self.assertEqual(by_id["chk-r1"]["tier"], "medium")

    def test_failed_retry_inherits_pinned_tier(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work",
                        "tier": "large", "agent": {"agent_cli": "codex"}}}
        _, new, _ = kf.continue_stub("req", nodes, {"t1": {"status": "failed"}}, 0)
        self.assertEqual(new[0]["tier"], "large")

    def test_evaluator_replacement_uses_same_retry_ladder(self):
        old = kf._AGENT_OVERRIDES
        self.addCleanup(setattr, kf, "_AGENT_OVERRIDES", old)
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides({
            "worker": {"agent_cli": "ollama", "fallbacks": [{"agent_cli": "claude"}]}})
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "failed", "output": "bad"}}
        answer = ('{"decision":"replan","new_tasks":['
                  '{"id":"t1r","goal":"fix","deps":[],"kind":"work","replaces":"t1"}]}')
        with mock.patch.object(kf, "run_agent", return_value=answer):
            decision, new, _ = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["replaces"], "t1")
        self.assertEqual(new[0]["retries"], 1)
        self.assertEqual(new[0]["agent"]["agent_cli"], "claude")


class PlannerRobustnessTests(unittest.TestCase):
    """planner（kiro）がオブジェクトでなくベア配列を返しても落ちないこと。"""

    def test_continue_agent_handles_bare_list(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(
                kf, "run_agent",
                return_value='[{"id":"n1","goal":"次","deps":[],"kind":"work"}]'):
            decision, new, _ = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["n1"])

    def test_continue_agent_handles_scalar(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", return_value="42"):
            decision, new, _ = kf.continue_agent("req", nodes, results, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_human_feedback_from_results_is_executor_agnostic(self):
        # gitlab に限らず、結果コントラクト data.guidance / notes[].body を汎用に読む
        results = {
            "n1": {"status": "failed", "output": "x",
                   "data": {"decision": "rejected", "guidance": "実サーバで検証すること"}},
            "n2": {"status": "done", "output": "y",
                   "data": {"notes": [{"body": "命名は kebab-case で"}]}},
            "n3": {"status": "done", "output": "z"},          # data 無しは無視
        }
        hf = kf.human_feedback_from_results(results)
        self.assertIn("実サーバで検証すること", hf)
        self.assertIn("kebab-case", hf)

    def test_inflight_amend_only_pending_nodes(self):
        import types as _types
        tmp = tempfile.mkdtemp(prefix="kf-inflight-")
        bus = kf.Bus(tmp, "runX")
        bus.ensure_run("req")
        nodes = {"src": {"goal": "作業", "deps": [], "kind": "work"},
                 "p1": {"goal": "待機タスク1", "deps": [], "kind": "work"},
                 "c1": {"goal": "実行中タスク", "deps": [], "kind": "work"}}
        for nid, e in nodes.items():
            bus.write_task({"id": nid, **e})
        bus.write_graph({"nodes": nodes, "iteration": 0})
        # src は差し戻し guidance 付きで settled、c1 は claimed（実行中）、p1 は pending
        bus.write_result("src", "w", "failed", "ng",
                         data={"decision": "rejected", "guidance": "実サーバで検証すること"})
        self.assertTrue(bus.try_claim("c1", "w", lease_sec=60))
        args = _types.SimpleNamespace(run_id="runX")
        consumed = set()
        n = kf._inflight_amend_pending(bus, {"nodes": nodes, "iteration": 0}, "orch", args, consumed)
        self.assertEqual(n, 1)                                  # p1 のみ反映
        with open(os.path.join(bus.tasks_dir, "p1.json")) as f:
            p1 = json.load(f)
        with open(os.path.join(bus.tasks_dir, "c1.json")) as f:
            c1 = json.load(f)
        self.assertIn("実サーバで検証すること", p1["goal"])       # 待機ノードに人指摘が入った
        self.assertNotIn("実サーバで検証すること", c1["goal"])    # 実行中ノードは不変
        with open(os.path.join(bus.events_dir, "orch.jsonl")) as f:
            event = json.loads(f.read().splitlines()[-1])
        self.assertEqual(event["changes"]["updated"], ["p1"])
        self.assertIn("人の指摘", event["reason"])
        # 冪等: 同じ発生源では二度入れない
        self.assertEqual(kf._inflight_amend_pending(bus, {"nodes": nodes, "iteration": 0},
                                                    "orch", args, consumed), 0)

    def test_continue_agent_prompt_includes_human_feedback(self):
        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "failed", "output": "ng",
                          "data": {"decision": "rejected", "guidance": "実サーバで検証して"}}}
        seen = {}
        def fake_run(prompt, model, purpose=""):
            seen["p"] = prompt
            return '{"decision":"done","new_tasks":[]}'
        with mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.continue_agent("req", nodes, results, 0)
        self.assertIn("人からの指摘", seen["p"])
        self.assertIn("実サーバで検証して", seen["p"])       # 差し戻し guidance が replan に届く

    def test_plan_strategy_agent_handles_bare_list(self):
        with mock.patch.object(
                kf, "run_agent",
                return_value='[{"id":"t1","goal":"分解","deps":[],"kind":"split"}]'):
            strat, tasks = kf.plan_strategy_agent("req", None)
        self.assertEqual([t["id"] for t in tasks], ["t1"])
        self.assertEqual([d["decision"] for d in strat["decision_comparisons"]],
                         ["planner.pattern", "planner.parallelism"])

    def test_rule_agreement_is_measurement_only(self):
        answer = json.dumps({
            "patterns": ["tournament"], "parallelism": 4, "reason": "llm choice",
            "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}],
        })
        with mock.patch.object(kf, "run_agent", return_value=answer):
            strategy, _ = kf.plan_strategy_agent("単純な作業", None)
        self.assertEqual(strategy["patterns"], ["tournament"])
        self.assertEqual(strategy["parallelism"], 4)
        self.assertTrue(all(not d["agree"] for d in strategy["decision_comparisons"]))


class FlowPlannerAgentCliTests(unittest.TestCase):
    """flow-planner スキルを、planner に設定したエージェント CLI で動かすこと。

    スキル（scripts/plan.py）の既定は kiro-cli。それを黙って使うと、agent_cli を claude/codex に
    していても計画だけ kiro-cli で走り、kiro-cli が使えない環境では毎回失敗して stub 戦略へ
    落ちる（LLM を呼べていないのに計画できたように見える）。planner の設定を渡して揃える。"""

    def setUp(self):
        self._saved = (kf._AGENT_CLI, dict(kf._AGENT_OVERRIDES))
        self.addCleanup(lambda: setattr(kf, "_AGENT_CLI", self._saved[0]))
        self.addCleanup(lambda: setattr(kf, "_AGENT_OVERRIDES", self._saved[1]))

    def _capture_cmd(self, request="req", model=None):
        """plan_strategy_flow_planner が組み立てる argv を捕まえる（スクリプトは実行しない）。"""
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "strategy": {"patterns": ["fan-out-and-synthesize"], "parallelism": 2},
                    "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}],
                }),
                stderr="")

        with mock.patch.object(kf, "_find_flow_planner_script", return_value="/tmp/plan.py"), \
                mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            kf.plan_strategy_flow_planner(request, model)
        return seen["cmd"]

    def test_passes_planner_agent_cli_and_model(self):
        kf._AGENT_CLI = "kiro"
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides(
            {"planner": {"agent_cli": "claude", "model": "opus"}})
        cmd = self._capture_cmd()
        self.assertIn("--agent-cli", cmd)
        self.assertEqual(cmd[cmd.index("--agent-cli") + 1], "claude")
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")   # planner の model 上書きが勝つ

    def test_falls_back_to_global_agent_cli(self):
        # planner 個別の指定が無ければグローバル agent_cli に従う
        kf._AGENT_CLI = "codex"
        kf._AGENT_OVERRIDES = {}
        cmd = self._capture_cmd()
        self.assertEqual(cmd[cmd.index("--agent-cli") + 1], "codex")

    def test_planner_model_overrides_call_model(self):
        # 呼び出し値（グローバル model）より planner の model 上書きが優先される
        kf._AGENT_CLI = "claude"
        kf._AGENT_OVERRIDES = kf._normalize_agent_overrides({"planner": {"model": "sonnet"}})
        cmd = self._capture_cmd(model="opus")
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")

    def test_any_declared_agent_cli_is_passed_through(self):
        """定義ファイルを置いただけの CLI もそのままスキルへ渡す（白リストを作らない）。"""
        kf._AGENT_CLI = "cursor"
        kf._AGENT_OVERRIDES = {}
        cmd = self._capture_cmd()
        self.assertEqual(cmd[cmd.index("--agent-cli") + 1], "cursor")

    def test_planner_uses_the_declared_variant(self):
        """planner は JSON 契約の役割なので、定義が申告する用途別の変種へ自動で振り替わる。
        人が agents: を役割ごとに書き並べなくても、計画がツールループ型の起動形で
        空回りしない（コンセプト 柱3 / C9）。"""
        kf._AGENT_CLI = "ollama"
        kf._AGENT_OVERRIDES = {}
        cmd = self._capture_cmd()
        self.assertEqual(cmd[cmd.index("--agent-cli") + 1], "ollama-json")

    def test_timeout_follows_the_agent_timeout_setting(self):
        """1 回分の 3 倍を待つ。無効化（agent_timeout=0）なら待ち続ける。"""
        seen = {}

        def fake_run(cmd, **kw):
            seen["timeout"] = kw.get("timeout")
            return types.SimpleNamespace(
                returncode=0, stderr="",
                stdout=json.dumps({"strategy": {"patterns": ["fan-out-and-synthesize"]},
                                   "tasks": [{"id": "t1", "goal": "g", "deps": [], "kind": "work"}]}))

        with mock.patch.object(kf, "_find_flow_planner_script", return_value="/tmp/plan.py"), \
                mock.patch.object(kf.subprocess, "run", side_effect=fake_run):
            with mock.patch.object(kf, "_agent_timeout", return_value=600.0):
                kf.plan_strategy_flow_planner("req", None)
                self.assertEqual(seen["timeout"], 1800.0)
            with mock.patch.object(kf, "_agent_timeout", return_value=None):
                kf.plan_strategy_flow_planner("req", None)
                self.assertIsNone(seen["timeout"])

    def test_skill_env_exposes_agentcore(self):
        """スキル（独立プロセス）が agentcore を import できる PYTHONPATH を渡す。"""
        env = kf._skill_env()
        root = os.path.dirname(os.path.dirname(os.path.abspath(kf._agentcli.__file__)))
        self.assertIn(root, env["PYTHONPATH"].split(os.pathsep))

    def test_flow_planner_failure_is_recorded_not_swallowed(self):
        """スキルが失敗したら、縮退した事実を strategy.reason に残す。

        以前は黙って stub まで落ち、「計画できた」ように見えていた。"""
        def boom(cmd, **kw):
            return types.SimpleNamespace(returncode=2, stdout="",
                                         stderr="invalid choice: 'ollama'")

        with mock.patch.object(kf, "_find_flow_planner_script", return_value="/tmp/plan.py"), \
                mock.patch.object(kf.subprocess, "run", side_effect=boom), \
                mock.patch.object(kf, "run_agent", side_effect=RuntimeError("LLM 不通")):
            strategy, tasks = kf.plan_strategy_flow_planner("候補を出す", None)
        self.assertIn("flow-planner 不使用", strategy["reason"])
        self.assertIn("agent planner 失敗", strategy["reason"])   # stub まで落ちたことも残る
        self.assertTrue(tasks)

    def test_missing_skill_is_recorded(self):
        with mock.patch.object(kf, "_find_flow_planner_script", return_value=None), \
                mock.patch.object(kf, "run_agent", side_effect=RuntimeError("LLM 不通")):
            strategy, _ = kf.plan_strategy_flow_planner("何かする", None)
        self.assertIn("スキルが見つかりません", strategy["reason"])


class GraphHealthTests(unittest.TestCase):
    def test_unknown_deps_dropped(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["ghost"], "kind": "work"},
                 "b": {"id": "b", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        self.assertEqual(nodes["a"]["deps"], [])      # 未知 ghost を除去
        self.assertEqual(nodes["b"]["deps"], ["a"])   # 正当な依存は保持

    def test_cycle_broken(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["b"], "kind": "work"},
                 "b": {"id": "b", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        # 循環が断ち切られ、トポロジカル順が成立する（少なくとも片方の deps が空）
        self.assertTrue(nodes["a"]["deps"] == [] or nodes["b"]["deps"] == [])

    def test_self_loop_dropped(self):
        nodes = {"a": {"id": "a", "goal": "", "deps": ["a"], "kind": "work"}}
        kf._sanitize_graph(nodes)
        self.assertEqual(nodes["a"]["deps"], [])


class ContinuationTests(unittest.TestCase):
    def test_replan_retries_failed_once(self):
        nodes = {"t1": {"goal": "ok", "deps": [], "kind": "work"},
                 "t2": {"goal": "FAIL bad", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done"}, "t2": {"status": "failed"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual([t["id"] for t in new], ["t2r"])
        self.assertNotIn("FAIL", new[0]["goal"])  # retry のゴールは修正済み

    def test_no_replan_when_all_done(self):
        nodes = {"t1": {"goal": "ok", "deps": [], "kind": "work"}}
        decision, new, _ = kf.continue_stub("req", nodes, {"t1": {"status": "done"}}, 0)
        self.assertEqual(decision, "done")
        self.assertEqual(new, [])

    def test_classify_routes_to_specialist(self):
        nodes = {"classify": {"goal": "分類: backend のバグ", "deps": [], "kind": "classify"}}
        results = {"classify": {"status": "done", "output": "class=backend"}}
        decision, new, _ = kf.continue_stub("backend のバグ", nodes, results, 0)
        self.assertEqual(decision, "replan")
        self.assertEqual(new[0]["id"], "classify-act")
        self.assertIn("backend", new[0]["goal"])
        self.assertEqual(new[0]["deps"], ["classify"])

    def test_verify_fail_triggers_regen_and_recheck(self):
        nodes = {"gen1": {"goal": "FLAKY work", "deps": [], "kind": "generate"},
                 "verify1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done", "output": "[stub] 未完(issue)"},
                   "verify1": {"status": "done", "output": "verify=fail"}}
        decision, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual(decision, "replan")
        ids = [t["id"] for t in new]
        self.assertIn("gen1-r1", ids)     # 作り直し
        self.assertIn("verify1-r1", ids)  # 再検証
        self.assertNotIn("FLAKY", next(t for t in new if t["id"] == "gen1-r1")["goal"])

    def test_failed_verify_retries_only_the_verify_executor(self):
        nodes = {"gen1": {"goal": "work", "deps": [], "kind": "generate"},
                 "verify1": {"goal": "検証", "deps": ["gen1"], "kind": "verify"}}
        results = {"gen1": {"status": "done"},
                   "verify1": {"status": "failed", "output": "verify=fail",
                               "data": {"ok": False}}}
        _, new, _ = kf.continue_stub("req", nodes, results, 0)
        self.assertEqual({t["id"] for t in new}, {"verify1r"})


class PatternStrategyTests(unittest.TestCase):
    def test_pattern_detection(self):
        cases = {
            "バグを分類して振り分けて": "classify-and-act",
            "3案を比較して最良を選ぶ tournament": "tournament",
            "候補を出してフィルタ": "generate-and-filter",
            "成果をレビューして検証": "adversarial-verification",
            "テストが通るまで繰り返す": "loop-until-done",
            "資料を3観点でまとめる": "fan-out-and-synthesize",
        }
        for req, want in cases.items():
            self.assertEqual(kf._detect_pattern(req), want, req)

    def test_boilerplate_sections_do_not_decide_the_pattern(self):
        """agent-project の定型（対象リポジトリ一覧の「書込先候補」等）でパターンが決まらない。

        実測: この定型のせいで 15 件中 9 件が要求の中身と無関係に generate-and-filter へ倒れた。
        判定に使うのは要求本体（先頭の段落）だけ。"""
        request = (
            "設計書と README を実装に追随させる\n\n"
            "対象リポジトリ:\n"
            "- agent-flow = https://example.invalid/x（書込先候補（owns: tools/agent-flow/**））\n"
            "- agent-board = https://example.invalid/y（書込先候補（owns: tools/agent-board/**））\n")
        self.assertEqual(kf._detect_pattern(request), "fan-out-and-synthesize")

    def test_named_pattern_in_the_tail_is_respected(self):
        """本体の外でもパターン名の名指しは尊重する（完了条件の但し書き等）。"""
        request = ("codd-gate の連携コードを整理する\n\n"
                   "このタスクは完了条件を満たすまで反復すること（loop-until-done）。\n")
        self.assertEqual(kf._detect_pattern(request), "loop-until-done")

    def test_parallelism_extraction(self):
        self.assertEqual(kf._parallelism("候補を x4 出す", 2), 4)
        self.assertEqual(kf._parallelism("並列5で", 2), 5)
        self.assertEqual(kf._parallelism("ふつうの要求", 3), 3)

    def test_fanout_graph_has_synthesize_over_parallel(self):
        # 既定（auto）では集約パターンに検証 gate が入るため、純粋な構造は --no-review で確認
        strat, tasks = kf.plan_strategy_stub("A; B; C", review=False)
        self.assertEqual(strat["patterns"], ["fan-out-and-synthesize"])
        self.assertFalse(strat["review"])
        synth = [t for t in tasks if t["kind"] == "synthesize"]
        self.assertEqual(len(synth), 1)
        # 統合ノードは全並列ノードに依存
        gens = [t["id"] for t in tasks if t["kind"] != "synthesize"]
        self.assertEqual(sorted(synth[0]["deps"]), sorted(gens))

    def test_aggregating_pattern_auto_enables_review(self):
        # 公式準拠: 集約パターンは既定で検証 gate を自動挿入する
        strat, tasks = kf.plan_strategy_stub("A; B; C")  # fan-out-and-synthesize
        self.assertTrue(strat["review"])
        self.assertIn("verify", [t["kind"] for t in tasks])

    def test_non_aggregating_pattern_no_auto_review(self):
        # 集約点を持たない（または内包する）パターンは auto では gate を足さない
        strat, _ = kf.plan_strategy_stub("バグを分類して振り分けて")  # classify-and-act
        self.assertFalse(strat["review"])

    def test_explicit_no_review_overrides_auto(self):
        strat, _ = kf.plan_strategy_stub("ファイルをそれぞれ処理して集約", review=False)
        self.assertFalse(strat["review"])

    def test_tournament_graph_has_judge(self):
        strat, tasks = kf.plan_strategy_stub("最良案を選ぶ tournament x3")
        self.assertEqual(strat["patterns"], ["tournament"])
        self.assertEqual(strat["parallelism"], 3)
        self.assertEqual(len([t for t in tasks if t["kind"] == "generate"]), 3)
        self.assertEqual(len([t for t in tasks if t["kind"] == "judge"]), 1)


class GranularityTests(unittest.TestCase):
    def test_factor_levels(self):
        self.assertEqual(kf.granularity_factor("auto"), 1)
        self.assertEqual(kf.granularity_factor("coarse"), 1)
        self.assertEqual(kf.granularity_factor("fine"), 2)
        self.assertEqual(kf.granularity_factor("finest"), 3)
        self.assertEqual(kf.granularity_factor(None), 1)         # 既定は auto（倍率1）
        self.assertEqual(kf.granularity_factor("unknown"), 1)

    def test_directive_auto_empty_others_scope(self):
        self.assertEqual(kf.granularity_directive("auto"), "")
        self.assertEqual(kf.granularity_directive(None), "")
        self.assertIn("scope", kf.granularity_directive("coarse"))
        self.assertIn("30", kf.granularity_directive("fine"))
        self.assertIn("30", kf.granularity_directive("finest"))

    def test_stub_scales_node_count_by_granularity(self):
        # 同じ要求でも粒度が細かいほど並列ノードが増える（明示並列が無い場合）。
        # plan_stub は単一セグメントで乱数を使うため、同一 base になるよう seed を固定する
        import random
        req = "最良案を選ぶ tournament"            # generate ノード数 = parallelism

        def plan(g):
            random.seed(0)
            return kf.plan_strategy_stub(req, granularity=g)

        coarse, ctasks = plan("coarse")
        fine, _ = plan("fine")
        finest, ftasks = plan("finest")
        self.assertLess(coarse["parallelism"], fine["parallelism"])
        self.assertLess(fine["parallelism"], finest["parallelism"])
        self.assertEqual(fine["parallelism"], coarse["parallelism"] * 2)
        self.assertEqual(finest["parallelism"], coarse["parallelism"] * 3)
        gens = lambda ts: len([t for t in ts if t["kind"] == "generate"])
        self.assertGreater(gens(ftasks), gens(ctasks))           # 細かいほどノードが多い

    def test_auto_matches_coarse_scale(self):
        import random
        req = "最良案を選ぶ tournament"
        random.seed(0)
        auto, _ = kf.plan_strategy_stub(req, granularity="auto")
        random.seed(0)
        coarse, _ = kf.plan_strategy_stub(req, granularity="coarse")
        self.assertEqual(auto["parallelism"], coarse["parallelism"])

    def test_explicit_parallelism_not_scaled(self):
        # 要求に "x3" 等の明示があれば粒度倍率は効かせない（ユーザ指定を尊重）
        strat, _ = kf.plan_strategy_stub("案を出して選ぶ tournament x3", granularity="finest")
        self.assertEqual(strat["parallelism"], 3)

    def test_scale_parallelism_caps_at_16(self):
        self.assertEqual(kf.scale_parallelism(6, "finest"), 16)   # 6*3=18 → 16 にクランプ

    def test_default_config_granularity_is_auto(self):
        self.assertEqual(kf.CONFIG_DEFAULTS.get("granularity"), "auto")


class FinalResultNodesTests(unittest.TestCase):
    """`_final_result_nodes` の選択規則。

    以前は done のノードだけを集めていたため、全ノードが失敗した run で空リストを返していた。
    委譲 executor の却下は park の決着として failed ノードになるので、却下 run では
    `result --json` の final_nodes が常に空になり、そこから読む reject_guidance /
    result_notes / discoveries が submit 経路でも板経路でも一切拾えなかった。"""

    def _nodes(self):
        return {"t1": {"kind": "work", "deps": []},
                "synth": {"kind": "synthesize", "deps": ["t1"]}}

    def test_successful_run_is_unchanged(self):
        results = {"t1": {"status": "done"}, "synth": {"status": "done"}}
        self.assertEqual(kf._final_result_nodes(self._nodes(), results), ["synth"])

    def test_all_failed_run_returns_failed_sink(self):
        results = {"t1": {"status": "failed"}, "synth": {"status": "failed"}}
        self.assertEqual(kf._final_result_nodes(self._nodes(), results), ["synth"])

    def test_done_is_preferred_over_failed(self):
        # 一部成功した run では従来どおり done 側が最終成果（見え方を変えない）。
        results = {"t1": {"status": "done"}, "synth": {"status": "failed"}}
        self.assertEqual(kf._final_result_nodes(self._nodes(), results), ["t1"])

    def test_unfinished_run_returns_empty(self):
        results = {"t1": {"status": "running"}, "synth": {}}
        self.assertEqual(kf._final_result_nodes(self._nodes(), results), [])


class FinalResultNodeTests(unittest.TestCase):
    def test_prefers_aggregation_sink(self):
        nodes = {
            "t1": {"kind": "work", "deps": []},
            "t2": {"kind": "work", "deps": []},
            "synth": {"kind": "synthesize", "deps": ["t1", "t2"]},
        }
        results = {k: {"status": "done"} for k in nodes}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["synth"])

    def test_falls_back_to_sinks_without_agg_kind(self):
        # 末端が work のみ → 集約 kind が無いので末端ノードを返す
        nodes = {
            "a": {"kind": "work", "deps": []},
            "b": {"kind": "work", "deps": ["a"]},
        }
        results = {k: {"status": "done"} for k in nodes}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["b"])

    def test_falls_back_when_agg_node_not_done(self):
        # 集約ノードが未完了なら done の末端へフォールバック
        nodes = {
            "t1": {"kind": "work", "deps": []},
            "synth": {"kind": "synthesize", "deps": ["t1"]},
        }
        results = {"t1": {"status": "done"}, "synth": {"status": "pending"}}
        self.assertEqual(kf._final_result_nodes(nodes, results), ["t1"])

    def test_empty_when_nothing_done(self):
        self.assertEqual(
            kf._final_result_nodes({"t1": {"kind": "work", "deps": []}},
                                   {"t1": {"status": "pending"}}), [])
        self.assertEqual(kf._final_result_nodes({}, {}), [])


class SplitPolicyTests(unittest.TestCase):
    """分割の単位（split_policy）— 粒度とは独立の「どこで切るか」を planner へ渡すこと。

    設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P2
    """

    def test_default_policy_is_behavior(self):
        self.assertEqual(kf.split_policy(None), "behavior")
        self.assertEqual(kf.split_policy(""), "behavior")
        self.assertEqual(kf.split_policy("unknown"), "behavior")
        self.assertEqual(kf.split_policy("file"), "file")

    def test_behavior_directive_forbids_file_split(self):
        note = kf.split_policy_directive("behavior")
        self.assertIn("利用者から見える 1 つの振る舞いを 1 ノード", note)
        self.assertIn("共有部品", note)
        self.assertIn("水平分割", note)

    def test_file_policy_is_opt_in_and_asks_for_alignment(self):
        note = kf.split_policy_directive("file")
        self.assertIn("ファイル境界で水平に分割してよい", note)
        self.assertIn("揃えるべき点", note)

    def test_planner_prompt_carries_split_directive(self):
        seen = {}

        def fake_run(prompt, model, purpose=""):
            seen["p"] = prompt
            return '{"patterns":["fan-out-and-synthesize"],"parallelism":2,"tasks":[' \
                   '{"id":"t1","goal":"g","deps":[],"kind":"work"}]}'

        with mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.plan_strategy_agent("req", None)
        self.assertIn(kf.split_policy_directive("behavior"), seen["p"])

        with mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.plan_strategy_agent("req", None, policy="file")
        self.assertIn(kf.split_policy_directive("file"), seen["p"])

    def test_config_default_reaches_args(self):
        args = types.SimpleNamespace(config=None, split_policy=None)
        kf.resolve_config(args)
        self.assertEqual(args.split_policy, "behavior")


class ReviewLensTests(unittest.TestCase):
    """レビューラウンドの観点（レンズ）— 契約整合の 1 本槍にしないこと。

    設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P4
    """

    def test_lenses_cover_duplication_divergence_and_verbosity(self):
        self.assertEqual([key for key, _label, _detail in kf.REVIEW_LENSES],
                         ["duplication", "divergence", "verbosity"])

    def test_directive_requires_reason_even_when_nothing_found(self):
        note = kf.review_lens_directive()
        self.assertIn("二重実装", note)
        self.assertIn("画面間・用途間の表現差異", note)
        self.assertIn("文言量", note)
        self.assertIn("decision=done", note)

    def test_evaluator_prompt_carries_lenses(self):
        seen = {}

        def fake_run(prompt, model, purpose=""):
            seen["p"] = prompt
            return '{"decision":"done","new_tasks":[]}'

        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.continue_agent("req", nodes, results, 0)
        self.assertIn(kf.review_lens_directive(), seen["p"])


class PerTaskRuleTests(unittest.TestCase):
    """工程ごとに選ぶ作業ルール（per-task rule）を planner・評価役へ渡す配線。

    カタログの正典は run 専用 tuning.json（dashboard が selection: "per-task" の定義を
    enabled: false のまま複製する）。エンジンは「その一覧を prompt へ載せ、選ばれたタスク
    だけへ role の合う本文を複製する」規則だけを持つ。

    設計: docs/plans/2026-08-15-workflow-feature-improvement-implementation.md 第 5 段
    """

    def _tuning_dir_with(self, methods):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "tuning.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "enabled": True, "methods": methods, "trials": []}, f)
        return d

    def test_catalog_is_empty_without_a_tuning_file(self):
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": os.path.join(tempfile.mkdtemp(), "no-such-dir")}):
            self.assertEqual(kf._per_task_rule_catalog(), {})
            self.assertEqual(kf.per_task_rule_directive(), "")

    def test_catalog_reads_only_selection_per_task(self):
        d = self._tuning_dir_with([
            {"id": "integration-verify", "description": "統合検証", "selection": "per-task",
             "fragments": [{"role": "verify", "text": "全体を回す"}]},
            {"id": "ui-consistency", "description": "画面の一貫性", "selection": "auto", "enabled": True,
             "fragments": [{"role": "worker", "text": "既存 UI に揃える"}]},
        ])
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d}):
            catalog = kf._per_task_rule_catalog()
            self.assertEqual(list(catalog.keys()), ["integration-verify"])
            note = kf.per_task_rule_directive()
            self.assertIn("integration-verify: 統合検証", note)
            self.assertNotIn("ui-consistency", note)
            self.assertIn('"methods":', note)

    def test_catalog_excludes_rules_whose_tier_the_run_does_not_use(self):
        # when.tiers を宣言した per-task ルールは、いま走っている実行 tier と合わなければ
        # planner へ提示しない（合わない候補を見せてもどのみち選べないので、素直に落とす）。
        d = self._tuning_dir_with([
            {"id": "large-only", "description": "高性能限定の確認", "selection": "per-task",
             "when": {"tiers": ["large"]}, "fragments": [{"role": "verify", "text": "x"}]},
            {"id": "any-tier", "description": "tier を選ばない確認", "selection": "per-task",
             "fragments": [{"role": "verify", "text": "y"}]},
        ])
        control_dir = tempfile.mkdtemp(prefix="kf-per-task-tier-")
        self.addCleanup(shutil.rmtree, control_dir, True)
        with open(os.path.join(control_dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"workloads": {"flow": {"tier": "basic"}}}, f)
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d, "AGENT_CONTROL_DIR": control_dir}):
            catalog = kf._per_task_rule_catalog()
            self.assertEqual(list(catalog.keys()), ["any-tier"])

    def test_catalog_keeps_tier_scoped_rules_when_the_control_tier_is_unknown(self):
        # agent-control 未導入（tier 宣言なし）の実行では、どの段が走るか判定材料が無い。
        # 判定できないことを理由に候補を消さない（フェイルオープン）。
        d = self._tuning_dir_with([
            {"id": "large-only", "description": "高性能限定の確認", "selection": "per-task",
             "when": {"tiers": ["large"]}, "fragments": [{"role": "verify", "text": "x"}]},
        ])
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d,
                                          "AGENT_CONTROL_DIR": os.path.join(tempfile.mkdtemp(), "no-such")}):
            self.assertEqual(list(kf._per_task_rule_catalog().keys()), ["large-only"])

    def test_catalog_does_not_reject_on_unknown_role_or_purpose_fields(self):
        # roles/purposes/agent_cli/relative_cost は「どのノードが選ぶか」に依存するため、
        # planner へ提示するこの時点では判定しない（node が確定してから role で絞る）。
        # agentcore.methods.matches() をそのまま使うと when.roles 宣言だけで全滅するので、
        # そうなっていないことを確認する。
        d = self._tuning_dir_with([
            {"id": "integration-verify", "description": "統合検証", "selection": "per-task",
             "when": {"roles": ["verify"], "purposes": ["verify"], "max_relative_cost": 0},
             "fragments": [{"role": "verify", "text": "x"}]},
        ])
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d}):
            self.assertEqual(list(kf._per_task_rule_catalog().keys()), ["integration-verify"])

    def test_coerce_tasks_duplicates_text_only_for_chosen_ids_and_matching_role(self):
        d = self._tuning_dir_with([
            {"id": "integration-verify", "description": "統合検証", "selection": "per-task",
             "fragments": [{"role": "verify", "text": "パッケージ全体を回す。"}]},
        ])
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d}):
            tasks = kf._coerce_tasks([
                {"id": "check", "goal": "確認する", "deps": [], "kind": "verify",
                 "methods": ["integration-verify"]},
                {"id": "build", "goal": "実装する", "deps": [], "kind": "work",
                 "methods": ["integration-verify"]},  # role が worker なので本文は付かない
                {"id": "plain", "goal": "何もしない", "deps": [], "kind": "work"},
            ])
        by_id = {t["id"]: t for t in tasks}
        self.assertIn("パッケージ全体を回す。", by_id["check"]["goal"])
        self.assertIn("作業ルール「統合検証」", by_id["check"]["goal"])
        self.assertEqual(by_id["build"]["goal"], "実装する", "role が合わない選択は本文を足さない")
        self.assertEqual(by_id["plain"]["goal"], "何もしない")
        # goal 以外の構造化フィールドへは methods を残さない（実行エンジンへ生の選択を渡さない）
        self.assertNotIn("methods", by_id["check"])

    def test_coerce_tasks_ignores_unknown_ids_and_dedupes(self):
        d = self._tuning_dir_with([
            {"id": "integration-verify", "description": "統合検証", "selection": "per-task",
             "fragments": [{"role": "verify", "text": "全体を回す。"}]},
        ])
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d}):
            tasks = kf._coerce_tasks([
                {"id": "check", "goal": "確認する", "deps": [], "kind": "verify",
                 "methods": ["integration-verify", "no-such-id", "integration-verify"]},
            ])
        self.assertEqual(tasks[0]["goal"].count("全体を回す。"), 1, "同じ id の重複複製はしない")

    def test_coerce_tasks_is_a_no_op_without_a_tuning_file(self):
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": os.path.join(tempfile.mkdtemp(), "no-such-dir")}):
            tasks = kf._coerce_tasks([
                {"id": "check", "goal": "確認する", "deps": [], "kind": "verify",
                 "methods": ["integration-verify"]},
            ])
        self.assertEqual(tasks[0]["goal"], "確認する")

    def test_planner_prompt_carries_the_catalog_when_present(self):
        d = self._tuning_dir_with([
            {"id": "integration-verify", "description": "統合検証", "selection": "per-task",
             "fragments": [{"role": "verify", "text": "全体を回す。"}]},
        ])
        seen = {}

        def fake_run(prompt, model, purpose=""):
            seen["p"] = prompt
            return '{"patterns":["fan-out-and-synthesize"],"parallelism":2,"tasks":[' \
                   '{"id":"t1","goal":"g","deps":[],"kind":"work"}]}'

        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d}), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.plan_strategy_agent("req", None)
        self.assertIn(kf.per_task_rule_directive(), seen["p"])

    def test_planner_prompt_unchanged_without_a_catalog(self):
        seen = {}

        def fake_run(prompt, model, purpose=""):
            seen["p"] = prompt
            return '{"patterns":["fan-out-and-synthesize"],"parallelism":2,"tasks":[' \
                   '{"id":"t1","goal":"g","deps":[],"kind":"work"}]}'

        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": os.path.join(tempfile.mkdtemp(), "no-such-dir")}), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.plan_strategy_agent("req", None)
        self.assertNotIn("工程ごとに選べる追加ルール", seen["p"])

    def test_evaluator_prompt_carries_the_catalog(self):
        d = self._tuning_dir_with([
            {"id": "integration-verify", "description": "統合検証", "selection": "per-task",
             "fragments": [{"role": "verify", "text": "全体を回す。"}]},
        ])
        seen = {}

        def fake_run(prompt, model, purpose=""):
            seen["p"] = prompt
            return '{"decision":"done","new_tasks":[]}'

        nodes = {"t1": {"id": "t1", "goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.dict(os.environ, {"AGENT_TUNING_DIR": d}), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run):
            kf.continue_agent("req", nodes, results, 0)
        self.assertIn(kf.per_task_rule_directive(), seen["p"])


class TerminalVerificationTests(unittest.TestCase):
    """run の完了条件 — 終端 verify が緑であること。

    設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P1
    """

    NODES = {
        "work": {"id": "work", "kind": "work", "deps": []},
        "check": {"id": "check", "kind": "verify", "deps": ["work"]},
    }

    def _verdict(self, verify_result):
        results = {"work": {"status": "done", "output": "作った"}, "check": verify_result}
        return kf.terminal_verification(self.NODES, results)

    def test_structured_ok_decides_the_run(self):
        self.assertEqual(self._verdict({"status": "done", "output": "verify=pass",
                                        "data": {"ok": True}})["state"], "passed")
        red = self._verdict({"status": "done", "output": "verify=fail", "data": {"ok": False}})
        self.assertEqual(red["state"], "failed")
        self.assertEqual(red["failed"], ["check"])

    def test_missing_verdict_falls_back_to_the_one_normalizer(self):
        # data が無い成果は _normalize_verify の 1 実装で読む（曖昧な出力は fail へ倒す）
        self.assertEqual(self._verdict({"status": "done", "output": "verify=pass"})["state"], "passed")
        self.assertEqual(self._verdict({"status": "done", "output": "LGTM"})["state"], "failed")
        # 本文の素朴な文字列一致では判定しない（"no failures" を赤と読まない）
        self.assertEqual(
            self._verdict({"status": "done", "output": "verify=pass — no failures"})["state"],
            "passed")

    def test_failed_and_unfinished_verify_nodes(self):
        self.assertEqual(self._verdict({"status": "failed", "output": "落ちた"})["state"], "failed")
        pending = self._verdict({"status": "pending"})
        self.assertEqual(pending["state"], "pending")
        self.assertEqual(pending["pending"], ["check"])

    def test_only_terminal_verify_nodes_count(self):
        # 統合前 gate（後段が依存する verify）は終端ではないので完了条件に使わない
        nodes = {
            "t1": {"id": "t1", "kind": "work", "deps": []},
            "gate": {"id": "gate", "kind": "verify", "deps": ["t1"]},
            "synth": {"id": "synth", "kind": "synthesize", "deps": ["t1", "gate"]},
        }
        results = {"t1": {"status": "done"},
                   "gate": {"status": "done", "output": "verify=fail", "data": {"ok": False}},
                   "synth": {"status": "done", "output": "統合"}}
        self.assertEqual(kf.terminal_verification(nodes, results), {"state": "none", "nodes": []})

    def test_failure_reason_is_tagged_for_triage(self):
        self.assertIsNone(kf._verification_failure({"state": "passed", "nodes": ["check"]}))
        self.assertIsNone(kf._verification_failure({"state": "none", "nodes": []}))
        self.assertIn("[verification]",
                      kf._verification_failure({"state": "failed", "failed": ["check"]}))
        self.assertIn("check", kf._verification_failure({"state": "failed", "failed": ["check"]}))
