from __future__ import annotations
# flow.py — 元 agent-project.py の 4100-4640 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
def _new_run_id(task: "Task", cfg: "Config") -> str:
    """この試行の run-id。viewer が run ↔ タスクを突き合わせられる形にする
    （req-<hash>-<task-id>-r<retries>[-v<rev>]。dashboard の parseRunId / lineage もこの形を前提）。

    daemon submit（_req_id_for）と同一導出にする。かつては hash(task.id) だったため、
    同期 run と daemon/offload で同じタスクが別 lineage に割れて UI の系統まとめが壊れていた。"""
    return _req_id_for(task, cfg, task.retries)


_FLOW_TERMINAL = ("done", "failed", "canceled")

# リース未記録の非終端 run を「停滞」とみなすまでの猶予。agent-flow の worker は 1 ノードに
# 数分かかるので、生きている run を誤って停滞と読まない程度に長く取る。
_STALE_RUN_SEC = 600.0


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
        return False                      # done / canceled は作り直す
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
    plan_changed = bool(task.get("revised")) or (
        bool(task.get("feedback")) and not task.get("env_resume"))
    if rid and not plan_changed and _run_resumable(cfg, rid):
        return rid                        # 失敗・停滞した所だけやり直す（done は温存）
    return _new_run_id(task, cfg)


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


def daemon_lock_path(cfg: "Config", use_git: bool) -> Path:
    """agent-flow daemon の singleton ロックパス（agent-flow と同一規則）。

    外部起動の daemon を取りこぼさないため、agent-flow と完全に同じ導出をする:
      - ロック置き場は設定 `lock_dir`（無ければ tempdir 配下）
      - local キーは realpath で canonical 化（symlink/相対パスのズレを吸収）"""
    if use_git and cfg.git_bus:
        key = f"git::{cfg.git_bus}@{cfg.git_branch}/{cfg.git_subdir or ''}"
    else:
        key = "local::" + os.path.realpath(str(cfg.bus))
    h = hashlib.sha1(key.encode()).hexdigest()
    base = cfg.lock_dir or str(Path(tempfile.gettempdir()) / "agent-flow-locks")
    return Path(base) / f"daemon-{h}.lock"


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


def _lock_pid(p: Path) -> int:
    """ロックファイル先頭行の pid を読む（agent-flow daemon が記録）。読めなければ 0。"""
    try:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return 0
    try:
        return int(lines[0]) if lines else 0
    except ValueError:
        return 0


def _flock_held(p: Path) -> "bool | None":
    """flock の保持状況。True=保持中 / False=未保持 / None=判定不能（fcntl 無し・非対応FS 等）。"""
    if fcntl is None:
        return None
    try:
        f = open(p, "r+")
    except OSError:
        return None
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        return False           # 取得できた = 誰も保持していない
    except BlockingIOError:
        return True            # 保持されている = daemon 稼働中
    except OSError:
        return None            # flock 非対応FS 等 → pid で判定へ
    finally:
        f.close()


def daemon_running(cfg: "Config", use_git: bool = False) -> bool:
    """対象バスの agent-flow daemon が稼働中かを判定する。
    flock を第一の根拠とし、判定不能（fcntl 無し / 異種FS）なら daemon が記録した
    pid の生存で補完する。これで外部起動・Windows・NFS 上の daemon も発見できる。"""
    p = daemon_lock_path(cfg, use_git)
    if not p.exists():
        return False
    held = _flock_held(p)
    if held is not None:
        return held
    return _pid_alive(_lock_pid(p))


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
    既定の終端は canceled（人の停止・軌道修正＝次 run は inherit しない）。
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
    end_status = "failed" if failed else "canceled"

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
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
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
    # canceled: 先にマーカー（人の停止意図を同期）してから終端化。
    if failed:
        _apply_terminal()
        _write_cancel_marker()
    else:
        _write_cancel_marker()
        _apply_terminal()
    # meta を触れたときだけマーカーを消す。meta 無し（まだ submit 前）は残し、
    # daemon の run 化前 cancel（cancel_request_run）へ渡す。
    if applied:
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
    deadline = (time.time() + cfg.act_timeout) if cfg.act_timeout > 0 else None
    # 同期 run は agent-project のループを塞ぐため、従来は run 完了まで state_git が push
    # されず、別 PC の dashboard/engine が bus/runs の graph・claims・results を見られなかった。
    # 待機中も state_git_interval ごとに best-effort 同期して、同期 run のままでも分担/監視できる
    # 回避路を作る（force しないのでリモート負荷は既存 interval に従う）。
    next_progress_sync = 0.0
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                drainer.join(timeout=2.0)
                break
            abort = _wait_abort_reason(cfg, task, rid)
            if abort:
                why = abort
                task.set("flow_run", rid)
                detach_flow_run(cfg, task, f"{why} により同期 run を中断")
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except OSError:
                        pass
                # 刈り残した orch/worker は daemon を残して止める（外部 daemon 全滅を避ける）
                reap_orphan_flow(cfg, include_daemon=False)
                return (False, f"daemon run {rid} の結果待ちを中断（{why} を検知）")
            now = time.time()
            if now >= next_progress_sync:
                sync = globals().get("state_sync")
                if sync is not None:
                    try:
                        sync(cfg, force=False)
                    except Exception:  # noqa: BLE001 - state_sync 自体も best-effort。run は止めない。
                        pass
                next_progress_sync = now + max(1.0, float(getattr(cfg, "state_git_interval", 300.0) or 300.0))
            if deadline is not None and now >= deadline:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except OSError:
                        pass
                # submit タイムアウトと同じ: 対象 run を止めて外部 daemon 採用を防ぐ。
                # failed（canceled ではない）＝次リトライで done ノードを inherit できる。
                task.set("flow_run", rid)
                detach_flow_run(cfg, task, f"agent-flow run タイムアウト（{cfg.act_timeout}s）",
                                failed=True)
                reap_orphan_flow(cfg, include_daemon=False)
                return (False, f"agent-flow run タイムアウト（{cfg.act_timeout}s）")
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
    # 同期 run の canceled は exit≠0 でもメッセージが日本語のため、meta で確定して
    # 上位の canceled 特別扱い（リトライ非消費で ready）へ乗せる。
    try:
        meta = json.loads((cfg.bus / "runs" / rid / "meta.json").read_text(encoding="utf-8"))
        if str(meta.get("status") or "") == "canceled":
            return (False, f"daemon run {rid} canceled")
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


def _task_file_revised(cfg: "Config", task: Task) -> bool:
    """実行中の revise（軌道修正）が入ったか＝backlog ファイルに `revised` マーカーがあるか。
    act の結果待ちループから毎ポーリング呼ばれるため、小さなファイル読みだけで判定する。"""
    fresh = _load_task_file(cfg, task.id)
    return fresh is not None and bool(fresh.get("revised"))


def _wait_abort_reason(cfg: "Config", task: Task, run_id: str) -> "str | None":
    """同期結果待ちを打ち切るべき人操作があればその理由、無ければ None。

    revise 以外（approve / hold / reject / feedback）は `revised` 無しで status / flow_run
    だけ変える。flow_run を待ち開始時にピンしておき、外れたら中断する。
    status だけで中断しない（ピン時点の status 揺れや ready 表記残りを false-positive にしない）。
    flow_run が残ったまま status だけ変わるのは、人が別操作で上書きしたケースとして
    flow_run 不一致・欠落と合わせて検知する。"""
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


def _submit_req_id(task: Task, cfg: "Config") -> str:
    """リブート跨ぎで同じ act 試行へ再接続するための決定的 req_id。

    （backlog パス, task.id, retries）で一意にする——PC のシャットダウン等で submit の
    待機ごと消えても、再起動後の同じ試行は同じ req_id を再 submit するため、agent-flow 側の
    既存 run（daemon が孤児を自動再開する）に合流して結果を受け取れる＝二重実行しない。
    リトライ（retries+1）は新しい試行＝新しい run。backlog パスの hash は共有バスに
    複数プロジェクトが乗るときの衝突を防ぐ。人の revise（rev 世代）も新しい試行＝
    新しい run にする（軌道修正後の act が修正前の古い run に合流しないように）。"""
    return _req_id_for(task, cfg, task.retries)


def _req_id_for(task: Task, cfg: "Config", retries: int) -> str:
    """指定 retries 世代の決定的 req_id（_submit_req_id の一般化）。"""
    h = hashlib.sha1(str(cfg.backlog.resolve()).encode()).hexdigest()[:8]
    tid = re.sub(r"[^\w.-]+", "_", str(task.id))[:60]
    rev = str(task.get("rev", "") or "").strip()
    return f"req-{h}-{tid}-r{retries}" + (f"-v{rev}" if rev else "")


def _inherit_from_run(task: Task, new_run_id: str, cfg: "Config | None" = None) -> "str | None":
    """新 run へ引き継ぐ先行 run-id。`last_run` が新 id と違えばそれを使う。

    `_prev_req_id`（retries-1・現 rev）だと revise で rev が上がったあと、実在しない
    `…-r{N-1}-v{newRev}` を指して inherit が空振りする。last_run が実際の先行。
    canceled の last_run は引き継がない（人の停止・軌道修正を尊重。done を蘇らせない）。
    タイムアウト等の failed は引き継ぐ（agent-flow inherit_from と同じ契約）。"""
    last = str(task.get("last_run") or "").strip()
    if not last or last == str(new_run_id or "").strip():
        return None
    if cfg is not None:
        try:
            meta = json.loads((cfg.bus / "runs" / last / "meta.json").read_text(encoding="utf-8"))
            if str(meta.get("status") or "") == "canceled":
                return None
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return last


def _prev_req_id(task: Task, cfg: "Config") -> "str | None":
    """直前の試行の run-id（互換フォールバック）。

    呼び出し側は `_inherit_from_run` を優先すること。ここは last_run が空のときの
    retries-1 推定（同 rev）に留める。"""
    return _req_id_for(task, cfg, task.retries - 1) if task.retries > 0 else None


class _Pending:
    """act の第3の結果＝『実行層 daemon へ非ブロッキング submit 済み・まだ終端していない』。
    run_loop はこれを受けたらタスクを offloaded にして settle をスキップし、次パスでポーリングする。"""
    __slots__ = ("run_id",)

    def __init__(self, run_id: str):
        self.run_id = run_id


def _flow_result_once(cfg: "Config", use_git: bool, run_id: str) -> "tuple[bool, bool, str]":
    """agent-flow result を1回だけ読む（待たない）。(terminal, ok, msg) を返す。
    terminal=run が終端（done/failed/canceled）に達したか。
    ok=成功終端（done）か。failed / canceled は ok=False（canceled を success と取り違えない —
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
    if status == "canceled":
        return (True, False, f"daemon run {run_id} canceled")
    return (True, True, f"daemon run {run_id} done")


def _act_offload(task: Task, cfg: "Config", use_git: bool) -> "tuple":
    """非ブロッキング委譲: run が無ければ submit し、結果を1回だけ確認する。終端なら (ok, msg)、
    未終端なら (_Pending(run_id), msg) を返す（待たない）。専用 daemon が run を保持するので、
    gitlab の長期委譲でもループをブロックせず次のタスクへ進める（結果は次パスで回収する）。"""
    base = _kf_base(cfg, use_git) + _workspace_cmd_args(cfg, task) + _reference_cmd_args(cfg, task)
    run_id = _submit_req_id(task, cfg)
    prev = _inherit_from_run(task, run_id, cfg)
    if prev is None and not str(task.get("last_run") or "").strip():
        prev = _prev_req_id(task, cfg)  # last_run 空のときだけ推定（canceled skip を踏み潰さない）
    _pin_last_run(cfg, task, run_id)
    term, ok, msg = _flow_result_once(cfg, use_git, run_id)
    if not term:                                  # 未作成/実行中: 未作成なら submit（作成済みは冪等 no-op）
        inherit = ["--inherit-from", prev] if prev else []
        try:
            sub = subprocess.run(base + ["--run-id", run_id, "submit", build_request(task, cfg)]
                                 + inherit, cwd=str(cfg.workdir),
                                 timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return (False, f"submit 失敗: {e}")
        if sub.returncode != 0:
            return (False, f"submit rc={sub.returncode}: {sub.stderr.strip()[:200]}")
        term, ok, msg = _flow_result_once(cfg, use_git, run_id)   # submit 直後に一応もう一度
        if not term:
            return (_Pending(run_id), f"daemon run {run_id} 実行中（offload・非ブロッキング）")
    return (ok, msg)


def _act_submit(task: Task, cfg: "Config", use_git: bool) -> "tuple[bool, str]":
    """daemon があるとき: submit して、その run が終端に達するまで待つ（verify は待機後）。
    req_id は決定的（_submit_req_id）——リブート後の再実行は既存 run に合流する。"""
    base = _kf_base(cfg, use_git) + _workspace_cmd_args(cfg, task) + _reference_cmd_args(cfg, task)
    run_id = _submit_req_id(task, cfg)
    # pin する前に先行 run を決める（pin 後は last_run が新 id になる）
    prev = _inherit_from_run(task, run_id, cfg)
    if prev is None and not str(task.get("last_run") or "").strip():
        prev = _prev_req_id(task, cfg)
    _pin_last_run(cfg, task, run_id)
    inherit = ["--inherit-from", prev] if prev else []
    try:
        sub = subprocess.run(base + ["--run-id", run_id, "submit", build_request(task, cfg)] + inherit,
                             cwd=str(cfg.workdir),
                             timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return (False, f"submit 失敗: {e}")
    if sub.returncode != 0:
        return (False, f"submit rc={sub.returncode}: {sub.stderr.strip()[:200]}")
    out = (sub.stdout or "").strip().splitlines()
    got = out[0].strip() if out else ""
    if not got:
        return (False, "run-id を取得できません")
    if got != run_id:
        _pin_last_run(cfg, task, got)
        run_id = got
    # 待ちのあいだ approve/hold が detach できるよう flow_run をピン（offloaded と同じ契約）。
    task.set("flow_run", run_id)
    persist_task(cfg, task)
    # act_timeout=0（以下）はタイムアウト無効＝終端に達するまで待つ。gitlab 等の長時間委譲
    # （人のレビュー往復で数日かかりうる）で、待ち切れずに retry を空増やしする事故を防ぐ。
    deadline = (time.time() + cfg.act_timeout) if cfg.act_timeout > 0 else None
    while deadline is None or time.time() < deadline:
        try:
            res = subprocess.run(base + ["result", "--run-id", run_id, "--json"],
                                cwd=str(cfg.workdir), timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
            data = json.loads(res.stdout)
            if data.get("done"):
                # done=True は終端（done/failed/canceled）を意味する。failed / canceled は act
                # 失敗として扱い（canceled を success と取り違えない）、success と区別する。
                # orchestrator がクラッシュして daemon が failed に確定した場合もここで即検知でき、
                # act_timeout までの永久待機を避けられる。
                st = str(data.get("status") or "")
                task.drop("flow_run", "flow_loc")
                if st == "failed":
                    return (False, f"daemon run {run_id} failed")
                if st == "canceled":
                    return (False, f"daemon run {run_id} canceled")
                return (True, f"daemon run {run_id} done")
        except Exception:  # noqa: BLE001 — 取得失敗は次ポーリングで再試行
            pass
        abort = _wait_abort_reason(cfg, task, run_id)
        if abort:
            # 人が既に detach 済み（flow_run 無し）なら二重 cancel しない。revise はこちらで止める。
            fresh = _load_task_file(cfg, task.id)
            still = fresh is not None and str(fresh.get("flow_run") or "").strip() == run_id
            if still or abort == "revise":
                task.set("flow_run", run_id)
                detach_flow_run(cfg, task, f"{abort} により結果待ちを中断")
            else:
                task.drop("flow_run", "flow_loc")
            return (False, f"daemon run {run_id} の結果待ちを中断（{abort} を検知）")
        time.sleep(2.0)
    # daemon 自体は他 run / park 監視のオーナーなので殺さない。この run だけ cancel して止める。
    task.set("flow_run", run_id)
    detach_flow_run(cfg, task, f"daemon run タイムアウト（{cfg.act_timeout}s）", failed=True)
    return (False, f"daemon run {run_id} タイムアウト")


def _act_board(task: Task, cfg: "Config") -> "tuple":
    """委譲公示板（agent-board）への非ブロッキング公示。post が無ければ書き、結果を1回だけ確認する。
    終端なら (ok, msg)、未終端なら (_Pending(delegation_id), msg) を返す（待たない・常に非同期 —
    board は「公示して請負側の入札を待つ」性質上、remote/daemon の act_async 切替とは無関係）。
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
        env = task_to_delegation(task, spec, workload=cfg.board_workload, delegation_id=did,
                                 request=build_request(task, cfg), references=refs)
        if board.write_post(env):          # 新規のときだけ push（無駄な空 commit を作らない）
            board.sync_push(f"post {did}")
        term, ok, msg = _board_result_once(board, did)   # 直後にもう一度（同一 cycle 内解決対応）
        if not term:
            return (_Pending(did), f"board delegation {did} 公示（入札・実行待ち）")
    return (ok, msg)


def _board_result_once(board: "BoardRepo", did: str) -> "tuple[bool, bool, str]":
    """board の result.json を1回だけ読む（待たない）。(terminal, ok, msg)。
    _flow_result_once と同じ契約: terminal=確定したか・ok=成功終端（done）か・
    cancelled/failed は ok=False（未終端は毎回 sync_pull 済みの呼び出し元が次パスで再確認）。
    cancelled は 2 経路ある: cancelled.json（入札前・依頼者の中止）と result.json の
    status（実行中に人が中止。agent_flow/agent_amigos の report_board_results が
    自エンジンの canceled/cancelled 終端をそのまま書き戻す）— どちらもメッセージを
    "cancelled" で終える（_reap_offloaded の人中止判定 endswith と一致させる。
    flow 側の "canceled"（米語）とは綴りが異なる点に注意——board 語彙は "cancelled"）。"""
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
    """location（local/daemon/remote/board）に応じて agent-flow（または委譲公示板）へ委譲する。

      local  → run（単発）
      daemon → ローカル daemon に submit＋結果待ち（daemon が無ければ run にフォールバック）
      remote → git バスの remote daemon に submit＋結果待ち（オフロード。フォールバックしない）
      board  → 委譲公示板へ post（非ブロッキング）。請負側の board 参加デーモンが入札・実行し、
               結果は board の result.json をポーリングして回収する（依頼側の自動配線・opt-in）

    例外: resume-run / 失敗・停滞 run の「続きから」は submit では効かない
    （daemon は run_exists で無視し、retry_failed は cmd_run だけ）。再開可能な
    last_run があるときは location によらず run（同期）へ寄せる。board 由来の last_run（dg-…）は
    agent-flow の req-id 形式（req-…）と一致しないため、この特例には自然に当たらない。
    """
    last = str(task.get("last_run") or "").strip()
    if last and run_id_for(cfg, task) == last and _run_resumable(cfg, last):
        return _act_run(task, cfg, use_git=(location == "remote"))
    if location == "board":
        return _act_board(task, cfg)
    async_ok = bool(getattr(cfg, "act_async", False))
    if location == "remote":
        return _act_offload(task, cfg, True) if async_ok else _act_submit(task, cfg, use_git=True)
    if location == "daemon":
        if daemon_running(cfg, use_git=False):
            # 非ブロッキング（act_async）: submit して待たず次へ。専用 daemon が run を保持し、
            # 結果は次パスのポーリングで回収する（gitlab 等の長期委譲でループを塞がない）。
            return _act_offload(task, cfg, False) if async_ok else _act_submit(task, cfg, use_git=False)
        return _act_run(task, cfg, use_git=False)  # daemon 不在 → run（同期・待つ）
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


def verify_lib_path(cfg: "Config") -> Path:
    """検証済み verify（procedural memory）の格納先。DR を汚さない専用ファイル。"""
    return cfg.decisions / ".verifylib.md"


def save_validated_verify(cfg: "Config", task: "Task") -> None:
    """done 確定した**自動生成 verify**（synth/template/reused）を、タイトル付きで再利用ライブラリへ保存する。
    人が書いた verify は元から良質＝ライブラリ経由を要さない。同一 (title, cmd) は重複保存しない。"""
    if not cfg.learn_capture or not task.verify:
        return
    src = dict(task.extra).get("verify_source", "")
    if src not in ("synth", "template", "reused"):
        return
    line = f"- verifycmd: {task.title.replace(chr(10), ' ')} :: {task.verify.replace(chr(10), ' ')}\n"
    p = verify_lib_path(cfg)
    if p.exists() and line in p.read_text(encoding="utf-8"):
        return
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


_VERIFYCMD_RE = re.compile(r"^- verifycmd:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")


def find_learned_verify(cfg: "Config", task: "Task") -> "str | None":
    """検証済み verify ライブラリから、タイトルが十分似た過去の verify コマンドを返す（決定的・Jaccard）。
    毎回ゼロから合成せず、実績のある検査を再利用する（red-green が別途、変更を弁別するか実行で確かめる）。"""
    p = verify_lib_path(cfg)
    if not p.exists():
        return None
    m = _best_learn_match(task, cfg.learn_threshold, [p], label=lambda f: f.stem,
                          pattern=_VERIFYCMD_RE)
    return m[1] if m else None


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
