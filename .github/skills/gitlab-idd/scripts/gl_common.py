#!/usr/bin/env python3
"""
gl_common.py — gitlab-idd 共通ユーティリティ

gl_poll_daemon.py と gl_poll_setup.py で共有する型定義・設定管理・
エージェント CLI 検出ロジック。

インストール時は gl_poll_daemon.py・gl_poll_setup.py と同じディレクトリに配置される。

Requirements: Python 3.11+  /  stdlib only
"""

import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NotRequired, TypedDict


# ---------------------------------------------------------------------------
# 型定義
# ---------------------------------------------------------------------------

class RepoConfig(TypedDict):
    host: str
    project: str
    local_path: str
    token: NotRequired[str]


class DaemonConfig(TypedDict):
    poll_interval_seconds: int
    repos: list[RepoConfig]
    seen_issues: dict[str, list[int]]
    preferred_cli: NotRequired[str]
    mock_cli: NotRequired[bool]  # True にすると CLI の代わりにモックを使用


DEFAULT_POLL_INTERVAL = 300  # 5 分


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def retry_on_network_error(
    func: Callable,
    *args,
    retries: int = 3,
    backoff: float = 2.0,
    logger=None,
    **kwargs,
):
    """一時的なネットワークエラー時に指数バックオフでリトライする。

    urllib.error.URLError と OSError（タイムアウト含む）をリトライ対象とする。
    urllib.error.HTTPError（4xx/5xx）はリトライしない。
    """
    import logging as _logging
    _log = logger or _logging.getLogger(__name__)
    delay = backoff
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except urllib.error.HTTPError:
            raise  # HTTPエラーはリトライしない
        except (urllib.error.URLError, OSError) as exc:
            if attempt == retries:
                raise
            _log.warning("ネットワークエラー (試行 %d/%d): %s — %.0fs 後にリトライ",
                         attempt + 1, retries, exc, delay)
            time.sleep(delay)
            delay *= 2


def title_to_slug(title: str) -> str:
    """イシュータイトルを URL セーフなブランチ名スラッグに変換する。

    非 ASCII 文字（日本語など）は除去するため、ASCII 文字を含まないタイトルは
    空文字になる。その場合は "task" にフォールバックする。
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug[:40].strip("-")
    return slug or "task"


# ---------------------------------------------------------------------------
# エージェント CLI 検出
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentCLI:
    """エージェント CLI の呼び出し情報。"""
    name: str           # "claude" / "codex" / "kiro" / "amazonq"
    binary: str         # 実行ファイルのフルパス
    prompt_args: list[str] = field(default_factory=list)  # プロンプト前のオプション
    via_wsl: bool = False  # Windows + kiro 専用（WSL2 経由）

    def build_command(self, prompt: str) -> list[str]:
        """CLI を起動するコマンドリストを生成する。WSL kiro は wsl prefix。"""
        if self.via_wsl:
            # WSL 経由: --cwd は使用しない（プロンプト内の clone 指示で対処）
            return ["wsl", self.binary] + self.prompt_args + [prompt]
        return [self.binary] + self.prompt_args + [prompt]


_CLI_CANDIDATES: list[tuple[str, str, list[str]]] = [
    ("claude",   "claude",    ["-p"]),
    ("codex",    "codex",     ["-q"]),
    ("kiro",     "kiro-cli",  ["chat", "--no-interactive", "--trust-all-tools"]),
    ("amazonq",  "q",         ["chat"]),
]


def _verify_cli(binary: str) -> bool:
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _check_wsl_kiro() -> bool:
    try:
        r = subprocess.run(["wsl", "kiro-cli", "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def find_available_agent_clis() -> list[AgentCLI]:
    """利用可能なすべてのエージェント CLI を検出して返す（優先順）。"""
    system = platform.system()
    found: list[AgentCLI] = []
    for name, binary_name, prompt_args in _CLI_CANDIDATES:
        if system == "Windows" and name == "kiro":
            if _check_wsl_kiro():
                found.append(AgentCLI(name, binary_name, prompt_args, via_wsl=True))
        else:
            if path := shutil.which(binary_name):
                if _verify_cli(path):
                    found.append(AgentCLI(name, path, prompt_args))
    return found


def find_best_agent_cli(preferred: str | None = None) -> AgentCLI | None:
    """
    最適なエージェント CLI を返す（優先順: claude→codex→kiro→amazonq）。
    preferred が指定された場合はそれを最優先する。
    """
    clis = find_available_agent_clis()
    if not clis:
        return None
    if preferred:
        for cli in clis:
            if cli.name == preferred:
                return cli
    return clis[0]


# ---------------------------------------------------------------------------
# 設定ディレクトリ・ファイル管理
# ---------------------------------------------------------------------------

def get_config_dir() -> Path:
    match platform.system():
        case "Windows":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        case "Darwin":
            base = Path.home() / "Library" / "Application Support"
        case _:
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "gitlab-idd"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def load_config() -> DaemonConfig:
    path = get_config_path()
    if not path.exists():
        return DaemonConfig(
            poll_interval_seconds=DEFAULT_POLL_INTERVAL,
            repos=[],
            seen_issues={},
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: DaemonConfig, *, dry_run: bool = False) -> None:
    path = get_config_path()
    if dry_run:
        print(f"  [DRYRUN] 書き込み予定: {path}")
        preview = json.dumps(config, ensure_ascii=False, indent=2)
        for line in preview.splitlines()[:20]:
            print(f"           {line}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    if platform.system() != "Windows":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
