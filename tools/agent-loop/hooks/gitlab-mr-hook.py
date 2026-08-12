#!/usr/bin/env python3
"""GitLab merge request polling hook with event fallback (agent-loop hooks)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from _event_fallback import EventCandidate, PendingAck, apply_ack, normalize_state, select_event
from _gitlab_client import GitLabError, connect_from_cwd, event_key, list_merge_requests
from _hook_util import hook_state_dir, load_json, save_json_atomic

STATE_FILE_NAME = "events.json"

MR_STATE = os.environ.get("AGENT_LOOP_MR_STATE", "opened")
MR_ASSIGNEE = os.environ.get("AGENT_LOOP_MR_ASSIGNEE", "")
MR_SOURCE_BRANCH_PREFIX = os.environ.get("AGENT_LOOP_MR_SOURCE_BRANCH_PREFIX", "")
GL_PY = os.environ.get("AGENT_LOOP_GL_PY", "scripts/gl.py")

_PROMPT = (
    "自分にアサインされたオープン MR の未解決ディスカッションを確認してください。\n"
    "対象 MR (iid={iid}): {title}\n{web_url}\n\n"
    "手順:\n"
    "1. python {gl_py} get-mr-discussions {iid} --unresolved で未解決スレッドを取得\n"
    "2. 各スレッドの指摘内容を確認し:\n"
    "   - 質問・説明要求 → python {gl_py} add-mr-comment {iid} --body \"返答内容\" で返答し、"
    "python {gl_py} resolve-mr-discussion {iid} DISCUSSION_ID でスレッドをクローズ\n"
    "   - コード修正要求 → 修正を実装して push し、python {gl_py} add-mr-comment {iid} "
    "--body \"修正しました: ...\" で報告してスレッドをクローズ\n"
    "3. 全スレッドがクローズしたら python {gl_py} add-mr-comment {iid} "
    "--body \"全指摘に対応しました。再レビューをお願いします。\" を投稿\n"
    "未解決スレッドが無ければ「対応待ちのコメントはありません」と報告して終了。\n\n"
    "MR の詳細:\n{mr_json}"
)
_REPLAY_PREFIX = "（再確認）以下の MR の未解決スレッドを念のため確認してください。\n\n"
_FALLBACK_PREFIX = "（フォールバック）"

_runtime: dict[str, Any] = {
    "entry_id": None,
    "state_path": None,
    "pending": None,
    "state": None,
}


def _state_path(entry_id: str) -> Path:
    return hook_state_dir(entry_id) / STATE_FILE_NAME


def _load_state(path: Path) -> dict[str, Any]:
    return normalize_state(load_json(path, {}))


def _save_state(path: Path, state: dict[str, Any]) -> None:
    save_json_atomic(path, state)


def _format_prompt(mr: dict[str, Any], *, prefix: str = "") -> str:
    body = _PROMPT.format(
        iid=mr.get("iid", ""),
        title=mr.get("title", ""),
        web_url=mr.get("web_url", ""),
        gl_py=GL_PY,
        mr_json=json.dumps(mr, ensure_ascii=False, indent=2),
    )
    return prefix + body


def _mr_candidates(mrs: list[dict[str, Any]], project_path: str) -> list[EventCandidate]:
    out: list[EventCandidate] = []
    for mr in mrs:
        iid = mr.get("iid")
        if iid is None:
            continue
        version = str(mr.get("updated_at") or "")
        key = event_key(project_path, "mr", iid)
        out.append(EventCandidate(key=key, version=version, sort_key=version, payload=mr))
    return out


def _fetch_mrs(hook_config: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str] | None:
    cwd = (hook_config or {}).get("cwd")
    workspace = (hook_config or {}).get("workspace")
    try:
        conn, token = connect_from_cwd(cwd, workspace)
        mrs = list_merge_requests(
            conn,
            token,
            state=MR_STATE,
            assignee=MR_ASSIGNEE,
            source_branch_prefix=MR_SOURCE_BRANCH_PREFIX,
        )
        return mrs, conn.project_path
    except GitLabError:
        return None


def check(hook_config: dict[str, Any] | None = None) -> str | None:
    global _runtime
    entry_id = str((hook_config or {}).get("entry_id") or "default")
    path = _state_path(entry_id)
    _runtime = {"entry_id": entry_id, "state_path": path, "pending": None, "state": None}

    fetched = _fetch_mrs(hook_config)
    if fetched is None:
        return None
    mrs, project_path = fetched
    candidates = _mr_candidates(mrs, project_path)
    state = _load_state(path)

    def fmt_new(c: EventCandidate) -> str:
        return _format_prompt(c.payload)

    def fmt_replay(c: EventCandidate) -> str:
        return _format_prompt(c.payload, prefix=_REPLAY_PREFIX)

    result, pending, updated = select_event(
        candidates,
        state,
        hook_config,
        format_new=fmt_new,
        format_replay=fmt_replay,
    )

    if pending is None and not state.get("baseline_done"):
        _save_state(path, updated)
        return None

    _runtime["pending"] = pending
    _runtime["state"] = state
    if isinstance(result, str) and pending and pending.kind == "fallback":
        return _FALLBACK_PREFIX + result
    return result if isinstance(result, str) else None


def ack() -> None:
    pending: PendingAck | None = _runtime.get("pending")
    path: Path | None = _runtime.get("state_path")
    state: dict[str, Any] | None = _runtime.get("state")
    if pending is None or path is None or state is None:
        return
    _save_state(path, apply_ack(state, pending))
    _runtime["pending"] = None


if __name__ == "__main__":
    print(check({"entry_id": "manual"}))
