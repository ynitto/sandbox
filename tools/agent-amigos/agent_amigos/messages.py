"""エージェント間メッセージング — チャンネル / inbox / 型付きメッセージ（設計書 §7）。

- 送信者は自分名義のファイルだけを書く（inbox は `<ulid>-<from>.json`、
  all チャンネルは `channels/all/<who>/<ulid>.json`）→ git バスでも衝突しない。
- 既読はバスに書かない。各 amigo がローカルカーソル（status 内の last_seen）を持つ。
"""
from __future__ import annotations

import os

from .bus import MissionPaths
from .util import now_iso, read_json, ulid

MESSAGE_TYPES = ("question", "answer", "request", "review", "status",
                 "decision-request", "info", "wrap-up", "approve", "feedback")


def valid_target(to: str, roles: "dict[str, dict]") -> bool:
    return to in ("all", "owner") or to in roles


def build_message(from_who: str, to: str, mtype: str, subject: str = "", body: str = "",
                  reply_to: "str | None" = None, refs: "list | None" = None) -> "tuple[str, dict]":
    if mtype not in MESSAGE_TYPES:
        raise ValueError(f"不正なメッセージ型です: {mtype!r}（許可: {', '.join(MESSAGE_TYPES)}）")
    mid = ulid()
    return mid, {"id": mid, "from": from_who, "to": to, "type": mtype,
                 "subject": str(subject or ""), "body": str(body or ""),
                 "reply_to": reply_to, "refs": list(refs or []), "created_at": now_iso()}


def message_path(mp: MissionPaths, msg: dict) -> str:
    """メッセージの置き場所（送信者名義でファイル名が衝突しない）。"""
    if msg["to"] == "all":
        return os.path.join(mp.channel_all_dir(msg["from"]), f"{msg['id']}.json")
    return os.path.join(mp.inbox_dir(msg["to"]), f"{msg['id']}-{msg['from']}.json")


def _iter_dir(d: str):
    try:
        names = sorted(os.listdir(d))
    except FileNotFoundError:
        return
    for name in names:
        if not name.endswith(".json") or ".tmp." in name:
            continue
        data = read_json(os.path.join(d, name))
        if isinstance(data, dict) and data.get("id"):
            yield data


def read_channel_all(mp: MissionPaths) -> list:
    """all チャンネル全件（id = ulid で時系列順にマージ）。"""
    out = []
    base = mp.channel_all_dir()
    try:
        whos = sorted(os.listdir(base))
    except FileNotFoundError:
        return out
    for who in whos:
        out.extend(_iter_dir(os.path.join(base, who)))
    return sorted(out, key=lambda m: m["id"])


def read_inbox(mp: MissionPaths, role_id: str) -> list:
    return sorted(_iter_dir(mp.inbox_dir(role_id)), key=lambda m: m["id"])


def new_messages(mp: MissionPaths, role_id: str, cursor: str) -> "tuple[list, str]":
    """自ロール宛 inbox + all チャンネルのカーソル以降。(新着列, 新カーソル) を返す。"""
    merged = sorted(read_inbox(mp, role_id) + read_channel_all(mp), key=lambda m: m["id"])
    fresh = [m for m in merged if m["id"] > (cursor or "")]
    new_cursor = merged[-1]["id"] if merged else (cursor or "")
    return fresh, new_cursor


def answered_ids(mp: MissionPaths, roles: "dict[str, dict]") -> set:
    """reply_to を持つ answer が指す質問 id の集合（全 inbox + all チャンネルを走査）。"""
    out = set()
    for role_id in list(roles) + ["owner"]:
        for m in read_inbox(mp, role_id):
            if m.get("type") == "answer" and m.get("reply_to"):
                out.add(m["reply_to"])
    for m in read_channel_all(mp):
        if m.get("type") == "answer" and m.get("reply_to"):
            out.add(m["reply_to"])
    return out


def unanswered_questions(mp: MissionPaths, roles: "dict[str, dict]") -> list:
    """未回答の question 一覧（owner 宛は除く — owner への滞留は人の判断待ちで、
    静穏化（quiescence）の妨げにしない）。"""
    done = answered_ids(mp, roles)
    out = []
    for role_id in roles:
        for m in read_inbox(mp, role_id):
            if m.get("type") == "question" and m["id"] not in done:
                out.append(m)
    return sorted(out, key=lambda m: m["id"])
