#!/usr/bin/env python3
"""
setup-token-reduction.py — kiro-loop 配下の kiro-cli に rtk / caveman を一発適用する暫定パッチ。

kiro-loop 本体は改修しない。既存の 2 つの設定面だけを書き換える:

  1. agent-session-commands（~/.agents/session/session.json）
     dashboard が編集できる汎用契約。ここに chat コマンド（/caveman、rtk 前置指示）を
     追加する。**ON/OFF の正は常にこのファイル** — dashboard からエントリを消せば無効化される。
  2. エージェント定義（~/.kiro/agents/kiro-loop-concurrency.json）の hooks
     fresh_context の /clear でセッション開始コマンドの効果が消えるため、
     userPromptSubmit（既定）/ agentSpawn hook で指示を再注入する補強。
     hook コマンドは session.json に tokred-* エントリが存在するときだけ発火するため、
     dashboard 側のスイッチに従属する（設定の二重管理にならない）。

rtk / caveman に特化した暫定スクリプト。恒久対応（汎用の agent-tuning 契約と
kiro-loop 改修）は docs/plans/2026-08-03-kiro-loop-token-reduction-proposals.md を参照。

使い方:
  python setup-token-reduction.py             # 適用（冪等）
  python setup-token-reduction.py --status    # 現在の適用状態を表示
  python setup-token-reduction.py --revert    # 追加分をすべて撤去
  python setup-token-reduction.py --dry-run   # 変更内容の表示のみ
  オプション: --caveman-mode lite|full|ultra（既定 full）
             --inject userPromptSubmit|agentSpawn（既定 userPromptSubmit）
             --skip-rtk / --skip-caveman

依存: Python 3.9+（stdlib のみ）。rtk バイナリ / caveman スキルの導入自体は
install.py（setup_rtk / setup_caveman）または各公式インストーラで行うこと。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# パス定義（環境変数で上書き可能なものは実行時に解決する）
# ---------------------------------------------------------------------------

MARKER = "token-reduction"          # hook コマンド識別用（revert はこの部分文字列で判定）
ID_PREFIX = "tokred-"               # session.json のエントリ識別用
UPDATED_BY = "setup-token-reduction"


def _home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def _session_file() -> Path:
    base = os.environ.get("AGENT_SESSION_DIR") or str(_home() / ".agents" / "session")
    return Path(os.path.expanduser(base)) / "session.json"


def _agent_file() -> Path:
    return _home() / ".kiro" / "agents" / "kiro-loop-concurrency.json"


def _cache_dir() -> Path:
    return _home() / ".kiro" / "cache" / MARKER


CAVEMAN_PREAMBLE = """\
[出力スタイル指示] 応答は caveman 圧縮モード（{mode}）で行うこと:
- 前置き・確認・冗長な説明を省き、要点のみ簡潔な断片で述べる
- コード・コマンド・エラーメッセージ・数値は省略せず正確に保つ
- 人間へ提出する成果物（MR コメント・Issue 報告などの本文）は通常の文体で書く
"""

RTK_INSTRUCTIONS = """\
[ツール出力削減指示] シェルコマンドは `rtk` を前置して実行すること（例: rtk git status, rtk ls src/）。
- rtk はコマンド出力を LLM 向けに圧縮するラッパー（git / ls / grep / find / cat / diff / tree / 各種テスト・ビルドに対応）
- rtk 非対応のコマンドや rtk で失敗したコマンドはそのまま実行してよい
- エラー詳細の精査など完全な生出力が必要なときは rtk を外して再実行してよい
"""

# rtk 前置の chat 指示（session-commands 用の 1 行版）
RTK_CHAT_DIRECTIVE = (
    "今後シェルコマンドを実行するときは `rtk` を前置してください（例: rtk git status）。"
    "rtk 非対応・失敗時はそのまま実行して構いません。"
)


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _backup_once(path: Path) -> None:
    """初回適用時のみ元ファイルを退避する（既に退避済みなら何もしない）。"""
    backup = path.with_name(path.name + ".tokred-backup")
    if path.is_file() and not backup.exists():
        shutil.copy2(path, backup)


def _hook_command(entry_id: str, payload_file: str, require_rtk: bool = False) -> str:
    """session.json に対応エントリがあるときだけ payload を注入する hook コマンドを組み立てる。

    - tokred-* エントリが session.json から消えていれば何も出力しない（dashboard が正）
    - session.json 全体が enabled:false なら何も出力しない
    - require_rtk=True の場合は rtk バイナリが無ければ何も出力しない
    """
    session = '"${AGENT_SESSION_DIR:-$HOME/.agents/session}/session.json"'
    checks = [
        f"f={session}",
        '[ -f "$f" ]',
        f'grep -q {entry_id} "$f"',
        '! grep -Eq "\\"enabled\\":[[:space:]]*false" "$f"',
    ]
    if require_rtk:
        checks.append("command -v rtk >/dev/null 2>&1")
    body = "; ".join(checks[:1]) + " && " + " && ".join(checks[1:])
    return f"sh -c '{body} && cat \"$HOME/.kiro/cache/{MARKER}/{payload_file}\" || true'"


# ---------------------------------------------------------------------------
# 事前チェック
# ---------------------------------------------------------------------------

def preflight(skip_rtk: bool, skip_caveman: bool) -> None:
    if not skip_rtk:
        if shutil.which("rtk") or (_home() / ".local" / "bin" / "rtk").is_file():
            print("  ✓ rtk バイナリ: あり")
        else:
            print("  ⚠ rtk バイナリが見つかりません。指示は入りますが hook 側は発火しません")
            print("    導入: python install.py --agent kiro（setup_rtk）または rtk-ai/rtk の install.sh")
    if not skip_caveman:
        skill = _home() / ".kiro" / "skills" / "caveman" / "SKILL.md"
        if skill.is_file():
            print("  ✓ caveman スキル: あり")
        else:
            print("  ⚠ caveman スキルが見つかりません（/caveman コマンドは失敗します）")
            print("    導入: npx -y skills add JuliusBrussee/caveman --skill '*' -a kiro-cli --yes -g")


# ---------------------------------------------------------------------------
# 1) session.json（agent-session-commands 契約）の編集
# ---------------------------------------------------------------------------

def _build_entries(caveman_mode: str, skip_rtk: bool, skip_caveman: bool) -> list[dict]:
    when = {"engines": ["kiro-loop"], "agent_cli": ["kiro"]}
    entries: list[dict] = []
    if not skip_caveman:
        entries.append({
            "id": f"{ID_PREFIX}caveman",
            "mode": "chat",
            "run": f"/caveman {caveman_mode}",
            "when": dict(when),
        })
    if not skip_rtk:
        entries.append({
            "id": f"{ID_PREFIX}rtk",
            "mode": "chat",
            "run": RTK_CHAT_DIRECTIVE,
            "when": dict(when),
        })
    return entries


def patch_session_json(entries: list[dict], remove: bool, dry_run: bool) -> None:
    path = _session_file()
    data = _load_json(path) or {"version": 1, "revision": 0, "enabled": True, "commands": []}
    commands = [c for c in data.get("commands") or [] if isinstance(c, dict)]
    kept = [c for c in commands if not str(c.get("id", "")).startswith(ID_PREFIX)]
    new_commands = kept if remove else kept + entries

    if new_commands == commands:
        print(f"  = session.json: 変更なし ({path})")
        return
    action = "撤去" if remove else "追加"
    ids = [c["id"] for c in (entries if not remove else commands) if str(c.get("id", "")).startswith(ID_PREFIX)]
    print(f"  → session.json: {action} {ids} (revision {data.get('revision', 0)} → {int(data.get('revision', 0)) + 1})")
    if dry_run:
        return
    _backup_once(path)
    data["commands"] = new_commands
    data["revision"] = int(data.get("revision", 0)) + 1
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    data["updated_by"] = UPDATED_BY
    _atomic_write_json(path, data)


# ---------------------------------------------------------------------------
# 2) エージェント定義（hooks）の編集
# ---------------------------------------------------------------------------

def patch_agent_json(trigger: str, skip_rtk: bool, skip_caveman: bool,
                     remove: bool, dry_run: bool) -> None:
    path = _agent_file()
    data = _load_json(path)
    if data is None:
        print(f"  ⚠ エージェント定義が見つかりません: {path}")
        print("    kiro-loop の install.sh を先に実行してください（hooks 補強はスキップ。"
              "session-commands 経路のみで動作します）")
        return

    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    # 既存の全トリガーから当スクリプト由来（MARKER を含む）のエントリを外す
    cleaned: dict = {}
    for trig, items in hooks.items():
        if not isinstance(items, list):
            cleaned[trig] = items
            continue
        kept = [h for h in items
                if not (isinstance(h, dict) and MARKER in str(h.get("command", "")))]
        if kept:
            cleaned[trig] = kept

    added: list[dict] = []
    if not remove:
        if not skip_caveman:
            added.append({"type": "command",
                          "command": _hook_command(f"{ID_PREFIX}caveman", "caveman-preamble.md")})
        if not skip_rtk:
            added.append({"type": "command",
                          "command": _hook_command(f"{ID_PREFIX}rtk", "rtk-instructions.md",
                                                   require_rtk=True)})
        if added:
            cleaned.setdefault(trigger, [])
            cleaned[trigger] = list(cleaned[trigger]) + added

    if cleaned == hooks:
        print(f"  = エージェント定義: 変更なし ({path})")
        return
    action = "撤去" if remove else f"追加 (hooks.{trigger} に {len(added)} 件)"
    print(f"  → エージェント定義: {action}")
    if dry_run:
        return
    _backup_once(path)
    data["hooks"] = cleaned
    _atomic_write_json(path, data)


# ---------------------------------------------------------------------------
# 3) 注入ペイロードの配置
# ---------------------------------------------------------------------------

def write_payloads(caveman_mode: str, skip_rtk: bool, skip_caveman: bool,
                   remove: bool, dry_run: bool) -> None:
    cache = _cache_dir()
    if remove:
        if cache.exists():
            print(f"  → ペイロード撤去: {cache}")
            if not dry_run:
                shutil.rmtree(cache, ignore_errors=True)
        else:
            print("  = ペイロード: なし（変更なし）")
        return

    print(f"  → ペイロード配置: {cache}")
    if dry_run:
        return
    cache.mkdir(parents=True, exist_ok=True)
    if not skip_caveman:
        (cache / "caveman-preamble.md").write_text(
            CAVEMAN_PREAMBLE.format(mode=caveman_mode), encoding="utf-8")
    if not skip_rtk:
        (cache / "rtk-instructions.md").write_text(RTK_INSTRUCTIONS, encoding="utf-8")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def show_status() -> None:
    print("[setup-token-reduction] 適用状態:")
    session = _load_json(_session_file()) or {}
    ids = [str(c.get("id")) for c in session.get("commands") or []
           if isinstance(c, dict) and str(c.get("id", "")).startswith(ID_PREFIX)]
    print(f"  session.json ({_session_file()}):")
    print(f"    tokred エントリ: {ids or 'なし'} / revision: {session.get('revision', '-')}"
          f" / enabled: {session.get('enabled', True)}")

    agent = _load_json(_agent_file())
    if agent is None:
        print(f"  エージェント定義 ({_agent_file()}): なし")
    else:
        hooks = agent.get("hooks") if isinstance(agent.get("hooks"), dict) else {}
        ours = {trig: sum(1 for h in items if isinstance(h, dict) and MARKER in str(h.get("command", "")))
                for trig, items in hooks.items() if isinstance(items, list)}
        ours = {k: v for k, v in ours.items() if v}
        print(f"  エージェント定義 hooks: {ours or 'tokred なし'}")

    cache = _cache_dir()
    files = sorted(p.name for p in cache.glob("*.md")) if cache.exists() else []
    print(f"  ペイロード ({cache}): {files or 'なし'}")

    rtk_ok = bool(shutil.which("rtk") or (_home() / ".local" / "bin" / "rtk").is_file())
    caveman_ok = (_home() / ".kiro" / "skills" / "caveman" / "SKILL.md").is_file()
    print(f"  外部ツール: rtk={'あり' if rtk_ok else 'なし'} / caveman スキル={'あり' if caveman_ok else 'なし'}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="kiro-loop 配下の kiro-cli へ rtk / caveman を一発適用する暫定パッチ")
    parser.add_argument("--status", action="store_true", help="適用状態の表示のみ")
    parser.add_argument("--revert", action="store_true", help="追加分をすべて撤去する")
    parser.add_argument("--dry-run", action="store_true", help="変更内容の表示のみ（書き込まない）")
    parser.add_argument("--caveman-mode", choices=["lite", "full", "ultra"], default="full",
                        help="caveman の圧縮レベル（既定 full）")
    parser.add_argument("--inject", choices=["userPromptSubmit", "agentSpawn"],
                        default="userPromptSubmit",
                        help="hooks の注入トリガー。userPromptSubmit は /clear 後も毎プロンプト再注入"
                             "（既定・堅牢）、agentSpawn はセッション生成時のみ（低コスト）")
    parser.add_argument("--skip-rtk", action="store_true", help="rtk 分を設定しない")
    parser.add_argument("--skip-caveman", action="store_true", help="caveman 分を設定しない")
    args = parser.parse_args()

    if args.status:
        show_status()
        return 0

    if args.skip_rtk and args.skip_caveman and not args.revert:
        print("[setup-token-reduction] --skip-rtk と --skip-caveman が両方指定されているため何もしません")
        return 0

    mode = "撤去 (--revert)" if args.revert else "適用"
    print(f"[setup-token-reduction] {mode}{'（dry-run）' if args.dry_run else ''}:")
    if not args.revert:
        preflight(args.skip_rtk, args.skip_caveman)

    write_payloads(args.caveman_mode, args.skip_rtk, args.skip_caveman,
                   remove=args.revert, dry_run=args.dry_run)
    patch_agent_json(args.inject, args.skip_rtk, args.skip_caveman,
                     remove=args.revert, dry_run=args.dry_run)
    entries = _build_entries(args.caveman_mode, args.skip_rtk, args.skip_caveman)
    patch_session_json(entries, remove=args.revert, dry_run=args.dry_run)

    if not args.dry_run:
        print("完了。反映はペインの再起動から（既存セッションには遡及しません）。")
        print("ON/OFF は dashboard のセッション開始コマンド編集（tokred-* エントリ）で行えます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
