"""レコードの中の値を指すパスと、条件式。

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


def has_wildcard(path: str) -> bool:
    return any(t[0] == "*" for t in parse(path))


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


def delete(obj: Any, path: str) -> None:
    """パスの値を取り除く（obj を書き換える）。ワイルドカード可。"""
    toks = parse(path)
    if not toks:
        return
    parents = [obj]
    for t in toks[:-1]:
        parents = _step(parents, t)
    last = toks[-1]
    for p in parents:
        if last[0] == "k" and isinstance(p, dict):
            p.pop(last[1], None)
        elif last[0] == "i" and isinstance(p, list) and -len(p) <= last[1] < len(p):
            del p[last[1]]
        elif last[0] == "*":
            if isinstance(p, dict):
                p.clear()
            elif isinstance(p, list):
                p.clear()


def transform(obj: Any, path: str, fn) -> Any:
    """パスの値を fn(値) で置き換えた新しい値を返す（元は変えない前提で、呼び出し側がコピーを渡す）。"""
    toks = parse(path)
    if not toks:
        return fn(obj)

    def walk(node: Any, i: int) -> Any:
        if i == len(toks):
            return fn(node)
        t = toks[i]
        if t[0] == "k" and isinstance(node, dict) and t[1] in node:
            node[t[1]] = walk(node[t[1]], i + 1)
        elif t[0] == "i" and isinstance(node, list) and -len(node) <= t[1] < len(node):
            node[t[1]] = walk(node[t[1]], i + 1)
        elif t[0] == "*":
            if isinstance(node, list):
                for j in range(len(node)):
                    node[j] = walk(node[j], i + 1)
            elif isinstance(node, dict):
                for k in list(node):
                    node[k] = walk(node[k], i + 1)
        return node

    return walk(obj, 0)


# ---------------------------------------------------------------------------
# 条件式
# ---------------------------------------------------------------------------
#   {path: state, op: eq, value: opened}
#   {all: [...]} / {any: [...]} / {not: {...}}
# op を省くと、value があれば eq、無ければ exists。

OPS_WITH_VALUE = {"eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "contains", "not_contains",
                  "regex", "startswith", "endswith"}
OPS_NO_VALUE = {"exists", "missing", "null", "not_null", "empty", "not_empty", "truthy", "falsy"}


def validate_condition(cond: Any, where: str) -> None:
    if not isinstance(cond, dict):
        raise ConfigError(f"{where}: 条件は {{path, op, value}} か all/any/not の形で書いてください")
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        items = cond[key]
        if not isinstance(items, list) or not items:
            raise ConfigError(f"{where}.{key}: 条件の配列を書いてください")
        for i, c in enumerate(items):
            validate_condition(c, f"{where}.{key}[{i}]")
        return
    if "not" in cond:
        validate_condition(cond["not"], f"{where}.not")
        return
    if "path" not in cond:
        raise ConfigError(f"{where}: path がありません")
    parse(str(cond["path"]))
    op = cond.get("op") or ("eq" if "value" in cond else "exists")
    if op not in OPS_WITH_VALUE | OPS_NO_VALUE:
        raise ConfigError(f"{where}: 知らない op です: {op}")
    if op in OPS_WITH_VALUE and "value" not in cond:
        raise ConfigError(f"{where}: op {op} には value が要ります")
    if op == "regex":
        try:
            re.compile(str(cond["value"]))
        except re.error as e:
            raise ConfigError(f"{where}: 正規表現を読めません: {e}") from e
    if op in ("in", "not_in") and not isinstance(cond["value"], list):
        raise ConfigError(f"{where}: op {op} の value は配列にしてください")


def equal(a: Any, b: Any) -> bool:
    """JSON の値として等しいか（true と 1 を区別する）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return a == b


def _cmp(a: Any, b: Any, op: str) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        pass
    elif isinstance(a, str) and isinstance(b, str):
        pass
    else:
        return False
    return {"gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]


def check(cond: dict, rec: Any) -> bool:
    if "all" in cond:
        return all(check(c, rec) for c in cond["all"])
    if "any" in cond:
        return any(check(c, rec) for c in cond["any"])
    if "not" in cond:
        return not check(cond["not"], rec)
    v = get(rec, str(cond["path"]))
    op = cond.get("op") or ("eq" if "value" in cond else "exists")
    want = cond.get("value")
    if op == "exists":
        return v is not MISSING
    if op == "missing":
        return v is MISSING
    if op == "null":
        return v is None
    if op == "not_null":
        return v is not MISSING and v is not None
    if op == "empty":
        return v is MISSING or v is None or v == "" or v == [] or v == {}
    if op == "not_empty":
        return not (v is MISSING or v is None or v == "" or v == [] or v == {})
    if op == "truthy":
        return v is not MISSING and bool(v)
    if op == "falsy":
        return v is MISSING or not bool(v)
    if v is MISSING:
        return op in ("ne", "not_in", "not_contains")
    if op == "eq":
        return equal(v, want)
    if op == "ne":
        return not equal(v, want)
    if op in ("gt", "ge", "lt", "le"):
        return _cmp(v, want, op)
    if op == "in":
        return any(equal(v, w) for w in want)
    if op == "not_in":
        return not any(equal(v, w) for w in want)
    if op in ("contains", "not_contains"):
        if isinstance(v, str):
            hit = isinstance(want, str) and want in v
        elif isinstance(v, list):
            hit = any(equal(x, want) for x in v)
        elif isinstance(v, dict):
            hit = isinstance(want, str) and want in v
        else:
            hit = False
        return hit if op == "contains" else not hit
    if op == "regex":
        return isinstance(v, str) and re.search(str(want), v) is not None
    if op == "startswith":
        return isinstance(v, str) and v.startswith(str(want))
    if op == "endswith":
        return isinstance(v, str) and v.endswith(str(want))
    return False
