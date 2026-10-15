"""指摘の出力（text / json / markdown）。"""

from __future__ import annotations

import json
from collections import Counter

from . import __version__
from .model import CONFLICT, DEFECT, DUPLICATE, GAP, SEVERITY_LABEL, SEVERITY_ORDER

CATEGORIES = (DEFECT, DUPLICATE, CONFLICT, GAP)


def sort_key(f):
    return (f.file, f.line or 0, -SEVERITY_ORDER[f.severity], f.code)


def summary(findings, n_cases, n_files) -> dict:
    cat = Counter(f.category for f in findings)
    sev = Counter(f.severity for f in findings)
    return {
        "files": n_files,
        "cases": n_cases,
        "by_category": {c: cat.get(c, 0) for c in CATEGORIES},
        "by_severity": {s: sev.get(s, 0) for s in ("error", "warning", "info")},
    }


def _where(f) -> str:
    rel = [f"{ln} 行目" if file == f.file else f"{file}:{ln}" for file, ln in f.related if ln]
    return f"（ほかに {'・'.join(rel)}）" if rel else ""


def _summary_line(s) -> str:
    cat = " / ".join(f"{c} {n}" for c, n in s["by_category"].items())
    sev = "・".join(f"{SEVERITY_LABEL[k]} {n}" for k, n in s["by_severity"].items())
    return f"{s['files']} ファイル・ケース {s['cases']} 件: {cat}（{sev}）"


def render_text(findings, s) -> str:
    out = []
    current = None
    for f in sorted(findings, key=sort_key):
        if f.file != current:
            current = f.file
            out.append(f"\n{f.file or '(要件一覧)'}")
        line = f"{f.line:>5}" if f.line else "    -"
        ids = [x for x in f.cases if not x.startswith("(")]  # ID のないケースは行番号で示す
        who = f" {', '.join(ids[:4])}{' ほか' if len(ids) > 4 else ''}" if ids else ""
        out.append(f"{line}  {SEVERITY_LABEL[f.severity]:<3} {f.code} {f.category}{who}  {f.message}{_where(f)}")
        if f.excerpt:
            out.append(f"{'':>7}  「{f.excerpt}」")
        if f.hint:
            out.append(f"{'':>7}  → {f.hint}")
    out.append("")
    out.append(_summary_line(s) if findings else f"{_summary_line(s)} — 指摘はありません")
    return "\n".join(out).lstrip("\n") + "\n"


def render_json(findings, s) -> str:
    return json.dumps({
        "tool": "testcase-lint",
        "version": __version__,
        "summary": s,
        "findings": [f.to_dict() for f in sorted(findings, key=sort_key)],
    }, ensure_ascii=False, indent=2) + "\n"


def _md(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_markdown(findings, s) -> str:
    out = ["# テストケースの点検結果", "", _summary_line(s), ""]
    for cat in CATEGORIES:
        fs = sorted((f for f in findings if f.category == cat), key=sort_key)
        out += [f"## {cat}（{len(fs)} 件）", ""]
        if not fs:
            out += ["なし", ""]
            continue
        out += ["| 重さ | 規則 | 場所 | ケース | 指摘 | 直し方 |", "|---|---|---|---|---|---|"]
        for f in fs:
            where = f"{f.file}:{f.line}" if f.line else (f.file or "要件一覧")
            msg = f.message + (f"<br>「{f.excerpt}」" if f.excerpt else "") + _where(f)
            out.append(f"| {SEVERITY_LABEL[f.severity]} | {f.code} {f.name} | {_md(where)} | "
                       f"{_md(', '.join(f.cases))} | {_md(msg)} | {_md(f.hint)} |")
        out.append("")
    return "\n".join(out)


RENDERERS = {"text": render_text, "json": render_json, "markdown": render_markdown, "md": render_markdown}
