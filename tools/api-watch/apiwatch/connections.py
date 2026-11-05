"""connections.yaml（スキル共通の接続設定）を読む。

書式はスキルの config_loader.py と同じ:

    myapi:
      - label: default
        url: https://api.example.com
        token: ${MY_API_TOKEN}

ファイルはコマンド行の `--connections <パス>` で渡したものだけを読む。環境変数や決まった場所からは探さない。
値の中の ${VAR} は書式の一部として展開する（参照先が無ければ、その値を使う前に止める）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .util import ConfigError, REDACT

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


class Connections:
    """--connections で渡された 1 つのファイル。渡されていなければ、接続設定を使う時点で止める。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(os.path.expanduser(str(path))).resolve() if path else None
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            if self.path is None:
                raise ConfigError("接続設定を使う取得元・受け渡し先があります。connections.yaml のパスを "
                                  "--connections で渡してください")
            if not self.path.is_file():
                raise ConfigError(f"接続設定のファイルが見つかりません: {self.path}")
            data = load_yaml_or_json(self.path) or {}
            if not isinstance(data, dict):
                raise ConfigError(f"{self.path}: サービス名をキーにした形で書いてください")
            self._data = data
        return self._data

    def get(self, service: str, label: str = "default") -> dict:
        """(サービス, ラベル) の接続設定を返す。${VAR} を展開し、秘密の値は伏せ字の対象に登録する。"""
        entries = self._load().get(service)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("label", "default")).lower() != label.lower():
                continue
            out = {k: _expand(v) for k, v in entry.items() if k != "label"}
            for k, v in out.items():
                if isinstance(v, str):
                    m = _ENV.search(v)
                    if m:
                        raise ConfigError(f"接続設定 {service}/{label} の {k} が参照する ${{{m.group(1)}}} を展開できません"
                                          f"（{self.path}）")
                    if k not in _PLAIN_KEYS:
                        REDACT.add(v)
            return out
        raise ConfigError(f"接続設定 {service}/{label} が {self.path} にありません")


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
