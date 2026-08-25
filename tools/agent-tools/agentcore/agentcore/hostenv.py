"""agentcore.hostenv — ローカル推論サーバへ届く環境を作る、唯一の実装。

## なぜ独立したモジュールなのか

`~/.profile` からの `OLLAMA_*` 補完とプロキシ迂回は、**LAN 上の ollama を叩く 3 つの
adapter すべて**（`ollama_adapter` / `aider_adapter` / `opencode_adapter`）が起動時に
必要とする。以前は `ollama_adapter` を正典とし、単体ファイルで配っていた 2 つが同じ
コードを複製して持ち、「直すときは 3 箇所を揃えること」という注記と AST 比較テストで
ずれを検出していた。

複製が必要だったのは**配布形態**の都合（単体ファイルは agentcore を import できない）で
あって、設計上の必然ではない。agent-herd が 3 adapter を 1 つの zipapp に畳んだことで
その制約が消えたので、写しを畳んでここ 1 実装にした（C7）。

振る舞いは複製時代の正典（`ollama_adapter.py`）と同一である。直すときはここだけを直す。

設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §1.1 / §6。
仕様: docs/specs/agent-herd-spec.md §6。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse


_PROFILE_ENV_PREFIXES = ("OLLAMA_", "AGENT_OLLAMA_")
_PROFILE_ENV_EXACT = ("NO_PROXY", "no_proxy")


def _complete_ollama_env() -> None:
    """OLLAMA_HOST / OLLAMA_API_BASE を相互に補い、ollama をプロキシ対象から外す。

    片方しか export していない環境でも、両方の読み手（ollama 系 CLI は OLLAMA_HOST、
    aider/litellm は OLLAMA_API_BASE）が同じサーバへ向くようにする。プロキシ迂回は
    NO_PROXY の取り込みだけに頼らない——親環境が不完全な NO_PROXY を持っていると
    profile の値は取り込まれず（環境が勝つ規則）、接続がプロキシへ流れて
    504 Gateway Timeout で落ちるため、ollama のホストを常に両表記へ追記する。
    NO_PROXY と no_proxy は読み手によって参照順が違う（urllib は小文字が勝つ）ので、
    両者の和集合を作って同じ値に揃える。
    """
    host = os.environ.get("OLLAMA_HOST", "")
    base = os.environ.get("OLLAMA_API_BASE", "")
    if host and not base:
        os.environ["OLLAMA_API_BASE"] = host if "://" in host else f"http://{host}"
    elif base and not host:
        os.environ["OLLAMA_HOST"] = base
    target = os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_HOST") or ""
    try:
        hostname = urllib.parse.urlsplit(
            target if "://" in target else f"//{target}").hostname
    except ValueError:
        hostname = None
    hosts = [hostname] if hostname else ["localhost", "127.0.0.1"]
    entries: "list[str]" = []
    for var in ("NO_PROXY", "no_proxy"):
        for item in os.environ.get(var, "").split(","):
            item = item.strip()
            if item and item not in entries:
                entries.append(item)
    entries.extend(h for h in hosts if h not in entries)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(entries)


def _import_profile_env(path: str) -> dict:
    profile = os.path.expanduser(path)
    if not os.path.isfile(profile):
        return {}
    # profile を source した後の環境を JSON で受け取る。stdin は閉じる——
    # このプロセスの stdin はプロンプト本文なので、profile に読ませてはいけない。
    dump = "import json, os; print(json.dumps(dict(os.environ)))"
    try:
        proc = subprocess.run(
            ["sh", "-c", '. "$1" >/dev/null 2>&1; exec "$2" -c "$3"',
             "sh", profile, sys.executable, dump],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    imported: dict = {}
    for name, value in data.items():
        if ((name.startswith(_PROFILE_ENV_PREFIXES) or name in _PROFILE_ENV_EXACT)
                and name not in os.environ and isinstance(value, str)):
            os.environ[name] = value
            imported[name] = value
    return imported


def load_profile_env(path: str = "~/.profile") -> dict:
    """~/.profile の OLLAMA_* / NO_PROXY を補完し、プロキシ迂回を確実にする。

    エンジン（agent-project / agent-flow / agent-amigos）は agent CLI を
    **非ログインシェルの subprocess** として起動するため、~/.profile に書いた
    `export OLLAMA_HOST=...` / `export NO_PROXY=...` は届かない。届かないと
    既定の 127.0.0.1 へ向かって「接続できません」で env 落ちするか、接続が
    社内プロキシへ流れて 504 Gateway Timeout になる——設定はしてあるのに
    動かない、という一番説明しづらい失敗になるので、CLI の入口で 1 回だけ自力で読む。

    - 環境に既にある変数が常に勝つ（呼び出し側の明示指定を profile で潰さない）
    - OLLAMA_HOST / OLLAMA_API_BASE / NO_PROXY（か no_proxy）が全部そろっていれば
      profile は読まない（構成済みの環境へ余計な subprocess を足さない）
    - profile の評価は sh の子プロセスに閉じ込め、失敗は黙って無視する
      （profile が壊れていても推論を止める理由にはしない）
    - 最後に OLLAMA_HOST ⇄ OLLAMA_API_BASE を相互補完し、ollama のホストを
      NO_PROXY / no_proxy へ追記する（この段は profile の有無と無関係に必ず行う）

    戻り値: profile から実際に取り込んだ変数（テストと診断のため）。
    """
    imported: dict = {}
    if not (os.environ.get("OLLAMA_HOST") and os.environ.get("OLLAMA_API_BASE")
            and (os.environ.get("NO_PROXY") or os.environ.get("no_proxy"))):
        imported = _import_profile_env(path)
    _complete_ollama_env()
    return imported
