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
