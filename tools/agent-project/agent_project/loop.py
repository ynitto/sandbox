from __future__ import annotations
# loop.py — 元 agent-project.py の 6603-7068 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
def _bus_inside_state(cfg: "Config") -> bool:
    """バスがプロジェクト状態と同じ同期領域（root 配下）にあるか（既定 <root>/bus は True）。"""
    try:
        cfg.bus.resolve().relative_to(cfg.backlog.parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def project_flow_remote(cfg: "Config") -> "tuple[str, str, float] | None":
    """このプロジェクトの agent-flow に注入すべき state-git の (remote, branch, interval)。無ければ None。

    **バスが root 配下（既定 <root>/bus）にあるなら常に None。** そこは agent-project 自身の
    state 同期が bus ごと鏡写しする領域で、agent-flow に独自の state_git を持たせると同一ブランチ
    への第二の書き手になる。書き手が増えると除外規則の食い違いが「tracked だが commit されない
    ファイル」を生み、状態同期が復旧不能に詰まる（実際に起きた: agent-flow の管理クローンが
    bus/.state-git としてコミットされ、双方の rebase が永久に失敗した）。状態リポジトリへの
    書き手はプロジェクトにつき agent-project の 1 プロセスに限る。

    バスを root の外（同期されない場所）に置いた構成でだけ、従来どおり agent-flow 自身の
    state_git で鏡写しさせる。"""
    if _bus_inside_state(cfg):
        return None
    root = cfg.backlog.parent
    if _direct_state_git_ok(cfg):
        r = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        remote = r.stdout.strip() if r.returncode == 0 else ""
        if not remote:
            return None
        b = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        branch = b.stdout.strip() or "main"
        return remote, branch, cfg.state_git_interval
    if getattr(cfg, "state_git", None):
        return cfg.state_git, cfg.state_git_branch, cfg.state_git_interval
    return None


def flow_daemon_cmd(cfg: "Config", budget: int) -> "list[str]":
    """このプロジェクトの agent-flow daemon 起動コマンド。CLI で注入するのは agent-project の役割である
    per-project routing（どのバスをどのリポジトリへ鏡写しするか＝`--state-git` remote/branch/interval）
    と、バス・executor・予算・ロック置き場だけ。state_git サブディレクトリを含む agent-flow の設定値は
    個別注入せず flow_config（--config）に集約して agent-flow に読ませる（未指定なら agent-flow の既定
    ＝subdir は "agent-flow"）。これで agent-project 側に agent-flow 設定を増やさずに済む。"""
    base = resolve_agent_flow(cfg.agent_flow) + ["--bus", str(cfg.bus)]
    rf = project_flow_remote(cfg)
    if rf is not None:
        remote, branch, interval = rf
        base += ["--state-git", remote, "--state-git-branch", branch,
                 "--state-git-interval", str(interval)]
    fc = getattr(cfg, "flow_config", None)
    if fc:
        base += ["--config", os.path.abspath(os.path.expanduser(str(fc)))]
    if cfg.lock_dir:
        base += ["--lock-dir", str(cfg.lock_dir)]   # agent-flow と同じロック置き場（検知の一致）
    base += ["daemon", "--max-workers", str(max(1, int(budget))), "--executor", cfg.executor]
    return base


def ensure_flow_daemon(cfg: "Config", budget: int) -> bool:
    """このプロジェクトの agent-flow daemon を（無ければ）detached で起動する。起動したら True。
    manage_flow_daemon が off・per-project 対象でない・既に稼働中、のときは何もしない（冪等）。
    agent-project 停止後も残す（start_new_session）＝in-flight run を跨いで維持する。"""
    if not getattr(cfg, "manage_flow_daemon", False):
        return False
    # バスが root 配下なら agent-project の state 同期が鏡写しするので、daemon に state_git は
    # 要らない（注入しない）。root の外のバスは従来どおり注入先が無ければ対象外。
    if not _bus_inside_state(cfg) and project_flow_remote(cfg) is None:
        return False
    if daemon_running(cfg, use_git=False):      # 既にこのバスの daemon が稼働（ロック保持）→ 冪等スキップ
        return False
    cmd = flow_daemon_cmd(cfg, budget)
    try:
        cfg.bus.mkdir(parents=True, exist_ok=True)
        logp = cfg.backlog.parent / "flow-daemon.log"
        try:
            logf = open(logp, "a", encoding="utf-8")
        except OSError:
            logf = subprocess.DEVNULL
        try:
            subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True,
                             cwd=str(cfg.workdir))
        finally:
            if hasattr(logf, "close"):
                logf.close()
        append_journal(cfg.journal,
                       f"agent-flow daemon 起動: bus={cfg.bus} max_workers={max(1, int(budget))}")
        return True
    except OSError as e:
        append_journal(cfg.journal, f"agent-flow daemon 起動失敗（続行）: {e}")
        return False


def status_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "status.json"


def pause_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "paused.json"


def is_paused(cfg: "Config") -> bool:
    return pause_path(cfg).exists()


class _StopRequested(Exception):
    """commands/ の {"command": "stop"} による graceful 停止の内部シグナル。
    KeyboardInterrupt と同じ finally 経路（レジストリ後始末）を通して 0 終了する。"""


def _status_fresh_after_sec(cfg: "Config") -> float:
    """リモート viewer が『稼働中』と信じてよい経過秒数の目安。state_git/status の同期間隔
    から書き手（自分の設定を知っている側）が計算し、viewer 側は単純比較だけで済むようにする。"""
    intervals = [i for i in (cfg.state_git_interval, cfg.status_interval) if i and i > 0]
    return max([2.0 * i for i in intervals] + [120.0])


def write_status(cfg: "Config") -> None:
    """status.json（生存信号）を書く。state_git 越しにリモートの agent-dashboard が
    『daemon が今も生きているか』を判定するための最小スナップショット（watch/level の
    現在値＋更新時刻のみ）。backlog/needs/decisions/run-log 等の実データはここで重複を
    持たない（既に state_git で同期されるため）。実パス完了時に呼べば、そのパスが触った
    他ファイルの変更と同じコミットに相乗りする＝これ単体で追加の push を生まない。"""
    rec = {
        "host": socket.gethostname(), "watch": cfg.watch, "level": cfg.level,
        "paused": is_paused(cfg),
        "updated_iso": _now_ts(), "fresh_after_sec": _status_fresh_after_sec(cfg),
        # Windows ビュアーが同一マシンの WSL 本体を「別マシン」と誤認しないための信号
        **detect_runtime(),
    }
    try:
        p = status_path(cfg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def maybe_heartbeat_status(cfg: "Config") -> None:
    """watch アイドル中の任意の生存信号更新（`--status-interval`。既定 0＝無効）。
    無効時は status.json に一切触れない＝state_git の commit-if-diff で追加コミットを
    作らない（idle の git 負荷は今日と同じゼロ）。有効時も前回書き込みから
    status_interval 秒経つまでは触らず、書き込み頻度を利用者の指定した間隔に抑える。"""
    if cfg.status_interval <= 0:
        return
    try:
        age = time.time() - status_path(cfg).stat().st_mtime
    except OSError:
        age = float("inf")     # 未作成 → 書く
    if age >= cfg.status_interval:
        write_status(cfg)


def state_sync(cfg: "Config", force: bool = False) -> None:
    """状態の git 同期（best-effort）。ネットワーク断・リポジトリ不通でもループは殺さず
    journal に残して続行する（done の確定や消化は state_git に一切依存しない）。"""
    sg = state_git_for(cfg)
    if sg is None:
        return
    try:
        imported, exported = sg.sync(force=force)
        # journal へ残すのは取り込み（リモートの指示の反映）だけ。export を記録すると
        # その行自体が同期対象（journal.md）の新しい差分になり、「export=1」の空同期と
        # コミットが恒久に続くフィードバックループになる（export の履歴は git 側が持つ）。
        if imported:
            append_journal(cfg.journal, f"state-git 同期: import={imported} export={exported}")
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"state-git 同期失敗（続行）: {e}")


def _mark_offloaded(cfg: "Config", task: "Task", location: str, run_id: str) -> None:
    """タスクを『非ブロッキング委譲・結果待ち』に退避する（run_loop が settle をスキップ）。"""
    task.status = "offloaded"
    task.set("flow_run", run_id)
    task.set("flow_loc", location)
    persist_task(cfg, task)


def _reap_offloaded(cfg: "Config", tasks: "list[Task]", policy: "Policy",
                    autonomy_cache: dict, reasons: dict, cycle0: int,
                    spawn_budget: int) -> dict:
    """offloaded タスク（非ブロッキング委譲・結果待ち）を1回ずつポーリングし、終端した run だけ
    settle する（未終端はそのまま次パスへ）。専用 daemon が run を保持するので、ここでは待たない。
    deltas（settled/archived/spawned/tokens/cost）を返す。"""
    settled = archived = spawned = tokens = 0
    cost = 0.0
    for task in [t for t in tasks if t.norm_status() == "offloaded"]:
        run_id = str(task.get("flow_run", "") or "")
        loc = str(task.get("flow_loc", "daemon") or "daemon")
        if not run_id:
            continue
        term, ok, msg = _flow_result_once(cfg, loc == "remote", run_id)
        if not term:
            continue                       # まだ実行中 → 次パスで再確認（ブロックしない）
        if not claim_task(cfg, task):      # 実行権を取ってから確定（他インスタンスと競合しない）
            continue
        # claim 後にディスク上で既に offloaded でなければ、他経路（revise/hold）が先に進めた。
        # ここで settle すると canceled を確定して revise 内容を踏み潰しうる。
        if task.norm_status() != "offloaded":
            release_claim(cfg, task)
            continue
        gb = git_change_baseline(cfg.workdir)   # 完了時点の基準（remote/daemon 委譲は local 差分なし）
        venv = {"KIRO_BASE_REV": gb[0]} if gb[0] else None
        # settle 前に last_run を残す（delivery / protect / resume）。flow_run を落とす前に移す。
        _pin_last_run(cfg, task, run_id)
        task.drop("flow_run", "flow_loc")
        task.status = "doing"
        persist_task(cfg, task)
        dtok, dusd = parse_cost(msg)
        tokens += dtok
        cost += dusd
        # 人が dashboard 等から run を中止したとき: verify=true でも done にしない。
        # retries を上げて次の run-id を変える（同一 id の canceled run を再開しようとして固まるのを防ぐ）。
        if not ok and msg.rstrip().endswith("canceled"):
            task.retries += 1
            task.status = "ready"
            persist_task(cfg, task)
            append_journal(cfg.journal,
                           f"cycle {cycle0 + settled + 1}: {task.id} offload run が canceled → "
                           f"ready（人が中止・retries={task.retries} で新 run）")
            release_claim(cfg, task)
            settled += 1
            continue
        # act/flow 失敗: verify=true で偽 done にしない（canceled 以外の not ok）
        if not ok:
            ev = delivery_evidence(cfg, msg, gb, loc,
                                   verify=task.verify, vmsg=str(msg or ""),
                                   ok=False, task=task)
            _settle_failure(cfg, task, str(msg or "daemon run failed")[:500],
                            cycle0 + settled + 1, ev, reasons, loc)
            release_claim(cfg, task)
            settled += 1
            continue
        res = _settle_task(cfg, task, loc, msg, cycle0 + settled + 1, dtok, dusd, gb, venv,
                           policy, autonomy_cache, reasons)
        archived += res["archived"]
        if res["followups"] and spawned < spawn_budget:
            new = spawn_followups(cfg, task, res["followups"], tasks, spawn_budget - spawned)
            spawned += len(new)
        release_claim(cfg, task)
        settled += 1
    return {"settled": settled, "archived": archived, "spawned": spawned,
            "tokens": tokens, "cost": cost}


def run_loop(cfg: Config, act=act_via_agent_flow, ranker=None, sleeper=time.sleep) -> dict:
    # 同期の前に作業ツリーをコミットしてクリーンにする。
    #
    # DirectStateGit は「人の作業を壊さない」ため **作業ツリーに触らない**（コミットは detached
    # worktree で組み立て、ブランチは update-ref の CAS で進める）。その配慮は正しいが、結果として
    # 作業ツリーの未コミット変更が残ったまま import（pull --rebase）へ進むため、
    # `cannot pull with rebase: You have unstaged changes` で **必ず失敗する**。
    # 取り込めなければ push も non-fast-forward で永久に通らず、リモートとの乖離が広がり続ける
    # （実際 viewer が同じ agent-state ブランチへ push した途端に詰まり、分散構成で状態が共有
    # されなくなった）。同期の直前にコミットしておけば rebase は素直に通る。
    # 人が本体側 <repo>/.agent-project を編集していたら、コミットする前に取り込む。人にとっての
    # 正本はそこ（リポジトリを開けばある）だが、実書き込み先は状態 worktree なので、取り込まないと
    # 編集は効かないまま鏡の同期で消える（実際 agent-flow.yaml の evaluator 切替が無視され続けた）。
    sync_mirror_edits(cfg)
    commit_state(cfg, force=True)
    state_sync(cfg)                    # 状態 git: リモートの指示（commands/inbox/needs 記入）を先に取り込む
    tasks, policy, reasons, ingested, inboxed, pre_blocked = _run_setup(cfg)
    append_journal(cfg.journal, f"=== agent-project 開始 tasks={len(tasks)} "
                                f"ingested={len(ingested)} planner={cfg.planner} "
                                f"executor={cfg.executor} dry_run={cfg.dry_run} ===")
    append_journal(cfg.journal, state_git_status_line(cfg))
    # 未 push のローカルコミットを起動時に必ず警告する。doctor は人が叩かないと動かないが、
    # これは黙って詰まる（worker と verify は origin から clone するので、ローカルにだけある
    # コミットは彼らからは存在しない）。原因に辿り着くのが難しい詰まり方なので、先に言う。
    _unpushed, _branch = unpushed_commits(cfg.state_top)
    if _unpushed:
        append_journal(cfg.journal,
                       f"警告: origin へ未 push のコミットが {_unpushed} 件ある（{_branch}）。"
                       f"worker と verify は origin から clone するため、これらの成果は彼らから "
                       f"見えない（ローカルでは通るのに verify が落ち続ける）。"
                       f"`git -C {cfg.state_top} push origin {_branch}` を検討すること")
    start = time.time()
    cycle = 0
    archived = 0
    spawned_total = 0
    tokens_used = 0
    cost_used = 0.0
    reason = REASON_DRAINED

    unavailable: set[str] = set()             # この run でクレームできなかった（他者処理中の）タスク
    plan: list[str] = []
    plan_seen: set[str] = set()               # 計画に載せた report タスク（重複追記の防止）
    autonomy_cache: dict = {}                  # track→自動昇格レコードの読みキャッシュ

    while True:                                # report タスクは actionable から除外し有限停止で収束
        budget_stop_reason = _budget_reason(cfg, cycle, start, tokens_used, cost_used)
        if budget_stop_reason:
            reason = budget_stop_reason
            break

        # 人の指示（commands/ ドロップ・needs 記入）はパス途中でも取り込む＝フィードバック即応。
        # この時点で act 中のタスクは無く（バッチは同期で settle 済み）、変更は都度 persist
        # されているため、ファイル（＝真実）から再読しても安全。バックログが長くても、
        # 人の revise（依存 after・優先度・内容の修正）が次のサイクルからすぐ効く。
        if cycle:
            state_sync(cfg)                    # リモートの指示も間隔律速の範囲で取り込む
            if _has_pending_input(cfg):
                ingest_commands(cfg)
                tasks = load_tasks(cfg.backlog)
                recover_revised(cfg, tasks)
                policy = load_policy(cfg.policy)
                ingested += ingest_feedback(cfg, tasks)

        # 非ブロッキング委譲（act_async）の回収: offloaded タスクの run を1回ずつポーリングし、
        # 終端したものだけ settle する（待たない）。専用 daemon が run を保持するので、gitlab の
        # 長期委譲でもループを塞がず、完了したものから順に消化できる。
        reaped = _reap_offloaded(cfg, tasks, policy, autonomy_cache, reasons, cycle,
                                 cfg.max_spawn - spawned_total)
        if reaped["settled"]:
            cycle += reaped["settled"]
            archived += reaped["archived"]
            spawned_total += reaped["spawned"]
            tokens_used += reaped["tokens"]
            cost_used += reaped["cost"]
            tasks = load_tasks(cfg.backlog)    # settle が状態を変えたので再読

        order_all = [t for t in prioritize(tasks, policy, cfg.planner, cfg.model, ranker)
                     if t.id not in unavailable]  # 他 worker/インスタンスがクレーム済みは除外
        levels = {t.id: resolve_level(t, cfg, autonomy_cache) for t in order_all}
        for t in order_all:                       # report タスクは実行せず「計画」に載せて保留（塩漬け）
            if levels[t.id] == "report" and t.id not in plan_seen:
                plan_seen.add(t.id)
                plan.append(t.id)
                append_journal(cfg.journal, f"report: {t.id} — {t.title}（level=report・実行せず保留）")
        order = [t for t in order_all if levels[t.id] != "report"]
        if not order:                             # 実行可能ゼロ＝消化完了（全 report ならグローバルに応じ report）
            reason = "report" if cfg.level == "report" else REASON_DRAINED
            break

        # 並列消費: 依存解決済み（=互いに独立）な先頭群を daemon/remote へ並行 submit。
        # verify 以降のローカル状態変更は逐次のまま（competition を避け不変条件を保つ）。
        batch = _select_batch(order, cfg, policy, cfg.max_cycles - cycle)
        git_base = git_change_baseline(cfg.workdir)   # act 前スナップショット（保護パス/進捗判定/成果参照）
        verify_env = {"KIRO_BASE_REV": git_base[0]} if git_base[0] else None  # verify に差分基準を渡す
        act_results = _act_batch(batch, cfg, act, policy)   # クレームできたものだけ実行
        if not act_results:                      # 全て他者がクレーム済み → 次パスへ（この run では触らない）
            unavailable.update(t.id for t in batch)
            continue

        stop = None
        for task in batch:
            if task.id not in act_results:        # クレームできなかった分はこの run では飛ばす
                unavailable.add(task.id)
                continue
            packed = act_results[task.id]
            location, pend, act_msg = packed[0], packed[1], packed[2]
            act_ok = packed[3] if len(packed) > 3 else True
            if pend is not None:                  # 非ブロッキング委譲（offload）: 待たず offloaded に退避
                _mark_offloaded(cfg, task, location, pend.run_id)
                release_claim(cfg, task)          # 実行権は解放（次パスでポーリングして終端したら settle）
                append_journal(cfg.journal, f"{task.id} を offload（run={pend.run_id}）→ 結果待ち")
                unavailable.add(task.id)          # この run ではもう触らない（再選択しない）
                continue
            cycle += 1
            cycle_start = time.time()
            dtok, dusd = parse_cost(act_msg)             # このサイクルのコストを計上（予算ゲート用）
            tokens_used += dtok
            cost_used += dusd
            if dtok or dusd:
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} cost tokens={dtok} usd={dusd:.4f}"
                                            f"（累計 tokens={tokens_used} usd={cost_used:.4f}）")
            # 人が run を中止したとき: verify=true でも done にしない（リトライ非消費で ready）。
            # retries は上げる＝次の run-id を変える。上げないと canceled な同一 id を作り直し、
            # agent-flow は終端 run を再開できず永久 no-op になる。
            # act 中の revise（軌道修正）は失敗/canceled より優先——結果を確定せず積み直す。
            if str(act_msg or "").rstrip().endswith("canceled") or act_ok is False:
                fresh = _load_task_file(cfg, task.id)
                if fresh is not None and fresh.get("revised"):
                    _requeue_revised(cfg, task, fresh, cycle)
                    release_claim(cfg, task)
                    continue
            if str(act_msg or "").rstrip().endswith("canceled"):
                task.retries += 1
                task.status = "ready"
                persist_task(cfg, task)
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} run が canceled → ready"
                               f"（人が中止・retries={task.retries} で新 run）")
                release_claim(cfg, task)
                continue
            # act 失敗（daemon failed 等）: verify=true の偽 done/review を防ぐ。失敗経路へ。
            if act_ok is False:
                ev = delivery_evidence(cfg, act_msg, git_base, location,
                                       verify=task.verify, vmsg=str(act_msg or ""),
                                       ok=False, task=task)
                _settle_failure(cfg, task, str(act_msg or "act failed")[:500], cycle, ev,
                                reasons, location)
                release_claim(cfg, task)
                continue
            res = _settle_task(cfg, task, location, act_msg, cycle, dtok, dusd, git_base,
                               verify_env, policy, autonomy_cache, reasons)
            archived += res["archived"]
            if res["followups"] and spawned_total < cfg.max_spawn:   # done から派生タスク（backlog 自走）
                new = spawn_followups(cfg, task, res["followups"], tasks, cfg.max_spawn - spawned_total)
                spawned_total += len(new)
                if new:
                    append_journal(cfg.journal,
                                   f"cycle {cycle}: {task.id} から派生生成 {[t.id for t in new]}")

            release_claim(cfg, task)          # doing でなくなったので実行権を解放
            if cfg.once:
                stop = "once"
                break
            delay = decide_pace(cfg, time.time() - cycle_start)
            if delay > 0:
                sleeper(delay)
        if stop:
            reason = stop
            break

    counts = summarize(tasks)
    newly_blocked = {t.id for t in tasks
                     if t.norm_status() in ("blocked", "review")} - pre_blocked
    budget_stop = reason in (REASON_BUDGET, REASON_COST)
    notified = notify(cfg, tasks, reasons, newly_blocked, budget_stop)
    promote_rules(cfg)                                     # 効いた学習を rules.md（常時注入層）へ昇格
    promoted = promote_learnings(cfg) if cfg.ltm else []   # 効いた学習を ltm-use へ昇格（横断・opt-in）
    _cleanup_bus(cfg)             # 不要な一時ファイル（agent-flow バスの run 状態）を掃除
    append_journal(cfg.journal, f"=== agent-project 停止 reason={reason} cycles={cycle} "
                                f"done={counts['done']} blocked={counts['blocked']} "
                                f"notified={notified} promoted={len(promoted)} ===")
    append_runlog(cfg.runlog, {                    # 構造化 run-log（機械可読・運用判断の土台）
        "ts": datetime.now().isoformat(timespec="seconds"), "reason": reason,
        "level": cfg.level, "cycles": cycle, "done": counts["done"],
        "blocked": counts["blocked"], "review": counts.get("review", 0),
        "archived": archived, "escalations": len(newly_blocked),
        "spawned": spawned_total, "inboxed": len(inboxed),
        "tokens": tokens_used, "cost": round(cost_used, 4),
        "duration_s": round(time.time() - start, 2)})
    write_status(cfg)             # 生存信号（このパスが触った他ファイルの変更と同じコミットに相乗り）
    state_sync(cfg, force=True)   # 状態 git: このパスの結果（done/needs/journal）を共有側へ押し出す
    return {"reason": reason, "cycles": cycle, "counts": counts, "tasks": tasks,
            "reasons": reasons, "newly_blocked": newly_blocked, "notified": notified,
            "ingested": ingested, "archived": archived, "promoted": promoted,
            "spawned": spawned_total, "tokens": tokens_used, "cost": cost_used,
            "inboxed": inboxed, "level": cfg.level, "plan": plan}


def _cleanup_bus(cfg: Config) -> None:
    """local run 後に不要となる agent-flow バスの一時状態を掃除する。
    daemon 稼働中や git バス（remote）は作業中のため触らない。また state_git でバスを
    リモート viewer へ鏡写ししている構成では、ここで runs/ を消すと『フロータブに見せたい
    run 状態』を破壊し、削除が次の同期でリモートへ伝播してしまうため触らない
    （agent-flow 側の state_git がバスの寿命を管理する＝gc に委ねる）。

    runs/<id>/ は viewer のフロータブが読む一次ソースなので、直近 bus_keep_runs 件は残す。
    かつては act のたびに runs/ を丸ごと消していたため、run は完了しているのに viewer が
    その最終状態（全ノード done）を観測する前にディレクトリごと消え、最後に撮れた
    スナップショット（最終ノードが実行中）のままフローが固まって見えていた。掃除は
    「古い run を捨てる」ためのものであって「いま終わった run を人の目から隠す」ためのものではない。"""
    if (not cfg.cleanup or cfg.git_bus or cfg.state_git
            or daemon_running(cfg, use_git=False)):
        return
    shutil.rmtree(cfg.bus / "inbox", ignore_errors=True)   # local run では使わない submit キュー
    runs = cfg.bus / "runs"
    if not runs.is_dir():
        return
    keep = max(0, int(cfg.bus_keep_runs))
    try:
        dirs = sorted((d for d in runs.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return
    for d in dirs[keep:]:                                  # 新しい順に keep 件を残して捨てる
        shutil.rmtree(d, ignore_errors=True)


def exit_code_for(result: dict) -> int:
    counts = result["counts"]
    if counts["blocked"] > 0 or counts.get("review", 0) > 0 \
            or counts.get("proposed", 0) > 0:   # 人の対応待ち（判断 / 検収承認 / 実行前レビュー）
        return 1
    if result["reason"] in (REASON_DRAINED, "report"):         # 正常停止（消化完了 or 計画報告）
        return 0
    return 2


# ---------------------------------------------------------------------------
# watch（終了条件後もプロセス常駐。エージェントは待機しない＝idle 中は起動しない）
# ---------------------------------------------------------------------------
def has_work(cfg: Config) -> bool:
    """次パスを起こすべき仕事があるか（新規/実行待ちタスク or フィードバック）。安価な FS 走査のみ。

    起床の条件は「そのパスで実際に処理できる仕事があるか」でなければならない。commands/ を
    ingest_commands と同じ述語（_read_command）で見るのはそのため: 取り込めない指示で起こすと、
    何も処理しないまま charter を再評価するパスが生まれ、承認済みマイルストーンが復活する。

    ready でも after 依存未達は消化できない。それを CONSUMABLE だけで起こすと、blocked/doing の
    後ろに dep-gated ready が並ぶだけで project_watch が空パスを無限に回す（実害: cycles が
    数千まで増え、journal が秒単位で埋まる）。dependents が ready でも ready_after_deps が
    空なら起こさない。"""
    tasks = load_tasks(cfg.backlog)
    if ready_after_deps(tasks):
        return True
    for t in tasks:
        st = t.norm_status()
        # offloaded は「機械が委譲実行中・結果待ち」＝次パスでポーリングして回収するため起こす。
        # inbox は triage 待ち。doing は「実行者が失踪した stale」だけ起こす（alive な doing を
        # 起こすとクレーム中タスクで毎 poll 空パスになる）。
        if st in ("inbox", "offloaded"):
            return True
        if st == "doing" and not _claim_alive(cfg, t.id):
            return True
    if cfg.inbox and cfg.inbox.exists() and any(cfg.inbox.glob("*")):
        return True               # 外部ドロップ(inbox/)が来たら起こす
    cdir = commands_dir(cfg)
    # ingest_commands と同じ条件（読めること）。読めない書きかけでは起こさない＝起きたパスは
    # 必ずその指示を処理できる（起床と取り込みの食い違いを作らない）。
    if cdir.exists() and any(_read_command(f)[0] is not None for f in cdir.glob("*.json")):
        return True               # 人の指示ドロップ(commands/)が来たら起こす
    if replan_request_path(cfg).exists():
        return True               # バックログ再分解の要求が来たら起こす（次パスで plan を強制）
    if cfg.needs.exists():
        for nf in cfg.needs.glob("*.md"):
            # ingest_feedback と同じ条件（確定 [x]・静穏化済み）。本文の有無だけで起こすと、
            # 書きかけのまま毎パス起床して何も取り込まない空振りを繰り返す。
            if feedback_submitted(nf) and settled(cfg, nf):
                return True
    return False


def _has_pending_input(cfg: Config) -> bool:
    """パス途中に取り込むべき人の入力があるか（commands/ ドロップ or needs の確定記入）。
    安価な FS 走査のみ（has_work の入力側サブセット。タスクの有無は見ない）。"""
    cdir = commands_dir(cfg)
    if cdir.exists() and any(cdir.glob("*.json")):
        return True
    if cfg.needs.exists():
        for nf in cfg.needs.glob("*.md"):
            try:
                if feedback_submitted(nf):
                    return True
            except OSError:
                continue
    return False


def run_watch(cfg: Config, act=act_via_agent_flow, ranker=None, sleeper=time.sleep,
              max_passes=None, heartbeat=None) -> dict:
    passes = 0
    last: dict = {}
    while True:
        if is_paused(cfg):           # pause 中はパスを起こさない（resume/stop の指示待ち）
            append_journal(cfg.journal, "=== watch: 一時停止中（resume/stop 待ち。エージェント非起動）===")
            write_status(cfg)        # paused をリモート viewer へ知らせる
        else:
            last = run_loop(cfg, act, ranker, sleeper)
            passes += 1
            if heartbeat:
                heartbeat()          # 各パスで生存信号を更新（共有レジストリ越しのリモート発見用）
            c = last["counts"]
            print(f"[watch] pass {passes}: reason={last['reason']} "
                  f"done={c['done']} blocked={c['blocked']}", flush=True)
            if last["reason"] == REASON_THROTTLE and cfg.level != "report":
                cfg.level = "report"  # ソフト予算超過 → 以降は report 降格（spend を止め監視は継続）
                print("[watch] throttle: ソフト予算超過につき report レベルへ降格（act 停止）", flush=True)
                append_journal(cfg.journal, "=== watch: throttle 降格（report・act 停止）===")
                write_status(cfg)    # 直近パスの生存信号は降格前の level だったため上書きしておく
            if max_passes is not None and passes >= max_passes:
                return last
            append_journal(cfg.journal, "=== watch: 監視中（新規タスク/フィードバック待ち。"
                                        "エージェントは待機しない）===")
        while is_paused(cfg) or not has_work(cfg):   # idle/pause: エージェント CLI/flow は一切起動しない
            sleeper(cfg.poll)
            if heartbeat:
                heartbeat()          # idle 中も heartbeat を保ち、リモートから生存が見えるようにする
            if not is_paused(cfg):
                run_intake(cfg)      # 外部ゲートからの汲み上げ（間隔律速。積まれれば has_work が起こす）
            maybe_heartbeat_status(cfg)  # --status-interval のときだけ idle 中も生存信号を更新（既定は無効＝無干渉）
            commit_state(cfg)        # 状態 worktree: 溜まった変更をまとめてコミット（間隔律速）
            state_sync(cfg)          # 状態 git: リモートの指示を取り込む（間隔律速。届けば has_work が起こす）
            if is_paused(cfg):
                ingest_commands(cfg)  # pause 中も resume/stop（と他の指示）は受け付ける
            if maybe_self_update(cfg):   # アイドル時のみ自己更新を確認・取り込み（取り込めたら再起動）
                raise _RestartRequested()


# ---------------------------------------------------------------------------
