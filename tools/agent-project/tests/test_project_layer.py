"""agent-project の単体テスト — project_layer（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _drained  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class TestProjectLayer(unittest.TestCase):
    def test_parse_charter(self):
        ch = km.parse_charter(CHARTER.replace("{flag}", "x"))
        self.assertEqual(ch.name, "demo")
        self.assertIn("CSV", ch.goal)
        self.assertEqual(ch.constraints, ["標準ライブラリのみ"])
        self.assertEqual(ch.deliverables, ["report.py"])
        self.assertEqual(ch.acceptance, ["test -f x"])

    def test_parse_charter_repos(self):
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n- https://git/lib.git\n")
        self.assertEqual(ch.repos, ["app = https://git/app.git", "https://git/lib.git"])
        rmap = km.charter_repo_map(ch)
        self.assertEqual(rmap["app"], "https://git/app.git")     # name 引き
        self.assertEqual(rmap["lib"], "https://git/lib.git")     # URL 末尾を name に
        self.assertEqual(rmap["https://git/app.git"], "https://git/app.git")  # URL 引き

    def test_parse_charter_repos_structured(self):
        # 構造化 repos: name=url ＋ desc/base/target（target 省略時は base）
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n"
            "  - desc: アプリ本体（API/UI）\n"
            "  - base: main\n"
            "  - target: develop\n"
            "- lib = https://git/lib.git\n"
            "  - 説明: 共有ライブラリ\n"
            "  - ベース: release\n")
        self.assertEqual(ch.repos, ["app = https://git/app.git", "lib = https://git/lib.git"])
        a, b = ch.repo_specs
        self.assertEqual((a["name"], a["url"], a["desc"], a["base"], a["target"]),
                         ("app", "https://git/app.git", "アプリ本体（API/UI）", "main", "develop"))
        # 日本語キー・target 省略（既定 base）
        self.assertEqual((b["name"], b["desc"], b["base"], b["target"]),
                         ("lib", "共有ライブラリ", "release", "release"))
        # charter_repo_map は従来どおり name/url 解決できる
        self.assertEqual(km.charter_repo_map(ch)["app"], "https://git/app.git")

    def test_validate_charter_requires_desc_and_base(self):
        ok = km.parse_charter("# Charter: r\n## goal\nx\n## repos\n"
                              "- app = u\n  - desc: d\n  - base: main\n")
        self.assertEqual(km.validate_charter(ok), [])
        bad = km.parse_charter("# Charter: r\n## goal\nx\n## repos\n- app = u\n")
        probs = km.validate_charter(bad)
        self.assertEqual(len(probs), 2)                  # desc と base の両方
        self.assertTrue(any("desc" in p or "説明" in p for p in probs))
        self.assertTrue(any("base" in p for p in probs))

    def test_charter_definition_renders_base_target_desc(self):
        ch = km.parse_charter("# Charter: r\n## goal\nやる\n## repos\n"
                              "- app = https://git/app.git\n  - desc: 本体\n  - base: main\n  - target: develop\n"
                              "## links\n- https://wiki/x — 仕様\n  - desc: 仕様メモ\n")
        d = km._charter_definition(ch)
        self.assertIn("base=main", d)
        self.assertIn("target=develop", d)
        self.assertIn("本体", d)
        self.assertIn("仕様メモ", d)

    def test_parse_charter_repos_path(self):
        # path 属性（モノレポ作業フォルダ）。日本語別名・先頭/末尾スラッシュ除去も確認
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api/\n  - 説明: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - フォルダ: /apps/web\n  - 役割: 画面\n  - base: main\n")
        a, b = ch.repo_specs
        self.assertEqual((a["path"], a["desc"]), ("apps/api", "API"))
        self.assertEqual((b["path"], b["desc"]), ("apps/web", "画面"))   # 役割=desc 別名

    def test_validate_charter_monorepo_requires_distinct_path(self):
        # 同一 URL を役割分割するなら distinct な path で区別できる
        ok = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api\n  - desc: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - path: apps/web\n  - desc: 画面\n  - base: main\n")
        self.assertEqual(km.validate_charter(ok), [])
        # path も branch も全て一致 → 曖昧な重複として弾く
        dupall = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - desc: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - desc: 画面\n  - base: main\n")
        self.assertTrue(any("重複" in p for p in km.validate_charter(dupall)))
        # path 重複（同一フォルダ・同一ブランチ）→ 問題
        dup = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/x\n  - desc: API\n  - base: main\n"
            "- web = https://git/shop.git\n  - path: apps/x\n  - desc: 画面\n  - base: main\n")
        self.assertTrue(any("重複" in p for p in km.validate_charter(dup)))
        # 単独エントリは path 任意（後方互換）
        single = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n- app = u\n  - desc: d\n  - base: main\n")
        self.assertEqual(km.validate_charter(single), [])

    def test_validate_charter_distinguishes_same_url_by_branch(self):
        # 同一 URL・path 無しでも base（ブランチ）が違えば別エントリとして成立する
        bybase = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app-main = https://git/app.git\n  - desc: 本流\n  - base: main\n"
            "- app-rel = https://git/app.git\n  - desc: backport\n  - base: release/1.x\n")
        self.assertEqual(km.validate_charter(bybase), [])
        # 同一 URL・同一 path でも target（PR 先ブランチ）が違えば成立する
        bytarget = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- a = https://git/app.git\n  - path: svc\n  - desc: develop 向け\n"
            "  - base: main\n  - target: develop\n"
            "- b = https://git/app.git\n  - path: svc\n  - desc: main 向け\n  - base: main\n")
        self.assertEqual(km.validate_charter(bytarget), [])

    def test_charter_definition_renders_path(self):
        ch = km.parse_charter(
            "# Charter: r\n## goal\nやる\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api\n  - desc: API\n  - base: main\n")
        d = km._charter_definition(ch)
        self.assertIn("path=apps/api", d)
        self.assertIn("API", d)

    def test_build_charter_request_lists_path_and_role(self):
        # プランナー提示にフォルダ(path)と役割(desc)が載る
        ch = km.parse_charter(
            "# Charter: r\n## goal\nやる\n## repos\n"
            "- api = https://git/shop.git\n  - path: apps/api\n  - desc: APIロジック\n  - base: main\n")
        req = km.build_charter_request(ch)
        self.assertIn("apps/api", req)
        self.assertIn("APIロジック", req)
        self.assertIn("api = https://git/shop.git", req)

    def test_parse_charter_repos_owns_marks_reference(self):
        # owns: があれば書込先候補（readonly False）。owns 未指定は参照リポジトリ（readonly True）。
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- a = u1\n  - owns: apps/api/**\n  - desc: d\n  - base: main\n"
            "- b = u2\n  - desc: d\n  - base: main\n"
            "- c = u3\n  - readonly: true\n  - owns: x/**\n  - desc: d\n  - base: main\n")
        a, b, c = ch.repo_specs
        self.assertEqual(a["owns"], ["apps/api/**"])
        self.assertFalse(a["readonly"])     # owns 有り → 書込先候補
        self.assertEqual(b["owns"], [])
        self.assertTrue(b["readonly"])      # owns 未指定 → 参照リポジトリ
        self.assertTrue(c["readonly"])      # readonly 明示は owns 有りでも参照

    def test_resolve_workspace_explicit_and_owns_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: apps/api/**\n  - path: apps/api\n"
                          "  - base: main\n  - target: develop\n  - desc: API\n"
                          "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n"
                          "- docs = https://git/docs.git\n  - desc: 参照元\n  - base: main\n")
            cfg = cfg_for(d, route_planner="none")
            pol = km.Policy()
            # 1. 明示 - workspace:
            t = km.Task(id="T1", title="x", extra=[("workspace", "lib")])
            spec, by = km.resolve_workspace(cfg, t, pol)
            self.assertEqual((spec["name"], by), ("lib", "explicit"))
            # 2. route: ルール（パターンはタイトル/ID の部分一致）
            pol2 = km.Policy(route=["API -> app"])
            spec, by = km.resolve_workspace(cfg, km.Task(id="T2", title="API 改修"), pol2)
            self.assertEqual((spec["name"], by), ("app", "rule"))
            # 3. owns: パス推定（- paths: ヒント）
            t3 = km.Task(id="T3", title="z", extra=[("paths", "packages/util.py")])
            spec, by = km.resolve_workspace(cfg, t3, pol)
            self.assertEqual((spec["name"], by), ("lib", "owns"))
            # 4. 既定ワークスペース（決まらないとき）
            cfg2 = cfg_for(d, route_planner="none", default_workspace="app")
            spec, by = km.resolve_workspace(cfg2, km.Task(id="T4", title="謎"), km.Policy())
            self.assertEqual((spec["name"], by), ("app", "default"))
            # docs は owns 無し → 参照リポジトリ（書込先候補にならない）
            docs = km.charter_repo_spec_map(km.load_charter(cfg))["docs"]
            self.assertTrue(km._is_reference_repo(docs))

    def test_resolve_workspace_persists_decision(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: **\n  - base: main\n")
            cfg = cfg_for(d, route_planner="none")
            (cfg.backlog).mkdir(parents=True, exist_ok=True)
            t = km.Task(id="T1", title="x", verify="true")
            km.persist_task(cfg, t)
            km.resolve_and_persist_workspace(cfg, t, km.Policy())
            reloaded = km.parse_task((cfg.backlog / "T1.md").read_text(), "T1")
            self.assertEqual(reloaded.get("workspace"), "app")   # 決定を md へ書き戻す
            self.assertEqual(reloaded.get("routed_by"), "sole")

    def test_explicit_workspace_accepts_unique_repo_url_basename_and_canonicalizes_it(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "repos.json").write_text(json.dumps({
                "src": {"url": "https://github.com/example/sandbox", "base": "main"},
            }), encoding="utf-8")
            cfg = cfg_for(d, route_planner="none")
            cfg.backlog.mkdir(parents=True, exist_ok=True)
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "sandbox")])
            km.persist_task(cfg, t)

            spec = km.resolve_and_persist_workspace(cfg, t, km.Policy())

            self.assertEqual(spec["name"], "src")
            reloaded = km.parse_task((cfg.backlog / "T1.md").read_text(), "T1")
            self.assertEqual(reloaded.get("workspace"), "src")
            self.assertEqual(reloaded.get("routed_by"), "explicit-alias")
            cmd = km.build_agent_flow_cmd(reloaded, cfg)
            self.assertIn("--workspace", cmd)

    def test_explicit_workspace_repo_url_basename_must_be_unique(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "repos.json").write_text(json.dumps({
                "one": {"url": "https://github.com/example/sandbox", "base": "main"},
                "two": {"url": "https://gitlab.example.com/example/sandbox.git", "base": "main"},
            }), encoding="utf-8")
            cfg = cfg_for(d, route_planner="none")
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "sandbox")])
            spec, routed_by = km.resolve_workspace(cfg, t, km.Policy())
            self.assertIsNone(spec)
            self.assertEqual(routed_by, "none")

    def test_workspace_token_json(self):
        # url/path/base/target/desc を JSON で構造化（readonly/name は載せない）
        tok = km._workspace_token({"name": "api", "url": "https://git/shop.git", "desc": "API",
                                   "base": "main", "target": "develop", "path": "apps/api"})
        obj = json.loads(tok)
        self.assertEqual((obj["url"], obj["path"], obj["base"], obj["target"]),
                         ("https://git/shop.git", "apps/api", "main", "develop"))
        self.assertNotIn("name", obj)
        self.assertNotIn("readonly", obj)

    def test_workspace_propagated_to_agent_flow(self):
        # 解決済み - workspace: が --workspace の JSON トークンとして agent-flow へ伝搬する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- api = https://git/shop.git\n  - owns: apps/api/**\n  - path: apps/api\n"
                          "  - base: main\n  - target: develop\n")
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "api")])
            cmd = km.build_agent_flow_cmd(t, cfg)
            self.assertNotIn("--repo", cmd)
            obj = json.loads(cmd[cmd.index("--workspace") + 1])
            self.assertEqual((obj["path"], obj["base"], obj["target"]), ("apps/api", "main", "develop"))

    def test_charter_renders_readonly(self):
        ch = km.parse_charter("# Charter: r\n## goal\nやる\n## repos\n"
                              "- lib = https://git/lib.git\n  - readonly: true\n  - desc: 参照元\n  - base: main\n")
        self.assertIn("参照のみ", km._charter_definition(ch))
        self.assertIn("参照のみ", km.build_charter_request(ch))

    def test_cmd_project_errors_on_invalid_repos(self):
        # desc/base 欠落の repos を持つ charter は cmd_project がエラー停止（return 2）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: X\n## goal\nやる\n## acceptance\n- true\n"
                             "## repos\n- app = https://git/app.git\n")
            self.assertEqual(km.cmd_project(cfg_for(d)), 2)

    def test_reference_repos_passed_as_structured_args(self):
        # owns 無し（参照リポジトリ）は --reference として構造化伝搬する（分解後の各ノード/gitlab
        # イシューにも届くように。要求本文へは畳まない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: **\n  - base: main\n"
                          "- spec = https://git/spec.git\n  - desc: API 仕様\n  - base: main\n")
            cfg = cfg_for(d)
            refs = km.task_reference_specs(cfg, km.Task(id="T1", title="x"))
            self.assertEqual([s["name"] for s in refs], ["spec"])      # owns 無しだけ参照に
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "app")])
            cmd = km.build_agent_flow_cmd(t, cfg)
            # --reference の値だけを集める（書込先 app は参照に含めない）
            ref_vals = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--reference"]
            self.assertEqual([json.loads(v)["url"] for v in ref_vals], ["https://git/spec.git"])
            self.assertFalse(any("app.git" in v for v in ref_vals))
            # 要求本文へは畳まない（構造化伝搬に一本化）
            self.assertNotIn("参照用リポジトリ", km.build_request(t, cfg))

    def test_workspace_only_propagated_when_resolved(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: **\n  - base: main\n")
            cfg = cfg_for(d)
            t = km.Task(id="T1", title="x", verify="true", extra=[("workspace", "app")])
            cmd = km.build_agent_flow_cmd(t, cfg)
            self.assertIn("--workspace", cmd)
            self.assertIn("https://git/app.git", cmd[cmd.index("--workspace") + 1])
            # 未解決（- workspace: 無し）のタスクは --workspace を付けない＝読み取り専用 run
            self.assertNotIn("--workspace", km.build_agent_flow_cmd(km.Task(id="T2", title="y"), cfg))

    def test_assign_plan_workspace_from_verify_paths(self):
        # plan が生成したタスクは、verify が操作するパスの owns を持つ repo を書込先にする
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n  - owns: apps/app/**\n  - base: main\n"
            "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n"
            "- spec = https://git/spec.git\n  - desc: 仕様（参照）\n  - base: main\n")
        sp = km.assign_plan_workspace(ch, {"title": "型を追加",
                                           "verify": "test -f packages/types.ts"})
        self.assertEqual(sp["workspace"], "lib")            # owns packages/** に一致 → lib が書込先
        self.assertIn("app", sp["refs"]); self.assertIn("spec", sp["refs"])  # 他は参照
        self.assertNotIn("lib", sp["refs"].split(","))      # 書込先は参照に含めない
        self.assertNotIn("repos", sp)                       # repos は廃止

    def test_assign_plan_workspace_respects_owning_hint(self):
        # プランナーが付けた workspace（owns 持ち）は尊重。owns を持たない指定は無視して推定に倒す
        ch = km.parse_charter(
            "# Charter: r\n## goal\nx\n## repos\n"
            "- app = https://git/app.git\n  - owns: apps/app/**\n  - base: main\n"
            "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n")
        sp = km.assign_plan_workspace(ch, {"title": "t", "verify": "test -f packages/x",
                                           "workspace": "app"})
        self.assertEqual(sp["workspace"], "app")            # プランナー指定（owns 持ち）を尊重
        sp2 = km.assign_plan_workspace(ch, {"title": "t", "verify": "test -f packages/x",
                                            "workspace": "spec"})  # owns 無し指定は無効
        self.assertEqual(sp2["workspace"], "lib")           # → verify パスの owns で確定

    def test_plan_via_agent_sets_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: apps/app/**\n  - base: main\n"
                          "- lib = https://git/lib.git\n  - owns: packages/**\n  - base: main\n")
            cfg = cfg_for(d)
            ch = km.load_charter(cfg)
            orig = km._run_agent_cli
            km._run_agent_cli = lambda prompt, model, purpose="": (
                '[{"title":"lib に型追加","paths":["packages/t.ts"]}]')
            try:
                specs = km.plan_via_agent(cfg, ch)
            finally:
                km._run_agent_cli = orig
            self.assertEqual(specs[0]["workspace"], "lib")  # paths=packages/** → lib（必ず明示される）

    def test_plan_via_stub_enqueues_charter_acceptance(self):
        # executor: stub の既定 planner（plan_via_stub）は _run_agent_cli を一切呼ばず、charter の
        # acceptance をそのまま初期タスクにする。verify は人が書いた受入条件そのもの。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"                      # 存在しない → acceptance 未達
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)                       # executor="stub"（cfg_for の既定）
            ch = km.load_charter(cfg)
            specs = km.plan_via_stub(cfg, ch)
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0]["verification_commands"], [f"test -f {flag}"])
            self.assertIn("受入条件を満たす", specs[0]["title"])

    def test_plan_via_stub_enqueues_even_when_acceptance_already_passes(self):
        # 回帰: 初回から PASS する acceptance（`echo ok` 等）でも起票する。plan は未達判定の場では
        # ない（それは evaluate の役目）。かつては acceptance をその場で実行して未達だけを起票して
        # いたため、こういう charter ではバックログが空のまま converged し、viewer で「バージョンを
        # 足してもバックログが現れない」ように見えていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("test -f {flag}", 'echo "hellO"'))
            cfg = cfg_for(d)
            ch = km.load_charter(cfg)
            specs = km.plan_via_stub(cfg, ch)      # PASS する条件でも初回は起票する
            self.assertEqual([s["verification_commands"] for s in specs], [['echo "hellO"']])

    def test_stub_plan_is_idempotent_across_cycles(self):
        # 常に起票する planner でも、同じ受入条件が積み直されないこと（_enqueue_specs が backlog と
        # archive のタイトルで冪等に弾く）。分解は明示要求（replan マーカー）でしか走らないので、
        # 各パスの前に要求を立てる——2 回要求しても積み増さないのが冪等の担保。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("test -f {flag}", 'echo "hellO"'))
            cfg = cfg_for(d, max_project_cycles=1)
            km.write_replan_request(cfg, "分解")
            km.cmd_project(cfg, runner=lambda c: _drained())
            first = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertEqual(len(first), 1)
            km.write_replan_request(cfg, "分解")
            km.cmd_project(cfg, runner=lambda c: _drained())   # 2 回目の分解要求は積み増さない
            self.assertEqual([t.title for t in km.load_tasks(cfg.backlog)], first)

    def test_cmd_project_stub_executor_never_calls_agent_for_planning(self):
        # 実運用インシデントの再発防止: .agent/agent-project.yaml で --planner none / --executor stub
        # を設定しても、charter があると run/watch は自動で cmd_project（charter 駆動）に入り、
        # 従来はその既定 plan_fn が黙って plan_via_agent（実エージェント呼び出し）を使っていた。
        # executor: stub では plan_via_stub に切り替わり、エージェントを一切呼ばないことを保証する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"                      # 存在しない → acceptance 未達のまま
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, max_project_cycles=1)  # executor="stub"（既定）。planner は注入しない
            km.write_replan_request(cfg, "分解")     # 分解は明示要求でしか走らない
            orig = km._run_agent_cli

            def _boom(prompt, model):
                raise AssertionError("stub モードなのにエージェント（_run_agent_cli）が呼ばれた")

            km._run_agent_cli = _boom
            try:
                km.cmd_project(cfg, runner=lambda c: _drained())
            finally:
                km._run_agent_cli = orig
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertTrue(any("受入条件を満たす" in t for t in titles))  # 決定的 stub planner の出力

    def test_cmd_project_agent_executor_still_uses_plan_via_agent(self):
        # 対の回帰テスト: executor が stub 以外（既定 agent 等）なら従来どおりエージェント委譲のまま。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, executor="agent", max_project_cycles=1)
            km.write_replan_request(cfg, "分解")     # 分解は明示要求でしか走らない
            calls = {"n": 0}
            orig = km._run_agent_cli

            def fake(prompt, model, purpose=""):
                calls["n"] += 1
                return '[{"title":"エージェント生成タスク","verify":"true"}]'

            km._run_agent_cli = fake
            try:
                km.cmd_project(cfg, runner=lambda c: _drained())
            finally:
                km._run_agent_cli = orig
            self.assertGreaterEqual(calls["n"], 1)
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("エージェント生成タスク", titles)

    def test_cmd_project_stub_executor_review_via_stub_skips_agent(self):
        # review_project=True（敵対的レビュー opt-in）でも executor: stub では review_via_stub
        # （常に所見なし）に切り替わり、エージェントを呼ばずに収束する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")   # acceptance 全 PASS（敵対的レビューの発火条件）
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, review_project=True, max_project_cycles=1)  # executor="stub"（既定）
            orig = km._run_agent_cli

            def _boom(prompt, model):
                raise AssertionError("stub モードなのにエージェント（_run_agent_cli）が呼ばれた")

            km._run_agent_cli = _boom
            try:
                km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            finally:
                km._run_agent_cli = orig
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(km.load_tasks(cfg.backlog), [])

    def test_plugin_executor_forwarded_to_agent_flow(self):
        # executor に agent-flow プラグイン名/パスを指定すると、そのまま agent-flow run へ委譲される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="gitlab")
            cmd = km.build_agent_flow_cmd(km.Task(id="T1", title="x", verify="true"), cfg)
            i = cmd.index("--executor")
            self.assertEqual(cmd[i + 1], "gitlab")
            cfg2 = cfg_for(d, executor="/path/to/my_executor.py")
            cmd2 = km.build_agent_flow_cmd(km.Task(id="T2", title="y"), cfg2)
            self.assertEqual(cmd2[cmd2.index("--executor") + 1], "/path/to/my_executor.py")

    def test_cli_accepts_plugin_executor(self):
        # CLI の --executor は choices で縛らず、プラグイン名をそのまま受理する（dry-run で act はしない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(Path(d), "T1", title="x", verify="true")
            rc = km.main(["run", "--workdir", str(d), "--root", str(Path(d) / ".ka"),
                          "--planner", "none", "--flow-planner", "stub",
                          "--executor", "gitlab", "--dry-run"])
            self.assertEqual(rc, 0)

    def test_repos_spec_roundtrips_to_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.task_from_spec(cfg, {"title": "x", "verify": "true", "repos": ["app", "lib"]})
            self.assertEqual(t.get("repos"), "app,lib")
            t2 = km.parse_task(km.serialize_task(t), t.id)      # 永続化往復で保持
            self.assertEqual(t2.get("repos"), "app,lib")

    def test_run_autodetects_charter(self):
        # run は charter.md があれば自動で目標駆動になる（project サブコマンドは廃止・1プロセス統合）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proot = d / "demo-proj"
            proot.mkdir(parents=True)
            (proot / "charter.md").write_text(
                "# Charter: demo\n## goal\nやる\n## acceptance\n- `true`\n", encoding="utf-8")
            rc = km.main(["run", "--workdir", str(d), "--root", str(proot),
                          "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run",
                          "--max-project-cycles", "1"])
            self.assertEqual(rc, 1)                       # 収束候補→人待ち
            self.assertTrue((proot / "project.json").exists())
            # milestone id はプロジェクト名（ルートのディレクトリ名）が一次（charter 名でなく）
            self.assertTrue((proot / "needs" / "demo-proj.md").exists())

    def test_run_without_charter_is_plain_loop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            proot = d
            (proot / "backlog").mkdir(parents=True, exist_ok=True)
            (proot / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: ready\n- verify: `true`\n", encoding="utf-8")
            rc = km.main(["run", "--no-delivery-review", "--workdir", str(d), "--planner", "none",
                          "--flow-planner", "stub", "--executor", "stub", "--dry-run"])
            self.assertEqual(rc, 0)                       # charter 無し→従来の backlog ループで drained
            self.assertFalse((proot / "project.json").exists())

    def test_missing_charter_errors(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(km.cmd_project(cfg_for(d)), 2)

    def test_no_acceptance_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: X\n## goal\nやる\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 1)
            self.assertTrue((d / "needs" / "X.md").exists())

    def test_acceptance_kind_classifies(self):
        self.assertEqual(km._acceptance_kind("pytest -q tests/"), ("command", "pytest -q tests/"))
        self.assertEqual(km._acceptance_kind("test -f x && grep -q y z"),
                         ("command", "test -f x && grep -q y z"))
        # 明示の accept: 接頭辞 → 自然言語（接頭辞を剥がし、人の検収へ）
        self.assertEqual(km._acceptance_kind("accept: README に概要がある"),
                         ("human", "README に概要がある"))
        self.assertEqual(km._acceptance_kind("受入: 画面が表示される"),
                         ("human", "画面が表示される"))
        # 接頭辞なしの散文（全角句読点）も人の検収に倒す
        self.assertEqual(km._acceptance_kind("レポートに要約が出力される。"),
                         ("human", "レポートに要約が出力される。"))
        # 明示の 検収:/human: 接頭辞 → 人の検収項目（機械検証しない）
        self.assertEqual(km._acceptance_kind("検収: UI が崩れていない"),
                         ("human", "UI が崩れていない"))
        self.assertEqual(km._acceptance_kind("human: docs are easy to read"),
                         ("human", "docs are easy to read"))

    def test_classify_acceptance_splits_commands_criteria_human(self):
        # 自然文は人へ、コマンドだけを固定検証へ送る。LLM criterion は自動完了に使わない。
        ch = km.parse_charter("# Charter: x\n## goal\nやる\n## acceptance\n"
                              "- `test -f keep`\n- accept: README に概要がある\n"
                              "- 検収: UI が崩れていない\n")
        commands, criteria, human = km.classify_charter_acceptance(ch)
        self.assertEqual(commands, ["test -f keep"])
        self.assertEqual(criteria, [])
        self.assertEqual(human, ["README に概要がある", "UI が崩れていない"])

    def test_evaluate_acceptance_runs_only_deterministic_commands(self):
        # 自然文は人の検収へ送るため、ここでは明示された固定コマンドだけを実行・集計する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "keep").write_text("x")
            cfg = cfg_for(d)
            ch = km.parse_charter("# Charter: x\n## goal\nやる\n## acceptance\n"
                                  "- `test -f keep`\n- accept: README に概要がある\n")
            passed, total, results = km.evaluate_acceptance(cfg, ch,
                                                            agent_run=lambda p, m: self.fail(
                                                                "自然文を LLM 判定へ送ってはいけない"))
            self.assertEqual((passed, total), (1, 1))
            self.assertEqual(results[0][0], "test -f keep")

    def test_evaluate_acceptance_excludes_human_criteria_from_machine_total(self):
        # 自然文だけなら機械評価は 0 件。LLM が pass を発明できる経路を持たない。
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            ch = km.parse_charter("# Charter: x\n## goal\nやる\n## acceptance\n"
                                  "- accept: 曖昧で検証できない\n")
            passed, total, _results = km.evaluate_acceptance(cfg, ch,
                                                             agent_run=lambda p, m: self.fail(
                                                                 "自然文を LLM 判定へ送ってはいけない"))
            self.assertEqual((passed, total), (0, 0))

    def test_human_acceptance_converges_with_checklist(self):
        # 機械 acceptance + 検収項目 → 収束し、milestone に検収チェックリストが載る。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            flag.write_text("x")
            write_charter(d, "# Charter: hx\n## goal\nやる\n## acceptance\n"
                             f"- `test -f {flag}`\n- 検収: 画面が自然に見える\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: _drained(),
                                  agent_run=lambda p, m: self.fail("合成された"))
            self.assertEqual(code, 1)            # converged → 人の承認待ち
            state = km.load_project_state(cfg_for(d))
            self.assertEqual(state["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(state["human_acceptance"], ["画面が自然に見える"])
            needs = (d / "needs" / "hx.md").read_text(encoding="utf-8")
            self.assertIn("検収チェックリスト", needs)
            self.assertIn("- [ ] 画面が自然に見える", needs)

    def test_human_only_acceptance_does_not_dead_end(self):
        # 全条件が人の検収でも no-acceptance で塞がず、収束して人の承認ゲートへ進む。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: ho\n## goal\nやる\n## acceptance\n"
                             "- 検収: 使い勝手に問題がない\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: _drained(),
                                  agent_run=lambda p, m: self.fail("合成された"))
            self.assertEqual(code, 1)
            self.assertEqual(km.load_project_state(cfg_for(d))["status"],
                             km.REASON_PROJECT_CONVERGED)

    def test_natural_language_criterion_goes_to_human_checklist(self):
        # 自然文の達成条件は verifier に判定させず、そのまま人の検収票へ残す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: un\n## goal\nやる\n## acceptance\n"
                             "- accept: 曖昧な完了条件\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: _drained(),
                                  agent_run=lambda p, m: self.fail(
                                      "自然文を LLM 判定へ送ってはいけない"))
            self.assertEqual(code, 1)
            needs = (d / "needs" / "un.md").read_text(encoding="utf-8")
            self.assertIn("検収チェックリスト", needs)
            self.assertIn("曖昧な完了条件", needs)

    def test_natural_language_acceptance_waits_for_human_without_agent_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, "# Charter: nl\n## goal\nやる\n## acceptance\n"
                             f"- accept: flag ファイルが存在する\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: (flag.write_text("x"), _drained())[1],
                                  agent_run=lambda p, m: self.fail(
                                      "自然文の完了条件を LLM 判定へ送ってはいけない"))
            self.assertEqual(code, 1)            # converged → 人の承認待ち
            state = km.load_project_state(cfg_for(d))
            self.assertEqual(state["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(state["human_acceptance"], ["flag ファイルが存在する"])
            needs = (d / "needs" / "nl.md").read_text(encoding="utf-8")
            self.assertIn("検収チェックリスト", needs)

    def test_natural_language_acceptance_does_not_depend_on_agent_availability(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: nl\n## goal\nやる\n## acceptance\n"
                             "- accept: 曖昧な完了条件\n")
            code = km.cmd_project(cfg_for(d), planner=lambda ch: [],
                                  runner=lambda c: _drained(),
                                  agent_run=lambda p, m: self.fail(
                                      "自然文を LLM 判定へ送ってはいけない"))
            self.assertEqual(code, 1)
            needs = (d / "needs" / "nl.md").read_text(encoding="utf-8")
            self.assertIn("検収チェックリスト", needs)
            self.assertIn("曖昧な完了条件", needs)

    def test_plan_enqueues_then_converges(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            planned = {"n": 0}

            def planner(ch):
                planned["n"] += 1
                return [{"title": "成果物を作る", "verify": f"test -f {flag}"}]

            def runner(c):                      # 実行を模す: acceptance を満たすファイルを作る
                flag.write_text("x")
                return _drained()

            cfg = cfg_for(d)
            km.write_replan_request(cfg, "分解")  # 分解は明示要求でしか走らない
            code = km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(code, 1)           # converged → 人の承認待ち
            st = km.load_project_state(cfg_for(d))
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(planned["n"], 1)   # 1 回だけ plan（要求は one-shot）
            self.assertTrue((d / "needs" / "demo.md").exists())

    def test_charter_plan_signature_is_content_based(self):
        # 署名は「分解に効く内容」のハッシュ。同一内容は一致、goal 変更で変化、acceptance だけの
        # 変更では変化しない（acceptance は done 判定に効くが分解入力ではないため）。
        a = km.parse_charter("# Charter: x\n## goal\nやる\n## constraints\n- c1\n")
        a2 = km.parse_charter("# Charter: x\n## goal\nやる\n## constraints\n- c1\n")
        b = km.parse_charter("# Charter: x\n## goal\n別のことをやる\n## constraints\n- c1\n")
        c = km.parse_charter("# Charter: x\n## goal\nやる\n## constraints\n- c1\n"
                             "## acceptance\n- test -f z\n")
        self.assertEqual(km._charter_plan_signature(a), km._charter_plan_signature(a2))
        self.assertNotEqual(km._charter_plan_signature(a), km._charter_plan_signature(b))
        self.assertEqual(km._charter_plan_signature(a), km._charter_plan_signature(c))

    def test_charter_edit_does_not_replan_without_request(self):
        # charter を編集しても、明示の分解要求が無ければ再計画しない（分解は人の明示操作だけ）。
        # 旧仕様は署名比較で変更を検知して自動再計画していたが、人が整理したバックログを次パスが
        # 黙って作り直す原因になるため廃止した。編集を反映したいときは分解ボタン（replan）を押す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return [{"title": f"タスク{calls['n']}", "verify": f"test -f {flag}"}]

            def runner(c):                        # blocked で 1 サイクル抜ける（タスクは消化可能のまま残す）
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            # 1回目: 明示要求 → planner が呼ばれる
            km.write_replan_request(cfg, "分解")
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)
            self.assertTrue(any(t.consumable() for t in km.load_tasks(cfg.backlog)))  # 消化可能タスクが残る

            # charter の goal を変更しても、要求なしのパスでは再計画しない
            write_charter(d, CHARTER.replace("{flag}", str(flag)).replace(
                "CSV を要約する CLI を完成させる。", "JSON を要約する CLI を完成させる。"))
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)

            # 明示要求すれば、編集後の charter で再計画され差分が入る
            km.write_replan_request(cfg, "編集を反映")
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 2)
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("タスク2", titles)      # charter 差分が生む新規タスクが backlog に反映

    def test_replan_request_forces_redecompose_and_recreates_done(self):
        # エラー回復: 人が「charter から再分解」を要求すると、消化可能タスクが残り charter が
        # 無変更でも 1 回だけ plan を強制する。冪等照合は「現行処理中のバックログ」だけと行う:
        # 処理中タスクと類似は二重投入しないが、done/archive と類似はやり直しとして再作成を許す
        # （過去の完了実績が回復のための再分解を丸ごと弾き「押しても何も起きない」のを防ぐ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            mkb(d, "SEED", status="ready", verify="true",
                title="処理中の既存タスク")                        # 消化可能タスクを残す
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "OLD.md").write_text(
                "## OLD: 既存の done タスク\n- status: done\n- verify: `true`\n", encoding="utf-8")

            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                # done と同一タイトル（やり直し＝再作成される）＋処理中と同一タイトル（弾かれる）
                # ＋新規タイトル（取りこぼし＝入る）
                return [{"title": "既存の done タスク", "verify": "true"},
                        {"title": "処理中の既存タスク", "verify": "true"},
                        {"title": "取りこぼした新規タスク", "verify": f"test -f {flag}"}]

            def runner(c):
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            # baseline: 消化可能タスクあり・charter 無変更 → 再分解しない（署名だけ記録）
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 0)

            # viewer のボタン相当: commands に replan をドロップ → ingest でマーカー化
            cd = km.commands_dir(cfg)
            cd.mkdir(parents=True, exist_ok=True)
            (cd / "replan.json").write_text(json.dumps(
                {"command": "replan", "reason": "取りこぼし回復"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(cfg), ["replan:project"])
            self.assertEqual(list(cd.glob("*.json")), [])          # 処理したら消す
            self.assertTrue(km.replan_request_path(cfg).exists())  # 再分解要求マーカーが立つ
            self.assertTrue(km.has_work(cfg))                      # idle watch を起こす
            self.assertIn("DR-", (cfg.decisions / "demo.md").read_text())  # 決定記録も残る

            # 次パス: 消化可能タスクがあり charter 無変更でも、要求により再分解が走る
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)
            self.assertFalse(km.replan_request_path(cfg).exists())  # one-shot で消化
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("取りこぼした新規タスク", titles)          # 差分（取りこぼし）は入る
            self.assertIn("既存の done タスク", titles)              # done と同種のやり直しは再作成
            self.assertEqual(titles.count("処理中の既存タスク"), 1)   # 処理中とは二重投入しない

            # さらに次パス: 要求は消化済みなので再分解しない（one-shot）
            km.cmd_project(cfg, planner=planner, runner=runner)
            self.assertEqual(calls["n"], 1)

    def test_replan_does_not_resurrect_rejected_tasks(self):
        # replan のやり直しは done の再作成を許すが、却下済み（rejected・人の明示判断）は
        # archive にあっても照合に残し、復活させない（reject → 人が分解を要求した直後が典型）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            mkb(d, "T1", title="決済APIを追加", verify="true")
            km.cmd_reject(cfg, "T1", "スコープ外")            # archive に rejected として退避

            km.write_replan_request(cfg, "やり直し")

            def planner(ch):
                return [{"title": "決済APIを追加", "verify": "true"}]

            km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertNotIn("決済APIを追加", titles)          # 却下済みは復活しない

    def test_replan_request_consumed_on_no_acceptance_pass(self):
        # acceptance 未定義で cmd_project が早期 return するパスでも、再分解要求マーカーは
        # 入口で消費される（残すと has_work が永久に True になり idle watch が空振り起床し続ける）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: X\n## goal\nやる\n")   # acceptance 無し
            cfg = cfg_for(d)
            km.write_replan_request(cfg, "回復")
            self.assertTrue(km.has_work(cfg))                   # 要求中は起きる
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, km.project_exit_code("no-acceptance"))
            self.assertFalse(km.replan_request_path(cfg).exists())  # 入口で消費済み＝空振り起床しない

    def test_replan_command_without_charter_is_rejected(self):
        # charter が無い（backlog ループ）プロジェクトでは再分解の対象が無いため、
        # replan 指示は取り込まず .err に退避し、マーカーも立てない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cd = km.commands_dir(cfg)
            (cd / "r.json").write_text(json.dumps({"command": "replan"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(cfg), [])
            self.assertEqual(len(list(cd.glob("*.json.err"))), 1)   # .err に退避
            self.assertFalse(km.replan_request_path(cfg).exists())  # マーカーは立たない

    def test_unmet_acceptance_awaits_plan_without_auto_tasks(self):
        # 未達 acceptance から改善タスクを**自動では起こさない**（分解は人の明示操作だけ）。
        # 旧仕様は未達を改善タスク化して回し続けたが、人が消したタスクを evaluate が作り直す
        # 原因だった。未達は awaiting-plan として人へ返し、milestone で分解を案内する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "never")))
            cfg = cfg_for(d, max_project_cycles=3)
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 1)           # 人の対応待ち（分解待ち）
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_AWAITING_PLAN)
            self.assertEqual(km.load_tasks(cfg.backlog), [])   # 改善タスクは積まれない
            body = (d / "needs" / "demo.md").read_text(encoding="utf-8")
            self.assertIn("分解", body)          # milestone が分解の実行を案内する

    def test_resolve_verify_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(km.resolve_verify_cwd(cfg_for(d)), d)        # 既定は workdir
            self.assertEqual(km.resolve_verify_cwd(cfg_for(d, verify_cwd="/abs/clone")),
                             Path("/abs/clone"))                          # 絶対パスはそのまま
            self.assertEqual(km.resolve_verify_cwd(cfg_for(d, verify_cwd="clone")),
                             d / "clone")                                 # 相対は workdir 起点

    def test_verify_cwd_overrides_acceptance_dir(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            clone = d / "clone"; clone.mkdir(); (clone / "M").write_text("x")
            charter = km.parse_charter("# Charter: c\n## goal\nx\n## acceptance\n- test -f M\n")
            # workdir(d) には M が無い → 未指定なら FAIL
            self.assertEqual(km.evaluate_acceptance(cfg_for(d), charter)[0], 0)
            # verify_cwd をクローン先に向けると PASS（成果のある場所で検証）
            passed, total, _ = km.evaluate_acceptance(cfg_for(d, verify_cwd=str(clone)), charter)
            self.assertEqual((passed, total), (1, 1))

    def _make_git_repo(self, path: Path, marker: str = "MARKER.txt") -> None:
        g = ["git", "-C", str(path)]
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        (path / marker).write_text("ok")
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["-c", "user.email=a@b", "-c", "user.name=x",
                            "commit", "-qm", "init"], check=True)

    def test_acceptance_clones_single_repo_when_workdir_lacks_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            # workdir(d) には MARKER が無いので、clone せず workdir で見ると FAIL になるはず。
            # base/target を省く（branch 非依存で既定ブランチを clone）。url は単一・非 readonly。
            charter = km.parse_charter(
                f"# Charter: c\n## goal\nx\n## acceptance\n- test -f MARKER.txt\n"
                f"## repos\n- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            passed, total, _ = km.evaluate_acceptance(cfg_for(d), charter)
            self.assertEqual((passed, total), (1, 1))   # 一時 clone 先で検証 → PASS

    def test_acceptance_clone_failure_is_all_ng(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            charter = km.parse_charter(
                "# Charter: c\n## goal\nx\n## acceptance\n- true\n"
                f"## repos\n- app = {d / 'does-not-exist'}\n  - owns: **\n  - desc: 対象\n")
            passed, total, results = km.evaluate_acceptance(cfg_for(d), charter)
            self.assertEqual(passed, 0)                 # clone 失敗 → 黙ってフォールバックせず全 NG
            self.assertTrue(any("clone" in m for _, _, m in results))

    def test_acceptance_multi_repo_uses_workdir(self):
        # 対象 repo が複数なら（どれを cwd にするか曖昧）従来どおり workdir で実行する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "M").write_text("x")
            charter = km.parse_charter(
                "# Charter: c\n## goal\nx\n## acceptance\n- test -f M\n## repos\n"
                "- a = https://git/a.git\n  - desc: A\n  - base: main\n"
                "- b = https://git/b.git\n  - desc: B\n  - base: main\n")
            self.assertIsNone(km._charter_single_repo(charter))
            self.assertEqual(km.evaluate_acceptance(cfg_for(d), charter)[0], 1)  # workdir(d) で PASS

    def test_task_verify_cwd_clones_workspace_repo(self):
        # workspace 指定タスクは git-bus ルート(workdir)でなく該当 repo のクローン内で検証する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote, marker="WS.txt")     # workdir(d) には WS.txt が無い
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f WS.txt")
            task.set("workspace", "app")
            # 本ケースは「workspace を clone して検証する」こと自体の検証。task_branch は
            # test_task_verify_cwd_uses_task_branch_not_target で別途確認する。
            vcwd, tmp = km._task_verify_cwd(cfg_for(d, task_branch=False), task)
            try:
                self.assertIsNotNone(tmp)                    # 一時 clone を作った
                self.assertTrue((vcwd / "WS.txt").exists())  # クローン内に成果がある
                self.assertNotEqual(vcwd, d)                 # workdir ではない
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_task_verify_cwd_uses_task_branch_not_target(self):
        # task_branch 時の成果は ap/<task-id> にある。target/base（main）を clone すると
        # 成果が無く永久 NG になる（journal の @main 誤検証バグ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote, marker="MAIN.txt")
            g = ["git", "-C", str(remote)]
            defb = subprocess.run(g + ["rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(g + ["checkout", "-qb", "ap/T1"], check=True)
            (remote / "TASK.txt").write_text("ok")
            subprocess.run(g + ["add", "-A"], check=True)
            subprocess.run(g + ["-c", "user.email=a@b", "-c", "user.name=x",
                                "commit", "-qm", "task"], check=True)
            subprocess.run(g + ["checkout", "-q", defb], check=True)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - base: {defb}\n"
                             f"  - target: {defb}\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f TASK.txt")
            task.set("workspace", "app")
            cfg = cfg_for(d, task_branch=True)
            vcwd, tmp = km._task_verify_cwd(cfg, task)
            try:
                self.assertTrue((vcwd / "TASK.txt").exists(),
                                "ap/T1 上の成果を検証すること（main には無い）")
                journal = (d / "journal.md").read_text(encoding="utf-8")
                self.assertIn("ap/T1", journal)
                self.assertNotIn(f"@{defb} のクローン", journal)
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_task_verify_cwd_falls_back_to_base_when_task_branch_unpushed(self):
        # ap/<task-id> は worker が push して初めて生まれる。push の無いタスクで origin に
        # 存在しない ap/ をそのまま clone すると「clone 失敗」という完了条件と無関係な NG で
        # リトライが焼かれる（agent-project-codd-gate--042729 で retries=4 を消費した実障害）。
        # 「無いことを確認できた」場合に限り target/base へ倒し、journal に残す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote, marker="MAIN.txt")   # ap/T1 は作らない（push なし相当）
            defb = subprocess.run(["git", "-C", str(remote), "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - base: {defb}\n"
                             f"  - target: {defb}\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f MAIN.txt")
            task.set("workspace", "app")
            cfg = cfg_for(d, task_branch=True)
            vcwd, tmp = km._task_verify_cwd(cfg, task)
            try:
                self.assertTrue((vcwd / "MAIN.txt").exists(),
                                "ap/T1 が無ければ base へ倒して検証できること（clone 失敗 NG にしない）")
                journal = (d / "journal.md").read_text(encoding="utf-8")
                self.assertIn("未作成", journal)             # フォールバックの決定を journal に残す
                self.assertIn("ap/T1", journal)
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_task_verify_cwd_no_fallback_when_branch_lookup_unreachable(self):
        # フォールバックは「ls-remote で ap/ が無いことを確認できた（False）」場合だけ。
        # 照会不能（None）は従来どおり ap/ の clone を試し、そのエラーを人に見せる
        # （無言の既定フォールバック＝成果の無い場所での偽判定をしない厳密さを保つ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote, marker="MAIN.txt")   # ap/T1 は無い
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f MAIN.txt")
            task.set("workspace", "app")
            with mock.patch.object(km, "_remote_branch_exists", return_value=None):
                with self.assertRaises(RuntimeError):
                    km._task_verify_cwd(cfg_for(d, task_branch=True), task)

    def test_remote_branch_exists_distinguishes_absent_from_unreachable(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            defb = subprocess.run(["git", "-C", str(remote), "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
            self.assertTrue(km._remote_branch_exists(str(remote), defb))       # 実在 → True
            self.assertFalse(km._remote_branch_exists(str(remote), "ap/nope"))  # 照会成功・無い → False
            self.assertIsNone(km._remote_branch_exists(str(d / "no-such-repo"), "x"))  # 照会不能 → None

    def test_task_verify_cwd_uses_clone_root_not_path(self):
        # path（モノレポのサブフォルダ）があっても cwd はクローンのルート。verify は
        # リポジトリ直下からの相対（例 `cd pkg && …`）で書かれる規約なので path には潜らない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            (remote / "pkg").mkdir()
            (remote / "pkg" / "IN_SUB.txt").write_text("ok")
            subprocess.run(["git", "-C", str(remote), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(remote), "-c", "user.email=a@b",
                            "-c", "user.name=x", "commit", "-qm", "sub"], check=True)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - path: pkg\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="test -f pkg/IN_SUB.txt")
            task.set("workspace", "app")
            vcwd, tmp = km._task_verify_cwd(cfg_for(d, task_branch=False), task)
            try:
                self.assertNotEqual(vcwd.name, "pkg")        # path には潜らない（クローンのルート）
                self.assertTrue((vcwd / ".git").exists())    # ルートなので $AGENT_BASE_REV を取り直せる
                self.assertTrue((vcwd / "pkg" / "IN_SUB.txt").exists())   # path はルートからの相対で届く
            finally:
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_task_verify_cwd_bad_path_raises(self):
        # path: が clone 内に無い（誤設定）は RuntimeError（黙って workdir に倒さない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - path: nope\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x", verify="true")
            task.set("workspace", "app")
            with self.assertRaises(RuntimeError):
                km._task_verify_cwd(cfg_for(d), task)

    def test_task_verify_cwd_no_workspace_falls_back_to_workdir(self):
        # workspace 未指定は従来どおり workdir（一時 clone を作らない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            vcwd, tmp = km._task_verify_cwd(cfg_for(d), km.Task(id="T1", title="x"))
            self.assertEqual(vcwd, d)
            self.assertIsNone(tmp)

    def test_task_verify_cwd_explicit_verify_cwd_wins(self):
        # 明示 verify_cwd は workspace 指定より優先（運用の上書き・clone しない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            remote = d / "remote"
            self._make_git_repo(remote)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {remote}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x")
            task.set("workspace", "app")
            vcwd, tmp = km._task_verify_cwd(cfg_for(d, verify_cwd="/abs/clone"), task)
            self.assertEqual(vcwd, Path("/abs/clone"))
            self.assertIsNone(tmp)

    def test_task_verify_cwd_clone_failure_raises(self):
        # clone 失敗は黙って workdir に倒さず RuntimeError（成果の無い場所で誤判定しない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: c\n## goal\nx\n## repos\n"
                             f"- app = {d / 'nope'}\n  - owns: **\n  - desc: 対象\n")
            task = km.Task(id="T1", title="x")
            task.set("workspace", "app")
            with self.assertRaises(RuntimeError):
                km._task_verify_cwd(cfg_for(d), task)

    def test_stall_escalates(self):
        # 自動改善が無くなったため、1 パスの evaluate は 1 回＝stall はパスをまたいで積み上がる。
        # PASS 数が増えないまま project_stall 回評価されると停滞（no-progress）で人へ。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "never")))
            cfg = cfg_for(d, max_project_cycles=9, project_stall=2)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"],
                             km.REASON_PROJECT_AWAITING_PLAN)   # 1 回目はまだ分解待ち
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_STALL)
            self.assertEqual(code, 1)

    def test_approve_finalizes_converged_project(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertIn("project", (d / "DELIVERY.md").read_text(encoding="utf-8"))

    def test_converged_project_records_best_pass_count(self):
        # 回帰: 一発で全 PASS して収束したプロジェクトの best（過去最高 PASS 数）が 0 のまま
        # 保存され、viewer の概要タブが完了しているのに「0 / 1 達成」と表示していた。
        # best の更新が収束の early return より後ろにあったのが原因。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")     # acceptance は最初から PASS
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(st["acceptance_total"], 1)
            self.assertEqual(st["history"], [1])
            self.assertEqual(st["best"], 1)             # 1/1 達成として記録される

    def test_stall_still_counts_when_best_not_improved(self):
        # best を評価の先頭で更新するようにしても、停滞判定（PASS 数が過去最高を更新しないと
        # stall を積む）の意味は変わらない。更新前の値と比べていることの確認。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"                            # 存在しない → acceptance は常に FAIL
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, project_stall=2, max_project_cycles=5)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            st = km.load_project_state(cfg)
            self.assertEqual(st["status"], km.REASON_PROJECT_STALL)   # 0 PASS のまま → 停滞で人へ
            self.assertEqual(st["best"], 0)

    def test_approved_project_does_not_resurrect_milestone_on_rerun(self):
        # 実運用インシデントの再発防止: approve 後に charter.md が無変更のまま run/watch が
        # 再度 cmd_project を呼んでも、毎回 acceptance を再収束させて milestone（needs/<pid>.md）を
        # 復活させてはいけない（「承認しても復活してくる」バグ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)
            self.assertFalse((cfg.needs / "demo.md").exists())  # 承認で milestone は消える

            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return []

            code = km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            self.assertEqual(code, 0)                         # accepted のまま＝正常終了
            self.assertEqual(calls["n"], 0)                    # plan すら呼ばれない＝ループ自体が動かない
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertFalse((cfg.needs / "demo.md").exists())  # milestone は復活しない

    def test_approved_project_reopens_when_charter_changes(self):
        # 対の回帰テスト: charter.md を編集すれば accepted のガードを抜けて通常どおり再評価される
        # （「続行: charter.md を更新して run を再実行」という既存の案内どおりの挙動）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)

            write_charter(d, CHARTER.replace("{flag}", str(flag)) + "\n<!-- bump -->\n")
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)
            self.assertNotEqual(code, 0)                       # 収束候補として再び人待ちに戻る

    def test_project_status_not_clobbered_before_execute_stage(self):
        # 実運用インシデントの再発防止: cmd_project は冒頭で state["status"] を無条件に "running" へ
        # 上書き保存していた。② execute（runner=run_loop）は内部で ingest_commands を呼び、その場で
        # 人の approve/hold 指示（commands/ ファイルドロップ）を処理するが、この時点で読む
        # project.json はすでに "running" に潰されており、直前サイクルの "converged" が見えない。
        # watch 中は次サイクルが数秒おきに回るため、承認がほぼ常にこのタイミングとぶつかり、
        # cmd_approve が「converged の milestone が見つからない」として exit 2 で失敗し続け、
        # プロジェクトは承認しても再収束して milestone（needs/<pid>.md）が復活し続けていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_CONVERGED)

            seen = {}

            def runner(c):
                # execute 段に入った時点（ingest_commands が呼ばれるのと同じタイミング）の status
                seen["mid_cycle_status"] = km.load_project_state(c).get("status")
                return _drained()

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner)
            self.assertEqual(seen["mid_cycle_status"], km.REASON_PROJECT_CONVERGED)

    def test_approve_succeeds_when_ingested_mid_next_cycle(self):
        # 上のバグの実害を、実際の ingest_commands 呼び出しタイミングを模して直接検証する:
        # execute 段（runner の中）で approve を試みても、旧実装のように "running" に潰されておらず
        # 成功すること。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())

            rc = {}

            def runner(c):
                rc["approve"] = km.cmd_approve(c, "demo", "OK")
                return _drained()

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner)
            self.assertEqual(rc["approve"], 0)
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_ACCEPTED)

    def test_replan_request_bypasses_accepted_guard(self):
        # 実運用インシデントの再発防止:「charter から再分解」を押しても何も起きないバグ。
        # replan_req は cmd_project 冒頭で consume_replan_request により一発で消費されるため、
        # その直後の accepted ガードが素通りせず早期 return すると、要求は消えたのに一度も
        # plan_fn に反映されない（人の指示の握り潰し）になっていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)

            self.assertEqual(km.cmd_replan(cfg, "エラー回復"), 0)
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return []

            km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            self.assertEqual(calls["n"], 1)   # accepted でも明示の再分解要求は必ず一度処理される

    def test_replan_zero_diff_keeps_accepted_and_no_milestone(self):
        # 実運用インシデントの再発防止: 承認済み（accepted）のプロジェクトに差分ゼロの再分解を
        # かけると、再評価が accepted → converged に降格させて承認済みマイルストーン
        # （needs/<pid>.md）が復活していた（「承認ボタンを押しても再び表示される」の直接原因）。
        # 新しい仕事が何も無い再収束は accepted を維持し、milestone も書かない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)

            self.assertEqual(km.cmd_replan(cfg, "エラー回復"), 0)
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 0)                                           # accepted のまま
            self.assertEqual(km.load_project_state(cfg)["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertFalse((cfg.needs / "demo.md").exists())   # milestone は復活しない

    def test_stale_milestone_cleared_while_pass_runs(self):
        # 前パスの milestone（needs/<pid>.md）は次パスの再評価開始時に掃除される。残したままだと
        # run 実行中も「要対応: マイルストーン」カードが出続け、収束前の承認（exit 2）を誘発する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"
            write_charter(d, CHARTER.replace("{flag}", str(flag)))    # 未達 → converged しない
            cfg = cfg_for(d)

            def runner_fail(c):
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner_fail)
            self.assertTrue((cfg.needs / "demo.md").exists())         # blocked milestone が立つ

            seen = {}

            def runner_check(c):
                # execute 段（run 実行中に相当）では前パスの milestone は消えている
                seen["mid_run_needs"] = (cfg.needs / "demo.md").exists()
                r = _drained()
                r["counts"]["blocked"] = 1
                return r

            km.cmd_project(cfg, planner=lambda ch: [], runner=runner_check)
            self.assertFalse(seen["mid_run_needs"])
            self.assertTrue((cfg.needs / "demo.md").exists())         # 停止時に書き直される

    def test_pending_commands_ingested_even_on_accepted_early_return(self):
        # 実運用インシデントの再発防止: accepted ガードの早期 return は execute（run_loop）まで
        # 到達しないため、commands/ に落ちた指示ファイルが何パスも放置され、watch が空振り
        # 起床を繰り返していた。cmd_project は入口で指示を消化する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "OK"), 0)    # accepted にする

            cd = km.commands_dir(cfg)
            cd.mkdir(parents=True, exist_ok=True)
            (cd / "approve2.json").write_text(json.dumps(
                {"command": "approve", "id": "demo", "reason": "二度押し"}), encoding="utf-8")
            code = km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(code, 0)
            self.assertEqual(list(cd.glob("*.json")), [])             # 入口で消化される
            self.assertEqual(list(cd.glob("*.json.err")), [])         # 二度押しは .err にしない

    def test_approve_milestone_idempotent_and_clear_error(self):
        # 承認済み milestone への approve は冪等に成功（二度押し・取り込み遅延の再送を .err に
        # しない）。収束前（blocked 等）の approve は原因が分かるエラーで exit 2。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d)
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertEqual(km.cmd_approve(cfg, "demo", "1回目"), 0)
            self.assertEqual(km.cmd_approve(cfg, "demo", "2回目"), 0)   # 冪等

            st = km.load_project_state(cfg)
            st["status"] = km.REASON_PROJECT_BLOCKED                    # 収束前の状態を模す
            km.save_project_state(cfg, st)
            self.assertEqual(km.cmd_approve(cfg, "demo", "早すぎる承認"), 2)

    def test_master_charter_alone_is_not_decomposed(self):
        # マスター憲章（`## master` 付き charter.md）はプロジェクト全体の普遍的な前提であり、
        # それ自体はバックログへ分解されない。バージョン（charters/<name>.md）が無く、やることも
        # 無ければアイドル（リセット直後などに run_loop を回して無駄なログを増やさない）。
        # acceptance はマスターに書かなくてよい（バージョン側が持つ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- 分解しないマスター\n\n"
                             "## goal\n普遍的な目標\n")
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), [])       # 分解対象なし
            self.assertTrue(km._has_master_charter(cfg))

            ran = {"n": 0}
            planned = {"n": 0}

            def runner(c):
                ran["n"] += 1
                return _drained()

            def planner(ch):
                planned["n"] += 1
                return []

            # やることが無ければアイドル（run_loop も走らない）
            km.project_watch(cfg, planner=planner, runner=runner, max_passes=1)
            self.assertEqual(ran["n"], 0)                     # 空なら消化も走らない
            self.assertEqual(planned["n"], 0)                 # 分解（plan）は走らない
            self.assertEqual(list(cfg.needs.glob("*.md")), [])  # milestone も立たない

            # 実 backlog タスクがあるときだけ消化する
            mkb(d, "T1", status="ready", verify="true")
            km.project_watch(cfg, planner=planner, runner=runner, max_passes=1)
            self.assertEqual(ran["n"], 1)                     # backlog があれば消化は回る
            self.assertEqual(planned["n"], 0)                 # それでも分解はしない

    def test_project_watch_without_lease_executes_but_does_not_plan(self):
        # 複数 PC 構成: 計画（charter 分解・acceptance 評価）は controller lease を取れた
        # 1 台だけが行う（複数 PC ガイド §3.2）。起動時にピアの生存がまだ観測できない PC が
        # project_watch へ入っても、coordination が有効になった後 lease を取れないパスは
        # cmd_project（分解）を起こさず、割当済みタスクの消化（runner）だけを行う。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: demo\n\n## goal\ng\n\n## acceptance\n- `true`\n")
            mkb(d, "T1", status="ready", verify="true")
            cfg = cfg_for(d, node="pc-b")
            ran = {"n": 0}
            planned = {"n": 0}

            def runner(c):
                ran["n"] += 1
                return _drained()

            def planner(ch):
                planned["n"] += 1
                return []

            with mock.patch.object(km, "_coordination_active", return_value=True), \
                    mock.patch.object(km, "renew_controller_lease", return_value=False):
                km.project_watch(cfg, planner=planner, runner=runner, max_passes=1)
            self.assertEqual(planned["n"], 0)                 # 分解は起きない
            self.assertEqual(ran["n"], 1)                     # 消化（実行役）は回る

    def test_project_watch_replan_request_bypasses_lease_gate(self):
        # 再分解要求（.replan.request）はノード局所ファイル（ドット始まり＝同期対象外）なので、
        # このノードでしか消化できない。人の明示アクションは lease が無くても通す——さもないと
        # 実行役の PC で受理された「再分解」ボタンが計画役の交代まで永久に効かない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: demo\n\n## goal\ng\n\n## acceptance\n- `true`\n")
            cfg = cfg_for(d, node="pc-b")
            km.write_replan_request(cfg, "エラー回復")
            planned = {"n": 0}

            def planner(ch):
                planned["n"] += 1
                return []

            def runner(c):
                return _drained()

            with mock.patch.object(km, "_coordination_active", return_value=True), \
                    mock.patch.object(km, "renew_controller_lease", return_value=False):
                km.project_watch(cfg, planner=planner, runner=runner, max_passes=1)
            self.assertGreater(planned["n"], 0)               # 明示要求の分解は走る

    def test_version_inherits_master_charter(self):
        # 計画バージョン（charters/<name>.md）はマスター憲章を継承する:
        # goal はバージョン側が優先、acceptance・制約・前提はバージョンに無ければマスターから補う。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## goal\n普遍的な目標\n\n## constraints\n- 標準ライブラリのみ\n\n"
                             "## assumptions\n- 入力は UTF-8\n\n"
                             f"## acceptance\n- `test -f {flag}`\n")
            cd = d / "charters"
            cd.mkdir()
            (cd / "v1.md").write_text(
                "# Charter: v1\n\n## goal\nCSV 要約機能を作る\n\n"
                "## constraints\n- 追加の制約\n", encoding="utf-8")
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), ["v1"])   # バージョンだけが駆動される

            ch = km._load_named_charter(cfg, "v1")
            self.assertEqual(ch.goal, "CSV 要約機能を作る")     # goal はバージョン優先
            self.assertEqual(ch.acceptance, [f"test -f {flag}"])  # acceptance はマスター継承
            # 制約はバージョンが ## constraints を明示していれば置換（マスターの値は混ぜない）。
            # 明示した内容がそのバージョンの意思 — 空セクションなら「継承値を消す」意思として扱う。
            self.assertEqual(ch.constraints, ["追加の制約"])
            # 前提はバージョンに ## assumptions 見出しが無いのでマスターから継承する。
            self.assertEqual(ch.assumptions, ["入力は UTF-8"])

            # 継承済み acceptance で v1 が通常どおり収束する（マスター側は動かない）
            code = km.cmd_project(cfg, planner=lambda c: [], runner=lambda c: _drained(),
                                  charter_name="v1")
            self.assertNotEqual(code, 0)                       # converged（人待ち）
            st = km.load_charter_state(cfg, "v1")
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)

    def test_version_omitting_section_inherits_master(self):
        # バージョンが ## constraints / ## assumptions を持たなければ、両方マスターから継承する。
        # （明示置換の対称：見出しが無ければフォールバック、空見出しなら継承値を消す）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## goal\n普遍的な目標\n\n## constraints\n- 標準ライブラリのみ\n\n"
                             "## assumptions\n- 入力は UTF-8\n\n"
                             "## acceptance\n- `true`\n")
            cd = d / "charters"
            cd.mkdir()
            # v2 は goal だけを持ち、constraints / assumptions の見出しを一切書かない。
            (cd / "v2.md").write_text(
                "# Charter: v2\n\n## goal\nCSV 集計\n", encoding="utf-8")
            cfg = cfg_for(d)
            ch = km._load_named_charter(cfg, "v2")
            self.assertEqual(ch.goal, "CSV 集計")               # goal はバージョン優先
            self.assertEqual(ch.constraints, ["標準ライブラリのみ"])  # 見出しが無い→マスター継承
            self.assertEqual(ch.assumptions, ["入力は UTF-8"])   # 同上

    def test_version_empty_section_clears_inherited(self):
        # バージョンが ## constraints 見出しを空で置けば、マスターの制約を継承せず空にする意思。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## goal\n普遍的な目標\n\n## constraints\n- 標準ライブラリのみ\n\n"
                             "## acceptance\n- `true`\n")
            cd = d / "charters"
            cd.mkdir()
            (cd / "v3.md").write_text(
                "# Charter: v3\n\n## goal\n制約なし版\n\n## constraints\n", encoding="utf-8")
            cfg = cfg_for(d)
            ch = km._load_named_charter(cfg, "v3")
            self.assertEqual(ch.constraints, [])                # 空見出し＝継承値を消す

    def test_version_target_overrides_shared_registry(self):
        # 共有レジストリ（repos.json）を使っていても、各バージョン charter の ## repos が
        # 明示した『base と異なる target』（バージョン毎のリリース先ブランチ）が効く。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## acceptance\n- `true`\n")
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "desc": "本体", "base": "main",
                         "owns": ["src/**"]}}), encoding="utf-8")   # 手書き＝レジストリが正・全版共有
            cd = d / "charters"; cd.mkdir()
            (cd / "v1.md").write_text(
                "# Charter: v1\n\n## goal\nv1\n\n## repos\n- app = git@x:app.git\n"
                "  - owns: src/**\n  - base: main\n  - target: release/1.x\n", encoding="utf-8")
            (cd / "v2.md").write_text(
                "# Charter: v2\n\n## goal\nv2\n\n## repos\n- app = git@x:app.git\n"
                "  - owns: src/**\n  - base: main\n  - target: release/2.x\n", encoding="utf-8")
            cfg = cfg_for(d)

            ch1 = km._load_named_charter(cfg, "v1")
            ch2 = km._load_named_charter(cfg, "v2")
            s1 = next(s for s in ch1.repo_specs if s["name"] == "app")
            s2 = next(s for s in ch2.repo_specs if s["name"] == "app")
            # url/owns/base はレジストリ由来のまま（同一性・ルーティングは不変）、target だけ版毎に差し替わる
            self.assertEqual(s1["url"], "git@x:app.git")
            self.assertEqual(s1["base"], "main")
            self.assertFalse(s1["readonly"])
            self.assertEqual(s1["target"], "release/1.x")     # v1 → release/1.x
            self.assertEqual(s2["target"], "release/2.x")     # v2 → release/2.x

    def test_version_without_target_keeps_registry_target(self):
        # バージョンが target を明示しない（or ## repos 自体が無い）なら、共有レジストリの
        # target をそのまま尊重する（後方互換＝上書きしない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## acceptance\n- `true`\n")
            (d / "repos.json").write_text(__import__("json").dumps(
                {"app": {"url": "git@x:app.git", "base": "main", "target": "develop",
                         "owns": ["src/**"]}}), encoding="utf-8")
            cd = d / "charters"; cd.mkdir()
            (cd / "v1.md").write_text("# Charter: v1\n\n## goal\nv1\n", encoding="utf-8")
            cfg = cfg_for(d)
            ch = km._load_named_charter(cfg, "v1")
            s = next(s for s in ch.repo_specs if s["name"] == "app")
            self.assertEqual(s["target"], "develop")          # レジストリの target を尊重

    def test_master_edit_affects_version_signatures(self):
        # マスターを編集すると、継承合成後の署名（plan/full）が変わる＝バージョン側の
        # 再計画・accepted 再開の判定にマスター編集が効く。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## constraints\n- 制約A\n\n## acceptance\n- `true`\n")
            cd = d / "charters"
            cd.mkdir()
            (cd / "v1.md").write_text("# Charter: v1\n\n## goal\nやること\n", encoding="utf-8")
            cfg = cfg_for(d)
            ch1 = km._load_named_charter(cfg, "v1")
            plan1, full1 = km._charter_plan_signature(ch1), km._charter_full_signature(ch1)

            write_charter(d, "# Charter: 全体\n\n## master\n- マスター\n\n"
                             "## constraints\n- 制約A\n- 制約B（追加）\n\n## acceptance\n- `true`\n")
            ch2 = km._load_named_charter(cfg, "v1")
            self.assertNotEqual(plan1, km._charter_plan_signature(ch2))
            self.assertNotEqual(full1, km._charter_full_signature(ch2))

    def test_non_master_charter_keeps_legacy_behavior(self):
        # `## master` の無い従来の charter.md は今までどおり単一 charter として駆動される。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", "x"))
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), ["default"])
            self.assertFalse(km._has_master_charter(cfg))

    def test_review_project_generates_findings(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))
            cfg = cfg_for(d, review_project=True, max_project_cycles=1)
            seen = {"n": 0}

            def reviewer(ch):
                seen["n"] += 1
                return [{"title": "テストを追加", "verify": "true"}]

            km.cmd_project(cfg, planner=lambda ch: [], reviewer=reviewer,
                           runner=lambda c: _drained())
            self.assertEqual(seen["n"], 1)      # acceptance 全 PASS でも敵対的レビューが走る
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertIn("テストを追加", titles)

    def test_inner_blocked_stops_project(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "f")))

            def runner(c):
                r = _drained(); r["counts"]["blocked"] = 1
                return r

            code = km.cmd_project(cfg_for(d), planner=lambda ch: [], runner=runner)
            self.assertEqual(km.load_project_state(cfg_for(d))["status"],
                             km.REASON_PROJECT_BLOCKED)
            self.assertEqual(code, 1)

    def test_request_injects_charter_and_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", "x"))
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            km.append_decision(cfg, "T1", "user", context="前回の判断",
                               action="approve", reason="ライブラリXを使う", affects="T1")
            t = km.Task(id="T1", title="やる", verify="true")
            req = km.build_request(t, cfg)
            self.assertIn("プロジェクト定義", req)       # charter(定義)が注入される
            self.assertIn("CSV", req)                    # goal 本文
            self.assertIn("過去の判断記録", req)         # needs の判断結果(decisions)が注入される
            self.assertIn("ライブラリXを使う", req)

    def test_request_no_charter_is_backward_compatible(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)                              # charter.md 無し（通常運用）
            t = km.Task(id="T1", title="やる", verify="true")
            self.assertNotIn("プロジェクト定義", km.build_request(t, cfg))
            self.assertEqual(km.build_request(t), km.build_request(t, None))  # cfg 無しは従来どおり

    def test_charter_definition_includes_repos_and_links(self):
        # charter の repos（対象リポジトリ）と links（ブランチ等）が定義文に含まれる
        ch = km.parse_charter(
            "# Charter: r\n## goal\nやる\n"
            "## repos\n- app = https://git/app.git\n"
            "## links\n- https://git/app.git@release ブランチで作業\n")
        d = km._charter_definition(ch)
        self.assertIn("対象リポジトリ", d)
        self.assertIn("https://git/app.git", d)
        self.assertIn("関連リンク", d)
        self.assertIn("release ブランチで作業", d)

    def test_request_carries_charter_repos_and_links(self):
        # build_request（→ agent-flow ワーカー/gitlab イシュー）に repos/links が伝わる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nやる\n"
                             "## repos\n- app = https://git/app.git\n"
                             "## links\n- https://git/app.git@release で作業\n")
            cfg = cfg_for(d)
            req = km.build_request(km.Task(id="T1", title="やる", verify="true"), cfg)
            self.assertIn("https://git/app.git", req)
            self.assertIn("release で作業", req)

    def test_idempotent_plan_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            existing = ["成果物を作る"]
            created = km._enqueue_specs(
                cfg, [{"title": "成果物を作る", "verify": "true"}], existing, 0.5)
            self.assertEqual(created, [])       # 既存と類似は投入しない

    def test_enqueue_specs_rereads_existing_at_enqueue_time(self):
        # plan/review はエージェント委譲で数分かかる。スナップショット取得後に投入された
        # タスク（別インスタンス・前パス・state_git 同期・リセット後に書き戻された残骸）が
        # 照合に無く、類似バックログを二重投入していた。投入直前に現物を読み直して照合する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            snapshot = km._existing_titles(cfg)          # 空バックログ時点のスナップショット
            km.enqueue_task(cfg, {"title": "成果物を作る", "verify": "true"})   # plan 中に投入された体
            created = km._enqueue_specs(
                cfg, [{"title": "成果物を作る", "verify": "true"}], snapshot, 0.5)
            self.assertEqual(created, [])       # 読み直しで重複を検知（二重投入しない）

    def test_enqueue_specs_dedups_against_archive_reread(self):
        # done（archive）も読み直しの対象。リセットを伴わない通常運用で、plan 中に done へ
        # 移ったタスクと類似の spec を再投入しない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            snapshot = km._existing_titles(cfg)
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "T9.md").write_text("## T9: 成果物を作る\n- status: done\n", encoding="utf-8")
            created = km._enqueue_specs(
                cfg, [{"title": "成果物を作る", "verify": "true"}], snapshot, 0.5)
            self.assertEqual(created, [])


class TestMultiCharter(unittest.TestCase):
    """複数 charter（charters/<name>.md）: 1 プロジェクトで複数バージョンを並行駆動する。
    タスクは charter タグでスコープされ、plan の冪等照合・drained 判定・milestone/state は
    charter 単位に閉じる（execute の backlog は共有）。"""

    def _mk_charter(self, d, name, goal="やる"):
        cdir = d / "charters"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{name}.md").write_text(
            f"# Charter: {name}\n## goal\n{goal}\n## acceptance\n- `true`\n", encoding="utf-8")

    def test_charter_names_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self.assertEqual(km.charter_names(cfg), [])                 # charter 無し
            cfg.charter.write_text("# Charter: solo\n## goal\nx\n", encoding="utf-8")
            self.assertEqual(km.charter_names(cfg), ["default"])        # 単一 charter.md
            self._mk_charter(d, "v2")
            self._mk_charter(d, "v1")
            self.assertEqual(km.charter_names(cfg), ["v1", "v2"])       # charters/ が優先・名前順
            chs = dict(km.load_charters(cfg))
            self.assertIn("v1", chs)
            self.assertEqual(chs["v2"].name, "v2")

    def test_reconcile_milestones_is_pure_projection_of_status(self):
        # 根本対策:「要対応マイルストーンが何度も復活する」。milestone ファイルは project.json の
        # status の純粋な投影であり、reconcile_milestones が唯一の調整点。承認済み・削除済み
        # バージョン・旧トップレベルの milestone は毎回消え、no-acceptance/converged は残る。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            cfg = cfg_for(d, project_name="proj")
            cdir = d / "charters"; cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "v1.md").write_text(
                f"# Charter: v1\n## goal\nv1\n## acceptance\n- `test -f {flag}`\n", encoding="utf-8")
            (cdir / "v2.md").write_text(          # 完了条件なし → no-acceptance
                "# Charter: v2\n## goal\nv2\n## acceptance\n", encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(), charter_name="v1")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(), charter_name="v2")
            data = km.load_project_state(cfg)
            self.assertEqual(data["charters"]["v1"]["status"], km.REASON_PROJECT_CONVERGED)
            self.assertEqual(data["charters"]["v2"]["status"], km.REASON_PROJECT_NO_ACCEPTANCE)

            # (1) 承認済みの milestone が復活しても GC が毎回消す
            km.finalize_project(cfg, data["charters"]["v1"], "OK",
                                charter=km._load_named_charter(cfg, "v1"), charter_name="v1")
            (cfg.needs / "proj-v1.md").write_text("# マイルストーン: v1\nkind: milestone\n", encoding="utf-8")
            # frontmatter kind を正しく（reconcile は kind=milestone だけ対象）
            (cfg.needs / "proj-v1.md").write_text(
                "---\nkind: milestone\n---\n# マイルストーン: v1\n", encoding="utf-8")
            km.reconcile_milestones(cfg)
            self.assertFalse((cfg.needs / "proj-v1.md").exists())   # accepted → 消える
            self.assertTrue((cfg.needs / "proj-v2.md").exists())    # no-acceptance → 残る

            # (2) 存在しないバージョンの milestone（orphan）も消す
            (cfg.needs / "proj-vX.md").write_text(
                "---\nkind: milestone\n---\n# マイルストーン: vX\n", encoding="utf-8")
            km.reconcile_milestones(cfg)
            self.assertFalse((cfg.needs / "proj-vX.md").exists())

            # (3) タスク級の needs（kind != milestone）は触らない
            (cfg.needs / "T1.md").write_text(
                "---\nkind: review\n---\n# 要対応: T1\n", encoding="utf-8")
            km.reconcile_milestones(cfg)
            self.assertTrue((cfg.needs / "T1.md").exists())

    def test_version_run_clears_stale_toplevel_milestone(self):
        # 実運用インシデントの再発防止:「要対応のマイルストーンが二度出る」。
        # 単一 charter.md で一度 run（トップレベル milestone needs/<project>.md を作る）した後に
        # charters/ を足してバージョン運用へ移行すると、旧トップレベル milestone が残り、
        # <project>.md と <project>-<version>.md の 2 枚が要対応に並んでしまう。
        # バージョン運用の run に入ったら旧トップレベル milestone を掃除する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flag = d / "flag"; flag.write_text("x")
            write_charter(d, CHARTER.replace("{flag}", str(flag)))       # 単一 charter.md
            cfg = cfg_for(d, project_name="proj")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained())
            self.assertTrue((cfg.needs / "proj.md").exists())            # トップレベル milestone

            self._mk_charter(d, "v1", goal="v1")                        # バージョンへ移行
            (d / "charters" / "v1.md").write_text(
                f"# Charter: v1\n## goal\nv1\n## acceptance\n- `test -f {flag}`\n", encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(),
                           charter_name="v1")
            self.assertTrue((cfg.needs / "proj-v1.md").exists())         # バージョンの milestone
            self.assertFalse((cfg.needs / "proj.md").exists())          # 旧トップレベルは掃除される
            self.assertEqual(len(list(cfg.needs.glob("*.md"))), 1)      # 要対応は 1 枚だけ

            # v1 を承認（accepted）した後でも、再び現れた旧トップレベル milestone は掃除される
            # （掃除は accepted の早期 return より前で行うため取り残さない）。
            self.assertEqual(km.cmd_approve(cfg, "proj-v1", "OK"), 0)
            (cfg.needs / "proj.md").write_text("# マイルストーン: proj\n", encoding="utf-8")  # 再発を模す
            km.cmd_project(cfg, planner=lambda ch: [], runner=lambda c: _drained(),
                           charter_name="v1")                            # accepted → 早期 return
            self.assertFalse((cfg.needs / "proj.md").exists())          # それでも掃除される

    def test_cmd_project_tags_tasks_and_scopes_state(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            planner = lambda ch: [{"title": f"{ch.name} のタスク", "verify": "true"}]
            km.write_replan_request(cfg, "分解", charter="v1")   # 分解は明示要求でしか走らない
            rc = km.cmd_project(cfg, planner=planner, reviewer=lambda ch: [],
                                charter_name="v1")
            self.assertEqual(rc, 1)                                     # 収束候補 → 人待ち
            # タスクに charter タグが付く（アーカイブ済みを含めて確認）
            arch = list((d / "archive").glob("*.md"))
            self.assertTrue(arch)
            t = km.parse_task(arch[0].read_text(encoding="utf-8"), arch[0].stem)
            self.assertEqual(t.get("charter"), "v1")
            # state は project.json の charters マップに閉じる
            data = km.load_project_state(cfg)
            self.assertIn("v1", data.get("charters", {}))
            pid = data["charters"]["v1"]["id"]
            self.assertTrue(pid.endswith("-v1"))                        # milestone id は charter 別
            self.assertTrue((cfg.needs / f"{pid}.md").exists())

    def test_milestone_heading_uses_version_name(self):
        # milestone 票の見出しはバージョン名（ファイル名）を正とする。charter の宣言名が
        # 前バージョンのコピー等でプロジェクト名のまま食い違っても、バージョンで識別できる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            cdir = d / "charters"; cdir.mkdir(parents=True, exist_ok=True)
            # 宣言名は「sandbox」だがファイル名（バージョン）は「v2」
            (cdir / "v2.md").write_text(
                "# Charter: sandbox\n## goal\nやる\n## acceptance\n- `true`\n", encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], reviewer=lambda ch: [], charter_name="v2")
            pid = km.load_project_state(cfg)["charters"]["v2"]["id"]
            body = (cfg.needs / f"{pid}.md").read_text(encoding="utf-8")
            self.assertIn("# マイルストーン: v2（sandbox）", body)     # バージョン名で識別＋宣言名併記
            self.assertNotIn("# マイルストーン: sandbox\n", body)

    def test_two_charters_plan_independently(self):
        # v1 に消化可能タスクが残っていても v2 の plan は起こる（drained 判定のスコープ）。
        # 同名タスクでも charter が違えば冪等排除しない（existing のスコープ）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1, dry_run=True)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            planner = lambda ch: [{"title": "共通タイトルの作業", "verify": "true"}]
            km.write_replan_request(cfg, "分解", charter="v1")
            km.cmd_project(cfg, planner=planner, reviewer=lambda ch: [], charter_name="v1")
            # v1 のタスクを未消化のまま残す（doing 相当ではなく ready のタスクを積み直す）
            km.enqueue_task(cfg, {"title": "v1 残作業", "verify": "true", "charter": "v1",
                                  "status": "ready"})
            km.write_replan_request(cfg, "分解", charter="v2")
            km.cmd_project(cfg, planner=planner, reviewer=lambda ch: [], charter_name="v2")
            # v2 にも同名タスクが plan された（archive/backlog を charter タグで数える）
            tagged = []
            for f in list((d / "archive").glob("*.md")) + list((d / "backlog").glob("*.md")):
                t = km.parse_task(f.read_text(encoding="utf-8"), f.stem)
                if t.title == "共通タイトルの作業":
                    tagged.append(t.get("charter"))
            self.assertIn("v1", tagged)
            self.assertIn("v2", tagged)

    def test_replan_request_scoped_to_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            km.write_replan_request(cfg, "v2 を作り直す", charter="v2")
            self.assertIsNone(km.consume_replan_request(cfg, "v1"))     # 別 charter 宛 → 残す
            self.assertTrue(km.replan_request_path(cfg).exists())
            got = km.consume_replan_request(cfg, "v2")                  # 対象 charter が消化
            self.assertEqual(got.get("charter"), "v2")
            self.assertFalse(km.replan_request_path(cfg).exists())
            # charter 指定の無い要求はどの charter でも消化できる
            km.write_replan_request(cfg, "全体")
            self.assertIsNotNone(km.consume_replan_request(cfg, "v1"))

    def test_run_single_drives_all_charters(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            seen = []
            orig = km.cmd_project

            def spy(c, *a, **kw):
                seen.append(kw.get("charter_name"))
                return orig(c, planner=lambda ch: [], reviewer=lambda ch: [],
                            charter_name=kw.get("charter_name"))

            with mock.patch.object(km, "cmd_project", side_effect=spy):
                km._run_single(cfg)
            self.assertEqual(seen, ["v1", "v2"])                        # 全 charter を順に回す

    def test_project_watch_round_robins_charters(self):
        # watch は全バージョンを 1 パスずつ回すが、plan が走るのは分解要求が宛てられた
        # バージョンだけ（分解は明示操作・要求は charter 宛てにスコープされる）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            self._mk_charter(d, "v2")
            seen = []
            planner = lambda ch: seen.append(ch.name) or []
            km.write_replan_request(cfg, "分解", charter="v2")
            km.project_watch(cfg, planner=planner, reviewer=lambda ch: [],
                             runner=km.run_loop, sleeper=lambda _s: None, max_passes=2)
            self.assertEqual(seen, ["v2"])       # v1 のパスは要求が無いので plan を起こさない

    def test_project_watch_exits_nonzero_on_infrastructure_error(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            calls = []

            def runner(_cfg):
                calls.append(1)
                return {"reason": km.REASON_INFRASTRUCTURE, "cost": 0.0,
                        "counts": {"done": 0, "blocked": 0, "review": 0, "proposed": 0}}

            code = km.project_watch(cfg, planner=lambda ch: [], reviewer=lambda ch: [],
                                    runner=runner, sleeper=lambda _s: None)
            self.assertEqual(code, 2)
            self.assertEqual(len(calls), 1)

    def test_milestone_approve_finalizes_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            self._mk_charter(d, "v1")
            km.cmd_project(cfg, planner=lambda ch: [], reviewer=lambda ch: [],
                           charter_name="v1")
            data = km.load_project_state(cfg)
            pid = data["charters"]["v1"]["id"]
            self.assertEqual(data["charters"]["v1"]["status"], km.REASON_PROJECT_CONVERGED)
            rc = km.cmd_approve(cfg, pid, "受領")
            self.assertEqual(rc, 0)
            data = km.load_project_state(cfg)
            self.assertEqual(data["charters"]["v1"]["status"], km.REASON_PROJECT_ACCEPTED)

    def test_build_request_injects_task_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._mk_charter(d, "v1", goal="V1-GOAL 保守")
            self._mk_charter(d, "v2", goal="V2-GOAL 新機能")
            t = km.enqueue_task(cfg, {"title": "x", "verify": "true", "charter": "v2"})
            req = km.build_request(t, cfg)
            self.assertIn("V2-GOAL", req)                               # タグの charter を注入
            self.assertNotIn("V1-GOAL", req)

    def test_single_charter_md_backward_compatible(self):
        # charter.md 単体は従来どおり（state はトップレベル・milestone id に接尾辞なし）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, max_project_cycles=1)
            cfg.charter.write_text("# Charter: solo\n## goal\nx\n## acceptance\n- `true`\n",
                                   encoding="utf-8")
            km.cmd_project(cfg, planner=lambda ch: [], reviewer=lambda ch: [],
                           charter_name="default")
            data = km.load_project_state(cfg)
            self.assertNotIn("charters", data)                          # 従来のトップレベル形
            self.assertFalse(str(data.get("id", "")).endswith("-default"))
