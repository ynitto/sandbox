from __future__ import annotations
# bus.py — 元 agent-flow.py の 374-1083 行目（機械分割・内容無改変ではなくなった。
# 常駐一本化 P0・W0-8 で claim/lease の実装を agentcore.protocol へ委譲した — 設計 §4.1・R1）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# Bus — メッセージバス抽象（M1: ローカルディレクトリ実装）
# --------------------------------------------------------------------------
from agentcore import protocol  # noqa: E402


class Bus:
    def __init__(self, root: str, run_id: str):
        self.root = root
        self.run_id = run_id
        self.runs_root = os.path.join(root, "runs")
        self.inbox_dir = os.path.join(root, "inbox")
        self.inbox_claims_dir = os.path.join(root, "inbox", "claims")
        # cancel マーカー（人の明示指示）。inbox/ 配下＝git 同期でリモート優先で全 PC へ伝わり、
        # 監視主体（daemon/run）がこれを見て run スコープで恒久停止する。
        self.inbox_cancels_dir = os.path.join(root, "inbox", "cancels")
        self.run_dir = os.path.join(root, "runs", run_id)
        self.tasks_dir = os.path.join(self.run_dir, "tasks")
        self.claims_dir = os.path.join(self.run_dir, "claims")
        # waits/<node>.json … 人の承認待ち等でノードを「park（保留）」した記録。executor が
        # 決着まで worker をブロックする代わりに DeferDecision を投げ、worker が claim を
        # 解放してここに書き残す。監視主体（daemon/run）の service_waits がバッチで再確認する。
        # runs/ 配下＝git バスで同期され、daemon 消失を跨いで生存する（孤児 reclaim と同じ耐性）。
        self.waits_dir = os.path.join(self.run_dir, "waits")
        self.interactions_dir = os.path.join(self.run_dir, "interactions")
        self.results_dir = os.path.join(self.run_dir, "results")
        self.artifacts_dir = os.path.join(self.run_dir, "artifacts")
        self.events_dir = os.path.join(self.run_dir, "events")
        self.meta_path = os.path.join(self.run_dir, "meta.json")
        self.graph_path = os.path.join(self.run_dir, "graph.json")
        self.final_path = os.path.join(self.run_dir, "final.json")
        # inherited/<旧run-id>.json … リトライ（世代交代）で削除した先行 run の墓標（要約）。
        # inherit_from が先行 run を掃除する前に meta・final・results（出力は抜粋）を残す。
        # これが無いと、完走したのに verify NG でリトライされた run の成果記録が bus から
        # 完全に消え、viewer（agent-dashboard）がポーリングしていなければ二度と見られない。
        self.inherited_dir = os.path.join(self.run_dir, "inherited")

    # --- 転送フック（ローカルバスでは no-op、GitBus が上書き） ---
    def sync_pull(self) -> None:
        pass

    def sync_push(self, msg: str = "") -> None:
        pass

    # --- セットアップ ---
    def ensure_dirs(self) -> None:
        for d in (self.tasks_dir, self.claims_dir, self.waits_dir, self.interactions_dir,
                  self.results_dir, self.events_dir):
            os.makedirs(d, exist_ok=True)

    def ensure_run(self, request: str, workspace: "dict | None" = None,
                   references: "list[dict] | None" = None,
                   verification_plan: "dict | None" = None,
                   readonly: bool = False, workspaces: "list[dict] | None" = None) -> None:
        self.ensure_dirs()
        # workset（書込先の集合）。`workspaces` があればそれが正典で、`workspace` は
        # primary（先頭要素）として引き続き載る＝旧読み手はそのまま動く。1 要素のときは
        # `workspaces` を書かない——meta の形を N=1 で変えないため（§5.2）。
        workset = normalize_workset(workspaces if workspaces is not None
                                    else ([workspace] if workspace else []))
        workspace = workset_primary(workset) or workspace
        meta = read_json(self.meta_path)
        if meta is None:
            meta = {
                "request": request,
                # この run（=バックログ単位）の書込先リポジトリの primary（worker が clone し、
                # 作業ブランチを作って作業する）。None なら読み取り専用 run（commit/push しない）。
                "workspace": workspace or None,
                **({"workspaces": workset} if len(workset) > 1 else {}),
                # 参照リポジトリ（読むだけ・書き込まない）。executor がイシュー/プロンプトに描画する。
                "references": list(references or []),
                # run 全体の実行権限。動的 fan-out / replan で後から追加されたノードも
                # worker が読み取り専用として強制する（ノード宣言だけに依存しない）。
                "readonly": readonly is True,
                "status": "planning",
                "created_at": now_iso(),
            }
            # 統一 verify: agent-project が確定した検証計画（digest 付き）。planner の自由記述へは
            # 混ぜず、成果 revision 確定後の専用 runner（run_verification_plan）だけが実行する。
            if isinstance(verification_plan, dict):
                meta["verification_plan"] = verification_plan
                # workspace の無い run（ローカル実行・成果は投入ノードの作業ツリーに出る）の
                # 差分基準。投入時点の HEAD を固定しておき、runner が $AGENT_BASE_REV として
                # 検証コマンドへ渡す（act 前 HEAD——旧 agent-project verify の verify_env と同じ）。
                base = _vp_result_rev(os.getcwd())
                if base:
                    meta["base_rev"] = base
            write_json_atomic(self.meta_path, meta)
            return
        # 再投入（resume / inherit 後）: 投入側が今回渡してきた契約で meta の欠けを補う。
        # worker と検証 runner は meta しか読まないため、作成時に workspace ルーティングが
        # 決まらず read-only で固まった run は、以後の再投入で argv に --workspace を渡し
        # 続けても永久に commit/push されない（成果ブランチが生まれない静かな機能欠落）。
        # workspace は「無い → 有る」の補充だけ行い、既存 spec の差し替えはしない
        # （inherit が done の commit を保つため base を旧ブランチへ差した spec を壊さない）。
        changed = False
        if readonly is True and meta.get("readonly") is not True:
            meta["readonly"] = True
            changed = True
        cur_ws = meta.get("workspace")
        if isinstance(workspace, dict) and workspace.get("url") \
                and not (isinstance(cur_ws, dict) and cur_ws.get("url")):
            meta["workspace"] = workspace
            if len(workset) > 1:
                meta["workspaces"] = workset
            changed = True
        elif len(workset) > 1 and not meta.get("workspaces") \
                and isinstance(cur_ws, dict) and cur_ws.get("url"):
            # 「無い → 有る」の補充は workset 全体で行う。primary だけが載っている run に
            # 後から集合が渡ってきたら、primary が同じときに限り集合で補う（既存要素の
            # 差し替えはしない＝世代交代が旧ブランチへ差した base を壊さない）。
            if str(workset[0].get("url") or "") == str(cur_ws.get("url") or ""):
                meta["workspaces"] = [{**workset[0], **cur_ws, "name": workset[0].get("name")}] \
                    + workset[1:]
                changed = True
        # verification_plan は最新の投入正本へ更新する。settle は常に「今の正本」と検算する
        # ため、作成時の古い plan（例: workspace 未解決時の digest）のままだと runner の
        # receipt が fail-close で捨てられ続ける。inherit 直後（_seed_from は plan を
        # 引き継がない）の欠落もここで埋まる。
        if isinstance(verification_plan, dict) \
                and meta.get("verification_plan") != verification_plan:
            meta["verification_plan"] = verification_plan
            changed = True
        if changed:
            write_json_atomic(self.meta_path, meta)

    def snapshot_instructions(self) -> bool:
        """グローバル指示（agent-instructions 契約）をこの run の meta.json へ固定する。
        投入ノードの instructions.json を描画し additive キー instructions:{revision,text} を書く。
        GitBus 同期で全ワーカーへ届く＝run 単位の一貫性基準（ワーカーはローカルを読まない）。
        冪等: 既にスナップショット済み・終端・request にマーカー混入済み・無効/空なら何もしない。"""
        meta = read_json(self.meta_path) or {}
        if meta.get("status") in TERMINAL:
            return False
        if isinstance(meta.get("instructions"), dict):
            return False
        if AGENT_INSTRUCTIONS_MARKER in str(meta.get("request", "")):
            return False
        snap = local_instructions_snapshot()
        if not snap:
            return False
        meta["instructions"] = snap
        write_json_atomic(self.meta_path, meta)
        return True

    def snapshot_context(self, path: "str | None") -> bool:
        """プロジェクト文脈（案 H・`--context-file`）をこの run の meta.json へ固定する。
        agent-project が stable_prefix 有効時に渡すテキストを、run 単位の一貫性基準として
        全ワーカー・planner・evaluator へ配る（instructions と同型・冪等）。
        既にスナップショット済み・終端・無効/空なら何もしない。"""
        meta = read_json(self.meta_path) or {}
        if meta.get("status") in TERMINAL:
            return False
        if isinstance(meta.get("context"), dict):
            return False
        if AGENT_CONTEXT_MARKER in str(meta.get("request", "")):
            return False
        snap = local_context_snapshot(path)
        if not snap:
            return False
        meta["context"] = snap
        write_json_atomic(self.meta_path, meta)
        return True

    def snapshot_knowledge(self, path: "str | None") -> bool:
        """知識注入メタ（`--knowledge-file`）を run の meta.json へ素通し固定する。
        agent-project が rules.md content hash と skill 参照を渡す。本ツールは中身を解釈せず、
        receipt / result / final へ引き継げるよう meta.knowledge に置くだけ（冪等）。"""
        meta = read_json(self.meta_path) or {}
        if meta.get("status") in TERMINAL:
            return False
        if isinstance(meta.get("knowledge"), dict):
            return False
        if not path:
            return False
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or not raw:
            return False
        meta["knowledge"] = raw
        write_json_atomic(self.meta_path, meta)
        return True

    def run_workspace(self) -> "dict | None":
        """この run の書込先 primary の spec（meta に記録）。無ければ None（読み取り専用 run）。

        workset（複数の書込先）の run でも **primary を返す**——このアクセサを読む旧来の
        呼び出し（gitlab executor の起票先解決等）が、集合を知らないまま先頭要素で
        従来どおり動けるようにするため。集合が要る呼び出しは `run_workset()` を使う。"""
        return workset_primary(self.run_workset())

    def run_workset(self) -> "list[dict]":
        """この run の書込先の集合（順序付き・先頭が primary）。読み取り専用 run では空。

        `meta.workspaces` があればそれが正典。無ければ `meta.workspace` 1 件を 1 要素の
        workset として読む（旧 run・1 要素 run はここを通って従来と同じ結果になる）。"""
        meta = read_json(self.meta_path) or {}
        raw = meta.get("workspaces")
        if isinstance(raw, list) and raw:
            return normalize_workset(raw)
        w = meta.get("workspace")
        return normalize_workset([w]) if isinstance(w, dict) and w.get("url") else []

    def run_references(self) -> "list[dict]":
        """この run の参照リポジトリ spec 一覧（読むだけ。meta に記録、executor が描画する）。"""
        meta = read_json(self.meta_path) or {}
        r = meta.get("references")
        return [s for s in r if isinstance(s, dict) and s.get("url")] if isinstance(r, list) else []

    def run_readonly(self) -> bool:
        """動的追加ノードも含む、この run 全体の読み取り専用契約。"""
        meta = read_json(self.meta_path) or {}
        return meta.get("readonly") is True

    # --- メタ / グラフ ---
    def set_status(self, status: str) -> None:
        """run の進捗 status を書く。既に終端（done/failed/cancelled）なら上書きしない。

        cancel / 完了後に orchestrator が set_status("running") を呼んでも、人が止めた／確定済みの
        終端を resurrect しない（_orch_check_canceled と resume/plan 経路の競合を防ぐ）。"""
        meta = read_json(self.meta_path) or {}
        cur = meta.get("status")
        if cur in TERMINAL and status != cur:
            return
        meta["status"] = status
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)

    def set_phase(self, phase: str, who: str) -> None:
        """非終端 run の現在段階を更新し、遷移時刻と同じ内容の event を残す。"""
        meta = read_json(self.meta_path) or {}
        if meta.get("status") in TERMINAL or meta.get("phase") == phase:
            return
        stamp = now_iso()
        meta["phase"] = phase
        meta["phase_started_at"] = stamp
        meta["updated_at"] = stamp
        write_json_atomic(self.meta_path, meta)
        self.event(who, "phase", phase=phase)

    def note_executor(self, executor: str) -> None:
        """この run を駆動する executor 名を meta に記録する（冪等）。
        viewer が「GitLab 連携の UI を出すか」を executor で切り替えるための表示用メタデータ
        （gitlab executor を使っていない run にイシュー突き合わせ等を出しても意味がない）。"""
        ex = str(executor or "").strip()
        meta = read_json(self.meta_path) or {}
        if not ex or meta.get("executor") == ex:
            return
        meta["executor"] = ex
        write_json_atomic(self.meta_path, meta)

    def note_delegation(self, delegation: "dict | None") -> None:
        """この run の来歴（委譲公示板 = agent-board 由来なら delegation id）を meta に記録する（冪等）。
        board が inbox/<id>.json に載せた delegation:{id, board} を run meta へ引き回し、
        viewer / status が「板の委譲 <id> 由来」と表示できるようにする（additive・未知でも無害）。"""
        if not isinstance(delegation, dict) or not delegation.get("id"):
            return
        meta = read_json(self.meta_path) or {}
        if meta.get("delegation") == delegation:
            return
        meta["delegation"] = delegation
        write_json_atomic(self.meta_path, meta)

    def get_status(self):
        meta = read_json(self.meta_path)
        return meta.get("status") if meta else None

    def write_graph(self, graph) -> None:
        write_json_atomic(self.graph_path, graph)

    def read_graph(self):
        return read_json(self.graph_path)

    # --- タスク ---
    def write_task(self, task) -> None:
        write_json_atomic(os.path.join(self.tasks_dir, f"{task['id']}.json"), task)

    def task_ids(self):
        g = self.read_graph()
        return list(g["nodes"].keys()) if g else []

    # --- claim（名前空間付き claim ＋ 決定的タイブレーク） ---
    #
    # 各クレーマは自分専用のファイル <claim_dir>/<who>.json を書く（ファイル名が
    # 衝突しないので git で add/add コンフリクトにならない）。勝者は全 claim のうち
    # lease 内で「(ts, who) が最小」の 1 件に決定的に定まる。ローカル/ git どちらの
    # 転送でも同じロジックで唯一の勝者が決まる。タスクにも要求にも同じ仕組みを使う。
    def _claim_dir(self, node_id: str) -> str:
        return os.path.join(self.claims_dir, node_id)

    def _list_claims_in(self, claim_dir: str):
        return protocol.list_claims(claim_dir)

    def _winner_in(self, claim_dir: str):
        """lease 内の claim から決定的に勝者を選ぶ。無ければ None。
        実体は agentcore.protocol.winner（flow のタスク claim・amigos のロール claim・
        板の入札で共通の (ts, who) タイブレーク実装 — 設計 §4.1・R1）。"""
        return protocol.winner(claim_dir)

    def _write_claim_in(self, claim_dir: str, who: str, lease_sec: float) -> None:
        protocol.write_claim(claim_dir, who, lease_sec)

    def _try_claim_in(self, claim_dir: str, who: str, lease_sec: float, msg: str) -> bool:
        # 同一マシン上の並行 claim を排他ロックで直列化する（ロックはバス外＝
        # git に乗せない一時ファイル）。これで「先着読みの勝者」と「決定的
        # タイブレークの勝者」の食い違いによる二重勝者を防ぐ。
        # git 分散（別マシン）はクローンごとに別ロックなので直列化されないが、
        # その整合は sync_pull 後の決定的タイブレーク＋lease が担う。
        return protocol.try_claim(
            claim_dir, who, lease_sec,
            on_write=lambda: self.sync_push(msg),
            on_sync=self.sync_pull,  # 他ノードの claim を取り込んでから勝敗判定
            # 敗者が自分の claim ファイルを残すと、勝者の park/release 後に敗者自己の
            # lease が _winner になり、誰も動いていないのに node_state=claimed の
            # zombie になる（git 分散で両者が書けた場合）。protocol.try_claim が
            # 負けた自分の分だけ消してから、以下で push する。
            on_withdraw=lambda: self.sync_push(f"claim withdraw {who}"))

    # 後方互換のためのノード単位ラッパ
    def _winner(self, node_id: str):
        return self._winner_in(self._claim_dir(node_id))

    def _write_claim(self, node_id: str, who: str, lease_sec: float) -> None:
        self._write_claim_in(self._claim_dir(node_id), who, lease_sec)

    def extend_claim(self, node_id: str, who: str, lease_sec: float) -> bool:
        """実行中ワーカーの心拍用: 自分の claim の lease_until **だけ**を延長する。
        ts / claimed_at は書き換えない（新しい ts を振り直すと勝者タイブレークの根拠が
        動いてしまう）。claim が消えている（release / withdraw 済み）か、lease 失効中に
        他者が勝者になっていれば延長せず False を返す——失った claim を心拍が無条件に
        書き戻すと二重実行になるため。実体は agentcore.protocol.extend_claim。"""
        return protocol.extend_claim(self._claim_dir(node_id), who, lease_sec)

    def try_claim(self, node_id: str, who: str, lease_sec: float) -> bool:
        self.sync_pull()
        if self.has_result(node_id):
            return False
        return self._try_claim_in(self._claim_dir(node_id), who, lease_sec,
                                  f"claim {node_id} by {who}")

    def release_claim(self, node_id: str, who: str) -> None:
        """自分の claim ファイルを消して node を手放す（park 時に worker スロットを空けるため）。
        心拍（Heartbeat）を停止してから呼ぶこと——停止前に消すと直後の心拍が claim を書き戻す。"""
        protocol.release_claim(self._claim_dir(node_id), who)
        self.sync_push(f"release {node_id} by {who}")

    # --- human interaction（request=engine / response=human / resolution=engine） ---
    def interaction_dir(self, interaction_id: str) -> str:
        return os.path.join(self.interactions_dir, interaction_id)

    def interaction_request_path(self, interaction_id: str) -> str:
        return os.path.join(self.interaction_dir(interaction_id), "request.json")

    def read_interaction_request(self, interaction_id: str):
        return read_json(self.interaction_request_path(interaction_id))

    def write_interaction_request(self, request: dict) -> bool:
        path = self.interaction_request_path(request["interaction_id"])
        if os.path.exists(path):
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, request)
        return True

    def interaction_responses_dir(self, interaction_id: str) -> str:
        return os.path.join(self.interaction_dir(interaction_id), "responses")

    def write_interaction_response(self, response: dict) -> bool:
        directory = self.interaction_responses_dir(response["interaction_id"])
        path = os.path.join(directory, f"{response['response_id']}.json")
        if os.path.exists(path):
            return False
        os.makedirs(directory, exist_ok=True)
        write_json_atomic(path, response)
        return True

    def list_interaction_responses(self, interaction_id: str) -> "list[dict]":
        directory = self.interaction_responses_dir(interaction_id)
        if not os.path.isdir(directory):
            return []
        out = []
        for name in sorted(os.listdir(directory)):
            if name.endswith(".json"):
                value = read_json(os.path.join(directory, name))
                if isinstance(value, dict):
                    out.append(value)
        return out

    def interaction_resolution_path(self, interaction_id: str) -> str:
        return os.path.join(self.interaction_dir(interaction_id), "resolution.json")

    def read_interaction_resolution(self, interaction_id: str):
        return read_json(self.interaction_resolution_path(interaction_id))

    def write_interaction_resolution(self, resolution: dict) -> bool:
        path = self.interaction_resolution_path(resolution["interaction_id"])
        if os.path.exists(path):
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, resolution)
        return True

    # --- park（保留待ち）プロトコル ---
    #
    # 承認待ち等の長い外部待機を worker スロットから切り離すための記録。claim と同じ
    # lease セマンティクス（wait_lease_until が生存判定）に相乗りし、失効すれば node_state は
    # pending に縮退＝full worker が token 再アタッチで拾い直す（行き止まりにしない）。
    # レコードにトークン等の秘密は載せない（バスは git 同期・共有されうるため）。
    def wait_path(self, node_id: str) -> str:
        return os.path.join(self.waits_dir, f"{node_id}.json")

    def read_wait(self, node_id: str):
        return read_json(self.wait_path(node_id))

    def write_wait(self, node_id: str, rec: dict) -> None:
        os.makedirs(self.waits_dir, exist_ok=True)
        write_json_atomic(self.wait_path(node_id), rec)

    def clear_wait(self, node_id: str) -> None:
        """park 記録を消す（決着して result を書いたとき／node を pending へ戻すとき）。"""
        try:
            os.remove(self.wait_path(node_id))
        except OSError:
            pass

    def list_waits(self) -> "list[dict]":
        """この run の park 記録一覧（id を含む dict の列）。無ければ空。"""
        out = []
        if not os.path.isdir(self.waits_dir):
            return out
        for name in sorted(os.listdir(self.waits_dir)):
            if name.endswith(".json"):
                rec = read_json(os.path.join(self.waits_dir, name))
                if rec:
                    rec.setdefault("id", name[:-5])
                    out.append(rec)
        return out

    def wait_is_live(self, node_id: str) -> bool:
        """park 記録が生存（wait_lease_until が未失効）か。失効＝監視主体が居ない/止まった
        とみなし、node_state は pending へ縮退させて full worker の再アタッチに委ねる。"""
        rec = self.read_wait(node_id)
        return bool(rec) and float(rec.get("wait_lease_until", 0) or 0) >= time.time()

    def open_wait_count(self) -> int:
        """この run で「起票済み・未決着」の park 記録数（throttle の同時イシュー上限に使う）。
        throttled（イシュー未作成で枠待ち）のレコードは数えない。"""
        return sum(1 for r in self.list_waits()
                   if not r.get("throttled") and (r.get("issue") or {}).get("iid") is not None)

    # --- 中間成果物（ファイル）プロトコル ---
    #
    # output/data（JSON）に乗らない大きな成果物（生成ファイル等）は、ノードごとの
    # 決定的なディレクトリ artifacts/<node-id>/ に置く。パスが node-id から一意に
    # 決まるので、後続タスクは依存ノードの同じパスを読んで成果物を発見できる。
    # （バスのファイルとして push/pull で同期されるため分散でも同じパスで参照可能。）
    def node_artifact_dir(self, node_id: str) -> str:
        return os.path.join(self.artifacts_dir, node_id)

    def ensure_artifact_dir(self, node_id: str) -> str:
        d = self.node_artifact_dir(node_id)
        os.makedirs(d, exist_ok=True)
        return d

    def list_artifacts(self, node_id: str) -> "list[str]":
        """ノードの成果物ディレクトリ内のファイル絶対パス一覧（無ければ空）。"""
        d = self.node_artifact_dir(node_id)
        if not os.path.isdir(d):
            return []
        out = []
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                out.append(os.path.join(dirpath, fn))
        return sorted(out)

    # --- 結果 ---
    def result_path(self, node_id: str) -> str:
        return os.path.join(self.results_dir, f"{node_id}.json")

    def has_result(self, node_id: str) -> bool:
        return os.path.exists(self.result_path(node_id))

    def read_result(self, node_id: str):
        return read_json(self.result_path(node_id))

    def write_result(self, node_id: str, who: str, status: str, output: str,
                     data=None, artifacts=None, node: "str | None" = None,
                     kind: "str | None" = None, agent_cli: "str | None" = None,
                     model: "str | None" = None, context_allocation: "dict | None" = None,
                     dependency_context: "dict | None" = None,
                     escalation: "dict | None" = None, methods: "list[str] | None" = None,
                     trial: "dict | None" = None, tier: "str | None" = None,
                     selection_source: "str | None" = None, pinned: bool = False,
                     selection_reason: "str | None" = None,
                     execution_decision: "dict | None" = None,
                     operation_class: "str | None" = None,
                     local_patch_blockers: "list[str] | None" = None,
                     contract_dropped: "list | None" = None) -> None:
        """ノードの結果を確定する。

        `node` は**実行した PC**（node_id の正規形）。`who`（worker の名義）にも PC 名は
        入るが、読み手（`agent-flow status` の内訳・doctor・dashboard の run 詳細）が名義を
        文字列として割って PC を推測するのは「同じ規則の 2 実装」を作る——書き手が事実として
        1 フィールドに残す。旧い結果（このフィールドが無い）を読む側は `who` へ落ちる。

        `agent_cli` / `model` は**実行に使ったエージェント**（agent executor のみ）。
        実効解決（`_agent_for`: control 上書き・縮退込み）は実行時にしか分からないため、
        読み手（dashboard のノード詳細）が設定から推測するのではなく書き手が事実を残す。"""
        rec = {
            "id": node_id,
            "who": who,
            "status": status,
            "output": output,
            "finished_at": now_iso(),
        }
        if node:
            rec["node"] = node
        if kind:
            rec["kind"] = kind
        if agent_cli:
            rec["agent_cli"] = agent_cli
        if model:
            rec["model"] = model
        if tier:
            rec["tier"] = tier
        if selection_source:
            rec["selection_source"] = selection_source
        if pinned:
            rec["pinned"] = True
        if selection_reason:
            rec["selection_reason"] = selection_reason
        if execution_decision is not None:
            # 実行 receipt v2 の execution_decision ブロック（schemas/execution-receipt）。
            # 読み手（dashboard / agent-audit）は設定から実モデルを再推測せず、これを正典にする。
            rec["execution_decision"] = execution_decision
        if operation_class:
            rec["operation_class"] = operation_class
        if local_patch_blockers:
            rec["local_patch_blockers"] = list(local_patch_blockers)
        if contract_dropped:
            # 宣言（operation / decision）が形式不正で剥がされた事実と理由。
            rec["contract_dropped"] = list(contract_dropped)
        if data is not None:  # 構造化成果（任意）。エージェント間を JSON で流す
            rec["data"] = data
        if context_allocation is not None:
            rec["context_allocation"] = context_allocation
        if dependency_context is not None:
            rec["dependency_context"] = dependency_context
        if escalation is not None:
            rec["escalation"] = escalation
        if methods:
            rec["methods"] = list(methods)
        if isinstance(trial, dict):
            rec["trial"] = dict(trial)
        if artifacts:  # 生成した中間成果物（run_dir 相対パス）。後続が参照できる
            rec["artifacts"] = list(artifacts)
        write_json_atomic(self.result_path(node_id), rec)

    # --- 状態導出 ---
    def node_state(self, node_id: str) -> str:
        # 優先順: result（終端） > claimed（生存 lease） > waiting（生存 wait_lease） > pending。
        # waiting は「park 済みで監視主体が生存確認中」。wait_lease 失効時は pending へ縮退させ、
        # full worker が token 再アタッチで拾えるようにする（park を行き止まりにしない）。
        res = self.read_result(node_id)
        if res:
            return res.get("status", "done")
        if self._winner(node_id) is not None:
            return "claimed"
        if self.wait_is_live(node_id):
            return "waiting"
        if os.path.exists(os.path.join(self.tasks_dir, f"{node_id}.json")):
            return "pending"
        return "unknown"

    def all_terminal(self) -> bool:
        ids = self.task_ids()
        return bool(ids) and all(self.node_state(i) in TERMINAL for i in ids)

    def retry_failed(self, clear_heal: bool = True) -> "list[str]":
        """failed 状態の run を「再実行できる状態」へ戻す。失敗ノード（results が failed）の結果と
        claim を消して pending へ戻し（＝再 claim・再実行の対象にする）、確定済み done ノードは温存する。
        併せて meta の終端・孤児簿記（failure_reason/superseded/orphaned/resume_count 等）を掃除し、
        status を running に戻す。戻したノード id 一覧を返す（commit/push は呼び出し側）。

        failed run はそのままでは再開しても全ノードが終端（node_state=failed）のまま静止し、
        何も再実行されない。人/消費者の明示 retry か auto-heal（レイヤ4）でこの reset を行い、
        失敗した所だけをやり直す。clear_heal: 人の明示 retry（既定）は heal 簿記も白紙に戻す。
        auto-heal は False で呼び、heal_count / heal_progress を heal 横断で数え続ける。"""
        reset: "list[str]" = []
        for nid in self.task_ids():
            res = self.read_result(nid)
            if res and res.get("status") == "failed":
                try:
                    os.remove(self.result_path(nid))
                except OSError:
                    pass
                shutil.rmtree(self._claim_dir(nid), ignore_errors=True)   # 失効前の claim も掃除
                reset.append(nid)
        meta = read_json(self.meta_path) or {}
        keys = ["failure_reason", "superseded", "superseded_by",
                "resume_count", "resume_progress"]
        if clear_heal:
            keys += ["heal_count", "heal_progress", "heal_next_at", "heal_exhausted"]
        else:
            meta.pop("heal_next_at", None)   # 再武装は次の失敗時（arm_heal）に行う
        for k in keys:
            meta.pop(k, None)
        meta["status"] = "running"
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)
        return reset

    # --- auto-heal（レイヤ4）簿記: transient 起因で failed 終端した run の自動再開 ---

    def heal_class(self, run_id: str) -> "str | None":
        """failed run が auto-heal 候補なら、そのトリアージ class（transient / quota）を返す。
        人の cancel（status=cancelled）・世代交代（superseded）・heal 上限超過（heal_exhausted）・
        タグ無し（内容の失敗）・auth/env（人が直す）は対象外＝None。"""
        meta = self.run_meta(run_id) or {}
        if meta.get("status") != "failed" or meta.get("superseded") or meta.get("heal_exhausted"):
            return None
        m = _AGENT_ERROR_TAG_RE.search(str(meta.get("failure_reason") or ""))
        cls = m.group(1) if m else None
        return cls if cls in ("transient", "quota") else None

    def arm_heal(self, run_id: str, cooldown: float) -> float:
        """heal の cooldown を武装する（初見の failed run にだけ heal_next_at を書く・冪等）。
        期限（epoch 秒）を返す。cooldown は heal_count に応じて指数で伸びる
        （恒久障害の run が heal と失敗を高频度で往復しない）。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path) or {}
        due = meta.get("heal_next_at")
        if isinstance(due, (int, float)):
            return float(due)
        n = int(meta.get("heal_count", 0) or 0)
        due = time.time() + float(cooldown) * (2 ** n)
        meta["heal_next_at"] = due
        write_json_atomic(v.meta_path, meta)
        return due

    def record_heal(self, run_id: str) -> int:
        """heal の実施を meta に記録し、「進捗なしの連続 heal 回数」を返す（record_resume と同じ
        思想: 前回 heal 以降に done ノードが増えていれば 1 から数え直す＝前進している run は
        何度でも回収し、進捗ゼロのまま失敗し続ける run だけが max_heals に達する）。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path) or {}
        try:
            done_now = sum(1 for f in os.listdir(v.results_dir) if f.endswith(".json"))
        except OSError:
            done_now = 0
        prev = meta.get("heal_progress")
        if prev is None or done_now > int(prev):
            n = 1
        else:
            n = int(meta.get("heal_count", 0) or 0) + 1
        meta["heal_count"] = n
        meta["heal_progress"] = done_now
        meta["healed_at"] = now_iso()
        meta["updated_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)
        return n

    def mark_heal_exhausted(self, run_id: str) -> None:
        """heal 上限超過を記録し、以後の poll で heal 候補から即座に外す（heal_class が None を
        返す）。failed run のまま人 / 消費者（agent-project の新世代リトライ）の回収に委ねる。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path) or {}
        meta["heal_exhausted"] = True
        meta["updated_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)

    def event(self, who: str, kind: str, **extra) -> None:
        rec = {"ts": now_iso(), "who": who, "kind": kind, **extra}
        os.makedirs(self.events_dir, exist_ok=True)
        with open(os.path.join(self.events_dir, f"{who}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def recent_events(self, limit: int):
        evs = []
        if os.path.isdir(self.events_dir):
            for name in os.listdir(self.events_dir):
                with open(os.path.join(self.events_dir, name), encoding="utf-8") as f:
                    for line in f:
                        try:
                            evs.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return sorted(evs, key=lambda e: e.get("ts", ""))[-limit:]

    # --- run 管理（gc / watch 用） ---
    def list_runs(self):
        if not os.path.isdir(self.runs_root):
            return []
        return sorted(d for d in os.listdir(self.runs_root)
                      if os.path.isdir(os.path.join(self.runs_root, d)))

    def run_meta(self, run_id: str):
        return read_json(os.path.join(self.runs_root, run_id, "meta.json")) or {}

    # --- 統一 verify の receipt（runs/<run-id>/receipt.json。書き手は専用 runner のみ） ---
    def receipt_path_for(self, run_id: str) -> str:
        return os.path.join(self.runs_root, run_id, "receipt.json")

    def run_receipt(self, run_id: str) -> "dict | None":
        rec = read_json(self.receipt_path_for(run_id))
        return rec if isinstance(rec, dict) else None

    def write_receipt(self, run_id: str, receipt: dict) -> None:
        write_json_atomic(self.receipt_path_for(run_id), receipt)

    def remove_run(self, run_id: str) -> None:
        shutil.rmtree(os.path.join(self.runs_root, run_id), ignore_errors=True)
        # 対応する inbox 要求と claim も消す（req_id == run_id）。残すとデーモンの
        # 重複排除（run_exists ベース）が外れ、gc 後にリース失効済みの要求を拾い直して
        # 完了済みの run を再実行してしまう。
        try:
            os.remove(os.path.join(self.inbox_dir, f"{run_id}.json"))
        except OSError:
            pass
        shutil.rmtree(os.path.join(self.inbox_claims_dir, run_id), ignore_errors=True)
        try:
            os.remove(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"))
        except OSError:
            pass

    def run_view(self, run_id: str) -> "Bus":
        """同じ作業ツリー上の別 run を読み取るための軽量ビュー（git 再クローンしない）。"""
        return Bus(self.root, run_id)

    # --- リトライ時の引き継ぎ（先行 run のデータ破棄設計） ---

    # 墓標に残す工程出力の上限（冒頭＋末尾）。全文を残すと git バス同期・run dir が肥大する。
    # 全文の正は削除される旧 run の results/ だったので、要約であることを明示して抜粋する。
    TOMBSTONE_OUTPUT_HEAD = 1200
    TOMBSTONE_OUTPUT_TAIL = 2400

    @classmethod
    def _tombstone_excerpt(cls, text: str) -> str:
        s = "" if text is None else str(text)
        limit = cls.TOMBSTONE_OUTPUT_HEAD + cls.TOMBSTONE_OUTPUT_TAIL
        if len(s) <= limit:
            return s
        omitted = len(s) - limit
        return (s[:cls.TOMBSTONE_OUTPUT_HEAD]
                + f"\n…（中略 {omitted} 文字）…\n"
                + s[-cls.TOMBSTONE_OUTPUT_TAIL:])

    def _predecessor_tombstone(self, old: "Bus", old_meta: dict) -> dict:
        """削除する先行 run の要約（墓標）。meta・graph（計画）・final・全ノードの results
        （出力は抜粋）・成果物ファイル名の一覧を残す。viewer はこれを「アーカイブ済み run」
        相当として表示できる（工程出力の全文と成果物の実体は旧 run と共に消える）。"""
        results: dict = {}
        for nid in old.task_ids():
            res = old.read_result(nid)
            if not res:
                continue
            rec = dict(res)
            if isinstance(rec.get("output"), str):
                rec["output"] = self._tombstone_excerpt(rec["output"])
            results[nid] = rec
        artifacts: list = []
        if os.path.isdir(old.artifacts_dir):
            for base, _dirs, files in os.walk(old.artifacts_dir):
                rel = os.path.relpath(base, old.artifacts_dir)
                for f in files:
                    p = f if rel == "." else os.path.join(rel, f)
                    artifacts.append(p.replace(os.sep, "/"))
        return {
            "run_id": os.path.basename(old.run_dir),
            "saved_at": now_iso(),
            "meta": old_meta,
            "graph": read_json(old.graph_path),
            "final": read_json(old.final_path),
            "results": results,
            "artifacts": sorted(artifacts),
        }

    def _preserve_predecessor(self, old: "Bus", old_meta: dict) -> None:
        """先行 run を削除する前に、その墓標をこの run の inherited/ へ書き残す。
        先行 run 自身が持っていた墓標（さらに前の世代）も引き継ぐ＝リトライ連鎖の全世代の
        要約が最新 run に残る。"""
        os.makedirs(self.inherited_dir, exist_ok=True)
        if os.path.isdir(old.inherited_dir):           # 前々世代以前の墓標を持ち越す
            for f in os.listdir(old.inherited_dir):
                if not f.endswith(".json"):
                    continue
                dst = os.path.join(self.inherited_dir, f)
                if not os.path.exists(dst):
                    shutil.copy2(os.path.join(old.inherited_dir, f), dst)
        old_id = os.path.basename(old.run_dir)
        write_json_atomic(os.path.join(self.inherited_dir, f"{old_id}.json"),
                          self._predecessor_tombstone(old, old_meta))

    def _seed_from(self, old: "Bus", request: str = "") -> int:
        """先行 run `old` の再利用可能な状態をこの（新しい）run dir へコピーする。
        戻り値＝引き継いだ done ノード数。graph.json（計画）・tasks/（ノード仕様）・
        artifacts/（node-id で決定的にアドレスされる中間成果物）を丸ごと、results/ は
        status==done のノードだけ引き継ぐ（failed はやり直させる）。workspace 付き run では
        確定済みノードの commit を失わないよう、新 run の作業ブランチを旧ブランチ af/<old> から
        派生させる（spec.base を旧ブランチに差す。旧ブランチが無ければ clone 側が既定へ
        フォールバックするので安全）。meta の lease/resume 簿記・claims/・events/ は引き継がない
        （wall-clock リースや孤児判定を汚染しないため）。

        request: 新世代（この run）の要求文。リトライの要求文には差し戻しの意図（run ブリーフ・
        feedback）が積み増されており、worker はこれを meta.request（全体文脈）として読む。
        旧 request をコピーすると「リトライの引き金になった指摘が再実行ノードに届かない」
        ため、指定があれば新 request を正とする（空なら従来どおり旧 request を保つ）。"""
        old_id = os.path.basename(old.run_dir)
        self.ensure_dirs()
        g = read_json(old.graph_path)
        if g is not None:
            write_json_atomic(self.graph_path, g)
        for nid in old.task_ids():                     # ノード仕様（tasks/<id>.json）
            spec = read_json(os.path.join(old.tasks_dir, f"{nid}.json"))
            if spec is not None:
                write_json_atomic(os.path.join(self.tasks_dir, f"{nid}.json"), spec)
        if os.path.isdir(old.artifacts_dir):           # 中間成果物（node-id アドレス）
            shutil.copytree(old.artifacts_dir, self.artifacts_dir, dirs_exist_ok=True)
        seeded = 0
        for nid in old.task_ids():                     # 確定済み（done）ノードの結果だけ
            res = old.read_result(nid)
            if res and res.get("status") == "done":
                write_json_atomic(self.result_path(nid), res)
                seeded += 1
        old_meta = read_json(old.meta_path) or {}
        # 世代交代は **要素ごとに** base を旧ブランチ af/<old-id> へ差し替える（§5.2）。
        # 1 要素なら従来と同じ 1 回の差し替えになる。
        old_set = old_meta.get("workspaces")
        if not (isinstance(old_set, list) and old_set):
            old_set = [old_meta["workspace"]] if isinstance(old_meta.get("workspace"), dict) else []
        seeded_set = [{**e, "base": run_branch_name(old_id)}
                      for e in normalize_workset(old_set)]
        ws = workset_primary(seeded_set)
        write_json_atomic(self.meta_path, {
            "request": (request or "").strip() or old_meta.get("request", ""),
            "workspace": ws or None,
            **({"workspaces": seeded_set} if len(seeded_set) > 1 else {}),
            "references": list(old_meta.get("references") or []),
            "readonly": old_meta.get("readonly") is True,
            "status": "planning",
            "created_at": now_iso(),
            "inherited_from": old_id,                  # 由来（可視化・監査用）
        })
        return seeded

    def inherit_from(self, old_run_id: str, orphan_grace: float = 0.0,
                     request: str = "") -> dict:
        """タイムアウト/失敗した先行 run から再利用可能な状態をこの run へ引き継ぎ、先行 run を
        削除する。リトライで毎回ゼロからやり直して確定済みノードの作業（トークン/時間）を捨てるのを
        防ぐための「引き継いでから掃除する」操作。

        安全条件: 先行 run が終端（done/failed）か孤児（生存リース切れ）のときだけ触る。実行中で
        リースが有効な run には seed も削除もしない（走っている run を壊さない）。
        先行 run が「完全に done」（全ノード確定＝verify=NG 等）なら状態は引き継がず掃除だけ行う
        （同一出力で即 done→再び NG の無限ループを避け、feedback 付きで新規にやり直させる）。
        戻り値: {inherited, seeded_nodes, deleted, reason}。"""
        if old_run_id == os.path.basename(self.run_dir):
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": "自分自身は引き継がない"}
        old = self.run_view(old_run_id)
        old_meta = read_json(old.meta_path)
        if old_meta is None:
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": "先行 run が見つからない"}
        terminal = old_meta.get("status") in TERMINAL
        if old_meta.get("status") == "cancelled":
            # 人の停止を尊重。seed も削除もしない（cancel 後リトライが cancelled 行を蘇らせない）。
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": "cancelled は引き継がない"}
        if not terminal and not self.run_is_orphaned(old_run_id, orphan_grace):
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": f"先行 run は実行中（status={old_meta.get('status')}）＝触らない"}
        ids = old.task_ids()
        fully_done = bool(ids) and all(old.node_state(i) == "done" for i in ids)
        seeded = 0
        # この run が既に実体を持つ（別経路で再開中）なら seed しない＝上書き事故を防ぐ
        if read_json(self.meta_path) is None and not fully_done:
            seeded = self._seed_from(old, request=request)
        # 削除の前に旧 run の墓標（meta・final・results 要約）をこの run へ残す。
        # 特に「全ノード done だが verify NG」のリトライは結果を引き継がない設計のため、
        # 墓標が無いと完走した run の成果記録が bus から即座に完全消滅する
        # （viewer がその瞬間にポーリングしていない限り二度と見られない）。
        # 墓標の保存失敗で掃除・リトライ自体は止めない。
        try:
            self._preserve_predecessor(old, old_meta)
        except OSError:
            pass
        self.remove_run(old_run_id)                    # 終端/孤児のみ到達＝安全に掃除
        return {"inherited": seeded > 0, "seeded_nodes": seeded, "deleted": True,
                "reason": ("完全 done のため状態は引き継がず掃除のみ" if fully_done
                           else f"確定済み {seeded} ノードを引き継いで先行 run を掃除")}

    def active_runs(self):
        """planning/running な run の id 一覧（終端した run は除く）。"""
        out = []
        for rid in self.list_runs():
            st = self.run_meta(rid).get("status")
            if st and st not in TERMINAL:
                out.append(rid)
        return out

    def run_claimable_count(self, run_id: str) -> int:
        """その run で今すぐ claim 可能（pending かつ依存充足）なタスク数。"""
        v = self.run_view(run_id)
        graph = v.read_graph()
        if not graph:
            return 0
        return sum(1 for nid, node in graph["nodes"].items()
                   if v.node_state(nid) == "pending" and deps_satisfied(v, node))

    def mark_run_failed(self, run_id: str, reason: str = "") -> bool:
        """run_id がまだ終端でなければ status を failed に確定する。
        orchestrator が done を書く前に異常終了した（クラッシュ・kill 等）ケースを終端化し、
        result/status を待つ消費者（agent-project の submit 待ちなど）が永久待機に陥らないようにする。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "failed"
        meta["updated_at"] = now_iso()
        if reason:
            meta["failure_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def mark_run_superseded(self, run_id: str, superseded_by: str = "") -> bool:
        """run_id がまだ終端でなければ status を failed に確定する（世代交代による停止）。
        agent-project はリトライ時に先行 run を明示 cancel せず、inherit_from 付きで次世代を
        inbox へ投入する。inherit_from は実行中の先行 run を安全のため殺さないので、旧世代の run が
        非終端のまま inbox に残る。owning daemon 消失後（PC シャットダウン等）に daemon を再起動
        すると、これら旧世代の孤児が一斉に adopt（再開）され、世代交代で消えるべき旧リトライが
        復活して二重実行になる。これを防ぐため、次世代に引き継がれた先行 run を再開せず終端化する。
        failed（≒ 異常終了）や cancelled（人の明示指示）と区別できるよう superseded=True を記録する。
        終端化後は次世代の inherit_from が確定済みノードを引き継いでから掃除できる（作業は失わない）。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "failed"
        meta["updated_at"] = now_iso()
        meta["superseded"] = True
        if superseded_by:
            meta["superseded_by"] = superseded_by
        meta["failure_reason"] = (
            f"superseded: 新世代のリトライ {superseded_by} に引き継がれた旧 run（再開しない）"
            if superseded_by else "superseded: 新世代のリトライに引き継がれた旧 run（再開しない）")
        write_json_atomic(v.meta_path, meta)
        return True

    # --- cancel（人の明示指示による run スコープの恒久停止） ---
    def cancel_request(self, run_id: str, who: str, reason: str = "",
                       close_issues: bool = False) -> None:
        """cancel マーカーを inbox/cancels/ に書く（git 同期でリモート優先で全 PC へ伝わる）。
        監視主体（daemon/run/orchestrator）がこれを見て run を cancelled に終端化し、その run の
        orchestrator/worker を止め、park 済みノードの再ポーリングを止める。"""
        os.makedirs(self.inbox_cancels_dir, exist_ok=True)
        write_json_atomic(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"), {
            "id": run_id, "who": who, "reason": reason,
            "close_issues": bool(close_issues), "requested_at": now_iso(),
        })

    def is_canceled_requested(self, run_id: str) -> bool:
        """run_id に cancel マーカーがあるか（＝人が停止を指示したか）。"""
        return os.path.exists(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"))

    def clear_cancel(self, run_id: str) -> bool:
        """実行所有者が停止を確認した cancel マーカーを消す。消えたら True。

        外部の cancel 適用側からは呼ばない。所有者が止まる前に消すと、並行 heartbeat の
        古い meta 書き戻しで停止意図が失われる。"""
        p = os.path.join(self.inbox_cancels_dir, f"{run_id}.json")
        try:
            os.remove(p)
            return True
        except OSError:
            return False

    def cancel_info(self, run_id: str) -> dict:
        return read_json(os.path.join(self.inbox_cancels_dir, f"{run_id}.json")) or {}

    def list_cancels(self) -> "list[str]":
        d = self.inbox_cancels_dir
        if not os.path.isdir(d):
            return []
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))

    def mark_canceled(self, run_id: str, reason: str = "") -> bool:
        """run_id がまだ終端でなければ status を cancelled に確定する（cancel マーカーの適用）。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "cancelled"
        meta["updated_at"] = now_iso()
        if reason:
            meta["cancel_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def clear_waits_for_run(self, run_id: str) -> int:
        """run_id の park 記録をすべて消す（cancel 時に再ポーリングを止める）。消した件数を返す。"""
        v = self.run_view(run_id)
        n = 0
        if os.path.isdir(v.waits_dir):
            for name in os.listdir(v.waits_dir):
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(v.waits_dir, name))
                        n += 1
                    except OSError:
                        pass
        return n

    def fail_request(self, req_id: str, reason: str = "") -> bool:
        """inbox 要求 req_id を failed run として終端化する（run 未作成でも）。
        orchestrator が run の meta を一度も書けずに死に続ける（例: クローンの git ロック残骸で
        sync_push が失敗し続ける）と run_exists が偽のままになり、daemon が毎 poll 同じ要求を
        再 claim → orchestrator 起動 → 即死 を繰り返す無限ループに陥る。meta が無ければ failed で
        新規作成して run_exists を真にし、このループを断ち切る（消費者も失敗を即検知できる）。
        既に run があれば mark_run_failed に委ねる（終端済みなら上書きせず False）。"""
        v = self.run_view(req_id)
        if read_json(v.meta_path) is not None:
            return self.mark_run_failed(req_id, reason)
        req = self.read_inbox(req_id) or {}
        meta = {
            "request": req.get("request", ""),
            "workspace": req.get("workspace"),
            **({"workspaces": req["workspaces"]} if isinstance(req.get("workspaces"), list)
               and req["workspaces"] else {}),
            "references": list(req.get("references") or []),
            "readonly": req.get("readonly") is True,
            "status": "failed",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        if reason:
            meta["failure_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def cancel_request_run(self, req_id: str, reason: str = "") -> bool:
        """run 化前に cancel された要求を cancelled run として終端化する（fail_request の cancelled 版）。
        既に run があれば mark_canceled に委ねる。これで消費者は「取り下げ」を終端として観測でき、
        daemon が同じ要求を毎 poll 受理し直すのを止める。"""
        v = self.run_view(req_id)
        if read_json(v.meta_path) is not None:
            return self.mark_canceled(req_id, reason)
        req = self.read_inbox(req_id) or {}
        write_json_atomic(v.meta_path, {
            "request": req.get("request", ""),
            "workspace": req.get("workspace"),
            **({"workspaces": req["workspaces"]} if isinstance(req.get("workspaces"), list)
               and req["workspaces"] else {}),
            "references": list(req.get("references") or []),
            "readonly": req.get("readonly") is True,
            "status": "cancelled",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "cancel_reason": reason or "cancel 指示（run 化前）",
        })
        return True

    def touch_run(self, run_id: str, lease_sec: float) -> None:
        """自分が orchestrator を回している run の生存リース（heartbeat）を更新する。
        これにより別デーモン／再起動後の自分が「この run は生きている（owner が駆動中）」と判定でき、
        孤児回収で誤って failed にしない。終端済み／不在の run には何もしない。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return
        meta["orch_lease_until"] = time.time() + lease_sec
        meta["heartbeat_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)
        # cancel と heartbeat が同じ古い meta から競合しても、残してある停止意図へ収束させる。
        if self.is_canceled_requested(run_id):
            info = self.cancel_info(run_id)
            self.mark_canceled(run_id, info.get("reason") or "cancel 指示")

    def run_is_orphaned(self, run_id: str, grace_sec: float) -> bool:
        """run が非終端なのに生存リースが切れている（owning daemon/orchestrator が消失した）か。
        owner が一度でも heartbeat していれば orch_lease_until で判定する。リース未記録の古い run
        （owner が heartbeat する前に死んだ／本変更前から残る run）は age を grace と比較して判定する。"""
        meta = read_json(self.run_view(run_id).meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        lease = meta.get("orch_lease_until")
        if isinstance(lease, (int, float)):
            return lease < time.time()
        return _age_hours(meta) * 3600.0 > grace_sec

    def record_resume(self, run_id: str) -> int:
        """自動再開の試行を meta に記録し、「進捗なしの連続再開回数」を返す。
        前回の再開以降に results/ が増えていれば 1 から数え直す＝進捗のある長期 run は
        （毎日の PC シャットダウンを跨いで）何度でも再開できる。進捗ゼロのまま数字だけ
        増える壊れた run だけが max_resumes に達して failed に確定される。

        生存中の park（承認待ち wait）も「健全な進捗」とみなす。gitlab の人レビューは
        数日〜数週間かかる前提で、結果が増えないまま毎晩再起動しても orphaned にしない。
        wait_lease 失効だけでは進捗無しにしない（一晩の電源断で lease だけ切れ、wait ファイル
        と未決着イシューは残る）。throttled（イシュー未作成）は枠待ちなので除外。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path) or {}
        try:
            done_now = sum(1 for f in os.listdir(v.results_dir) if f.endswith(".json"))
        except OSError:
            done_now = 0
        open_waits = sum(1 for rec in v.list_waits() if not rec.get("throttled"))
        prev = meta.get("resume_progress")
        if prev is None or done_now > int(prev) or open_waits > 0:
            n = 1                                     # 進捗あり / park 残存 / 初回 → 数え直し
        else:
            n = int(meta.get("resume_count", 0) or 0) + 1
        meta["resume_count"] = n
        meta["resume_progress"] = done_now
        meta["resume_live_waits"] = open_waits
        meta["resumed_at"] = now_iso()
        meta["updated_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)
        return n

    # --- inbox（要求キュー）と要求 claim ---
    def submit_request(self, req_id: str, request: str, submitter: str,
                       workspace: "dict | None" = None,
                       references: "list[dict] | None" = None,
                       inherit_from: "str | None" = None,
                       delegation: "dict | None" = None,
                       verification_plan: "dict | None" = None,
                       plan: "dict | None" = None,
                       pattern: "str | None" = None,
                       readonly: bool = False,
                       workspaces: "list[dict] | None" = None) -> None:
        # 投入側は書込先の集合（workset）を渡せる。`workspace` は primary として必ず載せる
        # ——`workspaces` を知らない旧 orchestrator も primary へは正しく書けるようにする。
        # `workspaces[0]` と `workspace` が食い違う要求は黙って直さずここで断る（§5.2）。
        workset = normalize_workset(workspaces if workspaces is not None
                                    else ([workspace] if workspace else []))
        primary = workset_primary(workset)
        if workspace and primary and str(workspace.get("url") or "") != str(primary.get("url") or ""):
            raise ValueError("workspaces[0] と workspace が食い違っています"
                             f"（{primary.get('url')} / {workspace.get('url')}）")
        errs = workset_errors(workset)
        if errs:
            raise ValueError("workset が不正です: " + "; ".join(errs))
        rec = {
            "id": req_id,
            "request": request,
            "submitter": submitter,
            "workspace": primary or workspace or None,  # primary を daemon の orchestrate へ伝搬する
            **({"workspaces": workset} if len(workset) > 1 else {}),
            "references": list(references or []),  # 参照リポジトリも daemon の orchestrate へ伝搬する
            "submitted_at": now_iso(),
        }
        if readonly is True:
            rec["readonly"] = True
        if inherit_from:                      # リトライ: 先行 run の引き継ぎ元を orchestrate へ伝搬
            rec["inherit_from"] = inherit_from
        if isinstance(delegation, dict) and delegation.get("id"):
            # 委譲公示板（agent-board）由来の来歴。daemon の orchestrate が run meta へ引き回す
            rec["delegation"] = delegation
        if isinstance(verification_plan, dict):
            # 統一 verify の検証計画（依頼側が digest 付きで確定済み）。_spawn_orchestrator が
            # `--verification-plan` として orchestrate へ渡し、専用 runner が receipt を返す。
            rec["verification_plan"] = verification_plan
        if isinstance(plan, dict) and plan.get("nodes"):
            # ユーザー定義フロー（ビルダー・人手投入）。orchestrate が planner を通さず
            # 検証だけでこのグラフを実行する（plan_strategy_user）。inbox 要求が唯一の権威で、
            # orchestrate 自身が inbox を読むため argv 転記は不要。
            rec["plan"] = plan
        if isinstance(pattern, str) and pattern.strip():
            rec["pattern"] = pattern.strip()
        write_json_atomic(os.path.join(self.inbox_dir, f"{req_id}.json"), rec)

    def list_inbox(self):
        if not os.path.isdir(self.inbox_dir):
            return []
        return sorted(f[:-5] for f in os.listdir(self.inbox_dir) if f.endswith(".json"))

    def read_inbox(self, req_id: str):
        return read_json(os.path.join(self.inbox_dir, f"{req_id}.json"))

    def run_exists(self, run_id: str) -> bool:
        return os.path.exists(os.path.join(self.runs_root, run_id, "meta.json"))

    def claim_request(self, req_id: str, who: str, lease_sec: float) -> bool:
        """どのデーモンがこの要求を orchestrate するかを 1 台に決める。"""
        self.sync_pull()
        if self.run_exists(req_id):
            return False  # 既に誰かが run を作って処理開始済み
        return self._try_claim_in(os.path.join(self.inbox_claims_dir, req_id),
                                  who, lease_sec, f"claim request {req_id} by {who}")

    def reclaim_request(self, req_id: str, who: str, lease_sec: float) -> bool:
        """孤児 run の再開担当を 1 台に決める。run が既に存在していても claim できる点が
        claim_request と違う（あちらは新規要求の受理用）。消失した旧 owner の claim は
        lease 切れで勝者判定から自然に外れるため、再起動後の自分や別 daemon が引き継げる
        （lease がまだ残っていれば False＝claim 失効まで次の poll で再試行される）。"""
        self.sync_pull()
        return self._try_claim_in(os.path.join(self.inbox_claims_dir, req_id),
                                  who, lease_sec, f"reclaim request {req_id} by {who}")
