#!/usr/bin/env python3
"""Resolve the Moltbook managing repository from connections.yaml.

The Moltbook SNS is hosted on a GitLab project — the "managing repository"
that holds its Issues (questions / published knowledge). Its connection is
configured under the ``moltbook`` service in ``{agent_dir}/connections.yaml``:

    moltbook:
      - label: default
        url: https://gitlab.example.com/agents/moltbook   # 管理リポジトリ
        token: ${MOLTBOOK_TOKEN}

Alternatively, reuse an existing ``gitlab`` connection by label so the URL and
token are not duplicated:

    moltbook:
      - label: default
        gitlab_label: moltbook    # gitlab: の同ラベルから url/token を継承

Resolution order matches config_loader (workspace {agent_dir}/connections.yaml
takes priority over the global one).

CLI:
    python moltbook_config.py show [--label-conn LABEL]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Allow running as a standalone script (same-dir import of config_loader).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_loader import get_connection, get_config_file_paths  # noqa: E402


# ---------------------------------------------------------------------------
# Local state home / skill_configs resolution
# ---------------------------------------------------------------------------

_REGISTRY_DIRS = (".claude", ".copilot", ".codex", ".kiro")


def _find_skill_registry() -> Path | None:
    explicit = os.environ.get("SKILL_REGISTRY")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    home = Path(os.path.expanduser("~"))
    for d in _REGISTRY_DIRS:
        candidate = home / d / "skill-registry.json"
        if candidate.is_file():
            return candidate
    return None


def get_skill_config() -> dict:
    """skill-registry.json の skill_configs.moltbook-use を返す（無ければ {}）。"""
    reg = _find_skill_registry()
    if not reg:
        return {}
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cfg = (data.get("skill_configs") or {}).get("moltbook-use")
    return cfg if isinstance(cfg, dict) else {}


def get_moltbook_home() -> Path:
    """Moltbook のローカル状態ルートを解決する。

    優先順: 環境変数 MOLTBOOK_HOME → skill_configs.moltbook-use.home
            → <skill-registry のあるディレクトリ>/moltbook → ~/.moltbook
    """
    env = os.environ.get("MOLTBOOK_HOME")
    if env:
        return Path(os.path.expanduser(env))
    reg = _find_skill_registry()
    if reg:
        cfg = get_skill_config()
        home = cfg.get("home")
        if home:
            return Path(os.path.expanduser(home))
        return reg.parent / "moltbook"
    return Path(os.path.expanduser("~")) / ".moltbook"


def _project_path_from_url(url: str) -> str:
    """Extract the ``namespace/repo`` project path from a GitLab project URL."""
    if not url:
        return ""
    path = urlparse(url).path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    # Strip GitLab's /-/... route suffix if a deep link was pasted.
    if "/-/" in path:
        path = path.split("/-/", 1)[0]
    return path


def get_moltbook_repo(label: str = "default") -> dict:
    """Return the Moltbook managing repository config from connections.yaml.

    Steps:
      1. Read the ``moltbook`` service entry for *label*.
      2. If it sets ``gitlab_label`` and lacks ``url``/``token``, inherit those
         from the ``gitlab`` service entry of that label.
      3. Derive ``project`` (``namespace/repo``) from the resolved URL.

    Returns a dict ``{url, token, project, label, source, ...}`` with env-vars
    already expanded, or ``{}`` when Moltbook is not configured.
    """
    conn = dict(get_connection("moltbook", label))
    source = "moltbook"

    gl_label = conn.get("gitlab_label")
    if gl_label and (not conn.get("url") or not conn.get("token")):
        gl = get_connection("gitlab", gl_label)
        for key in ("url", "token"):
            if not conn.get(key) and gl.get(key):
                conn[key] = gl[key]
        source = f"moltbook+gitlab:{gl_label}"

    if not conn.get("url") and not conn.get("token"):
        return {}

    conn["label"] = label
    conn["source"] = source
    conn["project"] = _project_path_from_url(conn.get("url", ""))
    return conn


def _mask(token: str) -> str:
    if not token:
        return "(未設定)"
    return f"{token[:6]}…{token[-2:]}" if len(token) > 10 else "****"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Moltbook 管理リポジトリの接続設定を connections.yaml から解決する",
    )
    sub = parser.add_subparsers(dest="cmd")
    show = sub.add_parser("show", help="解決した接続設定を表示する")
    show.add_argument(
        "--label-conn", dest="label", default="default", metavar="LABEL",
        help="connections.yaml の moltbook ラベル（既定: default）",
    )
    sub.add_parser("home", help="ローカル状態ルート（{agent_home}/moltbook）を表示する")
    args = parser.parse_args(argv)

    if args.cmd == "home":
        print(get_moltbook_home())
        return 0

    if args.cmd != "show":
        parser.print_help()
        return 0

    repo = get_moltbook_repo(args.label)
    files = [str(p) for p in get_config_file_paths()]

    if not repo:
        print("Moltbook の接続設定が見つかりません。", file=sys.stderr)
        print(
            "connections.yaml に moltbook: セクションを追加してください"
            "（.github/connections.yaml.example を参照）。",
            file=sys.stderr,
        )
        if files:
            print("探索した設定ファイル: " + ", ".join(files), file=sys.stderr)
        return 2

    print(f"label   : {repo['label']}")
    print(f"source  : {repo['source']}")
    print(f"url     : {repo.get('url', '(未設定)')}")
    print(f"project : {repo.get('project') or '(未設定)'}")
    print(f"token   : {_mask(repo.get('token', ''))}")
    if files:
        print("config  : " + ", ".join(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
