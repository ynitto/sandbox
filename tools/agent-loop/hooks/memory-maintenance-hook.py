#!/usr/bin/env python3
"""Run the deterministic memory-store maintenance batch without an LLM prompt.

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.2（K1）

LLM を要らない整理だけをここで回す（索引再構築・忘却曲線の更新・wiki lint・audit 収集）。
**削除は絶対に走らせない**——`cleanup_memory.py` は dry-run すら自律実行の対象にせず、
判断が要る整理は「記憶メンテナンス当番」の定期プロンプトへ回す（C4・C5）。
記憶ファイルを書き換えるのは各スキル自身のスクリプトのままで、このフックは呼ぶだけ。

hook_config:
  skill_home: ~/.claude/skills      # ltm-use / wiki-use のスクリプトを探す親
  ltm_scripts / wiki_scripts        # skill_home に載っていない置き方のときの直接指定
  ltm_scopes: [home]                # build_index / review の対象スコープ
  python: python3                   # スクリプトを走らせる実行系
  agent_audit: agent-audit          # 収集に使う CLI（audit_dir / budget_dir も渡せる）
  timeout: 300
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

DEFAULT_TIMEOUT = 300


def _path(value: str) -> str:
    return os.path.abspath(os.path.expanduser(str(value)))


def _label(command: "list[str]") -> str:
    """ログに出す短い名前（スクリプト名か CLI 名 + サブコマンド）。"""
    for arg in command:
        if arg.endswith(".py"):
            return os.path.basename(arg)
    return " ".join([os.path.basename(command[0])] + command[1:2])


def _scripts_dir(cfg: dict, key: str, skill: str) -> "str | None":
    """スクリプト置き場を解決する。見つからない層は黙って飛ばす（未導入なら正常）。"""
    explicit = cfg.get(key)
    if explicit:
        return _path(explicit)
    skill_home = cfg.get("skill_home")
    if not skill_home:
        return None
    cand = os.path.join(_path(skill_home), skill, "scripts")
    return cand if os.path.isdir(cand) else None


def _commands(cfg: dict) -> "list[list[str]]":
    """実行するコマンド列を決定的に組み立てる（**非破壊のものだけ**）。"""
    python = str(cfg.get("python") or "python3")
    scopes = [str(s) for s in (cfg.get("ltm_scopes") or ["home"]) if str(s)]
    commands: "list[list[str]]" = []

    ltm = _scripts_dir(cfg, "ltm_scripts", "ltm-use")
    if ltm:
        for scope in scopes:
            # 索引の再構築と忘却曲線の一括更新。どちらも記憶を消さない
            commands.append([python, os.path.join(ltm, "build_index.py"),
                             "--scope", scope, "--force"])
            commands.append([python, os.path.join(ltm, "review_memory.py"),
                             "--scope", scope, "--update-retention"])
    wiki = _scripts_dir(cfg, "wiki_scripts", "wiki-use")
    if wiki:
        # lint は報告のみ（--fix は付けない。index.md の書き換えは当番の判断に残す）
        commands.append([python, os.path.join(wiki, "wiki_lint.py")])

    audit = [str(cfg.get("agent_audit") or "agent-audit")]
    for key, flag in (("audit_dir", "--audit-dir"), ("budget_dir", "--budget-dir")):
        if cfg.get(key):
            audit += [flag, _path(cfg[key])]
    commands.append(audit + ["collect", "--source", "memory-store"])
    return commands


def check(hook_config: "dict[str, Any] | None" = None) -> None:
    """純バッチ——常に None を返し、LLM へは 1 文字も送らない。

    整理の一部が失敗しても残りは回す（索引が壊れていても収集は続けたい）。ただし
    最後の 1 つでも失敗していたら例外にして、loop のログへ理由を残す——黙って
    「整理が回っている」ことにしない。
    """
    cfg = (hook_config or {}).get("hook_config") or {}
    try:
        timeout = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    failures: "list[str]" = []
    for command in _commands(cfg):
        try:
            result = subprocess.run(command, text=True, capture_output=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{_label(command)}: {exc}")
            continue
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            failures.append(f"{_label(command)}: exit {result.returncode}"
                            f"{' — ' + detail[-1] if detail else ''}")
    if failures:
        raise RuntimeError("memory maintenance failed: " + " / ".join(failures))
    return None
