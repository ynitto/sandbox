from __future__ import annotations
# flow.py — 元 agent-project.py の 4100-4640 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。

# flow tick（`agent-flow participate` 1 巡）に掛かってよい時間。cancel 受理・板巡回・
# inbox 受理はどれも短いが、git バスでは sync_pull/push の転送が入る。
_FLOW_TICK_TIMEOUT_SEC = 120.0


def _new_run_id(task: "Task", cfg: "Config") -> str:
    """この試行の run-id。viewer が run ↔ タスクを突き合わせられる形にする
    （req-<hash>-<task-id>-r<retries>[-v<rev>]。dashboard の parseRunId / lineage もこの形を前提）。

    導出は `_req_id_for` に一本化する。かつては hash(task.id) だったため、
    同じタスクが経路ごとに別 lineage へ割れて UI の系統まとめが壊れていた。"""
    return _req_id_for(task, cfg, task.retries)


# agent-flow の run が終端か（`runs/<rid>/meta.json` の status）。**読み取り用の集合**
# （正典 done/failed/cancelled + 旧綴り "canceled"）を使う。参照箇所はすべて「バス上の
# 既存 meta.status が終端か」の判定であり、W0-9 の語彙統一より前に人が cancel した run は
# 旧綴りのままバスに残っている。それを非終端と読むと `_run_resumable` がリース/age 判定へ
# 落ちて「停滞」と誤読し、`expire_orphan_flow_leases` がリースを失効させて蘇生を確定させる
# （agentcore.vocab の docstring が警告する経路そのもの）。書き込みは常に正典のみ。
from agentcore.vocab import TERMINAL_READ as _FLOW_TERMINAL  # noqa: E402

# リース未記録の非終端 run を「停滞」とみなすまでの猶予。agent-flow の worker は 1 ノードに
# 数分かかるので、生きている run を誤って停滞と読まない程度に長く取る。
_STALE_RUN_SEC = 600.0
_FLOW_LEASE_ALIVE = "alive"
_FLOW_LEASE_EXPIRED = "expired"
_FLOW_LEASE_TERMINAL = "terminal"
_FLOW_LEASE_UNKNOWN = "unknown"
_FLOW_LEASE_EXPIRY_CONFIRM_SEC = 10.0


class _ClaimLost:
    """act の失敗ではなく、別 owner へ実行権が移ったことを表す。"""
    pass


class _ClaimUnknown:
    """claim の所有権を確認できず、安全に継続も解放もできないことを表す。"""
    pass


class _BudgetExpired:
    """act の内容失敗ではなく、run 全体の実時間予算到達を表す。"""
    pass


def _run_age_sec(meta: dict) -> float:
    """run メタの最終更新からの経過秒（時刻が読めなければ inf ＝ 古いものとして扱う）。"""
    ts = str(meta.get("updated_at") or meta.get("created_at") or "").strip()
    if not ts:
        return float("inf")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _flow_run_bus(cfg: "Config", use_git: bool = False) -> "Path":
    """監視対象runの実体があるbus root。"""
    bus = cfg.bus
    if use_git and cfg.git_bus:
        # agent-flow run 自身の GitBus clone は --bus/run に固定され、監視ループが pull する。
        bus = bus / "run"
        if cfg.git_subdir:
            bus /= cfg.git_subdir.strip("/")
    return bus


def _flow_run_lease_state(cfg: "Config", rid: str,
                          now: "float | None" = None, *,
                          use_git: bool = False) -> str:
    """orchestrator lease を alive / expired / terminal / unknown で返す。"""
    bus = _flow_run_bus(cfg, use_git)
    try:
        meta = json.loads((bus / "runs" / rid / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _FLOW_LEASE_UNKNOWN
    if str(meta.get("status") or "") in _FLOW_TERMINAL:
        return _FLOW_LEASE_TERMINAL
    lease = meta.get("orch_lease_until")
    if not isinstance(lease, (int, float)):
        return _FLOW_LEASE_UNKNOWN
    return (_FLOW_LEASE_EXPIRED
            if float(lease) < (time.time() if now is None else now)
            else _FLOW_LEASE_ALIVE)


def _act_deadline(cfg: "Config", now: "float | None" = None) -> "tuple[float | None, str]":
    """local act が従う最も早い壁時計上限と、その設定名。"""
    base = time.time() if now is None else now
    candidates = []
    if cfg.act_timeout > 0:
        candidates.append((base + cfg.act_timeout, "act_timeout"))
    overall = getattr(cfg, "_active_run_deadline", None)
    if isinstance(overall, (int, float)):
        candidates.append((float(overall), "max_seconds"))
    return min(candidates, default=(None, ""),
               key=lambda item: float("inf") if item[0] is None else item[0])


def _run_resumable(cfg: "Config", rid: str) -> bool:
    """その run は「続きから」やり直せるか。

    やり直せる = 失敗して終わった（failed）か、停滞している（非終端なのに orchestrator の生存
    リースが切れている＝誰も進めていない）。後者を見落とすと、orchestrator が落ちた run は
    status=running のまま永久に残り、失敗ノードも pending ノードも二度と実行されない
    （実際 agent-project を止めるたびにこの孤児 run が量産され、成功していた 14 ノードごと
    作り直していた）。"""
    try:
        meta = json.loads((cfg.bus / "runs" / rid / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    st = str(meta.get("status") or "")
    if st == "failed":
        return True
    if st in _FLOW_TERMINAL:
        return False                      # done / cancelled は作り直す
    lease = meta.get("orch_lease_until")  # 非終端: 生存リースで実態を見る
    if isinstance(lease, (int, float)):
        return float(lease) < time.time()
    # リース未記録の run は agent-flow の run_is_orphaned と同じく age に落とす。ここで False を
    # 返すと（＝リース不在を「生きている」と読む）、heartbeat を張る前に死んだ run も、旧版が
    # 残した run も永久に再開できず、進捗を抱えたまま非終端で固まる。実際 9/31 ノードまで
    # 進んだ run がこれで宙吊りになり、やり直す手段が無かった。
    return _run_age_sec(meta) > _STALE_RUN_SEC


def run_id_for(cfg: "Config", task: "Task") -> str:
    """この試行で agent-flow に渡す run-id。**失敗・停滞した直前の run は作り直さず再開する。**

    agent-flow は failed / 停滞 run を `--run-id` で受けると retry_failed を実行し、**失敗ノード
    だけを pending へ戻して done のノードは温存**して続きから走る。ところが agent-project は
    これまで --run-id を一切渡していなかったため、リトライのたびにまっさらな run を作っていた。
    25 ノードのうち 1 つが失敗しただけで、成功していた 14 ノード分の LLM 呼び出しを丸ごと捨てて
    全部やり直すことになる（コストも時間も N 倍）。

    ただし人がタスクを触ったとき（revise / 差し戻しの feedback）は計画そのものが変わるので、
    続きからではなく新しい run を作る。環境要因ブロック（env_resume）からの復帰は、人が
    needs にメモを書いても計画変更ではない——同じ run の続きを約束しているので feedback を無視する。"""
    rid = str(task.get("last_run") or "").strip()
    plan_changed = _plan_changed_since_last_run(task)
    if rid and not plan_changed and _run_resumable(cfg, rid):
        return rid                        # 失敗・停滞した所だけやり直す（done は温存）
    return _new_run_id(task, cfg)


def _context_file_for(task: Task, cfg: "Config") -> "str | None":
    """安定プレフィックス化（案 H・オプトイン）: project_context_block() を
    agent-flow へ渡すファイルへ書く。既定 off なら None（呼び出し側は引数を足さない＝
    request の組み立てと agent-flow への argv が従来と 1 バイトも変わらない）。

    パスはタスク単位で決定的（OS 一時ディレクトリ配下）にし、実行のたびに上書きする。
    状態リポジトリ（root）配下には置かない——charter/rules/repo_map が変わるたびに
    差分が生まれる使い捨てファイルを git 同期対象へ混ぜない。クリーンアップは
    要らない（同じタスクの次回実行が上書きするので、キー空間はバックログ規模に留まる）。"""
    if not getattr(cfg, "stable_prefix", False):
        return None
    block = project_context_block(cfg, task)
    if not block:
        return None
    path = os.path.join(tempfile.gettempdir(), f"agent-project-context-{task.id}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(block)
    return path


def build_agent_flow_cmd(task: Task, cfg: "Config", use_git: bool = False,
                        run_id: str = "", inherit_from: str = "") -> "list[str]":
    """agent-flow run（都度起動）のコマンド。planner/executor を制御できる（submit では不可）。
    書込先は _act_batch で確定・永続化済みの `- workspace:` を読む（再ルーティングしない）。
    run_id を渡すと、その run を再開する（failed なら agent-flow が失敗ノードだけ戻して続行）。
    inherit_from は新 run 時に先行 run の done ノードを引き継ぐ（submit/offload と同じ契約）。"""
    executor = cfg.executor
    if task.get("spec_for") and executor_delegates(cfg):
        # spec 作成タスクは委譲しない（§5.10）: gitlab 等の委譲先では specs/<id>/ がローカルに
        # 生成されず verify が成立しない。組み込み agent でローカル完結させる
        # （decide_location が spec タスクを local 固定しているため、この差し替えが必ず効く）。
        executor = "agent"
    base = _kf_base(cfg, use_git)
    if run_id:
        base += ["--run-id", run_id]      # グローバル引数（サブコマンドより前）
    # 内側（実行時タスクグラフ）の分解粒度は flow_granularity（既定 auto）を渡す。外側の
    # granularity（バックログの INVEST 粒度・既定 coarse）を渡してはいけない——別のノブで、
    # coarse を内側へ流すと agent-flow の complexity 導出が常に上書きされ、work ノードレンジが
    # 1〜3 に固定される（複雑なタスクでも「まとめて 1〜3 ノード」に畳まれる）。
    # agent-flow の `--granularity` はグローバル引数なので run より前に置く。
    base += ["--granularity", str(getattr(cfg, "flow_granularity", "auto") or "auto")]
    # 統一 verify: 検証計画を構造化して渡す（planner の自由記述へ混ぜない）。agent-flow は
    # 成果 revision 確定後の専用 runner で一度だけ実行し、receipt を返す（settle が検算する）。
    # 受け渡しは argv `--verification-plan`（env 渡しは不安定として人が却下・2026-07-31）。
    # agent-project と agent-flow は同時に更新する前提（旧 agent-flow はこのフラグを解釈できない）。
    _plan = build_task_verification_plan(cfg, task)
    if _plan:
        base += ["--verification-plan", json.dumps(_plan, ensure_ascii=False)]
    ctx_file = _context_file_for(task, cfg)
    if ctx_file:
        base += ["--context-file", ctx_file]
    cmd = (base + _workspace_cmd_args(cfg, task)
           + _reference_cmd_args(cfg, task) + [
        "run", build_request(task, cfg), "--planner", cfg.flow_planner,
        "--executor", executor, "--max-iterations", str(cfg.max_iterations)])
    if inherit_from:
        cmd += ["--inherit-from", inherit_from]
    # 委譲 executor（gitlab）の却下は agent-flow 内部で再委譲せず即失敗させ、agent-project の
    # 通常リトライ（人コメント注入つき）に委ねる。複数イシューの濫造を防ぐ。
    if executor not in ("agent", "stub"):
        cmd += ["--max-retries", "0"]
    return cmd


def cmd_gc(cfg: "Config", json_out: bool = False) -> int:
    """状態リポジトリの保持契約（W11）を実行し、agent-flow バスの一時ファイル・古い run
    アーカイブを掃除する（設計 §4.2 node 層 gc tick の実体）。

    バス側の掃除の実装は持たない（R1）——既存 agent-flow の `run_cleanup`/`cmd_gc` を
    `agent-flow cleanup`/`agent-flow gc` として単発起動するだけ。状態リポジトリ側
    （verifications / journal / run-log）の保持契約だけはこちらの `enforce_retention` が持つ。

    `_cleanup_bus`（loop.py）は git_bus 構成のバスをあえて素通りする（作業中のため）ので、
    その委譲先はここ。ロック/tmp/孤立クローン/共有キャッシュの掃除は git_bus の有無に
    関わらず常に必要（旧 flow daemon の cleanup_interval が担っていたが、常駐一本化で
    flow daemon 自体を廃止したため呼び手が居なくなっていた）。"""
    use_git = bool(cfg.git_bus)
    base = _kf_base(cfg, use_git)
    totals: dict = {f"state.{k}": v for k, v in enforce_retention(cfg).items()}  # 保持契約（W11）
    proc = subprocess.run(base + ["cleanup", "--json"], capture_output=True, text=True)
    try:
        totals.update({f"cleanup.{k}": v for k, v in json.loads(proc.stdout or "{}").items()})
    except ValueError:
        pass   # 掃除の失敗は gc tick 全体を止めない（呼び出し元 resident.gc.run_gc が隔離）
    if use_git:                    # _cleanup_bus が素通りする構成だけ archive gc も担う
        proc2 = subprocess.run(base + ["gc", "--older-than", "7", "--keep",
                                       str(cfg.bus_keep_runs)], capture_output=True, text=True)
        totals["gc.deleted"] = proc2.stdout.count("削除: ")
    if json_out:
        print(json.dumps(totals, ensure_ascii=False))
    else:
        print(f"[agent-project] gc: {totals}")
    return 0


def cmd_flow_participate(cfg: "Config", running: str = "", json_out: bool = False,
                         node_declaration: str = "") -> int:
    """このプロジェクトのバスで `agent-flow participate` を 1 巡させる（設計 §4.2 node 層
    flow tick の実体）。cancel 受理・park 再確認・孤児回収・板巡回・inbox 受理を agent-flow に
    行わせ、**実行すべき run-id をそのまま中継する**（実行は呼び出し側＝常駐体の
    NodeWorkerPool が `flow-run` で起こす）。

    参加の実装は持たない（R1）。ここが担うのは agent-project の設定（バス・git バス・
    flow_config・executor）から agent-flow の argv を組み立てることだけ——常駐体に
    プロジェクト設定を解決させると `resolve_config` が 2 箇所になる。"""
    base = _kf_base(cfg, bool(cfg.git_bus))
    # 板の入札選別に使うノード宣言（host.yaml の repos / tags / agent_cli）の在処。
    # agent-flow の**グローバル引数**なのでサブコマンドより前に置く（--bus / --git と同じ）。
    # 渡さなければ agent-flow が既定の探索順（cwd → ~/.agents）で自分で見つける——
    # 常駐体が非既定の host.yaml で動いているときだけ、この明示が要る。
    if node_declaration:
        base += ["--node-declaration", node_declaration]
    base += ["participate", "--json", "--executor", cfg.executor]
    if running:
        base += ["--running", running]
    proc = subprocess.run(base, cwd=str(cfg.workdir), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=_FLOW_TICK_TIMEOUT_SEC)
    if proc.returncode != 0:
        print(f"[agent-project] flow participate 失敗 (exit {proc.returncode}): "
              f"{(proc.stderr or '').strip()[-300:]}", file=sys.stderr)
        return 1
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError:
        items = []
    run_ids = [str((it or {}).get("run_id") or "").strip() for it in items]
    run_ids = [r for r in run_ids if r]
    if json_out:
        print(json.dumps([{"run_id": r} for r in run_ids], ensure_ascii=False))
    else:
        for r in run_ids:
            print(r)
    return 0


def cmd_flow_run(cfg: "Config", run_id: str) -> int:
    """`participate` が受理した run を実行する（完了まで待つ）。要求文・書込先ワークスペース・
    参照リポジトリ・引き継ぎ元は `--from-inbox` が inbox 要求から読む——ここで argv へ転記すると
    項目が増えるたびに転記漏れが静かな機能欠落になる。

    常駐体はこれを `NodeWorkerPool` の 1 仕事として起こす。run 自身が生存リースを張り park も
    面倒を見るので、駆動を代行する常駐プロセスは要らない。"""
    rid = str(run_id or "").strip()
    if not rid:
        print("エラー: --run-id が必要です", file=sys.stderr)
        return 2
    cmd = _kf_base(cfg, bool(cfg.git_bus)) + ["--run-id", rid,
                                              # 内側の粒度は flow_granularity（build_agent_flow_cmd と同じ理由）
                                              "--granularity",
                                              str(getattr(cfg, "flow_granularity", "auto") or "auto"),
                                              "run", "--from-inbox",
                                              "--planner", cfg.flow_planner,
                                              "--executor", cfg.executor,
                                              "--max-iterations", str(cfg.max_iterations)]
    return subprocess.run(cmd, cwd=str(cfg.workdir)).returncode


def _pid_alive(pid: int) -> bool:
    """pid が生存しているか（POSIX）。0/負や不在は False。別ユーザのプロセスは生存扱い。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # 別ユーザの生存プロセス（送れないだけ）
    except OSError:
        return False
    return True


def _pin_last_run(cfg: "Config", task: Task, run_id: str) -> None:
    """この試行で使った run-id をタスクへ残す（再開判断・viewer 突合・作業ブランチ解決用）。
    同期 run 以外（submit/offload）でも必ず書く。書いていないと offload 回収後に last_run が無く、
    delivery / protect / resume が状態 worktree のノイズ差分を見てしまう。
    再開（または新 run）を掴んだ時点で env_resume は消化する。"""
    rid = str(run_id or "").strip()
    if not rid:
        return
    task.drop("last_run", "env_resume")
    task.extra.append(("last_run", rid))
    persist_task(cfg, task)


def detach_flow_run(cfg: "Config", task: Task, reason: str = "",
                    *, failed: bool = False) -> "str | None":
    """委譲中（offloaded）の agent-flow run を切り離して止める（best-effort）。

    revise / hold / reject でタスクを別方向へ進めるとき、旧 run を放置すると
    ap/<task-id> へ二重書き込みし、reap も古結果を settle しうる。cancel マーカー＋
    waits 掃除は agent-flow cmd_cancel / dashboard cancelRun と同契約。
    既定の終端は cancelled（人の停止・軌道修正＝次 run は inherit しない）。
    タイムアウトなど一時失敗は failed=True（failure_reason 付き）にし、次 run が
    done ノードを引き継げるようにする。戻り値は止めた run-id（無ければ None）。"""
    rid = str(task.get("flow_run") or "").strip()
    task.drop("flow_run", "flow_loc")
    if not rid:
        return None
    why = (reason or "agent-project: タスクを委譲から切り離し").strip()
    bus = cfg.bus
    cancels = bus / "inbox" / "cancels"
    run_dir = bus / "runs" / rid
    meta_path = run_dir / "meta.json"
    applied = False
    end_status = "failed" if failed else "cancelled"

    def _write_cancel_marker() -> None:
        try:
            cancels.mkdir(parents=True, exist_ok=True)
            rec = {
                "id": rid, "who": "agent-project", "reason": why,
                "close_issues": False,
                "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            p = cancels / f"{rid}.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass

    def _apply_terminal() -> None:
        nonlocal applied
        try:
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                st = str(meta.get("status") or "")
                if st not in _FLOW_TERMINAL:
                    meta["status"] = end_status
                    meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if failed:
                        meta["failure_reason"] = why
                    else:
                        meta["cancel_reason"] = why
                    write_json_atomic(str(meta_path), meta)
                applied = True  # meta がある＝適用済み（既終端でもマーカーは消してよい）
            waits = run_dir / "waits"
            if waits.is_dir():
                for f in list(waits.glob("*.json")):
                    try:
                        f.unlink()
                    except OSError:
                        pass
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # failed: 先に終端化してから cancel マーカー（daemon の mark_canceled が no-op になる）。
    # cancelled: 先にマーカー（人の停止意図を同期）してから終端化。
    if failed:
        _apply_terminal()
        _write_cancel_marker()
    else:
        _write_cancel_marker()
        _apply_terminal()
    # failed は子を止めた後に使う一時マーカーなので、その場で消して再開可能にする。
    # cancelled は実行所有者が停止を確認するまで残す。外部から消すと、並行 heartbeat が
    # 古い meta を書き戻したときに停止意図まで失われる。
    if applied and failed:
        try:
            (cancels / f"{rid}.json").unlink(missing_ok=True)
        except OSError:
            pass
    append_journal(cfg.journal, f"flow detach: {task.id} の run {rid} を {end_status}（{why}）")
    return rid


def _act_run(task: Task, cfg: "Config", use_git: bool = False) -> "tuple[bool, str]":
    """agent-flow run で都度起動（同期実行）。daemon 不要。

    run-id は run_id_for が決める（直前の run が failed なら再開＝失敗ノードだけやり直す）。
    使った run-id はタスクへ残し、次の試行の再開判断と viewer の突き合わせに使う。
    結果待ち中に人が revise したら submit 経路と同じく cancel で切り離す（放置すると完走して
    二重書き込みしうる）。"""
    rid = run_id_for(cfg, task)
    resuming = rid == str(task.get("last_run") or "").strip()
    # 新 run なら先行 last_run から done を引き継ぐ（submit 経路と同じ。retries-1 推定は rev ずれで外れる）
    inherit = "" if resuming else (_inherit_from_run(task, rid, cfg) or "")
    cmd = build_agent_flow_cmd(task, cfg, use_git, run_id=rid, inherit_from=inherit)
    _pin_last_run(cfg, task, rid)
    # 同期待ち中も approve/hold が detach できるようピン（submit 経路と同じ）
    task.set("flow_run", rid)
    persist_task(cfg, task)
    if resuming:
        append_journal(cfg.journal,
                       f"run 再開: {task.id} は {rid} の失敗ノードだけをやり直します（done は温存）")
    # 統一 verify の plan は build_agent_flow_cmd が argv `--verification-plan` で渡す
    # （env 渡しは不安定として人が却下・2026-07-31。両ツールは同時更新が前提）。
    try:
        # Popen＋ポーリング: subprocess.run だと timeout まで mid-revise を検知できない。
        proc = subprocess.Popen(cmd, cwd=str(cfg.workdir),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        task.drop("flow_run", "flow_loc")
        persist_task(cfg, task)
        return (False, f"agent-flow を起動できません: {e}")
    # PIPE が満杯になると agent-flow が書き込みブロックするので、待機中に吐き出す。
    out_chunks: "list[str]" = []

    def _drain() -> None:
        try:
            if proc.stdout:
                for chunk in iter(proc.stdout.readline, ""):
                    out_chunks.append(chunk)
        except (OSError, ValueError):
            pass

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    deadline, deadline_source = _act_deadline(cfg)
    lease_start_deadline = time.time() + _STALE_RUN_SEC
    lease_monitor_armed = False
    lease_expired_since = None
    lease_unknown_since = None
    terminal_since = None
    claim_unknown_since = None

    def _fail_run(why: str, status=False, *, detach: bool = True):
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except OSError:
                pass
        if detach:
            task.set("flow_run", rid)
            detach_flow_run(cfg, task, why, failed=True)
        reap_orphan_flow(cfg)
        return status, why

    # 同期 run は agent-project のループを塞ぐため、従来は run 完了まで state_git が push
    # されず、別 PC の dashboard/engine が bus/runs の graph・claims・results を見られなかった。
    # 待機中も state_git_interval ごとに best-effort 同期して、同期 run のままでも分担/監視できる
    # 回避路を作る（force しないのでリモート負荷は既存 interval に従う）。
    next_progress_sync = 0.0
    next_claim_heartbeat = time.time() + _CLAIM_HEARTBEAT_SEC
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                drainer.join(timeout=2.0)
                break
            abort = _wait_abort_reason(cfg, task, rid)
            if abort:
                why = abort
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except OSError:
                        pass
                task.set("flow_run", rid)
                detach_flow_run(cfg, task, f"{why} により同期 run を中断")
                # 刈り残した orch/worker は daemon を残して止める（外部 daemon 全滅を避ける）
                reap_orphan_flow(cfg)
                return (False, f"daemon run {rid} の結果待ちを中断（{why} を検知）")
            now = time.time()
            lease_state = _flow_run_lease_state(cfg, rid, now, use_git=use_git)
            tick = time.monotonic()
            if lease_state == _FLOW_LEASE_ALIVE:
                lease_monitor_armed = True
                lease_expired_since = lease_unknown_since = None
                terminal_since = None
            elif lease_state == _FLOW_LEASE_TERMINAL:
                lease_expired_since = lease_unknown_since = None
                try:
                    meta = json.loads((_flow_run_bus(cfg, use_git) / "runs" / rid / "meta.json")
                                      .read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    meta = {}
                healing = (str(meta.get("status") or "") == "failed"
                           and isinstance(meta.get("heal_next_at"), (int, float))
                           and float(meta["heal_next_at"]) > now
                           and not meta.get("heal_exhausted"))
                if healing:
                    terminal_since = None
                else:
                    if terminal_since is None:
                        terminal_since = tick
                    if tick - terminal_since >= _FLOW_TICK_TIMEOUT_SEC:
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception:  # noqa: BLE001
                            try:
                                proc.kill()
                            except OSError:
                                pass
                        reap_orphan_flow(cfg)
                        drainer.join(timeout=2.0)
                        break
            elif lease_state == _FLOW_LEASE_EXPIRED:
                terminal_since = None
                lease_unknown_since = None
                if lease_monitor_armed or now >= lease_start_deadline:
                    if lease_expired_since is None:
                        lease_expired_since = tick
                    if tick - lease_expired_since >= _FLOW_LEASE_EXPIRY_CONFIRM_SEC:
                        return _fail_run("agent-flow run 応答なし（orchestrator lease 失効）")
            else:
                terminal_since = None
                lease_expired_since = None
                if lease_unknown_since is None:
                    lease_unknown_since = tick
                if tick - lease_unknown_since >= _STALE_RUN_SEC:
                    return _fail_run("agent-flow run 応答なし（orchestrator lease 未記録）")
            if now >= next_claim_heartbeat:
                refreshed = _refresh_claim(cfg, task)
                if refreshed == _CLAIM_REFRESH_OK:
                    claim_unknown_since = None
                    next_claim_heartbeat = now + _CLAIM_HEARTBEAT_SEC
                elif refreshed == _CLAIM_REFRESH_LOST:
                    return _fail_run("agent-project task claim 喪失", _ClaimLost())
                else:
                    if claim_unknown_since is None:
                        claim_unknown_since = tick
                    if tick - claim_unknown_since >= _CLAIM_REFRESH_UNKNOWN_GRACE_SEC:
                        return _fail_run("agent-project task claim を確認不能", _ClaimUnknown(),
                                         detach=False)
                    next_claim_heartbeat = now + _CLAIM_REFRESH_RETRY_SEC
            if now >= next_progress_sync:
                sync = globals().get("state_sync")
                if sync is not None:
                    try:
                        sync(cfg, force=False)
                    except Exception:  # noqa: BLE001 - state_sync 自体も best-effort。run は止めない。
                        pass
                next_progress_sync = now + max(1.0, float(getattr(cfg, "state_git_interval", 300.0) or 300.0))
            if deadline is not None and now >= deadline:
                # submit タイムアウトと同じ: failed にして次回は done ノードを引き継ぐ。
                why = (f"agent-project 実時間上限（max_seconds={cfg.max_seconds}s）"
                       if deadline_source == "max_seconds"
                       else f"agent-flow run タイムアウト（{cfg.act_timeout}s）")
                status = _BudgetExpired() if deadline_source == "max_seconds" else False
                return _fail_run(why, status)
            time.sleep(1.0)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if proc.stdout:
            try:
                proc.stdout.close()
            except OSError:
                pass
    out = "".join(out_chunks)
    task.drop("flow_run", "flow_loc")
    # 同期 run の cancelled は exit≠0 でもメッセージが日本語のため、meta で確定して
    # 上位の cancelled 特別扱い（リトライ非消費で ready）へ乗せる。
    try:
        meta = json.loads((_flow_run_bus(cfg, use_git) / "runs" / rid / "meta.json")
                          .read_text(encoding="utf-8"))
        terminal_status = str(meta.get("status") or "")
        if terminal_status == "cancelled":
            return (False, f"daemon run {rid} cancelled")
        if terminal_status == "done":
            return (True, out[-300:].strip())
        if terminal_status == "failed":
            return (False, out[-300:].strip() or str(meta.get("failure_reason") or "run failed"))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return (proc.returncode == 0, out[-300:].strip())


def _load_task_file(cfg: "Config", tid: str) -> "Task | None":
    """backlog/<id>.md をディスクから読み直す（無い/読めないなら None）。"""
    p = cfg.backlog / f"{tid}.md"
    try:
        return parse_task(p.read_text(encoding="utf-8"), tid) if p.exists() else None
    except OSError:
        return None


def human_cleared_reason(cfg: "Config", tid: str) -> "str | None":
    """実行中に人がこのタスクを片付けたか（強制完了・削除・却下）。片付いていればその理由。

    片付いた後に試行の結果を確定させると、settle が backlog へ書き戻してタスクが復活する
    ——archive（完了）と backlog（実行中）に同じ id が並ぶ二重在庫になり、画面では
    「完了したのにまだ実行中」に見える。強制完了は実行中（doing）・委譲中（offloaded）の
    タスクにも効く口なので、この競合は例外ではなく通常経路として起きる。

    ファイル消失（archive へ退避済み）と `force_completed` マーカーの両方を見る。状態
    リポジトリ同期の遅れで片方しか見えないことがあるため（マーカーだけ届いてファイルは
    まだ残っている／その逆）、どちらか一方でも片付いた証拠として扱う。読めないだけの
    一過性エラーを片付け扱いにしないよう、消失はパスの非存在で判定する。"""
    p = cfg.backlog / f"{tid}.md"
    if not p.exists():
        return "タスクファイルが無い（強制完了・削除・却下で片付け済み）"
    fresh = _load_task_file(cfg, tid)
    if fresh is None:
        return None
    if fresh.get("force_completed"):
        return f"人が強制完了させた: {fresh.get('force_complete_reason') or '理由なし'}"
    return None



def _wait_abort_reason(cfg: "Config", task: Task, run_id: str) -> "str | None":
    """同期結果待ちを打ち切るべき人操作があればその理由、無ければ None。

    revise 以外（approve / hold / reject / feedback）は `revised` 無しで status / flow_run
    だけ変える。flow_run を待ち開始時にピンしておき、外れたら中断する。
    status だけで中断しない（ピン時点の status 揺れや ready 表記残りを false-positive にしない）。
    flow_run が残ったまま status だけ変わるのは、人が別操作で上書きしたケースとして
    flow_run 不一致・欠落と合わせて検知する。"""
    # 強制完了・削除・却下で片付いたら待つ意味が無い（結果は採用しない）。待ち続けると、
    # 人が画面で完了させたタスクの run が終わるまでループが塞がる。
    if human_cleared_reason(cfg, task.id):
        return "片付け済み"
    fresh = _load_task_file(cfg, task.id)
    if fresh is None:
        return None
    if fresh.get("revised"):
        return "revise"
    pinned = str(run_id or "").strip()
    fr = str(fresh.get("flow_run") or "").strip()
    if pinned and not fr:
        return "detach"
    if pinned and fr and fr != pinned:
        return "flow_run 変更"
    return None


def _adopt_task(task: Task, fresh: Task) -> None:
    """in-memory の Task をディスクの内容（fresh）へ合わせる（人の revise/直接編集の採用）。"""
    task.title, task.status, task.source = fresh.title, fresh.status, fresh.source
    task.priority, task.verify, task.retries = fresh.priority, fresh.verify, fresh.retries
    task.extra = list(fresh.extra)


def _requeue_revised(cfg: "Config", task: Task, fresh: Task, cycle: int) -> None:
    """実行中に人が revise したタスクを、結果を確定させずに修正内容で積み直す。
    verify も done もしない（方向の変わった成果を判定しても意味を持たないため）。"""
    fresh.drop("revised")
    fresh.status = "ready"
    _adopt_task(task, fresh)
    persist_task(cfg, task)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の revise により積み直し"
                                "（この試行の結果は確定しない）")


def _req_id_for(task: Task, cfg: "Config", retries: int) -> str:
    """指定 retries 世代の決定的 req_id。

    （backlog パス, task.id, retries, rev）で一意にする。backlog パスの hash は共有バスに
    複数プロジェクトが乗るときの衝突を防ぐ。リトライ（retries+1）と人の revise（rev 世代）は
    新しい試行＝新しい run にする（軌道修正後の act が修正前の古い run に合流しないように）。"""
    h = hashlib.sha1(str(cfg.backlog.resolve()).encode()).hexdigest()[:8]
    tid = re.sub(r"[^\w.-]+", "_", str(task.id))[:60]
    rev = str(task.get("rev", "") or "").strip()
    return f"req-{h}-{tid}-r{retries}" + (f"-v{rev}" if rev else "")


def _plan_changed_since_last_run(task: Task) -> bool:
    """直前の run 以後に成果内容が変更されたか。環境復帰の feedback は変更に数えない。"""
    return bool(task.get("revised")) or (
        bool(task.get("feedback")) and not task.get("env_resume"))


def _inherit_from_run(task: Task, new_run_id: str, cfg: "Config | None" = None) -> "str | None":
    """新 run へ引き継ぐ先行 run-id。`last_run` が新 id と違えばそれを使う。

    retries-1・現 rev から推定すると、revise で rev が上がったあと実在しない
    `…-r{N-1}-v{newRev}` を指して inherit が空振りする。last_run が実際の先行。
    cancelled の last_run は引き継がない（人の停止・軌道修正を尊重。done を蘇らせない）。
    タイムアウト等の failed は引き継ぐ（agent-flow inherit_from と同じ契約）。"""
    if _plan_changed_since_last_run(task):
        return None
    last = str(task.get("last_run") or "").strip()
    if not last or last == str(new_run_id or "").strip():
        return None
    if cfg is not None:
        try:
            meta = json.loads((cfg.bus / "runs" / last / "meta.json").read_text(encoding="utf-8"))
            if str(meta.get("status") or "") == "cancelled":
                return None
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return last


class _Pending:
    """act の第3の結果＝『委譲公示板へ公示済み・まだ終端していない』。
    run_loop はこれを受けたらタスクを offloaded にして settle をスキップし、次パスでポーリングする。"""
    __slots__ = ("run_id",)

    def __init__(self, run_id: str):
        self.run_id = run_id


def _flow_result_once(cfg: "Config", use_git: bool, run_id: str) -> "tuple[bool, bool, str]":
    """agent-flow result を1回だけ読む（待たない）。(terminal, ok, msg) を返す。
    terminal=run が終端（done/failed/cancelled）に達したか。
    ok=成功終端（done）か。failed / cancelled は ok=False（cancelled を success と取り違えない —
    dashboard から人が中止した run を verify=true で done 確定させないため）。
    取得不能は (False, False, "error: …") で継続待ち扱いにするが、msg でエラーを区別して
    返す——CLI 不在・バス破損・出力化けを「まだ実行中」と読み続けると offloaded タスクが
    永久にスタックする（呼び出し側が連続エラーを数えて打ち切れるように）。"""
    base = _kf_base(cfg, use_git)
    try:
        res = subprocess.run(base + ["result", "--run-id", run_id, "--json"],
                             cwd=str(cfg.workdir), timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            return (False, False,
                    f"error: agent-flow result rc={res.returncode}: {(res.stderr or '').strip()[:200]}")
        data = json.loads(res.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError, ValueError) as e:
        return (False, False, f"error: agent-flow result 取得失敗: {e}")
    if not data.get("done"):
        return (False, False, "")
    status = str(data.get("status") or "")
    if status == "failed":
        return (True, False, f"daemon run {run_id} failed")
    if status == "cancelled":
        return (True, False, f"daemon run {run_id} cancelled")
    return (True, True, f"daemon run {run_id} done")


def _act_board(task: Task, cfg: "Config") -> "tuple":
    """委譲公示板（agent-board）への非ブロッキング公示。post が無ければ書き、結果を1回だけ確認する。
    終端なら (ok, msg)、未終端なら (_Pending(delegation_id), msg) を返す（待たない・常に非同期 —
    board は「公示して請負側の入札を待つ」性質上、常に非同期）。
    請負側（agent-flow / agent-amigos の board 参加デーモン）が入札・実行し、完了したら board の
    result.json へ書き戻す（agent_flow/board.py・agent_amigos/board.py の report_results）。
    委譲 id はそのまま実行側の run-id / mission-id として使われる（共通 id は対応表を持たない —
    delegation 契約 D1 と同じ規約）ので、last_run（delivery/branch 解決）はそのまま使える。"""
    did = _board_delegation_id(task, cfg)
    board = BoardRepo(cfg.board, workdir=cfg.board_workdir)
    board.sync_pull()
    _pin_last_run(cfg, task, did)
    term, ok, msg = _board_result_once(board, did)
    if not term:
        spec = _workspace_spec_for(cfg, task)
        refs = task_reference_specs(cfg, task)
        # board 委譲は請負側が別マシン（--context-file のようなローカル参照を渡せない）ため、
        # stable_prefix が有効でも charter/rules/repo_map は本文へ埋め込む。
        env = task_to_delegation(task, spec, workload=cfg.board_workload, delegation_id=did,
                                 request=build_request(task, cfg, force_inline_context=True),
                                 references=refs)
        if board.write_post(env):          # 新規のときだけ push（無駄な空 commit を作らない）
            board.sync_push(f"post {did}")
        term, ok, msg = _board_result_once(board, did)   # 直後にもう一度（同一 cycle 内解決対応）
        if not term:
            return (_Pending(did), f"board delegation {did} 公示（入札・実行待ち）")
    return (ok, msg)


VERIFY_DELEGATION_LOC = "board-verify"
"""検証委譲（P4-b）の `- flow_loc:`。実行そのものではなく**検証だけ**を板へ回した印で、
回収（`_reap_offloaded`）はこの値を見て「結果＝検証の判定」として扱う。"""


def _verify_delegation_id(task: "Task", cfg: "Config", rev: str) -> str:
    """検証委譲の id。成果コミット（rev）まで含めて決定的にする——成果が進めば別の委譲に
    なり、古い版の検証結果を今の版の根拠に使えない（`external_verdict_path` と同じ規律）。"""
    base = _board_delegation_id(task, cfg)
    tail = re.sub(r"[^A-Za-z0-9_-]+", "", str(rev or ""))[:8]
    return f"{base}-vfy{('-' + tail) if tail else ''}"[:64]


def _verification_request(task: "Task", cfg: "Config", criteria: "list[str]",
                          reasons: str) -> str:
    """検証委譲の依頼文。**確かめて報告することだけ**を頼む（直すことは頼まない）——
    成果はもう出来ており、足りないのは「この端末では確かめられなかった」という事実だけ。
    直す作業まで頼むと、依頼側が知らないうちに成果が変わる（合意の外の変更）。"""
    lines = [f"# 検証の依頼: {task.title or task.id}", "",
             "別の端末で作られた成果が、次の受入基準を満たしているかを**確かめて報告**して"
             "ください。**成果物を変更しないでください**（直す必要があると分かった場合は、"
             "その理由を報告に書いてください）。", "", "## 受入基準"]
    lines += [f"{i}. {c}" for i, c in enumerate(criteria, 1)]
    lines += ["", "## この端末で確かめられなかった理由（依頼元）", reasons or "（記録なし）", "",
              "## 報告の仕方",
              "- 基準ごとに、確かめた手順（コマンド）と出力を証跡として残してください。",
              "- すべての基準を満たしていれば成功として終えてください。",
              "- 1 つでも満たしていなければ、どの基準がなぜ満たされていないかを書いて"
              "失敗として終えてください。"]
    return "\n".join(lines)


def delegate_verification(cfg: "Config", task: "Task", verification: dict,
                          reasons: str, cycle: int) -> bool:
    """「このノードでは確かめられない」基準の検証を板へ公示する（P4-b・S5-2 の (a)）。

    公示できたら True（タスクは `offloaded` で結果待ちになる）。板が無い・成果の在処が
    分からない・板が落ちている場合は False を返し、呼び出し側は従来どおり人へ回す
    ——**人検収は最後の手段**であって既定ではない（C3: 機械で試せる解決を先に試す。
    C5: 人の検収に品質判定そのものを負わせない）。

    委譲するのは**検証だけ**で、成果の変更は依頼しない（`_verification_request`）。
    公示には `verification_plan`（digest 付き）を載せる——請負側の agent-flow は同じ plan を
    専用 runner で実行して receipt を返し、依頼側はそれを内蔵 verifier と同じ検算に通す。
    plan を載せずに「板が成功終端で終わった」を根拠にしていた頃は、証跡が 1 つも無い pass が
    done へ通っていた。受理点（`verifications/<task-id>/<rev>.external.json`）に置くのは
    返ってきた receipt そのもので、次の settle がその rev の判定として検算する。"""
    if not str(getattr(cfg, "board", "") or "").strip():
        return False
    criteria = [c["text"] for c in (verification.get("criteria") or [])
                if c.get("verdict") == "unverifiable"] or task_acceptance(task)
    if not criteria:
        return False
    plan = build_task_verification_plan(cfg, task)
    if not plan:
        # 検証材料が無い＝請負側に確かめる対象を渡せない。委譲せず人へ回す。
        return False
    rev = git_change_baseline(cfg.workdir)[0] or ""
    if not rev:
        # rev が取れない＝受理点（rev ごとの記録）を作れない。委譲しても結果を今の成果に
        # 結び付けられないので、素直に人へ回す。
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} 検証委譲は見送り（成果の版が"
                                    "特定できないため）")
        return False
    did = _verify_delegation_id(task, cfg, rev)
    spec = _workspace_spec_for(cfg, task)
    try:
        board = BoardRepo(cfg.board, workdir=cfg.board_workdir)
        board.sync_pull()
        env = task_to_delegation(task, spec, workload="flow", delegation_id=did,
                                 request=_verification_request(task, cfg, criteria, reasons),
                                 references=task_reference_specs(cfg, task))
        env["title"] = f"検証: {task.title or task.id}"
        env["verification_plan"] = plan   # 請負側 agent-flow の runner が同じ plan を実行する
        if board.write_post(env):
            board.sync_push(f"post {did}（検証委譲）")
    except (OSError, RuntimeError, ValueError) as e:
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} 検証委譲に失敗（人へ回します）: {e}")
        return False
    task.set("verify_rev", rev)          # 受理点の照合キー（結果はこの版の検証として受ける）
    task.set("verify_plan_digest", str(plan.get("digest") or ""))   # 返り receipt の照合キー
    task.drop("env_resume")              # 人の approve 待ちではない（板の結果待ち）
    _mark_offloaded(cfg, task, VERIFY_DELEGATION_LOC, did)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 検証委譲（{did}・基準 "
                                f"{len(criteria)} 件を板へ）")
    return True


def _board_result_once(board: "BoardRepo", did: str) -> "tuple[bool, bool, str]":
    """board の result.json を1回だけ読む（待たない）。(terminal, ok, msg)。
    _flow_result_once と同じ契約: terminal=確定したか・ok=成功終端（done）か・
    cancelled/failed は ok=False（未終端は毎回 sync_pull 済みの呼び出し元が次パスで再確認）。
    cancelled は 2 経路ある: cancelled.json（入札前・依頼者の中止）と result.json の
    status（実行中に人が中止。agent_flow/agent_amigos の report_board_results が
    自エンジンの cancelled 終端をそのまま書き戻す）— どちらもメッセージを
    "cancelled" で終える（_reap_offloaded の人中止判定 endswith と一致させる。
    語彙統一（W0-9）以降 flow 側も "cancelled" に揃っており、翻訳は不要）。"""
    if board.is_cancelled(did):
        return (True, False, f"board delegation {did} cancelled")
    res = board.read_result(did)
    if not res:
        return (False, False, "")
    status = str(res.get("status") or "done")
    if status == "failed":
        return (True, False, f"board delegation {did} failed（winner={res.get('winner', '?')}）")
    if status == "cancelled":
        return (True, False, f"board delegation {did}（winner={res.get('winner', '?')}）cancelled")
    return (True, True, f"board delegation {did} done（winner={res.get('winner', '?')}）")


def act_via_agent_flow(task: Task, cfg: "Config", location: str = "local") -> "tuple[bool, str]":
    """location（local/board）に応じて agent-flow（または委譲公示板）へ委譲する。

      local  → run（単発・自己完結）。orchestrator が自分で生存リースを張り park も面倒見るので、
               駆動を代行する常駐プロセスは要らない。
      board  → 委譲公示板へ post（非ブロッキング）。請負側の board 参加者が入札・実行し、
               結果は board の result.json をポーリングして回収する（依頼側の自動配線・opt-in）

    例外: 再開可能な last_run（＝この PC で途中まで進んだ run）があるときは location に依らず
    run（同期）へ寄せて続きから進める。board 由来の last_run（dg-…）は agent-flow の req-id 形式
    （req-…）と一致しないため、この特例には自然に当たらない。
    """
    last = str(task.get("last_run") or "").strip()
    if last and run_id_for(cfg, task) == last and _run_resumable(cfg, last):
        return _act_run(task, cfg, use_git=False)
    if location == "board":
        return _act_board(task, cfg)
    return _act_run(task, cfg, use_git=False)


# ---------------------------------------------------------------------------
# 委譲 executor（gitlab 等）のやり直し連携。
#   gitlab executor は「関連 MR が全マージ＝承認 / 一つでも未マージクローズ＝却下」を判定し、
#   却下時は人コメント（無ければ自動判断）を `[gitlab-reject]` 付きで失敗にする。agent-flow run は
#   failed で非 0 終了し、agent-project は verify=NG 相当として通常リトライする。その際、却下時の
#   人コメントを次 act の feedback に注入して活かす。
# ---------------------------------------------------------------------------
_REJECT_MARK = "[gitlab-reject]"


def executor_delegates(cfg: "Config") -> bool:
    """この executor が外部（人）へ委譲し、却下→やり直しのコメント連携を要するか。
    組み込み agent/stub はローカル完結＝対象外。"""
    return cfg.executor not in ("agent", "stub")


def read_reject_guidance(cfg: "Config", use_git: bool, run_id: str = "") -> str:
    """指定 run（無ければ直近）のノード結果から却下のやり直し指示（人コメント）を取り出す。
    `agent-flow result --json` を読むだけ（決定的）。まず構造化 data
    （decision=rejected の guidance。gitlab executor が却下例外に載せる）を見て、
    無ければ従来どおり output の `[gitlab-reject]` マーカーから取り出す（後方互換）。
    見つからなければ空（＝自動判断）。run_id を渡さないと共有バスで別タスクの結果を
    拾い得るので、settle 側は last_run を渡す。"""
    if not executor_delegates(cfg):
        return ""
    cmd = _kf_base(cfg, use_git) + ["result", "--json"]
    rid = str(run_id or "").strip()
    if rid:
        cmd += ["--run-id", rid]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=60,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return ""
    for n in data.get("final_nodes", []):
        d = (n or {}).get("data")
        if isinstance(d, dict) and d.get("decision") == "rejected":
            g = str(d.get("guidance") or "").strip()
            if g:
                return g[:1500]
    for n in data.get("final_nodes", []):
        out = str((n or {}).get("output", ""))
        i = out.find(_REJECT_MARK)
        if i >= 0:
            return out[i + len(_REJECT_MARK):].strip()[:1500]
    return ""


def read_result_notes(cfg: "Config", use_git: bool, run_id: str = "") -> "list[dict]":
    """指定 run（無ければ直近）のノード結果 data.notes（gitlab executor が載せる**人コメント**）を集める。
    承認/却下いずれの決着でも、人/エージェント判別済みの人コメントだけが載っている（判別は executor 側）。
    重複排除は note_id で行う。agent-flow result --json を読むだけ（決定的）。"""
    if not executor_delegates(cfg):
        return []
    cmd = _kf_base(cfg, use_git) + ["result", "--json"]
    rid = str(run_id or "").strip()
    if rid:
        cmd += ["--run-id", rid]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=60,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return []
    seen, out = set(), []
    for n in data.get("final_nodes", []):
        for note in ((n or {}).get("data") or {}).get("notes", []) if isinstance((n or {}).get("data"), dict) else []:
            if not isinstance(note, dict):
                continue
            nid = note.get("note_id")
            key = nid if nid is not None else str(note.get("body", ""))[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(note)
    return out


def read_brief_discoveries(cfg: "Config", use_git: bool, run_id: str = "") -> "list[str]":
    """指定 run（無ければ直近）のノード結果 `data.constraints`（各ノードが実行中に発見した恒常制約）を集める。
    回収先は run ブリーフ。read_result_notes（gitlab 却下/承認の人コメント）と違い、**委譲/組み込み
    executor いずれでも**読む（ローカルの agent executor でも一貫性制約は発生するため）。集約（sink）
    ノードが `data.constraints` に配列で載せる契約（build_request がその提示を要求する）。
    agent-flow result --json を読むだけ（決定的）。重複は本文で排除する。settle 側は last_run を渡す
    （共有バスで別タスクの結果を拾わないため）。"""
    cmd = _kf_base(cfg, use_git) + ["result", "--json"]
    rid = str(run_id or "").strip()
    if rid:
        cmd += ["--run-id", rid]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=60,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return []
    out: "list[str]" = []
    seen: "set[str]" = set()
    for n in data.get("final_nodes", []):
        d = (n or {}).get("data")
        items = d.get("constraints") if isinstance(d, dict) else None
        if not isinstance(items, list):
            continue
        for c in items:
            if isinstance(c, dict):
                s = str(c.get("text") or c.get("constraint") or c.get("rule") or "").strip()
            else:
                s = str(c or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


# 検証済み verify ライブラリ（`verify_lib_path` / `save_validated_verify` /
# `find_learned_verify`）は S5 で廃止した。「実績のあるコマンドを再利用する」発想は、
# **昇格したコマンドが劣化した検証でも人には見抜けない**という根本問題を解決しないため。
# 一時期の置き換えだった `verify-recipes/`（旧 verifier への参考情報）も、旧 verifier の
# 撤去（P1-A8）とともに廃止した——検証は agent-flow runner の receipt が唯一の根拠。


def capture_approve_learn(cfg: "Config", task: "Task", location: str) -> None:
    """承認決着（done）時、gitlab result の人コメント notes（正例）を横断 learn 化する。
    従来 done では人コメントを還元せず承認時の良い指摘を捨てていた。判別済みの人コメントだけが
    notes に載る（判別は executor 側 _human_notes）。learn_capture off や委譲でない場合は何もしない。"""
    if not (cfg.learn_capture and executor_delegates(cfg)):
        return
    bodies = [str(n.get("body") or "").strip()
              for n in read_result_notes(cfg, location == "remote",
                                         run_id=str(task.get("last_run") or ""))]
    guidance = "\n".join(b for b in bodies if b)[:1500]
    if not guidance:
        return
    append_decision(cfg, task.id, "gitlab",
                    context=f"{task.id}（{task.title}）が gitlab で承認",
                    action="gitlab-approve", reason=guidance[:300],
                    affects=f"{task.id} → done",
                    learn=distill_learn(cfg, task.title, guidance))


def _distill_prompt(title: str, guidance: str) -> str:
    return (
        "次は、あるタスクに対して**人間が残したフィードバック/指摘**です。これを、"
        "**類似タスクにも再利用できる一般化した学習ルール**に蒸留してください。\n"
        "規則: ①タスク固有の固有名詞（イシュー番号・特定ファイル名等）は種別・パターンへ引き上げる "
        "②『どういう種類のタスクで/何に気をつけるべきか』を一文で ③一過性の相談・雑談は蒸留対象外"
        "（その場合は空行のみ返す）。\n"
        f"タスク: {title}\nフィードバック: {guidance}\n\n"
        "出力は `<一般化した条件> :: <再利用可能な指針>` の 1 行のみ（説明・コードフェンス不要）。")


def distill_learn(cfg: "Config", title: str, guidance: str, agent_run=None) -> "tuple[str, str]":
    """人コメント（guidance）を `(条件, 指針)` の一般化ルールへ蒸留する（ltm-use の consolidate 相当）。
    エージェント CLI 委譲。失敗・不能・一過性判定は **生 verbatim フォールバック**（劣化しても現状より前進）。
    返り値は append_decision(learn=) にそのまま渡せる (title, guide)。"""
    verbatim = (title, guidance.replace("\n", " ⏎ ").strip()[:400])
    if not cfg.distill_learn:                       # 蒸留 off＝従来どおり生の指摘を learn 化
        return verbatim
    run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="distill"))
    try:
        out = run(_distill_prompt(title, guidance), cfg.model)
    except Exception:  # noqa: BLE001  エージェント CLI 不在・タイムアウト等
        return verbatim
    for line in (out or "").splitlines():
        line = _strip_code(line.strip())
        if not line or line.startswith("#"):
            continue
        if "::" in line:
            cond, _, guide = line.partition("::")
            cond, guide = cond.strip(), guide.strip()
            if cond and guide:
                return (cond, guide)
        return verbatim                             # 蒸留形式でない最初の行＝失敗扱い
    return verbatim                                 # 空出力（一過性判定含む）＝生で残す


# ---------------------------------------------------------------------------
