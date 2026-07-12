#!/usr/bin/env python3
"""git_worktree — エージェント用の安全な git アクセス CLI（共有キャッシュ + worktree）。

エージェント（LLM ワーカー）がタスク中に別リポジトリ・別ブランチへアクセスするとき、
自前の git clone / checkout / 共有チェックアウトへの commit の代わりに使う唯一の入口。
docs/designs/git-worktree-cache-pattern.md のパターンを stdlib のみで実装しており、
kiro-flow / kiro-project とキャッシュ root（KIRO_GIT_CACHE_DIR / $TMPDIR/kiro-git-cache）を
共有する。

なぜ worktree か:
- 読み取りは URL 単位のホスト共有 bare ミラー（--mirror --filter=blob:none）から
  detached worktree を生やす。フル clone を「初回1回+増分」へ圧縮し、checkout の
  共有・使い回しをしない（他タスクと状態が混ざらない）。
- 書き込みは専用 worktree で commit し `push HEAD:refs/heads/<branch>` で送る。
  共有チェックアウトのブランチを動かさないため、並行タスク・人の作業と
  コミットが衝突しない（reject は fetch + rebase で自動リトライ）。

Usage:
    python3 git_worktree.py provision <URL|パス> [--ref <ブランチ|SHA>] [--dest DIR]
        → 用意した worktree のパスを stdout に出力（失敗時は非ゼロ終了）
    python3 git_worktree.py release <worktree パス>
        → worktree を削除し、キャッシュの worktree 登録を回収する
    python3 git_worktree.py push <worktree パス> --branch <ブランチ> [-m <メッセージ>]
        → worktree 内の全変更を commit し、origin の <ブランチ> へ push（rebase リトライ付き）

不変条件（パターン設計書の INV-1..3）:
    INV-1 鮮度: provision のたびに必ず fetch し、fetch 後に解決した SHA で worktree を作る。
    INV-2 保全: キャッシュの全変更は URL ロックで直列化。破損時は作り直し。gc.auto=0。
    INV-3 下限: キャッシュ経路が失敗したら従来の direct clone へフォールバックする。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

RETRIES = 3
_CORRUPT = ("not a git repository", "bad object", "corrupt", "broken link",
            "unable to read", "object directory", "fatal: bad")


def cache_root() -> str:
    """ホスト共有 git キャッシュの root（kiro-flow / kiro-project と同一の既定）。"""
    return os.environ.get("KIRO_GIT_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "kiro-git-cache")


def _cache_path_for(url: str) -> str:
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    return os.path.join(cache_root(), f"{h}.git")


@contextlib.contextmanager
def _url_lock(url: str):
    """URL 単位のホスト内ロック（INV-2）。kiro-flow / kiro-project と同じ .lock ファイルを使う。"""
    root = cache_root()
    os.makedirs(root, exist_ok=True)
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    path = os.path.join(root, f"{h}.lock")
    f = open(path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except ImportError:      # 非 POSIX: ロックなしで進む（direct clone フォールバックが下限）
            pass
        yield
    finally:
        f.close()


def _git(workdir: str, *args: str, timeout: float = 600):
    return subprocess.run(["git", "-C", workdir, *args],
                          capture_output=True, text=True, timeout=timeout)


def _is_cache_valid(cache: str) -> bool:
    if not os.path.isdir(cache):
        return False
    try:
        return _git(cache, "rev-parse", "--git-dir", timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mirror_clone(url: str, cache: str) -> bool:
    shutil.rmtree(cache, ignore_errors=True)
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    attempts = [["git", "clone", "--mirror", "--filter=blob:none", url, cache],
                ["git", "clone", "--mirror", url, cache]]   # INV-3: partial 非対応フォールバック
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            _git(cache, "config", "gc.auto", "0")                    # INV-2
            _git(cache, "config", "remote.origin.mirror", "false")   # refspec push を許可
            return True
        shutil.rmtree(cache, ignore_errors=True)
    return False


def ensure_cache(url: str) -> "str | None":
    cache = _cache_path_for(url)
    if _is_cache_valid(cache):
        return cache
    for i in range(RETRIES):
        if _mirror_clone(url, cache):
            return cache
        if i < RETRIES - 1:
            time.sleep(2 ** i)
    return None


def _cache_fetch(cache: str) -> bool:
    """INV-1: 全 heads を増分 fetch（リトライ付き）。破損系エラーは False。"""
    for i in range(RETRIES):
        try:
            r = _git(cache, "fetch", "--prune", "--no-tags", "origin",
                     "+refs/heads/*:refs/heads/*")
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return True
        if r is not None and any(s in (r.stderr or "").lower() for s in _CORRUPT):
            return False
        if i < RETRIES - 1:
            time.sleep(2 ** i)
    return False


def _resolve_sha(cache: str, ref: str) -> str:
    """ref（ブランチ/SHA/空=既定ブランチ）を fetch 後のコミット SHA へ解決する。"""
    for cand in ([f"refs/heads/{ref}", ref] if ref else ["HEAD"]):
        try:
            r = _git(cache, "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}",
                     timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""


def cmd_provision(url: str, ref: str, dest: "str | None") -> "str | None":
    """共有キャッシュから detached worktree を用意。失敗時は direct clone（INV-3）。"""
    dest = os.path.abspath(dest) if dest else tempfile.mkdtemp(prefix="kiro-worktree-")
    # mkdtemp で作った空ディレクトリは worktree add が嫌うため一旦消してパスだけ使う
    if os.path.isdir(dest) and not os.listdir(dest):
        os.rmdir(dest)
    try:
        with _url_lock(url):
            cache = ensure_cache(url)
            if cache and not _cache_fetch(cache):
                shutil.rmtree(cache, ignore_errors=True)   # INV-2: 破損疑い → 一度だけ再ミラー
                cache = ensure_cache(url)
                if cache and not _cache_fetch(cache):
                    cache = None
            if cache:
                sha = _resolve_sha(cache, ref)
                if sha:
                    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                    for _ in range(2):
                        r = _git(cache, "worktree", "add", "--detach", "--force",
                                 dest, sha, timeout=300)
                        if r.returncode == 0:
                            return dest
                        _git(cache, "worktree", "prune", timeout=60)
                        shutil.rmtree(dest, ignore_errors=True)
    except Exception:  # noqa: BLE001 — キャッシュ経路の想定外失敗はフォールバックへ
        pass
    # INV-3: direct clone フォールバック
    attempts = ([["git", "clone", "-b", ref, url, dest]] if ref else []) + \
               [["git", "clone", url, dest]]
    for cmd in attempts:
        shutil.rmtree(dest, ignore_errors=True)
        try:
            if subprocess.run(cmd, capture_output=True, text=True,
                              timeout=600).returncode == 0:
                return dest
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _owning_cache(worktree: str) -> "str | None":
    """worktree の .git ファイルから、それを生やしたキャッシュ（bare repo）のパスを引く。"""
    dotgit = os.path.join(worktree, ".git")
    if not os.path.isfile(dotgit):     # direct clone（.git がディレクトリ）はキャッシュ無し
        return None
    try:
        with open(dotgit, encoding="utf-8") as f:
            m = re.match(r"gitdir:\s*(.+)", f.read().strip())
    except OSError:
        return None
    if not m:
        return None
    # gitdir: <cache>/worktrees/<name> → <cache>
    admin = os.path.abspath(m.group(1))
    cand = os.path.dirname(os.path.dirname(admin))
    return cand if os.path.isdir(cand) else None


def cmd_release(worktree: str) -> int:
    cache = _owning_cache(worktree)
    shutil.rmtree(worktree, ignore_errors=True)
    if cache:
        with contextlib.suppress(Exception):
            _git(cache, "worktree", "prune", timeout=60)
    return 0


def _ensure_identity(worktree: str) -> None:
    if not _git(worktree, "config", "user.email").stdout.strip():
        _git(worktree, "config", "user.email", "flow-worker@local")
        _git(worktree, "config", "user.name", "flow-worker")


def cmd_push(worktree: str, branch: str, message: str) -> int:
    """worktree の全変更を commit し origin の branch へ push（衝突は fetch+rebase でリトライ）。
    detached のまま `push HEAD:refs/heads/<branch>` するため、共有チェックアウトの
    ブランチを動かさず、並行 push とはリベースで合流する。"""
    _ensure_identity(worktree)
    _git(worktree, "add", "-A")
    if _git(worktree, "diff", "--cached", "--quiet").returncode != 0:
        r = _git(worktree, "commit", "-m", message)
        if r.returncode != 0:
            print(f"commit 失敗: {r.stderr.strip()[:300]}", file=sys.stderr)
            return 1
    if _git(worktree, "rev-parse", "-q", "--verify", "HEAD").returncode != 0:
        print("commit がありません（変更なし）", file=sys.stderr)
        return 1
    for i in range(5):
        if _git(worktree, "push", "origin",
                f"HEAD:refs/heads/{branch}").returncode == 0:
            print(_git(worktree, "rev-parse", "HEAD").stdout.strip())
            return 0
        # reject → リモートの branch を取り込み、detached のまま rebase して再 push
        _git(worktree, "fetch", "--quiet", "origin", branch)
        r = _git(worktree, "rebase", "FETCH_HEAD")
        if r.returncode != 0:
            _git(worktree, "rebase", "--abort")
            print(f"rebase 失敗（コンフリクト）: {r.stderr.strip()[:300]}", file=sys.stderr)
            return 1
        time.sleep(2 ** i if i < 4 else 16)
    print(f"push が {branch} へ反映できませんでした", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="エージェント用の安全な git アクセス（worktree 必須）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provision", help="URL の detached worktree を用意しパスを出力")
    p.add_argument("url")
    p.add_argument("--ref", default="", help="ブランチ名 or SHA（省略で既定ブランチ）")
    p.add_argument("--dest", default=None, help="worktree の作成先（省略で temp）")
    p = sub.add_parser("release", help="worktree を削除してキャッシュ登録を回収")
    p.add_argument("worktree")
    p = sub.add_parser("push", help="worktree の変更を commit し origin へ安全に push")
    p.add_argument("worktree")
    p.add_argument("--branch", required=True, help="push 先ブランチ（HEAD:refs/heads/<branch>）")
    p.add_argument("-m", "--message", default="flow-worker: update", help="コミットメッセージ")
    args = ap.parse_args()
    if args.cmd == "provision":
        dest = cmd_provision(args.url, args.ref, args.dest)
        if not dest:
            print(f"provision 失敗: {args.url}", file=sys.stderr)
            return 1
        print(dest)
        return 0
    if args.cmd == "release":
        return cmd_release(os.path.abspath(args.worktree))
    return cmd_push(os.path.abspath(args.worktree), args.branch, args.message)


if __name__ == "__main__":
    sys.exit(main())
