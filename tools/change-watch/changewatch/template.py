"""設定の文字列に埋める {{ ... }}。

    {{ conn.token }}                 connections.yaml の値
    {{ env.NAME }}                   環境変数
    {{ position }}                   増分取得の位置（前回の最大値・カーソル）
    {{ now }} / {{ now_iso }}        いまの時刻（UNIX 秒 / ISO 8601 UTC）
    {{ key }}                        取得元の item（1 件の取り直し）のとき、レコードの key
    {{ x | default: "" }}            無ければ既定値
    {{ x | json }}                   JSON にする（{{ x | json: 2 }} なら 2 字下げで整形する）
    {{ x | urlencode }}              URL 用にエスケープする
    {{ x | プラグインのフィルタ: 引数 }}  --plugin で読み込んだプラグインが足したフィルタ（changewatch.hooks）

文字列全体が 1 つの {{ ... }} なら、値の型（数・null など）をそのまま返す。
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from . import jpath
from .util import ConfigError


class TemplateError(ConfigError):
    pass


def _spans(text: str):
    """{{ ... }} の位置を (開始, 終了, 式) で返す。引用符（" または '）の中の `}}` は閉じと見なさない。"""
    pos = 0
    while True:
        start = text.find("{{", pos)
        if start < 0:
            return
        i = start + 2
        quote = ""
        end = -1
        while i < len(text):
            c = text[i]
            if quote:
                if c == "\\" and quote == '"':
                    i += 1
                elif c == quote:
                    quote = ""
            elif c in "\"'":
                quote = c
            elif text.startswith("}}", i):
                end = i + 2
                break
            i += 1
        if end < 0:
            return
        yield start, end, text[start + 2:end - 2].strip()
        pos = end


def _lookup(ctx: dict, expr: str) -> Any:
    head, _, rest = expr.partition(".")
    if head not in ctx:
        return jpath.MISSING
    base = ctx[head]
    if not rest:
        return base
    return jpath.get(base, rest)


def _parse_literal(text: str) -> Any:
    """フィルタの引数。JSON として読み、読めなければ文字列。'...' の単引用符は外す（| default: '' と書けるように）。"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    try:
        return json.loads(text)
    except ValueError:
        return text


def _split_pipes(expr: str) -> list[str]:
    """`|` で区切る。引用符（" または '）の中の `|` は区切りにしない（| default: "a|b" と書けるように）。"""
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(expr):
        c = expr[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(expr):
                i += 1
                buf.append(expr[i])
            elif c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c == "|":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def evaluate(expr: str, ctx: dict) -> Any:
    parts = _split_pipes(expr)
    value = _lookup(ctx, parts[0])
    for f in parts[1:]:
        name, _, arg = f.partition(":")
        name = name.strip()
        if name == "default":
            if value is jpath.MISSING or value is None:
                value = _parse_literal(arg)
        elif name == "json":
            if value is jpath.MISSING:
                raise TemplateError(f"{{{{ {expr} }}}}: 値がありません")
            indent = _parse_literal(arg) if arg.strip() else None
            value = json.dumps(value, ensure_ascii=False, sort_keys=True,
                               indent=indent if isinstance(indent, int) and not isinstance(indent, bool) else None)
        elif name == "urlencode":
            value = urllib.parse.quote("" if value is jpath.MISSING or value is None else str(value), safe="")
        else:
            from . import hooks
            fn = hooks.get_filter(name)
            if fn is None:
                raise TemplateError(f"{{{{ {expr} }}}}: 知らないフィルタです: {name}"
                                    "（プラグインのフィルタなら --plugin を付けて実行してください）")
            value = fn(None if value is jpath.MISSING else value, _parse_literal(arg) if arg.strip() else None)
    if value is jpath.MISSING:
        raise TemplateError(f"{{{{ {expr} }}}}: 値がありません（| default: ... で既定値を書けます）")
    return value


def render(value: Any, ctx: dict) -> Any:
    """文字列・配列・辞書の中の {{ }} を展開した新しい値を返す。"""
    if isinstance(value, str):
        spans = list(_spans(value))
        if len(spans) == 1:
            st, en, expr = spans[0]
            if value[:st].strip() == "" and value[en:].strip() == "":
                return evaluate(expr, ctx)
        out: list[str] = []
        last = 0
        for st, en, expr in spans:
            out.append(value[last:st])
            v = evaluate(expr, ctx)
            if v is None:
                out.append("")
            elif isinstance(v, (dict, list)):
                out.append(json.dumps(v, ensure_ascii=False, sort_keys=True))
            elif isinstance(v, bool):
                out.append("true" if v else "false")
            else:
                out.append(str(v))
            last = en
        out.append(value[last:])
        return "".join(out)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value


def references(value: Any) -> list[str]:
    """値の中で参照している式の一覧（検査用）。"""
    out: list[str] = []
    if isinstance(value, str):
        out.extend(_split_pipes(e)[0] for _, _, e in _spans(value))
    elif isinstance(value, list):
        for v in value:
            out.extend(references(v))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(references(v))
    return out
