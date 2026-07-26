"""agent-project の単体テスト — planning（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class RiskDigestTests(unittest.TestCase):
    """検収（review）前のリスクダイジェスト（決定的な材料のみ・needs の ## リスク節と
    frontmatter risk: low/med/high）。承認フローは変えず情報だけが増えることを検証する。"""

    def test_levels_are_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="安全な作業")
            level, md = km.risk_digest(cfg, t, set(), [])
            self.assertEqual(level, "low")
            self.assertIn("総合: 低", md)
            # protect 接触 → high
            level, md = km.risk_digest(cfg, t, {"auth/x.py"}, [("auth/x.py", "auth/**")])
            self.assertEqual(level, "high")
            self.assertIn("保護パス接触", md)
            # リトライ経験 → med
            t2 = km.Task(id="T2", title="やり直した作業", retries=2)
            level, md = km.risk_digest(cfg, t2, set(), [])
            self.assertEqual(level, "med")
            self.assertIn("リトライ: 2 回", md)
            # 大きな差分（10 ファイル以上）→ med
            level, _ = km.risk_digest(cfg, t, {f"f{i}.py" for i in range(10)}, [])
            self.assertEqual(level, "med")

    def test_avoid_similarity_raises_to_high(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True)
            (cfg.decisions / "OLD.md").write_text(
                "## DR-0001  2026-07-01  actor: human\n"
                "- avoid: deploy prod release :: 本番系は人が見る\n", encoding="utf-8")
            t = km.Task(id="T1", title="deploy prod release v2")
            level, md = km.risk_digest(cfg, t, set(), [])
            self.assertEqual(level, "high")
            self.assertIn("回避判断", md)

    def test_synth_verify_and_assess_raise_to_med(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="合成 verify の作業")
            t.extra.append(("verify_source", "synth"))
            level, md = km.risk_digest(cfg, t, set(), [])
            self.assertEqual(level, "med")
            self.assertIn("自動合成", md)
            # 採点 r=3 も med に引き上げる（P2 の投入時アセスメント連動）
            t2 = km.Task(id="T2", title="採点付き")
            t2.extra.append(("assess", "c=1 r=3 a=1"))
            level, md = km.risk_digest(cfg, t2, set(), [])
            self.assertEqual(level, "med")
            self.assertIn("投入時採点", md)

    def test_review_needs_carries_risk_section(self):
        # delivery_review で review へ遷移した needs 票に ## リスク節と frontmatter risk が載る
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, delivery_review=True, learn=False, auto_adjudicate=False)
            mkb(d, "T1", verify="true")
            result = km.run_loop(cfg)
            self.assertEqual(result["counts"]["review"], 1)
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("risk: low", text.split("---")[1])   # frontmatter（viewer バッジ用）
            self.assertIn("## リスク", text)
            self.assertIn("総合: 低", text)

    def test_blocked_needs_has_no_risk_section(self):
        # リスクダイジェストは検収票（review）のみ。blocked 票は従来のまま
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1")
            t = km.load_tasks(cfg.backlog)[0]
            km.write_needs_file(cfg, t, "判断待ち")
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertNotIn("## リスク", text)
            self.assertNotIn("risk:", text.split("---")[1])


class AssessTests(unittest.TestCase):
    """投入時アセスメント（c=複雑さ r=リスク a=曖昧さ・各1-3）。採点は情報のみで、
    実行可否・done 条件を変えないことを検証する。"""

    def test_heuristic_scoring_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                            # executor=stub → ヒューリスティック
            t = km.Task(id="T1", title="verify 持ち", verify="true")
            self.assertEqual(km.assess_task(cfg, t), "c=1 r=1 a=1")
            t2 = km.Task(id="T2", title="accept のみ")
            t2.extra.append(("accept", "READMEに使用例"))
            self.assertEqual(km.assess_task(cfg, t2), "c=1 r=1 a=2")
            t3 = km.Task(id="T3", title="完了条件なし")
            self.assertEqual(km.assess_task(cfg, t3), "c=1 r=1 a=3")
            t4 = km.Task(id="T4", title="繰り返し {item}", verify="true")
            t4.extra.append(("cohort_items", "a,b,c"))
            self.assertEqual(km.assess_task(cfg, t4), "c=3 r=1 a=1")

    def test_avoid_similarity_scores_risk(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True)
            (cfg.decisions / "OLD.md").write_text(
                "## DR-0001  2026-07-01  actor: human\n"
                "- avoid: deploy prod release :: 本番系は人が見る\n", encoding="utf-8")
            t = km.Task(id="T1", title="deploy prod release v2", verify="true")
            self.assertEqual(km.assess_task(cfg, t), "c=1 r=3 a=1")

    def test_agent_scoring_clamps_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="kiro")           # 非 stub → エージェント採点
            t = km.Task(id="T1", title="x", verify="true")
            val = km.assess_task(cfg, t, agent_run=lambda p, m: '{"c": 9, "r": 0, "a": 2}')
            self.assertEqual(val, "c=3 r=1 a=2")        # 1-3 にクランプ
            # 非 JSON・例外はヒューリスティックへフォールバック
            t2 = km.Task(id="T2", title="y", verify="true")
            val = km.assess_task(cfg, t2, agent_run=lambda p, m: "説明文だけ")
            self.assertEqual(val, "c=1 r=1 a=1")
            t3 = km.Task(id="T3", title="z", verify="true")
            val = km.assess_task(cfg, t3,
                                 agent_run=lambda p, m: (_ for _ in ()).throw(RuntimeError()))
            self.assertEqual(val, "c=1 r=1 a=1")

    def test_assess_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="true")
            km.assess_task(cfg, t)
            km.assess_task(cfg, t)                      # 2 回目はスキップ（重複記録しない）
            self.assertEqual(sum(1 for k, _ in t.extra if k == "assess"), 1)

    def test_run_setup_scores_and_plan_review_card_shows_it(self):
        # run で新規タスクが採点され、plan-review 票（needs）に assess が載る
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, plan_review=True, assess=True)
            mkb(d, "T1", status="proposed")
            km.run_loop(cfg)
            body = (cfg.backlog / "T1.md").read_text(encoding="utf-8")
            self.assertIn("- assess: c=1 r=1 a=1", body)
            needs = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("assess: c=1 r=1 a=1", needs)  # レビュー票の判断材料に載る

    def test_assess_off_keeps_tasks_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, assess=False, delivery_review=False)
            mkb(d, "T1")
            km.run_loop(cfg)
            # done → archive でも assess は付かない
            adir = cfg.archive_dir()
            text = "".join(p.read_text(encoding="utf-8") for p in adir.glob("*.md")) \
                if adir.exists() else ""
            text += "".join(p.read_text(encoding="utf-8") for p in cfg.backlog.glob("*.md"))
            self.assertNotIn("- assess:", text)


class SpecTrackTests(unittest.TestCase):
    """spec ルーティング（opt-in spec_track）と spec 前段タスク連鎖。
    ルーティングは「タスクを足す」方向のみで done 条件・予算に触れないこと、
    展開は人の承認（spec タスクの done）後にだけ起きることを検証する。"""

    def _routed(self, d, assess="c=3 r=1 a=1", **kw):
        cfg = cfg_for(d, spec_track=True, **kw)
        (d / "backlog").mkdir(parents=True, exist_ok=True)
        (d / "backlog" / "T1.md").write_text(
            f"## T1: 大きめの機能\n- status: ready\n- source: human\n- verify: `true`\n"
            f"- retries: 0\n- assess: {assess}\n", encoding="utf-8")
        tasks = km.load_tasks(cfg.backlog)
        created = km.route_spec_tasks(cfg, tasks, km.Policy())
        return cfg, tasks, created

    def test_route_creates_spec_task_and_chains_after(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks, created = self._routed(d)
            self.assertEqual(len(created), 1)
            s = created[0]
            self.assertEqual(s.id, "T1-spec")
            self.assertEqual(s.get("spec_for"), "T1")
            self.assertEqual(s.get("review"), "human")       # spec は必ず人が検収
            self.assertIn("specs/T1", s.verify)              # 決定的 verify（3 ファイル存在）
            t = km.load_tasks(cfg.backlog)
            t1 = next(x for x in t if x.id == "T1")
            self.assertEqual(t1.get("route"), "spec")
            self.assertEqual(t1.get("spec_task"), "T1-spec")
            self.assertIn("T1-spec", km.task_deps(t1))       # T1 は spec 完了まで待つ
            # 決定記録が残る
            self.assertIn("spec-route", (cfg.decisions / "T1.md").read_text(encoding="utf-8"))

    def test_route_skips_low_score_and_explicit_direct(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks, created = self._routed(d, assess="c=1 r=1 a=1")
            self.assertEqual(created, [])                    # しきい値未満は素通り
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            (d / "backlog").mkdir(parents=True)
            (d / "backlog" / "T1.md").write_text(
                "## T1: 危ない機能\n- status: ready\n- verify: `true`\n"
                "- assess: c=3 r=3 a=3\n- route: direct\n", encoding="utf-8")
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km.route_spec_tasks(cfg, tasks, km.Policy()), [])  # 人の明示が勝つ

    def test_policy_spec_forces_routing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            mkb(d, "T1", title="deploy prod")
            tasks = km.load_tasks(cfg.backlog)
            created = km.route_spec_tasks(cfg, tasks, km.parse_policy("spec: prod\n"))
            self.assertEqual(len(created), 1)                # 採点に依らず policy が強制

    def test_spec_track_off_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                                 # spec_track 既定 off
            mkb(d, "T1")
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km.route_spec_tasks(cfg, tasks, km.Policy()), [])
            self.assertEqual(km.expand_spec_tasks(cfg, tasks), [])

    def _expanded_setup(self, d, tasks_md, spec_status="done"):
        cfg = cfg_for(d, spec_track=True)
        (d / "backlog").mkdir(parents=True, exist_ok=True)
        (d / "backlog" / "T1.md").write_text(
            "## T1: 大きめの機能\n- status: ready\n- verify: `true`\n"
            "- route: spec\n- spec_task: T1-spec\n- after: T1-spec\n", encoding="utf-8")
        adir = cfg.archive_dir()
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "T1-spec.md").write_text(
            f"## T1-spec: Spec 作成: 大きめの機能\n- status: {spec_status}\n", encoding="utf-8")
        if tasks_md is not None:
            sdir = km.specs_root(cfg) / "T1"
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "tasks.md").write_text(tasks_md, encoding="utf-8")
        return cfg, km.load_tasks(cfg.backlog)

    def test_expand_after_spec_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks = self._expanded_setup(d, (
                '# 実装タスク\n\n```json\n'
                '[{"title": "モデルを作る", "verify": "test -f m.py"},\n'
                ' {"title": "API を作る", "verify": "test -f api.py", "after": ["モデルを作る"]}]\n'
                '```\n'))
            created = km.expand_spec_tasks(cfg, tasks)
            self.assertEqual(len(created), 2)
            m = next(t for t in created if "モデル" in t.title)
            a = next(t for t in created if "API" in t.title)
            self.assertEqual(a.get("after"), m.id)           # 配列内 after（title）→ id 解決
            self.assertEqual(m.get("spec"), "T1")            # spec 文脈注入のタグ
            t1 = km.load_tasks(cfg.backlog)
            t1 = next(x for x in t1 if x.id == "T1")
            self.assertEqual(set(km.task_deps(t1)), {m.id, a.id})  # 総合検証として最後に走る
            self.assertEqual(t1.get("spec_expanded"), "2")
            # 冪等: 2 回目は展開しない
            tasks2 = km.load_tasks(cfg.backlog)
            self.assertEqual(km.expand_spec_tasks(cfg, tasks2), [])

    def test_expand_skips_rejected_spec(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks = self._expanded_setup(
                d, '[{"title": "x", "verify": "true"}]', spec_status="rejected")
            self.assertEqual(km.expand_spec_tasks(cfg, tasks), [])   # 却下は展開しない

    def test_expand_without_json_marks_none(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, tasks = self._expanded_setup(d, "JSON の無い自由文\n")
            self.assertEqual(km.expand_spec_tasks(cfg, tasks), [])
            t1 = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            self.assertEqual(t1.get("spec_expanded"), "none")  # 元タスクが spec 文脈で自力実装

    def test_build_request_injects_spec_instructions_and_context(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            # spec 作成タスク → 3 ファイルの作成指示が載る
            s = km.Task(id="T1-spec", title="Spec 作成: 大きめの機能", verify="true")
            s.extra += [("spec_for", "T1"), ("note", "対象タスク T1: 大きめの機能")]
            req = km.build_request(s, cfg)
            self.assertIn("specs/T1/spec.md", req)
            self.assertIn("tasks.md", req)
            self.assertIn("実装はしない", req.replace("コードの実装はしない", "実装はしない"))
            # 実装タスク → spec.md/design.md が文脈注入される
            sdir = km.specs_root(cfg) / "T1"
            sdir.mkdir(parents=True)
            (sdir / "spec.md").write_text("# 要求仕様\nログイン必須\n", encoding="utf-8")
            (sdir / "design.md").write_text("# 設計\nJWT を使う\n", encoding="utf-8")
            impl = km.Task(id="I1", title="API を作る", verify="true")
            impl.extra.append(("spec", "T1"))
            req = km.build_request(impl, cfg)
            self.assertIn("ログイン必須", req)
            self.assertIn("JWT を使う", req)
            # spec の無い通常タスクは従来のまま
            plain = km.Task(id="P1", title="通常", verify="true")
            self.assertNotIn("仕様（spec 前段の成果", km.build_request(plain, cfg))

    def test_spec_task_pins_local_and_swaps_delegating_executor(self):
        # spec 作成タスクは委譲しない: location は常に local、gitlab executor は agent へ差し替え
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True, executor="gitlab",
                          location="remote", git_bus="https://example.com/bus.git")
            s = km.Task(id="T1-spec", title="Spec 作成: x", verify="true")
            s.extra.append(("spec_for", "T1"))
            self.assertEqual(km.decide_location(s, km.Policy(offload=["Spec"]), cfg), "local")
            cmd = km.build_agent_flow_cmd(s, cfg)
            self.assertEqual(cmd[cmd.index("--executor") + 1], "agent")
            self.assertNotIn("--max-retries", cmd)   # 委譲用の即失敗化（0 固定）は付かない
            # 通常タスクは従来どおり（gitlab のまま・却下は即失敗化）
            t = km.Task(id="T2", title="通常", verify="true")
            cmd2 = km.build_agent_flow_cmd(t, cfg)
            self.assertEqual(cmd2[cmd2.index("--executor") + 1], "gitlab")
            self.assertIn("--max-retries", cmd2)

    def test_run_setup_routes_in_loop(self):
        # run_loop の S0 でルーティングが効く（spec タスクが前置され T1 は依存待ちで走らない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True, max_cycles=1, learn=False, auto_adjudicate=False)
            (d / "backlog").mkdir(parents=True)
            (d / "backlog" / "T1.md").write_text(
                "## T1: 大きめの機能\n- status: ready\n- verify: `true`\n"
                "- assess: c=3 r=1 a=1\n", encoding="utf-8")
            km.run_loop(cfg)
            ids = {t.id for t in km.load_tasks(cfg.backlog)}
            self.assertIn("T1-spec", ids)
            self.assertIn("T1", ids)                          # T1 は消化されず残る（依存待ち）


class PlanAfterTests(unittest.TestCase):
    """plan 分解が after（先行タスクの title）を出し、enqueue 側で id へ決定的に解決される。
    未知 title は落とす・循環は捨てる（DAG の健全性が優先）。"""

    def _charter(self, d):
        (d / "charter.md").write_text(
            "# Charter: demo\n## goal\nCLI を作る\n## acceptance\n- `true`\n", encoding="utf-8")
        return km.parse_charter((d / "charter.md").read_text(encoding="utf-8"))

    def test_plan_via_agent_captures_after_titles(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            charter = self._charter(d)
            out = ('[{"title": "モデルを作る", "verify": "test -f m.py"},'
                   ' {"title": "API を作る", "verify": "test -f api.py",'
                   '  "after": ["モデルを作る"]}]')
            with mock.patch.object(km, "_run_agent_cli", return_value=out):
                specs = km.plan_via_agent(cfg, charter)
            self.assertEqual(specs[1]["after_titles"], ["モデルを作る"])
            self.assertNotIn("after_titles", specs[0].get("after_titles") or [])

    def test_enqueue_resolves_titles_to_ids(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            specs = [{"title": "モデルを作る", "verify": "true"},
                     {"title": "API を作る", "verify": "true",
                      "after_titles": ["モデルを作る", "存在しないタスク"]}]
            created = km._enqueue_specs(cfg, specs, [], 0.9)
            self.assertEqual(len(created), 2)
            m, a = created
            self.assertEqual(a.get("after"), m.id)          # title → id・未知 title は落ちる
            self.assertNotIn("after_titles", dict(a.extra))  # 生 title はタスクに書かない
            self.assertIsNone(m.get("after"))

    def test_enqueue_drops_cyclic_after(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            specs = [{"title": "A を作る", "verify": "true", "after_titles": ["B を作る"]},
                     {"title": "B を作る", "verify": "true", "after_titles": ["A を作る"]}]
            created = km._enqueue_specs(cfg, specs, [], 0.9)
            a, b = created
            # 片方（後から解決した方）の after は循環になるため捨てられる
            deps_total = [x for t in (a, b) for x in km.task_deps(t)]
            self.assertEqual(len(deps_total), 1)
            journal = cfg.journal.read_text(encoding="utf-8")
            self.assertIn("循環のため破棄", journal)

    def test_coerce_titles_keeps_spaces(self):
        self.assertEqual(km._coerce_titles(["モデルを作る", " API を作る "]),
                         ["モデルを作る", "API を作る"])
        self.assertEqual(km._coerce_titles("モデルを作る, API を作る"),
                         ["モデルを作る", "API を作る"])   # 文字列はカンマ区切りのみ（空白は保持）
        self.assertEqual(km._coerce_titles(None), [])

    def test_plan_prompt_mentions_after(self):
        with tempfile.TemporaryDirectory() as d:
            charter = self._charter(Path(d))
            self.assertIn('"after"', km._plan_decompose_prompt(charter))


class RepoMapTests(unittest.TestCase):
    """リポジトリ理解の成果物化（context/<repo名>.md・opt-in repo_map）。
    生成は sha キャッシュで律速され、読み出し（注入）は常時効くことを検証する。"""

    def _write_map(self, cfg, name, body, sha="abc123"):
        cdir = km.context_dir(cfg)
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{name}.md").write_text(
            f"<!-- head: {sha} -->\n# リポジトリ理解: {name}\n\n{body}\n", encoding="utf-8")

    def test_repo_map_context_reads_and_filters(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self.assertEqual(km.repo_map_context(cfg), "")        # 無ければ空（従来動作）
            self._write_map(cfg, "app", "src/ 配下が本体。pytest で検証。")
            self._write_map(cfg, "docs", "mkdocs 構成。")
            all_ctx = km.repo_map_context(cfg)
            self.assertIn("pytest", all_ctx)
            self.assertIn("mkdocs", all_ctx)
            self.assertNotIn("<!-- head:", all_ctx)               # 署名マーカーは注入しない
            only = km.repo_map_context(cfg, ["app"])
            self.assertIn("pytest", only)
            self.assertNotIn("mkdocs", only)

    def test_ensure_repo_maps_caches_by_sha(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, repo_map=True, executor="kiro")
            charter = km.parse_charter(
                "# Charter: demo\n## goal\nx\n## repos\n"
                "- app = https://example.com/app.git\n  - owns: src/**\n")
            calls = []

            def gen(c, spec):
                calls.append(spec["url"])
                return "生成された理解"

            with mock.patch.object(km, "_repo_head_sha", return_value="abc123"), \
                 mock.patch.object(km, "_repo_map_generate", side_effect=gen):
                km.ensure_repo_maps(cfg, charter)
                km.ensure_repo_maps(cfg, charter)                 # 同一 sha → 再生成しない
            self.assertEqual(len(calls), 1)
            text = (km.context_dir(cfg) / "app.md").read_text(encoding="utf-8")
            self.assertIn("<!-- head: abc123 -->", text)
            self.assertIn("生成された理解", text)
            # sha が変わったら再生成
            with mock.patch.object(km, "_repo_head_sha", return_value="def456"), \
                 mock.patch.object(km, "_repo_map_generate", side_effect=gen):
                km.ensure_repo_maps(cfg, charter)
            self.assertEqual(len(calls), 2)

    def test_ensure_repo_maps_off_or_stub_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            charter = km.parse_charter(
                "# Charter: demo\n## goal\nx\n## repos\n- app = https://example.com/app.git\n")
            with mock.patch.object(km, "_repo_map_generate",
                                   side_effect=AssertionError("生成してはいけない")):
                km.ensure_repo_maps(cfg_for(d), charter)                      # repo_map 既定 off
                km.ensure_repo_maps(cfg_for(d, repo_map=True), charter)       # executor=stub
            self.assertFalse(km.context_dir(cfg_for(d)).exists())

    def test_build_request_injects_repo_map(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._write_map(cfg, "app", "src/ 配下が本体。pytest で検証。")
            t = km.Task(id="T1", title="機能を足す", verify="true")
            req = km.build_request(t, cfg)
            self.assertIn("リポジトリ理解", req)
            self.assertIn("pytest", req)
            # workspace 指定タスクは該当 repo 分だけ
            t2 = km.Task(id="T2", title="別の作業", verify="true")
            t2.extra.append(("workspace", "docs"))
            self.assertNotIn("pytest", km.build_request(t2, cfg))

    def test_plan_prompt_carries_context(self):
        charter = km.parse_charter("# Charter: demo\n## goal\nx\n")
        p = km._plan_decompose_prompt(charter, context="src/ 配下が本体")
        self.assertIn("リポジトリ理解", p)
        self.assertIn("src/ 配下が本体", p)
        self.assertNotIn("リポジトリ理解", km._plan_decompose_prompt(charter))


class NodeLocalRepoTests(unittest.TestCase):
    """S3: ノード固有のローカルクローン宣言（host.yaml `repos[]`）。

    `local` は「この PC のどこにクローンがあるか」で、共有 repos.json は charter から
    自動生成され状態リポジトリ経由で全 PC へ配られる——書くと 1 台のパスが全ノードへ伝播する
    （C3/C4 の元凶）。宣言は host.yaml だけに置き、伝搬はそこからの解決で行う。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kp-nodelocal-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.clone = self.tmp / "mirrors" / "app"
        self.clone.mkdir(parents=True)
        km._warned_registry_local = False
        self.addCleanup(setattr, km, "_warned_registry_local", False)

    def test_registry_local_is_ignored_with_a_warning(self):
        # 共有レジストリの local は spec に載せない（載せると全 PC へ配られる）。
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            spec = km._registry_entry("app", {"url": "https://h/t/app.git",
                                              "local": "/somebody-elses/path",
                                              "owns": ["**"]})
        self.assertNotIn("local", spec)
        msg = err.getvalue()
        self.assertIn("local", msg)
        self.assertIn("host.yaml", msg, "移行先を示す")

    def test_registry_local_warning_is_emitted_once(self):
        # レジストリはルーティングのたびに読まれるので、毎回警告すると出力が埋まる。
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                km._registry_entry("app", {"url": "https://h/t/app.git", "local": "/x"})
        self.assertEqual(err.getvalue().count(">>> 警告"), 1)

    def test_workspace_token_fills_local_from_node_declaration(self):
        use_host_config(self, {"repos": [{"url": "https://h/t/app.git",
                                          "local": str(self.clone)}]},
                        home=self.tmp / "agents-home")
        token = json.loads(km._workspace_token({"url": "https://h/t/app.git", "base": "main"}))
        self.assertEqual(token["local"], str(self.clone))
        self.assertEqual(token["base"], "main")

    def test_workspace_token_omits_local_when_not_declared(self):
        use_host_config(self, {"repos": []}, home=self.tmp / "agents-home")
        token = json.loads(km._workspace_token({"url": "https://h/t/other.git"}))
        self.assertNotIn("local", token)

    def test_resolve_local_repo_uses_the_shared_resolver(self):
        use_host_config(self, {"repos": [{"url": "https://h/t/app", "local": str(self.clone)}]},
                        home=self.tmp / "agents-home")
        # 末尾 .git の揺れを吸収して同じ実体に解決する（agentcore.repolocal の正規化）。
        self.assertEqual(km.resolve_local_repo("https://h/t/app.git"), self.clone)
        self.assertIsNone(km.resolve_local_repo("https://h/t/nope.git"))
