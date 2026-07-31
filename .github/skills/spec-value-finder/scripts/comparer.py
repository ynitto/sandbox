"""手動転記結果とスキル抽出結果の突き合わせ判定。

人が手で記入した転記シート（Excel/Word/PPTX/Markdown のいずれか。項目→値の表）と、
スキルが確定した findings.json を項目名で突き合わせ、一致/不一致を判定する。

判定は機械的チェック（数値・正規化文字列の一致）まで。セマンティック等価
（"有効" と "Enabled" など）の最終判定は Claude（呼び出し側）が行う。
"""
from __future__ import annotations

import re

from extract import extract_file
from models import normalize

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_MARK = {
    "match": "[OK]", "mismatch": "[NG]",
    "missing_manual": "[!手動空]", "missing_skill": "[!スキル空]",
    "both_empty": "[--]",
}


def _num(text: str):
    m = _NUM_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def read_manual_values(path, item_header: str = "項目",
                        value_header: str = "値") -> dict[str, dict]:
    """手動転記ファイル中の表から {正規化項目名: {target, value}} を抽出する。

    どの表でも、item_header を含む行をヘッダとみなし、その下の行を項目→値として読む。
    値列が見つからない場合は項目列の右隣を値列とする。
    """
    doc = extract_file(path)
    nih, nvh = normalize(item_header), normalize(value_header)
    result: dict[str, dict] = {}

    for block in doc.blocks:
        if getattr(block, "kind", "") != "table":
            continue
        item_i = val_i = header_at = None
        for r_idx, row in enumerate(block.rows):
            cols = {normalize(c.text): i for i, c in enumerate(row)}
            if nih in cols:
                item_i = cols[nih]
                val_i = cols.get(nvh)
                header_at = r_idx
                break
        if item_i is None:
            continue
        if val_i is None:
            val_i = item_i + 1
        for row in block.rows[header_at + 1:]:
            if item_i >= len(row):
                continue
            target = row[item_i].text.strip()
            if not target:
                continue
            value = row[val_i].text.strip() if val_i < len(row) else ""
            result[normalize(target)] = {"target": target, "value": value}
    return result


def _judge(manual_value: str, skill_value: str) -> tuple[str, bool, str]:
    """(verdict, needs_review, note) を返す。"""
    has_m = bool((manual_value or "").strip())
    has_s = bool((skill_value or "").strip())
    if not has_m and not has_s:
        return "both_empty", False, ""
    if not has_m:
        return "missing_manual", True, "手動転記に値がない（転記漏れの疑い）"
    if not has_s:
        return "missing_skill", True, "スキルが値を検出できなかった（keywords 見直し or 手動値の出典確認）"
    if normalize(manual_value) == normalize(skill_value):
        return "match", False, ""
    nm, ns = _num(manual_value), _num(skill_value)
    if nm is not None and ns is not None and nm == ns:
        return "match", True, "数値は一致するが表記が異なる（単位・桁区切り等を要確認）"
    return "mismatch", True, "値が一致しない（セマンティック等価かを要判定）"


def run_comparison(manual_values: dict[str, dict], findings: list[dict],
                   mapping_items: list[dict] | None = None) -> dict:
    """手動転記結果 × findings を突き合わせた比較結果 dict を返す。"""
    findings_by = {normalize(f.get("target", "")): f for f in findings}

    order: list[str] = []
    seen: set[str] = set()
    display: dict[str, str] = {}

    def add(nt: str, name: str) -> None:
        if nt and nt not in seen:
            seen.add(nt)
            order.append(nt)
        if nt:
            display.setdefault(nt, name)

    for m in (mapping_items or []):
        add(normalize(m.get("target", "")), m.get("target", ""))
    for f in findings:
        add(normalize(f.get("target", "")), f.get("target", ""))
    for nt, mv in manual_values.items():
        add(nt, mv["target"])

    items: list[dict] = []
    counts: dict[str, int] = {}
    for nt in order:
        mv = manual_values.get(nt)
        sk = findings_by.get(nt)
        manual_value = mv["value"] if mv else ""
        skill_value = sk.get("value", "") if sk else ""
        verdict, needs_review, note = _judge(manual_value, skill_value)
        conf = normalize(sk.get("confidence", "")) if sk else ""
        if verdict == "match" and conf in ("low", "低"):
            needs_review = True
            note = note or "一致するがスキルの確信度が低い（出典を要確認）"
        counts[verdict] = counts.get(verdict, 0) + 1
        items.append({
            "target": display.get(nt, nt),
            "manual_value": manual_value,
            "skill_value": skill_value,
            "verdict": verdict,
            "needs_review": needs_review,
            "note": note,
            "skill_source": sk.get("source", "") if sk else "",
            "skill_confidence": sk.get("confidence", "") if sk else "",
        })

    summary = {
        "total": len(items),
        "match": counts.get("match", 0),
        "mismatch": counts.get("mismatch", 0),
        "missing_manual": counts.get("missing_manual", 0),
        "missing_skill": counts.get("missing_skill", 0),
        "both_empty": counts.get("both_empty", 0),
        "needs_review": sum(1 for it in items if it["needs_review"]),
    }
    return {"summary": summary, "items": items}


def format_comparison(result: dict) -> str:
    s = result["summary"]
    lines = [
        f"判定結果: 全 {s['total']} 件 — "
        f"一致 {s['match']} / 不一致 {s['mismatch']} / "
        f"手動空 {s['missing_manual']} / スキル空 {s['missing_skill']}"
        f"（要確認 {s['needs_review']} 件）",
        "",
    ]
    for it in result["items"]:
        mark = _MARK.get(it["verdict"], "[?]")
        review = "  ※要確認" if it["needs_review"] else ""
        lines.append(f"{mark} {it['target']}{review}")
        lines.append(f"    手動転記 : {it['manual_value'] or '(空)'}")
        conf = f"  (確信度: {it['skill_confidence']})" if it["skill_confidence"] else ""
        lines.append(f"    スキル抽出: {it['skill_value'] or '(空)'}{conf}")
        if it["skill_source"]:
            lines.append(f"    出典     : {it['skill_source']}")
        if it["note"]:
            lines.append(f"    note     : {it['note']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
