from __future__ import annotations
# gitcache.py — 元 agent-flow.py の 1992-2276 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# 共有 git キャッシュ + worktree（docs/designs/git-worktree-cache-pattern.md）
#   リモート URL 単位のホスト共有 bare ミラー（--mirror --filter=blob:none）を 1 本持ち、
#   タスク/検証のたびに detached worktree を temp へ生やす。フル clone を「初回1回+増分」へ圧縮し、
#   GitLab の重い pack 生成を避ける。agent-project の verify/acceptance と同じ root を共有する。
#   不変条件: INV-1 鮮度（毎 fetch→fetch 後 SHA で worktree）/ INV-2 直列化・自己修復・gc.auto=0 /
#   INV-3 失敗時は従来の direct clone へフォールバック（下限を現状に固定）。
# --------------------------------------------------------------------------
_CACHE_CORRUPT = ("not a git repository", "bad object", "corrupt", "broken link",
                  "unable to read", "object directory", "fatal: bad")
_provisioned_urls: "set[str]" = set()   # cleanup で worktree prune する対象 URL
# provision_from_local が手元のクローンに登録した worktree（cleanup で外す）: [(local, dest), …]
_local_worktrees: "list[tuple[str, str]]" = []


def cache_root() -> str:
    """ホスト共有 git キャッシュの root。環境変数 KIRO_GIT_CACHE_DIR で上書き可
    （agent-project と必ず同じ既定にすること＝ホスト内でミラーを共有するため）。"""
    return os.environ.get("KIRO_GIT_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "kiro-git-cache")


def _cache_path_for(url: str) -> str:
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    return os.path.join(cache_root(), f"{h}.git")


@contextlib.contextmanager
def _cache_lock(url: str):
    """URL 単位のホスト内ロック（INV-2: cache の全変更を直列化）。"""
    root = cache_root()
    os.makedirs(root, exist_ok=True)
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    with _file_lock(os.path.join(root, f"{h}.lock")):
        yield


def _git_cache(cache: str, *args: str, timeout: float = 600):
    return subprocess.run(["git", "-C", cache, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


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
                ["git", "clone", "--mirror", url, cache]]   # INV-3: partial 非対応フォールバック
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            _git_cache(cache, "config", "gc.auto", "0")            # INV-2: 自動 repack 事故を防ぐ
            # --mirror が付ける remote.origin.mirror=true を無効化（refspec 付き push が拒否されるため）。
            _git_cache(cache, "config", "remote.origin.mirror", "false")
            _git_cache(cache, "config", "user.email", "agent-flow@local")
            _git_cache(cache, "config", "user.name", "agent-flow")
            return True
        shutil.rmtree(cache, ignore_errors=True)
    return False


def ensure_cache(url: str) -> "str | None":
    """URL の共有 bare ミラーを用意（無ければ作成・壊れていれば再作成）。ここでは fetch しない
    （鮮度は provision 側＝INV-1）。失敗時 None（呼び出し側は direct clone へフォールバック）。要 _cache_lock。"""
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
    """INV-1: 全 heads を増分 fetch（リトライ付き）。blob:none ミラーなので転送はメタデータ差分のみ。
    破損系エラーは False（呼び出し側で nuke & re-mirror を誘発）。"""
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
    """優先順 refs の先頭で解決できたコミット SHA を返す（"" は既定ブランチ=HEAD）。無ければ ""。"""
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
    """INV-1/2 を満たして dest に detached worktree を用意する（要 _cache_lock）。失敗時 None。
    refs は作業起点の優先順（例: [run ブランチ, base, ""=既定]）。"""
    cache = ensure_cache(url)
    if not cache:
        return None
    if not _cache_fetch(cache):
        shutil.rmtree(cache, ignore_errors=True)   # INV-2: 破損疑い → 一度だけ再ミラー
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
        _git_cache(cache, "worktree", "prune", timeout=60)   # locked/registered → prune して再試行
        shutil.rmtree(dest, ignore_errors=True)
    return None


def _local_remote_url(local: str) -> str:
    """ローカルクローンの origin URL（取れなければ ""）。"""
    try:
        r = subprocess.run(["git", "-C", local, "remote", "get-url", "origin"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _same_repo(a: str, b: str) -> bool:
    """git URL が同じリポジトリを指すか（末尾の .git / スラッシュ / 大小文字の揺れを吸収）。"""
    def norm(u: str) -> str:
        u = str(u or "").strip().rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        return u.lower()
    return bool(norm(a)) and norm(a) == norm(b)


def provision_from_local(local: str, url: str, refs: "list[str]", dest: str) -> "str | None":
    """手元にある同じリポジトリのクローンから detached worktree を切り出す（失敗時 None）。

    ネットワーク越しに bare ミラーを取り直す必要がなくなる（速い・オフラインでも動く）。
    worktree は別ディレクトリ・別 index なので、**ローカルの作業ツリーと index には触らない**
    （人がそこで作業していても巻き込まない）。origin URL が一致するクローンだけを使う。"""
    if not local or not os.path.isdir(local):
        return None
    if not _same_repo(_local_remote_url(local), url):
        return None                       # 別のリポジトリ → 使わない（取り違え防止）
    # 手元が古いと worker が古い base で作業するので、まず取り込む（失敗しても手元の範囲で続行）
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["git", "-C", local, "fetch", "--quiet", "origin"],
                       capture_output=True, timeout=180)
    # 作業起点の優先順: run ブランチ → base → 既定。ローカル/リモート追跡の両方を見る。
    sha = ""
    for ref in [*refs, ""]:
        for cand in ([f"refs/heads/{ref}", f"refs/remotes/origin/{ref}"] if ref else ["HEAD"]):
            try:
                r = subprocess.run(["git", "-C", local, "rev-parse", "--verify", "--quiet",
                                    f"{cand}^{{commit}}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and r.stdout.strip():
                sha = r.stdout.strip()
                break
        if sha:
            break
    if not sha:
        return None
    dest = os.path.abspath(dest)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        r = subprocess.run(["git", "-C", local, "worktree", "add", "--detach", dest, sha],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    _local_worktrees.append((local, dest))    # 後始末（worktree remove）のため覚えておく
    return dest


def cleanup_local_worktrees() -> None:
    """provision_from_local が作った worktree の登録をローカルクローンから外す。
    （dest 自体は _workspace_root ごと rmtree される。登録だけが残ると git worktree list が汚れる）"""
    for local, dest in list(_local_worktrees):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["git", "-C", local, "worktree", "remove", "--force", dest],
                           capture_output=True, timeout=60)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["git", "-C", local, "worktree", "prune"],
                           capture_output=True, timeout=60)
    _local_worktrees.clear()


def provision_tree(url: str, refs: "list[str] | str", dest: str,
                   local: str = "") -> "str | None":
    """作業ツリーを用意する。順に:
       1. local（手元の同じリポジトリのクローン）から detached worktree を切る — 取得ゼロで最速
       2. 共有 bare ミラーから detached worktree（INV-1/2）
       3. direct clone（INV-3 フォールバック）
    返り値: 作業ツリーのパス、または None（最終的に失敗）。"""
    ref_list = [refs] if isinstance(refs, str) else list(refs)
    if local:
        wt = provision_from_local(local, url, ref_list, dest)
        if wt:
            return wt
    try:
        with _cache_lock(url):
            wt = provision_worktree(url, ref_list, dest)
        if wt:
            _provisioned_urls.add(url)
            return wt
    except Exception:  # noqa: BLE001 — cache 系の想定外失敗は黙ってフォールバックへ
        pass
    base = next((r for r in ref_list if r), "")
    return _clone_repo(url, base, dest) or None


def _prune_caches(urls) -> None:
    """指定 URL の共有 cache の worktree 登録を回収する（temp を rmtree した後の後始末）。"""
    for url in list(urls):
        try:
            with _cache_lock(url):
                cache = _cache_path_for(url)
                if os.path.isdir(cache):
                    _git_cache(cache, "worktree", "prune", timeout=60)
        except Exception:  # noqa: BLE001
            pass


def sweep_cache_dirs(min_age_sec: float) -> int:
    """長期間未使用の共有ミラーを削除し、削除数を返す（disk 逼迫対策）。生存中の worktree は
    prune してから、mtime が min_age 以上古い bare ミラーのみ消す。共有のため通常は残す。"""
    root = cache_root()
    if not os.path.isdir(root):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(root):
        if not name.endswith(".git"):
            continue
        cache = os.path.join(root, name)
        if not os.path.isdir(cache):
            continue
        try:
            age = now - os.path.getmtime(cache)
        except OSError:
            continue
        _git_cache(cache, "worktree", "prune", timeout=60)   # 生存 worktree の登録は常に整理
        if age < min_age_sec:
            continue
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    return removed

