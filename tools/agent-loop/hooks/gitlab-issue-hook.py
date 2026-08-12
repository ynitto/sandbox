#!/usr/bin/env python3
"""GitLab issue polling hook with event fallback (agent-loop hooks)."""
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
from _gitlab_client import GitLabError, connect_from_cwd, event_key, list_issues
from _hook_util import hook_state_dir, load_json, save_json_atomic

STATE_FILE_NAME = "events.json"

ISSUE_STATE = os.environ.get("AGENT_LOOP_ISSUE_STATE", "opened")
ISSUE_LABELS = os.environ.get("AGENT_LOOP_ISSUE_LABELS", "")
ISSUE_ASSIGNEE = os.environ.get("AGENT_LOOP_ISSUE_ASSIGNEE", "")

_LABEL_PROMPTS: dict[str, str] = {
    "priority:critical": (
        "緊急イシューが割り当てられました。最優先で gitlab-idd スキルの"
        "ワーカーロールを実行し、対応してください。\n\n{issue_json}"
    ),
    "type:bug": (
        "バグイシューがあります。gitlab-idd スキルのワーカーロールで再現手順を"
        "確認して修正してください。\n\n{issue_json}"
    ),
}
_DEFAULT_PROMPT = (
    "新しいイシューが割り当てられました。gitlab-idd スキルのワーカーロールを"
    "実行して、このイシューを実装・報告してください。\n\n{issue_json}"
)
_REPLAY_PREFIX = "（再確認）以下の open issue を点検してください。\n\n"
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


def _format_prompt(issue: dict[str, Any], *, prefix: str = "") -> str:
    issue_json = json.dumps(issue, ensure_ascii=False, indent=2)
    template = _DEFAULT_PROMPT
    for label in issue.get("labels", []):
        if label in _LABEL_PROMPTS:
            template = _LABEL_PROMPTS[label]
            break
    return prefix + template.format(issue_json=issue_json)


def _issue_candidates(
    issues: list[dict[str, Any]],
    project_path: str,
) -> list[EventCandidate]:
    out: list[EventCandidate] = []
    for issue in issues:
        iid = issue.get("iid")
        if iid is None:
            continue
        version = str(issue.get("updated_at") or "")
        key = event_key(project_path, "issue", iid)
        out.append(EventCandidate(key=key, version=version, sort_key=version, payload=issue))
    return out


def _fetch_issues(hook_config: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str] | None:
    cwd = (hook_config or {}).get("cwd")
    workspace = (hook_config or {}).get("workspace")
    try:
        conn, token = connect_from_cwd(cwd, workspace)
        issues = list_issues(
            conn,
            token,
            state=ISSUE_STATE,
            labels=ISSUE_LABELS,
            assignee=ISSUE_ASSIGNEE,
        )
        return issues, conn.project_path
    except GitLabError:
        return None


def check(hook_config: dict[str, Any] | None = None) -> str | None:
    global _runtime
    entry_id = str((hook_config or {}).get("entry_id") or "default")
    path = _state_path(entry_id)
    _runtime = {"entry_id": entry_id, "state_path": path, "pending": None, "state": None}

    fetched = _fetch_issues(hook_config)
    if fetched is None:
        return None
    issues, project_path = fetched
    candidates = _issue_candidates(issues, project_path)
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
