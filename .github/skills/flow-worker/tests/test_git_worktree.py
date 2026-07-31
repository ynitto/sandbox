"""git_worktree.py のテスト — 共有キャッシュ + worktree の provision/release/push。

実リポジトリ（ローカル bare origin）で end-to-end に検証する。
実行: python -m pytest .github/skills/flow-worker/tests/ -q
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "git_worktree.py")


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def _run(env, *args, expect=0):
    r = subprocess.run([sys.executable, SCRIPT, *args],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == expect, f"{args}: rc={r.returncode} err={r.stderr}"
    return r.stdout.strip()


@pytest.fixture()
def repo_env(tmp_path):
    """bare origin（main に 1 コミット）と、隔離されたキャッシュ root の env を用意する。"""
    origin = str(tmp_path / "origin.git")
    subprocess.run(["git", "init", "--bare", "-b", "main", origin],
                   capture_output=True, check=True)
    seed = str(tmp_path / "seed")
    subprocess.run(["git", "clone", origin, seed], capture_output=True, check=True)
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    with open(os.path.join(seed, "a.txt"), "w") as f:
        f.write("v1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    _git(seed, "push", "origin", "HEAD:main")
    env = dict(os.environ)
    env["KIRO_GIT_CACHE_DIR"] = str(tmp_path / "cache")
    return origin, env, tmp_path


def test_provision_creates_detached_worktree_from_cache(repo_env):
    origin, env, tmp = repo_env
    wt = _run(env, "provision", origin, "--ref", "main")
    try:
        assert os.path.isfile(os.path.join(wt, "a.txt"))
        # detached（ブランチを checkout しない＝二重 checkout 制約を受けない）
        r = subprocess.run(["git", "-C", wt, "symbolic-ref", "-q", "HEAD"],
                           capture_output=True, text=True)
        assert r.returncode != 0
        # 共有キャッシュ（bare ミラー）が作られている
        caches = os.listdir(env["KIRO_GIT_CACHE_DIR"])
        assert any(c.endswith(".git") for c in caches)
    finally:
        _run(env, "release", wt)
    assert not os.path.exists(wt)


def test_provision_sees_new_commits_every_time(repo_env):
    """INV-1: 2 回目の provision は必ず fetch 後の SHA を使う（古いキャッシュで作業しない）。"""
    origin, env, tmp = repo_env
    wt1 = _run(env, "provision", origin, "--ref", "main")
    _run(env, "release", wt1)
    # origin に新コミットを積む
    seed2 = str(tmp / "seed2")
    subprocess.run(["git", "clone", origin, seed2], capture_output=True, check=True)
    _git(seed2, "config", "user.email", "t@t")
    _git(seed2, "config", "user.name", "t")
    with open(os.path.join(seed2, "b.txt"), "w") as f:
        f.write("v2\n")
    _git(seed2, "add", "-A")
    _git(seed2, "commit", "-m", "second")
    _git(seed2, "push", "origin", "HEAD:main")
    wt2 = _run(env, "provision", origin, "--ref", "main")
    try:
        assert os.path.isfile(os.path.join(wt2, "b.txt"))
    finally:
        _run(env, "release", wt2)


def test_push_lands_commit_on_branch_without_touching_checkouts(repo_env):
    origin, env, tmp = repo_env
    wt = _run(env, "provision", origin, "--ref", "main")
    with open(os.path.join(wt, "a.txt"), "w") as f:
        f.write("edited\n")
    sha = _run(env, "push", wt, "--branch", "feature/x", "-m", "edit a")
    _run(env, "release", wt)
    assert sha
    # origin の feature/x に反映され、main は動いていない
    assert _git(origin, "rev-parse", "feature/x") == sha
    assert _git(origin, "rev-parse", "main") != sha


def test_concurrent_pushes_merge_via_rebase(repo_env):
    """2 つの worktree が同一ブランチへ push しても、rebase リトライで両方の変更が残る。"""
    origin, env, tmp = repo_env
    wt1 = _run(env, "provision", origin, "--ref", "main")
    wt2 = _run(env, "provision", origin, "--ref", "main")
    with open(os.path.join(wt1, "one.txt"), "w") as f:
        f.write("1\n")
    with open(os.path.join(wt2, "two.txt"), "w") as f:
        f.write("2\n")
    _run(env, "push", wt1, "--branch", "shared", "-m", "one")
    _run(env, "push", wt2, "--branch", "shared", "-m", "two")   # reject → rebase → 再 push
    _run(env, "release", wt1)
    _run(env, "release", wt2)
    files = _git(origin, "ls-tree", "--name-only", "shared")
    assert "one.txt" in files and "two.txt" in files


def test_provision_fallback_when_cache_root_unwritable(repo_env, tmp_path):
    """INV-3: キャッシュが使えなくても direct clone で worktree（作業ツリー）は得られる。"""
    origin, env, tmp = repo_env
    env["KIRO_GIT_CACHE_DIR"] = "/dev/null/nowhere"   # キャッシュ作成が必ず失敗する場所
    wt = _run(env, "provision", origin, "--ref", "main")
    try:
        assert os.path.isfile(os.path.join(wt, "a.txt"))
        assert os.path.isdir(os.path.join(wt, ".git"))   # direct clone
    finally:
        _run(env, "release", wt)


def test_release_is_idempotent(repo_env):
    origin, env, tmp = repo_env
    wt = _run(env, "provision", origin, "--ref", "main")
    _run(env, "release", wt)
    _run(env, "release", wt)   # 二重 release でもエラーにしない


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
