from __future__ import annotations
# gitcache.py — 元 agent-project.py の 9602-9796 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 共有 git キャッシュ + worktree（docs/designs/git-worktree-cache-pattern.md）
#   検証（verify/acceptance）のたびに対象 repo を浅 clone する代わりに、ホスト共有の bare ミラー
#   （--mirror --filter=blob:none）を 1 本持ち、最新化（fetch）後に detached worktree を temp へ生やす。
#   agent-flow とミラー root を共有する（KIRO_GIT_CACHE_DIR / 既定 $TMPDIR/kiro-git-cache）。
#   不変条件: INV-1 鮮度（毎 fetch→fetch 後 SHA）/ INV-2 直列化・自己修復・gc.auto=0 /
#   INV-3 失敗時は従来の浅 clone へフォールバック。
# --------------------------------------------------------------------------
CLONE_RETRIES = 5
_CACHE_CORRUPT = ("not a git repository", "bad object", "corrupt", "broken link",
                  "unable to read", "object directory", "fatal: bad")
_provisioned_urls: "set[str]" = set()


@contextlib.contextmanager
def _file_lock(path: str):
    """fcntl があれば排他ロック。無ければ no-op（ベストエフォート）。"""
    if fcntl is None:
        yield
        return
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def cache_root() -> str:
    """ホスト共有 git キャッシュの root（agent-flow と同じ既定・同じ環境変数で共有する）。"""
    return os.environ.get("KIRO_GIT_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "kiro-git-cache")


def _cache_path_for(url: str) -> str:
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    return os.path.join(cache_root(), f"{h}.git")


@contextlib.contextmanager
def _cache_lock(url: str):
    """URL 単位のホスト内ロック（INV-2: cache の全変更を直列化。agent-flow と同一パス）。"""
    root = cache_root()
    os.makedirs(root, exist_ok=True)
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    with _file_lock(os.path.join(root, f"{h}.lock")):
        yield


def _git_cache(cache: str, *args: str, timeout: float = 600):
    return subprocess.run(["git", "-C", cache, *args],
                          capture_output=True, text=True, timeout=timeout)


def _is_cache_valid(cache: str) -> bool:
    if not os.path.isdir(cache):
        return False
    try:
        return _git_cache(cache, "rev-parse", "--git-dir", timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mirror_clone(url: str, cache: str) -> bool:
    """url を blob:none の bare ミラーとして cache に作る。partial 非対応サーバには filter 無しで再試行。"""
    shutil.rmtree(cache, ignore_errors=True)
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    attempts = [["git", "clone", "--mirror", "--filter=blob:none", url, cache],
                ["git", "clone", "--mirror", url, cache]]
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            _git_cache(cache, "config", "gc.auto", "0")
            # --mirror が付ける remote.origin.mirror=true を無効化（refspec 付き push 拒否を防ぐ）。
            _git_cache(cache, "config", "remote.origin.mirror", "false")
            return True
        shutil.rmtree(cache, ignore_errors=True)
    return False


def ensure_cache(url: str) -> "str | None":
    """URL の共有 bare ミラーを用意（無ければ作成・壊れていれば再作成）。fetch はしない。要 _cache_lock。"""
    cache = _cache_path_for(url)
    if _is_cache_valid(cache):
        return cache
    for i in range(CLONE_RETRIES):
        if _mirror_clone(url, cache):
            return cache
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)
    return None


def _cache_fetch(cache: str) -> bool:
    """INV-1: 全 heads を増分 fetch（リトライ付き）。破損系は False（再ミラー誘発）。"""
    for i in range(CLONE_RETRIES):
        try:
            r = _git_cache(cache, "fetch", "--prune", "--no-tags", "origin",
                           "+refs/heads/*:refs/heads/*")
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return True
        if r is not None and any(s in (r.stderr or "").lower() for s in _CACHE_CORRUPT):
            return False
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)
    return False


def _resolve_sha(cache: str, refs: "list[str]") -> str:
    for ref in refs:
        cand = f"refs/heads/{ref}" if ref else "HEAD"
        try:
            r = _git_cache(cache, "rev-parse", "--verify", "--quiet",
                           f"{cand}^{{commit}}", timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""


def provision_worktree(url: str, refs: "list[str]", dest: str) -> "str | None":
    """INV-1/2 を満たして dest に detached worktree を用意（要 _cache_lock）。失敗時 None。"""
    cache = ensure_cache(url)
    if not cache:
        return None
    if not _cache_fetch(cache):
        shutil.rmtree(cache, ignore_errors=True)
        cache = ensure_cache(url)
        if not cache or not _cache_fetch(cache):
            return None
    sha = _resolve_sha(cache, refs)
    if not sha:
        return None
    dest = os.path.abspath(dest)   # `git -C <cache> worktree add` は相対パスを cache 基準で解くため絶対化
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    for _ in range(2):
        try:
            r = _git_cache(cache, "worktree", "add", "--detach", "--force",
                           dest, sha, timeout=300)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return dest
        _git_cache(cache, "worktree", "prune", timeout=60)
        shutil.rmtree(dest, ignore_errors=True)
    return None


def _prune_caches(urls) -> None:
    for url in list(urls):
        try:
            with _cache_lock(url):
                cache = _cache_path_for(url)
                if os.path.isdir(cache):
                    _git_cache(cache, "worktree", "prune", timeout=60)
        except Exception:  # noqa: BLE001
            pass


def _clone_repo_shallow(url: str, branch: str, dest: str, timeout: float = 300) -> None:
    """検証用に dest へ対象 repo を用意する。まず共有 cache から detached worktree を生やし（最新化済み・
    INV-1）、失敗時は従来どおり branch（空なら既定）を浅 clone する（INV-3）。最終的に失敗なら RuntimeError。

    branch を明示した場合は **その branch が無ければ既定へ無言フォールバックしない**（refs に "" を
    足さない）。target が消えている等は「成果の無い場所での偽判定」を避けるため NG にする必要があり、
    元の `git clone --depth 1 --branch <target>`（無ければ失敗）と同じ厳密さを保つ。"""
    refs = [branch] if branch else [""]
    try:
        with _cache_lock(url):
            wt = provision_worktree(url, refs, dest)
        if wt:
            _provisioned_urls.add(url)
            return
    except Exception:  # noqa: BLE001 — cache 系の想定外失敗は黙って浅 clone へフォールバック
        pass
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, dest]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(str(e)) from e
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:300] or "git clone 失敗")


