#!/usr/bin/env python3
"""
moltbook_init.py — moltbook-use を初期化する

使い方:
  python scripts/moltbook_init.py                        # 対話的に設定して初期化
  python scripts/moltbook_init.py --non-interactive \\
      --reply-mode active --cooldown-hours 24 \\
      --url https://gitlab.example.com/agents/moltbook \\
      --token glpat-xxxx                                  # 非対話モード（直接接続）
  python scripts/moltbook_init.py --non-interactive \\
      --gitlab-label moltbook                             # 非対話モード（gitlab: 委譲）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moltbook_config import (  # noqa: E402
    get_skill_config,
    save_skill_config,
    get_moltbook_repo,
    _find_skill_registry,
)
from config_loader import get_yaml_write_path  # noqa: E402

_DEFAULTS = {
    "reply_mode": "active",
    "reply_budget": 3,
    "thread_depth": 2,
    "author_cooldown_min": 30,
    "auto_check_cooldown_hours": 24,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ask(prompt: str, default: str, secret: bool = False) -> str:
    hint = "****" if secret and default else default
    answer = input(f"{prompt} [{hint}]: ").strip()
    return answer if answer else default


def _load_connections_yaml(write_path: Path) -> dict:
    if not write_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import]
        with open(write_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_connections_yaml(write_path: Path, data: dict) -> None:
    import yaml  # type: ignore[import]
    write_path.parent.mkdir(parents=True, exist_ok=True)
    with open(write_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    write_path.chmod(0o600)


# ---------------------------------------------------------------------------
# skill_configs section
# ---------------------------------------------------------------------------

def _configure_skill_configs_interactive(existing: dict) -> dict:
    """reply_mode と auto_check_cooldown_hours を対話的に設定する。"""
    print("\n--- スキル設定 ---")

    cur_mode = existing.get("reply_mode", _DEFAULTS["reply_mode"])
    while True:
        mode = _ask("reply_mode (active / quiet)", cur_mode)
        if mode in ("active", "quiet"):
            break
        print("  active または quiet を入力してください。")

    cur_hours = str(existing.get("auto_check_cooldown_hours", _DEFAULTS["auto_check_cooldown_hours"]))
    while True:
        hours_str = _ask("auto_check_cooldown_hours（定期チェック最低待機時間、時間単位）", cur_hours)
        try:
            hours = int(hours_str)
            if hours >= 1:
                break
        except ValueError:
            pass
        print("  1 以上の整数を入力してください。")

    return {**existing, "reply_mode": mode, "auto_check_cooldown_hours": hours}


# ---------------------------------------------------------------------------
# connections.yaml section
# ---------------------------------------------------------------------------

def _current_moltbook_entry(write_path: Path, label: str) -> dict:
    """connections.yaml の moltbook[label] を返す（なければ {}）。"""
    data = _load_connections_yaml(write_path)
    entries = data.get("moltbook", [])
    if not isinstance(entries, list):
        return {}
    return next(
        (e for e in entries if isinstance(e, dict) and e.get("label", "default") == label),
        {},
    )


def _configure_connections_interactive(label: str) -> None:
    """connections.yaml の moltbook セクションを対話的に設定する。"""
    write_path = get_yaml_write_path()
    current = _current_moltbook_entry(write_path, label)

    print(f"\n--- 接続設定（connections.yaml: moltbook label={label}）---")
    if current:
        if current.get("gitlab_label"):
            print(f"  現在: gitlab_label={current['gitlab_label']} を委譲")
        else:
            print(f"  現在: url={current.get('url', '(未設定)')}")
        print()

    conn_type = _ask("接続方式 (direct / gitlab)", "direct")

    if conn_type.strip().lower() == "gitlab":
        cur_gl = current.get("gitlab_label", "")
        gitlab_label = _ask("gitlab: セクションのラベル名", cur_gl or "moltbook")
        new_entry: dict = {"label": label, "gitlab_label": gitlab_label}
    else:
        cur_url = current.get("url", "")
        cur_token = current.get("token", "${MOLTBOOK_TOKEN}")
        url = _ask("Moltbook 管理リポジトリ URL", cur_url)
        token = _ask("GitLab アクセストークン", cur_token, secret=True)
        if not url or not token:
            print("  URL とトークンは必須です。スキップします。")
            return
        new_entry = {"label": label, "url": url, "token": token}

    _write_moltbook_entry(write_path, label, new_entry)
    print(f"\n[OK] 保存しました: {write_path}  (moltbook label={label})")
    print("  確認: python scripts/moltbook_config.py show")


def _configure_connections_non_interactive(
    label: str,
    url: str | None,
    token: str | None,
    gitlab_label: str | None,
) -> None:
    """connections.yaml の moltbook セクションを非対話で設定する。"""
    write_path = get_yaml_write_path()

    if gitlab_label:
        new_entry: dict = {"label": label, "gitlab_label": gitlab_label}
    elif url and token:
        new_entry = {"label": label, "url": url, "token": token}
    else:
        return

    _write_moltbook_entry(write_path, label, new_entry)
    print(f"[OK] 保存しました: {write_path}  (moltbook label={label})")


def _write_moltbook_entry(write_path: Path, label: str, new_entry: dict) -> None:
    data = _load_connections_yaml(write_path)
    entries = data.get("moltbook", [])
    if not isinstance(entries, list):
        entries = []
    updated = [e for e in entries if isinstance(e, dict) and e.get("label", "default") != label]
    updated.append(new_entry)
    data["moltbook"] = updated
    _save_connections_yaml(write_path, data)


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------

def cmd_init_interactive() -> None:
    reg = _find_skill_registry()
    existing = get_skill_config()

    print("=== moltbook-use 初期化 ===")
    if existing:
        print(f"既存の設定が見つかりました: {reg} (skill_configs.moltbook-use)")
        for k, v in existing.items():
            print(f"  {k}: {v}")

    # 1. skill_configs
    config = _configure_skill_configs_interactive(existing)
    save_skill_config(config)
    print(f"\n[OK] 設定を保存しました: {reg} (skill_configs.moltbook-use)")

    # 2. connections.yaml
    write_path = get_yaml_write_path()
    current_repo = get_moltbook_repo()
    if current_repo:
        print(f"\n既存の接続設定が見つかりました: {write_path}")
        print(f"  url: {current_repo.get('url', '(未設定)')}")
        answer = input("接続設定を変更しますか? [y/N]: ").strip().lower()
        if answer == "y":
            _configure_connections_interactive("default")
    else:
        answer = input("\nconnections.yaml に Moltbook 接続を設定しますか? [Y/n]: ").strip().lower()
        if answer != "n":
            _configure_connections_interactive("default")

    # 3. 最終サマリー
    print("\n=== 設定完了 ===")
    print(f"  reply_mode               : {config['reply_mode']}")
    print(f"  auto_check_cooldown_hours: {config['auto_check_cooldown_hours']}h")
    repo = get_moltbook_repo()
    if repo:
        print(f"  moltbook url             : {repo.get('url', '(gitlab委譲)')}")
    else:
        print("  moltbook 接続            : 未設定")


def cmd_init_non_interactive(
    reply_mode: str,
    cooldown_hours: int,
    url: str | None,
    token: str | None,
    gitlab_label: str | None,
    conn_label: str,
) -> None:
    existing = get_skill_config()
    config = {**existing, "reply_mode": reply_mode, "auto_check_cooldown_hours": cooldown_hours}
    save_skill_config(config)
    reg = _find_skill_registry()
    print(f"[OK] 設定を保存しました: {reg} (skill_configs.moltbook-use)")
    print(f"  reply_mode               : {reply_mode}")
    print(f"  auto_check_cooldown_hours: {cooldown_hours}h")

    if url or token or gitlab_label:
        _configure_connections_non_interactive(conn_label, url, token, gitlab_label)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="moltbook-use を初期化する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="非対話モード",
    )

    # skill_configs
    parser.add_argument(
        "--reply-mode",
        choices=["active", "quiet"],
        default=None,
        help="自律返信モード: active（既定）/ quiet",
    )
    parser.add_argument(
        "--cooldown-hours",
        type=int,
        default=None,
        help="定期チェックの最低待機時間（時間、既定: 24）",
    )

    # connections.yaml
    parser.add_argument("--url", default=None, help="Moltbook 管理リポジトリ URL（直接接続）")
    parser.add_argument("--token", default=None, help="GitLab アクセストークン（直接接続）")
    parser.add_argument(
        "--gitlab-label",
        default=None,
        metavar="LABEL",
        help="gitlab: セクションのラベル名（委譲接続。--url/--token と排他）",
    )
    parser.add_argument(
        "--conn-label",
        default="default",
        metavar="LABEL",
        help="connections.yaml に書き込む moltbook ラベル（既定: default）",
    )

    args = parser.parse_args()

    if args.non_interactive:
        mode = args.reply_mode or _DEFAULTS["reply_mode"]
        hours = args.cooldown_hours if args.cooldown_hours is not None else _DEFAULTS["auto_check_cooldown_hours"]
        if hours < 1:
            parser.error("--cooldown-hours は 1 以上で指定してください")
        if args.url and args.gitlab_label:
            parser.error("--url と --gitlab-label は同時に指定できません")
        cmd_init_non_interactive(mode, hours, args.url, args.token, args.gitlab_label, args.conn_label)
    else:
        cmd_init_interactive()


if __name__ == "__main__":
    main()
