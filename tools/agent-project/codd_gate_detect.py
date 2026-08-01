#!/usr/bin/env python3
"""codd-gate CLI の実体と、推奨設定の可否に必要な生の検出値を返す sibling 部品。

``tools/agent-project`` 直下に置くパッケージ外モジュールとして、explicit → PATH →
同梱パスの実体解決、バージョン、repos schema、サブコマンド能力だけを検出する。
利用可否の判定、推奨コマンドの組み立て、yaml 注入、doctor 所見、``agent_project``
パッケージへの結線は行わない。依存は標準ライブラリのみ。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BINARY_NAME = "codd-gate"
# agent-project.py の wslpath 存在確認等、軽量プローブと同じ値（d1 2.2）
PROBE_TIMEOUT = 5
_VERSION_RE = re.compile(r"codd-gate (\d+)\.(\d+)\.(\d+)")
_SUBCOMMANDS_RE = re.compile(r"\{([\w,]+)\}")


def resolve_codd_gate(explicit: "str | None" = None, which=shutil.which) -> "list[str] | None":
    """codd-gate の起動 argv prefix を解決する。見つからなければ None。

    resolve_agent_flow と対称の解決連鎖（explicit → PATH → 同梱パス）を辿るが、
    agent-flow と異なり codd-gate は任意機能（無くても agent-project は動く）なので、
    同梱パスにも実体が無ければ「不明な起動コマンドを組み立てない」意味で None を返す。
    戻り値が str ではなく argv prefix の list なのは、同梱パス経由の起動時に
    Python インタプリタを明示する必要があるため（resolve_agent_flow と同じ理由）。
    """
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = which(BINARY_NAME)
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent / "codd-gate" / "codd-gate.py"
    if local.exists():
        return [sys.executable, str(local)]
    return None


def get_version(
    binary: "list[str]", run=subprocess.run, timeout: int = PROBE_TIMEOUT
) -> "tuple[int, int, int] | None":
    """`<binary> --version` の出力からバージョンタプルを得る（d1 2.2）。

    `--version` は argparse の `action="version"` で exit 0 直終了する経路（サブコマンド未指定時の
    通常エラー exit 2 とは別）。timeout・非 0 終了・パース不能はすべて「不明」（None）に倒す——
    「わからない」を「大丈夫」に丸めない（d1 の一貫方針）。
    """
    try:
        proc = run([*binary, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    m = _VERSION_RE.search(proc.stdout)
    return tuple(int(g) for g in m.groups()) if m else None


def check_repos_schema_compat(repos_path: "str | Path") -> "tuple[bool, str]":
    """repos.json（`export_repo_registry` の出力）が `repos.schema.json` の最小要件
    （トップレベル object、`_` 始まり以外の値が object）を満たすか（d1 2.3(a) の出力契約チェック）。

    schemas/ 実データにバージョンフィールドは無いため（s5 の結論）、semver 比較の代わりに
    構造チェックで代替する。読み込み・パース失敗も非互換として扱う（理由は戻り値の2要素目に残す）。
    """
    try:
        data = json.loads(Path(repos_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"repos.json を読み込めない: {exc}"
    if not isinstance(data, dict):
        return False, "repos.json のトップレベルが object ではない"
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if not isinstance(value, dict):
            return False, f"repos.json のエントリ '{key}' が object ではない"
    return True, ""


def detect_capabilities(
    binary: "list[str]", run=subprocess.run, timeout: int = PROBE_TIMEOUT
) -> "dict[str, bool]":
    """`--help` / `<サブコマンド> --help` を実プローブし、verify・tasks サブコマンドと
    --debt フラグの利用可能性を能力フラグ（`{"verify": bool, "tasks": bool, "debt": bool}`）
    として返す。

    `get_version` はバージョン文字列からの間接的な対応関係だが、こちらは実バイナリの
    argparse 出力に直接問い合わせるため、`--version` が壊れていても独立に成立する
    （d2 4.1/4.3 が組み立てる `verify --strict` / `tasks --debt` の実引数が、実際にこの
    バイナリで受理されるかの裏取り）。プローブ失敗（timeout・非 0 終了・出力不一致）は
    すべて False に倒す（d1 の「不明・不足は連携しない側に倒す」方針を能力単位に適用）。
    """
    capabilities = {"verify": False, "tasks": False, "debt": False}
    subcommands = _list_subcommands(binary, run=run, timeout=timeout)
    capabilities["verify"] = "verify" in subcommands
    capabilities["tasks"] = "tasks" in subcommands
    debt_checks = [
        _subcommand_supports_flag(binary, sub, "--debt", run=run, timeout=timeout)
        for sub in ("verify", "tasks") if capabilities[sub]
    ]
    capabilities["debt"] = bool(debt_checks) and all(debt_checks)
    return capabilities


def _list_subcommands(binary: "list[str]", run=subprocess.run, timeout: int = PROBE_TIMEOUT) -> "set[str]":
    try:
        proc = run([*binary, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    m = _SUBCOMMANDS_RE.search(proc.stdout)
    return set(m.group(1).split(",")) if m else set()


def _subcommand_supports_flag(
    binary: "list[str]", subcommand: str, flag: str, run=subprocess.run, timeout: int = PROBE_TIMEOUT
) -> bool:
    try:
        proc = run([*binary, subcommand, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and flag in proc.stdout
