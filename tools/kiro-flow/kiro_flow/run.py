from __future__ import annotations
# run.py — 元 kiro-flow.py の 4716-5097 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# run — 単発実行。既存 run-id なら再開、無ければ新規（状態で自動判断）
# --------------------------------------------------------------------------
def _mode_string(args, bus: str) -> str:
    """ログ用のモード表記。git バスなら `git:<repo>@<branch>`、ローカルなら `local:<bus>`。"""
    return f"git:{args.git}@{args.git_branch}" if args.git else f"local:{bus}"


def _child_base(args, bus_abs: str) -> list:
    """子プロセス（orchestrator/worker）へ引き継ぐ共通先頭 argv（バス・lease・設定・git・keep-clone）。
    グローバル引数のみ。run_id / repos / granularity 等はサブコマンド毎に呼び出し側で付け足す。"""
    base = [sys.executable, self_path(), "--bus", bus_abs, "--lease", str(args.lease)]
    cfg_path = getattr(args, "_config_path", None)
    if cfg_path:
        # 設定（executor プラグインの gitlab: ブロック等）を子へ伝搬。子は cwd が異なりうるので絶対パスで渡す。
        base += ["--config", os.path.abspath(cfg_path)]
    if args.git:
        base += ["--git", args.git, "--git-branch", args.git_branch, "--git-subdir", args.git_subdir or ""]
    if not getattr(args, "cleanup_clone", True):
        base += ["--keep-clone"]  # 親の指定を子（orchestrator/worker）へ引き継ぐ
    if getattr(args, "cleanup_per_node", False):
        base += ["--cleanup-per-node"]  # ノード単位の即時削除も子へ引き継ぐ
    ac = getattr(args, "agent_cli", None)
    if ac:
        base += ["--agent-cli", str(ac)]  # LLM 実行 CLI（kiro/claude）を子へ引き継ぐ
    return base


def _acquire_daemon_lock(args):
    """daemon singleton ロックを取得して pid を記録し、lock_file を返す。既に保持中なら None。
    pid は flock の有無に関わらず記録する（flock 非対応環境でも pid 生存で発見できるように）。"""
    lock_path = _daemon_lock_path(args)
    # 既存ホルダの pid を消さないよう truncate せず開く（flock 取得後にだけ書く）
    lock_file = os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644), "r+")
    if fcntl is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return None
    else:
        # flock 非対応（Windows 等）: README の「PID 生存で singleton」契約を実装する。
        # 既存ロックに生きた別 pid があれば取得を拒否（死んでいれば書き換えて引き継ぐ）。
        try:
            lock_file.seek(0)
            raw = (lock_file.read() or "").strip()
            if raw:
                old = int(raw)
                if old != os.getpid() and _pid_alive(old):
                    lock_file.close()
                    return None
        except (ValueError, OSError):
            pass
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _release_daemon_lock(lock_file) -> None:
    """daemon singleton ロックを解放して fd を閉じる（自己更新の再起動前に呼ぶ）。
    flock は fd に紐づくため、execv で再起動する前に解放しないと再取得で多重起動扱いになる。"""
    if lock_file is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        lock_file.close()
    except OSError:
        pass


def _run_lease_window(args) -> float:
    """run 生存リース（heartbeat）の猶予秒。健康な daemon は poll 毎に更新するので、
    poll の十数倍を確保すれば一過性の遅延（GC/ネットワーク）で誤回収しない。一方 act_timeout
    （消費者側の上限・既定 1800s）より十分短くして、owner 消失後すばやく孤児回収できるようにする。"""
    return max(float(getattr(args, "poll", 2.0) or 2.0) * 10.0, 120.0)


def _resume_run(bus: Bus, daemon_id: str, args, base: list, req_id: str, req: dict,
                lease_window: float, spawn=None):
    """孤児 run の orchestrator を同じ run-id で再起動する（cmd_orchestrate の resume）。
    確定済みの results/ はバスに残っているため、未完了ノードだけが続きから実行される。
    進捗なしの連続再開が max_resumes を超えたら None を返す（呼び出し側が failed に確定）。"""
    n = bus.record_resume(req_id)
    max_r = int(getattr(args, "max_resumes", 3) or 0)
    if n > max_r:
        return None
    p = (spawn or _spawn_orchestrator)(base, args, req_id, req)
    bus.touch_run(req_id, lease_window)   # 引き継ぎ直後に生存リースを張る（孤児の再判定を防ぐ）
    bus.run_view(req_id).event(daemon_id, "run-resumed", run=req_id, resume=n)
    bus.sync_push(f"run {req_id} resumed（孤児を引き継ぎ #{n}）")
    return p


def _superseded_run_ids(bus: Bus) -> dict:
    """inbox 要求の inherit_from から「新世代のリトライに引き継がれた先行 run」の
    {先行 run_id: 新世代 req_id} を作る。kiro-project はリトライ時に先行 run を明示 cancel せず、
    inherit_from 付きで次世代を投入する（inherit_from は実行中の先行 run を安全のため殺さない）。
    そのため旧世代の run が非終端のまま inbox に残る。この集合の run は世代交代で役目を終えた旧
    リトライ＝daemon 再起動時の一斉 adopt で復活させてはいけない。"""
    superseded: dict = {}
    for req_id in bus.list_inbox():
        rec = bus.read_inbox(req_id)
        prev = rec.get("inherit_from") if rec else None
        if prev and prev != req_id:
            superseded[prev] = req_id
    return superseded


def _run_fully_parked(bus: Bus, run_id: str) -> bool:
    """run の in-flight が全て park（承認待ち等）か。claim 中のノードも今すぐ claim 可能な
    pending も無く、生存 park が 1 つ以上ある run は worker も計画エージェントも使わない＝
    実行枠（max_runs）に数えない（gitlab 長期委譲が枠を占有して新規 run が詰まらないように）。"""
    v = bus.run_view(run_id)
    graph = v.read_graph()
    if not graph:
        return False                     # グラフ未作成（計画中）は実行中扱い
    parked = False
    for nid, node in graph["nodes"].items():
        st = v.node_state(nid)
        if st == "claimed" or (st == "pending" and deps_satisfied(v, node)):
            return False
        if st == "waiting":
            parked = True
    return parked


def _busy_run_count(bus: Bus, run_ids) -> int:
    """実行枠（max_runs）を消費している run 数（駆動中のうち全 park の run を除く）。"""
    return sum(1 for r in run_ids if not _run_fully_parked(bus, r))


def _adopt_orphan_runs(bus: Bus, daemon_id: str, owned: set, lease_window: float,
                       args, base: list, spawn=None,
                       slots: "int | None" = None) -> "tuple[dict, list]":
    """inbox 由来で owning daemon が消失した（生存リース切れ）非終端 run を引き継ぐ。

    PC の毎日シャットダウン等で daemon ごと消えても run を失敗にしない中核。孤児を
    見つけたら reclaim（1 台に決める）→ orchestrator を同じ run-id で再起動（resume）し、
    途中まで確定した results/ を活かして続きから回す。再開できないもの——自動再開が
    無効（max_resumes<=0）・要求ファイル欠損・進捗なしの連続再開が上限超過——だけを
    従来どおり failed に確定し、result を待つ消費者（kiro-project の submit 等）の
    永久待機を防ぐ。`owned` は自分が今回している run（誤引き継ぎしない）。

    ただし新世代のリトライに inherit_from で引き継がれた先行 run（世代交代で消えるべき旧
    リトライ）は再開しない。素朴に全孤児を再開すると再起動時に旧世代が一斉に復活して二重実行
    になるため、これらは終端化して next-gen の inherit_from が確定済みノードを引き継いでから
    掃除できるようにする（作業は失わない）。
    戻り値は（再開した run_id→Popen, 終端化した run_id 一覧）。"""
    adopted: dict = {}
    failed: "list[str]" = []
    used = 0                     # 実行枠（slots）を消費した引き継ぎ数（全 park の run は数えない）
    max_r = int(getattr(args, "max_resumes", 3) or 0)
    superseded = _superseded_run_ids(bus)
    for req_id in bus.list_inbox():
        if req_id in owned or not bus.run_exists(req_id):
            continue
        if not bus.run_is_orphaned(req_id, lease_window):
            continue
        if req_id in superseded:
            # 新世代のリトライに引き継がれた旧 run。孤児化しているが再開すると世代交代で消える
            # べき旧リトライが復活して二重実行になる。再開せず終端化する（next-gen の
            # inherit_from が確定済みノードを引き継いでから掃除する＝作業は失わない）。
            if bus.mark_run_superseded(req_id, superseded[req_id]):
                bus.run_view(req_id).event(daemon_id, "run-superseded", run=req_id,
                                           by=superseded[req_id])
                bus.sync_push(f"run {req_id} superseded（新世代 {superseded[req_id]} に引き継ぎ）")
                failed.append(req_id)
                log(daemon_id, f"孤児 run を終端化: {req_id} → superseded"
                               f"（新世代 {superseded[req_id]} に引き継ぎ・再開しない）")
            continue
        req = bus.read_inbox(req_id)
        why = "自動再開が無効（max_resumes<=0）" if max_r <= 0 else "要求ファイルを読めない"
        if req and max_r > 0:
            # 実行枠（max_runs 由来の slots）: 全 park の run は枠を要さないため無条件に引き継ぐ
            # （service_waits の監視オーナーが必要）。それ以外は枠が無ければ今回は再開せず
            # 次 poll へ持ち越す（failed にはしない＝再起動直後の一斉再開でプロセスが溢れない）。
            parked = slots is not None and _run_fully_parked(bus, req_id)
            if slots is not None and not parked and used >= slots:
                continue
            if not bus.reclaim_request(req_id, daemon_id, args.lease):
                continue      # 旧 owner の claim がまだ lease 内 → 失効後の poll で再試行
            p = _resume_run(bus, daemon_id, args, base, req_id, req, lease_window, spawn)
            if p is not None:
                adopted[req_id] = p
                if slots is not None and not parked:
                    used += 1
                continue
            why = f"進捗なしの連続再開が上限超過（max_resumes={max_r}）"
        if bus.mark_run_failed(req_id, f"orphaned: owning daemon が消失（生存リース切れ・{why}）"):
            bus.run_view(req_id).event(daemon_id, "run-orphaned", run=req_id)
            bus.sync_push(f"run {req_id} failed: orphaned（生存リース切れ・{why}）")
            failed.append(req_id)
    return adopted, failed


def _spawn_orchestrator(base: list, args, req_id: str, req: dict):
    """要求 req を担当する orchestrator を base argv から起動する（daemon のオンデマンド起動）。"""
    ws = req.get("workspace")   # 要求に紐づく唯一の書込先ワークスペースを run meta へ載せる
    ws_args = ["--workspace", json.dumps(ws, ensure_ascii=False)] if ws else []
    for r in (req.get("references") or []):   # 参照リポジトリも run meta へ伝搬する
        ws_args += ["--reference", json.dumps(r, ensure_ascii=False)]
    inh = req.get("inherit_from")             # リトライ: 先行 run の引き継ぎ元を orchestrate へ
    return subprocess.Popen(base + ws_args + [
        "--granularity", str(getattr(args, "granularity", "finest") or "finest"),
        *(["--exemplar-first"] if getattr(args, "exemplar_first", False) else []),
        "--run-id", req_id, "orchestrate", "--request", req["request"],
        # --inherit-from は orchestrate サブコマンドの引数（グローバルではない）。
        # サブコマンド名より前に置くと親 parser に拾われ usage エラーで即死するため、
        # 必ず "orchestrate" の後ろに付ける（cmd_run の起動と同じ並び）。
        *(["--inherit-from", inh] if inh else []),
        "--planner", args.planner, "--executor", args.executor,
        "--max-iterations", str(args.max_iterations),
        "--max-fanout", str(args.max_fanout),
        "--max-retries", str(args.max_retries),
        "--model_opt", args.model or "", "--poll", str(args.poll),
        "--node-id", f"orchestrator-{req_id}",
    ])


def _spawn_worker(base: list, args, rid: str, wid: str):
    """run rid のワーカーを1つ base argv から起動する（idle-exit のオンデマンド worker）。
    親（daemon）で解決した executor プラグイン設定（例 gitlab: の repo_url/conn_label）を
    `KIRO_FLOW_EXECUTOR_CONFIG` として worker の環境に明示的に渡す。worker が `--config` を
    再解決できない/別の設定を拾う場合でも、親の設定が確実に届くようにする。"""
    env = os.environ.copy()
    cfgjson = resolve_executor_config_json(args)
    if cfgjson is not None:
        env["KIRO_FLOW_EXECUTOR_CONFIG"] = cfgjson
    # park & poll: daemon は service_waits で park を面倒見るので worker の deferral を有効化する
    # （承認待ちで worker スロットをブロックさせず、承認待ちは waits/ へ退避させる）。
    # 設定 defer_waits=false のときは有効化せず、従来モード（worker がブロック待機）に戻す。
    if _defer_enabled(args):
        env["KIRO_FLOW_DEFER_WAITS"] = "1"
    return subprocess.Popen(base + [
        "--run-id", rid, "work", "--node-id", wid,
        "--executor", args.executor, "--model_opt", args.model or "",
        "--poll", str(args.poll), "--idle-exit",
    ], env=env)


def cmd_run(args) -> int:
    probe = make_bus(args, "run")
    probe.sync_pull()
    resuming = bool(args.run_id) and probe.run_exists(args.run_id)
    if resuming:
        meta = probe.run_meta(args.run_id)
        args.request = meta.get("request", "")
        status = meta.get("status")
        # 停滞した run（orchestrator が消えて非終端のまま止まったもの）も、失敗 run と同じく
        # 「失敗ノードを戻して続きから」やり直す。
        # status だけを見ると救えない: orchestrator が落ちる（停止・クラッシュ・マシン再起動）と
        # run は status=running のままリースだけが切れて残り、失敗ノードも pending ノードも誰も
        # 進めない。再開しても failed の results が終端として残るので、その工程は永久に再実行
        # されない。生存リースで実態を見て、止まっているなら失敗ノードを pending へ戻す。
        stalled = probe.run_is_orphaned(args.run_id,
                                        float(getattr(args, "orphan_grace", 0.0) or 0.0))
        if status == "failed" or stalled:
            reset = probe.retry_failed()
            why = "失敗" if status == "failed" else "停滞（orchestrator 消失）"
            probe.sync_push(f"retry {'failed' if status == 'failed' else 'stalled'} run "
                            f"{args.run_id}: reset {len(reset)} failed node(s)")
            print(f">>> {why} run {args.run_id} を再実行します"
                  f"（失敗ノード {len(reset)} 件を pending へ戻し、done は温存）", flush=True)
        else:
            print(f">>> 既存 run {args.run_id} を再開します（status={status}）", flush=True)
    else:
        if not args.request:
            print("エラー: 新規実行には <要求> が必要です（再開なら既存の --run-id を指定）",
                  file=sys.stderr)
            return 2
        args.run_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    run_id = args.run_id

    bus_root = os.path.abspath(args.bus)
    # グローバル引数（バス・転送・run_id・ワークスペース・分解粒度）を子プロセスへ引き継ぐ
    base = _child_base(args, bus_root) + ["--run-id", run_id]
    if getattr(args, "workspace", None):
        base += ["--workspace", args.workspace]   # 唯一の書込先を orchestrator/worker へ伝搬
    for r in (getattr(args, "references", None) or []):
        base += ["--reference", r]                # 参照リポジトリを orchestrator/worker へ伝搬
    base += ["--granularity", str(getattr(args, "granularity", "finest") or "finest")]  # 分解粒度
    if getattr(args, "exemplar_first", False):
        base += ["--exemplar-first"]   # 見本先行分解を orchestrator へ伝搬
    mode = _mode_string(args, bus_root)

    procs = []
    orch = subprocess.Popen(base + [
        "orchestrate", "--request", args.request,
        *(["--inherit-from", args.inherit_from] if getattr(args, "inherit_from", None)
          and not resuming else []),   # 新規時のみ: 先行 run から引き継ぐ（再開時は不要）
        "--planner", args.planner, "--executor", args.executor,
        "--max-iterations", str(args.max_iterations),
        "--max-fanout", str(args.max_fanout),
        "--max-retries", str(args.max_retries),
        *(["--review"] if args.review is True
          else ["--no-review"] if args.review is False else []),
        "--model_opt", args.model or "",
        "--poll", str(args.poll), "--node-id", "orchestrator",
    ])
    procs.append(("orchestrator", orch))

    # park & poll: cmd_run も監視ループで service_waits を回すので worker の deferral を有効化する。
    # 設定 defer_waits=false のときは有効化せず従来モード（worker がブロック待機）に戻す。
    worker_env = os.environ.copy()
    if _defer_enabled(args):
        worker_env["KIRO_FLOW_DEFER_WAITS"] = "1"
    else:
        worker_env.pop("KIRO_FLOW_DEFER_WAITS", None)
    for i in range(args.workers):
        wid = f"worker-{i+1}"
        w = subprocess.Popen(base + [
            "work", "--node-id", wid, "--executor", args.executor,
            "--model_opt", args.model or "", "--poll", str(args.poll),
        ], env=worker_env)
        procs.append((wid, w))

    print(f"\n>>> kiro-flow run: run_id={run_id} bus={mode} ({'resume' if resuming else 'new'})")
    print(f">>> {state_git_status_line(args)}", flush=True)
    print(f">>> orchestrator x1 + worker x{args.workers} を起動しました。Ctrl-C で全停止。\n", flush=True)

    bus = make_bus(args, "run")

    def shutdown(*_):
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(130)))

    # park & poll: この run の park 済みノードを監視ループで面倒見る（daemon と同じ service_waits）。
    # deferring executor（gitlab 等）でなければ no-op。watch_interval 毎に間引いて再確認する。
    next_wait_service = 0.0
    watch_interval = _watch_interval(_executor_cfg(args))
    # run が終端に達するか orchestrator が落ちるまで待機
    try:
        while True:
            bus.sync_pull()
            state_sync(args)   # 状態 git: 進捗をリモートの viewer へ共有（間隔律速・ローカルバス時のみ）
            if time.time() >= next_wait_service:
                try:
                    service_waits(bus, args, only_runs=[run_id], daemon_id="run")
                except Exception as e:  # noqa: BLE001 — 監視失敗は run を止めない
                    print(f">>> service_waits でエラー（無視して継続）: {e}", flush=True)
                next_wait_service = time.time() + watch_interval
            if bus.is_canceled_requested(run_id) and bus.get_status() not in TERMINAL:
                # cancel 指示: この run を canceled に終端化し、park の再ポーリングを止め、
                # 子（orchestrator/worker）を停止する。--close-issues は cmd_cancel 側で実施済み。
                bus.mark_canceled(run_id, bus.cancel_info(run_id).get("reason") or "cancel 指示")
                bus.clear_waits_for_run(run_id)
                bus.sync_push(f"cancel run {run_id}")
                print(f"\n>>> run {run_id} は cancel されました。停止します。", flush=True)
                break
            if bus.get_status() in TERMINAL:
                print(f"\n>>> run {bus.get_status()}。ワーカーを停止します。", flush=True)
                break
            if orch.poll() is not None and bus.get_status() not in TERMINAL:
                print("\n>>> orchestrator が終了しました。停止します。", flush=True)
                break
            time.sleep(max(args.poll, 1))
    finally:
        shutdown()

    bus.sync_pull()
    state_sync(args, force=True)   # 状態 git: run の結末（results/final/meta）を間隔を待たず共有側へ
    final = read_json(bus.final_path)
    if final:
        print("\n=== 最終結果 ===")
        print(final.get("summary", ""))
    # run が failed で終端したら非 0 を返す（委譲先の却下など、上位＝kiro-project が
    # act 失敗として検知しリトライできるようにする）。done は 0。
    return 1 if bus.get_status() == "failed" else 0
