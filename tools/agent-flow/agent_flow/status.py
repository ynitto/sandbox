from __future__ import annotations
# status.py — 元 agent-flow.py の 5673-5937 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# status — 状態表示。既定は 1 回表示、--follow でライブ監視（tmux ペイン向け）
# --------------------------------------------------------------------------
_STATE_GLYPH = {"done": "✓", "failed": "✗", "claimed": "▶", "pending": "○", "unknown": "·"}


def _progress_bar(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[" + "·" * width + "] 0/0"
    filled = int(width * done / total)
    pct = int(100 * done / total)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {done}/{total} ({pct}%)"


def _node_depth(nid, nodes, memo):
    if nid in memo:
        return memo[nid]
    memo[nid] = 0  # 循環ガード（_sanitize_graph 済みだが念のため）
    deps = [d for d in nodes.get(nid, {}).get("deps", []) if d in nodes]
    d = 0 if not deps else 1 + max(_node_depth(x, nodes, memo) for x in deps)
    memo[nid] = d
    return d


def _elapsed(meta) -> str:
    a = meta.get("created_at")
    b = meta.get("updated_at") or now_iso()
    try:
        ta = datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
        tb = datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
        s = int((tb - ta).total_seconds())
        return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"
    except (TypeError, ValueError):
        return "-"


# 集約・最終ノード（sink）として優先する kind。これらがあれば最終成果とみなす。
_AGG_KINDS = ("synthesize", "reduce", "judge", "filter")


def _final_result_nodes(nodes: dict, results: dict) -> list:
    """ワークフローの最終成果に当たるノード id を返す。

    sink（他ノードの deps に現れない末端）かつ done のものを集め、集約 kind
    （synthesize/reduce/judge/filter）があればそれを優先する。末端が無い／done で
    ないときは done ノード全体へフォールバックする（最終結果を必ず何か返すため）。"""
    if not nodes:
        return []
    done = [nid for nid in nodes if (results.get(nid) or {}).get("status") == "done"]
    if not done:
        return []
    depended = {d for n in nodes.values() for d in n.get("deps", [])}
    sinks = [nid for nid in done if nid not in depended]
    pool = sinks or done
    agg = [nid for nid in pool if nodes[nid].get("kind") in _AGG_KINDS]
    return agg or pool


def _render_status(bus, run_id, events):
    """公式 Dynamic Workflows 風のダッシュボード表示。
    進捗バー / エージェント（タスク）状態ツリー / 直近アクティビティ / 最終サマリ。"""
    graph = bus.read_graph()
    status = bus.get_status()
    meta = bus.run_meta(run_id) if hasattr(bus, "run_meta") else (read_json(bus.meta_path) or {})
    nodes = (graph or {}).get("nodes", {})

    states = {nid: bus.node_state(nid) for nid in nodes}
    counts = {}
    for st in states.values():
        counts[st] = counts.get(st, 0) + 1
    total = len(nodes)
    done = counts.get("done", 0) + counts.get("failed", 0)

    L = []
    L.append(f"╭─ agent-flow ── run {run_id} ── [{(status or '?').upper()}]  ⏱ {_elapsed(meta)}")
    if meta.get("request"):
        L.append(f"│  request : {meta['request'][:78]}")
    if graph and graph.get("strategy"):
        s = graph["strategy"]
        pats = " + ".join(s.get("patterns", []) or [])
        L.append(f"│  strategy: {pats}   ‖parallel={s.get('parallelism','?')}"
                 f"   iter={graph.get('iteration', 0)}")
    if total:
        L.append(f"│  progress: {_progress_bar(done, total)}")
        order = ("done", "claimed", "pending", "failed", "unknown")
        agentline = "  ".join(f"{_STATE_GLYPH[k]}{k}={counts[k]}" for k in order if counts.get(k))
        L.append(f"│  agents  : {total}   {agentline}")
        L.append("├─ tasks")
        memo = {}
        ordered = sorted(nodes, key=lambda n: (_node_depth(n, nodes, memo), n))
        for nid in ordered:
            node = nodes[nid]
            g = _STATE_GLYPH.get(states[nid], "·")
            indent = "  " * _node_depth(nid, nodes, memo)
            res = bus.read_result(nid) or {}
            who = res.get("who", "")
            dep = (" ← " + ",".join(node.get("deps", []))) if node.get("deps") else ""
            who_s = f"  @{who}" if who else ""
            L.append(f"│  {g} {indent}{nid} [{node.get('kind','work')}]{dep}{who_s}")
    else:
        L.append("│  (グラフ未生成 — 計画中)")

    if events:
        evs = bus.recent_events(events)
        if evs:
            L.append("├─ activity")
            for e in evs:
                ts = (e.get("ts", "") or "")[11:19]  # HH:MM:SS
                detail = e.get("node", "") or (",".join(e.get("tasks", [])) if e.get("tasks") else "")
                L.append(f"│  {ts}  {e.get('who',''):<14} {e.get('kind',''):<8} {detail}")

    if status in TERMINAL:
        node_results = {nid: bus.read_result(nid) or {} for nid in nodes}
        sink_ids = _final_result_nodes(nodes, node_results)
        if sink_ids:
            L.append("├─ result")
            for nid in sink_ids:
                out = str(node_results[nid].get("output", "")).strip()
                lines = out.splitlines() or ["(出力なし)"]
                L.append(f"│  ◆ {nid} [{nodes[nid].get('kind', 'work')}]")
                for line in lines[:10]:
                    L.append(f"│    {line[:96]}")
                if len(lines) > 10:
                    L.append(f"│    … (全 {len(lines)} 行 — 全文は `agent-flow result` で)")
        else:
            final = read_json(bus.final_path)
            if final:
                L.append("├─ result")
                for line in final.get("summary", "").splitlines()[:20]:
                    L.append(f"│  {line}")
    L.append("╰─")
    return status, "\n".join(L)


def _resolve_run_id(args) -> str | None:
    """--run-id 未指定時に最新 run を自動選択（done/failed 含む）。
    見つからなければ None を返す。"""
    probe = make_bus(args, "status-viewer")
    probe.sync_pull()
    runs = probe.list_runs()
    if not runs:
        return None
    metas = [(rid, probe.run_meta(rid)) for rid in runs]
    metas.sort(key=lambda x: x[1].get("created_at", x[0]), reverse=True)
    return metas[0][0]


def cmd_status(args) -> int:
    # --list: run 一覧を表示して終了
    if getattr(args, "list", False):
        probe = make_bus(args, "status-viewer")
        probe.sync_pull()
        runs = probe.list_runs()
        if not runs:
            print("run がありません。")
            return 0
        metas = [(rid, probe.run_meta(rid)) for rid in runs]
        metas.sort(key=lambda x: x[1].get("created_at", x[0]), reverse=True)
        for rid, meta in metas:
            req = meta.get("request", "")[:50]
            print(f"  {rid}  status={meta.get('status','?'):<8}  "
                  f"created={meta.get('created_at','?')}  req={req}")
        return 0

    # run_id が未指定の場合、最新の run を自動選択（終了済み含む）
    if not args.run_id:
        resolved = _resolve_run_id(args)
        if not resolved:
            print("エラー: run が見つかりません。まず agent-flow run を実行してください。",
                  file=sys.stderr)
            return 1
        args.run_id = resolved
        print(f"(run_id 未指定 — 最新の run を表示: {args.run_id})", file=sys.stderr)

    bus = make_bus(args, "status-viewer")
    try:
        while True:
            bus.sync_pull()
            status, text = _render_status(bus, args.run_id, args.events)
            if args.follow:
                sys.stdout.write("\033[2J\033[H")  # 画面クリア
            print(text, flush=True)
            if not args.follow or (args.until_done and status in TERMINAL):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_result(args) -> int:
    """完了した run の最終結果を探し出して提示する。

    status が進捗ダッシュボードなのに対し、result は成果そのものを返す。
    最終成果＝集約／末端（sink）ノードの全文出力（`_final_result_nodes` で特定）。
    run_id 未指定なら最新 run を自動選択（status と同じ挙動）。未完了なら
    その旨を知らせ、確定済みの成果があれば参考表示する。"""
    if not args.run_id:
        resolved = _resolve_run_id(args)
        if not resolved:
            print("エラー: run が見つかりません。まず agent-flow run を実行してください。",
                  file=sys.stderr)
            return 1
        args.run_id = resolved
        print(f"(run_id 未指定 — 最新の run: {args.run_id})", file=sys.stderr)

    bus = make_bus(args, "result-viewer")
    bus.sync_pull()
    status = bus.get_status()
    graph = bus.read_graph() or {}
    nodes = graph.get("nodes", {})
    results = {nid: (bus.read_result(nid) or {}) for nid in nodes}
    final_meta = read_json(bus.final_path) or {}
    request = final_meta.get("request") or bus.run_meta(args.run_id).get("request", "")
    sink_ids = _final_result_nodes(nodes, results)

    if getattr(args, "json", False):
        print(json.dumps({
            "run_id": args.run_id,
            "status": status,
            "done": status in TERMINAL,
            "request": request,
            "strategy": graph.get("strategy") or final_meta.get("strategy", {}),
            "finished_at": final_meta.get("finished_at"),
            "final_nodes": [
                {"id": nid, "kind": nodes.get(nid, {}).get("kind", "work"),
                 "output": str(results.get(nid, {}).get("output", "")),
                 "data": results.get(nid, {}).get("data"),
                 "artifacts": results.get(nid, {}).get("artifacts", [])}
                for nid in sink_ids
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if status not in TERMINAL:
        done_n = sum(1 for r in results.values() if r.get("status") in TERMINAL)
        print(f"run {args.run_id} はまだ完了していません（status={status}, "
              f"{done_n}/{len(nodes)} 完了）。"
              f"進捗は `agent-flow status --run-id {args.run_id} --follow` で確認してください。",
              file=sys.stderr)
        if not sink_ids:
            return 0
        print("（現時点で確定している成果のみ表示します）")

    if not sink_ids:
        print("（最終結果がまだありません）")
        return 0

    print(f"== run {args.run_id} 最終結果 ==")
    if request:
        print(f"request : {request}")
    if final_meta.get("finished_at"):
        print(f"finished: {final_meta['finished_at']}")
    for nid in sink_ids:
        r = results.get(nid, {})
        kind = nodes.get(nid, {}).get("kind", "work")
        print(f"\n── {nid} [{kind}] ──")
        out = str(r.get("output", "")).strip()
        print(out or "(出力なし)")
        if r.get("data") is not None:
            print(f"[data] {json.dumps(r['data'], ensure_ascii=False)}")
        if r.get("artifacts"):
            print(f"[artifacts] {', '.join(r['artifacts'])}")
    return 0

