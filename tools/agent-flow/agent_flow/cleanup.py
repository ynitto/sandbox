from __future__ import annotations
# cleanup.py — 元 agent-flow.py の 5425-5670 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# cleanup — 一時ファイルの自動掃除（ロック / 中間 .tmp / 孤立クローン）
# --------------------------------------------------------------------------
# バス内の run（gc が掃除する）とは別に、agent-flow は「バス外の一時ファイル」を
# 残す。これらは削除処理が無く溜まり続けるため、daemon ループから定期掃除する。
#   A) $TMPDIR/agent-flow-locks/*.lock        … claim/daemon の排他ロック
#   B) <path>.tmp.<pid>                       … write_json_atomic の中間ファイル（crash 残骸）
#   C) {bus}/<node>/                          … git モードのノード別クローン（run 終了後に孤立）
_TMP_SUFFIX_RE = re.compile(r"\.tmp\.(\d+)$")


def _locks_root() -> str:
    return os.path.join(tempfile.gettempdir(), "agent-flow-locks")


def _pid_alive(pid: int) -> bool:
    """pid のプロセスが存命か。判定不能なら安全側で True を返す。
    Windows の os.kill(pid, 0) は生死判定として信頼できない（不在 pid でも
    PermissionError になる等）ため、tasklist で存在確認する。"""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover — Windows のみ
        try:
            p = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                               capture_output=True, text=True, timeout=10)
            return f'"{pid}"' in (p.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 別ユーザのプロセス＝存在はする
        return True
    except OSError:
        return True
    return True


def sweep_lock_files(min_age_sec: float = 3600.0) -> int:
    """$TMPDIR/agent-flow-locks/ の使われていない .lock を削除し、削除数を返す。
    保持中のロックを消すと排他が壊れるため、(1) 十分古い（min_age_sec 以上アイドル）
    かつ (2) flock を非ブロッキングで取得できた（＝誰も保持していない）ものに限る。"""
    d = _locks_root()
    if not os.path.isdir(d):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(d):
        if not name.endswith(".lock"):
            continue
        path = os.path.join(d, name)
        try:
            if now - os.path.getmtime(path) < min_age_sec:
                continue  # 最近使われた → 残す
            f = open(path, "a")  # "a": 既存内容を切り詰めない（保持中でも無害）
        except OSError:
            continue
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue  # 保持中 → 残す（finally で close）
            elif msvcrt is not None:  # pragma: no cover — Windows のみ
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    continue  # 保持中 → 残す（年齢だけで消すと排他が壊れる）
            os.remove(path)
            removed += 1
        except OSError:
            pass
        finally:
            f.close()
    return removed


def sweep_tmp_files(root: str, min_age_sec: float = 300.0) -> int:
    """write_json_atomic が残した <path>.tmp.<pid> の残骸を掃除し、削除数を返す。
    正常時は即 os.replace されるので、残存＝書き込み中かクラッシュ由来。書き込み元 pid が
    死んでいる、または min_age_sec 以上古いものを消す（.git 配下は触らない）。"""
    if not os.path.isdir(root):
        return 0
    removed = 0
    now = time.time()
    for dirpath, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")  # git 内部には踏み込まない
        for fn in files:
            m = _TMP_SUFFIX_RE.search(fn)
            if not m:
                continue
            path = os.path.join(dirpath, fn)
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if _pid_alive(int(m.group(1))) and age < min_age_sec:
                continue  # 生存プロセスが書き込み中かも → 残す
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


_WORK_REPO_DIR_RE = re.compile(r"^agent-flow-ws-(\d+)-")


def sweep_work_repo_dirs(min_age_sec: float = 3600.0) -> int:
    """SIGKILL/OOM/電源断で finally が走らず残ったワークスペースの孤立 clone を回収し、削除数を返す。
    名前に埋めた pid（`agent-flow-ws-<pid>-…`）で所有プロセスの生死を判定し、**死んでいるものだけ**消す
    （稼働中・`--keep-alive` 長命 worker の clone は残す）。pid 再利用の誤判定を避けるため min_age も併用。"""
    root = tempfile.gettempdir()
    if not os.path.isdir(root):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(root):
        m = _WORK_REPO_DIR_RE.match(name)
        if not m:
            continue
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        try:
            age = now - os.path.getmtime(sub)
        except OSError:
            continue
        if _pid_alive(int(m.group(1))):
            continue  # 所有プロセス生存（--keep-alive 長命 worker 含む）→ 経過時間に関わらず残す
        if age < min_age_sec:
            continue  # 死亡判定でも作成直後は残す（pid 再利用の誤判定・終了直前 race の保険）
        shutil.rmtree(sub, ignore_errors=True)
        removed += 1
    return removed


def sweep_clone_dirs(bus_parent: str, keep_basename: str, min_age_sec: float) -> int:
    """git モードでノードごとに作られた孤立クローン（{bus}/<node>/）を削除し、削除数を返す。
    最近 git 操作のあったクローン（mtime が新しい＝稼働中）と、稼働デーモン自身の
    クローン（keep_basename）は残す。クローン以外（runs/inbox 等）は .git の有無で除外。"""
    if not os.path.isdir(bus_parent):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(bus_parent):
        if name == keep_basename:
            continue
        sub = os.path.join(bus_parent, name)
        gitdir = os.path.join(sub, ".git")
        if not os.path.exists(gitdir):
            continue  # クローンでない → 触らない
        try:
            ref = max(os.path.getmtime(sub), os.path.getmtime(gitdir))
        except OSError:
            continue
        if now - ref < min_age_sec:
            continue  # 最近使われた → 残す
        shutil.rmtree(sub, ignore_errors=True)
        removed += 1
    return removed


def run_cleanup(args, bus: Bus) -> dict:
    """A/B/C の一時ファイルをまとめて掃除し、{種別: 削除数} を返す。
    ロックは lease の 2 倍（最低 1h）アイドルなら確実に未使用。クローンは cleanup_age 時間。"""
    bus_parent = os.path.abspath(args.bus)
    lock_age = max(float(args.lease) * 2.0, 3600.0)
    n_lock = sweep_lock_files(lock_age)
    n_tmp = sweep_tmp_files(bus_parent)
    n_clone = 0
    if getattr(args, "git", None):  # 孤立クローンは git モードのみ存在する
        keep = os.path.basename(bus.workdir) if isinstance(bus, GitBus) else ""
        n_clone = sweep_clone_dirs(bus_parent, keep, float(args.cleanup_age) * 3600.0)
    # 成果物リポジトリの孤立 temp clone（pid 死亡）を回収（SIGKILL リーク対策・local/git 共通）
    n_work = sweep_work_repo_dirs(float(args.cleanup_age) * 3600.0)
    # 共有 git キャッシュ: 生存 worktree を prune し、長期未使用のミラーを回収
    n_cache = sweep_cache_dirs(float(args.cleanup_age) * 3600.0)
    return {"locks": n_lock, "tmp": n_tmp, "clones": n_clone,
            "work_repos": n_work, "cache": n_cache}


# --------------------------------------------------------------------------
# gc — 古い run を掃除
# --------------------------------------------------------------------------
def _age_hours(meta) -> float:
    # run メタは updated_at/created_at、inbox 要求レコードは submitted_at を持つ（両方に使える）。
    ts = meta.get("updated_at") or meta.get("created_at") or meta.get("submitted_at")
    if not ts:
        return float("inf")  # タイムスタンプ無し＝十分古いとみなす
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def cmd_gc(args) -> int:
    bus = make_bus(args, "gc")
    bus.sync_pull()
    runs = bus.list_runs()
    metas = [(rid, bus.run_meta(rid)) for rid in runs]
    # 新しい順に並べ、先頭 keep 件は無条件で保護
    metas.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)

    to_delete = []
    for i, (rid, meta) in enumerate(metas):
        if i < args.keep:
            continue
        if _age_hours(meta) < args.older_than * 24.0:
            continue
        if args.status and meta.get("status") != args.status:
            continue
        to_delete.append((rid, meta))

    for rid, meta in to_delete:
        tag = "[dry-run] " if args.dry_run else ""
        print(f"{tag}削除: {rid} (status={meta.get('status')}, age={_age_hours(meta):.1f}h)")
        if not args.dry_run:
            bus.remove_run(rid)

    # 孤児 inbox 要求の掃除: run を伴わない inbox 要求は、daemon がこれを「新規要求」と誤認して
    # 再び orchestrator を起動し **不要な run を走らせる**原因になる（受理ゲートは run_exists のみ）。
    # remove_run は対応 inbox を消すので通常は run と一緒に片付くが、旧バージョンや外部ツールが
    # run だけ消した／crash 等で取り残された要求は掃除されず残る。ここで run が無く十分古く、かつ
    # 現在 claim されていない（lease 内で担当 daemon が処理中でない）要求を掃除する。フレッシュな
    # 未受理要求（--older-than 未満）は正規の受理待ちとして保護し、--status 指定時は「run の status で
    # 絞る」意図なので触らない。
    reaped = []
    if not args.status:
        for req_id in bus.list_inbox():
            if bus.run_exists(req_id):
                continue                          # run があるものは上の run-gc が対応（inbox も一緒に消える）
            rec = bus.read_inbox(req_id) or {}
            if _age_hours(rec) < args.older_than * 24.0:
                continue                          # まだ新しい＝受理待ちの正規要求かも → 保護
            claim_dir = os.path.join(bus.inbox_claims_dir, req_id)
            if bus._winner_in(claim_dir) is not None:
                continue                          # lease 内で担当 daemon が処理中 → 触らない
            reaped.append(req_id)
    for req_id in reaped:
        tag = "[dry-run] " if args.dry_run else ""
        age = _age_hours(bus.read_inbox(req_id) or {})
        print(f"{tag}孤児 inbox 掃除: {req_id}（run 無し・{age:.1f}h前）")
        if not args.dry_run:
            bus.remove_run(req_id)                # run 無しでも inbox 要求・claim・cancel を消す

    if (to_delete or reaped) and not args.dry_run:
        bus.sync_push(f"gc: removed {len(to_delete)} run(s), {len(reaped)} orphan inbox")
    tail = f" ＋ 孤児 inbox {len(reaped)} 件" if reaped else ""
    print(f"削除 {len(to_delete)} / 全 {len(runs)} runs{tail}"
          f"{'（dry-run）' if args.dry_run else ''}")
    if len(to_delete) == 0 and len(runs) > 0:
        oldest_h = max(_age_hours(m) for _, m in metas) if metas else 0
        print(f"ヒント: --keep {args.keep} で全件保護中、最古 run は {oldest_h:.1f}h前。"
              f" --keep 0 --older-than 0 で全件を対象にできます。")
    return 0
