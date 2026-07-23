from __future__ import annotations
# board.py — 委譲公示板（agent-board）への参加（請負・入札）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
#
# agent-board は「リポジトリ＋契約」だけで処理を持たない（schemas/board.schema.json）。
# 入札・引き渡しの処理はこの請負側デーモンが担う: 板を巡回し、workload=flow の公示に repos/tags
# 照合で入札（flow の claim をそのまま流用＝同じ仕様）、勝てば自分の inbox へ submit_request で
# 取り込む（＝下の inbox→orchestrator フローがそのまま拾う）。結合はデータ契約のみ — agent-board
# のコードは import せず、板のレイアウトを読み書きするだけ。設計:
# docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md


def _board_bus(spec: str, node_id: str, args) -> "Bus":
    """板リポジトリを指す Bus/GitBus。git+<url> はノード専用クローン、他はローカル dir。
    flow の Bus/GitBus をそのまま使い、claim（_try_claim_in / _winner_in）を板の bids ディレクトリへ
    適用する（run レイアウトは使わないので run_id は "_"）。"""
    spec = str(spec or "").strip()
    if spec.startswith("git+"):
        remote = spec[4:]
        base = getattr(args, "board_workdir", None) or os.path.join(
            os.path.expanduser("~/.agents/flow-board"),
            hashlib.sha1(remote.encode()).hexdigest()[:8])
        clone_dir = os.path.join(os.path.abspath(base), _safe(node_id))
        return GitBus(clone_dir, "_", remote=remote,
                      branch=getattr(args, "board_branch", "main") or "main", subdir="")
    return Bus(os.path.abspath(spec), "_")


def _norm_repo_url(u: str) -> str:
    u = str(u or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _node_repo_ids(node_repos) -> "set[str]":
    """ノードの repos レジストリ（repos.schema.json 形）から担当リポジトリの名前と正規化 URL の集合。
    参照（owns 無し / readonly）は書込先候補にしないため除く。"""
    have: "set[str]" = set()
    items = node_repos.items() if isinstance(node_repos, dict) else []
    for name, e in items:
        if str(name).startswith("_") or not isinstance(e, dict):
            continue
        if e.get("readonly") or not (e.get("owns")):
            continue
        have.add(str(name))
        if e.get("url"):
            have.add(_norm_repo_url(e["url"]))
    return have


def board_eligible(post: dict, node_repos, node_tags) -> bool:
    """このノードが公示に入札してよいか（成果物リポジトリ・タグでの選別）。
    workspace.url と requires.repos を担当し、requires.tags を包含していれば可。"""
    req = post.get("requires") or {}
    need_tags = set(str(t) for t in (req.get("tags") or []))
    if need_tags and not need_tags.issubset(set(node_tags or [])):
        return False
    have = _node_repo_ids(node_repos)
    ws = post.get("workspace") or {}
    if ws.get("url"):
        if _norm_repo_url(ws["url"]) not in have:
            return False
    for ref in (req.get("repos") or []):
        if str(ref) not in have and _norm_repo_url(ref) not in have:
            return False
    return True


def _board_request(post: dict) -> str:
    goal = str(post.get("goal") or "").strip()
    design = str(post.get("design") or "").strip()
    return f"## 設計\n\n{design}\n\n---\n\n{goal}" if design else goal


_FLOW_TO_BOARD_STATUS = {"done": "done", "failed": "failed", "canceled": "cancelled"}


def report_board_results(bus_local: "Bus", board: "Bus", node_id: str) -> "list[str]":
    """自分が落札・引き渡し済みの委譲のうち、ローカル run が終端に達したものを board の
    result.json へ書き戻す（依頼側 agent-project 等の自動回収先。board は「リポジトリ＋契約」
    だけで処理を持たないため、この報告は請負側＝このデーモンの責務）。speculation 無し（既定・
    単一落札）を前提に、落札ノード自身が確定点を書く（設計 board.schema.json §result）。
    冪等（result.json が既にあれば触らない・二重報告しない）。書き戻した委譲 id の一覧を返す。"""
    deleg_root = os.path.join(board.root, "delegations")
    reported = []
    if not os.path.isdir(deleg_root):
        return reported
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir) or os.path.exists(os.path.join(ddir, "result.json")):
            continue
        status_path = os.path.join(ddir, "status", f"{_safe(node_id)}.json")
        st = read_json(status_path)
        if not st or st.get("state") != "dispatched":
            continue    # 自分が落札した委譲ではない（または既に終端まで報告済み）
        run_status = bus_local.run_view(did).get_status()
        board_status = _FLOW_TO_BOARD_STATUS.get(str(run_status or ""))
        if board_status is None:
            continue    # まだ実行中（pending/planning/running 等）
        write_json_atomic(os.path.join(ddir, "result.json"), {
            "winner": node_id, "native_id": did, "status": board_status,
            "resolved_by": node_id, "resolved_at": now_iso(),
        })
        write_json_atomic(status_path, {**st, "state": board_status, "heartbeat": now_iso()})
        reported.append(did)
        log(node_id, f"board 成果報告 {did}: {board_status}")
    if reported:
        board.sync_push(f"report {len(reported)} results")
    return reported


def _write_or_renew_bid(bids_dir: str, node_id: str, lease: float, workload: str) -> bool:
    """bids/<node_id>.json を書く／更新する。既存が無ければ新規（ts はいま）、あれば残 lease が
    半分未満のときだけ lease_until を延長する（(ts, who) タイブレークの根拠 ts は温存し、
    毎 poll 書き換えて先勝ちの意味を壊さない・push 頻度も抑える）。書いたら True。"""
    path = os.path.join(bids_dir, f"{_safe(node_id)}.json")
    cur = read_json(path)
    now = time.time()
    if isinstance(cur, dict):
        if float(cur.get("lease_until", 0)) - now > lease / 2.0:
            return False    # まだ十分残っている → 今回は延長不要
        ts = cur.get("ts", now)
        claimed_at = cur.get("claimed_at", now_iso())
    else:
        ts = now
        claimed_at = now_iso()
    os.makedirs(bids_dir, exist_ok=True)
    write_json_atomic(path, {"who": node_id, "ts": ts, "claimed_at": claimed_at,
                             "lease_until": now + lease, "workload": workload})
    return True


def _renew_dispatched_leases(board: "Bus", node_id: str, lease: float) -> None:
    """自分が落札・引き渡し済みでまだ終端していない委譲の bid lease を延長する（設計 §5.2 の
    「status/<who>.json のハートビートで延長」）。延長しないと長時間 run が board_lease
    （既定 900 秒）を超えたときに他ノードから見て勝者が失効し、再入札→二重実行が起こりうる
    （§8「落札ノードのクラッシュ」検知はこの心拍の停止で成立する——延長を止める＝クラッシュ扱い）。"""
    deleg_root = os.path.join(board.root, "delegations")
    if not os.path.isdir(deleg_root):
        return
    renewed = False
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir) or os.path.exists(os.path.join(ddir, "result.json")) or \
           os.path.exists(os.path.join(ddir, "cancelled.json")):
            continue
        status_path = os.path.join(ddir, "status", f"{_safe(node_id)}.json")
        st = read_json(status_path)
        if not st or st.get("state") in (None, "done", "failed", "cancelled", "away"):
            continue    # 自分が落札した委譲ではない（または既に終端/away）
        if _write_or_renew_bid(os.path.join(ddir, "bids"), node_id, lease, "flow"):
            write_json_atomic(status_path, {**st, "heartbeat": now_iso(),
                                            "lease_until": time.time() + lease})
            renewed = True
    if renewed:
        board.sync_push(f"lease renew by {node_id}")


def poll_board(bus_local: "Bus", args, node_id: str) -> "list[str]":
    """板を 1 巡: まず自分が落札済みの委譲の完了を board へ報告し、実行中のものは bid lease を
    延長し、次に workload=flow の公示に入札する。policy.assignment が既定の first-come なら
    claim 勝者＝即落札。owner-picks なら bid（応募）を書くだけに留め、依頼者が award.json で
    自分を指名したときだけ落札として取り込む（設計 §5.2）。落札→取り込んだ委譲 id の一覧を返す
    （報告は別途 report_board_results の返り値・呼び出し元は必要なら両方 log できる）。
    board 未設定なら no-op。例外は呼び出し側が握る。"""
    spec = getattr(args, "board", None)
    if not spec:
        return []
    board = _board_bus(spec, node_id, args)
    board.sync_pull()
    report_board_results(bus_local, board, node_id)
    node_repos = getattr(args, "board_repos", None) or {}
    node_tags = getattr(args, "board_tags", None) or []
    lease = float(getattr(args, "board_lease", None) or 900.0)
    _renew_dispatched_leases(board, node_id, lease)
    deleg_root = os.path.join(board.root, "delegations")
    handed = []
    if not os.path.isdir(deleg_root):
        return handed
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir):
            continue
        # 終端（result / cancelled）は触らない
        if os.path.exists(os.path.join(ddir, "result.json")) or \
           os.path.exists(os.path.join(ddir, "cancelled.json")):
            continue
        post = read_json(os.path.join(ddir, "post.json"))
        if not isinstance(post, dict) or post.get("workload") != "flow" or post.get("op") != "post":
            continue
        # 既に自分が取り込み済み（inbox / run 生成済み）ならスキップ
        if bus_local.read_inbox(did) is not None or bus_local.run_exists(did):
            continue
        bids_dir = os.path.join(ddir, "bids")
        assignment = str((post.get("policy") or {}).get("assignment") or "first-come")
        if assignment == "owner-picks":
            # 先勝ちタイブレークでは決めない。bid ＝応募として書くだけで、依頼者が
            # award.json を書いた者だけが落札する（設計 §5.2）。
            if not board_eligible(post, node_repos, node_tags):
                continue
            award = read_json(os.path.join(ddir, "award.json"))
            awarded_node = award.get("node") if isinstance(award, dict) else None
            if awarded_node is None:
                if _write_or_renew_bid(bids_dir, node_id, lease, "flow"):
                    board.sync_push(f"apply {did} by {node_id}")
                continue
            if awarded_node != node_id:
                continue    # 他ノードが落札
            # 自分が award された → 落札として下の取り込みへ進む
        else:
            w = board._winner_in(bids_dir)
            if w is not None and w != node_id:
                continue      # 既に他ノードが勝者（先勝ち）
            if not board_eligible(post, node_repos, node_tags):
                continue
            if not board._try_claim_in(bids_dir, node_id, lease, f"bid {did} by {node_id}"):
                continue
        # 落札 → 自分の inbox へ取り込み（下の inbox→orchestrator が拾う）
        bus_local.submit_request(
            did, _board_request(post), f"agent-board:{node_id}",
            workspace=post.get("workspace") or None,
            references=post.get("references") or [],
            delegation={"id": did, "board": True})
        bus_local.sync_push(f"board handoff {did} -> inbox")
        # 板へ実行状態を残す（依頼側の観測用）
        write_json_atomic(os.path.join(ddir, "status", f"{_safe(node_id)}.json"), {
            "who": node_id, "state": "dispatched", "native_id": did,
            "heartbeat": now_iso(), "lease_until": time.time() + lease})
        board.sync_push(f"won+dispatch {did} by {node_id}")
        handed.append(did)
        log(node_id, f"board 落札→取り込み {did}: {str(post.get('goal',''))[:50]}")
    return handed
