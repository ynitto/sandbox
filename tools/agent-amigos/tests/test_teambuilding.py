"""agent-amigos の単体テスト — teambuilding（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TeamBuildingTests(AmigosTestCase):
    """チームビルディング（ミッションのみ → team-builder スキルで役割設計 → 従来 post へ合流）。

    LLM は使わず agentcli.run_agent を差し替えて設計出力を注入する。
    """

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    DESIGN = {
        "mission": {"budget": {"execution_minutes": 45},
                    "convergence": {"done_when": "reviewer-approved"}},
        "roles": [
            {"id": "architect", "mission": "設計を確定する", "deliverables": ["architecture.md"]},
            {"id": "impl", "mission": "実装する", "deliverables": ["src/"],
             "requires": {"tags": ["python"]}, "collaborates_with": ["architect"]},
            {"id": "reviewer", "mission": "レビューする", "approver": True},
        ],
    }

    def _stub_agent(self, output):
        from agent_amigos import agentcli
        original = agentcli.run_agent
        agentcli.run_agent = lambda *a, **k: output
        self.addCleanup(setattr, agentcli, "run_agent", original)

    def _capture_agent(self, output):
        """run_agent を差し替え、渡されたプロンプトを self._last_prompt に記録する。"""
        from agent_amigos import agentcli
        original = agentcli.run_agent
        box = {}

        def _fake(prompt, *a, **k):
            box.setdefault("prompt", prompt)   # 最初の呼び出し（設計）を記録。
            return output                       # 後続の amigo ターンでは上書きしない
        agentcli.run_agent = _fake
        self.addCleanup(setattr, agentcli, "run_agent", original)
        return box

    def test_skill_is_resolved_from_repo(self):
        from agent_amigos import teambuilding
        text, source = teambuilding.resolve_skill_instructions()
        self.assertTrue(source.endswith("SKILL.md") or source == "(builtin)")
        self.assertIn("team-builder", text.lower())

    def test_build_team_designs_and_validates(self):
        from agent_amigos import teambuilding
        # 設計 JSON の前後に地の文があっても extract_json が拾える
        self._stub_agent("設計結果:\n" + json.dumps(self.DESIGN, ensure_ascii=False) + "\n以上")
        brief = {"title": "FAQ", "goal": "FAQ ボットを作る",
                 "capabilities": ["python"], "agent_cli": "claude"}
        mission_over, roles, meta = teambuilding.build_team(brief, "claude")
        ids = [r["id"] for r in roles]
        self.assertEqual(ids, ["architect", "impl", "reviewer"])
        # agent_cli 未指定のロールにはブリーフ既定が補われる
        self.assertTrue(all(r.get("agent_cli") == "claude" for r in roles))
        self.assertEqual(mission_over["title"], "FAQ")   # ブリーフから補完
        self.assertEqual(mission_over["goal"], "FAQ ボットを作る")
        self.assertTrue(meta.get("skill_source"))

    def test_build_team_requires_real_cli(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps(self.DESIGN))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "x"}, "stub")
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "x"}, "")

    def test_build_team_needs_goal_or_design(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps(self.DESIGN))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"title": "no goal"}, "claude")

    def test_build_team_rejects_invalid_design(self):
        from agent_amigos import teambuilding
        bad = {"roles": [{"id": "a", "mission": "x"}, {"id": "a", "mission": "y"}]}  # 重複 id
        self._stub_agent(json.dumps(bad))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "g"}, "claude")

    def test_build_team_rejects_empty_roles(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps({"roles": []}))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "g"}, "claude")

    def test_build_team_command_posts_mission(self):
        """dashboard/人が投函する build-team 指示を常駐デーモンが取り込み公示する。"""
        from agent_amigos.configfile import commands_dir
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "title": "FAQ", "goal": "FAQ ボットを作る",
                       "capabilities": ["python"], "agent_cli": "claude"}, f, ensure_ascii=False)
        d = NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                       commands_home=self.home)
        d.cycle()
        mids = self.bus.list_missions()
        self.assertEqual(len(mids), 1)
        mp = self.bus.mission(mids[0])
        mission = load_mission(mp)
        roles = load_roles(mp)
        self.assertEqual(mission["title"], "FAQ")
        self.assertEqual(mission["convergence"]["done_when"], "reviewer-approved")
        # 設計したロール + 省略された integrator の自動補充 + design doc 自動生成
        self.assertEqual(set(roles), {"architect", "impl", "reviewer", "integrator"})
        self.assertTrue(os.path.isfile(mp.design_doc()))

    def test_build_team_command_uses_given_design_doc(self):
        from agent_amigos.configfile import commands_dir
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "goal": "g", "design": "# 与えた設計\n受入基準\n",
                       "agent_cli": "claude"}, f, ensure_ascii=False)
        NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                   commands_home=self.home).cycle()
        mids = self.bus.list_missions()
        self.assertEqual(len(mids), 1)
        with open(self.bus.mission(mids[0]).design_doc(), encoding="utf-8") as f:
            self.assertIn("与えた設計", f.read())

    # --- オーケストレーションパターン（カタログ・自動選択・明示指定） --------------

    def test_pattern_catalog_loads_and_tiers(self):
        from agent_amigos import teambuilding
        high = teambuilding.list_patterns(tier="high")
        allp = teambuilding.list_patterns()
        self.assertGreaterEqual(len(high), 8)
        self.assertGreater(len(allp), len(high))          # medium も存在する
        ids = {p["id"] for p in high}
        self.assertIn("self-refine", ids)
        self.assertIn("metagpt-sop", ids)
        for p in allp:                                    # 契約の必須キー
            for k in ("id", "name", "category", "tier", "when_to_use", "feasibility"):
                self.assertIn(k, p, p.get("id"))
            if p.get("target") == "agent-flow":
                self.assertIn("flow", p, p.get("id"))     # 委譲パターンは team を持たない
            else:
                self.assertTrue((p.get("team") or {}).get("roles"), p.get("id"))

    def test_build_team_injects_high_patterns_and_records_choice(self):
        from agent_amigos import teambuilding
        box = self._capture_agent(json.dumps(
            {"pattern": "self-refine", **self.DESIGN}, ensure_ascii=False))
        _mo, _roles, meta = teambuilding.build_team({"goal": "磨き上げたい"}, "claude")
        # 高価値パターンのカタログがプロンプトへ注入されている（自動選択）
        self.assertIn("self-refine", box["prompt"])
        self.assertIn("metagpt-sop", box["prompt"])
        self.assertNotIn("reflexion", box["prompt"])       # medium は自動選択に載らない
        self.assertEqual(meta["chosen_pattern"], "self-refine")

    def test_build_team_forced_pattern_injects_only_that(self):
        from agent_amigos import teambuilding
        box = self._capture_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        _mo, _roles, meta = teambuilding.build_team(
            {"goal": "g"}, "claude", pattern="reflexion")       # medium を明示指定
        self.assertIn("reflexion", box["prompt"])
        self.assertIn("厳守", box["prompt"])                    # forced 見出し
        self.assertEqual(meta["chosen_pattern"], "reflexion")   # 指定が優先

    def test_build_team_unknown_pattern_rejected(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        with self.assertRaises(RuntimeError):
            teambuilding.build_team({"goal": "g"}, "claude", pattern="does-not-exist")

    def test_build_team_pattern_none_is_normalized(self):
        from agent_amigos import teambuilding
        self._stub_agent(json.dumps({"pattern": "none", **self.DESIGN}, ensure_ascii=False))
        _mo, _roles, meta = teambuilding.build_team({"goal": "g"}, "claude")
        self.assertIsNone(meta["chosen_pattern"])

    def test_build_team_command_passes_pattern(self):
        from agent_amigos.configfile import commands_dir
        box = self._capture_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "goal": "g", "agent_cli": "claude",
                       "pattern": "agentcoder"}, f, ensure_ascii=False)
        NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                   commands_home=self.home).cycle()
        self.assertIn("agentcoder", box["prompt"])
        self.assertEqual(len(self.bus.list_missions()), 1)

    def test_cli_list_patterns(self):
        rc = cli.main(["build-team", "--list-patterns"])
        self.assertEqual(rc, 0)

    # --- G4: agent-flow への委譲（探索木・動的分解） -------------------------
    def _stub_flow(self):
        self._stub_agent(json.dumps({"target": "agent-flow", "pattern": "tree-of-thoughts",
                                     "flow": {"goal": "24 パズルを解く",
                                              "strategy": "分岐→スコア→ビーム"}}))

    def test_build_team_flow_target_returns_delegation(self):
        from agent_amigos import teambuilding
        self._stub_flow()
        mo, roles, meta = teambuilding.build_team({"goal": "パズル", "title": "p"}, "claude")
        self.assertEqual(meta["target"], "agent-flow")
        self.assertEqual(roles, [])
        d = meta["delegation"]
        self.assertEqual((d["op"], d["workload"], d["version"]), ("post", "flow", 1))
        self.assertTrue(d["id"].startswith("dg-"))
        self.assertIn("戦略ヒント", d["goal"])              # strategy が goal に畳まれる
        # delegation.schema.json の必須キーを満たす
        schema = read_json(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                        "schemas", "delegation.schema.json"))
        for k in schema["required"]:
            self.assertIn(k, d)

    def test_build_team_flow_cli_dry_run_prints_envelope(self):
        import io
        import contextlib
        self._stub_flow()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["build-team", "--bus", self.bus.root, "--goal", "g",
                           "--agent-cli", "claude", "--node-id", "n1"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn('"workload": "flow"', out)
        self.assertIn("agent-flow submit", out)
        self.assertEqual(self.bus.list_missions(), [])     # amigos へは公示しない

    def test_build_team_command_flow_writes_delegation_not_mission(self):
        from agent_amigos.configfile import commands_dir, state_dir
        self._stub_flow()
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "bt.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "build-team", "goal": "探索する", "agent_cli": "claude"}, f,
                      ensure_ascii=False)
        NodeDaemon(self.bus, "owner-node", agent_cli="claude", interval=0,
                   commands_home=self.home).cycle()
        self.assertEqual(self.bus.list_missions(), [])     # amigos ミッションは作らない
        designs = os.path.join(state_dir(self.home), "designs")
        self.assertTrue(any(n.endswith("-delegation.json") for n in os.listdir(designs)))

    def test_build_team_cli_out_dry_run(self):
        from agent_amigos import teambuilding  # noqa: F401 (ensures import path)
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        out = os.path.join(self.tmp, "roles.json")
        rc = cli.main(["build-team", "--bus", self.bus.root, "--goal", "g",
                       "--agent-cli", "claude", "--out", out, "--node-id", "n1"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.bus.list_missions(), [])   # ドライランは公示しない
        spec = read_json(out)
        self.assertEqual([r["id"] for r in spec["roles"]], ["architect", "impl", "reviewer"])

    def test_build_team_cli_post(self):
        self._stub_agent(json.dumps(self.DESIGN, ensure_ascii=False))
        rc = cli.main(["build-team", "--bus", self.bus.root, "--goal", "g", "--title", "T",
                       "--agent-cli", "claude", "--post", "--node-id", "n1"])
        self.assertEqual(rc, 0)
        mids = self.bus.list_missions()
        self.assertEqual(len(mids), 1)
        self.assertEqual(load_mission(self.bus.mission(mids[0]))["title"], "T")
