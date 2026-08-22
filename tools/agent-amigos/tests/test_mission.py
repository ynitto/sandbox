"""agent-amigos の単体テスト — mission（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class NormalizeTests(unittest.TestCase):
    def test_integrator_auto_added_and_defaults(self):
        mission, roles = normalize_mission(base_spec())
        ids = [r["id"] for r in roles]
        self.assertIn("integrator", ids)
        self.assertEqual(mission["assignment_policy"], "first-come")
        self.assertEqual(mission["convergence"]["question_timeout"], 2)
        self.assertEqual(mission["budget"]["on_exhausted"], "wrap-up")

    def test_rejects_duplicate_and_reserved_ids(self):
        spec = base_spec()
        spec["roles"].append({"id": "impl"})
        with self.assertRaises(SystemExit):
            normalize_mission(spec)
        spec = base_spec()
        spec["roles"][0]["id"] = "owner"
        with self.assertRaises(SystemExit):
            normalize_mission(spec)

    def test_rejects_unknown_collaborator_and_invalid_policies(self):
        spec = base_spec()
        spec["roles"][1]["collaborates_with"] = ["ghost"]
        with self.assertRaises(SystemExit):
            normalize_mission(spec)
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(assignment_policy="lottery"))
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(acceptance="codd-gate"))   # 将来拡張（未対応）
        # P2 で追加されたポリシーは通る
        normalize_mission(base_spec(assignment_policy="owner-picks"))
        normalize_mission(base_spec(acceptance="agent"))

    def test_rejects_unknown_convergence_and_budget_keys(self):
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(convergence={"review_round": 2}))
        with self.assertRaises(SystemExit):
            normalize_mission(base_spec(budget={"execution_minute": 30}))


class MatchesRoleTests(unittest.TestCase):
    """ロール要件 requires.{tags,cli,repos} とノード能力のマッチング。"""

    def test_tags_and_cli(self):
        from agent_amigos.assign import matches_role
        role = {"id": "impl", "requires": {"tags": ["python"], "cli": "codex"}}
        self.assertTrue(matches_role(role, ["python"], ["codex"]))
        self.assertFalse(matches_role(role, ["rust"], ["codex"]))
        self.assertFalse(matches_role(role, ["python"], ["claude"]))

    def test_requires_repos_by_name_and_url(self):
        from agent_amigos.assign import matches_role
        repos = {"app": {"url": "git@h:team/app.git", "owns": ["**"]},
                 "docs": {"url": "git@h:team/docs.git", "readonly": True}}
        role_name = {"id": "impl", "requires": {"repos": ["app"]}}
        role_url = {"id": "impl", "requires": {"repos": ["git@h:team/app"]}}  # .git 揺れ
        role_miss = {"id": "impl", "requires": {"repos": ["other"]}}
        self.assertTrue(matches_role(role_name, [], [], repos))
        self.assertTrue(matches_role(role_url, [], [], repos))
        self.assertFalse(matches_role(role_miss, [], [], repos))
        # repos 宣言が無いノードは requires.repos を満たせない
        self.assertFalse(matches_role(role_name, [], [], None))
        # requires.repos が無いロールは repos 宣言に関係なく通る
        self.assertTrue(matches_role({"id": "r"}, [], [], None))


class EnvelopeTests(AmigosTestCase):
    def test_safe_relpath_rejects_traversal(self):
        for bad in ("../x", "a/../../x", "/etc/passwd", "~/x", ""):
            with self.assertRaises(ValueError):
                safe_relpath(bad)
        self.assertEqual(safe_relpath("./a/b.txt"), "a/b.txt")

    def test_apply_actions_rejects_invalid(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        runner = AmigoRunner(self.bus, mid, "impl", "n1")
        from agent_amigos.bus import TurnTxn
        txn = TurnTxn()
        st = {"turn": 0, "open_questions": {}}
        actions = [
            {"kind": "write_artifact", "path": "../escape.txt", "content": "x"},
            {"kind": "send", "to": "ghost", "type": "info", "body": "x"},
            {"kind": "declare_done", "approve": True},     # impl は approver でない
            {"kind": "nope"},
            {"kind": "write_artifact", "path": "ok.txt", "content": "ok"},
        ]
        applied, rejected = runner._apply_actions(txn, actions, roles,
                                                  roles["impl"], st, 0)
        self.assertEqual(rejected, 4)
        # 不正な approve 付き declare_done は「検証してから変異」なので状態は汚れない
        self.assertIsNone(st.get("done_round"))
        self.assertIn("write_artifact", applied)
        txn.apply(self.bus)
        self.assertTrue(os.path.isfile(
            os.path.join(mp.artifacts_dir("impl"), "ok.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(mp.artifacts_dir("impl"), "..", "escape.txt")) and
            os.path.isfile(os.path.join(mp.root, "artifacts", "escape.txt")))


class MissionSchemaTests(AmigosTestCase):
    """schemas/mission.schema.json（正典）と normalize_mission の突き合わせ。
    実行時は stdlib パーサが検証する（jsonschema 依存なし）— スキーマの enum/既定値が
    実装とズレていないことをテストで担保する。"""

    def _schema(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "mission.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_enums_match_implementation(self):
        schema = self._schema()
        props = schema["properties"]["mission"]["properties"]
        self.assertEqual(props["assignment_policy"]["enum"], ["first-come", "owner-picks"])
        self.assertEqual(props["staffing_policy"]["enum"], ["self-staff", "wait", "fail"])
        self.assertEqual(props["acceptance"]["enum"], ["manual", "agent"])
        conv = props["convergence"]["properties"]
        from agent_amigos.mission import DONE_WHEN_MODES
        self.assertEqual(conv["done_when"]["enum"], list(DONE_WHEN_MODES))
        budget = props["budget"]["properties"]
        self.assertEqual(budget["on_exhausted"]["enum"], ["wrap-up", "fail"])

    def test_schema_defaults_match_normalize(self):
        from agent_amigos.mission import (BUDGET_DEFAULTS, CONVERGENCE_DEFAULTS,
                                          DEFAULTS)
        schema = self._schema()
        props = schema["properties"]["mission"]["properties"]
        self.assertEqual(props["assignment_policy"]["default"], DEFAULTS["assignment_policy"])
        self.assertEqual(props["staffing_policy"]["default"], DEFAULTS["staffing_policy"])
        self.assertEqual(props["acceptance"]["default"], DEFAULTS["acceptance"])
        self.assertEqual(props["staffing_timeout"]["default"], DEFAULTS["staffing_timeout"])
        conv = props["convergence"]["properties"]
        for key in ("quiescence_turns", "review_rounds", "question_timeout"):
            self.assertEqual(conv[key]["default"], CONVERGENCE_DEFAULTS[key], key)
        self.assertEqual(conv["done_when"]["default"], CONVERGENCE_DEFAULTS["done_when"])
        budget = props["budget"]["properties"]
        for key in ("execution_minutes", "per_role_turns", "soft_ratio", "on_exhausted"):
            self.assertEqual(budget[key]["default"], BUDGET_DEFAULTS[key], key)

    def test_normalized_roles_validate_against_role_schema_keys(self):
        _mission, roles = __import__("agent_amigos.mission", fromlist=["normalize_mission"]) \
            .normalize_mission(base_spec())
        role_props = set(self._schema()["properties"]["roles"]["items"]["properties"])
        for role in roles:
            self.assertTrue(set(role).issubset(role_props),
                            f"スキーマに無いキー: {set(role) - role_props}")


class CommandSchemaTests(AmigosTestCase):
    """schemas/amigos-command.schema.json（commands/ ドロップの契約）と
    commands._dispatch の突き合わせ。投函側（agent-dashboard writeCommand・人）と
    取り込み側でコマンド一覧・必須フィールドがズレていないことをテストで担保する。"""

    def _schema(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "amigos-command.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_commands_match_dispatch(self):
        schema = self._schema()
        self.assertEqual(schema["properties"]["command"]["enum"],
                         ["post", "build-team", "claim", "assign", "restaff", "accept",
                          "reject", "cancel", "say"])
        # oneOf の const 一覧も enum と一致する（宣言漏れ・重複なし）
        consts = [e["properties"]["command"]["const"] for e in schema["oneOf"]]
        self.assertEqual(consts, schema["properties"]["command"]["enum"])

    def test_schema_required_fields_match_dispatch_validation(self):
        """スキーマの required と _dispatch の実検証が一致する:
        必須欠落のドロップは .rejected になり、必須が揃えば成功する。"""
        from agent_amigos.commands import ingest_commands
        from agent_amigos.configfile import commands_dir
        home = os.path.join(self.tmp, "home")
        cdir = commands_dir(home)
        os.makedirs(cdir, exist_ok=True)

        def drop(name, rec):
            with open(os.path.join(cdir, name), "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)

        # post: roles 必須・design か design_file が必須（スキーマの required / anyOf と同じ）
        drop("bad-post.json", {"command": "post", "design": "# d\n"})
        drop("bad-post2.json", {"command": "post",
                                "roles": [{"id": "impl", "mission": "実装"}]})
        drop("ok-post.json", {"command": "post", "mission_id": "am-schema",
                              "design": "# d\n", "title": "t",
                              "mission": {"staffing_timeout": 0},
                              "roles": [{"id": "impl", "mission": "実装",
                                         "deliverables": ["main.py"]}]})
        ingest_commands(self.bus, "owner-node", home)
        names = sorted(os.listdir(cdir))
        self.assertIn("bad-post.json.rejected", names, "roles 欠落は棄却")
        self.assertIn("bad-post2.json.rejected", names, "design/design_file 欠落は棄却")
        self.assertNotIn("ok-post.json", names, "必須が揃えば処理されて消える")
        self.assertIsNotNone(read_json(self.bus.mission("am-schema").mission_json()))
        # 未知コマンド（enum 外）も棄却
        drop("bad-cmd.json", {"command": "rm-rf"})
        ingest_commands(self.bus, "owner-node", home)
        self.assertIn("bad-cmd.json.rejected", sorted(os.listdir(cdir)))

    def test_posted_mission_matches_bus_read_contract(self):
        """バスへ書かれる mission.json が $defs.posted_mission（外部ビュアーの読取契約）に
        合う: 実行時フィールド（id / owner_node / posted_at）が required どおり存在する。"""
        schema = self._schema_mission()
        required = schema["$defs"]["posted_mission"]["required"]
        self.assertEqual(sorted(required), ["id", "owner_node", "posted_at"])
        mid = self.post(mid="am-posted")
        doc = read_json(self.bus.mission(mid).mission_json())
        for key in required:
            self.assertIn(key, doc, f"posted mission.json に {key} が無い")
        self.assertEqual(doc["id"], mid)
        self.assertEqual(doc["owner_node"], "owner-node")

    def _schema_mission(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "mission.schema.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)


class TopologyTests(AmigosTestCase):
    """同期討論の通信トポロジ（各席が読む相手の制限）。"""

    def test_topology_neighbors(self):
        from agent_amigos.mission import topology_neighbors
        self.assertEqual([topology_neighbors(i, 5, "ring") for i in range(5)],
                         [[1, 4], [0, 2], [1, 3], [2, 4], [0, 3]])
        self.assertEqual(topology_neighbors(0, 4, "star"), [1, 2, 3])
        self.assertEqual(topology_neighbors(2, 4, "star"), [0])
        self.assertEqual(topology_neighbors(0, 7, "tree"), [1, 2])
        self.assertEqual(sorted(topology_neighbors(1, 7, "tree")), [0, 3, 4])

    def test_topology_requires_rounds(self):
        with self.assertRaises(SystemExit):
            normalize_mission({"roles": [{"id": "d", "seats": 3, "topology": "ring"}]})
        with self.assertRaises(SystemExit):
            normalize_mission({"roles": [{"id": "d", "seats": 3, "rounds": 2,
                                          "topology": "mesh"}]})

    def test_star_spoke_reads_only_hub(self):
        from agent_amigos.runner import AmigoRunner
        spec = {"mission": {"title": "d", "goal": "g"},
                "roles": [{"id": "d", "mission": "討論", "seats": 4, "rounds": 2,
                           "topology": "star"}]}
        mid = self.post(spec, mid="am-star")
        roles = load_roles(self.bus.mission(mid))
        spoke = roles["d#2"]
        r = AmigoRunner(self.bus, mid, "d#2", "owner-node")
        peers = sorted(roles)
        self.assertEqual(r._topology_readable(spoke, peers), ["d#0"])   # ハブのみ
        hub = roles["d#0"]
        rh = AmigoRunner(self.bus, mid, "d#0", "owner-node")
        self.assertEqual(rh._topology_readable(hub, peers), ["d#1", "d#2", "d#3"])


class StaffingPolicyTests(AmigosTestCase):
    """staffing_policy の検証と `fail` の終端（仕様書 §7.1・設計書 §6.3）。

    以前は値が素通しで、`fail` を指定しても誰も見ていなかった（`wait` と同じく open の
    まま滞留）。タイポも黙って通り、指定した意味が無かった。
    """

    def _spec(self, policy):
        spec = base_spec(staffing_policy=policy, staffing_timeout=0)
        spec["roles"].append({"id": "specialist", "mission": "誰も持てない専門",
                              "required": True, "requires": {"tags": ["no-such-tag"]}})
        return spec

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            normalize_mission(base_spec(staffing_policy="self_staff"))
        self.assertIn("staffing_policy", str(cm.exception))

    def test_fail_terminates_when_required_role_stays_unfilled(self):
        mid = self.post(self._spec("fail"), mid="am-fail")
        # 能力の合わないノードは specialist を埋められない → 充足しないまま失効
        self.daemon(tags=[]).cycle()
        self.assertEqual(self.phase(mid), "failed")

    def test_wait_keeps_the_mission_open(self):
        mid = self.post(self._spec("wait"), mid="am-wait")
        self.daemon(tags=[]).cycle()
        self.assertEqual(self.phase(mid), "open")

    def test_fail_does_not_kill_a_mission_that_already_started(self):
        """走り出した後にノードが落ちて席が空くのは再募集の領分（§5.3）。
        区別しないと、夜中の 1 台のクラッシュが進行中のミッションを巻き添えにする。"""
        mid = self.post(base_spec(staffing_policy="fail", staffing_timeout=3600),
                        mid="am-fail-running")
        self.daemon().cycle()                          # 全ロールが埋まり手番が進む
        mp = self.bus.mission(mid)
        self.assertTrue(read_json(mp.roster()))
        mission = load_mission(mp)
        mission["staffing_timeout"] = 0                # 募集期限が切れた状況にする
        write_json_atomic(mp.mission_json(), mission)
        write_json_atomic(mp.roster(), {})             # 担当が消えた（クラッシュ相当）
        self.assertEqual(self.phase(mid), "open")      # failed ではなく再募集へ戻る

    def test_owner_is_told_why_it_failed(self):
        mid = self.post(self._spec("fail"), mid="am-fail-notice")
        d = self.daemon(tags=[])
        d.cycle()
        d.cycle()                                    # 2 巡目でも重ねて鳴らさない
        mp = self.bus.mission(mid)
        notices = [m for m in read_inbox(mp, "owner")
                   if "failed で終端" in str(m.get("subject", ""))]
        self.assertEqual(len(notices), 1)


class DeadlineNoticeTests(AmigosTestCase):
    """wall-clock の締切超過はオーナーへ通知するだけ（自動 fail はしない、設計書 §6.3）。"""

    def test_overrun_notifies_owner_once_without_failing(self):
        past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
        mid = self.post(base_spec(deadline=past), mid="am-deadline")
        d = self.daemon()
        d.cycle()
        d.cycle()
        mp = self.bus.mission(mid)
        notices = [m for m in read_inbox(mp, "owner")
                   if "締切を超過" in str(m.get("subject", ""))]
        self.assertEqual(len(notices), 1)
        self.assertNotEqual(self.phase(mid), "failed")   # 締切超過では終端しない

    def test_no_notice_before_the_deadline(self):
        future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        mid = self.post(base_spec(deadline=future), mid="am-deadline-ok")
        self.daemon().cycle()
        self.assertFalse([m for m in read_inbox(self.bus.mission(mid), "owner")
                          if "締切を超過" in str(m.get("subject", ""))])
