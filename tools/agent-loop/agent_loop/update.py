from __future__ import annotations
# update.py — zipapp 版表示・自己更新（Phase 2C）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
import io
import tempfile
import zipfile

_BUILD_INFO_NAME = "build-info.json"

_update_lock_file: "io.TextIOWrapper | None" = None


def resolve_executable_path() -> Path:
    """現在の agent-loop 実行 file の resolved path を返す。"""
    raw = Path(sys.argv[0])
    if not raw.is_absolute():
        raw = (Path.cwd() / raw).resolve()
    else:
        raw = raw.resolve()
    if raw.is_symlink():
        return raw.resolve()
    return raw


def _invoked_executable_path() -> Path:
    """symlink を解決せず、実行時に指定された path を返す。"""
    raw = Path(sys.argv[0]).expanduser()
    return raw if raw.is_absolute() else Path.cwd() / raw


def executable_hash(path: "Path | None" = None) -> str:
    target = path or resolve_executable_path()
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    return digest[:16]


def update_lock_path(path: "Path | None" = None) -> Path:
    return agent_home_dir() / "update-locks" / f"{executable_hash(path)}.lock"


class UpdateLockError(RuntimeError):
    """update 用 flock 取得失敗。"""


def _read_build_info_from_zip(path: Path) -> dict[str, Any] | None:
    if not zipfile.is_zipfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            if _BUILD_INFO_NAME not in zf.namelist():
                return None
            raw = zf.read(_BUILD_INFO_NAME).decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile):
        return None


def read_build_info(path: "Path | None" = None) -> dict[str, Any] | None:
    target = path or resolve_executable_path()
    return _read_build_info_from_zip(target)


def is_zipapp_install(path: "Path | None" = None) -> bool:
    target = path or resolve_executable_path()
    if not target.is_file():
        return False
    if target.is_symlink():
        return False
    if not zipfile.is_zipfile(target):
        return False
    return read_build_info(target) is not None


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    for _ in range(12):
        if (current / ".git").is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _git_describe(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


def _source_version() -> str:
    try:
        import agent_loop as pkg  # noqa: WPS433 — 実行時の実パッケージ位置

        pkg_dir = Path(pkg.__file__).resolve().parent
    except Exception:  # noqa: BLE001
        pkg_dir = Path.cwd()
    root = _find_git_root(pkg_dir)
    if root is not None:
        described = _git_describe(root)
        if described:
            return described
    return "dev"


def get_version(path: "Path | None" = None) -> str:
    target = path or resolve_executable_path()
    info = read_build_info(target)
    if info:
        commit = str(info.get("commit") or "").strip()
        if commit:
            return commit
    if is_zipapp_install(target):
        return "unknown"
    return _source_version()


def format_version_line(path: "Path | None" = None) -> str:
    return f"agent-loop {get_version(path)}"


def acquire_update_lock(
    path: "Path | None" = None,
    *,
    exclusive: bool,
) -> Any:
    if fcntl is None:  # pragma: no cover — POSIX 前提
        raise UpdateLockError("fcntl が利用できないため update は無効です")
    lock_path = update_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(handle.fileno(), flags | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise UpdateLockError("update lock を取得できません") from exc
    return handle


def release_update_lock(handle: Any) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def register_daemon_update_lock() -> None:
    """zipapp daemon 起動時に shared update lock を保持する。"""
    global _update_lock_file
    if fcntl is None:
        return
    exe = resolve_executable_path()
    if not is_zipapp_install(exe):
        return
    if _update_lock_file is not None:
        return
    try:
        _update_lock_file = acquire_update_lock(exe, exclusive=False)
    except UpdateLockError:
        log.warning("update shared lock を取得できませんでした")
        return
    atexit.register(release_daemon_update_lock)


def release_daemon_update_lock() -> None:
    global _update_lock_file
    if _update_lock_file is None:
        return
    release_update_lock(_update_lock_file)
    _update_lock_file = None


def _remote_commit(remote: str, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote, ref],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"git ls-remote に失敗しました: {exc}") from exc
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git ls-remote に失敗しました: {err}")
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 1 and parts[0]:
            return parts[0]
    return None


def _sparse_checkout(remote: str, branch: str, dest: Path) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    steps = [
        ["git", "init"],
        ["git", "remote", "add", "origin", remote],
        ["git", "sparse-checkout", "init", "--cone"],
        [
            "git", "sparse-checkout", "set",
            "tools/agent-loop", "tools/agent-tools/agentcore", "agents",
        ],
        ["git", "fetch", "--depth", "1", "origin", branch],
        ["git", "checkout", "FETCH_HEAD"],
    ]
    for cmd in steps:
        result = subprocess.run(
            cmd,
            cwd=str(dest),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"{' '.join(cmd)} に失敗しました: {err}")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("checkout 後の commit を解決できません")
    return (result.stdout or "").strip()


def _copy_python_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*.py"):
        rel = path.relative_to(src)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)


def _write_zipapp_main(build_dir: Path) -> None:
    (build_dir / "__main__.py").write_text(
        "from agent_loop import main\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )


def _build_zipapp_candidate(
    checkout_root: Path,
    output_path: Path,
    *,
    build_info: dict[str, Any],
    python_cmd: str,
) -> None:
    agent_loop_src = checkout_root / "tools" / "agent-loop" / "agent_loop"
    agentcore_src = checkout_root / "tools" / "agent-tools" / "agentcore" / "agentcore"
    if not agent_loop_src.is_dir():
        raise RuntimeError(f"agent_loop が見つかりません: {agent_loop_src}")

    build_dir = Path(tempfile.mkdtemp(prefix="agent-loop-build-"))
    try:
        _copy_python_tree(agent_loop_src, build_dir / "agent_loop")
        if agentcore_src.is_dir():
            _copy_python_tree(agentcore_src, build_dir / "agentcore")
        _write_zipapp_main(build_dir)
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run(
            [python_cmd, "-m", "zipapp", str(build_dir), "-o", str(output_path), "-p", f"/usr/bin/env {python_cmd}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"zipapp 生成に失敗しました: {err}")
        with zipfile.ZipFile(output_path, "a") as zf:
            zf.writestr(
                _BUILD_INFO_NAME,
                json.dumps(build_info, ensure_ascii=False, indent=2) + "\n",
            )
        output_path.chmod(output_path.stat().st_mode | 0o111)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def _verify_zipapp_import(candidate: Path) -> None:
    code = (
        "import zipimport\n"
        f"zipimport.zipimporter({str(candidate)!r}).load_module('agent_loop')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"import 検証に失敗しました: {err}")


def verify_update_candidate(candidate: Path) -> None:
    version = subprocess.run(
        [str(candidate), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if version.returncode != 0:
        err = (version.stderr or version.stdout or "").strip()
        raise RuntimeError(f"--version 検証に失敗しました: {err}")
    doctor = subprocess.run(
        [str(candidate), "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if doctor.returncode not in (0, 1):
        err = (doctor.stderr or doctor.stdout or "").strip()
        raise RuntimeError(f"doctor --json 検証に失敗しました: {err}")
    _verify_zipapp_import(candidate)


def _replace_executable(current: Path, candidate: Path) -> None:
    mode = current.stat().st_mode
    temp_path = current.parent / f".agent-loop-update-{uuid.uuid4().hex}"
    try:
        shutil.copy2(candidate, temp_path)
        os.chmod(temp_path, mode)
        os.replace(temp_path, current)
    except OSError:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def _reject_update(reason: str) -> None:
    print(f"[agent-loop] ERROR: {reason}", file=sys.stderr)
    sys.exit(1)


def cmd_update(args: argparse.Namespace) -> None:  # noqa: ARG001 — 将来の flag 用
    if fcntl is None:
        _reject_update("fcntl が利用できない環境では update は無効です")

    invoked = _invoked_executable_path()
    if invoked.is_symlink():
        _reject_update("symlink 経由の実行では update できません")
    exe = resolve_executable_path()
    if not is_zipapp_install(exe):
        if exe.is_file() and zipfile.is_zipfile(exe):
            _reject_update("zipapp に build-info.json がありません")
        _reject_update("source checkout / pip / OS package 経由では update できません")

    if not os.access(exe, os.W_OK) or not os.access(exe.parent, os.W_OK):
        _reject_update("実行 file またはインストール先 directory へ書き込みできません")

    info = read_build_info(exe) or {}
    remote = str(info.get("remote") or "").strip()
    branch = str(info.get("branch") or "main").strip() or "main"
    current_commit = str(info.get("commit") or "").strip()
    if not remote:
        _reject_update("build-info.json に remote がありません")

    lock_handle = None
    checkout_dir: Path | None = None
    candidate_dir: Path | None = None
    candidate_path: Path | None = None
    try:
        lock_handle = acquire_update_lock(exe, exclusive=True)
    except UpdateLockError:
        _reject_update("update lock を取得できません（稼働中 daemon の可能性があります）")

    try:
        try:
            remote_commit = _remote_commit(remote, branch)
            if not remote_commit:
                _reject_update("remote branch の commit を取得できません")
            if remote_commit == current_commit:
                print(f"[agent-loop] 既に最新です ({remote_commit[:12]})", file=sys.stderr)
                sys.exit(0)

            checkout_dir = Path(tempfile.mkdtemp(prefix="agent-loop-checkout-"))
            checked_out = _sparse_checkout(remote, branch, checkout_dir)
            built_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            build_info = {
                "version": int(info.get("version") or 1),
                "commit": checked_out,
                "remote": remote,
                "branch": branch,
                "built_at": built_at,
            }
            candidate_dir = Path(tempfile.mkdtemp(prefix="agent-loop-candidate-"))
            candidate_path = candidate_dir / "agent-loop.pyz"
            _build_zipapp_candidate(
                checkout_dir,
                candidate_path,
                build_info=build_info,
                python_cmd=sys.executable,
            )
            verify_update_candidate(candidate_path)
            _replace_executable(exe, candidate_path)
            print(
                f"[agent-loop] update 完了: {checked_out[:12]} "
                "(実行中プロセスは旧 code のまま。次回起動から新 code になります)",
                file=sys.stderr,
            )
            sys.exit(0)
        except Exception as exc:  # noqa: BLE001
            _reject_update(str(exc))
        finally:
            if candidate_path is not None and candidate_path.exists():
                try:
                    candidate_path.unlink()
                except OSError:
                    pass
            if checkout_dir is not None:
                shutil.rmtree(checkout_dir, ignore_errors=True)
            if candidate_dir is not None:
                shutil.rmtree(candidate_dir, ignore_errors=True)
    finally:
        release_update_lock(lock_handle)
