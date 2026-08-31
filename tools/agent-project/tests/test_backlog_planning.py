"""agent-project の単体テスト — S6（バックログの生成・レビュー・整合）と S7（spec の 3 段）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


def _charter(cfg, goal: str = "テスト目標", name: str = "charter") -> "km.Charter":
    """最小の charter（acceptance 1 件・書込先 repo 1 件）をファイルへ書いて読み込む。"""
    cfg.charter.parent.mkdir(parents=True, exist_ok=True)
    cfg.charter.write_text(
        f"# {name}\n\n## goal\n{goal}\n\n## acceptance\n- true\n"
        "\n## repos\n- app = https://git.example.com/app.git\n  - owns: src/**\n",
        encoding="utf-8")
    return km.load_charter(cfg)


class AcceptanceRoundTripTests(unittest.TestCase):
    """S6-0: `acceptance` の受け渡し。**S5 が確定させた表現が、生成側・レビュー側・編集側の
    どこにも通っていなかった**——その修理を固定する。"""

    def test_list_becomes_one_line_per_criterion(self):
        # 配列を str(list) して 1 行に潰すと、`- acceptance: ['A', 'B']` という Python の
        # repr が md に残り、task_acceptance はそれを 1 項目の基準として読んでしまう。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "acceptance": ["基準A", "基準B, カンマ入り"]})
            md = (cfg.backlog / f"{t.id}.md").read_text(encoding="utf-8")
            # 書き込み境界の正規化（P1-A8）: 旧 `acceptance` spec でも正規形の行で保存される
            self.assertIn("- task_acceptance_criteria: 基準A", md)
            self.assertIn("- task_acceptance_criteria: 基準B, カンマ入り", md)
            self.assertNotIn("['", md)
            back = km.parse_task(md, t.id)
            self.assertEqual(km.task_acceptance(back), ["基準A", "基準B, カンマ入り"])

    def test_single_string_is_one_criterion(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "acceptance": "ひとつだけ"})
            self.assertEqual(km.task_acceptance(t), ["ひとつだけ"])

    def test_acceptance_only_task_is_ready_not_inbox(self):
        # has_verify_plan は acceptance を数えるのに task_from_spec が数えておらず、
        # 受入基準しか持たないタスクが人の triage（inbox）へ落ちていた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "acceptance": ["基準A"]})
            self.assertEqual(t.norm_status(), "ready")
            self.assertTrue(km.has_verify_plan(t))

    def test_plan_review_card_shows_criteria(self):
        # 人が読んで直す一次表現が票に出ていなければ、直す機会は無い
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            t = km.Task(id="T1", title="X",
                        extra=[("acceptance", "基準A"), ("acceptance", "基準B")])
            block = km._task_definition_block(t)
            self.assertIn("acceptance", block)
            self.assertIn("1. 基準A", block)
            self.assertIn("2. 基準B", block)

    def test_plan_review_card_warns_on_too_many_criteria(self):
        t = km.Task(id="T1", title="X",
                    extra=[("acceptance", f"基準{i}") for i in range(9)])
        self.assertIn("目安は 3〜7 件", km._task_definition_block(t))

    def test_risks_reach_reviewer_and_worker(self):
        t = km.Task(id="T1", title="X", extra=[
            ("risks", "誤操作を防ぐ"),
            ("risks", "旧形式も維持する"),
        ])
        review = km._task_definition_block(t)
        request = km.build_request(t)
        for risk in ("誤操作を防ぐ", "旧形式も維持する"):
            self.assertIn(risk, review)
            self.assertIn(risk, request)

    def test_legacy_accept_shown_once(self):
        # accept は task_acceptance が箇条書きへ畳むので、`- accept:` 行として二重に出さない
        t = km.Task(id="T1", title="X", extra=[("accept", "昔の 1 行")])
        block = km._task_definition_block(t)
        self.assertIn("1. 昔の 1 行", block)
        self.assertNotIn("- accept:", block)

    def test_revise_replaces_all_criteria(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "acceptance": ["旧A", "旧B", "旧C"]})
            rc = km.cmd_revise(cfg, t.id, {"acceptance": ["新A", "新B"]}, "", "手直し")
            self.assertEqual(rc, 0)
            after = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(km.task_acceptance(after), ["新A", "新B"])

    def test_revise_can_clear_criteria(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "verify": "true",
                                      "acceptance": ["旧A"]})
            km.cmd_revise(cfg, t.id, {"acceptance": [""]}, "", "削除")
            self.assertEqual(km.task_acceptance(km.load_tasks(cfg.backlog)[0]), [])

    def test_revise_replaces_all_risks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "acceptance": ["基準"],
                                      "risks": ["旧A", "旧B"]})
            self.assertEqual(km.cmd_revise(
                cfg, t.id, {"risks": ["新A", "新B"]}, "", "更新"), 0)
            after = km.load_tasks(cfg.backlog)[0]
            self.assertEqual([v for k, v in after.extra if k == "risks"], ["新A", "新B"])

    def test_revise_marks_human_edited(self):
        # プランナーへ「人が確定済み・作り直すな」を伝える印
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "X", "verify": "true"})
            km.cmd_revise(cfg, t.id, {"title": "X（人が直した）"}, "", "手直し")
            self.assertEqual(km.load_tasks(cfg.backlog)[0].get("edited"), "human")


class TombstoneTests(unittest.TestCase):
    """S6-4/未決 5: 墓標。**抑止は完全一致のみ・類似は提示に回す。**"""

    def test_reject_writes_tombstone(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "board の UI を作る", "verify": "true"})
            km.cmd_reject(cfg, t.id, "別案に置き換えた")
            graves = km.load_tombstones(cfg)
            self.assertEqual([g["title"] for g in graves], ["board の UI を作る"])
            self.assertEqual(graves[0]["reason"], "別案に置き換えた")

    def test_exact_match_is_suppressed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "board の UI を作る", "やめた")
            created = km._enqueue_specs(
                cfg, [{"title": "board の UI を作る", "verify": "true"}], [], 0.5)
            self.assertEqual(created, [])

    def test_similar_is_not_suppressed_but_annotated(self):
        # Jaccard 0.5 は「board UI」と「board 観測 UI」を同一視する強さがある。
        # 恒久抑止に使うと、後から本当に要るタスクが起票できなくなる（人は気づけない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "board の UI を 作る", "やめた")
            created = km._enqueue_specs(
                cfg, [{"title": "board の UI を 直す"}], [], 0.5)
            self.assertEqual(len(created), 1, "類似だけでは投入を止めない")
            self.assertIn("却下済みのタスクに似ています", created[0].get("note") or "")

    def test_charter_tagged_tombstone_is_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "X をやる", "v1 ではやめた", charter="v1")
            self.assertEqual(len(km.load_tombstones(cfg, "v1")), 1)
            self.assertEqual(km.load_tombstones(cfg, "v2"), [], "別 charter には効かない")

    def test_untagged_tombstone_applies_everywhere(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "X をやる", "恒久的にやめた")
            self.assertEqual(len(km.load_tombstones(cfg, "v9")), 1)

    def test_normalization_ignores_case_width_and_symbols(self):
        self.assertEqual(km._norm_title("Board の UI を作る！"), km._norm_title("ｂｏａｒｄ の ui を作る"))

    def test_normalization_keeps_word_order(self):
        # 語順まで捨てると「A を B にする」と「B を A にする」が同一指紋になり、逆向きを潰す
        self.assertNotEqual(km._norm_title("A を B にする"), km._norm_title("B を A にする"))

    def test_revive_removes_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "X をやる", "やめた")
            self.assertEqual(km.cmd_revive(cfg, "x を やる！"), 0, "正規化して照合する")
            self.assertEqual(km.load_tombstones(cfg), [])

    def test_revive_reports_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(km.cmd_revive(cfg, "無い"), 1)

    def test_replan_revive_ignores_without_deleting(self):
        # 行を残すのは「再分解の結果を見てから消すか決める」ため
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "X をやる", "やめた")
            created = km._enqueue_specs(cfg, [{"title": "X をやる"}], [], 0.5,
                                        ignore_tombstones=True)
            self.assertEqual(len(created), 1)
            self.assertEqual(len(km.load_tombstones(cfg)), 1, "墓標は残る")

    def _two_charters(self, d: Path):
        """charters/ を 2 つ持つプロジェクト（`charter_names` が 2 件を返す状態）。"""
        (d / "charters").mkdir(parents=True, exist_ok=True)
        for name in ("v1", "v2"):
            (d / "charters" / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")

    def test_revive_scoped_to_a_charter(self):
        # 追記は (指紋, charter) 単位なのに削除が指紋だけだと、片方を revive したつもりで
        # 両方が復活する（次の plan が黙って作り直す＝人は気づけない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._two_charters(d)
            km.append_tombstone(cfg, "X をやる", "v1 でやめた", charter="v1")
            km.append_tombstone(cfg, "X をやる", "v2 でもやめた", charter="v2")
            km.append_tombstone(cfg, "X をやる", "どこでもやめた")
            self.assertEqual(km.cmd_revive(cfg, "X をやる", charter="v1"), 0)
            rest = [(g["title"], g["charter"]) for g in km.load_tombstones(cfg)]
            self.assertEqual(rest, [("X をやる", "v2")], "v1 とタグ無しだけが消える")

    def test_revive_stops_when_the_scope_is_ambiguous(self):
        # 「消しすぎ」は人が気づけないので、曖昧なら消さずに聞く。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._two_charters(d)
            km.append_tombstone(cfg, "X をやる", "v1", charter="v1")
            km.append_tombstone(cfg, "X をやる", "v2", charter="v2")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(km.cmd_revive(cfg, "X をやる"), 2)
            self.assertEqual(len(km.load_tombstones(cfg)), 2, "1 行も消していない")
            self.assertIn("--charter", err.getvalue())

    def test_revive_all_removes_every_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._two_charters(d)
            km.append_tombstone(cfg, "X をやる", "v1", charter="v1")
            km.append_tombstone(cfg, "X をやる", "v2", charter="v2")
            self.assertEqual(km.cmd_revive(cfg, "X をやる", all_charters=True), 0)
            self.assertEqual(km.load_tombstones(cfg), [])

    def test_revive_default_is_unchanged_for_a_single_charter(self):
        # 単一 charter 運用（大多数）では従来と同じ結果になること。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            (d / "charter.md").write_text("# 目標\n", encoding="utf-8")
            km.append_tombstone(cfg, "X をやる", "やめた", charter="default")
            self.assertEqual(km.cmd_revive(cfg, "X をやる"), 0)
            self.assertEqual(km.load_tombstones(cfg), [])

    def test_human_written_tombstone_is_read(self):
        # 人が手で書き足せることが `tombstones.md`（イベント台帳ではない）を選んだ理由の 1 つ
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.tombstones_path(cfg).write_text(
                "# 墓標\n- 手で書いた :: いらない :: 2026-07-26\n", encoding="utf-8")
            self.assertEqual([g["title"] for g in km.load_tombstones(cfg)], ["手で書いた"])


class PlannedTitleTests(unittest.TestCase):
    """S6-3: 人が題を直しても原題で照合が効く（さもないと毎 replan で復活する）。"""

    def test_original_title_still_matches(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.enqueue_task(cfg, {"title": "起動先ドロップダウンの追加", "verify": "true",
                                  "planned_title": "CLI チャットの起動先を選べるようにする"})
            titles = km._existing_titles(cfg)
            self.assertIn("起動先ドロップダウンの追加", titles)
            self.assertIn("CLI チャットの起動先を選べるようにする", titles)
            created = km._enqueue_specs(
                cfg, [{"title": "CLI チャットの起動先を選べるようにする"}], [], 0.5)
            self.assertEqual(created, [], "原題での再提案は重複として落ちる")

    def test_reject_tombstones_both_titles(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = km.enqueue_task(cfg, {"title": "直した題", "verify": "true",
                                      "planned_title": "元の題"})
            km.cmd_reject(cfg, t.id, "いらない")
            self.assertEqual({g["title"] for g in km.load_tombstones(cfg)},
                             {"直した題", "元の題"})


class PlannerSkillTests(unittest.TestCase):
    """S6-1/S6-2: backlog-planner スキルと必須セクションの決定的ゲート。

    出力契約は器で分岐する（`_plan_object_only`）。この組は **1 件ずつ（オブジェクト限定の
    器）** に固定して従来の検証を保つ——環境の定義解決に依存させない。配列契約側は
    PlannerContractRoutingTests が見る。"""

    def setUp(self):
        p = mock.patch.object(km, "_plan_object_only", lambda cfg: True)
        p.start()
        self.addCleanup(p.stop)

    def _cfg(self, d: Path, **kw):
        return cfg_for(d, executor="agent", **kw)

    def _item(self, title="T", **kw):
        base = {"title": title, "why": "目標に効く", "desc": "変更対象と手順",
                "scope": ["src/**"], "risks": ["なし"],
                "acceptance": ["基準A", "基準B"], "size": "M", "workspace": "app"}
        base.update(kw)
        return base

    def test_scope_and_risks_are_required_for_plan_review(self):
        missing = km._validate_backlog_spec(self._item(scope=[], risks=[]))
        self.assertEqual(missing, ["scope", "risks"])

    def _stub_skill(self, name: str, body: str) -> None:
        """cwd（テストは中立な一時 cwd で走る）に差し替えスキルを置く。
        上位に同名スキルを置けば全面的に差し替えられる、という解決順そのものの検証にもなる。"""
        d = Path.cwd() / ".github" / "skills" / name / "scripts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prompt.py").write_text(body, encoding="utf-8")
        self.addCleanup(shutil.rmtree, Path.cwd() / ".github", True)

    def test_skill_prompt_is_used_when_found(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            self._stub_skill("backlog-planner",
                             "import sys, json\n"
                             "spec = json.load(sys.stdin)\n"
                             "sys.stdout.write('STUB PLANNER ' + spec['granularity'])\n")
            prompt = km.build_planner_prompt(cfg, km.build_planner_input(cfg, ch), ch)
            self.assertEqual(prompt, "STUB PLANNER coarse")

    def test_custom_skill_name_is_honored(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, planner_skill="my-planner")
            ch = _charter(cfg)
            self._stub_skill("my-planner", "import sys; sys.stdout.write('MINE')\n")
            self.assertEqual(km.build_planner_prompt(cfg, km.build_planner_input(cfg, ch), ch),
                             "MINE")

    def test_broken_skill_falls_back_to_builtin(self):
        # 計画が止まるとプロジェクトが 1 歩も進まないので、スキルは必須にしない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            self._stub_skill("backlog-planner", "import sys; sys.exit(3)\n")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                prompt = km.build_planner_prompt(cfg, km.build_planner_input(cfg, ch), ch)
            self.assertIn("あなたはプロジェクトを実行可能なタスクに分解するプランナーです", prompt)
            self.assertIn("警告", err.getvalue(), "黙って落ちない")

    def test_missing_skill_falls_back_to_builtin(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, planner_skill="no-such-planner-skill")
            ch = _charter(cfg)
            prompt = km.build_planner_prompt(cfg, km.build_planner_input(cfg, ch), ch)
            self.assertIn("あなたはプロジェクトを実行可能なタスクに分解するプランナーです", prompt)

    def test_builtin_prompt_demands_the_same_required_fields(self):
        # スキルが無い環境でも決定的ゲートを通せる出力を要求しないと、必ず draft/proposed 送りになる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, planner_skill="no-such-planner-skill")
            ch = _charter(cfg)
            prompt = km.build_planner_prompt(cfg, km.build_planner_input(cfg, ch), ch)
            for key in km.PLAN_REQUIRED_KEYS:
                self.assertIn(key, prompt, key)

    def test_planner_review_lists_survive_task_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            spec = km._plan_spec_from_item(ch, self._item(
                desc=["変更対象: src", "手順: UIを更新"],
                scope=["src/ui"],
                risks=["誤操作を防ぐ", "旧形式も読めること"],
            ))
            task = km.enqueue_task(cfg, spec)
            loaded = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(loaded.get("desc"), "変更対象: src ⏎ 手順: UIを更新")
            self.assertEqual([v for k, v in loaded.extra if k == "risks"],
                             ["誤操作を防ぐ", "旧形式も読めること"])

    def test_existing_and_tombstones_are_in_the_input(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            km.enqueue_task(cfg, {"title": "既にあるタスク", "verify": "true"})
            km.enqueue_task(cfg, {"title": "人が直した", "verify": "true", "edited": "human"})
            km.append_tombstone(cfg, "却下したタスク", "いらない")
            spec = km.build_planner_input(cfg, ch)
            self.assertEqual({e["title"] for e in spec["existing"]},
                             {"既にあるタスク", "人が直した"})
            self.assertEqual([e["edited"] for e in spec["existing"] if e["title"] == "人が直した"],
                             ["human"])
            self.assertEqual([g["title"] for g in spec["tombstones"]], ["却下したタスク"])

    def test_existing_carries_rejected_with_reason_from_archive(self):
        """W14 一次防衛の契約: 却下済みも**理由付きで**プランナー入力に載る（生成側で抑止する）。
        載らないとプランナーは同じ意図を出し続け、投入側で黙って落とされる分を払い続ける。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            km.enqueue_task(cfg, {"title": "生きているタスク", "verify": "true", "why": "理由文"})
            km.enqueue_task(cfg, {"title": "却下されるタスク", "verify": "true"})
            tid = [t.id for t in km.load_tasks(cfg.backlog) if t.title == "却下されるタスク"][0]
            self.assertEqual(km.cmd_reject(cfg, tid, "方向性が違う"), 0)
            spec = km.build_planner_input(cfg, ch)
            by = {e["title"]: e for e in spec["existing"]}
            self.assertEqual(by["却下されるタスク"]["status"], "rejected")
            self.assertEqual(by["却下されるタスク"]["reason"], "方向性が違う")
            self.assertEqual(by["生きているタスク"]["summary"], "理由文")   # status/summary も落とさない
            self.assertIn("ready", {e["status"] for e in spec["existing"]})

    def test_rejected_list_is_bounded_to_the_most_recent(self):
        """archive は際限なく育つので却下済みは直近 N 件で有界（全件索引は現役側が持つ）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            for i in range(km._PLANNER_REJECTED_LIMIT + 5):
                p = adir / f"R{i}.md"
                p.write_text(f"## R{i}: 却下 {i}\n- status: rejected\n\n- 却下: 理由 {i}\n",
                             encoding="utf-8")
                os.utime(p, (1000.0 + i, 1000.0 + i))       # 新しいほど i が大きい
            rejected = [e for e in km.build_planner_input(cfg, ch)["existing"]
                        if e["status"] == "rejected"]
            self.assertEqual(len(rejected), km._PLANNER_REJECTED_LIMIT)
            titles = {e["title"] for e in rejected}
            self.assertIn(f"却下 {km._PLANNER_REJECTED_LIMIT + 4}", titles)   # 直近は残る
            self.assertNotIn("却下 0", titles)                                # 最古から落ちる

    def test_builtin_planner_prompt_separates_live_and_rejected(self):
        """スキル不在でも組み込みプロンプトが live / 却下（理由付き）/ 墓標を出し分ける。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, planner_skill="no-such-planner-skill")
            ch = _charter(cfg)
            km.enqueue_task(cfg, {"title": "生きているタスク", "verify": "true"})
            km.append_tombstone(cfg, "墓標のタスク", "二度と出さない")
            spec = km.build_planner_input(cfg, ch)
            spec["existing"].append({"id": "R1", "title": "却下のタスク", "status": "rejected",
                                     "edited": "", "reason": "方向性が違う", "summary": ""})
            prompt = km.build_planner_prompt(cfg, spec, ch)
            self.assertIn("生きているタスク", prompt)
            self.assertIn("却下のタスク", prompt)
            self.assertIn("方向性が違う", prompt)              # 却下理由がプランナーへ届く
            self.assertIn("墓標のタスク", prompt)

    def test_machine_suppression_is_exact_title_match_only(self):
        """W14 二次: 機械が投入を止めるのは墓標との**完全一致**だけ。類似は止めず注記に留める。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.append_tombstone(cfg, "board UI を作る", "いらない")
            stones = km.load_tombstones(cfg)
            self.assertIsNotNone(km.tombstone_hit("Board　UI を作る", stones))   # 正規化して一致
            self.assertIsNone(km.tombstone_hit("board 観測 UI を作る", stones))  # 類似は止めない
            self.assertTrue(km.similar_tombstones("board 観測 UI を作る", stones, 0.5))  # 提示はする

    def test_existing_is_scoped_to_the_charter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            km.enqueue_task(cfg, {"title": "v1 のタスク", "verify": "true", "charter": "v1"})
            km.enqueue_task(cfg, {"title": "v2 のタスク", "verify": "true", "charter": "v2"})
            titles = {e["title"] for e in km.build_planner_input(cfg, ch, "v1")["existing"]}
            self.assertEqual(titles, {"v1 のタスク"})

    def test_missing_sections_trigger_one_retry(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            calls = []

            def fake(prompt, model, purpose=""):
                calls.append(prompt)
                if len(calls) == 1:
                    return json.dumps({"title": "T", "workspace": "app"})     # 全欠落
                if len(calls) == 2:
                    return json.dumps(self._item())
                return json.dumps({"done": True})                             # もう無い

            with mock.patch.object(km, "_run_agent_cli", fake):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(len(calls), 3, "欠落は 1 回だけ再要求し、そのあと次の 1 件を訊く")
            self.assertIn("未記入", calls[1], "何が欠けたかを添えて出し直させる")
            self.assertEqual(specs[0]["acceptance"], ["基準A", "基準B"])
            self.assertNotIn("status", specs[0])

    def test_second_failure_goes_to_human_not_dropped(self):
        # 捨てると「プランナーが何も出さなかった」としか見えず、切り分ける材料が消える
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, plan_review=True)
            ch = _charter(cfg)
            bad = json.dumps([{"title": "T", "workspace": "app"}])
            with mock.patch.object(km, "_run_agent_cli", lambda *a, **k: bad):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(len(specs), 1, "捨てない")
            self.assertEqual(specs[0]["status"], "proposed", "人が直して承認できる場所へ")
            self.assertIn("why", specs[0]["needs_reason"])
            self.assertIn("acceptance", specs[0]["needs_reason"])

    def test_second_failure_is_draft_when_plan_review_off(self):
        # 票が立たない設定では、未記入のまま実行されないよう消化対象外にする
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, plan_review=False)
            ch = _charter(cfg)
            bad = json.dumps([{"title": "T", "workspace": "app"}])
            with mock.patch.object(km, "_run_agent_cli", lambda *a, **k: bad):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(specs[0]["status"], "draft")
            self.assertNotIn("draft", km.CONSUMABLE, "draft は消化対象外")

    def test_plan_sections_warn_does_not_gate(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, plan_sections="warn")
            ch = _charter(cfg)
            bad = json.dumps({"title": "T", "workspace": "app"})
            calls = []

            def fake(prompt, model, purpose=""):
                calls.append(prompt)
                return bad

            with mock.patch.object(km, "_run_agent_cli", fake):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(len(specs), 1, "同じ題を繰り返したら打ち切る")
            self.assertTrue(all("未記入" not in c for c in calls), "warn は再要求しない")
            self.assertNotIn("status", specs[0])

    def test_repeated_title_stops_the_loop(self):
        """1 件ずつ出させる契約の止め所——同じ題を返し始めたら進んでいない。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            calls = []

            def fake(prompt, model, purpose=""):
                calls.append(prompt)
                return json.dumps({"tasks": [self._item("同じ題")]})   # 包みも 1 段は剥がす

            with mock.patch.object(km, "_run_agent_cli", fake):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual([sp["title"] for sp in specs], ["同じ題"])
            self.assertEqual(len(calls), 2, "2 件目で打ち切る（上限まで回さない）")
            self.assertEqual(km.build_planner_input(cfg, ch, produced=["同じ題"])["produced"],
                             ["同じ題"], "既に出した題はプランナー入力に載る")

    def test_planned_title_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            out = json.dumps([self._item("元の題")])
            with mock.patch.object(km, "_run_agent_cli", lambda *a, **k: out):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(specs[0]["planned_title"], "元の題")

    def test_size_is_normalized_and_bad_values_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, plan_sections="warn")
            ch = _charter(cfg)
            outs = iter([json.dumps(self._item("A", size="l")),
                         json.dumps(self._item("B", size="でかい")),
                         json.dumps({"done": True})])
            with mock.patch.object(km, "_run_agent_cli", lambda *a, **k: next(outs)):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(specs[0]["size"], "L")
            self.assertNotIn("size", specs[1])

    def test_agent_failure_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)

            def boom(*a, **k):
                raise RuntimeError("CLI が無い")

            with mock.patch.object(km, "_run_agent_cli", boom):
                self.assertEqual(km.plan_via_agent(cfg, ch), [])


class PlannerContractRoutingTests(unittest.TestCase):
    """plan の出力契約は**器で選ぶ**（2026-08-31 の 1 件ずつ化の適用範囲を器へ限定）。

    1 件ずつはオブジェクトしか返せない器（`--format json`＝`json_object_only`）への
    手当てであり、配列を返せる器（クラウド CLI ほか）へ課すとタスク K 件に K+1 回の
    呼び出し（毎回 charter 全文）を払う。ここでは (a) 定義の宣言どおりに器を読むこと、
    (b) 配列の器では配列契約 1 回で受けること、を固定する。"""

    def _cfg(self, d: Path, **kw):
        return cfg_for(d, executor="agent", **kw)

    def _item(self, title="T", **kw):
        base = {"title": title, "why": "目標に効く", "desc": "変更対象と手順",
                "scope": ["src/**"], "risks": ["なし"],
                "acceptance": ["基準A", "基準B"], "size": "M", "workspace": "app"}
        base.update(kw)
        return base

    def test_object_only_is_read_from_the_definition_not_the_spelling(self):
        with mock.patch.object(km, "_agent_for", lambda purpose: ("ollama-json", None)), \
                mock.patch.object(km, "load_agent_plugin",
                                  lambda cli: {"json_object_only": True}):
            self.assertTrue(km._plan_object_only(None))
        with mock.patch.object(km, "_agent_for", lambda purpose: ("kiro", None)), \
                mock.patch.object(km, "load_agent_plugin", lambda cli: {}):
            self.assertFalse(km._plan_object_only(None))

    def test_unresolvable_definition_falls_to_one_at_a_time(self):
        # 1 件ずつはどちらの器でも動く側。定義が引けないだけで plan を止めない
        def boom(cli):
            raise RuntimeError("定義がありません")
        with mock.patch.object(km, "_agent_for", lambda purpose: ("kiro", None)), \
                mock.patch.object(km, "load_agent_plugin", boom):
            self.assertTrue(km._plan_object_only(None))

    def test_array_container_plans_in_one_call(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            calls = []

            def fake(prompt, model, purpose=""):
                calls.append(prompt)
                return json.dumps([self._item("A"), self._item("B")])

            with mock.patch.object(km, "_plan_object_only", lambda cfg: False), \
                    mock.patch.object(km, "_run_agent_cli", fake):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual([sp["title"] for sp in specs], ["A", "B"])
            self.assertEqual(len(calls), 1, "配列の器はタスク数に呼び出し回数を比例させない")
            self.assertIn("JSON 配列のみ", calls[0])
            self.assertNotIn("オブジェクト 1 件のみ", calls[0])

    def test_array_contract_retries_missing_sections_once(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d, plan_review=True)
            ch = _charter(cfg)
            calls = []

            def fake(prompt, model, purpose=""):
                calls.append(prompt)
                return json.dumps([{"title": "T", "workspace": "app"}])   # 必須欠落のまま

            with mock.patch.object(km, "_plan_object_only", lambda cfg: False), \
                    mock.patch.object(km, "_run_agent_cli", fake):
                specs = km.plan_via_agent(cfg, ch)
            self.assertEqual(len(calls), 2, "欠落は 1 回だけ再要求する")
            self.assertIn("未記入", calls[1])
            self.assertEqual(specs[0]["status"], "proposed", "2 回目も欠けたら人の目へ")

    def test_single_container_prompt_keeps_the_object_contract(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            ch = _charter(cfg)
            seen = []

            def fake(prompt, model, purpose=""):
                seen.append(prompt)
                return json.dumps({"done": True})

            with mock.patch.object(km, "_plan_object_only", lambda cfg: True), \
                    mock.patch.object(km, "_run_agent_cli", fake):
                km.plan_via_agent(cfg, ch)
            self.assertIn("オブジェクト 1 件のみ", seen[0])
            self.assertNotIn("JSON 配列のみ", seen[0])


class ShippedPlannerSkillTests(unittest.TestCase):
    """同梱スキル（.github/skills/backlog-planner）の出力契約。

    テストは中立な一時 cwd で走る（`_shared` の隔離）ため解決順には載らない。
    ここではスクリプトを直接叩いて、契約に要る文言が落ちていないことだけを見る。"""

    SCRIPT = (Path(__file__).resolve().parents[3]
              / ".github" / "skills" / "backlog-planner" / "scripts" / "prompt.py")
    TASK_SCHEMA = Path(__file__).resolve().parents[3] / "schemas" / "task.schema.json"

    def _run(self, spec: dict) -> str:
        proc = subprocess.run([sys.executable, str(self.SCRIPT)],
                              input=json.dumps(spec, ensure_ascii=False),
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_script_exists(self):
        self.assertTrue(self.SCRIPT.is_file(), self.SCRIPT)

    def test_task_schema_defines_risks_as_review_guidance(self):
        prop = json.loads(self.TASK_SCHEMA.read_text(encoding="utf-8"))["properties"]["risks"]
        self.assertEqual(prop["type"], ["array", "string"])
        self.assertEqual(prop["items"], {"type": "string"})
        self.assertIn("完了条件にはしない", prop["description"])

    def test_task_schema_accepts_legacy_todo_status(self):
        status = json.loads(self.TASK_SCHEMA.read_text(encoding="utf-8"))["properties"]["status"]
        self.assertIn("todo", status["enum"])
        self.assertIn("後方互換", status["description"])

    def test_required_fields_are_demanded(self):
        out = self._run({"charter": "目標", "granularity": "coarse"})
        for key in ("why", "desc", "acceptance", "size", "workspace"):
            self.assertIn(key, out, key)
        self.assertIn("受入基準の配列", out)

    def test_review_description_fields_are_demanded(self):
        out = self._run({"charter": "目標", "granularity": "coarse"})
        self.assertIn('"scope"', out)
        self.assertIn('"risks"', out)
        self.assertIn('"なし"', out)
        self.assertIn('"desc": [', out)

    def test_existing_and_tombstones_are_rendered(self):
        out = self._run({
            "charter": "目標",
            "existing": [{"title": "既にある", "status": "ready", "edited": "human"},
                         {"title": "普通の", "status": "proposed"},
                         {"title": "廃止済みの施策", "status": "rejected",
                          "reason": "方針転換で不要"}],
            "tombstones": [{"title": "却下した", "reason": "別案にした"}],
        })
        self.assertIn("既にある", out)
        self.assertIn("人が確定済み", out, "人の編集はプランナーへ明示する")
        self.assertIn("却下した", out)
        self.assertIn("別案にした", out, "却下理由も届ける（違う切り口の判断材料）")
        self.assertIn("意図が同じ・似ている項目は出力しない", out,
                      "抑止はタイトル一致でなく意図ベース（スキルの責務）")
        self.assertIn("廃止済みの施策", out)
        self.assertIn("方針転換で不要", out, "却下理由はプランナーの判断材料として届ける")

    def test_notes_and_retry_are_optional(self):
        base = self._run({"charter": "目標"})
        self.assertNotIn("観点メモ", base)
        with_notes = self._run({"charter": "目標", "notes": "気になっていること"})
        self.assertIn("気になっていること", with_notes)
        with_retry = self._run({"charter": "目標", "retry": "「T」: why が未記入"})
        self.assertIn("必須項目をすべて埋めて", with_retry)

    def test_granularity_directive_changes(self):
        coarse = self._run({"charter": "目標", "granularity": "coarse"})
        finest = self._run({"charter": "目標", "granularity": "finest"})
        self.assertIn("ユーザーストーリー相当", coarse)
        self.assertIn("原子的に分解", finest)

    def test_contract_selects_the_output_shape(self):
        # 契約は agent-project が器に問い合わせて渡す（スキルは写しを持たない）。
        # 既定（single）は 1 件ずつ、array は配列一括——両方の文言が生きていること。
        single = self._run({"charter": "目標"})
        self.assertIn("オブジェクト 1 件のみ", single)
        self.assertIn('{"done": true}', single)
        arr = self._run({"charter": "目標", "contract": "array"})
        self.assertIn("JSON 配列のみ", arr)
        self.assertNotIn("オブジェクト 1 件のみ", arr)
        self.assertIn('[{"title"', arr, "例も配列の形で示す")

    def test_bad_input_is_rejected(self):
        proc = subprocess.run([sys.executable, str(self.SCRIPT)], input="not json",
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 2)


class IntakeReconcileTests(unittest.TestCase):
    """S6-6: 随時取り込みの整合パス（enqueue / inbox / intake_cmd の共通入口）。"""

    def test_duplicate_is_not_enqueued_twice(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.enqueue_task(cfg, {"title": "検収 カード に 証跡 を 出す", "verify": "true"})
            # 同じ題は止める（正規化して完全一致）
            t, msg = km.enqueue_reconciled(cfg, {"title": "検収 カード に 証跡 を 出す"})
            self.assertIsNone(t)
            self.assertIn("同じ題", msg)
            self.assertEqual(len(km.load_tasks(cfg.backlog)), 1)
            # 似ているだけなら止めず、注記を付けて通す（機械の抑止は取り返しがつかない）
            t2, msg2 = km.enqueue_reconciled(cfg, {"title": "検収 カード に 証跡 を 表示 する"})
            self.assertIsNotNone(t2)
            self.assertEqual(msg2, "")
            self.assertIn("既存タスクに似ています", dict(t2.extra).get("note", ""))

    def test_new_task_passes_through(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t, msg = km.enqueue_reconciled(cfg, {"title": "まったく別の作業", "verify": "true"})
            self.assertIsNotNone(t)
            self.assertEqual(msg, "")

    def test_charter_tag_is_attached(self):
        # タグ無しタスクはスコープ判定の穴を毎回踏むので、入口で埋める
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            _charter(cfg)
            t, _ = km.enqueue_reconciled(cfg, {"title": "新規", "verify": "true"})
            self.assertEqual(t.get("charter"), km.charter_names(cfg)[0])

    def test_tombstoned_intake_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.append_tombstone(cfg, "やめた作業", "不要")
            t, msg = km.enqueue_reconciled(cfg, {"title": "やめた作業"})
            self.assertIsNone(t)
            self.assertIn("墓標", msg)


class CharterScopeTests(unittest.TestCase):
    """S6-6 の同時修正: タグ無しタスクがスコープから落ちていたバグ。"""

    def test_untagged_task_belongs_to_any_charter(self):
        t = km.Task(id="T1", title="X")
        self.assertTrue(km.task_belongs_to_charter(t, "v1"))
        self.assertTrue(km.task_belongs_to_charter(t, None))

    def test_tagged_task_is_scoped(self):
        t = km.Task(id="T1", title="X", extra=[("charter", "v1")])
        self.assertTrue(km.task_belongs_to_charter(t, "v1"))
        self.assertFalse(km.task_belongs_to_charter(t, "v2"))


class NotesTests(unittest.TestCase):
    """S6-7: 観点メモ。**plan は自動では消費しない**（人が押したときだけ）。"""

    def test_notes_are_read_and_archived(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            nd = km.notes_dir(cfg)
            nd.mkdir(parents=True)
            (nd / "a.md").write_text("観点その 1", encoding="utf-8")
            (nd / "b.md").write_text("観点その 2", encoding="utf-8")
            body, files = km.read_notes(cfg)
            self.assertIn("観点その 1", body)
            self.assertIn("観点その 2", body)
            self.assertEqual(km.archive_notes(cfg, files), 2)
            self.assertEqual(list(nd.glob("*.md")), [])
            self.assertEqual(len(list((nd / "archive").glob("*.md"))), 2)

    def test_plan_does_not_consume_notes_automatically(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent")
            ch = _charter(cfg)
            (km.notes_dir(cfg)).mkdir(parents=True)
            (km.notes_dir(cfg) / "a.md").write_text("勝手にタスクにしないで", encoding="utf-8")
            seen = []
            with mock.patch.object(km, "_run_agent_cli",
                                   lambda p, *a, **k: seen.append(p) or "[]"):
                km.plan_via_agent(cfg, ch)
            self.assertNotIn("勝手にタスクにしないで", seen[0])

    def test_distill_notes_injects_and_archives(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent")
            _charter(cfg)
            (km.notes_dir(cfg)).mkdir(parents=True)
            (km.notes_dir(cfg) / "a.md").write_text("証跡が薄いときに気づきたい", encoding="utf-8")
            seen = []
            out = json.dumps([{"title": "証跡の薄さを検収カードで警告する", "why": "w",
                               "desc": "d", "acceptance": ["基準A"], "size": "S"}])

            def fake(prompt, *a, **k):
                seen.append(prompt)
                return out

            with mock.patch.object(km, "_repo_map_generate", lambda *a, **k: ""):
                with mock.patch.object(km, "_run_agent_cli", fake):
                    with contextlib.redirect_stdout(io.StringIO()):
                        rc = km.cmd_distill_notes(cfg)
            self.assertEqual(rc, 0)
            self.assertIn("証跡が薄いときに気づきたい", seen[0], "メモがプロンプトへ載る")
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertEqual(titles, ["証跡の薄さを検収カードで警告する"])
            self.assertEqual(list(km.notes_dir(cfg).glob("*.md")), [], "消費したメモは archive へ")

    def test_distill_notes_keeps_notes_when_all_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent")
            _charter(cfg)
            km.enqueue_task(cfg, {"title": "既にあるタスク", "verify": "true"})
            (km.notes_dir(cfg)).mkdir(parents=True)
            (km.notes_dir(cfg) / "a.md").write_text("メモ", encoding="utf-8")
            out = json.dumps([{"title": "既にあるタスク", "why": "w", "desc": "d",
                               "acceptance": ["基準A"], "size": "S"}])
            with mock.patch.object(km, "_repo_map_generate", lambda *a, **k: ""):
                with mock.patch.object(km, "_run_agent_cli", lambda *a, **k: out):
                    with contextlib.redirect_stdout(io.StringIO()):
                        km.cmd_distill_notes(cfg)
            self.assertEqual(len(list(km.notes_dir(cfg).glob("*.md"))), 1,
                             "投入ゼロならメモは残す（人が書いたものを黙って消さない）")

    def test_distill_notes_reports_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent")
            _charter(cfg)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(km.cmd_distill_notes(cfg), 1)


class CommandDropTests(unittest.TestCase):
    """dashboard からの指示は commands/ ドロップ 1 本（CLI は使わない）。"""

    def _drop(self, cfg, rec):
        d = km.commands_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "viewer-x.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    def test_distill_notes_drop_is_ingested(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent")
            _charter(cfg)
            km.notes_dir(cfg).mkdir(parents=True)
            (km.notes_dir(cfg) / "a.md").write_text("メモ", encoding="utf-8")
            self._drop(cfg, {"command": "distill-notes", "reason": "dashboard から"})
            out = json.dumps([{"title": "メモ由来のタスク", "why": "w", "desc": "d",
                               "acceptance": ["基準A"], "size": "S"}])
            with mock.patch.object(km, "_repo_map_generate", lambda *a, **k: ""):
                with mock.patch.object(km, "_run_agent_cli", lambda *a, **k: out):
                    with contextlib.redirect_stdout(io.StringIO()):
                        done = km.ingest_commands(cfg)
            self.assertIn("distill-notes:project", done)
            self.assertEqual([t.title for t in km.load_tasks(cfg.backlog)], ["メモ由来のタスク"])

    def test_distill_notes_without_notes_is_not_left_as_error(self):
        # メモを書く前に押しただけで .err の残骸を積まない（heal と同じ判断）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent")
            _charter(cfg)
            self._drop(cfg, {"command": "distill-notes"})
            with contextlib.redirect_stdout(io.StringIO()):
                done = km.ingest_commands(cfg)
            self.assertIn("distill-notes:project", done)
            self.assertEqual(list(km.commands_dir(cfg).glob("*.err")), [])
            self.assertEqual(list(km.commands_dir(cfg).glob("*.json")), [])


class CliWiringTests(unittest.TestCase):
    """新しいサブコマンドが CLI から届くこと（配線漏れは静かな機能欠落になる）。"""

    def test_new_subcommands_are_registered(self):
        src = (Path(km.__file__).parent / "cli.py").read_text(encoding="utf-8")
        for name in ("revive", "distill-notes"):
            self.assertIn(f'sub.add_parser("{name}"', src, name)
            self.assertIn(f'"{name}": lambda', src, f"{name} のディスパッチ")
        # サブコマンド一覧の集合に載っていないと「サブコマンド無し＝serve」の既定に飲まれ、
        # `agent-project revive …` が常駐体の起動になってしまう
        i = src.index("_subcommands = {")
        block = src[i:src.index("}", i)]
        for name in ("revive", "distill-notes"):
            self.assertIn(f'"{name}"', block, f"{name} を _subcommands へ")

    def test_acceptance_flags_exist(self):
        src = (Path(km.__file__).parent / "cli.py").read_text(encoding="utf-8")
        self.assertIn('enq.add_argument("--acceptance"', src)
        self.assertIn('rv.add_argument("--acceptance"', src)


class SpecRoutingTests(unittest.TestCase):
    """S7: spec の 3 段ルーティング（スキップ / ライト / フル）。"""

    def _cfg(self, d: Path, **kw):
        return cfg_for(d, spec_track=True, spec_threshold_full=3,
                       spec_threshold_light=2, **kw)

    def _task(self, cfg, assess, tid="T1"):
        return km.enqueue_task(cfg, {"id": tid, "title": f"作業 {tid}", "verify": "true",
                                     "status": "ready", "assess": assess})

    def test_three_way_routing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            policy = km.Policy()
            for tid, assess, want in (("T1", "c=1 r=1 a=1", ""),
                                      ("T2", "c=2 r=1 a=1", "light"),
                                      ("T3", "c=3 r=1 a=1", "full")):
                t = self._task(cfg, assess, tid)
                created = km.route_spec_tasks(cfg, [t], policy)
                if not want:
                    self.assertEqual(created, [], f"{tid} はスキップ")
                else:
                    self.assertEqual(created[0].get("spec_kind"), want, tid)

    def test_light_verify_needs_only_design(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            self.assertEqual(km._spec_verify(cfg, "T1", "light"), "test -s specs/T1/design.md")
            self.assertIn("spec.md", km._spec_verify(cfg, "T1", "full"))

    def test_light_instructions_forbid_the_other_two_files(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            t = km.Task(id="T1-spec", title="x",
                        extra=[("spec_for", "T1"), ("spec_kind", "light")])
            text = km._spec_instructions(cfg, t)
            self.assertIn("design.md を 1 枚だけ", text)
            self.assertIn("タスク分解も書かない", text)

    def test_policy_force_goes_full(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            policy = km.Policy(spec=["作業"])
            t = self._task(cfg, "c=1 r=1 a=1")
            created = km.route_spec_tasks(cfg, [t], policy)
            self.assertEqual(created[0].get("spec_kind"), "full", "明示強制はフル")

    def test_light_is_not_expanded(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            t = self._task(cfg, "c=2 r=1 a=1")
            s = km.route_spec_tasks(cfg, [t], km.Policy())[0]
            # spec 前段を done として archive へ（承認済みの体裁）
            cfg.archive_dir().mkdir(parents=True, exist_ok=True)
            s.status = "done"
            (cfg.archive_dir() / f"{s.id}.md").write_text(km.serialize_task(s), encoding="utf-8")
            km.delete_task_file(cfg, s)
            t = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            t.set("spec_kind", "light")     # 元タスク側にも段を持たせる（route が付ける印）
            km.persist_task(cfg, t)
            created = km.expand_spec_tasks(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(created, [], "ライトは展開しない")
            after = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            self.assertEqual(after.get("spec_expanded"), "light",
                             "`none`（tasks.md が壊れていた）とは区別する")

    def test_design_md_is_injected_into_act(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            (km.specs_root(cfg) / "T1").mkdir(parents=True)
            (km.specs_root(cfg) / "T1" / "design.md").write_text("変更方針: ここを直す",
                                                                 encoding="utf-8")
            t = km.Task(id="T1", title="x", extra=[("route", "spec")])
            self.assertIn("変更方針: ここを直す", km.spec_context(cfg, t))


class SpecThresholdConfigTests(unittest.TestCase):
    """S7: 閾値の後方互換とクランプ（仕様草案の `full=4` は採点上限 3 に到達しない）。"""

    def _thresholds(self, **kw):
        ns = types.SimpleNamespace(**{"spec_threshold": None, "spec_threshold_full": None,
                                      "spec_threshold_light": None, **kw})
        return km._spec_thresholds(ns)

    def test_defaults(self):
        self.assertEqual(self._thresholds(),
                         {"spec_threshold": 3, "spec_threshold_full": 3,
                          "spec_threshold_light": 2})

    def test_legacy_key_is_read_as_full(self):
        got = self._thresholds(spec_threshold=2)
        self.assertEqual(got["spec_threshold_full"], 2, "既存設定はフルの閾値として効く")

    def test_explicit_full_wins_over_legacy(self):
        self.assertEqual(self._thresholds(spec_threshold=2, spec_threshold_full=3)
                         ["spec_threshold_full"], 3)

    def test_out_of_range_is_clamped(self):
        # 採点は各軸 1〜3 なので 4 は到達しない（仕様草案の誤り）
        self.assertEqual(self._thresholds(spec_threshold_full=4)["spec_threshold_full"], 3)
        self.assertEqual(self._thresholds(spec_threshold_full=0)["spec_threshold_full"], 1)

    def test_light_never_exceeds_full(self):
        got = self._thresholds(spec_threshold_full=2, spec_threshold_light=3)
        self.assertEqual(got["spec_threshold_light"], 2, "ライトの窓を消さない")


class RepoMapPrefetchTests(unittest.TestCase):
    """S6-2 / S7-3: 作業概要と影響範囲は既存コード文脈が無いと書けない。"""

    def test_plan_path_generates_even_when_opt_in_is_off(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent", repo_map=False)
            ch = _charter(cfg)
            with mock.patch.object(km, "_repo_map_generate", lambda *a, **k: "理解の要約"):
                with mock.patch.object(km, "_repo_head_sha", lambda *a, **k: "abc123"):
                    km.ensure_repo_maps(cfg, ch, force=True)
            self.assertIn("理解の要約",
                          (km.context_dir(cfg) / "app.md").read_text(encoding="utf-8"))

    def test_opt_in_still_gates_other_paths(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent", repo_map=False)
            ch = _charter(cfg)
            with mock.patch.object(km, "_repo_map_generate", lambda *a, **k: "理解の要約"):
                km.ensure_repo_maps(cfg, ch)
            self.assertFalse(km.context_dir(cfg).exists())

    def test_generation_failure_does_not_block_plan(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, executor="agent", repo_map=False)
            ch = _charter(cfg)
            with mock.patch.object(km, "_repo_head_sha", lambda *a, **k: "abc123"):
                with mock.patch.object(km, "_repo_map_generate", lambda *a, **k: ""):
                    km.ensure_repo_maps(cfg, ch, force=True)     # 例外を出さない


class FlowGranularityTests(unittest.TestCase):
    """内側（agent-flow）へ渡す粒度は flow_granularity（既定 auto）。

    外側の granularity（バックログの INVEST 粒度・既定 coarse）は**別のノブ**で、内側へ
    流してはいけない——coarse を渡すと agent-flow の complexity 導出が常に上書きされ、
    work ノードレンジが 1〜3 に固定される（実際、複雑なタスクでも「まとめて 1〜3 ノード」に
    畳まれていた）。"""

    def _cmd(self, d, **kw):
        cfg = cfg_for(d, agent_flow="agent-flow", **kw)
        t = km.Task(id="T1", title="x", verify="true")
        return km.build_agent_flow_cmd(t, cfg, use_git=False)

    def test_default_is_auto_so_inner_derives_from_complexity(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = self._cmd(Path(d))
            i = cmd.index("--granularity")
            self.assertEqual(cmd[i + 1], "auto")
            # agent-flow の --granularity は **run サブコマンドの引数**（run より後ろ）。
            # 計画しないサブコマンドで受け付けないようグローバルから移されたので、run より
            # 前に置くと `unrecognized arguments` で毎回失敗する。
            self.assertGreater(i, cmd.index("run"), "サブコマンド名より後ろに置く")

    def test_outer_granularity_does_not_leak_into_the_inner_graph(self):
        """外側を fine/coarse にしても内側は auto のまま（ノブの独立）。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = self._cmd(Path(d), granularity="fine")
            self.assertEqual(cmd[cmd.index("--granularity") + 1], "auto")

    def test_verification_plan_is_passed_as_argv(self):
        """検証計画は argv `--verification-plan` で渡す（env 渡しは不安定として人が却下・
        2026-07-31。両ツールは同時更新が前提で旧 agent-flow との混在は非対応）。"""
        with tempfile.TemporaryDirectory() as d:
            cmd = self._cmd(Path(d))
            i = cmd.index("--verification-plan")
            self.assertLess(i, cmd.index("run"), "グローバル引数（サブコマンドより前）")
            plan = json.loads(cmd[i + 1])
            self.assertEqual([c["command"] for c in plan["commands"]], ["true"])
            self.assertTrue(str(plan.get("digest", "")).startswith("sha256:"))

    def test_explicit_flow_granularity_is_forwarded(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = self._cmd(Path(d), flow_granularity="finest")
            self.assertEqual(cmd[cmd.index("--granularity") + 1], "finest")


if __name__ == "__main__":
    unittest.main()


class IntakeDuplicateSuppressionTests(unittest.TestCase):
    """W14 二次: 現役タスク相手に機械が止めるのは正規化タイトルの完全一致だけ（類似は注記）。"""

    def _cfg(self, d):
        return cfg_for(d, planner="none")

    def test_exact_title_is_blocked_with_a_record(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.enqueue_task(cfg, {"title": "board UI を作る", "verify": "true"})
            spec, why = km.reconcile_intake(cfg, {"title": "Board　UI を作る"})  # 全角・大小差
            self.assertIsNone(spec)
            self.assertIn("同じ題", why)

    def test_similar_title_is_admitted_with_a_note(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.enqueue_task(cfg, {"title": "board UI を作る", "verify": "true"})
            spec, why = km.reconcile_intake(cfg, {"title": "board 観測 UI を作る"})
            self.assertEqual(why, "")
            self.assertIsNotNone(spec)
            self.assertIn("既存タスクに似ています", spec["note"])
            self.assertIn("board UI を作る", spec["note"])

    def test_planner_intake_records_the_skip_and_notes_similarity(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            km.enqueue_task(cfg, {"title": "board UI を作る", "verify": "true"})
            created = km._enqueue_specs(cfg, [{"title": "board UI を作る", "verify": "true"},
                                              {"title": "board 観測 UI を作る", "verify": "true"}],
                                        [], 0.5)
            titles = [t.title for t in created]
            self.assertEqual(titles, ["board 観測 UI を作る"])       # 類似は投入される
            self.assertIn("同じ題の既存タスクがあるため投入を見送り",
                          cfg.journal.read_text(encoding="utf-8"))   # 完全一致は記録して見送る
            note = dict(created[0].extra).get("note", "")
            self.assertIn("既存タスクに似ています", note)
