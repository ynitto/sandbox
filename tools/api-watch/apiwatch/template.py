"""設定の文字列に埋める {{ ... }}。

    {{ conn.token }}                 connections.yaml の値
    {{ env.NAME }}                   環境変数
    {{ position }}                   増分取得の位置（前回の最大値・カーソル）
    {{ now }} / {{ now_iso }}        いまの時刻（UNIX 秒 / ISO 8601 UTC）
    {{ event.after.title }}          受け渡しのとき（イベントの中身）
    {{ x | default: "" }}            無ければ既定値
    {{ x | json }}                   JSON にする
    {{ x | urlencode }}              URL 用にエスケープする

文字列全体が 1 つの {{ ... }} なら、値の型（数・null など）をそのまま返す。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from . import jpath
from .util import ConfigError

_EXPR = re.compile(r"\{\{\s*(.*?)\s*\}\}")


class TemplateError(ConfigError):
    pass


def _lookup(ctx: dict, expr: str) -> Any:
    head, _, rest = expr.partition(".")
    if head not in ctx:
        return jpath.MISSING
    base = ctx[head]
    if not rest:
        return base
    return jpath.get(base, rest)


def _parse_literal(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def evaluate(expr: str, ctx: dict) -> Any:
    parts = [p.strip() for p in expr.split("|")]
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
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif name == "urlencode":
            value = urllib.parse.quote("" if value is jpath.MISSING or value is None else str(value), safe="")
        else:
            raise TemplateError(f"{{{{ {expr} }}}}: 知らないフィルタです: {name}")
    if value is jpath.MISSING:
        raise TemplateError(f"{{{{ {expr} }}}}: 値がありません（| default: ... で既定値を書けます）")
    return value


def render(value: Any, ctx: dict) -> Any:
    """文字列・配列・辞書の中の {{ }} を展開した新しい値を返す。"""
    if isinstance(value, str):
        m = _EXPR.fullmatch(value.strip()) if value.strip().startswith("{{") else None
        if m and value.strip().count("{{") == 1:
            return evaluate(m.group(1), ctx)

        def rep(mm: re.Match) -> str:
            v = evaluate(mm.group(1), ctx)
            if v is None:
                return ""
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False, sort_keys=True)
            if isinstance(v, bool):
                return "true" if v else "false"
            return str(v)

        return _EXPR.sub(rep, value)
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    return value


def references(value: Any) -> list[str]:
    """値の中で参照している式の一覧（検査用）。"""
    out: list[str] = []
    if isinstance(value, str):
        out.extend(m.group(1).split("|")[0].strip() for m in _EXPR.finditer(value))
    elif isinstance(value, list):
        for v in value:
            out.extend(references(v))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(references(v))
    return out
