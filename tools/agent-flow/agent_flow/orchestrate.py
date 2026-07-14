from __future__ import annotations
# orchestrate.py — 元 agent-flow.py の 4257-4550 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# orchestrate
# --------------------------------------------------------------------------
def _plan_strategy(args):
    review = getattr(args, "review", "auto")  # 'auto'/True/False の三値
    gran = getattr(args, "granularity", "finest")
    if args.planner == "flow-planner":
        return plan_strategy_flow_planner(args.request, args.model, review, gran)
    if args.planner == "agent":
        return plan_strategy_kiro(args.request, args.model, review, gran)
    return plan_strategy_stub(args.request, review, gran)


def _env_failure_reason(results: dict) -> "str | None":
    """失敗結果に環境要因（quota/auth/env）のトリアージタグがあれば、その説明を返す。

    環境が壊れているとき（認証切れ・利用上限・CLI 不在）は、どのノードをリトライしても
    同じ理由で落ちる。実際 codex の利用上限で全ノードが 1 つずつリトライを焼き尽くし、
    26 ノード × max_retries 回の無駄な LLM 起動と「理由不明の全滅」が起きた。
    タスクの内容の問題（タグ無し）とは区別し、run を即座に失敗で終端して人に環境を直させる
    （直後の resume-run / agent-project の自動再開で done は温存されたまま続きから走る）。"""
    for nid, r in results.items():
        if r.get("status") != "failed":
            continue
        m = _AGENT_ERROR_TAG_RE.search(str(r.get("output", "")))
        if m and m.group(1) in AGENT_ERROR_ENV_CLASSES:
            hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == m.group(1)), "")
            return (f"[agent-error:{m.group(1)}] 環境要因の失敗（{nid}）: {hint} "
                    "リトライを打ち切りました。環境を直してから再開してください"
                    "（完了済みの工程は温存されます）。")
    return None


def _continue(args, request, nodes, results, iteration, strategy=None):
    # 失敗トリアージ: 環境要因（quota/auth/env）の失敗が 1 つでもあれば再計画せず打ち切る。
    # planner（stub/kiro）に依らず先に判定する（LLM 評価も同じ環境で失敗するため）。
    env_fail = _env_failure_reason(results)
    if env_fail:
        return "failed", [], env_fail
    mf = int(getattr(args, "max_fanout", 50) or 50)
    # 計画時に確定した review 判断を再利用（resume・継続でも一貫させる）。
    # CLI で明示指定（True/False）があればそれを優先。
    cli = getattr(args, "review", "auto")
    if isinstance(cli, bool):
        review = cli
    elif strategy and "review" in strategy:
        review = bool(strategy["review"])
    else:
        review = _review_decision(cli, (strategy or {}).get("patterns", []))
    ef = bool(getattr(args, "exemplar_first", False))
    mr = int(getattr(args, "max_retries", 3) or 3)
    # 再計画（evaluator-optimizer）はオーケストレータ側でローカルに判断する。stub のときだけ
    # stub 継続、それ以外（kiro やプラグイン executor）はローカル kiro で判断する
    # （プラグインはワーカータスクの実行のみを委譲し、メタ評価はローカルに残す）。
    if args.executor == "stub":
        return continue_stub(request, nodes, results, iteration, mf, review, ef, mr)
    return continue_kiro(request, nodes, results, iteration, mf, review, ef, mr)


def _node_entry(t):
    e = {"goal": t["goal"], "deps": t["deps"], "kind": t.get("kind", "work")}
    if t.get("retries"):  # サーキットブレーカー用の作り直し回数（>0 のときだけ保持）
        e["retries"] = int(t["retries"])
    return e


def _collapse_split_successors(nodes: dict) -> dict:
    """split は実行時 fan-out で map→reduce を生成するのが正典。planner が split の
    後段に静的な work/reduce を付けると fan-out と二重化し、意図を失った map と
    重複 reduce が並走する。fan-out 前（<split>-reduce 未生成）に限り、split に
    （推移的に）依存する静的後段ノードを除去する。"""
    splits = {i for i, n in nodes.items()
              if n.get("kind") == "split" and f"{i}-reduce" not in nodes}
    if not splits:
        return nodes
    tainted, changed = set(splits), True
    while changed:
        changed = False
        for i, n in nodes.items():
            if i in tainted:
                continue
            if any(d in tainted for d in n.get("deps", [])):
                tainted.add(i)
                changed = True
    for i in tainted - splits:  # split 自体は残し、後段だけ落とす
        nodes.pop(i, None)
    return nodes


def _sanitize_graph(nodes: dict) -> dict:
    """グラフ健全性検査: 未知の依存 ID を除去し、循環依存を断ち切る。
    planner（kiro）の誤出力や継続での追加に対する防御。"""
    _collapse_split_successors(nodes)
    ids = set(nodes)
    for n in nodes.values():
        n["deps"] = [d for d in n.get("deps", []) if d in ids and d != n.get("id")]
    # Kahn 法で到達可能順を求め、到達できないノード（循環）の残依存を落とす
    from collections import deque
    pending = {i: set(nodes[i]["deps"]) for i in ids}
    ready = deque(i for i in ids if not pending[i])
    done = set()
    while ready:
        x = ready.popleft()
        done.add(x)
        for i in ids:
            if x in pending[i]:
                pending[i].discard(x)
                if not pending[i] and i not in done and i not in ready:
                    ready.append(i)
    for i in ids:
        if i not in done:  # 循環に含まれる → 未解決の依存を断ち切る
            nodes[i]["deps"] = [d for d in nodes[i]["deps"] if d in done]
    return nodes


def _finalize_run(bus, args, iteration: int, failure: "str | None" = None) -> None:
    """全ノードの結果を集約して final.json を書き出し、run を終端して push・ログ出力する。
    failure（環境要因の打ち切り等）が渡されたら done でなく failed で終端し、理由を
    meta.failure_reason に残す（トリアージタグ付き → agent-project / viewer が同じ判定を読む）。"""
    results = {nid: (bus.read_result(nid) or {}) for nid in bus.task_ids()}
    summary = "\n".join(
        f"- {nid} [{r.get('status')}]: {str(r.get('output',''))[:200]}"
        for nid, r in results.items())
    write_json_atomic(bus.final_path, {
        "request": args.request,
        "finished_at": now_iso(),
        "iterations": iteration,
        "strategy": (bus.read_graph() or {}).get("strategy", {}),
        "summary": summary,
        "results": results,
        **({"failure_reason": failure} if failure else {}),
    })
    if failure:
        bus.mark_run_failed(args.run_id, failure)
        log(args.node_id, f"打ち切り（iteration={iteration}）: {failure}")
    else:
        bus.set_status("done")
        log(args.node_id, f"完了（iteration={iteration}）。final.json を書き出しました。")
    bus.sync_push(f"finalize run {args.run_id}")
    log(args.node_id, "結果サマリ:\n" + summary)


def _orch_check_canceled(bus: Bus, args, who: str) -> bool:
    """cancel マーカーがあれば run を canceled に終端化して True を返す（orchestrator の停止用）。
    close_issues 要求があるときは waits を残す（daemon/cmd_cancel がイシュー後始末に座標を使う）。
    それ以外は waits を掃除して park の再ポーリングを止める。
    既に meta が canceled ならマーカー無しでも止まる（daemon が適用後にマーカーを消したあとでも
    同じ ID の orch が走り続けない／再起動即 cancel と整合する）。"""
    meta = bus.run_meta(args.run_id) or {}
    already = meta.get("status") == "canceled"
    if not already and not bus.is_canceled_requested(args.run_id):
        return False
    info = bus.cancel_info(args.run_id) if bus.is_canceled_requested(args.run_id) else {}
    reason = info.get("reason") or meta.get("cancel_reason") or "cancel 指示"
    if not info.get("close_issues"):
        bus.clear_waits_for_run(args.run_id)
    if not already:
        bus.mark_canceled(args.run_id, reason)
    bus.event(who, "canceled", run=args.run_id, reason=reason)
    bus.sync_push(f"cancel run {args.run_id}: {reason}")
    log(who, f"cancel 指示を検知（{reason}）。orchestrator を終了します。")
    return True


def cmd_orchestrate(args) -> int:
    who = args.node_id
    bus = make_bus(args, who)
    bus.sync_pull()
    # リトライ: 先行 run（--inherit-from）から確定済みノードを引き継ぎ、先行 run を掃除する。
    # ensure_run より前に行う＝seed した meta を ensure_run が上書きしないようにする。
    inh = getattr(args, "inherit_from", None)
    if inh and read_json(bus.meta_path) is None:
        info = bus.inherit_from(inh, getattr(args, "orphan_grace", 0.0) or 0.0)
        log(who, f"先行 run {inh} を処理: {info['reason']}"
                 f"（引き継ぎ {info['seeded_nodes']} ノード・削除={info['deleted']}）")
        bus.sync_push(f"inherit {inh} -> {args.run_id}: {info['reason']}")
    bus.ensure_run(args.request, parse_workspace(getattr(args, "workspace", None)),
                   parse_references(getattr(args, "references", None)))
    bus.note_executor(getattr(args, "executor", None) or "agent")   # viewer の表示切替用
    # 生存リース（heartbeat）は orchestrator 自身が張る。daemon 経由の run だけが lease を持つと、
    # agent-flow run で都度起動される run（agent-project の主経路）には lease が永久に書かれず、
    # 消費者側の「停滞 run か？」判定（run_is_orphaned / _run_resumable）が lease の不在を
    # 「生きている」とも「死んでいる」とも決められない。orchestrator が消えた run は永久に
    # status=running のまま残り、失敗ノードも pending ノードも二度と実行されなくなる。
    lease_window = _run_lease_window(args)
    _last_touch = [0.0]

    def heartbeat(force: bool = False) -> None:
        """「この run は駆動中」を meta に刻む。

        git バスでは meta の書き換えを未コミットのまま残せない: sync_pull は pull --rebase なので
        dirty な作業ツリーでは失敗し続け、他ノードの結果を永久に取り込めなくなる（静止判定に
        到達せず run が止まる）。更新したぶんは必ず sync_push で確定させる。push は転送を伴う
        ので、毎 poll ではなくリースの 1/3 ごとに間引く（ローカルバスでは sync_push は no-op）。"""
        now = time.time()
        if not force and now - _last_touch[0] < lease_window / 3.0:
            return
        _last_touch[0] = now
        bus.touch_run(args.run_id, lease_window)
        bus.sync_push(f"heartbeat run {args.run_id}")

    heartbeat(force=True)      # 計画（LLM）は数十秒かかる。その前に張る。
    # 計画が lease_window（下限 120s）を超えると run_is_orphaned が真になり、daemon 経由で
    # ない直起動 orchestrate や心拍を引き継ぐ親が居ない経路で二重に adopt される。
    # 計画中は短い間隔で heartbeat し続ける（終了後 stop）。
    _plan_stop = threading.Event()

    def _plan_hb() -> None:
        while not _plan_stop.wait(max(lease_window / 3.0, 5.0)):
            try:
                heartbeat(force=True)
            except Exception:  # noqa: BLE001 — 心拍失敗で計画自体を落とさない
                pass

    _plan_th = threading.Thread(target=_plan_hb, name="orch-plan-hb", daemon=True)
    graph = bus.read_graph()

    # 既存グラフがあれば計画をやり直さず再開（resume）
    if graph and graph.get("nodes"):
        iteration = graph.get("iteration", 0)
        log(who, f"run={args.run_id} 再開（既存 {len(graph['nodes'])} ノード, iteration={iteration}）")
        if not bus.all_terminal():
            bus.set_status("running")
            bus.sync_push(f"resume run {args.run_id}")
    else:
        # 要求から 7 パターンの組み合わせと並列数を選び、初期グラフを形作る
        _plan_th.start()
        try:
            strategy, tasks = _plan_strategy(args)
        finally:
            _plan_stop.set()
            _plan_th.join(timeout=2.0)
        graph = {"strategy": strategy,
                 "nodes": {t["id"]: _node_entry(t) for t in tasks},
                 "iteration": 0}
        _sanitize_graph(graph["nodes"])  # 未知依存・循環を弾く
        bus.write_graph(graph)
        for t in tasks:
            bus.write_task(t)
        bus.set_status("running")
        bus.event(who, "planned", patterns=strategy["patterns"],
                  parallelism=strategy["parallelism"], tasks=[t["id"] for t in tasks])
        bus.sync_push(f"plan run {args.run_id}: {strategy['patterns']} x{strategy['parallelism']}")
        log(who, f"戦略: patterns={strategy['patterns']} parallelism={strategy['parallelism']} "
                 f"（{strategy.get('reason','')}）")
        log(who, f"初期タスク: {[(t['id'], t.get('kind','work')) for t in tasks]}")
        iteration = 0

    # evaluator-optimizer ループ: 静止（claim 可能・実行中タスクが無い）→ パターン継続判断
    consumed_fb: set = set()   # in-flight 反映済みの人フィードバック発生源（同一 settlement を二度反映しない）
    while True:
        if _orch_check_canceled(bus, args, who):
            return 0
        heartbeat()               # 評価・再計画は長い（LLM）ので周回ごとに更新
        graph = bus.read_graph()
        while not _quiesced(bus, graph["nodes"]):
            bus.sync_pull()
            heartbeat()          # 走っている限りリースを延ばす
            if _orch_check_canceled(bus, args, who):
                return 0
            graph = bus.read_graph()
            # in-flight 差し戻し: 静止を待たず、人の指摘を待機ノードへ即時反映（実行中は不変）。
            # ノード追加は静止時の評価役に委ねる（二重生成回避）。
            _inflight_amend_pending(bus, graph, who, args, consumed_fb)
            time.sleep(args.poll)
            graph = bus.read_graph()
        bus.sync_pull()
        graph = bus.read_graph()
        nodes = graph["nodes"]
        results = {nid: (bus.read_result(nid) or {}) for nid in nodes}

        if iteration >= args.max_iterations:
            decision, new_tasks, reason = "done", [], f"max-iterations({args.max_iterations}) 到達"
        else:
            decision, new_tasks, reason = _continue(
                args, args.request, nodes, results, iteration, graph.get("strategy"))
        log(who, f"評価 #{iteration}: {decision} — {reason}")

        if decision == "replan" and new_tasks:
            iteration += 1
            for t in new_tasks:
                graph["nodes"][t["id"]] = _node_entry(t)
                bus.write_task({k: v for k, v in t.items() if k != "replaces"})
                # replaces 指定: 旧ノードを外し、旧ノードに依存する後続を新ノードへ付け替える
                old = t.get("replaces")
                if old and old in graph["nodes"]:
                    for n in graph["nodes"].values():
                        n["deps"] = [t["id"] if d == old else d for d in n.get("deps", [])]
                    del graph["nodes"][old]
            _sanitize_graph(graph["nodes"])  # 追加で混入した未知依存・循環を弾く
            graph["iteration"] = iteration
            bus.write_graph(graph)
            bus.set_status("running")
            bus.event(who, "replan", iteration=iteration, added=[t["id"] for t in new_tasks])
            bus.sync_push(f"replan #{iteration} run {args.run_id}: +{[t['id'] for t in new_tasks]}")
            log(who, f"再計画 #{iteration}: 追加タスク {[(t['id'], t.get('kind','work')) for t in new_tasks]}")
            continue
        break

    # 全ノード結果を集約 → final.json 書き出し → 終端（done / 環境要因なら failed）・push
    _finalize_run(bus, args, iteration,
                  failure=(reason if decision == "failed" else None))
    return 0
