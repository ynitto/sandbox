from __future__ import annotations
# bus.py — 元 kiro-flow.py の 374-1083 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# Bus — メッセージバス抽象（M1: ローカルディレクトリ実装）
# --------------------------------------------------------------------------
class Bus:
    def __init__(self, root: str, run_id: str):
        self.root = root
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
        self.results_dir = os.path.join(self.run_dir, "results")
        self.artifacts_dir = os.path.join(self.run_dir, "artifacts")
        self.events_dir = os.path.join(self.run_dir, "events")
        self.meta_path = os.path.join(self.run_dir, "meta.json")
        self.graph_path = os.path.join(self.run_dir, "graph.json")
        self.final_path = os.path.join(self.run_dir, "final.json")

    # --- 転送フック（ローカルバスでは no-op、GitBus が上書き） ---
    def sync_pull(self) -> None:
        pass

    def sync_push(self, msg: str = "") -> None:
        pass

    # --- セットアップ ---
    def ensure_dirs(self) -> None:
        for d in (self.tasks_dir, self.claims_dir, self.waits_dir,
                  self.results_dir, self.events_dir):
            os.makedirs(d, exist_ok=True)

    def ensure_run(self, request: str, workspace: "dict | None" = None,
                   references: "list[dict] | None" = None) -> None:
        self.ensure_dirs()
        if read_json(self.meta_path) is None:
            write_json_atomic(self.meta_path, {
                "request": request,
                # この run（=バックログ単位）の唯一の書込先リポジトリ（worker が clone し、
                # 作業ブランチを作って作業する）。None なら読み取り専用 run（commit/push しない）。
                "workspace": workspace or None,
                # 参照リポジトリ（読むだけ・書き込まない）。executor がイシュー/プロンプトに描画する。
                "references": list(references or []),
                "status": "planning",
                "created_at": now_iso(),
            })

    def run_workspace(self) -> "dict | None":
        """この run の唯一の書込先ワークスペース spec（meta に記録）。無ければ None（読み取り専用 run）。"""
        meta = read_json(self.meta_path) or {}
        w = meta.get("workspace")
        return w if isinstance(w, dict) and w.get("url") else None

    def run_references(self) -> "list[dict]":
        """この run の参照リポジトリ spec 一覧（読むだけ。meta に記録、executor が描画する）。"""
        meta = read_json(self.meta_path) or {}
        r = meta.get("references")
        return [s for s in r if isinstance(s, dict) and s.get("url")] if isinstance(r, list) else []

    # --- メタ / グラフ ---
    def set_status(self, status: str) -> None:
        meta = read_json(self.meta_path) or {}
        meta["status"] = status
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)

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
        out = {}
        if os.path.isdir(claim_dir):
            for name in os.listdir(claim_dir):
                if name.endswith(".json"):
                    info = read_json(os.path.join(claim_dir, name))
                    if info:
                        out[name[:-5]] = info
        return out

    def _winner_in(self, claim_dir: str):
        """lease 内の claim から決定的に勝者を選ぶ。無ければ None。"""
        now = time.time()
        live = [
            (info.get("ts", 0.0), who)
            for who, info in self._list_claims_in(claim_dir).items()
            if info.get("lease_until", 0) >= now
        ]
        return min(live)[1] if live else None

    def _write_claim_in(self, claim_dir: str, who: str, lease_sec: float) -> None:
        os.makedirs(claim_dir, exist_ok=True)
        write_json_atomic(os.path.join(claim_dir, f"{who}.json"), {
            "who": who,
            "ts": _unique_ts(),
            "claimed_at": now_iso(),
            "lease_until": time.time() + lease_sec,
        })

    def _try_claim_in(self, claim_dir: str, who: str, lease_sec: float, msg: str) -> bool:
        # 同一マシン上の並行 claim を排他ロックで直列化する（ロックはバス外＝
        # git に乗せない一時ファイル）。これで「先着読みの勝者」と「決定的
        # タイブレークの勝者」の食い違いによる二重勝者を防ぐ。
        # git 分散（別マシン）はクローンごとに別ロックなので直列化されないが、
        # その整合は sync_pull 後の決定的タイブレーク＋lease が担う。
        os.makedirs(claim_dir, exist_ok=True)
        with _file_lock(_claim_lock_path(claim_dir)):
            w = self._winner_in(claim_dir)
            if w is not None and w != who:
                return False  # 既に他者が勝者（lease 内）
            self._write_claim_in(claim_dir, who, lease_sec)
            self.sync_push(msg)
            self.sync_pull()  # 他ノードの claim を取り込んでから勝敗判定
            if self._winner_in(claim_dir) == who:
                return True
            # 敗者が自分の claim ファイルを残すと、勝者の park/release 後に敗者和己の
            # lease が _winner になり、誰も動いていないのに node_state=claimed の
            # zombie になる（git 分散で両者が書けた場合）。負けた自分の分だけ消す。
            try:
                os.remove(os.path.join(claim_dir, f"{who}.json"))
                self.sync_push(f"claim withdraw {who}")
            except OSError:
                pass
            return False

    # 後方互換のためのノード単位ラッパ
    def _winner(self, node_id: str):
        return self._winner_in(self._claim_dir(node_id))

    def _write_claim(self, node_id: str, who: str, lease_sec: float) -> None:
        self._write_claim_in(self._claim_dir(node_id), who, lease_sec)

    def try_claim(self, node_id: str, who: str, lease_sec: float) -> bool:
        self.sync_pull()
        if self.has_result(node_id):
            return False
        return self._try_claim_in(self._claim_dir(node_id), who, lease_sec,
                                  f"claim {node_id} by {who}")

    def release_claim(self, node_id: str, who: str) -> None:
        """自分の claim ファイルを消して node を手放す（park 時に worker スロットを空けるため）。
        心拍（Heartbeat）を停止してから呼ぶこと——停止前に消すと直後の心拍が claim を書き戻す。"""
        try:
            os.remove(os.path.join(self._claim_dir(node_id), f"{who}.json"))
        except OSError:
            pass
        self.sync_push(f"release {node_id} by {who}")

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
                     data=None, artifacts=None) -> None:
        rec = {
            "id": node_id,
            "who": who,
            "status": status,
            "output": output,
            "finished_at": now_iso(),
        }
        if data is not None:  # 構造化成果（任意）。エージェント間を JSON で流す
            rec["data"] = data
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

    def retry_failed(self) -> "list[str]":
        """failed 状態の run を「再実行できる状態」へ戻す。失敗ノード（results が failed）の結果と
        claim を消して pending へ戻し（＝再 claim・再実行の対象にする）、確定済み done ノードは温存する。
        併せて meta の終端・孤児簿記（failure_reason/superseded/orphaned/resume_count 等）を掃除し、
        status を running に戻す。戻したノード id 一覧を返す（commit/push は呼び出し側）。

        failed run はそのままでは再開しても全ノードが終端（node_state=failed）のまま静止し、
        何も再実行されない。人/消費者の明示 retry でだけこの reset を行い、失敗した所だけをやり直す。"""
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
        for k in ("failure_reason", "superseded", "superseded_by",
                  "resume_count", "resume_progress"):
            meta.pop(k, None)
        meta["status"] = "running"
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)
        return reset

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
    def _seed_from(self, old: "Bus") -> int:
        """先行 run `old` の再利用可能な状態をこの（新しい）run dir へコピーする。
        戻り値＝引き継いだ done ノード数。graph.json（計画）・tasks/（ノード仕様）・
        artifacts/（node-id で決定的にアドレスされる中間成果物）を丸ごと、results/ は
        status==done のノードだけ引き継ぐ（failed はやり直させる）。workspace 付き run では
        確定済みノードの commit を失わないよう、新 run の作業ブランチを旧ブランチ kf/<old> から
        派生させる（spec.base を旧ブランチに差す。旧ブランチが無ければ clone 側が既定へ
        フォールバックするので安全）。meta の lease/resume 簿記・claims/・events/ は引き継がない
        （wall-clock リースや孤児判定を汚染しないため）。"""
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
        ws = old_meta.get("workspace")
        if isinstance(ws, dict) and ws.get("url"):
            ws = dict(ws)
            ws["base"] = run_branch_name(old_id)       # 旧ブランチから派生＝done の commit を保つ
        write_json_atomic(self.meta_path, {
            "request": old_meta.get("request", ""),
            "workspace": ws or None,
            "references": list(old_meta.get("references") or []),
            "status": "planning",
            "created_at": now_iso(),
            "inherited_from": old_id,                  # 由来（可視化・監査用）
        })
        return seeded

    def inherit_from(self, old_run_id: str, orphan_grace: float = 0.0) -> dict:
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
        if not terminal and not self.run_is_orphaned(old_run_id, orphan_grace):
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": f"先行 run は実行中（status={old_meta.get('status')}）＝触らない"}
        ids = old.task_ids()
        fully_done = bool(ids) and all(old.node_state(i) == "done" for i in ids)
        seeded = 0
        # この run が既に実体を持つ（別経路で再開中）なら seed しない＝上書き事故を防ぐ
        if read_json(self.meta_path) is None and not fully_done:
            seeded = self._seed_from(old)
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
        result/status を待つ消費者（kiro-project の submit 待ちなど）が永久待機に陥らないようにする。
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
        kiro-project はリトライ時に先行 run を明示 cancel せず、inherit_from 付きで次世代を
        inbox へ投入する。inherit_from は実行中の先行 run を安全のため殺さないので、旧世代の run が
        非終端のまま inbox に残る。owning daemon 消失後（PC シャットダウン等）に daemon を再起動
        すると、これら旧世代の孤児が一斉に adopt（再開）され、世代交代で消えるべき旧リトライが
        復活して二重実行になる。これを防ぐため、次世代に引き継がれた先行 run を再開せず終端化する。
        failed（≒ 異常終了）や canceled（人の明示指示）と区別できるよう superseded=True を記録する。
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
        監視主体（daemon/run/orchestrator）がこれを見て run を canceled に終端化し、その run の
        orchestrator/worker を止め、park 済みノードの再ポーリングを止める。"""
        os.makedirs(self.inbox_cancels_dir, exist_ok=True)
        write_json_atomic(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"), {
            "id": run_id, "who": who, "reason": reason,
            "close_issues": bool(close_issues), "requested_at": now_iso(),
        })

    def is_canceled_requested(self, run_id: str) -> bool:
        """run_id に cancel マーカーがあるか（＝人が停止を指示したか）。"""
        return os.path.exists(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"))

    def cancel_info(self, run_id: str) -> dict:
        return read_json(os.path.join(self.inbox_cancels_dir, f"{run_id}.json")) or {}

    def list_cancels(self) -> "list[str]":
        d = self.inbox_cancels_dir
        if not os.path.isdir(d):
            return []
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))

    def mark_canceled(self, run_id: str, reason: str = "") -> bool:
        """run_id がまだ終端でなければ status を canceled に確定する（cancel マーカーの適用）。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "canceled"
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
            "references": list(req.get("references") or []),
            "status": "failed",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        if reason:
            meta["failure_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def cancel_request_run(self, req_id: str, reason: str = "") -> bool:
        """run 化前に cancel された要求を canceled run として終端化する（fail_request の canceled 版）。
        既に run があれば mark_canceled に委ねる。これで消費者は「取り下げ」を終端として観測でき、
        daemon が同じ要求を毎 poll 受理し直すのを止める。"""
        v = self.run_view(req_id)
        if read_json(v.meta_path) is not None:
            return self.mark_canceled(req_id, reason)
        req = self.read_inbox(req_id) or {}
        write_json_atomic(v.meta_path, {
            "request": req.get("request", ""),
            "workspace": req.get("workspace"),
            "references": list(req.get("references") or []),
            "status": "canceled",
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
        数日〜数週間かかる前提で、結果が増えないまま毎晩再起動しても orphaned にしない。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path) or {}
        try:
            done_now = sum(1 for f in os.listdir(v.results_dir) if f.endswith(".json"))
        except OSError:
            done_now = 0
        live_waits = sum(1 for rec in v.list_waits()
                         if float(rec.get("wait_lease_until", 0) or 0) >= time.time())
        prev = meta.get("resume_progress")
        if prev is None or done_now > int(prev) or live_waits > 0:
            n = 1                                     # 進捗あり / park 生存 / 初回 → 数え直し
        else:
            n = int(meta.get("resume_count", 0) or 0) + 1
        meta["resume_count"] = n
        meta["resume_progress"] = done_now
        meta["resume_live_waits"] = live_waits
        meta["resumed_at"] = now_iso()
        meta["updated_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)
        return n

    # --- inbox（要求キュー）と要求 claim ---
    def submit_request(self, req_id: str, request: str, submitter: str,
                       workspace: "dict | None" = None,
                       references: "list[dict] | None" = None,
                       inherit_from: "str | None" = None) -> None:
        rec = {
            "id": req_id,
            "request": request,
            "submitter": submitter,
            "workspace": workspace or None,   # 唯一の書込先を daemon の orchestrate へ伝搬する
            "references": list(references or []),  # 参照リポジトリも daemon の orchestrate へ伝搬する
            "submitted_at": now_iso(),
        }
        if inherit_from:                      # リトライ: 先行 run の引き継ぎ元を orchestrate へ伝搬
            rec["inherit_from"] = inherit_from
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

