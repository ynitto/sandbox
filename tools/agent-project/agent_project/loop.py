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



def status_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "status.json"


def status_dir(cfg: "Config") -> Path:
    """ノード毎の生存信号 status/<node>.json の置き場（複数 PC 分散運用）。"""
    return cfg.backlog.parent / "status"


def node_status_path(cfg: "Config") -> "Path | None":
    """このエンジンのノード別生存信号ファイル。node 未設定（無名エンジン）なら None。

    ファイル名のサニタイズは板側と同じ `normalize_node_id` を通す。ここに独自の規則を
    持っていたのが `status/DESKTOP-X.json` と `nodes/desktop-x.json` の 2 名義の原因だった
    （P0-3）。`Config.node` は既に正規形なので結果は同値だが、**規則を 2 つ持たない**。"""
    node = str(getattr(cfg, "node", "") or "").strip()
    if not node:
        return None
    return status_dir(cfg) / f"{normalize_node_id(node)}.json"


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
        # ノード名（複数 PC 分散運用）。無名エンジンは空（従来と同じ見え方）。
        "node": str(getattr(cfg, "node", "") or "").strip(),
        "availability": availability_state(cfg),
        "updated_iso": _now_ts(), "fresh_after_sec": _status_fresh_after_sec(cfg),
        # Windows ビュアーが同一マシンの WSL 本体を「別マシン」と誤認しないための信号
        **detect_runtime(),
    }
    body = json.dumps(rec, ensure_ascii=False, indent=2)
    try:
        p = status_path(cfg)                       # 従来の単一 status.json（後方互換）
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    except OSError:
        pass
    # ノード名があれば status/<node>.json にも書く。複数の名前付きエンジンが同じ状態リポジトリを
    # 共有しても、各ノードが別ファイルを持つので単一 status.json の上書き合戦にならない。
    # viewer はこのディレクトリを読んでノード一覧（生存・実行中）を出せる。
    np = node_status_path(cfg)
    if np is not None:
        try:
            np.parent.mkdir(parents=True, exist_ok=True)
            np.write_text(body, encoding="utf-8")
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


def settle_task(cfg: "Config", task: "Task", *args) -> dict:
    """versioned state を確定・同期してから、ホスト局所の claim を解放する。"""
    result = _settle_task(cfg, task, *args)
    state_sync(cfg, force=True)
    release_claim(cfg, task)
    return result


def _mark_offloaded(cfg: "Config", task: "Task", location: str, run_id: str) -> None:
    """タスクを『非ブロッキング委譲・結果待ち』に退避する（run_loop が settle をスキップ）。"""
    task.status = "offloaded"
    task.set("flow_run", run_id)
    task.set("flow_loc", location)
    persist_task(cfg, task)


# offloaded の結果取得（agent-flow result）が連続でエラーになったら人へ回す上限。
# watch の 1 パスごとに 1 カウントなので、既定 5 秒間隔なら約 1 分相当の猶予。
_OFFLOAD_POLL_ERR_LIMIT = 12


def _settle_verify_delegation(cfg: "Config", task: "Task", did: str, ok: bool, msg: str,
                              cycle: int, reasons: dict) -> int:
    """検証委譲（P4-b）の結果を受け取る。settle した件数（常に 1）を返す。

    受け取るのは板の result に載った receipt で、それを受理点
    （`verifications/<task-id>/<rev>.external.json`）へ置き、タスクを `ready` へ戻す
    ——次の巡回の settle が内蔵 verifier と同じ検算を通してから採用する
    （done の根拠を「誰が確かめたか」で分岐させない）。

    receipt が返らなかった場合は成功終端でも受理しない。「板の run が終わった」は
    確かめた証拠ではないからで、証跡の無い pass を通す唯一の穴がここだった。
    失敗・中止・請け負い手なしと同じく人へ回す。票には「板でも確かめられなかった」ことまで書く。"""
    rev = str(task.get("verify_rev") or "").strip()
    receipt = _board_result_receipt(cfg, did)
    task.drop("flow_run", "flow_loc", "verify_rev", "verify_plan_digest")
    if ok and rev and receipt is not None:
        rel = save_external_verdict(cfg, task, rev, {
            "receipt": receipt, "did": did, "by": _board_result_winner(cfg, did),
            "at": _now_ts(), "detail": msg[:300]})
        task.status = "ready"
        persist_task(cfg, task)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} 検証委譲の receipt を受理"
                                    f"（{did}・{rel or '記録の保存に失敗'}）→ 次の巡回で検算へ")
        return 1
    if ok and rev:
        msg = (f"{msg}（板の run は終端しましたが receipt が返っていません。"
               "確かめた証跡が無いので採用できません）")
    task.set("env_resume", "1")
    task.status = "blocked"
    why = ("[agent-error:env] 検証不能: このノードでは確かめられない基準があり、板へ検証を"
           f"回しましたが決着しませんでした（{did}: {msg[:200]}）。環境を直して approve すると、"
           "同じ run の続きから再開します。")
    _block(cfg, task, why, reasons)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（検証委譲も決着せず）")
    return 1


def _board_result_receipt(cfg: "Config", did: str) -> "dict | None":
    """板の result に載った検証 receipt（無ければ None）。採否の検算は settle 側が行う。"""
    try:
        res = BoardRepo(cfg.board, workdir=cfg.board_workdir).read_result(did) or {}
    except (OSError, RuntimeError, ValueError):
        return None
    rec = res.get("receipt")
    return rec if isinstance(rec, dict) else None


def _board_result_winner(cfg: "Config", did: str) -> str:
    """検証を請け負った端末の名義（読めなければ空）。受理の根拠として記録に残す。"""
    try:
        res = BoardRepo(cfg.board, workdir=cfg.board_workdir).read_result(did) or {}
    except (OSError, RuntimeError, ValueError):
        return ""
    return str(res.get("winner") or "")


def _settle_cancelled(cfg: "Config", task: "Task", cycle: int, reasons: dict) -> None:
    """人が run を中止したときの確定。中止の繰り返しを**有限回で人へ返す**（C7）。

    `retries` は「次の run-id を作るための世代番号」として上げる（同一 id の cancelled run は
    agent-flow が再開できず永久 no-op になる）。ただしこれは**タスクの内容の失敗ではない**ので、
    リトライ上限（`_settle_failure` の `retries > max_retries`）の判定材料には使えない——中止は
    その失敗経路を一度も通らないため、上限はどこにも効かず、人が中止するたびにループが新しい
    run を起こし続けた（＝止まらないループ）。

    そこで中止した回数を `cancel_count` に別に数え、`max_retries` を超えたら人の判断へ回す
    （`_env_block` が環境要因の反復を `env_resume_limit` で締めるのと同じ形）。approve で
    人が介入すればカウンタは戻る（`cmd_approve`）ので、上限は「人が一度も触らずに中止だけを
    繰り返せる回数」を縛る。"""
    count = int(task.get("cancel_count") or 0) + 1
    task.retries += 1
    task.set("cancel_count", str(count))
    if count > cfg.max_retries:
        _escalate(cfg, task,
                  f"人が run を {count} 回中止しました（上限 {cfg.max_retries}）。"
                  "そのまま積み直しても同じ工程を起こすだけなので、内容・進め方を見直して"
                  "から approve してください。リトライ回数は消費していません。",
                  reasons, cycle)
        if task.norm_status() == "blocked":
            append_journal(cfg.journal,
                           f"cycle {cycle}: {task.id} → 人の判断（中止の繰り返し {count} 回・"
                           f"上限 {cfg.max_retries}）")
        return
    task.status = "ready"
    persist_task(cfg, task)
    append_journal(cfg.journal,
                   f"cycle {cycle}: {task.id} run が cancelled → ready"
                   f"（人が中止・retries={task.retries} で新 run・中止 {count}/{cfg.max_retries}）")


def _reap_offloaded(cfg: "Config", tasks: "list[Task]", policy: "Policy",
                    autonomy_cache: dict, reasons: dict, cycle0: int,
                    spawn_budget: int) -> dict:
    """offloaded タスク（非ブロッキング委譲・結果待ち）を1回ずつポーリングし、終端した run だけ
    settle する（未終端はそのまま次パスへ）。請負側が run を保持するので、ここでは待たない。
    deltas（settled/archived/spawned/tokens/cost）を返す。"""
    settled = archived = spawned = tokens = 0
    cost = 0.0
    _board = None   # 遅延・使い回し（board-loc の offloaded がある回だけ 1 回だけ構築・pull する）
    collected: "list[str]" = []   # 回収し終えた公示（パス末尾でまとめて板から消す）
    for task in [t for t in tasks if t.norm_status() == "offloaded"]:
        run_id = str(task.get("flow_run", "") or "")
        loc = str(task.get("flow_loc", "local") or "local")
        if not run_id:
            continue
        if loc in ("board", VERIFY_DELEGATION_LOC):
            if _board is None:
                _board = BoardRepo(cfg.board, workdir=cfg.board_workdir)
                _board.sync_pull()
            term, ok, msg = _board_result_once(_board, run_id)
        else:
            # 旧 daemon/remote 経路で offloaded になったまま残っているタスクの回収路
            # （新規に board 以外の offloaded は作られない）。
            term, ok, msg = _flow_result_once(cfg, False, run_id)
        if not term:
            if msg.startswith("error:"):
                # 結果の取得自体が失敗（CLI 不在・バス破損・出力化け等）。これを「まだ実行中」と
                # 読み続けると offloaded が永久にスタックする。連続エラーを数え、上限で人へ回す。
                errs = int(task.get("flow_poll_err") or 0) + 1
                task.drop("flow_poll_err")
                if errs >= _OFFLOAD_POLL_ERR_LIMIT:
                    if not claim_task(cfg, task):
                        continue
                    task.drop("flow_run", "flow_loc")
                    task.status = "blocked"
                    persist_task(cfg, task)
                    write_needs_file(cfg, task,
                                     f"委譲 run {run_id} の結果を {errs} 回連続で取得できません"
                                     f"（{msg[:200]}）。agent-flow 環境（CLI・バス）を確認してください。")
                    append_journal(cfg.journal,
                                   f"cycle {cycle0 + settled + 1}: {task.id} offload 結果取得が"
                                   f"連続失敗 → blocked（{msg[:120]}）")
                    release_claim(cfg, task)
                    settled += 1
                else:
                    task.extra.append(("flow_poll_err", str(errs)))
                    persist_task(cfg, task)
            elif task.get("flow_poll_err"):
                task.drop("flow_poll_err")   # 取得が復調 → カウンタを戻す
                persist_task(cfg, task)
            continue                       # まだ実行中 → 次パスで再確認（ブロックしない）
        if task.get("flow_poll_err"):
            task.drop("flow_poll_err")
        if not claim_task(cfg, task):      # 実行権を取ってから確定（他インスタンスと競合しない）
            continue
        # claim 後にディスク上で既に offloaded でなければ、他経路（revise/hold）が先に進めた。
        # ここで settle すると cancelled を確定して revise 内容を踏み潰しうる。
        if task.norm_status() != "offloaded":
            release_claim(cfg, task)
            continue
        if loc == VERIFY_DELEGATION_LOC:
            # 検証委譲（P4-b）の回収。返ってきたのは**検証の判定**であって成果ではない。
            settled += _settle_verify_delegation(cfg, task, run_id, ok, msg,
                                                 cycle0 + settled + 1, reasons)
            collected.append(run_id)
            release_claim(cfg, task)
            continue
        gb = git_change_baseline(cfg.workdir)   # 完了時点の基準（remote/daemon 委譲は local 差分なし）
        venv = {"AGENT_BASE_REV": gb[0], "KIRO_BASE_REV": gb[0]} if gb[0] else None
        # settle 前に last_run を残す（delivery / protect / resume）。flow_run を落とす前に移す。
        _pin_last_run(cfg, task, run_id)
        task.drop("flow_run", "flow_loc")
        task.status = "doing"
        persist_task(cfg, task)
        if loc == "board":
            # 終端を読み終えた公示は板から消す（設計 §4.2 gc tick「終端した公示」の実体）。
            # **消してよいと知っているのは依頼側だけ**なので、時間ベースの一括掃除にはしない
            # ——その期間オフラインだった依頼側が結果を読む前に消えると `read_result` が
            # None を返し、offloaded が未終端のまま永久に固まる（タイムアウトが無い）。
            #
            # **消すのは「タスクが offloaded を抜けた後」**。上の persist_task より前に消すと、
            # そこでクラッシュしたときタスクは offloaded のまま公示だけ消え、次パスで結果が
            # 読めず永久に固まる——まさに時間ベース掃除で避けたかった状態になる。
            # 実際の削除はパス末尾で 1 回にまとめる（board への push を run ごとに撃たない）。
            collected.append(run_id)
        dtok, dusd = parse_cost(msg)
        tokens += dtok
        cost += dusd
        # 人が dashboard 等から run を中止したとき: verify=true でも done にしない。
        # 積み直し（retries を上げて新 run-id）と繰り返しの打ち切りは `_settle_cancelled` に一本化。
        # 語彙統一（W0-9）により board 経由・flow/daemon 経由とも "cancelled" で揃っている
        # （旧 "canceled"（米式）との二重判定は不要になった）。
        if not ok and msg.rstrip().endswith("cancelled"):
            _settle_cancelled(cfg, task, cycle0 + settled + 1, reasons)
            release_claim(cfg, task)
            settled += 1
            continue
        # act/flow 失敗: verify=true で偽 done にしない（cancelled 以外の not ok）。
        # 上の act 失敗と同じく、検証はここまで到達していないので未実行として記録する。
        if not ok:
            ev = delivery_evidence(cfg, msg, gb, loc,
                                   verify=task.verify, vmsg="", ok=False,
                                   verdict=VERIFY_NOT_RUN, phase=PHASE_ACT, task=task)
            _settle_failure(cfg, task, str(msg or "daemon run failed")[:500],
                            cycle0 + settled + 1, ev, reasons, loc,
                            phase=PHASE_ACT, verdict=VERIFY_NOT_RUN)
            release_claim(cfg, task)
            settled += 1
            continue
        res = settle_task(cfg, task, loc, msg, cycle0 + settled + 1, dtok, dusd, gb, venv,
                          policy, autonomy_cache, reasons)
        archived += res["archived"]
        if res["followups"] and spawned < spawn_budget:
            new = spawn_followups(cfg, task, res["followups"], tasks, spawn_budget - spawned)
            spawned += len(new)
        settled += 1
    if _board is not None:
        # 回収し終えた公示を消す。失敗しても実害は無い（次パスで同じ run_id を通らないだけ。
        # 孤児は gc tick の長期マージン掃除が拾う）ので、板の不調で reap を止めない。
        for did in collected:
            with contextlib.suppress(Exception):   # noqa: BLE001 — git 転送は OSError に限らない
                _board.drop_delegation(did)
        # dashboard 等がこのマシン上の同一板クローンへ直接書いた award.json / cancelled.json は
        # agent-project 自身は post 時（新規のときだけ）しか push しない——ここで押し出さないと
        # 依頼側の意思表示（落札確定・中止）が板の同期契機を失い、リモートの請負ノードへ永久に
        # 届かない。post 有無に関わらず、板を触った回は必ず 1 回 push しておく（差分無しは no-op）。
        _board.sync_push("reap sync")
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
    state_sync(cfg)                    # 状態 git: リモートの指示（commands/inbox/needs 記入）を先に取り込む
    controller = (not _coordination_active(cfg)
                  or renew_controller_lease(cfg))
    tasks, policy, reasons, ingested, inboxed, pre_blocked = _run_setup(cfg, controller)
    append_journal(cfg.journal, f"=== agent-project 開始 tasks={len(tasks)} "
                                f"ingested={len(ingested)} planner={cfg.planner} "
                                f"executor={cfg.executor} dry_run={cfg.dry_run} ===")
    append_journal(cfg.journal, state_git_status_line(cfg))
    # 未 push のローカルコミットを起動時に必ず警告する。doctor は人が叩かないと動かないが、
    # これは黙って詰まる（worker と verify は origin から clone するので、ローカルにだけある
    # コミットは彼らからは存在しない）。原因に辿り着くのが難しい詰まり方なので、先に言う。
    _unpushed, _branch = unpushed_commits(cfg.backlog.parent)
    if _unpushed:
        append_journal(cfg.journal,
                       f"警告: origin へ未 push のコミットが {_unpushed} 件ある（{_branch}）。"
                       f"worker と verify は origin から clone するため、これらの成果は彼らから "
                       f"見えない（ローカルでは通るのに verify が落ち続ける）。"
                       f"`git -C {cfg.backlog.parent} push origin {_branch}` を検討すること")
    start = time.time()
    cfg._active_run_deadline = start + cfg.max_seconds if cfg.max_seconds > 0 else None
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
        if _DRAIN_REQUESTED.is_set():
            reason = REASON_DRAINED
            break
        budget_stop_reason = _budget_reason(cfg, cycle, start, tokens_used, cost_used, tasks)
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
            # フォージ（MR/PR）側の決着を取り込む（S4-4）。検収待ちタスクだけを照会するので
            # API 呼び出しは有界。到達不能なら何もしない（現状維持）。
            if poll_task_mrs(cfg, tasks):
                tasks = load_tasks(cfg.backlog)

        # 非ブロッキング委譲（board）の回収: offloaded タスクの run を1回ずつポーリングし、
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
                     if t.id not in unavailable        # 他 worker/インスタンスがクレーム済みは除外
                     and task_runnable_here(cfg, t)]    # 他ノード（PC）へ割当済みは消化しない
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
        verify_env = ({"AGENT_BASE_REV": git_base[0], "KIRO_BASE_REV": git_base[0]}
                      if git_base[0] else None)  # 旧名は互換期間だけ併記
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
            if isinstance(act_ok, _ClaimLost):
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} の claim 喪失を検知し結果を不採用")
                unavailable.add(task.id)
                continue
            if isinstance(act_ok, _ClaimUnknown):
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} の claim を確認できないため安全停止")
                stop = REASON_INFRASTRUCTURE
                break
            if isinstance(act_ok, _BudgetExpired):
                task.status = "ready"
                persist_task(cfg, task)
                release_claim(cfg, task)
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} を実時間予算到達で中断（retry 不消費）")
                stop = REASON_BUDGET
                break
            if pend is not None:                  # 非ブロッキング委譲（offload）: 待たず offloaded に退避
                _mark_offloaded(cfg, task, location, pend.run_id)
                release_claim(cfg, task)          # 実行権は解放（次パスでポーリングして終端したら settle）
                append_journal(cfg.journal, f"{task.id} を offload（run={pend.run_id}）→ 結果待ち")
                unavailable.add(task.id)          # この run ではもう触らない（再選択しない）
                continue
            cycle += 1
            cycle_start = time.time()
            # act の最中に人がタスクを片付けた（強制完了・削除・却下）なら、この試行の結果は
            # どの経路でも確定しない。以降の cancelled / act 失敗 / settle はいずれも
            # persist_task でタスクを書き戻すため、ここで抜けないと完了させたタスクが復活する。
            cleared = human_cleared_reason(cfg, task.id)
            if cleared:
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} は実行中に人が片付けたため結果を確定しない"
                               f"（{cleared}）")
                release_claim(cfg, task)
                unavailable.add(task.id)
                continue
            dtok, dusd = parse_cost(act_msg)             # このサイクルのコストを計上（予算ゲート用）
            tokens_used += dtok
            cost_used += dusd
            if dtok or dusd:
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} cost tokens={dtok} usd={dusd:.4f}"
                                            f"（累計 tokens={tokens_used} usd={cost_used:.4f}）")
            # 人が run を中止したとき: verify=true でも done にしない（リトライ非消費で ready）。
            # 積み直し（retries を上げて新 run-id）と繰り返しの打ち切りは `_settle_cancelled` に一本化。
            # act 中の revise（軌道修正）は失敗/cancelled より優先——結果を確定せず積み直す。
            # 語彙統一（W0-9）により board 経由の同一サイクル即時終端（_act_board）・flow/daemon
            # 経由とも "cancelled" で揃っている（旧 "canceled"（米式）との二重判定は不要）。
            if str(act_msg or "").rstrip().endswith("cancelled") or act_ok is False:
                fresh = _load_task_file(cfg, task.id)
                if fresh is not None and fresh.get("revised"):
                    _requeue_revised(cfg, task, fresh, cycle)
                    release_claim(cfg, task)
                    continue
            if str(act_msg or "").rstrip().endswith("cancelled"):
                _settle_cancelled(cfg, task, cycle, reasons)
                release_claim(cfg, task)
                continue
            # act 失敗（daemon failed 等）: verify=true の偽 done/review を防ぐ。失敗経路へ。
            # verify はここでは走っていない。ok=False で「検証が失敗した」と書くと、着手前に
            # 止まった run が画面で「検証コマンドが失敗しました」になり、人は存在しない
            # テスト失敗を調べに行く。未実行は未実行として記録する。
            if act_ok is False:
                ev = delivery_evidence(cfg, act_msg, git_base, location,
                                       verify=task.verify, vmsg="", ok=False,
                                       verdict=VERIFY_NOT_RUN, phase=PHASE_ACT, task=task)
                _settle_failure(cfg, task, str(act_msg or "act failed")[:500], cycle, ev,
                                reasons, location, phase=PHASE_ACT, verdict=VERIFY_NOT_RUN)
                release_claim(cfg, task)
                continue
            res = settle_task(cfg, task, location, act_msg, cycle, dtok, dusd, git_base,
                              verify_env, policy, autonomy_cache, reasons)
            archived += res["archived"]
            if res["followups"] and spawned_total < cfg.max_spawn:   # done から派生タスク（backlog 自走）
                new = spawn_followups(cfg, task, res["followups"], tasks, cfg.max_spawn - spawned_total)
                spawned_total += len(new)
                if new:
                    append_journal(cfg.journal,
                                   f"cycle {cycle}: {task.id} から派生生成 {[t.id for t in new]}")

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
        "run_id": f"{int(time.time_ns())}-{os.getpid()}",
        "node": str(getattr(cfg, "node", "") or ""),
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
    daemon 稼働中や git バス（remote）は作業中のため触らない。

    runs/<id>/ は viewer のフロータブが読む一次ソースなので、直近 bus_keep_runs 件は残す。
    状態リポジトリは bus/ も同期するので削除は次の同期でリモートへ伝播するが、それは
    「古い run を捨てる」意図どおり——だから消すのは keep 件数を超えた分だけに限る。
    かつては act のたびに runs/ を丸ごと消していたため、run は完了しているのに viewer が
    その最終状態（全ノード done）を観測する前にディレクトリごと消え、最後に撮れた
    スナップショット（最終ノードが実行中）のままフローが固まって見えていた。掃除は
    「古い run を捨てる」ためのものであって「いま終わった run を人の目から隠す」ためのものではない。"""
    # 旧 `cfg.state_git`（明示 URL 指定）での素通りは廃止した（S1）。状態ルートは常に状態
    # 専用リポジトリの clone になり、その条件は「常に真」＝ 掃除が永久に走らなくなるため。
    if not cfg.cleanup or cfg.git_bus:
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
    if _coordination_active(cfg) and availability_state(cfg) != "active":
        return False
    tasks = load_tasks(cfg.backlog)
    # 他ノード（PC）へ割当済みの ready では起こさない。起こすと消化対象ゼロの空パスを
    # 毎 poll 繰り返す（cycles が増え journal が埋まる）。自ノードが消化できる ready だけで起こす。
    if any(task_runnable_here(cfg, t) for t in ready_after_deps(tasks)):
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
    charter_seen: dict[str, float] = {}
    controller = True                 # pause 中に idle 判定へ入っても未定義参照にならないように
    while True:
        if is_paused(cfg):           # pause 中はパスを起こさない（resume/stop の指示待ち）
            append_journal(cfg.journal, "=== watch: 一時停止中（resume/stop 待ち。エージェント非起動）===")
            write_status(cfg)        # paused をリモート viewer へ知らせる
        else:
            controller = (not _coordination_active(cfg)
                          or renew_controller_lease(cfg))
            # 計画（charter 分解）は controller だけが行う。**coordination が有効かどうかでは
            # 分岐しない**——ピアが消えて単独に戻った PC（coordination 非活性 → controller=True）
            # も計画を続けなければ、生き残った側で charter 駆動が止まる。
            if controller and (charter_names(cfg) or _has_master_charter(cfg)):
                # 多 charter はラウンドロビンで全 charter を 1 巡する（max_passes=1 だと
                # 毎パス先頭の charter しか処理されず、2 本目以降が永久に計画されない）
                project_result = {}

                def _project_runner(c):
                    result = run_loop(c, act, ranker, sleeper)
                    project_result.clear()
                    project_result.update(result)
                    return result

                project_watch(cfg, runner=_project_runner, sleeper=sleeper,
                              max_passes=max(1, len(charter_names(cfg))), heartbeat=heartbeat)
                if project_result.get("reason") == REASON_INFRASTRUCTURE:
                    return project_result
                tasks = load_tasks(cfg.backlog)
                last = {"reason": "project", "cycles": 1, "counts": summarize(tasks),
                        "tasks": tasks, "level": cfg.level}
                charter_seen = _charter_mtimes(cfg)
            else:
                last = run_loop(cfg, act, ranker, sleeper)
            passes += 1
            if heartbeat:
                heartbeat()          # 各パスで生存信号を更新（共有レジストリ越しのリモート発見用）
            c = last["counts"]
            print(f"[watch] pass {passes}: reason={last['reason']} "
                  f"done={c['done']} blocked={c['blocked']}", flush=True)
            if last["reason"] == REASON_INFRASTRUCTURE:
                return last
            if last["reason"] == REASON_THROTTLE and cfg.level != "report":
                cfg.level = "report"  # ソフト予算超過 → 以降は report 降格（spend を止め監視は継続）
                print("[watch] throttle: ソフト予算超過につき report レベルへ降格（act 停止）", flush=True)
                append_journal(cfg.journal, "=== watch: throttle 降格（report・act 停止）===")
                write_status(cfg)    # 直近パスの生存信号は降格前の level だったため上書きしておく
            if max_passes is not None and passes >= max_passes:
                return last
            if _DRAIN_REQUESTED.is_set():
                return last
            append_journal(cfg.journal, "=== watch: 監視中（新規タスク/フィードバック待ち。"
                                        "エージェントは待機しない）===")
        while is_paused(cfg) or not has_work(cfg):   # idle/pause: エージェント CLI/flow は一切起動しない
            sleeper(cfg.poll)
            if _DRAIN_REQUESTED.is_set():
                return last
            if _coordination_active(cfg):
                state = availability_state(cfg)
                if state != "active":
                    release_controller_lease(cfg)
                if state == "stopped":
                    return last
            if heartbeat:
                heartbeat()          # idle 中も heartbeat を保ち、リモートから生存が見えるようにする
            if not is_paused(cfg):
                run_intake(cfg)      # 外部ゲートからの汲み上げ（間隔律速。積まれれば has_work が起こす）
            maybe_heartbeat_status(cfg)  # --status-interval のときだけ idle 中も生存信号を更新（既定は無効＝無干渉）
            state_sync(cfg)          # 状態 git: 溜まった変更をコミットし、リモートの指示を取り込む
            #                          （コミットは毎回・fetch/push だけ間隔律速。届けば has_work が起こす）
            next_controller = (not _coordination_active(cfg)
                               or renew_controller_lease(cfg))
            if not is_paused(cfg) and next_controller \
                    and (not controller or _charter_mtimes(cfg) != charter_seen):
                break                 # 前 controller 停止後の自動昇格／charter 更新で project パスへ
            controller = next_controller
            if is_paused(cfg):
                ingest_commands(cfg)  # pause 中も resume/stop（と他の指示）は受け付ける
            if maybe_self_update(cfg):   # アイドル時のみ自己更新を確認・取り込み（取り込めたら再起動）
                raise _RestartRequested()


# ---------------------------------------------------------------------------
