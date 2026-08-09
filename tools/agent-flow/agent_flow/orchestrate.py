from __future__ import annotations
# orchestrate.py — 元 agent-flow.py の 4257-4550 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# orchestrate
# --------------------------------------------------------------------------
def _plan_strategy(args, bus):
    review = getattr(args, "review", "auto")  # 'auto'/True/False の三値
    gran = getattr(args, "granularity", "auto") or "auto"
    # プロジェクト文脈（案 H・オプトイン）。run 作成時に snapshot_context() が meta へ固定済み
    # なので、ここではその結果を読むだけ（描画・マーカー付与は snapshot 側の責務）。
    ctx = run_context_text(bus)
    if args.planner == "flow-planner":
        return plan_strategy_flow_planner(args.request, args.model, review, gran, ctx)
    if args.planner == "agent":
        return plan_strategy_agent(args.request, args.model, review, gran, ctx)
    return plan_strategy_stub(args.request, review, gran)


def _plan_changes(tasks: list[dict]) -> dict:
    """再計画の種類を表示側で推測せずに済む、共通の変更差分。"""
    replaced = [
        {"old": str(t["replaces"]), "next": str(t["id"])}
        for t in tasks if t.get("replaces")
    ]
    return {
        "added": [str(t["id"]) for t in tasks],
        "replaced": replaced,
        "updated": [],
        "removed": [item["old"] for item in replaced],
    }


def _env_failure_reason(results: dict) -> "str | None":
    """失敗結果に実行制御・環境要因または transient のトリアージタグがあれば、その説明を返す。

    環境が壊れているとき（認証切れ・利用上限・CLI 不在）は、どのノードをリトライしても
    同じ理由で落ちる。実際 codex の利用上限で全ノードが 1 つずつリトライを焼き尽くし、
    26 ノード × max_retries 回の無駄な LLM 起動と「理由不明の全滅」が起きた。
    タスクの内容の問題（タグ無し）とは区別し、run を即座に失敗で終端して人に環境を直させる
    （直後の resume-run / agent-project の自動再開で done は温存されたまま続きから走る）。

    transient も同じ理屈で run 単位に打ち切る: ここへ届く transient はレイヤ1（run_agent の
    in-place 再試行）を使い切ってなお失敗している＝環境がまだ不調で、他ノードも同じ理由で
    落ちる公算が高い。ノード単位で再計画 retry の予算を焼かず run を failed 終端し、
    レイヤ4（daemon の auto-heal）が cooldown 後に done 温存のまま自動再開する。"""
    for nid, r in results.items():
        if r.get("status") != "failed":
            continue
        # 発生元マーカー > 構造化 error_class（worker が data に載せる）> output のタグ。
        # error_class は「その時の分類器」の答えなので、旧版が付けた誤分類をそのまま運ぶ
        # （実際 [agent-control] の停止が quota として保存され、再開しても直らなかった）。
        # 生の output に残るマーカーだけは後から見ても正しいので、それを最優先にする。
        output = str(r.get("output", ""))
        d = r.get("data")
        cls = _source_marker_class(output)
        if cls is None:
            cls = d.get("error_class") if isinstance(d, dict) else None
        if cls not in ("transient", "integration") + AGENT_ERROR_ENV_CLASSES:
            m = _AGENT_ERROR_TAG_RE.search(output)
            cls = m.group(1) if m else None
        if cls == "transient":
            return (f"[agent-error:transient] 一時的エラーの失敗（{nid}）: in-place 再試行でも"
                    "回復せず、run を打ち切りました。自動再開（auto-heal）の対象です"
                    "（完了済みの工程は温存されます）。")
        if cls == "integration":
            return (f"[agent-error:integration] target 統合の失敗（{nid}）: "
                    "未統合の成果を後続で検証・完了扱いにしないため run を打ち切りました。"
                    "新しい試行で最新 target を統合してください。")
        if cls in AGENT_ERROR_ENV_CLASSES:
            # 発生元マーカーも run まで運び、管理面の停止と node-budget の予算超過を
            # 表示層が区別できるようにする（どちらも「止まっている」だが直し方が違う）。
            marker = next((mk for mk, _ in _AGENT_ERROR_SOURCE_CLASSES if mk in output), "")
            source = f" {marker}" if marker else ""
            if marker == "[agent-control]":
                hint = "管理面で対象ワークロードの実行が一時停止または停止されています。"
            elif marker == "[node-budget]":
                hint = "このノードに設定した実行時間またはトークン予算に達しています。"
            else:
                hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == cls), "")
            category = "実行制御による停止" if cls == "control" else "環境要因の失敗"
            remedy = ("dashboard で実行を許可してから再開してください"
                      if cls == "control" else "環境を直してから再開してください")
            return (f"[agent-error:{cls}]{source} {category}（{nid}）: {hint} "
                    f"リトライを打ち切りました。{remedy}"
                    "（完了済みの工程は温存されます）。")
    return None


def _continue(args, bus, request, nodes, results, iteration, strategy=None):
    # 失敗トリアージ: 実行制御・環境要因の失敗が 1 つでもあれば再計画せず打ち切る。
    # planner（stub/エージェント）に依らず先に判定する（LLM 評価も同じ環境で失敗するため）。
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
    # 集約の扇入れ幅（設定 reduce_width）。子プロセスの orchestrate も --config 経由で
    # 同じ値を解決するため、argv での転記は要らない。
    rw = int(getattr(args, "reduce_width", _DEFAULT_REDUCE_WIDTH) or _DEFAULT_REDUCE_WIDTH)
    # 再計画（evaluator-optimizer）はオーケストレータ側でローカルに判断する。stub のときだけ
    # stub 継続、それ以外（agent やプラグイン executor）はローカルのエージェント CLI で判断する
    # （プラグインはワーカータスクの実行のみを委譲し、メタ評価はローカルに残す）。
    if args.executor == "stub":
        return continue_stub(request, nodes, results, iteration, mf, review, ef, mr, rw)
    # プロジェクト文脈（案 H・オプトイン）。全 replan 反復で同一バイト列を渡す
    # （既存の実装は毎回 request 全文を再埋め込みしており、charter/rules を分離した今
    # ここで補わないと反復のたびに分解質の材料が失われる）。
    ctx = run_context_text(bus)
    return continue_agent(request, nodes, results, iteration, mf, review, ef, mr, rw, context=ctx)


def _node_entry(t):
    e = {"goal": t["goal"], "deps": t["deps"], "kind": t.get("kind", "work")}
    if t.get("retries"):  # サーキットブレーカー用の作り直し回数（>0 のときだけ保持）
        e["retries"] = int(t["retries"])
    if t.get("read_allocation"):
        e["read_allocation"] = t["read_allocation"]
    if str(t.get("dependency_input") or "").strip().lower() in ("full", "digest"):
        e["dependency_input"] = str(t["dependency_input"]).strip().lower()
    if t.get("agent"):
        e["agent"] = t["agent"]
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
    planner（エージェント）の誤出力や継続での追加に対する防御。"""
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
    """cancel マーカーがあれば run を cancelled に終端化して True を返す（orchestrator の停止用）。
    close_issues 要求があるときは waits を残す（daemon/cmd_cancel がイシュー後始末に座標を使う）。
    それ以外は waits を掃除して park の再ポーリングを止める。
    既に meta が cancelled ならマーカー無しでも止まる（daemon が適用後にマーカーを消したあとでも
    同じ ID の orch が走り続けない／再起動即 cancel と整合する）。"""
    meta = bus.run_meta(args.run_id) or {}
    already = meta.get("status") == "cancelled"
    if not already and not bus.is_canceled_requested(args.run_id):
        return False
    info = bus.cancel_info(args.run_id) if bus.is_canceled_requested(args.run_id) else {}
    reason = info.get("reason") or meta.get("cancel_reason") or "cancel 指示"
    if not info.get("close_issues"):
        bus.clear_waits_for_run(args.run_id)
    if not already:
        bus.mark_canceled(args.run_id, reason)
    bus.event(who, "cancelled", run=args.run_id, reason=reason)
    bus.sync_push(f"cancel run {args.run_id}: {reason}")
    if bus.is_canceled_requested(args.run_id):
        bus.clear_cancel(args.run_id)  # heartbeat停止済みの実行所有者 acknowledgement
        bus.sync_push(f"ack cancel run {args.run_id}")
    log(who, f"cancel 指示を検知（{reason}）。orchestrator を終了します。")
    return True


def _with_run_heartbeat(heartbeat, lease_window: float, fn):
    """ブロッキング処理中も orchestrator lease を更新して fn の結果を返す。"""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(max(lease_window / 3.0, 0.01)):
            try:
                heartbeat(force=True)
            except Exception:  # noqa: BLE001 — 心拍失敗で本処理を落とさない
                pass

    thread = threading.Thread(target=beat, name="orch-blocking-hb", daemon=True)
    thread.start()
    try:
        return fn()
    finally:
        stop.set()
        thread.join()  # in-flight heartbeat の書込完了前に次の状態更新へ進まない


def cmd_orchestrate(args) -> int:
    who = args.node_id
    # セッション開始コマンド（agent-session-commands）。orchestrator も 1 プロセス＝1 セッション
    # として扱い、bus に触る前に前準備を済ませる。on_error='fail' が失敗したら起こさない。
    if not run_session_commands(who, {
        "engine": "agent-flow", "workload": "flow", "cwd": os.getcwd(),
        "workspace": os.getcwd(), "agent_cli": getattr(args, "agent_cli", "") or "",
        "model": getattr(args, "model", "") or "", "run_id": getattr(args, "run_id", "") or "",
        "node_id": who,
    }):
        return 1
    bus = make_bus(args, who)
    bus.sync_pull()
    # リトライ: 先行 run（--inherit-from）から確定済みノードを引き継ぎ、先行 run を掃除する。
    # ensure_run より前に行う＝seed した meta を ensure_run が上書きしないようにする。
    inh = getattr(args, "inherit_from", None)
    if inh and read_json(bus.meta_path) is None:
        # request（新世代の要求文＝差し戻しの意図・run ブリーフ入り）を渡し、seed される
        # meta.request を新世代で上書きする（worker の全体文脈へ最新の指摘を届ける）。
        info = bus.inherit_from(inh, getattr(args, "orphan_grace", 0.0) or 0.0,
                                request=args.request or "")
        log(who, f"先行 run {inh} を処理: {info['reason']}"
                 f"（引き継ぎ {info['seeded_nodes']} ノード・削除={info['deleted']}）")
        bus.sync_push(f"inherit {inh} -> {args.run_id}: {info['reason']}")
    bus.ensure_run(args.request, parse_workspace(getattr(args, "workspace", None)),
                   parse_references(getattr(args, "references", None)),
                   parse_verification_plan(getattr(args, "verification_plan", None)))
    bus.note_executor(getattr(args, "executor", None) or "agent")   # viewer の表示切替用
    _deleg_raw = getattr(args, "delegation", None)                  # 委譲公示板由来の来歴（board）
    if _deleg_raw:
        try:
            bus.note_delegation(json.loads(_deleg_raw))
        except (ValueError, TypeError):
            pass
    # グローバル指示（agent-instructions）を投入ノードの instructions.json から meta へ固定する。
    # GitBus 同期で委譲先ワーカーへ伝播する（run 単位の一貫性基準）。--no-global-instructions /
    # 環境変数 AGENT_FLOW_NO_GLOBAL_INSTRUCTIONS=1 で無効化。
    if not (getattr(args, "no_global_instructions", False)
            or os.environ.get("AGENT_FLOW_NO_GLOBAL_INSTRUCTIONS") == "1"):
        if bus.snapshot_instructions():
            bus.sync_push(f"snapshot global instructions {args.run_id}")
    # プロジェクト文脈スナップショット（案 H・オプトイン）。--context-file が渡されたときだけ
    # 固定する（agent-project が stable_prefix 有効時に渡す）。全ノード（planner・worker・
    # evaluator）が同一バイト列を使い回せるよう、この run の生涯で 1 度だけ書く。
    ctx_file = getattr(args, "context_file", None)
    if ctx_file:
        if bus.snapshot_context(ctx_file):
            bus.sync_push(f"snapshot project context {args.run_id}")
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

    def phase(name: str) -> None:
        bus.set_phase(name, who)
        bus.sync_push(f"phase {name} run {args.run_id}")

    heartbeat(force=True)      # 計画（LLM）は数十秒かかる。その前に張る。
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
        phase("planning")
        strategy, tasks = _with_run_heartbeat(
            heartbeat, lease_window, lambda: _plan_strategy(args, bus))
        graph = {"strategy": strategy,
                 "nodes": {t["id"]: _node_entry(t) for t in tasks},
                 "iteration": 0}
        base_sync = inject_base_sync(graph["nodes"], bus.run_workspace())
        if base_sync:
            tasks.append(base_sync)
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

    phase("executing")

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

        phase("evaluating")
        if iteration >= args.max_iterations:
            decision, new_tasks, reason = "done", [], f"max-iterations({args.max_iterations}) 到達"
        else:
            decision, new_tasks, reason = _with_run_heartbeat(
                heartbeat, lease_window,
                lambda: _continue(args, bus, args.request, nodes, results, iteration,
                                  graph.get("strategy")))
        log(who, f"評価 #{iteration}: {decision} — {reason}")

        if decision == "replan" and new_tasks:
            iteration += 1
            changes = _plan_changes(new_tasks)
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
            bus.event(who, "replan", iteration=iteration, reason=reason,
                      added=changes["added"], changes=changes)
            bus.sync_push(f"replan #{iteration} run {args.run_id}: +{[t['id'] for t in new_tasks]}")
            log(who, f"再計画 #{iteration}: 追加タスク {[(t['id'], t.get('kind','work')) for t in new_tasks]}")
            phase("executing")
            continue

        # 統一 verify: 評価が done に達し、成果 revision が確定したここで一度だけ検証する。
        # criterion / コマンドの fail は同じ run の修正ループへ戻す（世代交代より先に、いちばん
        # 安い層で直す）。inconclusive は修正回数を消費せず receipt のまま上位へ返す。
        # 環境要因の打ち切り（decision=failed）では検証しない——成果が確定していない。
        if decision != "failed":
            phase("verifying")
            try:
                receipt = run_verification_plan(
                    bus, args, who, heartbeat=heartbeat, lease_window=lease_window)
            except Exception as e:  # noqa: BLE001 — 検証の失敗で run を落とさない（receipt 無し=不採用）
                log(who, f"統一 verify の実行に失敗（receipt 無しで続行・fail-close）: {e}")
                receipt = None
            if receipt and receipt.get("verdict") == "fail" and iteration < args.max_iterations:
                fix = verify_fix_task(receipt, iteration)
                iteration += 1
                graph["nodes"][fix["id"]] = _node_entry(fix)
                bus.write_task(fix)
                _sanitize_graph(graph["nodes"])
                graph["iteration"] = iteration
                bus.write_graph(graph)
                bus.set_status("running")
                bus.event(who, "verify-fix", iteration=iteration, added=[fix["id"]])
                bus.sync_push(f"verify-fix #{iteration} run {args.run_id}: +{fix['id']}")
                log(who, f"統一 verify が fail → 修正ループ #{iteration}: {fix['id']}")
                phase("executing")
                continue
        break

    # 全ノード結果を集約 → final.json 書き出し → 終端（done / 環境要因なら failed）・push
    phase("finalizing")
    _finalize_run(bus, args, iteration,
                  failure=(reason if decision == "failed" else None))
    return 0
