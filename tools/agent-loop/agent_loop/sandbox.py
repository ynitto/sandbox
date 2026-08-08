from __future__ import annotations
# sandbox.py — Phase 2A git worktree sandbox。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

_SANDBOX_ROOT = agent_home_subdir("", "sandboxes")
_SANDBOX_STALE_SECONDS = 24 * 3600


def resolve_git_toplevel(cwd: str | Path) -> Path | None:
    """git rev-parse --show-toplevel。repository でなければ None。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    root = (r.stdout or "").strip()
    if not root:
        return None
    try:
        return Path(root).resolve()
    except OSError:
        return None


def sandbox_dir(sandbox_id: str) -> Path:
    return _SANDBOX_ROOT / str(sandbox_id)


def sandbox_worktree_path(sandbox_id: str) -> Path:
    return sandbox_dir(sandbox_id) / "worktree"


def sandbox_registry_path(sandbox_id: str) -> Path:
    return sandbox_dir(sandbox_id) / "registry.json"


def _is_protected_path(path: Path) -> bool:
    """repo root / agents root / 未解決 path を削除対象にしない。"""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    agents = agent_home_dir().resolve()
    if resolved == agents:
        return True
    if resolved == agents.parent:
        return True
    # sandboxes ディレクトリ自体は触らない
    if resolved == _SANDBOX_ROOT.resolve():
        return True
    return False


def write_sandbox_registry(data: dict[str, Any]) -> Path:
    sid = str(data.get("id", ""))
    if not sid:
        raise ValueError("sandbox registry に id がありません")
    path = sandbox_registry_path(sid)
    _atomic_write_json(path, data)
    return path


def read_sandbox_registry(sandbox_id: str) -> dict[str, Any] | None:
    path = sandbox_registry_path(sandbox_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def create_sandbox(
    *,
    repo_root: str | Path,
    request_id: str,
    owner_pid: int | None = None,
    sandbox_id: str | None = None,
) -> dict[str, Any]:
    """git worktree add --detach で sandbox を作り registry を書く。"""
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise ValueError(f"repository root が存在しません: {root}")
    verified = resolve_git_toplevel(root)
    if verified is None or verified.resolve() != root.resolve():
        raise ValueError(f"git repository ではありません: {root}")

    sid = sandbox_id or uuid.uuid4().hex
    wt = sandbox_worktree_path(sid)
    wt.parent.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        raise RuntimeError(f"sandbox worktree が既に存在します: {wt}")

    r = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "--detach", str(wt), "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"git worktree add に失敗しました: {err}")

    now = time.time()
    reg = {
        "version": 1,
        "id": sid,
        "repo_root": str(root),
        "path": str(wt.resolve()),
        "request_id": str(request_id),
        "owner_pid": int(owner_pid if owner_pid is not None else os.getpid()),
        "status": "active",
        "created_at": now,
        "last_used_at": now,
    }
    write_sandbox_registry(reg)
    log.info("event=sandbox_created sandbox_id=%s path=%s", sid, wt)
    return reg


def worktree_is_clean(worktree: str | Path) -> bool | None:
    """porcelain 空なら True。判定不能は None。"""
    path = Path(worktree)
    if not path.is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return not bool((r.stdout or "").strip())


def cleanup_sandbox(
    sandbox_id: str,
    *,
    retain_reason: str | None = None,
) -> str:
    """クリーンなら remove、dirty/失敗は保持。戻り値は最終 status。"""
    reg = read_sandbox_registry(sandbox_id)
    if reg is None:
        return "missing"
    wt = Path(str(reg.get("path") or sandbox_worktree_path(sandbox_id)))
    repo_root = Path(str(reg.get("repo_root") or ""))
    now = time.time()
    reg["owner_pid"] = None
    reg["last_used_at"] = now

    if retain_reason:
        reg["status"] = retain_reason
        write_sandbox_registry(reg)
        log.info("event=sandbox_retained sandbox_id=%s reason=%s", sandbox_id, retain_reason)
        return retain_reason

    if _is_protected_path(wt) or (repo_root and wt.resolve() == repo_root.resolve()):
        reg["status"] = "cleanup_failed"
        write_sandbox_registry(reg)
        log.warning("sandbox cleanup: 保護 path のため削除しません: %s", wt)
        return "cleanup_failed"

    clean = worktree_is_clean(wt)
    if clean is not True:
        status = "retained_dirty" if clean is False else "cleanup_failed"
        reg["status"] = status
        write_sandbox_registry(reg)
        log.info("event=sandbox_retained sandbox_id=%s reason=%s path=%s", sandbox_id, status, wt)
        return status

    r = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "remove", str(wt)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        reg["status"] = "cleanup_failed"
        write_sandbox_registry(reg)
        log.warning("sandbox worktree remove 失敗: %s", (r.stderr or "").strip())
        return "cleanup_failed"

    # registry directory 削除（保護チェック済み）
    sdir = sandbox_dir(sandbox_id)
    try:
        if sdir.is_dir() and not _is_protected_path(sdir):
            reg_path = sandbox_registry_path(sandbox_id)
            reg_path.unlink(missing_ok=True)
            # worktree は git が消す。残りがあれば rmtree
            if wt.exists():
                shutil.rmtree(wt, ignore_errors=True)
            try:
                sdir.rmdir()
            except OSError:
                shutil.rmtree(sdir, ignore_errors=True)
    except OSError as exc:
        log.warning("sandbox directory 削除失敗: %s", exc)
        reg["status"] = "cleanup_failed"
        write_sandbox_registry(reg)
        return "cleanup_failed"
    log.info("event=sandbox_cleaned sandbox_id=%s", sandbox_id)
    return "cleaned"


def cleanup_stale_sandboxes(*, now: float | None = None) -> dict[str, int]:
    """daemon 起動時: 24h 以上未更新かつ owner PID 死亡かつ clean なものだけ掃除。"""
    ts = float(now if now is not None else time.time())
    stats = {"cleaned": 0, "retained_dirty": 0, "skipped": 0}
    root = _SANDBOX_ROOT
    if not root.is_dir():
        return stats
    for child in list(root.iterdir()):
        if not child.is_dir():
            continue
        reg = read_sandbox_registry(child.name)
        if reg is None:
            stats["skipped"] += 1
            continue
        last = float(reg.get("last_used_at") or reg.get("created_at") or 0)
        if ts - last < _SANDBOX_STALE_SECONDS:
            stats["skipped"] += 1
            continue
        owner = reg.get("owner_pid")
        if owner is not None:
            try:
                pid = int(owner)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0 and _pid_alive(pid):
                stats["skipped"] += 1
                continue
        wt = Path(str(reg.get("path") or ""))
        clean = worktree_is_clean(wt) if wt else None
        if clean is True:
            status = cleanup_sandbox(child.name)
            if status == "cleaned":
                stats["cleaned"] += 1
            else:
                stats["skipped"] += 1
        elif clean is False:
            log.warning("stale dirty sandbox を保持します: %s path=%s", child.name, wt)
            stats["retained_dirty"] += 1
        else:
            stats["skipped"] += 1
    return stats
