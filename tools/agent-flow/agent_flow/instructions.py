from __future__ import annotations
# instructions.py — グローバル指示（agent-instructions 契約）の読取・決定的レンダリング。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典: schemas/agent-instructions.schema.json。実体は $AGENT_INSTRUCTIONS_DIR
# （既定 ~/.agent/instructions/）の instructions.json（管理面＝agent-dashboard が原子書換）。
#
# agent-flow は run 作成時（orchestrate の ensure_run 後）に投入ノードの instructions.json を
# 描画して runs/<run-id>/meta.json に additive キー instructions:{revision,text} として固定する。
# GitBus 同期で全ワーカーへ届く（run 単位の一貫性基準＝run ブリーフと同じ哲学。ワーカーは
# ローカルの instructions.json を読まない）。ワーカーはこの描画済みブロックを実行エージェントの
# プロンプト先頭へ前置する。planner / evaluator 等のメタ LLM 呼び出しへは注入しない。
#
# レンダラは dashboard（JS）・kiro-loop / agent-loop（Python）と同一出力になるよう決定的に保つ。
# stdlib のみ（json / os / re）。フェイルセーフ: 不在 / 破損 / enabled=false は空文字＝注入しない。

AGENT_INSTRUCTIONS_MARKER = "<!-- agent-instructions"
_AGENT_INSTRUCTIONS_HEADING = "## 共通指示（agent-dashboard 管理・全ノード共通）"
_AGENT_INSTRUCTIONS_DEFAULT_MAX = 2000
_AGENT_INSTRUCTIONS_HARD_MAX = 8000

# ワーカーが注入した run スナップショットの revision（status ハートビートへ写す。未注入は None）。
_INSTRUCTIONS_REV_APPLIED: "int | None" = None


def _instructions_dir() -> str:
    return os.path.abspath(os.path.expanduser(
        os.environ.get("AGENT_INSTRUCTIONS_DIR", os.path.join("~", ".agent", "instructions"))))


def load_instructions(directory: "str | None" = None) -> "dict | None":
    """instructions.json を読む。無ければ / 壊れていれば None（＝指示なし）。"""
    path = os.path.join(directory or _instructions_dir(), "instructions.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clamp_max_chars(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return _AGENT_INSTRUCTIONS_DEFAULT_MAX
    if n <= 0:
        return _AGENT_INSTRUCTIONS_DEFAULT_MAX
    return min(n, _AGENT_INSTRUCTIONS_HARD_MAX)


def render_instructions_block(data: "dict | None", max_chars: "int | None" = None) -> str:
    """契約（agent-instructions.schema.json）→ 決定的テキストブロック。
    dashboard（JS renderBlock）・kiro-loop / agent-loop と同一出力。
    enabled=false / 中身なしのときは空文字（＝注入しない）。"""
    if not isinstance(data, dict) or data.get("enabled") is False:
        return ""
    text = str(data.get("text") or "").strip()
    skills = []
    for s in (data.get("skills") or []):
        if isinstance(s, str):
            name, note = s.strip(), ""
        elif isinstance(s, dict):
            name, note = str(s.get("name") or "").strip(), str(s.get("note") or "").strip()
        else:
            continue
        if name:
            skills.append((name, note))
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    allow = [str(t).strip() for t in (tools.get("allow") or []) if str(t).strip()]
    deny_note = str(tools.get("deny_note") or "").strip()
    if not text and not skills and not allow and not deny_note:
        return ""

    try:
        rev = int(data.get("revision") or 0)
    except (TypeError, ValueError):
        rev = 0
    marker = f"{AGENT_INSTRUCTIONS_MARKER} rev:{rev} -->"
    lines = [marker, _AGENT_INSTRUCTIONS_HEADING]
    if text:
        lines.append(text)
    if skills:
        lines.append("")
        lines.append("推奨スキル（ローカルに存在する場合のみ適用）:")
        for name, note in skills:
            lines.append(f"- {name}" + (f" — {note}" if note else ""))
    if allow:
        lines.append("ツール（許可）: " + ", ".join(allow))
    if deny_note:
        lines.append(f"ツール方針: {deny_note}")

    block = "\n".join(lines)
    cap = _clamp_max_chars(max_chars if max_chars is not None else data.get("max_chars"))
    if len(block) > cap:
        # マーカー行は必ず残す（病的に小さい cap ではマーカーだけ返す）。
        block = marker if cap <= len(marker) else block[:cap - 1].rstrip() + "…"
    return block


def local_instructions_snapshot() -> "dict | None":
    """このノードの instructions.json を描画し {revision, text} を返す。無効 / 空なら None。
    agent-flow が run 作成時に meta.json へ固定するスナップショット。"""
    data = load_instructions()
    block = render_instructions_block(data)
    if not block:
        return None
    try:
        rev = int((data or {}).get("revision") or 0)
    except (TypeError, ValueError):
        rev = 0
    return {"revision": rev, "text": block}


def prepend_instructions(target: str, block: str) -> str:
    """block を target 先頭へ前置する。既にマーカーを含むなら二重注入しない。"""
    t = str(target or "")
    if not block:
        return t
    if AGENT_INSTRUCTIONS_MARKER in t:
        return t
    return f"{block}\n\n{t}" if t else block


def _note_instructions_applied(rev: "int | None") -> None:
    """ワーカーが注入したスナップショット revision を記録（status ハートビートへ写す）。"""
    global _INSTRUCTIONS_REV_APPLIED
    if rev is not None:
        _INSTRUCTIONS_REV_APPLIED = int(rev)
