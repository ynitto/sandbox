from __future__ import annotations
# instructions.py — グローバル指示（agent-instructions 契約）の読取・決定的レンダリング。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典: schemas/agent-instructions.schema.json。実体は $AGENT_INSTRUCTIONS_DIR
# （既定 ~/.agent/instructions/）の instructions.json（管理面＝agent-dashboard が原子書換）。
# agent-loop は長寿命の kiro-cli ペインへ、revision 差分があるときだけ送信プロンプト先頭へ前置する。
# レンダラは dashboard（JS）・agent-flow / kiro-loop（Python）と同一出力になるよう決定的に保つ。
# 由来: tools/kiro-loop の同名実装をクローンし改称（agent-loop は kiro-loop の後継クローン）。

AGENT_INSTRUCTIONS_MARKER = "<!-- agent-instructions"
_AGENT_INSTRUCTIONS_HEADING = "## 共通指示（agent-dashboard 管理・全ノード共通）"
_AGENT_INSTRUCTIONS_DEFAULT_MAX = 2000
_AGENT_INSTRUCTIONS_HARD_MAX = 8000
_INSTRUCTIONS_REV_APPLIED: "int | None" = None


def _instructions_dir() -> str:
    return str(agent_home_subdir("AGENT_INSTRUCTIONS_DIR", "instructions").absolute())


def _load_instructions() -> "dict | None":
    """instructions.json を読む。無ければ / 壊れていれば None（＝指示なし）。"""
    path = os.path.join(_instructions_dir(), "instructions.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _instructions_clamp_max(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return _AGENT_INSTRUCTIONS_DEFAULT_MAX
    return _AGENT_INSTRUCTIONS_DEFAULT_MAX if n <= 0 else min(n, _AGENT_INSTRUCTIONS_HARD_MAX)


def render_instructions_block(data: "dict | None", max_chars: "int | None" = None) -> str:
    """契約 → 決定的テキストブロック。dashboard / agent-flow / kiro-loop と同一出力。
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
    cap = _instructions_clamp_max(max_chars if max_chars is not None else data.get("max_chars"))
    if len(block) > cap:
        block = marker if cap <= len(marker) else block[:cap - 1].rstrip() + "…"
    return block


def _instructions_revision(data: "dict | None") -> int:
    try:
        return int((data or {}).get("revision") or 0)
    except (TypeError, ValueError):
        return 0


def prepend_instructions(target: str, block: str) -> str:
    """block を target 先頭へ前置。既にマーカーを含むなら二重注入しない。"""
    t = str(target or "")
    if not block:
        return t
    if AGENT_INSTRUCTIONS_MARKER in t:
        return t
    return f"{block}\n\n{t}" if t else block
