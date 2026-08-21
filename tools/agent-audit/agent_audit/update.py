"""自己更新 — agent-flow / agent-project の update.py と同型（仕様書 §7・§9）。

1. git ls-remote でスキルリポジトリ branch の最新コミットを得る
2. 適用済み SHA（state ファイル）と違えば「更新あり」
3. temp 領域へ sparse-checkout（tools/agent-audit + tools/agent-tools の 2 パス。
   共有物を含めないと installer が agentcore を同梱できず自己更新が必ず失敗する）
4. install.sh を実行して ~/.local/bin の本体を更新

agent-audit は常駐を持たないので、確認も取り込みも `update` サブコマンドの単発実行だけ。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .configfile import TOOL_SUBDIR, agent_home_dir
from .util import log, now_iso, read_json, write_json_atomic

_AGENT_HOME_DIRS = (".agents", ".kiro", ".claude", ".copilot", ".codex")


def _update_state_path() -> str:
    """適用済み SHA の記録先。$KIRO_STATE_HOME はツール横断の自己更新契約
    （agent-flow / agent-project と同じ）なので尊重する。"""
    base = os.environ.get("KIRO_STATE_HOME")
    if base:
        return os.path.join(os.path.expanduser(base), "agent-audit.update.json")
    return os.path.join(agent_home_dir(), "agent-audit.update.json")


def read_update_state() -> dict:
    return read_json(_update_state_path()) or {}


def write_update_state(state: dict) -> None:
    write_json_atomic(_update_state_path(), state)


def remote_branch_sha(repo: str, branch: str, runner=None) -> "str | None":
    if not repo:
        return None
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True,
                                              encoding="utf-8", errors="replace", timeout=60))
    try:
        r = run(["git", "ls-remote", repo, f"refs/heads/{branch}"])
    except Exception:  # noqa: BLE001 git 不在・ネットワーク不通・タイムアウト
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    lines = (getattr(r, "stdout", "") or "").strip().splitlines()
    if not lines:
        return None
    sha = lines[0].split()[0].strip()
    return sha if len(sha) >= 7 else None


def find_skill_registry() -> "str | None":
    env = os.environ.get("KIRO_SKILL_REGISTRY")
    if env:
        p = os.path.expanduser(env)
        cand = os.path.join(p, "skill-registry.json") if os.path.isdir(p) else p
        return cand if os.path.isfile(cand) else None
    for d in _AGENT_HOME_DIRS:
        c = os.path.join(os.path.expanduser("~"), d, "skill-registry.json")
        if os.path.isfile(c):
            return c
    return None


def registry_update_source() -> "tuple[str | None, str | None]":
    path = find_skill_registry()
    if not path:
        return (None, None)
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return (None, None)
    repos = reg.get("repositories") or []
    chosen = next((r for r in repos if r.get("name") == "origin"), None)
    if chosen is None and repos:
        chosen = sorted(repos, key=lambda r: r.get("priority", 99))[0]
    if chosen and chosen.get("url"):
        return (chosen["url"], chosen.get("branch") or "main")
    idir = reg.get("install_dir")
    if idir and os.path.isdir(idir):
        return (idir, (chosen.get("branch") if chosen else None) or "main")
    return (None, None)


def resolve_update_target(args) -> "tuple[str, str]":
    repo = getattr(args, "update_repo", "") or ""
    branch = getattr(args, "update_branch", "main") or "main"
    if not repo:
        rurl, rbranch = registry_update_source()
        if rurl:
            repo = rurl
            if rbranch and branch == "main":
                branch = rbranch
    return repo, branch


def check_update(args, runner=None) -> dict:
    """更新の有無を調べる。`enabled` が False なら以降は何もしない。

    無効の理由は 2 つあり、区別して返す。`update_enabled: false` は**人が明示的に切った**
    キルスイッチで、`update_repo` 未設定は**そもそも設定していない**状態だ。前者を後者と
    同じ「未設定です」で片づけると、切ったつもりの人に理由が届かない。
    """
    repo, branch = resolve_update_target(args)
    disabled = not bool(getattr(args, "update_enabled", True))
    info = {"enabled": bool(repo) and not disabled, "disabled_by_config": disabled,
            "repo": repo, "branch": branch, "remote_sha": None,
            "applied_sha": None, "available": False, "baseline": False}
    if not repo or disabled:
        return info
    state = read_update_state()
    info["applied_sha"] = state.get("applied_sha")
    remote = remote_branch_sha(repo, branch, runner=runner)
    info["remote_sha"] = remote
    if not remote:
        return info
    if not info["applied_sha"]:
        state["applied_sha"] = remote
        state["baseline_at"] = now_iso()
        write_update_state(state)
        info["applied_sha"] = remote
        info["baseline"] = True
        return info
    info["available"] = (remote != info["applied_sha"])
    return info


def split_subdirs(subdir: str) -> "list[str]":
    parts = [p for p in re.split(r"[,\s]+", str(subdir or "").strip()) if p]
    return parts or [p for p in re.split(r"[,\s]+", TOOL_SUBDIR) if p]


def sparse_checkout_tool(repo: str, branch: str, subdir: str, dest: str, runner=None) -> str:
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   encoding="utf-8", errors="replace",
                                                   timeout=600, **k))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    r = run(["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none",
             "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:
        r = run(["git", "clone", "--no-checkout", "--depth", "1", "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:
        raise RuntimeError(f"git clone 失敗: {(getattr(r, 'stderr', '') or '').strip()[:300]}")

    def g(cmd):
        return run(["git", "-C", dest] + cmd)
    subdirs = split_subdirs(subdir)
    g(["sparse-checkout", "init", "--cone"])
    g(["sparse-checkout", "set"] + subdirs)
    co = g(["checkout", branch])
    if getattr(co, "returncode", 1) != 0:
        raise RuntimeError(f"git checkout 失敗: {(getattr(co, 'stderr', '') or '').strip()[:300]}")
    for part in subdirs:
        if not os.path.isdir(os.path.join(dest, part)):
            raise RuntimeError(f"sparse-checkout 後に {part} が見つかりません（リポジトリ構成を確認）")
    return os.path.join(dest, subdirs[0])


def run_installer(tool_dir: str, installer: str = "install.sh", runner=None) -> "tuple[bool, str]":
    path = os.path.join(tool_dir, installer)
    if not os.path.isfile(path):
        return False, f"インストーラが見つかりません: {path}"
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   encoding="utf-8", errors="replace",
                                                   timeout=600, **k))
    try:
        r = run(["bash", path], cwd=tool_dir)
    except Exception as e:  # noqa: BLE001
        return False, f"インストーラ実行に失敗: {e}"
    out = ((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")).strip()
    return getattr(r, "returncode", 1) == 0, out[-2000:]


def _tree_digest(root: str, subdirs: "list[str] | None" = None) -> str:
    """update_subdir の内容ダイジェスト。コミット SHA 比較では「HEAD は進んだが本体は
    同じ」を判別できない（agent-flow update.py の前例と同じ理由）。"""
    h = hashlib.sha256()
    for base in (subdirs or [""]):
        top = os.path.join(root, base) if base else root
        h.update(f"\0{base}\0".encode("utf-8"))
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(filenames):
                p = os.path.join(dirpath, name)
                h.update(os.path.relpath(p, top).encode("utf-8"))
                try:
                    with open(p, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                except OSError:
                    continue
    return h.hexdigest()


def apply_update(args, info: dict, runner=None) -> bool:
    subdir = getattr(args, "update_subdir", TOOL_SUBDIR) or TOOL_SUBDIR
    installer = getattr(args, "update_installer", "install.sh") or "install.sh"
    tmp = tempfile.mkdtemp(prefix="agent-audit-update-")
    dest = os.path.join(tmp, "repo")
    try:
        tool_dir = sparse_checkout_tool(info["repo"], info["branch"], subdir, dest, runner=runner)
        digest = _tree_digest(dest, split_subdirs(subdir))
        state = read_update_state()
        if digest == state.get("applied_digest"):
            state["applied_sha"] = info["remote_sha"]
            state["skipped_at"] = now_iso()
            write_update_state(state)
            log("update", f"本体（{subdir}）に変更なし——適用をスキップし "
                          f"ベースラインを {info['remote_sha'][:8]} へ進めました。")
            return False
        ok, out = run_installer(tool_dir, installer, runner=runner)
        if not ok:
            log("update", f"install.sh 失敗（更新を見送り）: {out[-300:]}")
            return False
        state = read_update_state()
        state["applied_sha"] = info["remote_sha"]
        state["applied_digest"] = digest
        state["applied_at"] = now_iso()
        write_update_state(state)
        log("update", f"更新を適用しました（{info['remote_sha'][:8]}）。")
        return True
    except Exception as e:  # noqa: BLE001 clone/checkout/installer の失敗は次回再試行
        log("update", f"更新の取り込みに失敗（次回再試行）: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_update(args) -> int:
    info = check_update(args)
    if info.get("disabled_by_config"):
        print("[agent-audit] update: 設定 update_enabled が false のため何もしません。")
        return 0
    if not info["enabled"]:
        print("[agent-audit] update: update_repo が未設定です（設定ファイルで指定してください）。",
              file=sys.stderr)
        return 2
    if info["remote_sha"] is None:
        print(f"[agent-audit] update: リモート {info['repo']}@{info['branch']} を"
              "取得できませんでした。", file=sys.stderr)
        return 2
    if info.get("baseline"):
        print(f"[agent-audit] update: ベースラインを記録しました（{info['remote_sha'][:8]}）。"
              "以降この地点からの更新を検出します。")
        return 0
    if not info["available"]:
        print(f"[agent-audit] update: 最新です（{info['applied_sha'][:8]}）。")
        return 0
    print(f"[agent-audit] update: 更新があります "
          f"{info['applied_sha'][:8]} → {info['remote_sha'][:8]}")
    if getattr(args, "check", False) or not getattr(args, "now", False):
        print("  取り込むには `agent-audit update --now` を実行してください。")
        return 0
    if apply_update(args, info):
        print("  install.sh を実行して更新しました（次回の起動から新しい本体が使われます）。")
        return 0
    if read_update_state().get("applied_sha") == info.get("remote_sha"):
        print("  本体（update_subdir）に変更が無かったため適用をスキップし、"
              "ベースラインだけ進めました。")
        return 0
    print("  更新の取り込みに失敗しました（ログを確認してください）。", file=sys.stderr)
    return 1
