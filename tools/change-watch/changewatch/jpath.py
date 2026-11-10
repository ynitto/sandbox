"""レコードの中の値を指すパス。

パスの書き方:
    title                 キー
    user.name             入れ子
    labels[0]             配列の添字
    labels[*].name        配列の全要素（結果は配列）
    ["a.b"].c             ドットを含むキー
    $ / 空文字            全体
先頭の `$.` は付けても付けなくてもよい。

値が無い（キーが無い・添字が範囲外）ことは MISSING で表し、null（None）と区別する。
"""
from __future__ import annotations

import re
from typing import Any

from .util import ConfigError


class _Missing:
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()

_TOKEN = re.compile(r"""
    \.?(?P<key>[^.\[\]]+)            # .key / key
  | \[(?P<idx>-?\d+)\]               # [0]
  | \[(?P<wild>\*)\]                 # [*]
  | \[(?P<q>["'])(?P<qkey>.*?)(?P=q)\]   # ["key"]
""", re.X)

_cache: dict[str, tuple] = {}


def parse(path: str) -> tuple:
    """パスを (('k', name) | ('i', n) | ('*',)) の並びにする。"""
    if path in _cache:
        return _cache[path]
    p = (path or "").strip()
    if p in ("", "$", "."):
        _cache[path] = ()
        return ()
    if p.startswith("$."):
        p = p[2:]
    elif p.startswith("$["):
        p = p[1:]
    out: list[tuple] = []
    pos = 0
    while pos < len(p):
        m = _TOKEN.match(p, pos)
        if not m or m.end() == pos:
            raise ConfigError(f"パスを読めません: {path!r}（{pos} 文字目）")
        if m.group("key") is not None:
            k = m.group("key")
            out.append(("*",) if k == "*" else ("k", k))
        elif m.group("idx") is not None:
            out.append(("i", int(m.group("idx"))))
        elif m.group("wild") is not None:
            out.append(("*",))
        else:
            out.append(("k", m.group("qkey")))
        pos = m.end()
    t = tuple(out)
    _cache[path] = t
    return t


def _step(values: list, tok: tuple) -> list:
    out = []
    for v in values:
        if tok[0] == "k":
            if isinstance(v, dict) and tok[1] in v:
                out.append(v[tok[1]])
        elif tok[0] == "i":
            if isinstance(v, list) and -len(v) <= tok[1] < len(v):
                out.append(v[tok[1]])
        else:
            if isinstance(v, list):
                out.extend(v)
            elif isinstance(v, dict):
                out.extend(v.values())
    return out


def get(obj: Any, path: str) -> Any:
    """値を返す。無ければ MISSING。ワイルドカードを含むパスは見つかった値の配列を返す。"""
    toks = parse(path)
    values = [obj]
    for t in toks:
        values = _step(values, t)
    if any(t[0] == "*" for t in toks):
        return values
    return values[0] if values else MISSING


def equal(a: Any, b: Any) -> bool:
    """JSON の値として等しいか（true と 1 を区別する）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return a == b
