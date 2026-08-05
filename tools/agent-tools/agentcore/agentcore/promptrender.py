"""agentcore.promptrender — LLM プロンプトへ注入する構造化データの決定的な圧縮描画。

設計: docs/plans/2026-08-05-json-prompt-compression-study.md

スキーマ（各ツールの契約データモデル）は一切変えない。保存・転送（git 上のファイル・
schemas/ 準拠の契約）は今日どおり json.dumps のままでよく、ここが扱うのは
**LLM への入力としてプロンプト文字列へ埋め込む瞬間**だけ。

- dumps_prompt(value)  … 案 K-1。indent 無し・区切り最小の compact JSON。
  内容等価・完全可逆（json.loads で元に戻る）。既定 on で使ってよい。
- render_table(value)  … 案 K-2。均質な dict 配列（全要素が同じキー集合・スカラー値のみ）を
  見つけたときだけ「ヘッダ 1 回 + 行」の表へ畳む。均質でない配列・空配列・入れ子の混在は
  dumps_prompt と同じ compact 表現へ決定的にフォールバックする。出力は LLM 可読なテキストで
  あり、エンジンが再パースする契約ではない（注入側専用・オプトイン）。
"""
from __future__ import annotations

import json

__all__ = ["dumps_prompt", "render_table"]


def dumps_prompt(value) -> str:
    """LLM プロンプトへ注入する JSON の正規描画（案 K-1）。

    ensure_ascii=False（日本語をエスケープしない）・区切りを最小にするだけで、
    キー・値・型は 1 ビットも変わらない。json.loads で元の値に戻る。
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _is_scalar(x) -> bool:
    return x is None or isinstance(x, (str, int, float, bool))


def _fmt_scalar(x) -> str:
    """表のセル 1 個分の描画。カンマ・改行・二重引用符を含む値は JSON 文字列として
    クォートする（行の途中でカンマ分割が壊れないようにするための可逆な最小エスケープ）。"""
    if x is None:
        return ""
    if isinstance(x, bool):
        return "true" if x else "false"
    s = str(x)
    if any(c in s for c in ',\n"'):
        return json.dumps(s, ensure_ascii=False)
    return s


def _homogeneous_record_keys(items: list) -> "list[str] | None":
    """items が「全要素が同じキー集合を持ち、値がすべてスカラーの dict」の配列なら
    キー列（出現順）を返す。1 件でも外れていれば None（呼び出し側は compact JSON へ
    決定的にフォールバックする）。"""
    if not items or not all(isinstance(e, dict) for e in items):
        return None
    keys = list(items[0].keys())
    for e in items:
        if list(e.keys()) != keys or not all(_is_scalar(v) for v in e.values()):
            return None
    return keys


def render_table(value, *, name: str = "value") -> str:
    """均質な dict 配列を表形式へ畳んで描画する（案 K-2）。

    トップレベルが dict ならキーごとに、list ならその 1 ブロックとして走査する。
    均質な dict 配列が見つかった箇所だけ `key[件数]{k1,k2,...}:` ヘッダ＋カンマ区切り行に、
    スカラーだけの配列は `key[件数]: v1,v2,...` の 1 行に畳む。それ以外（空配列・非均質配列・
    dict/list が混じる配列・空 dict）は dumps_prompt と同じ compact 表現へ決定的に落とす。
    """
    lines: list[str] = []

    def walk(key: str, val, indent: int) -> None:
        pad = "  " * indent
        if isinstance(val, list):
            if val and all(isinstance(e, dict) for e in val):
                keys = _homogeneous_record_keys(val)
                if keys is not None:
                    lines.append(f"{pad}{key}[{len(val)}]{{{','.join(keys)}}}:")
                    for row in val:
                        lines.append(pad + "  " + ",".join(_fmt_scalar(row[k]) for k in keys))
                    return
            if val and all(_is_scalar(e) for e in val):
                lines.append(f"{pad}{key}[{len(val)}]: " + ",".join(_fmt_scalar(e) for e in val))
                return
            # 空配列・非均質配列・入れ子（dict/list 混在）は compact JSON へ決定的フォールバック。
            lines.append(f"{pad}{key}: {dumps_prompt(val)}")
            return
        if isinstance(val, dict):
            if val:
                lines.append(f"{pad}{key}:")
                for k2, v2 in val.items():
                    walk(k2, v2, indent + 1)
            else:
                lines.append(f"{pad}{key}: {dumps_prompt(val)}")
            return
        lines.append(f"{pad}{key}: {_fmt_scalar(val)}")

    if isinstance(value, dict):
        for k, v in value.items():
            walk(k, v, 0)
    elif isinstance(value, list):
        walk(name, value, 0)
    else:
        return _fmt_scalar(value)
    return "\n".join(lines)
