from __future__ import annotations
# context.py — 案 H: プロジェクト文脈スナップショット（安定プレフィックス化）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
#
# agent-project が --context-file で渡す charter/rules.md/リポジトリ理解のテキスト
# （agent_project.request.project_context_block が組み立てる。stable_prefix 有効時のみ）を
# run 作成時に meta.json へ固定し、GitBus 同期で全ワーカーへ届ける
# （instructions.py の agent-instructions スナップショットと同型の仕組み。
#  design: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §3）。
#
# instructions と違う点: **planner / evaluator にも注入する**。charter/rules/repo_map は
# 分解・再計画の質に直接効くため、request から外した分をここで補わないと情報が失われる。

AGENT_CONTEXT_MARKER = "<!-- agent-project-context"
CONTEXT_READ_REPORT = "<!-- context-read-report "


def render_context_block(text: str) -> str:
    """生のプロジェクト文脈テキスト（agent-project の project_context_block の出力）を
    マーカー付きブロックへ描画する。空/空白のみなら空文字（＝注入しない）。"""
    t = str(text or "").strip()
    if not t:
        return ""
    marker = f"{AGENT_CONTEXT_MARKER} -->"
    return f"{marker}\nプロジェクト文脈（charter・ルール・リポジトリ理解。常に踏まえること）:\n{t}"


def local_context_snapshot(path: "str | None") -> "dict | None":
    """`--context-file` の内容を読み {text} を返す。無い/読めない/空なら None。
    agent-flow が run 作成時に meta.json へ固定するスナップショットの元。"""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    block = render_context_block(raw)
    return {"text": block} if block else None


def run_context_text(bus: "Bus") -> str:
    """この run の meta.json に固定済みのプロジェクト文脈テキスト（無ければ空文字）。
    orchestrate（planner・evaluator）と work（worker）が読み出す唯一の口。"""
    meta = read_json(bus.meta_path) or {}
    c = meta.get("context")
    return str(c.get("text", "")) if isinstance(c, dict) else ""


def normalize_read_allocation(raw) -> "list[dict]":
    """planner/task spec の読込割付を、worker に渡せる最小形へ丸める。"""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("path") or "").strip():
            continue
        row = {"path": str(item["path"]).strip(),
               "reason": str(item.get("reason") or "").strip()}
        if str(item.get("range") or "").strip():
            row["range"] = str(item["range"]).strip()
        out.append(row)
    return out[:32]


def render_read_allocation(raw) -> str:
    rows = normalize_read_allocation(raw)
    if not rows:
        return ""
    lines = []
    for row in rows:
        where = row["path"] + (f" ({row['range']})" if row.get("range") else "")
        lines.append(f"- {where}: {row['reason'] or 'タスクに必要な文脈'}")
    return ("【読込割付】まず以下から読み、割付外の探索は不足時だけ追加してください。\n"
            + "\n".join(lines)
            + "\n成果の末尾に実際に読んだ path を、割付内 used と割付外 extra に分けて "
              f'{CONTEXT_READ_REPORT}{{"used":[],"extra":[]}} --> の形で報告してください。')


def extract_read_report(text: str, raw) -> "tuple[str, dict | None]":
    """末尾の自己申告を除去し、割付外読込の有無を機械可読化する。"""
    rows = normalize_read_allocation(raw)
    if not rows:
        return text, None
    assigned = {row["path"] for row in rows}
    marker = re.search(r"\s*<!-- context-read-report (\{.*?\}) -->\s*$", str(text), re.S)
    report = {"assigned": len(assigned), "reported": False}
    if not marker:
        return text, report
    try:
        data = json.loads(marker.group(1))
    except (TypeError, ValueError):
        return text, report
    used = list(dict.fromkeys(str(x) for x in data.get("used", []) if str(x).strip()))
    extra = list(dict.fromkeys([*(str(x) for x in data.get("extra", []) if str(x).strip()),
                               *(x for x in used if x not in assigned)]))
    report.update({"reported": True, "used": used, "extra": extra,
                   "outside_reads": len(extra), "hit": not extra})
    return str(text)[:marker.start()].rstrip(), report
