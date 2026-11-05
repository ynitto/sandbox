"""プラグインが足す「フィルタ」と「チェック」の登録先。本体はこの口だけを知る。

    フィルタ  テンプレートの {{ 値 | 名前: 引数 }} で使う関数。fn(value, arg) -> 値
              arg は「:」の後ろを JSON として読んだもの（無ければ None）。設定のどのテンプレートでも使え、
              `render` コマンドで文章（プロンプトなど）を組むのにも使う
    チェック  受け渡し先の実行の前後に呼ぶ判定。fn(ctx: CheckContext) -> bool
              受け渡し先の skip_if（真なら実行しない）と handled_before_if（真なら、処理し終えたのは
              実行前の状態までにする）に名前で書く

どちらも名前で登録し、使う時点で探す（--plugin を付け忘れたら、その時点で案内して止める）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .util import ConfigError


@dataclass
class _Entry:
    fn: Callable
    provider: str


_FILTERS: dict[str, _Entry] = {}
_CHECKS: dict[str, _Entry] = {}


def _register(table: dict, kind: str, name: str, fn: Callable, provider: str) -> None:
    if not name.replace("_", "").replace(".", "").isalnum():
        raise ConfigError(f"{kind}の名前は英数字・_ . で書いてください: {name}")
    if name in table and table[name].provider != provider:
        raise ConfigError(f"{kind} {name} が 2 か所（{table[name].provider}・{provider}）で登録されています")
    table[name] = _Entry(fn, provider)


def add_filter(name: str, fn: Callable[[Any, Any], Any], provider: str) -> None:
    _register(_FILTERS, "フィルタ", name, fn, provider)


def add_check(name: str, fn: Callable[["CheckContext"], bool], provider: str) -> None:
    _register(_CHECKS, "チェック", name, fn, provider)


def get_filter(name: str) -> Callable | None:
    e = _FILTERS.get(name)
    return e.fn if e else None


def get_check(name: str) -> Callable[["CheckContext"], bool]:
    e = _CHECKS.get(name)
    if e is None:
        known = ", ".join(sorted(_CHECKS)) or "なし"
        raise ConfigError(f"チェック {name} がありません（使えるもの: {known}）。"
                          "プラグインのチェックなら --plugin を付けて実行してください")
    return e.fn


def reset_plugins() -> None:
    for table in (_FILTERS, _CHECKS):
        for name in [n for n, e in table.items() if e.provider != "本体"]:
            del table[name]


@dataclass
class CheckContext:
    """チェックに渡すもの。HTTP は受け渡し元のルールの取得元の接続で送り、要求の間隔・上限・失敗の待ちを分け合う。"""
    event: dict                     # イベント（refresh があれば取り直したレコード入り）
    delivery: dict                  # id・rule・target・attempt・started_at（実行を始めた UNIX 秒）
    options: dict                   # 設定に書いたチェックの options
    conn: dict                      # 取得元の接続設定（connections.yaml の値）
    _get: Callable = field(repr=False, default=None)
    _render: Callable = field(repr=False, default=None)
    _log: Callable = field(repr=False, default=None)

    def render(self, value: Any) -> Any:
        """{{ event.* }}・{{ delivery.* }}・{{ options.* }}・{{ conn.* }}・{{ env.* }} を展開する。"""
        return self._render(value)

    def get_json(self, url: str, headers: dict | None = None, query: dict | None = None) -> Any:
        """GET して JSON を返す（404 なら None）。url・headers・query はテンプレートとして展開する。
        headers を書かなければ取得元のヘッダー（認証）を使う。失敗したら TransientError / PermanentError。"""
        return self._get(url, headers, query)

    def log(self, msg: str) -> None:
        self._log(msg)
