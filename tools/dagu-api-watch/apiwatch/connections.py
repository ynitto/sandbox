"""connections.yaml（スキル共通の接続設定）を読む。

書式はスキルの config_loader.py と同じ:

    myapi:
      - label: default
        url: https://api.example.com
        token: ${MY_API_TOKEN}

探す順（(サービス, ラベル) ごとに最初に見つかったものを使う）:
    1. 設定の connections_file、または環境変数 APIWATCH_CONNECTIONS_FILE / SKILL_CONNECTIONS_FILE
       （指定したらそのファイルだけを見る）
    2. 設定ファイルのフォルダから上へ、続いて作業フォルダから上へ:
       <フォルダ>/.github/connections.yaml・.claude/・.copilot/・.kiro/
    3. ホーム: ~/.copilot/・~/.claude/・~/.github/・~/.kiro/ の connections.yaml
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .util import ConfigError, REDACT

AGENT_DIRS = (".github", ".claude", ".copilot", ".kiro")
HOME_DIRS = (".copilot", ".claude", ".github", ".kiro")
_ENV = re.compile(r"\$\{([^}]+)\}")
# 値として伏せなくてよい項目（URL・ユーザー名・ラベル）
_PLAIN_KEYS = {"label", "url", "base_url", "api_url", "user", "username", "login", "project", "group", "org"}


def load_yaml_or_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        import json
        return json.loads(text)
    try:
        import yaml  # type: ignore[import]
    except ImportError as e:
        raise ConfigError(f"{path}: YAML を読むには PyYAML が要ります（pip install pyyaml）。"
                          "JSON で書けば追加のパッケージは要りません") from e
    return yaml.safe_load(text)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    return value


def candidate_files(config_dir: Path | None, explicit: str | None) -> list[Path]:
    override = explicit or os.environ.get("APIWATCH_CONNECTIONS_FILE") or os.environ.get("SKILL_CONNECTIONS_FILE")
    if override:
        p = Path(os.path.expanduser(override))
        if not p.is_absolute() and config_dir is not None and explicit:
            p = config_dir / p
        return [p]
    found: list[Path] = []

    def add(p: Path) -> None:
        if p.is_file() and p.resolve() not in [f.resolve() for f in found]:
            found.append(p)

    starts = []
    if config_dir is not None:
        starts.append(config_dir.resolve())
    starts.append(Path.cwd().resolve())
    for start in starts:
        for anc in [start, *start.parents]:
            for d in AGENT_DIRS:
                add(anc / d / "connections.yaml")
    home = Path.home()
    for d in HOME_DIRS:
        add(home / d / "connections.yaml")
    return found


class Connections:
    def __init__(self, config_dir: Path | None = None, explicit: str | None = None):
        self.files = candidate_files(config_dir, explicit)
        self._data: list[tuple[Path, dict]] | None = None

    def _load(self) -> list[tuple[Path, dict]]:
        if self._data is None:
            self._data = []
            for f in self.files:
                if not f.is_file():
                    raise ConfigError(f"接続設定のファイルが見つかりません: {f}")
                data = load_yaml_or_json(f) or {}
                if not isinstance(data, dict):
                    raise ConfigError(f"{f}: サービス名をキーにした形で書いてください")
                self._data.append((f, data))
        return self._data

    def get(self, service: str, label: str = "default") -> dict:
        """(サービス, ラベル) の接続設定を返す。${VAR} を展開し、秘密の値は伏せ字の対象に登録する。"""
        for path, data in self._load():
            entries = data.get(service)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("label", "default")).lower() != label.lower():
                    continue
                out = {k: _expand(v) for k, v in entry.items() if k != "label"}
                for k, v in out.items():
                    if isinstance(v, str):
                        m = _ENV.search(v)
                        if m:
                            raise ConfigError(
                                f"接続設定 {service}/{label} の {k} が参照する環境変数 {m.group(1)} が設定されていません"
                                f"（{path}）")
                        if k not in _PLAIN_KEYS:
                            REDACT.add(v)
                return out
        where = "、".join(str(p) for p in self.files) or "（見つかったファイルなし）"
        raise ConfigError(f"接続設定 {service}/{label} が見つかりません。探したファイル: {where}")


def parse_ref(ref: Any, where: str) -> tuple[str, str] | None:
    """`gitlab` / `gitlab:work` / {service, label} を (サービス, ラベル) にする。"""
    if ref is None:
        return None
    if isinstance(ref, str):
        service, _, label = ref.partition(":")
        return service.strip(), (label.strip() or "default")
    if isinstance(ref, dict) and "service" in ref:
        return str(ref["service"]), str(ref.get("label") or "default")
    raise ConfigError(f"{where}: connection は 'サービス' か 'サービス:ラベル' で書いてください")
