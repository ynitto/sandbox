from __future__ import annotations
# daemon.py — 元 agent-flow.py の 5174-5422 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# daemon — 常駐し、要求に応じて orchestrator/worker をオンデマンド起動
# --------------------------------------------------------------------------
def daemon_lock_dir(lock_dir: "str | None" = None) -> str:
    """daemon ロックを置く共有ディレクトリ。
    起動側とプローブ側（agent-project 等）で必ず一致させる必要があるため、
    設定ファイルの `lock_dir`（CLI `--lock-dir`）で明示でき、既定は tempdir 配下。
    TMPDIR 差で別ディレクトリを見て「外部 daemon を発見できない」事故を防ぐ。"""
    d = lock_dir or os.path.join(tempfile.gettempdir(), "agent-flow-locks")
    os.makedirs(d, exist_ok=True)
    return d


def daemon_lock_key(args) -> str:
    """バスを正規化した singleton キー。symlink/相対パス/別 cwd で起動された
    外部 daemon でも同じ論理バスなら同一キーになるよう realpath で canonical 化する。"""
    if getattr(args, "git", None):
        return f"git::{args.git}@{args.git_branch}/{args.git_subdir or ''}"
    return "local::" + os.path.realpath(args.bus)


def _daemon_lock_path(args) -> str:
    """バス単位のデーモン singleton 用ロックパス（バス外の一時領域）。"""
    h = hashlib.sha1(daemon_lock_key(args).encode()).hexdigest()
    return os.path.join(daemon_lock_dir(getattr(args, "lock_dir", None)), f"daemon-{h}.lock")


def cmd_daemon(args) -> int:
    # 冪等化: 同一バスのデーモンが既に稼働していれば何もしない（多重起動しない）
    lock_file = _acquire_daemon_lock(args)
    if lock_file is None:
        print(f">>> agent-flow daemon は既に稼働中です（{_mode_string(args, os.path.realpath(args.bus))}）。"
              "起動をスキップします。", flush=True)
        return 0

    daemon_id = args.node_id or f"{socket.gethostname()}-{os.getpid()}"
    bus = make_bus(args, f"daemon-{_safe(daemon_id)}")
    base = _child_base(args, os.path.abspath(args.bus))
    mode = _mode_string(args, os.path.abspath(args.bus))

    orchestrators = {}   # run_id -> Popen
    workers = []         # list of (run_id, Popen)
    wcounter = 0
    stop = {"v": False}

    def shutdown(*_):
        stop["v"] = True
        for _, p in list(orchestrators.items()) + workers:
            if p.poll() is None:
                p.terminate()
    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(130)))
    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(143)))

    max_runs = int(getattr(args, "max_runs", 0) or 0)
    log(daemon_id, f"daemon 起動 bus={mode} max_workers={args.max_workers} "
                   f"max_runs={max_runs if max_runs > 0 else '無制限'} poll={args.poll}")
    log(daemon_id, state_git_status_line(args))   # バスがリモートへ鏡写しされるかを起動時に明示
    # 起動直後に一度だけ書いておく（ここでは push しない＝新規 push トリガーは増やさない）。
    # state_git 有効時は既存の毎 tick state_sync(args) が自分の interval で自然に拾って
    # 押し出すため、完全アイドルのままでも state_git_interval 以内に生存が可視化される。
    write_daemon_status(args, bus, daemon_id, orchestrators, workers)
    cleanup_interval = float(args.cleanup_interval)
    # 起動直後に 1 回掃除しないよう、最初の判定は interval 後になるよう初期化
    last_cleanup = time.time()
    # 自己更新（既定 on）: 起動直後の最初のアイドルでも実施するため last=0 で初期化し、cwd を保持
    start_cwd = os.getcwd()
    update_state = {"last": 0.0}
    # 自分が回している run の生存リース（heartbeat）。ローカル meta は毎 poll 更新（安価）、
    # git バスへの push は lease_window/3 毎に間引く（毎 poll の push を避ける）。
    lease_window = _run_lease_window(args)
    next_heartbeat_push = 0.0
    # park & poll: 全 active run の park 済みノードをバッチ再確認する（承認待ちを worker から
    # 切り離す監視主体）。watch_interval 毎に間引く。deferring executor でなければ no-op。
    watch_interval = _watch_interval(_executor_cfg(args))
    next_wait_service = 0.0

    while not stop["v"]:
        bus.sync_pull()
        state_sync(args)   # 状態 git: バス状態の共有と inbox 投入の取り込み（間隔律速・ローカルバス時のみ）
        maybe_heartbeat_daemon_status(args, bus, daemon_id, orchestrators, workers)  # --status-interval のときだけ
        # cancel 指示の受理: マーカーのある run を canceled に終端化し、その run の
        # orchestrator/worker を止め、park の再ポーリングを止める（--close-issues ならイシューも
        # 後始末）。これで承認待ちで park 中の run も、暴走中の run も、run スコープで恒久停止できる。
        for rid in bus.list_cancels():
            meta = bus.run_meta(rid)
            info = bus.cancel_info(rid)
            reason = info.get("reason") or "cancel 指示"
            # run 化前: マーカーを残し、下の inbox ループの cancel_request_run に任せる。
            # ここで clear すると受理前にマーカーが消え、要求がそのまま起動してしまう。
            if not bus.run_exists(rid):
                continue
            # 既に終端でも waits 掃除は行う（orchestrator が先に mark_canceled した場合、
            # ここをスキップすると park が残り service_waits が動き続ける）。
            if meta.get("status") in TERMINAL:
                # close_issues 要求があるのに orch が先に終端＋waits を消す前にここに来た場合、
                # waits が残っていれば先に on_cancel してから掃除する。
                if info.get("close_issues"):
                    _apply_on_cancel(bus, args, rid)
                cleared = bus.clear_waits_for_run(rid)
                bus.clear_cancel(rid)  # 適用済みマーカーを残さない（再 cancel / 無限 poll 防止）
                if cleared or info.get("close_issues"):
                    bus.sync_push(f"cancel cleanup waits {rid}")
                continue
            if info.get("close_issues"):
                _apply_on_cancel(bus, args, rid)      # waits を消す前にイシューを後始末
            bus.clear_waits_for_run(rid)
            # この daemon が駆動中の子を止める（run スコープ）
            if rid in orchestrators and orchestrators[rid].poll() is None:
                orchestrators[rid].terminate()
            for _, wp in [(r, p) for r, p in workers if r == rid]:
                if wp.poll() is None:
                    wp.terminate()
            marked = bus.mark_canceled(rid, reason)
            if marked:
                bus.clear_cancel(rid)
            bus.run_view(rid).event(daemon_id, "canceled", run=rid, reason=reason)
            bus.sync_push(f"cancel run {rid}: {reason}")
            if marked:
                log(daemon_id, f"cancel 受理: {rid} を canceled に終端化（{reason}）")
        # park & poll: 承認待ち等で park されたノードをまとめて再確認し、決着なら終端 result を書く。
        # 監視は**自分が駆動している run だけ**を対象にする（分散時に N 台が全 park を重複ポーリング
        # しないよう、1 run の監視は駆動オーナー 1 台に分担する）。オーナー消失時は孤児 reclaim が
        # run（＝監視）を別 PC へ移すので取りこぼさない。
        if time.time() >= next_wait_service:
            try:
                n = service_waits(bus, args, only_runs=list(orchestrators), daemon_id=daemon_id)
                if n:
                    write_daemon_status(args, bus, daemon_id, orchestrators, workers)
            except Exception as e:  # noqa: BLE001 — 監視失敗は daemon を止めない
                log(daemon_id, f"service_waits でエラー（無視して継続）: {e}")
            next_wait_service = time.time() + watch_interval
        # 一時ファイルの自動クリーンアップ（ロック / 中間 .tmp / 孤立クローン）を定期実行
        if cleanup_interval > 0 and time.time() - last_cleanup >= cleanup_interval:
            last_cleanup = time.time()
            try:
                c = run_cleanup(args, bus)
                if any(c.values()):
                    log(daemon_id, f"cleanup: locks={c['locks']} tmp={c['tmp']} "
                                   f"clones={c['clones']} work_repos={c['work_repos']} "
                                   f"cache={c.get('cache', 0)}")
            except Exception as e:  # noqa: BLE001 — 掃除失敗は daemon を止めない
                log(daemon_id, f"cleanup でエラー（無視して継続）: {e}")
        # 死んだ子を刈り取る。orchestrator が done を書く前に異常終了（クラッシュ / kill /
        # 起動失敗）した場合は run が終端に達さないまま放置され、result/status を待つ消費者
        # （agent-project の charter 駆動 watch など）が永久待機に陥る。終端でなければ
        # まず同じ run-id で再起動（resume。確定済み results/ を活かして続きから）を試み、
        # 進捗なしの連続再開が max_resumes を超えたときだけ failed に確定する。
        finished_runs = False   # このラウンドで終端に達した run（state git へ間隔を待たず押し出す）
        superseded_now = _superseded_run_ids(bus)
        for rid in [r for r, p in orchestrators.items() if p.poll() is not None]:
            rc = orchestrators[rid].poll()
            del orchestrators[rid]
            if bus.run_meta(rid).get("status") in TERMINAL:
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）")
                finished_runs = True
                continue
            if rid in superseded_now and bus.mark_run_superseded(rid, superseded_now[rid]):
                # 実行中に新世代のリトライへ引き継がれた旧 run が異常終了した。ここで再開すると
                # 世代交代で消えるべき旧リトライが復活して二重実行になるため、再開せず終端化する。
                bus.run_view(rid).event(daemon_id, "run-superseded", run=rid,
                                        by=superseded_now[rid])
                bus.sync_push(f"run {rid} superseded（新世代 {superseded_now[rid]} に引き継ぎ）")
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）→ superseded"
                               f"（新世代 {superseded_now[rid]} に引き継ぎ・再開しない）")
                finished_runs = True
                continue
            req = bus.read_inbox(rid)
            p = None
            if req and int(args.max_resumes or 0) > 0 and not stop["v"]:
                p = _resume_run(bus, daemon_id, args, base, rid, req, lease_window)
            if p is not None:
                orchestrators[rid] = p
                log(daemon_id, f"orchestrator 異常終了: {rid}（rc={rc}）→ 同じ run-id で再開"
                               f"（resume #{bus.run_meta(rid).get('resume_count', '?')}）")
            elif bus.fail_request(rid, f"orchestrator が終端化前に終了しました（rc={rc}）"):
                # fail_request は run 未作成（orchestrator が meta を一度も push できずに死んだ）
                # でも failed run を作って終端化する。ここで終端化しないと run_exists が偽の
                # ままになり、次 poll の受理ループが同じ要求を再 claim（commit/push）し続ける。
                bus.run_view(rid).event(daemon_id, "run-failed", run=rid, rc=rc)
                bus.sync_push(f"run {rid} failed: orchestrator 異常終了（rc={rc}）")
                log(daemon_id, f"orchestrator 異常終了: {rid}（rc={rc}）→ run を failed に確定")
                finished_runs = True
            else:
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）")
        workers = [(r, p) for r, p in workers if p.poll() is None]
        if finished_runs:
            write_daemon_status(args, bus, daemon_id, orchestrators, workers)  # 相乗り（追加 push 無し）
            state_sync(args, force=True)   # 状態 git: 終端した run の結果を間隔を待たず共有側へ

        # 自分が回している run の生存リースを更新（再起動後の自分・別デーモンへ「駆動中」を示す）。
        # ローカル meta は毎 poll 更新し、git バスへの伝搬は間引いて push する。
        for rid in orchestrators:
            bus.touch_run(rid, lease_window)
        if orchestrators and time.time() >= next_heartbeat_push:
            write_daemon_status(args, bus, daemon_id, orchestrators, workers)  # 相乗り（追加 push 無し）
            bus.sync_push("heartbeat: 駆動中の run の生存リースを更新")
            next_heartbeat_push = time.time() + lease_window / 3.0

        # 孤児 run の引き継ぎ: owning daemon が消失した非終端 run（PC シャットダウン・クラッシュ等）
        # を同じ run-id で再開する（続きから）。再開できないものだけ failed に確定する（再起動した
        # 新プロセスが status:running を放置せず、消費者が act_timeout まで待たずに復旧できるように）。
        if not stop["v"]:
            slots = None
            if max_runs > 0:   # 実行枠の残り（全 park の run は消費しない）。孤児の一斉再開を律速する
                slots = max(0, max_runs - _busy_run_count(bus, set(orchestrators)))
            adopted, orphan_failed = _adopt_orphan_runs(
                bus, daemon_id, set(orchestrators), lease_window, args, base, slots=slots)
            for rid, p in adopted.items():
                orchestrators[rid] = p
                log(daemon_id, f"孤児 run を引き継ぎ: {rid} → 再開"
                               f"（resume #{bus.run_meta(rid).get('resume_count', '?')}）")
            for rid in orphan_failed:
                log(daemon_id, f"孤児 run を回収: {rid} → failed（owning daemon 消失・再開不可）")
            # auto-heal（レイヤ4）: transient 起因の failed run を cooldown 後に自動再開
            # （done 温存・進捗リセット付き max_heals・superseded/canceled は尊重）。
            if slots is not None:
                slots = max(0, max_runs - _busy_run_count(bus, set(orchestrators)))
            for rid, p in _heal_failed_runs(bus, daemon_id, set(orchestrators),
                                            lease_window, args, base, slots=slots).items():
                orchestrators[rid] = p
                log(daemon_id, f"auto-heal: {rid} → 再開"
                               f"（heal #{bus.run_meta(rid).get('heal_count', '?')}）")

        # 1) 新しい要求を受理 → orchestrator をオンデマンド起動（分散時は 1 台だけ担当）。
        #    max_runs>0 なら「実行中（全 park を除く）の run 数」で受理を律速する。超過した要求は
        #    inbox に残り、枠が空いた poll で受理される（バックログ一括投入で orchestrator と
        #    計画エージェントがバックログ分同時に立ち上がるのを防ぐ）。cancel の受理は枠と無関係。
        busy = _busy_run_count(bus, set(orchestrators)) if max_runs > 0 else None
        for req_id in bus.list_inbox():
            if bus.run_exists(req_id) or req_id in orchestrators:
                continue
            if bus.is_canceled_requested(req_id):
                # run 化前に cancel された要求は起動せず canceled で終端化する（＝受理しない）。
                if bus.cancel_request_run(req_id, bus.cancel_info(req_id).get("reason") or ""):
                    bus.clear_cancel(req_id)
                    bus.sync_push(f"cancel request {req_id}（run 化前）")
                    log(daemon_id, f"cancel: 要求 {req_id} を run 化前に canceled で終端化")
                continue
            if busy is not None and busy >= max_runs:
                continue   # 受理枠なし → inbox に残す（取りこぼさない。枠が空いた poll で受理）
            req = bus.read_inbox(req_id)
            if not req:
                continue
            if bus.claim_request(req_id, daemon_id, args.lease):
                orchestrators[req_id] = _spawn_orchestrator(base, args, req_id, req)
                bus.touch_run(req_id, lease_window)   # 受理直後に生存リースを張る（孤児誤判定を防ぐ）
                if busy is not None:
                    busy += 1
                log(daemon_id, f"要求 {req_id} を受理 → orchestrator 起動: {req['request'][:50]}")

        # 2) claim 可能タスク量に応じてワーカーをオンデマンド起動
        claim_by_run = {r: bus.run_claimable_count(r) for r in bus.active_runs()}
        alive_by_run = {}
        for r, _ in workers:
            alive_by_run[r] = alive_by_run.get(r, 0) + 1
        for rid in sorted(claim_by_run, key=lambda x: -claim_by_run[x]):
            want = claim_by_run[rid]
            have = alive_by_run.get(rid, 0)
            while have < want and len(workers) < args.max_workers:
                wcounter += 1
                wid = f"{daemon_id}-w{wcounter}"
                workers.append((rid, _spawn_worker(base, args, rid, wid)))
                have += 1
                log(daemon_id, f"ワーカー起動: {wid} → run {rid}（claim可能={want}）")

        # 3) アイドル（要求も子も無い）なら自己更新を確認。更新を取り込めたら graceful 再起動。
        idle = not orchestrators and not workers and not bus.list_inbox()
        if maybe_self_update(args, idle, update_state):
            log(daemon_id, "自己更新を適用しました。子を停止し graceful 再起動します。")
            shutdown()                       # 残っている子があれば terminate（idle なので基本居ない）
            _release_daemon_lock(lock_file)  # flock を解放してから再取得できるようにする
            restart_self(start_cwd)          # 動いていた cwd のまま新しい本体へ（戻らない）

        time.sleep(args.poll)
    return 0
