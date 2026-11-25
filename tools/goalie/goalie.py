#!/usr/bin/env python3
"""goalie は目標管理（上層）と依頼の受け箱 runner（下層）をまとめたツール。

1 つの目標 = 1 つのフォルダ。tick と act が目標を管理し、job サブコマンドで runner に依頼を渡す。
エージェントは outbox に提案を書く。利用者との対話チャネルは外部で設定する。

    goal.md          目標と完了の条件（人が書く）
    policy.json      やってよいこと（作用ごとに auto / ask / deny）・登録したコマンド・起きる回数・完了の検査
    state.json       いまの状態（このスクリプトだけが書く）
    inbox/           新しい出来事（event と act が置く）
    outbox/          エージェントの提案（1 件 1 ファイルの JSON）
    notes/           エージェントの自分へのメモ（日ごと）
    journal.jsonl    やったこと・捨てたことの記録（追記のみ）
    .goalie/            作業用（依頼文・送るコメント・取りかかり中の出来事・ロック）

コマンド:
    init    フォルダを作る
    event   出来事を inbox に置く（ハブの issue の変化 JSON、または文）。承認・却下・質問への返事もここで受ける
    tick    起こすかを決め、起こすなら依頼文を .goalie/prompt.md に書いて理由を 1 行出す（起こさないなら何も出さない）
    act     outbox の提案を境界と照らして片付け、承認されたものを実行し、ハブに送るコメントがあればそのファイルのパスを出す
    harvest 許可されたソースから候補を集めて .goalie/discovery/ に保存する
    proposals Discovery の提案を見て採否を記録する（list / show / approve / reject）
    status  いまの状態を出す
    job     runner のコマンドを実行する（job --help で一覧を表示）

終了コード: 0 = できた / 1 = 収集・キュー操作の失敗 / 2 = 使い方の誤り /
3 = ほかの処理が state.json を使っていて待ちきれなかった（やり直してよい）。
標準ライブラリだけで動く（Windows の Python 3.9 以上）。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import hashlib
import uuid
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import discovery
import routines
from discovery import WORK, work_path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BUSY = 3

JOB_BOX = ".copilot-jobs"          # runner の受け箱（作業フォルダ直下）
JOB_LABEL = "goalie"               # 受け箱に置く依頼の名前（job run --name）
INFLIGHT_STALE_SEC = 3 * 3600      # 取りかかり中のまま、これより古ければ捨ててやり直す
MAX_RETRIES = 2                    # 提案が 1 件も無かった回を、出来事を戻してやり直す回数
NOTE_LINES = 20                    # 依頼文に載せるメモの行数
QUESTION_KEEP = 5                  # 依頼文に載せる、最近の質問の数
APPROVAL_KEEP = 20                 # 済んだ承認の依頼を state に残す数
LOCK_WAIT_SEC = 30
LOCK_STALE_SEC = 120
OUTPUT_TAIL = 1000                 # 実行したコマンドの出力を残す長さ
COMMENT_TAIL = 300                 # そのうちハブに書く長さ
RUNNING_STALE_SEC = 6 * 3600       # 実行中のまま、これより古ければ止まったとみなす

KNOWN_ACTIONS = {
    # 作用の名前 → 必須の項目。ここに無い作用は境界に書いても通さない。
    "hub.comment": ("body",),
    "run": ("name",),              # policy.json の commands に登録したコマンドを動かす
}
LEVELS = ("auto", "ask", "deny")
PROACTIVE_KINDS = ("note", "ask", "wait")

DEFAULT_POLICY = {
    "actions": {"hub.comment": "auto"},
    "commands": {},
    "approvers": [],
    "approval_expire_days": 7,
    "quiet_hours": "",
    "wake_per_day": 24,
    "done_check": [],
    "proactive_per_day": 0,
    "proactive_idle_min": 120,
    "tools": {},
    "routines": {},
}

GOAL_TEMPLATE = """# {name}

何を、いつまでに終わらせたいかを書く。

## 完了の条件

- 終わったと言える状態を、確かめられる形で書く

## 前提・使ってよいもの

- 読んでよいフォルダや資料
"""

NAME_RE = re.compile(r"^[^\s【】]{1,40}$")
DECISION_RE = re.compile(r"^(承認|却下)[ \t　]*(a\d+)\b[ \t　]*(.*)$", re.S | re.I)
ANSWER_RE = re.compile(r"^(q\d+)[ \t　:：]*(.*)$", re.S | re.I)
QUIET_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


class Busy(Exception):
    pass


# ---------------------------------------------------------------- ファイル


def now_ts() -> float:
    return time.time()


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def today(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def stamp(ts: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_journal(d: Path, entry: dict, ts: float) -> None:
    entry = {"at": iso(ts), **entry}
    with (d / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_state(d: Path) -> dict:
    work_path(d)
    state = read_json(d / "state.json", None)
    if state is None:
        raise SystemExit(f"目標のフォルダではありません（state.json が無い）: {d}")
    state.setdefault("questions", [])
    state.setdefault("approvals", [])
    state.setdefault("held", [])
    return state


def save_state(d: Path, state: dict) -> None:
    write_json(d / "state.json", state)


def load_policy(d: Path) -> dict:
    policy = read_json(d / "policy.json", {})
    merged = dict(DEFAULT_POLICY)
    merged.update(policy if isinstance(policy, dict) else {})
    return merged


def sorted_files(folder: Path, suffix: str) -> "list[Path]":
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name.endswith(suffix))


@contextlib.contextmanager
def locked(d: Path, wait: "float | None" = None):
    """state.json を読み書きする間だけ持つロック。返事の受け取りと片付けが重なっても壊さない。"""
    deadline = time.monotonic() + (LOCK_WAIT_SEC if wait is None else wait)
    folder = work_path(d)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "lock"
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > LOCK_STALE_SEC:
                    path.unlink()            # 落ちた処理が残したロック
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise Busy()
            time.sleep(0.2)
        else:
            os.close(fd)
            break
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def put_event(d: Path, state: dict, event: dict, ts: float) -> Path:
    state["seq"] = int(state.get("seq") or 0) + 1
    event = {**event, "at": iso(ts)}
    path = d / "inbox" / f"{stamp(ts)}-{state['seq']:05d}.json"
    write_json(path, event)
    return path


def routine_api(d: Path) -> dict:
    """routines.py が台帳の外（出来事・質問・承認の依頼）を触るときの口。"""
    def event(state: dict, text: str, ts: float, base: "dict | None" = None) -> None:
        put_event(d, state, {**(base or {"from": "goalie"}), "text": text}, ts)

    def question(state: dict, text: str, options: list, comments: list, ts: float) -> str:
        return add_question(state, text, options, comments, ts)

    def approval(state: dict, action: str, proposal: dict, summary: str, comments: list, ts: float) -> str:
        entry = {"id": next_id(state, "a"), "action": action, "proposal": proposal, "summary": summary.splitlines()[0][:200],
                 "created": iso(ts), "created_ts": ts, "status": "pending"}
        state["approvals"].append(entry)
        comments.append(f"承認待ち {entry['id']}: {summary}\n"
                        f"（「【{state['name']}】承認 {entry['id']}」か「【{state['name']}】却下 {entry['id']} 理由」で返信してください）")
        return entry["id"]

    return {"event": event, "question": question, "approval": approval}


def add_question(state: dict, question: str, options: list, comments: list, ts: float) -> str:
    qid = next_id(state, "q")
    lines = [f"質問 {qid}: " + question]
    lines.extend(f"- {o}" for o in options)
    example = options[0] if options else "…"
    lines.append(f"（「【{state['name']}】{qid} {example}」のように返信してください）")
    comments.append("\n".join(lines))
    qs = list(state.get("questions") or [])
    qs.append({"id": qid, "at": iso(ts), "question": question, "options": options})
    state["questions"] = qs[-QUESTION_KEEP * 2:]
    return qid


def next_id(state: dict, prefix: str) -> str:
    key = f"{prefix}seq"
    state[key] = int(state.get(key) or 0) + 1
    return f"{prefix}{state[key]}"


# ---------------------------------------------------------------- init


def cmd_init(a) -> int:
    d = Path(a.dir)
    work_path(d)
    name = a.name or d.name
    if not NAME_RE.match(name):
        print(f"名前に空白と【】は使えません（40 文字まで）: {name}", file=sys.stderr)
        return EXIT_USAGE
    if (d / "state.json").exists():
        print(f"既に目標のフォルダです: {d}", file=sys.stderr)
        return EXIT_USAGE
    init_goal(d, name, now_ts())
    print(f"作りました: {d}")
    print("goal.md に目標と完了の条件を書いてから、このフォルダを Copilot アプリのプロジェクトとして開いてください。")
    return EXIT_OK


def init_goal(d: Path, name: str, ts: float, **extra) -> None:
    work = work_path(d)
    for folder in (d / "inbox", d / "outbox", d / "notes", work):
        folder.mkdir(parents=True, exist_ok=True)
    if not (d / "goal.md").exists():
        (d / "goal.md").write_text(GOAL_TEMPLATE.format(name=name), encoding="utf-8")
    if not (d / "policy.json").exists():
        write_json(d / "policy.json", DEFAULT_POLICY)
    save_state(d, {
        "name": name,
        "status": "active",
        "created": iso(ts),
        "last_wake": None,
        "next_wake": None,
        "wakes": {},
        "inflight": None,
        "questions": [],
        "approvals": [],
        "held": [],
        "seq": 0,
        **extra,
    })


# ---------------------------------------------------------------- event


def find_goal(root: Path, name: str) -> "Path | None":
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        state = read_json(child / "state.json", None) if child.is_dir() else None
        if isinstance(state, dict) and state.get("name") == name:
            return child
    return None


def addressed(body: str) -> "tuple[str, str] | None":
    """1 行目が【名前】で始まるコメントなら (名前, 残りの本文)。"""
    m = re.match(r"^【([^\s【】]{1,40})】[ \t　]*(.*)$", body, re.S)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def find_approval(state: dict, aid: str) -> "dict | None":
    for entry in state.get("approvals") or []:
        if entry.get("id") == aid.lower():
            return entry
    return None


def take_reply(d: Path, state: dict, policy: dict, author: str, text: str, base: dict, ts: float) -> str:
    """ハブの issue の返事を、承認・却下・質問への返事・ふつうの出来事のどれかとして受け取る。"""
    m = DECISION_RE.match(text)
    if m:
        verb, aid, why = m.group(1), m.group(2).lower(), m.group(3).strip()
        entry = find_approval(state, aid)
        approvers = [str(x) for x in (policy.get("approvers") or [])]
        if entry is None or entry.get("status") != "pending":
            put_event(d, state, {**base, "text": f"{verb} {aid}（該当する承認待ちはありません）"}, ts)
            return "unknown-approval"
        if approvers and author not in approvers:
            put_event(d, state, {**base, "text": f"{author} さんから {aid} の{verb}がありましたが、承認できる人ではないので受けていません"}, ts)
            return "not-approver"
        entry["decided_by"] = author
        entry["decided_at"] = iso(ts)
        if verb == "承認":
            entry["status"] = "approved"      # 次の act が実行する
            return "approved"
        entry["status"] = "rejected"
        entry["note"] = why
        routines.on_rejected(state, entry, why)
        msg = f"{aid}（{entry.get('summary', '')}）は却下されました" + (f"。理由: {why}" if why else "")
        put_event(d, state, {**base, "text": msg}, ts)
        return "rejected"
    handled = routines.take_reply(d, state, policy, text, base, ts, routine_api(d))
    if handled:
        return handled
    m = ANSWER_RE.match(text)
    if m:
        qid, answer = m.group(1).lower(), m.group(2).strip()
        for q in state.get("questions") or []:
            if q.get("id") == qid:
                q["answer"] = answer
                q["answered_at"] = iso(ts)
                put_event(d, state, {**base, "question": qid, "text": f"{qid} への返事: {answer}（質問: {q.get('question', '')}）"}, ts)
                return "answer"
    put_event(d, state, {**base, "text": text}, ts)
    return "event"


def cmd_event(a) -> int:
    ts = now_ts()
    if a.change is not None:
        raw = a.change.strip()
        if not raw:
            return EXIT_OK                       # 変化が無かった回
        try:
            change = json.loads(raw)
        except json.JSONDecodeError:
            print("変化の JSON が読めません", file=sys.stderr)
            return EXIT_USAGE
        record = change.get("record") or {}
        if change.get("kind") != "appeared" or record.get("system"):
            return EXIT_OK
        hit = addressed(str(record.get("body") or ""))
        if not hit:
            return EXIT_OK
        name, text = hit
        d = Path(a.dir) if a.dir else find_goal(Path(a.root), name)
        if d is None:
            return EXIT_OK                       # 知らない名前（ほかの用途のコメント）
        author = (record.get("author") or {}).get("username") or ""
        with locked(d):
            state = load_state(d)
            if state.get("name") != name:
                return EXIT_OK
            if not text:
                return EXIT_OK
            result = take_reply(d, state, load_policy(d), author,
                                text, {"from": "hub", "author": author, "id": record.get("id")}, ts)
            state["last_event_ts"] = ts
            if state.get("status") in ("done", "review"):
                state["status"] = "active"       # 終えた目標に話しかけたら、また動かす
            save_state(d, state)
        print(f"受けました: {result}")
        return EXIT_OK

    if not a.dir:
        print("--text / --file では --dir を指定してください", file=sys.stderr)
        return EXIT_USAGE
    d = Path(a.dir)
    text = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or "")
    if not text.strip():
        print("出来事の本文が空です", file=sys.stderr)
        return EXIT_USAGE
    with locked(d):
        state = load_state(d)
        path = put_event(d, state, {"from": a.source, "text": text.strip()}, ts)
        state["last_event_ts"] = ts
        if state.get("status") in ("done", "review"):
            state["status"] = "active"
        save_state(d, state)
    print(f"置きました: {path.name}")
    return EXIT_OK


# ---------------------------------------------------------------- tick


def job_busy(d: Path) -> bool:
    """この目標の依頼が受け箱で待っているか、取りかかられているか。"""
    for sub in ("pending", "running"):
        folder = d / JOB_BOX / sub
        if any(p.stem.endswith("-" + JOB_LABEL) for p in sorted_files(folder, ".md")):
            return True
    return False


def restore_inflight(d: Path, state: dict) -> "list[Path]":
    """取りかかり中だった出来事を inbox に戻す。"""
    held = d / WORK / "inflight"
    moved = []
    for p in sorted_files(held, ".json"):
        target = d / "inbox" / p.name
        os.replace(p, target)
        moved.append(target)
    state["inflight"] = None
    return moved


def wake_reasons(d: Path, state: dict, ts: float) -> "list[str]":
    reasons = []
    if not state.get("last_wake"):
        reasons.append("start")
    if sorted_files(d / "inbox", ".json"):
        reasons.append("events")
    nxt = state.get("next_wake")
    if nxt is not None and float(nxt) <= ts and state.get("status") == "active":
        reasons.append("due")
    if state.get("status") == "done":
        return []
    if state.get("status") == "review":
        return [r for r in reasons if r == "events"]
    return reasons


def proactive_reason(state: dict, policy: dict, ts: float) -> "list[str]":
    """起こす理由が無いとき、手の空いた時間の調べもの（読み取りだけ）をさせるか。"""
    limit = int(policy.get("proactive_per_day") or 0)
    if limit <= 0 or state.get("status") != "active" or not state.get("last_wake") or state.get("routine"):
        return []
    if int((state.get("proactive") or {}).get(today(ts)) or 0) >= limit:
        return []
    if in_quiet(str(policy.get("quiet_hours") or ""), ts):
        return []
    idle = float(policy.get("proactive_idle_min") or 0) * 60
    last = max(float(state.get("last_wake_ts") or 0), float(state.get("last_event_ts") or 0))
    if ts - last < idle:
        return []                                 # 利用者とのやりとりが最近あった
    nxt = state.get("next_wake")
    if nxt is not None and float(nxt) - ts < idle:
        return []                                 # もうすぐ起きる予定がある
    return ["proactive"]


def read_notes(d: Path, limit: int = NOTE_LINES) -> "list[str]":
    lines: list[str] = []
    for p in reversed(sorted_files(d / "notes", ".md")):
        part = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        lines = [f"{p.stem} {ln}" for ln in part] + lines
        if len(lines) >= limit:
            break
    return lines[-limit:]


def level_of(policy: dict, action: str, prop: dict) -> str:
    """作用の段（auto / ask / deny）。知らない作用・登録していないコマンドは deny。"""
    if action not in KNOWN_ACTIONS:
        return "deny"
    if action == "run":
        spec = (policy.get("commands") or {}).get(str(prop.get("name") or ""))
        if not isinstance(spec, dict) or not spec.get("run"):
            return "deny"
        level = spec.get("level", "ask")
    else:
        level = (policy.get("actions") or {}).get(action, "deny")
    return level if level in LEVELS else "deny"


def build_prompt(d: Path, state: dict, policy: dict, events: "list[dict]", reasons: "list[str]", ts: float) -> str:
    goal = (d / "goal.md").read_text(encoding="utf-8").strip() if (d / "goal.md").exists() else "（goal.md がありません）"
    name = state["name"]
    out = []
    out.append(f"あなたは目標「{name}」を、何日かかけて進めるエージェントです。今回起きた理由: {', '.join(reasons)}。")
    out.append(f"いまは {iso(ts)} です。作業フォルダはこのプロジェクトのフォルダです。")
    out.append("")
    out.append("## 目標（goal.md）")
    out.append(goal)
    out.append("")
    out.append("## 新しい出来事")
    if events:
        for e in events:
            who = f"{e.get('author')} さん" if e.get("author") else e.get("from", "")
            out.append(f"- {e.get('at', '')} {who}: {e.get('text', '')}")
    else:
        out.append("- なし")
    out.append("")
    out.append("## 自分のメモ（直近）")
    notes = read_notes(d)
    if notes:
        out.extend(f"- {n}" for n in notes)
    else:
        out.append("- なし")
    open_qs = [q for q in state.get("questions") or [] if not q.get("answer")]
    if open_qs:
        out.append("")
        out.append("## 返事を待っている質問")
        out.extend(f"- {q['id']} {q.get('question', '')}" for q in open_qs[-QUESTION_KEEP:])
    pending = [x for x in state.get("approvals") or [] if x.get("status") in ("pending", "approved")]
    if pending:
        out.append("")
        out.append("## 承認を待っている提案（同じものを出し直さない）")
        out.extend(f"- {x['id']} {x.get('summary', '')}（{x.get('status')}）" for x in pending)
    out.extend(routines.prompt_section(d, state, policy, events))
    out.append("")
    if reasons == ["proactive"]:
        out.append("## 今回は調べものの回")
        out.append("利用者はいまこの目標に触れていません。手が空いた時間に、読むだけで進められることを調べます。")
        out.append("- 読むのはこのフォルダと、許された資料だけ。ファイルを作ってよいのは notes/ と outbox/ だけ。")
        out.append("- 書ける提案は メモ（note）・質問（ask）・次に起きる時刻（wait）だけ。報告・コマンド・完了は書かない（書いても捨てられる）。")
        out.append("- 調べることが無ければ、メモを 1 件だけ書いて終える。")
        out.append("")
    out.append("## やり方")
    out.append("1. 目標とメモと出来事を読み、今回進められることを進める。調べる・下書きを作るのはこのフォルダの中で行う。")
    out.append("2. 外へは何も書かない（メール・チャット・issue・push などは直接しない）。外へ出したいことは提案として outbox/ に書く。")
    out.append("3. 終える前に、outbox/ に JSON のファイルを 1 件以上書く（1 件 1 ファイル、名前は 01.json, 02.json …）。")
    out.append("")
    out.append("書ける提案:")
    out.append('- メモ: {"kind": "note", "body": "次に読むべきは仕様書の 3 章"}')
    hub = level_of(policy, "hub.comment", {})
    if hub != "deny":
        note = "" if hub == "auto" else "（利用者の承認のあとで書かれる）"
        out.append('- 利用者への報告: {"kind": "act", "action": "hub.comment", "body": "下書きを drafts/plan.md に置きました"}' + note)
    commands = {k: v for k, v in (policy.get("commands") or {}).items()
                if level_of(policy, "run", {"name": k}) != "deny"}
    if commands:
        out.append('- 登録したコマンドを動かす: {"kind": "act", "action": "run", "name": "<名前>", "input": "コマンドの標準入力に渡す文", "why": "動かす理由"}')
        out.append("  動かせるコマンド:")
        for k, v in commands.items():
            when = "すぐ動く" if level_of(policy, "run", {"name": k}) == "auto" else "利用者が承認してから動く"
            out.append(f"  - {k}: {v.get('description', '')}（{when}。結果は次の出来事として届く）")
    out.append('- 利用者への質問: {"kind": "ask", "question": "A と B のどちらで進めますか", "options": ["A", "B"]}')
    out.append('- 次に起きる時刻: {"kind": "wait", "until": "2026-11-25T09:00", "why": "先方の返事待ち"}')
    out.append('- 完了の報告: {"kind": "done", "evidence": ["drafts/plan.md に完了の条件の 3 点を書いた"]}')
    out.append("")
    out.append(f"利用者は、ハブの issue に「【{name}】」で始まるコメントを書いて返事をします。返事は次の出来事として届きます。")
    out.append("wait を書かなければ、次は返事が来るまで起きません。")
    return "\n".join(out) + "\n"


def cmd_tick(a) -> int:
    d = Path(a.dir)
    ts = a.now if a.now is not None else now_ts()
    with locked(d):
        return _tick(d, ts)


def _tick(d: Path, ts: float) -> int:
    state = load_state(d)
    policy = load_policy(d)

    if job_busy(d):
        return EXIT_OK                            # 前の依頼がまだ受け箱にある
    inflight = state.get("inflight")
    if inflight:
        if inflight.get("kind") == "discovery" and evaluation_code(d, inflight) is not None:
            return EXIT_OK
        if ts - float(inflight.get("since") or 0) < INFLIGHT_STALE_SEC:
            return EXIT_OK                        # act がまだ片付けていない
        restore_inflight(d, state)
        append_journal(d, {"kind": "tick", "result": "stale", "why": "取りかかり中のまま古くなったので出来事を戻した"}, ts)

    reasons = wake_reasons(d, state, ts) or proactive_reason(state, policy, ts)
    if not reasons:
        save_state(d, state)
        return EXIT_OK

    day = today(ts)
    used = int((state.get("wakes") or {}).get(day) or 0)
    limit = int(policy.get("wake_per_day") or 0)
    if limit and used >= limit:
        if state.get("budget_noted") != day:
            state["budget_noted"] = day
            append_journal(d, {"kind": "tick", "result": "budget", "why": f"今日は {limit} 回起きたので止める"}, ts)
        save_state(d, state)
        return EXIT_OK

    evaluation = None
    if reasons == ["proactive"]:
        try:
            config = discovery.parse_policy(policy, d)
            if config["enabled"]:
                rows = proposal_queue(d)
                remaining = proposal_remaining(config, rows, ts)
                excluded = {ident for row in rows for ident in row.get("signal_ids", [])}
                material = discovery.evaluation_material(d, config, excluded, remaining) if remaining else None
                if not material:
                    save_state(d, state)
                    return EXIT_OK
                prompt, sources = material
                ident = "e" + uuid.uuid4().hex
                evaluation = {"id": ident, "sources": sources, "remaining": remaining}
                write_json(discovery.index_dir(d) / "evaluation.json", evaluation)
        except discovery.SOURCE_ERRORS as e:
            append_journal(d, {"kind": "tick", "result": "discovery-error",
                               "why": discovery._error_reason(e)}, ts)
            save_state(d, state)
            return EXIT_OK

    held = d / WORK / "inflight"
    held.mkdir(parents=True, exist_ok=True)
    events = []
    for p in sorted_files(d / "inbox", ".json"):
        events.append(read_json(p, {}))
        os.replace(p, held / p.name)

    for event in events:
        ident = event.get("proposal_id")
        link = state.get("discovery_links", {}).get(ident)
        if event.get("from") == "discovery" and link:
            link["delivered"] = True
            if not link.get("machine"):
                active = state.setdefault("discovery_active", [])
                if ident not in active:
                    active.append(ident)
    if evaluation is None:
        prompt = build_prompt(d, state, policy, events, reasons, ts)
    (d / WORK / "prompt.md").write_text(prompt, encoding="utf-8")
    state.pop("discovery_outbox", None)
    state["wakes"] = {day: used + 1}              # 今日の分だけ残す
    state["last_wake"] = iso(ts)
    state["last_wake_ts"] = ts
    if reasons == ["proactive"]:
        state["proactive"] = {day: int((state.get("proactive") or {}).get(day) or 0) + 1}
    if "due" in reasons:
        state["next_wake"] = None
    state["inflight"] = {"since": ts, "reasons": reasons, "tries": int(state.get("pending_tries") or 0)}
    if evaluation:
        state["inflight"].update(kind="discovery", evaluation_id=evaluation["id"])
    save_state(d, state)
    append_journal(d, {"kind": "tick", "result": "wake", "reasons": reasons, "events": len(events)}, ts)
    print("wake: " + ",".join(reasons))
    return EXIT_OK


# ---------------------------------------------------------------- act


def parse_until(value: str) -> float:
    t = dt.datetime.fromisoformat(str(value).strip())
    if t.tzinfo is None:
        t = t.astimezone()                        # タイムゾーンが無ければこの PC の時刻
    return t.timestamp()


def in_quiet(spec: str, ts: float) -> bool:
    """quiet_hours（例 "22:00-07:00"）の間か。日をまたいでよい。読めなければ静かにしない。"""
    m = QUIET_RE.match(spec or "")
    if not m:
        return False
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    t = dt.datetime.fromtimestamp(ts)
    now = t.hour * 60 + t.minute
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def decode_output(raw: bytes) -> str:
    """コマンドの出力を読む。UTF-8 で読めなければ CP932（Windows の日本語の既定）で読み直す。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def run_command(d: Path, command: "list[str]", stdin: str = "", timeout: float = 600) -> "tuple[int, str]":
    # Python のコマンドは標準入出力を UTF-8 にする（Windows の既定のままだと日本語を書けずに落ちる）
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(command, cwd=str(d), input=stdin.encode("utf-8"),
                              capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, f"動かせませんでした: {e}"
    out = (proc.stdout or b"") + (proc.stderr or b"")
    return proc.returncode, decode_output(out).strip()[-OUTPUT_TAIL:]


def summarize(action: str, prop: dict, policy: dict) -> str:
    if action == "run":
        name = str(prop.get("name"))
        desc = ((policy.get("commands") or {}).get(name) or {}).get("description", "")
        return f"コマンド {name}" + (f"（{desc}）" if desc else "")
    if action == "hub.comment":
        body = str(prop.get("body") or "").strip().splitlines()
        return "ハブへの報告: " + (body[0][:60] if body else "")
    return action


def approval_comment(state: dict, entry: dict) -> str:
    prop = entry.get("proposal") or {}
    lines = [f"承認待ち {entry['id']}: {entry.get('summary', '')}"]
    detail = str(prop.get("input") or prop.get("body") or "").strip()
    if detail:
        lines.append(detail[:500])
    if prop.get("why"):
        lines.append(f"理由: {prop['why']}")
    lines.append(f"（「【{state['name']}】承認 {entry['id']}」か「【{state['name']}】却下 {entry['id']}」で返信してください）")
    return "\n".join(lines)


def handle(d: Path, state: dict, policy: dict, prop: dict, ts: float, comments: "list[str]", to_run: "list[dict]",
           proactive: bool = False) -> dict:
    """1 件の提案を片付け、journal に残す中身を返す。動かすものは to_run に足す（ロックの外で動かす）。"""
    kind = prop.get("kind")
    if kind == "propose":
        return {"result": "denied", "why": "対応する Discovery 評価がありません"}
    if proactive and kind not in PROACTIVE_KINDS:
        return {"result": "denied", "why": "調べものの回では、メモ・質問・時刻のほかは提案できない"}
    if kind == "note":
        body = str(prop.get("body") or "").strip()
        if not body:
            return {"result": "denied", "why": "body が空"}
        path = d / "notes" / f"{today(ts)}.md"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {dt.datetime.fromtimestamp(ts).strftime('%H:%M')} " + body.replace("\n", " ") + "\n")
        return {"result": "done"}
    if kind == "act":
        action = str(prop.get("action") or "")
        if action not in KNOWN_ACTIONS:
            return {"result": "denied", "why": f"知らない作用: {action}"}
        missing = [k for k in KNOWN_ACTIONS[action] if not str(prop.get(k) or "").strip()]
        if missing:
            return {"result": "denied", "why": "項目が足りない: " + ", ".join(missing)}
        level = level_of(policy, action, prop)
        if level == "deny":
            return {"result": "denied", "why": f"境界で許していない: {summarize(action, prop, policy)}"}
        entry = {"id": next_id(state, "a"), "action": action, "proposal": prop,
                 "summary": summarize(action, prop, policy), "created": iso(ts), "created_ts": ts}
        if level == "auto" and action == "hub.comment":
            comments.append(str(prop["body"]).strip())   # 報告はその場で書く（承認の記録を作らない）
            return {"result": "done"}
        if level == "auto":
            entry["status"] = "approved"
            entry["decided_by"] = "policy"
            state["approvals"].append(entry)
            to_run.append(entry)
            return {"result": "accepted", "id": entry["id"]}
        entry["status"] = "pending"
        state["approvals"].append(entry)
        comments.append(approval_comment(state, entry))
        return {"result": "asked", "id": entry["id"]}
    if kind == "ask":
        question = str(prop.get("question") or "").strip()
        if not question:
            return {"result": "denied", "why": "question が空"}
        options = [str(o) for o in (prop.get("options") or []) if str(o).strip()]
        qid = next_id(state, "q")
        lines = [f"質問 {qid}: " + question]
        lines.extend(f"- {o}" for o in options)
        example = options[0] if options else "…"
        lines.append(f"（「【{state['name']}】{qid} {example}」のように返信してください）")
        comments.append("\n".join(lines))
        qs = list(state.get("questions") or [])
        qs.append({"id": qid, "at": iso(ts), "question": question, "options": options})
        state["questions"] = qs[-QUESTION_KEEP * 2:]
        return {"result": "done", "id": qid}
    if kind == "routine":
        try:
            return routines.handle(d, state, policy, prop, ts, comments, routine_api(d))
        except routines.RoutineError as e:
            return {"result": "denied", "why": str(e)}
    if kind == "wait":
        try:
            until = parse_until(prop.get("until"))
        except (TypeError, ValueError):
            return {"result": "denied", "why": f"until が読めない: {prop.get('until')}"}
        state["next_wake"] = until
        return {"result": "done", "until": iso(until)}
    if kind == "done":
        evidence = [str(e) for e in (prop.get("evidence") or [])]
        check = policy.get("done_check") or []
        if check:
            code, out = run_command(d, [str(c) for c in check])
            if code != 0:
                comments.append("完了と報告しましたが、完了の検査が通りませんでした。続けます。\n" + out[-COMMENT_TAIL:])
                return {"result": "denied", "why": "完了の検査が通らない", "check": out}
            state["status"] = "done"
            comments.append("完了しました。\n" + "\n".join(f"- {e}" for e in evidence))
            return {"result": "done"}
        state["status"] = "review"
        comments.append("完了したと報告しています。確かめてください（続けるなら「【"
                        + state["name"] + "】」で始めて返信）。\n" + "\n".join(f"- {e}" for e in evidence))
        return {"result": "done", "status": "review"}
    return {"result": "denied", "why": f"知らない提案: {kind}"}


def expire_approvals(d: Path, state: dict, policy: dict, ts: float) -> None:
    days = float(policy.get("approval_expire_days") or 0)
    if days <= 0:
        return
    for entry in state["approvals"]:
        if entry.get("status") == "pending" and ts - float(entry.get("created_ts") or ts) > days * 86400:
            entry["status"] = "expired"
            put_event(d, state, {"from": "goalie", "text": f"{entry['id']}（{entry.get('summary', '')}）は返事が無いまま {days:g} 日たったので取り下げました"}, ts)
            append_journal(d, {"kind": "approval", "id": entry["id"], "result": "expired"}, ts)


def trim_approvals(state: dict) -> None:
    live = [x for x in state["approvals"] if x.get("status") in ("pending", "approved", "running")]
    old = [x for x in state["approvals"] if x not in live]
    state["approvals"] = old[-APPROVAL_KEEP:] + live


def execute(d: Path, policy: dict, entry: dict) -> dict:
    """承認された作用を 1 つ実行する（ロックの外）。"""
    prop = entry.get("proposal") or {}
    if str(entry.get("action") or "").startswith("routine."):
        return {"code": 0, "internal": True}      # 台帳の書き換えはロックの中で（act の後半）
    if entry.get("action") == "hub.comment":
        return {"code": 0, "comment": str(prop.get("body") or "").strip()}
    spec = (policy.get("commands") or {}).get(str(prop.get("name") or "")) or {}
    command = [str(c) for c in (spec.get("run") or [])]
    if not command:
        return {"code": -1, "output": "コマンドが登録されていません（policy.json から消えた）"}
    code, out = run_command(d, command, str(prop.get("input") or ""), float(spec.get("timeout") or 600))
    return {"code": code, "output": out}


def cmd_act(a) -> int:
    d = Path(a.dir)
    ts = a.now if a.now is not None else now_ts()
    comments: list[str] = []
    auto_approved: set[str] = set()
    with locked(d):
        state = load_state(d)
        policy = load_policy(d)
        comment_file = d / WORK / "hub-comment.txt"
        if comment_file.exists():
            comment_file.unlink()
        to_run: list[dict] = []
        if not job_busy(d):                       # まだ終わっていない依頼の提案は、次の act で片付ける
            _take_proposals(d, state, policy, ts, comments, to_run, auto_approved)
            try:
                routines.advance(d, state, policy, ts, comments, routine_api(d))
            except (routines.RoutineError, OSError) as e:
                comments.append(f"業務を進められませんでした: {e}")
        expire_approvals(d, state, policy, ts)
        for entry in state["approvals"]:
            if entry.get("status") == "approved" and entry not in to_run:
                to_run.append(entry)              # 返事で承認されたもの
        for entry in state["approvals"]:
            if entry.get("status") == "running" and ts - float(entry.get("started_ts") or 0) > RUNNING_STALE_SEC:
                entry["status"] = "failed"        # 前の act が実行の途中で止まった。二重に動かさないよう、やり直さない
                put_event(d, state, {"from": "goalie", "approval": entry["id"],
                                     "text": f"{entry['id']}（{entry.get('summary', '')}）は実行の途中で止まったようです。結果は分かりません"}, ts)
                comments.append(f"{entry['id']}（{entry.get('summary', '')}）は実行の途中で止まったようです。結果を確かめてください")
                append_journal(d, {"kind": "run", "id": entry["id"], "result": "lost"}, ts)
        for entry in to_run:
            entry["status"] = "running"
            entry["started_ts"] = ts
        save_state(d, state)

    results = [(entry["id"], execute(d, policy, entry)) for entry in to_run]

    with locked(d):
        state = load_state(d)
        for aid, res in results:
            entry = find_approval(state, aid) or {}
            ok = res["code"] == 0
            entry["status"] = "done" if ok else "failed"
            entry["finished"] = iso(ts)
            append_journal(d, {"kind": "run", "id": aid, "action": entry.get("action"),
                               "proposal": entry.get("proposal"), "code": res["code"],
                               "output": res.get("output", "")}, ts)
            if res.get("internal"):
                try:
                    routines.on_approved(d, state, policy, entry, ts, comments, routine_api(d))
                except (routines.RoutineError, OSError) as e:
                    entry["status"] = "failed"
                    comments.append(f"{aid}（{entry.get('summary', '')}）を進められませんでした: {e}")
                continue
            if "comment" in res:
                comments.append(res["comment"])
                continue
            word = "動かしました" if ok else "失敗しました"
            tail = (res.get("output") or "").strip()
            put_event(d, state, {"from": "goalie", "approval": aid,
                                 "text": f"{aid}（{entry.get('summary', '')}）を{word}（終了コード {res['code']}）\n{tail}"}, ts)
            if entry.get("decided_by") != "policy" or not ok:
                comments.append(f"{aid}（{entry.get('summary', '')}）を{word}（終了コード {res['code']}）"
                                + (f"\n{tail[-COMMENT_TAIL:]}" if tail else ""))
        trim_approvals(state)

        if comments and in_quiet(str(policy.get("quiet_hours") or ""), ts):
            state["held"].extend(comments)        # 静かにする時間帯は溜めて、明けてから書く
            comments = []
        elif state["held"] and not in_quiet(str(policy.get("quiet_hours") or ""), ts):
            comments = list(state["held"]) + comments
            state["held"] = []
        save_state(d, state)

        if comments:
            comment_file = d / WORK / "hub-comment.txt"
            text = f"【{state['name']}より】\n\n" + "\n\n".join(comments) + "\n"
            comment_file.write_text(text, encoding="utf-8")
            print(str(comment_file))
    reconcile_discovery(d, ts, auto_approved)
    return EXIT_OK


def _take_proposals(d: Path, state: dict, policy: dict, ts: float, comments: "list[str]", to_run: "list[dict]",
                    auto_approved: set[str]) -> None:
    proposals = sorted_files(d / "outbox", ".json")
    inflight = state.get("inflight") or {}
    evaluating = inflight.get("kind") == "discovery"
    code = evaluation_code(d, inflight) if evaluating else None
    evaluated = {}
    if evaluating:
        ident = inflight["evaluation_id"]
        evaluated = {row["file"]: row for row in json_lines(d / "journal.jsonl")
                     if row.get("kind") == "proposal" and row.get("evaluation_id") == ident}
        if not proposals:
            restore_inflight(d, state)
            if not evaluated:
                result = "empty" if code == 0 else "evaluation-unknown" if code is None else "evaluation-failed"
                append_journal(d, {"kind": "act", "evaluation_id": ident,
                                   "result": result, "code": code}, ts)
            return
    if not proposals and state.pop("discovery_outbox", None):
        for p in sorted_files(d / WORK / "inflight", ".json"):
            p.unlink()
        state["inflight"] = None
        state.pop("pending_tries", None)
        return
    if not proposals:
        inflight = state.get("inflight")
        if not inflight:
            return
        tries = int(inflight.get("tries") or 0) + 1
        moved = restore_inflight(d, state)
        if tries > MAX_RETRIES:
            dropped = d / WORK / "dropped"
            dropped.mkdir(parents=True, exist_ok=True)
            for p in moved:
                os.replace(p, dropped / p.name)
            state.pop("pending_tries", None)
            append_journal(d, {"kind": "act", "result": "dropped", "why": "提案が無い回が続いたので出来事を捨てた", "events": len(moved)}, ts)
            return
        state["pending_tries"] = tries
        reasons = inflight.get("reasons") or []
        if "start" in reasons:
            state["last_wake"] = None             # 始めの回をやり直す
        if "due" in reasons and state.get("next_wake") is None:
            state["next_wake"] = ts               # 期限の回をやり直す
        append_journal(d, {"kind": "act", "result": "retry", "why": "提案が 1 件も無かった", "tries": tries}, ts)
        return

    proactive = ((state.get("inflight") or {}).get("reasons") or []) == ["proactive"]
    done_dir = d / WORK / "outbox-done"
    done_dir.mkdir(parents=True, exist_ok=True)
    for p in proposals:
        if p.name in evaluated:
            row = evaluated[p.name]
            received = dt.datetime.fromisoformat(row["at"]).timestamp()
            os.replace(p, done_dir / f"{stamp(received)}-{inflight['evaluation_id']}-{p.name}")
            continue
        previous = (state.get("discovery_outbox") or {}).get(p.name)
        if previous:
            os.replace(p, done_dir / previous)
            continue
        job = state.get("routine") or {}
        try:
            prop = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            entry, prop = {"result": "denied", "why": f"JSON が読めない: {e}"}, None
        else:
            if evaluating and isinstance(prop, dict):
                if code == 0:
                    entry = receive_discovery(d, policy, inflight, p.name, prop, ts)
                elif code is None:
                    entry = {"result": "denied", "why": "評価の終了コードが確認できない"}
                else:
                    entry = {"result": "denied", "why": "Discovery 評価が正常終了していません"}
            elif isinstance(prop, dict):
                entry = handle(d, state, policy, prop, ts, comments, to_run, proactive)
            else:
                entry, prop = {"result": "denied", "why": "JSON がオブジェクトでない"}, None
        if evaluating and "auto" in entry:
            auto_approved.add(entry["id"])
        row = {"kind": "proposal", "file": p.name, "proposal": prop, **entry}
        if evaluating:
            row["evaluation_id"] = inflight["evaluation_id"]
        if isinstance(prop, dict) and prop.get("kind") == "done" and not proactive and state.get("discovery_active"):
            result_id = "done-" + uuid.uuid4().hex
            row.update(result_id=result_id, proposal_ids=state["discovery_active"][:])
            if entry["result"] == "done" and entry.get("status") != "review":
                state["discovery_active"] = []
            state.setdefault("discovery_done", []).append(result_id)
        append_journal(d, row, ts)
        suffix = f"{inflight['evaluation_id']}-" if evaluating else ""
        archive = f"{stamp(ts)}-{suffix}{p.name}"
        if row.get("result_id") or (job.get("discovery_id") and isinstance(prop, dict)
                                    and prop.get("kind") == "routine" and prop.get("id") == job["id"]):
            state.setdefault("discovery_outbox", {})[p.name] = archive
            save_state(d, state)
        os.replace(p, done_dir / archive)

    for p in sorted_files(d / WORK / "inflight", ".json"):
        p.unlink()
    state["inflight"] = None
    state.pop("pending_tries", None)
    state.pop("discovery_outbox", None)


def json_lines(path: Path):
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except FileNotFoundError:
        pass


def read_queued_proposal(path: Path) -> dict:
    try:
        row = read_proposal(path)
        dt.datetime.fromisoformat(row.get("received_at", "")).timestamp()
        ids = row.get("signal_ids", [])
        if isinstance(ids, list) and all(isinstance(ident, str) for ident in ids):
            return row
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    return {}


def proposal_queue(d: Path) -> list[dict]:
    paths = sorted_files(discovery.index_dir(d) / "proposals", ".json")
    return [row for path in paths if (row := read_queued_proposal(path))]


def proposal_remaining(config: dict, rows: list[dict], ts: float) -> int:
    used = sum(today(dt.datetime.fromisoformat(received).timestamp()) == today(ts)
               for row in rows if (received := row.get("received_at")))
    return max(0, config["proposals_per_day"] - used)


def evaluation_code(d: Path, inflight: dict) -> int | None:
    """評価の起床後に受け箱へ置かれた最新の同名依頼の結果を返す。"""
    box = d / JOB_BOX
    since = dt.datetime.fromtimestamp(inflight["since"]).strftime("%Y%m%d-%H%M%S%f")
    jobs = [(job.name, False) for job in sorted_files(box / "done", f"-{JOB_LABEL}.md")]
    jobs.extend((job.name.removesuffix(".withdrawn.md") + ".md", True)
                for job in sorted_files(box / "tmp", f"-{JOB_LABEL}.withdrawn.md"))
    current = [(name, withdrawn) for name, withdrawn in jobs if name[:len(since)] >= since]
    if not current:
        return None
    name, withdrawn = max(current)
    if withdrawn:
        return 3
    with contextlib.suppress(FileNotFoundError):
        for line in (box / "results" / name).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                return {"status: ok": 0, "status: failed": 1}.get(line.strip().lower())
    return None


def receive_discovery(d: Path, policy: dict, inflight: dict, filename: str, prop: dict, ts: float) -> dict:
    def denied(reason):
        return {"result": "denied", "why": reason}

    ident = inflight["evaluation_id"]
    if prop.get("kind") != "propose":
        return denied("今回の評価の propose ではありません")
    try:
        folder = discovery.index_dir(d)
        evaluation = read_json(folder / "evaluation.json", {})
        if evaluation.get("id") != ident:
            return denied("対応する評価資料がありません")
        pid = "p" + hashlib.sha256(f"{ident}/{filename}".encode()).hexdigest()[:32]
        path = folder / "proposals" / f"{pid}.json"
        receipt = {"result": "accepted", "id": pid}
        if path.exists():
            prior = read_json(path, {})
            if "auto" in prior:
                receipt["auto"] = prior["auto"]
            return receipt
        if not discovery.proposal_fields(prop):
            return denied("propose の必須項目または列挙値が不正です")
        config = discovery.parse_policy(policy, d)
        if not config["enabled"]:
            return denied("Discovery が無効です")
        active = {row["id"]: row for row in discovery.active_signals(d)}
        coverage = read_json(folder / "coverage.json", {}).get("roots", {})
        evidence = {}
        for label in prop["sources"]:
            matches = evaluation["sources"].get(label)
            if not matches or "://" in label:
                return denied(f"提示されていない出典: {label}")
            if label not in prop["why"]:
                return denied(f"why に出典がありません: {label}")
            for source in matches:
                sid = source["signal_id"]
                row = active.get(sid)
                if not row or any(row[k] != source[k] for k in ("source_path", "kind")) or not (
                    discovery.current_source(config, source, unchanged=True, coverage=coverage)
                ):
                    return denied(f"変更・無効化されたか読み取れない出典: {label}")
                evidence[sid] = source
        rows = proposal_queue(d)
        for row in rows:
            prior_ids = set(row.get("signal_ids", []))
            if prior_ids == evidence.keys() or (row.get("status") == "rejected" and prior_ids.intersection(evidence)):
                return denied("同じ出典の仕事は受付済みまたは却下済みです")
        if not proposal_remaining(config, rows, ts) or sum(
            row.get("evaluation_id") == ident for row in rows
        ) >= evaluation["remaining"]:
            return denied("提案の受付枠を使い切りました")
        record = {key: prop[key] for key in ("title", "why", "sources", "size", "risk", "suggested_route")}
        record.update(id=pid, evaluation_id=ident, signal_ids=sorted(evidence), evidence=list(evidence.values()),
                      status="pending", received_at=iso(ts), decision=None, append_target=None)
        record["append_target"] = discovery_append_target(config, record)
        # 自動承認の効力は登録まで。実行は登録先の policy・フック・done_check に従う。
        if discovery.approval_requirement(config, record) is None:
            auto = discovery.auto_execute_match(config, record)
            if auto is not None:
                record.update(status="approved", auto=auto, decision={
                    "at": iso(ts), "by": "policy",
                    "reason": f"auto_execute の「{auto}」に一致し、require_approval に当たらないため自動承認",
                })
                receipt["auto"] = auto
        write_json(path, record)
        return receipt
    except (discovery.SOURCE_ERRORS + (routines.RoutineError,)) as e:
        return denied(f"Discovery の照合に失敗しました: {e}")


def discovery_append_target(config: dict, proposal: dict) -> dict | None:
    ids = set(proposal["signal_ids"])
    paths = {source["source_path"] for source in proposal["evidence"]}

    def matches(record):
        refs = {discovery.path_key(Path(path)) for path in record.get("source_paths") or []
                if isinstance(path, str) and Path(path).is_absolute()}
        return bool(ids.intersection(record.get("signal_ids") or []) or paths.intersection(refs))

    root = config.get("goal_root")
    if root and root.is_dir():
        for target in sorted(root.iterdir()):
            if target.is_symlink() or not (target / "state.json").is_file():
                continue
            state = load_state(target)
            if any(not link.get("machine") and matches(link) for link in state.get("discovery_links", {}).values()):
                return {"kind": "goal", "dir": str(target)}
            goal = target / "goal.md"
            if goal.is_file():
                body = goal.read_text(encoding="utf-8")
                tokens = re.findall(r"`([^`\n]+)`|\]\(([^)\n]+)\)", body)
                refs = {code or link.split("#", 1)[0] for code, link in tokens}
                file_refs = {discovery.path_key(target / ref) for ref in refs
                             if ref and "://" not in ref and ("/" in ref or "\\" in ref or Path(ref).suffix)}
                if ids & refs or paths & file_refs:
                    return {"kind": "goal", "dir": str(target)}
    target = config.get("routine_dir")
    if target and (target / "state.json").is_file():
        for source in proposal["evidence"]:
            parts = inside(work_path(target) / "runs", Path(source["source_path"]))
            if parts and len(parts) == 2 and parts[1].endswith(".json"):
                name, run_id = parts[0], Path(parts[1]).stem
                run = routines.load_run(target, name, run_id)
                if run and run.get("machine") == name and run.get("id") == run_id:
                    return {"kind": "routine", "dir": str(target), "machine": name,
                            "action": "improve", "run_id": run_id}
        for name, record in routines.load_registry(target).items():
            if matches(record) or matches(record.get("draft") or {}):
                return {"kind": "routine", "dir": str(target), "machine": name}
        state = load_state(target)
        for link in state.get("discovery_links", {}).values():
            if link.get("machine") and matches(link):
                return {"kind": "routine", "dir": str(target), "machine": link["machine"]}
    return None


def discovery_plan(config: dict, proposal: dict) -> dict:
    append = proposal.get("append_target")
    if append:
        plan = dict(append, append=True)
    elif proposal["suggested_route"] == "routine":
        target = config.get("routine_dir")
        if not target:
            raise ValueError("routine_dir が未設定です")
        plan = {"kind": "routine", "dir": str(target), "machine": proposal["id"]}
    else:
        root = config.get("goal_root")
        if not root:
            raise ValueError("goal_root が未設定です")
        plan = {"kind": "goal", "dir": str(root / proposal["id"])}
    plan["event_id"] = "discovery-" + proposal["id"]
    if plan["kind"] == "routine":
        plan["job_key"] = proposal["id"]
    target = Path(plan["dir"])
    root = config.get("goal_root")
    if target.resolve() != target or (
        plan["kind"] == "goal" and (not root or target.parent != root or not root.is_dir())
    ) or (plan["kind"] == "routine" and target != config.get("routine_dir")):
        raise ValueError("登録先が現在の policy の範囲にありません")
    return plan


def discovery_history(d: Path, proposal: dict, event: str, status: str, reason: str, ts: float,
                      ref: dict | None = None, result_id: str = "") -> None:
    path = discovery.index_dir(d) / "history.jsonl"
    event_id = ":".join(filter(None, (proposal["id"], event, result_id)))
    if any(row.get("event_id") == event_id for row in json_lines(path)):
        return
    row = {"event_id": event_id, "at": iso(ts), "proposal_id": proposal["id"],
           "signal_ids": proposal.get("signal_ids", []), "event": event, "status": status,
           "reason": reason, "execution_ref": ref}
    if event == "decision" and proposal.get("auto"):
        row["decision"] = proposal["decision"]
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def register_discovery(plan: dict, proposal: dict, ts: float) -> dict:
    target = Path(plan["dir"])
    pid = proposal["id"]
    is_goal = plan["kind"] == "goal"
    creating = is_goal and not plan.get("append")
    if not creating and not (target / "state.json").is_file():
        raise ValueError("登録先の目標がありません")
    if target.resolve() != target:
        raise ValueError("登録先の実体が変わりました")
    with locked(target, wait=0):
        if target.resolve() != target:
            raise ValueError("登録先の実体が変わりました")
        text = f'{proposal["title"]}\n\n{proposal["why"]}'
        details = {"signal_ids": proposal["signal_ids"],
                   "source_paths": [source["source_path"] for source in proposal["evidence"]]}
        ref = {"kind": plan["kind"], "dir": str(target), "event_id": plan["event_id"]}
        if creating:
            work = work_path(target)
            marked = read_json(work / "discovery-origin.json", None) == pid
            if not marked:
                if any(p != work for p in target.iterdir()) or any(
                    p.name != "lock" for p in work.iterdir()
                ):
                    raise ValueError("提案 ID の登録先に別の仕事があります")
            if not marked:
                write_json(target / WORK / "discovery-origin.json", pid)
            if not (target / "state.json").exists():
                condition = ("1 回分の成果物または検証結果を示す" if proposal["suggested_route"] == "one-shot"
                             else "提案の目的を満たした成果物と、その確認結果を示す")
                (target / "goal.md").write_text(f"# {text}\n\n## 完了の条件\n\n- {condition}\n", encoding="utf-8")
                write_json(hook_path(target), hooks_json(sys.executable))
                write_json(target / "policy.json", {**DEFAULT_POLICY, "actions": {"hub.comment": "deny", "run": "deny"},
                                                   "tools": {"shell": "allowlist"}})
                init_goal(target, pid, ts, discovery_links={pid: details}, discovery_active=[pid])
            return dict(ref, goal=load_state(target)["name"])

        state = load_state(target)
        ref["goal"] = state["name"]
        links = state.setdefault("discovery_links", {})
        link = links.get(pid)
        if link is None:
            statuses = ("active", "review") if is_goal else ("active",)
            if (job_busy(target) or state.get("inflight") or state.get("status") not in statuses
                    or sorted_files(target / "outbox", ".json")):
                raise ValueError("登録先の目標が使用中か、作業を受け付けられません")
            if is_goal:
                link = details
            else:
                link = register_discovery_routine(target, state, plan, details, text, ts)
            links[pid] = link
            save_state(target, state)
        if not link.get("delivered"):
            filename = f'{plan["event_id"]}.json'
            inbox = target / "inbox" / filename
            if not inbox.exists() and not (target / WORK / "inflight" / filename).exists():
                write_json(inbox, {"from": "discovery", "proposal_id": pid, "at": iso(ts),
                                   "text": link.get("message", text)})
            link["delivered"] = True
            save_state(target, state)
        if not is_goal:
            ref.update(machine=link["machine"], job_id=link["job_id"])
        return ref


def register_discovery_routine(target: Path, state: dict, plan: dict,
                               details: dict, text: str, ts: float) -> dict:
    policy = load_policy(target)
    if not routines.enabled(policy) or not routines.statemachine_dir(policy):
        raise ValueError("登録先で routines と statemachine の設定が必要です")
    if state.get("routine"):
        raise ValueError("業務係が使用中です")
    pid = plan["job_key"]
    name = plan["machine"]
    h = routine_api(target)
    messages = []
    h["event"] = lambda state, message, ts, base=None: messages.append(message)
    if plan.get("action") == "improve":
        entry = routines.find_entry(target, policy, name)
        prior = (routines.load_registry(target).get(name) or {}).get("draft") or {}
        if (not entry or entry.get("broken") or entry["status"] != "ready"
                or (prior.get("status") and prior.get("discovery_id") != pid) or state.get("routine_queue")):
            raise ValueError("業務の改善を始められる版と空き枠が必要です")
        routines.queue_improve(state, name, text, plan["run_id"], discovery_id=pid)
        routines.advance(target, state, policy, ts, [], h)
        job = state.get("routine")
        if not job or job.get("discovery_id") != pid:
            raise ValueError("業務の改善ジョブを開始できません")
        return {**details, "machine": name, "job_id": job["id"], "message": "\n".join([text, *messages])}
    if plan.get("append"):
        entry = routines.find_entry(target, policy, name)
        if entry is None or entry.get("broken") or entry["status"] not in ("ready", "unapproved"):
            raise ValueError("登録済みの有効なマシンが必要です")
        if routines.missing_inputs(entry, {}):
            raise ValueError("既存マシンに必要な入力が未指定です")
        if entry["status"] == "unapproved":
            digest = routines.digest(Path(entry["path"]))
            approvals = (item.get("proposal") or {} for item in state["approvals"]
                         if item.get("status") == "pending")
            if any(item.get("discovery_id") == pid and item.get("digest") == digest for item in approvals):
                raise ValueError("既存マシンの版の承認が必要です")
        request = {"action": "run", "machine": name, "request": text}
    else:
        request = {"action": "create", "name": name, "purpose": text, "request": text}
    comments = []
    result = routines.handle(target, state, policy, request, ts, comments, h, discovery_id=pid)
    if result["result"] == "asked" and result.get("id"):
        approval = find_approval(state, result["id"])
        approval["proposal"]["discovery_id"] = pid
        state["held"].extend(comments)
        save_state(target, state)
        raise ValueError("既存マシンの版の承認を待っています")
    if result["result"] != "accepted":
        raise ValueError(result.get("why") or "業務ジョブを開始できません")
    return {**details, "machine": name, "job_id": result["id"], "message": "\n".join([text, *messages])}


def discovery_results(proposal: dict):
    ref = proposal["execution_ref"]
    target = Path(ref["dir"])
    if target.resolve() != target or not (target / "state.json").is_file():
        return
    # The target lock makes journal and state a consistent snapshot.
    with locked(target, wait=0):
        state = load_state(target)
        if ref["kind"] == "routine":
            name = ref["machine"]
            for path in sorted_files(work_path(target) / "runs" / name, ".json"):
                run = routines.load_run(target, name, path.stem)
                if (not run or run.get("machine") != name or run.get("id") != path.stem
                        or run.get("mode") not in ("trial", "run")):
                    continue
                if run.get("discovery_id") != proposal["id"] or run.get("discovery_job") != ref["job_id"]:
                    continue
                if (state.get("routine") or {}).get("id") == run["id"]:
                    continue
                status = ("failed" if run["escalate"] or not run["ok"]
                          else "progress" if run["mode"] == "trial" else "success")
                reason = "escalate" if run["escalate"] else run["summary"]
                yield run["id"], status, reason, dict(ref, run_id=run["id"])
            return
        committed = set(state.get("discovery_done") or [])
        for row in json_lines(target / "journal.jsonl"):
            ident = row.get("result_id")
            if ident not in committed or proposal["id"] not in row.get("proposal_ids", []):
                continue
            if row.get("status") == "review":
                status, reason = "pending", "人の確認待ち"
            elif row.get("result") == "done":
                status, reason = "success", "完了の検査が通りました"
            elif row.get("why") == "完了の検査が通らない":
                status, reason = "progress", row["why"]
            else:
                continue
            yield ident, status, reason, dict(ref, result_id=ident)


def reconcile_discovery(d: Path, ts: float, auto_approved: set[str]) -> None:
    if not (work_path(d) / "discovery" / "proposals").is_dir():
        return
    with locked(d):
        if job_busy(d) or load_state(d).get("inflight"):
            return
        rows = proposal_queue(d)
    for row in rows:
        if row["id"] in auto_approved or row.get("status") not in ("rejected", "approved"):
            continue
        path = discovery.index_dir(d) / "proposals" / f'{row["id"]}.json'
        with locked(d):
            if job_busy(d) or load_state(d).get("inflight"):
                return
            proposal = read_queued_proposal(path)
            status = proposal.get("status")
            if status not in ("rejected", "approved"):
                continue
            decision = proposal.get("decision") or {}
            if not isinstance(decision, dict):
                continue
            if status == "approved" and proposal.get("auto") and not proposal.get("registration") and not proposal.get("execution_ref"):
                try:
                    config = discovery.parse_policy(load_policy(d), d)
                except discovery.SOURCE_ERRORS:
                    continue
                if (discovery.approval_requirement(config, proposal) is not None
                        or discovery.auto_execute_match(config, proposal) != proposal["auto"]):
                    proposal.update(status="pending", decision=None)
                    del proposal["auto"]
                    write_json(path, proposal)
                    continue
            discovery_history(d, proposal, "decision", status, decision.get("reason", ""), ts)
            if status != "approved":
                continue
            plan = None
            if not proposal.get("execution_ref"):
                try:
                    config = discovery.parse_policy(load_policy(d), d)
                    if not config["enabled"]:
                        raise ValueError("Discovery が無効です")
                    if not proposal.get("evidence") or not all(
                        discovery.current_source(config, item, unchanged=False) for item in proposal["evidence"]
                    ):
                        raise ValueError("現在の policy で読めない出典があります")
                    if not proposal.get("registration"):
                        latest = discovery_append_target(config, proposal)
                        recorded = proposal.get("append_target")
                        if recorded and recorded != latest:
                            raise ValueError("追記先との出典の一致を確認できません")
                        proposal["append_target"] = latest
                    plan = discovery_plan(config, proposal)
                    if proposal.get("registration") and proposal["registration"] != plan:
                        raise ValueError("登録先が予約後に変更されました")
                    if not proposal.get("registration"):
                        proposal["registration"] = plan
                        write_json(path, proposal)
                except (discovery.SOURCE_ERRORS + (routines.RoutineError,)) as e:
                    proposal["registration_reason"] = str(e)
                    write_json(path, proposal)
                    continue

        if plan is not None:
            try:
                ref = register_discovery(plan, proposal, ts)
                reason = ""
            except (discovery.SOURCE_ERRORS + (Busy, routines.RoutineError)) as e:
                ref, reason = None, str(e) or "登録先が使用中です"
            with locked(d):
                proposal = read_queued_proposal(path)
                if not proposal.get("execution_ref") and proposal.get("registration") == plan:
                    proposal["registration_reason"] = reason
                    if ref:
                        proposal["execution_ref"] = ref
                    write_json(path, proposal)
        ref = proposal.get("execution_ref")
        if ref:
            with locked(d):
                discovery_history(d, proposal, "registered", "registered", "", ts, ref)
            try:
                results = list(discovery_results(proposal))
            except (discovery.SOURCE_ERRORS + (Busy,)):
                continue
            with locked(d):
                for ident, status, reason, result_ref in results:
                    discovery_history(d, proposal, "result", status, reason, ts, result_ref, ident)


# ---------------------------------------------------------------- hook（Copilot の preToolUse）

HOOK_FILE = "goalie.json"


def hook_path(d: Path) -> Path:
    return d / ".github" / "hooks" / HOOK_FILE


PROTECTED = ("policy.json", "state.json", "journal.jsonl", WORK, ".github")
JOB_TMP = (JOB_BOX, "tmp")                    # 受け箱のうち、エージェントが報告を書く場所
PROACTIVE_WRITABLE = (("notes",), ("outbox",), JOB_TMP)
ROUTINE_DIRS = (routines.DRAFTS, ".statemachine")   # 業務の下書きと登録先（作る・直す回の下書きのほかは書かせない）

SHELL_TOOLS = {"bash", "powershell", "pwsh", "shell", "sh", "cmd", "terminal", "run_in_terminal", "execute", "exec"}
WRITE_TOOLS = {"edit", "create", "write", "write_file", "create_file", "str_replace", "insert", "delete", "rename",
               "move", "apply_patch", "edit_file", "multi_edit", "notebook_edit"}
READ_TOOLS = {"view", "read", "read_file", "glob", "grep", "ls", "list", "list_dir", "search", "find", "rg"}
WEB_TOOLS = {"web_fetch", "fetch", "web_search", "browser", "url_fetch", "http"}
SAFE_TOOLS = {"report_intent", "think", "update_todo", "todo", "todo_write", "task_complete"}
PATH_KEYS = ("path", "file_path", "filePath", "filename", "file", "target", "destination", "dest", "new_path", "source")
WRITE_KEYS = ("content", "file_text", "new_str", "old_str", "new_string", "old_string", "text", "patch", "edits")
SHELL_KEYS = ("command", "cmd", "script")
SHELL_META = re.compile(r"[;|`<>\r\n]|&&|\$\(")
JOB_COMMAND = re.compile(
    r'^\s*"?[^"\s]*?(python3?|py)(\.exe)?"?\s+(?P<quote>")?'
    r'(?:(?(quote)[^"]*|[^"\s]*)[\\/])?'
    r'(?:runner\.py(?(quote)")\s+|goalie\.py(?(quote)")\s+job\s+)'
    r'(next|done|dispatched|finish)(\s|$)', re.I,
)


def find_goal_from(start: Path) -> "Path | None":
    """start とその親から、目標のフォルダ（state.json と policy.json がある）を探す。"""
    try:
        here = start.resolve()
    except OSError:
        return None
    for folder in (here, *here.parents):
        if (folder / "state.json").is_file() and (folder / "policy.json").is_file():
            state = read_json(folder / "state.json", None)
            if isinstance(state, dict) and state.get("name"):
                return folder
    return None


def tool_args(payload: dict) -> dict:
    raw = payload.get("toolArgs", payload.get("tool_input", payload.get("toolInput", {})))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {"_raw": raw}
    return raw if isinstance(raw, dict) else {"_raw": str(raw)}


def inside(base: Path, target: Path) -> "tuple[str, ...] | None":
    """target が base の中なら、base からの相対の部品。外なら None。"""
    try:
        return target.resolve().relative_to(base.resolve()).parts
    except (OSError, ValueError):
        return None


def patch_paths(patch: str) -> "list[str]":
    return re.findall(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", patch, re.M) + \
        re.findall(r"^\*\*\* Move to:\s*(.+?)\s*$", patch, re.M)


def arg_paths(args: dict) -> "list[str]":
    paths = [str(args[k]) for k in PATH_KEYS if isinstance(args.get(k), str) and args.get(k)]
    for key in ("paths", "files"):
        if isinstance(args.get(key), list):
            paths += [str(x) for x in args[key] if isinstance(x, str)]
    for key in ("patch", "input", "_raw"):
        if isinstance(args.get(key), str):
            paths += patch_paths(args[key])
    return paths


def kind_of(name: str, args: dict) -> str:
    base = name.lower().rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    if base in SAFE_TOOLS:
        return "safe"
    if base == "str_replace_editor":
        return "read" if str(args.get("command") or "") == "view" else "write"
    if base in SHELL_TOOLS:
        return "shell"
    if base in WRITE_TOOLS:
        return "write"
    if base in READ_TOOLS:
        return "read"
    if base in WEB_TOOLS:
        return "web"
    if any(ch in name for ch in "/:") or "__" in name or "-" in name:
        return "other"                            # MCP のツールなど、外へつながるかもしれないもの
    if any(k in args for k in SHELL_KEYS):
        return "shell"
    if "url" in args:
        return "web"
    if arg_paths(args):
        return "write" if any(k in args for k in WRITE_KEYS) else "read"
    return "other"


def evaluation_job_command(d: Path, cwd: Path, command: str, inflight: dict) -> bool:
    # 受け箱の実行ファイルと目標内の引数だけを許す。
    body = command.strip().removeprefix("&").strip()
    if re.search(r"[;&|`<>\r\n$%!*?^'()]", body):
        return False
    tokens = re.findall(r'"[^"]*"|[^"\s]+', body)
    if " ".join(tokens) != re.sub(r'\s+(?=(?:[^"]*"[^"]*")*[^"]*$)', " ", body):
        return False
    tokens = [token.strip('"') for token in tokens]
    goalie_job = len(tokens) >= 2 and Path(tokens[1]).name == "goalie.py"
    if goalie_job:
        if tokens[2:3] != ["job"]:
            return False
        del tokens[2]
    if len(tokens) < 5 or len(tokens) % 2 != 1:
        return False
    python, script, action, *options = tokens
    if python.lower() not in ("python", "python3", "py", "python.exe", "python3.exe", "py.exe") and (
        not Path(python).is_absolute() or Path(python).resolve() != Path(sys.executable).resolve()
    ):
        return False
    default_script = Path(__file__).resolve()
    if not goalie_job:
        default_script = default_script.with_name("runner.py")
    expected = Path(inflight.get("job_script") or default_script)
    if (cwd / script).resolve() != expected.resolve() or action not in ("next", "done", "finish"):
        return False
    args = dict(zip(options[::2], options[1::2]))
    if len(args) * 2 != len(options) or "--dir" not in args or (cwd / args.pop("--dir")).resolve() != d.resolve():
        return False
    box = d.resolve() / JOB_BOX
    box_arg = args.pop("--box", str(box))
    if (cwd / box_arg).resolve() != box or any(
        (box / part).resolve() != box / part for part in ("tmp", "pending", "running", "dispatched", "done", "results")
    ):
        return False
    if action != "next":
        job = args.pop("--id", "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+-goalie", job) or args.pop("--status", "") not in ("ok", "failed"):
            return False
        if not any((d / JOB_BOX / state / f"{job}.md").is_file() for state in ("running", "dispatched", "done")):
            return False
        report = args.pop("--report-file", str(box / "tmp" / f"{job}.report.md"))
        if not inside(box / "tmp", cwd / report):
            return False
    if "--python" in args and args.pop("--python") != python:
        return False
    return all(key in ("--until", "--chunk", "--stale", "--stay", "--poll", "--dispatch-stale") and
               re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) for key, value in args.items())


def check_path(d: Path, cwd: Path, raw: str, write: bool, proactive: bool, policy: dict,
               scope: "dict | None" = None) -> "str | None":
    """この道が使えなければ理由を返す。"""
    scope = scope or {"read": [], "write_only": None}
    target = Path(raw)
    if not target.is_absolute():
        target = cwd / target
    parts = inside(d, target)
    if scope.get("evaluation"):
        allowed = (JOB_TMP, ("outbox",)) if write else (
            JOB_TMP, ("outbox",), (work_path(d).name, "prompt.md"), (JOB_BOX, "pending"), (JOB_BOX, "running"),
        )
        if parts is not None and any(parts[:len(prefix)] == prefix for prefix in allowed):
            return None
        return f"評価の回ではこのパスを使えません: {raw}"
    if not write:
        if parts is not None:
            return None
        for folder in list((policy.get("tools") or {}).get("read_allow") or []) + list(scope["read"]):
            if inside(Path(folder), target) is not None:
                return None
        return f"目標のフォルダの外は読めません: {raw}"
    if scope.get("write_only") and parts is not None and tuple(parts[:2]) != JOB_TMP \
            and inside(Path(scope["write_only"]), target) is None \
            and not any(tuple(parts[:len(w)]) == w for w in PROACTIVE_WRITABLE[:2]):
        return f"業務を作る・直す回では、下書き（{scope['write_only']}）と notes/・outbox/ のほかには書けません: {raw}"
    if scope.get("write_only") and inside(Path(scope["write_only"]), target) is not None:
        return None
    if parts is None:
        return f"目標のフォルダの外には書けません: {raw}"
    if not parts:
        return f"目標のフォルダそのものは変えられません: {raw}"
    if tuple(parts[:2]) == JOB_TMP:
        return None
    if parts[0] in PROTECTED or parts[0] in ROUTINE_DIRS or parts[0] == JOB_BOX:
        return f"このファイルは変えられません（動かし方の記録）: {raw}"
    if proactive and not any(tuple(parts[:len(w)]) == w for w in PROACTIVE_WRITABLE):
        return f"調べものの回では notes/ と outbox/ のほかには書けません: {raw}"
    return None


def decide(d: Path, cwd: Path, payload: dict, state: dict, policy: dict) -> "str | None":
    """ツールを止めるなら理由を返す。通すなら None。"""
    tools = policy.get("tools") or {}
    name = str(payload.get("toolName") or payload.get("tool_name") or payload.get("toolname") or "")
    args = tool_args(payload)
    proactive = ((state.get("inflight") or {}).get("reasons") or []) == ["proactive"]
    evaluating = (state.get("inflight") or {}).get("kind") == "discovery"
    scope = {"evaluation": True} if evaluating else routines.hook_scope(d, state, policy)
    if name in (tools.get("deny") or []):
        return f"このツールは止めています: {name}"
    if not evaluating and name in (tools.get("allow") or []):
        return None
    kind = kind_of(name, args)
    if kind == "safe":
        return None
    if kind in ("read", "write"):
        base = name.lower().rsplit("/", 1)[-1].rsplit(".", 1)[-1]
        if evaluating and base not in WRITE_TOOLS | {"view", "read", "read_file", "str_replace_editor"}:
            return f"評価の回ではこのツールを使えません: {name}"
        paths = arg_paths(args)
        if kind == "write" and not paths:
            return f"書き込み先が分からないので止めました: {name}"
        for raw in paths or ["."]:
            why = check_path(d, cwd, raw, kind == "write", proactive, policy, scope)
            if why:
                return why
        return None
    if kind == "shell":
        command = next((str(args[k]) for k in SHELL_KEYS if isinstance(args.get(k), str)), str(args.get("_raw") or ""))
        mode = str(tools.get("shell") or "allowlist")
        if evaluating:
            if mode != "deny" and evaluation_job_command(d, cwd, command, state["inflight"]):
                return None
            return "評価の回では受け箱の処理だけ実行できます"
        if mode == "allow" and not proactive:
            return None
        body = command.strip()
        body = body[1:].lstrip() if body.startswith("&") else body      # PowerShell の呼び出し演算子
        patterns = [JOB_COMMAND] + ([] if proactive else [re.compile(x) for x in tools.get("shell_allow") or []])
        if mode != "deny" and not SHELL_META.search(body) and any(pt.search(body) for pt in patterns):
            return None
        script = routines.script_of(body) if mode != "deny" and not SHELL_META.search(body) else None
        if script:
            path = Path(script) if Path(script).is_absolute() else cwd / script
            if any(inside(Path(x), path) is not None for x in scope["deny_scripts"]):
                return "試運転では外へ書くスキルは動かしません。書く中身を作って、そのステートの出力にしてください: " + command[:120]
            if any(inside(Path(x), path) is not None for x in scope["scripts"]):
                return None
        return "このコマンドは許していません（policy.json の tools.shell_allow に足すと動かせます）: " + command[:120]
    if evaluating:
        return f"評価の回ではこのツールを使えません: {name}"
    if kind == "web":
        allow = [str(h).lower() for h in tools.get("web_allow") or []]
        if "*" in allow:
            return None
        url = str(args.get("url") or args.get("uri") or "")
        host = re.sub(r"^[a-z]+://", "", url.lower()).split("/", 1)[0].split("@")[-1].split(":")[0]
        if host and any(host == h or host.endswith("." + h) for h in allow):
            return None
        return f"この Web は許していません（policy.json の tools.web_allow に足すと読めます）: {host or name}"
    return f"このツールは許していません（policy.json の tools.allow に足すと使えます）: {name}"


def cmd_hook(a) -> int:
    """Copilot の preToolUse から呼ばれる。標準入力にフックの JSON を受け、止めるときだけ JSON を出す。"""
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    cwd = Path(str(payload.get("cwd") or os.getcwd()))
    d = Path(a.dir) if a.dir else find_goal_from(cwd)
    if d is None:
        return EXIT_OK                            # 目標のフォルダの外のセッションには何もしない
    ts = now_ts()
    try:
        state = load_state(d)
        why = decide(d, cwd, payload, state, load_policy(d))
    except Exception as e:                        # 判定できないときは止める（通すほうが危ない）
        why = f"フックの判定に失敗したので止めました: {e}"
    name = str(payload.get("toolName") or payload.get("tool_name") or "")
    try:
        with (work_path(d) / "hook.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": iso(ts), "tool": name, "decision": "deny" if why else "allow",
                                "why": why or ""}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if why:
        print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": why}, ensure_ascii=False))
    return EXIT_OK


def hook_command(script: Path, python: str, shell: str) -> str:
    if shell == "powershell":
        return f"& '{python}' '{script}' hook"
    return f"'{python}' '{script}' hook"


def hooks_json(python: str) -> dict:
    script = Path(__file__).resolve()
    return {"version": 1, "hooks": {"preToolUse": [{
        "type": "command",
        "bash": hook_command(script, python, "bash"),
        "powershell": hook_command(script, python, "powershell"),
        "timeoutSec": 10,
    }]}}


def cmd_hooks(a) -> int:
    python = a.python or sys.executable
    data = hooks_json(python)
    if a.plugin:
        root = Path(a.plugin)
        plugin = root / "plugins" / "goalie"
        write_json(plugin / "hooks.json", data)
        write_json(plugin / "plugin.json", {"name": "goalie", "version": "1.0.0", "hooks": "hooks.json",
                                            "description": "目標のフォルダの中だけで動くよう、ツールを止めるフック"})
        write_json(root / ".github" / "plugin" / "marketplace.json", {
            "name": "goalie", "owner": {"name": "goalie"},
            "plugins": [{"name": "goalie", "source": "./plugins/goalie", "strict": False}]})
        print(f"プラグインを置きました: {root}")
        print("Copilot アプリの Customize → Plugins → 歯車 → マーケットプレイスの追加で、このフォルダを足し、goalie を入れてください。")
        print("プラグインはすべてのプロジェクトで動きますが、目標のフォルダの外では何もしません。")
        return EXIT_OK
    if not a.dir:
        print("--dir か --plugin を指定してください", file=sys.stderr)
        return EXIT_USAGE
    d = Path(a.dir)
    load_state(d)
    with locked(d):
        path = hook_path(d)
        write_json(path, data)
    print(f"フックを置きました: {path}")
    return EXIT_OK


# ---------------------------------------------------------------- harvest


def cmd_harvest(a) -> int:
    ts = a.now if a.now is not None else now_ts()
    try:
        d = Path(a.dir).resolve()
        work_path(d)
        now = dt.datetime.fromtimestamp(ts).astimezone()
        if not isinstance(read_json(d / "state.json", None), dict):
            raise discovery.DiscoveryConfigError(f"有効な state.json がありません: {d}")
        config = discovery.parse_policy(read_json(d / "policy.json", {}), d)
    except (OSError, ValueError, OverflowError, RuntimeError) as e:
        print(f"harvest: {e}", file=sys.stderr)
        return EXIT_USAGE
    if not config["enabled"]:
        return EXIT_OK

    observed, candidates, events = discovery.harvest_sources(config, now)
    failed = any(event["result"] == "failed" for event in events)
    for event in events:
        if event["result"] == "failed":
            print(f"harvest: {event['collector']} {event['root']} — {event['why']}", file=sys.stderr)
    try:
        discovery.index_dir(d)
        with locked(d):
            try:
                current = discovery.parse_policy(read_json(d / "policy.json", {}), d)
            except (OSError, ValueError, RuntimeError) as e:
                print(f"harvest: {e}", file=sys.stderr)
                return EXIT_USAGE
            if current != config:
                print("harvest: 走査中に Discovery の設定が変わりました", file=sys.stderr)
                return 1
            for event in events:
                try:
                    if (d / "journal.jsonl").is_symlink():
                        raise ValueError("journal.jsonl はシンボリックリンクです")
                    append_journal(d, {"kind": "harvest", **event}, ts)
                except (OSError, ValueError) as e:
                    print(f"harvest: journal に記録できません: {e}", file=sys.stderr)
                    failed = True
            try:
                discovery.merge_index(d, observed, candidates, now)
            except (OSError, ValueError, TypeError, RuntimeError) as e:
                print(f"harvest: 索引を保存できません: {e}", file=sys.stderr)
                failed = True
    except (OSError, ValueError, RuntimeError) as e:
        print(f"harvest: {e}", file=sys.stderr)
        return 1
    return int(failed)


# ---------------------------------------------------------------- proposals


PROPOSAL_STATUSES = ("pending", "approved", "rejected")


def read_proposal(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(f"提案ファイルにシンボリックリンクは使えません: {path}")
    row = read_json(path, None)
    if not isinstance(row, dict) or row.get("id") != path.stem or row.get("status") not in PROPOSAL_STATUSES:
        raise ValueError(f"提案レコードの形式が不正です: {path}")
    return row


def list_proposals(folder: Path, status: str, as_json: bool) -> None:
    try:
        paths = list(folder.iterdir())
    except FileNotFoundError:
        paths = []
    rows = [read_proposal(path) for path in paths if path.suffix == ".json"]
    rows = [row for row in rows if status == "all" or row["status"] == status]
    rows.sort(key=lambda row: row["id"])
    rows.sort(key=lambda row: dt.datetime.fromisoformat(row["received_at"]), reverse=True)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    columns = ("id", "status", "size", "risk", "suggested_route", "title")
    table = [("ID", "状態", "規模", "リスク", "ルート", "題名")]
    table.extend(tuple(row[key] for key in columns) for row in rows)
    widths = [max(len(value) for value in col) for col in zip(*table)]
    for line in table:
        print("  ".join(value.ljust(width) for value, width in zip(line[:-1], widths)) + "  " + line[-1])


def show_proposal(row: dict) -> None:
    print(f'{row["id"]} {row["status"]}  {row["title"]}')
    print(f'根拠: {row["why"]}')
    print(f'規模: {row["size"]}  リスク: {row["risk"]}  推奨ルート: {row["suggested_route"]}')
    print("出典:")
    for source in row["evidence"]:
        print(f'  {source["source_path"]}: {", ".join(source["locations"])}')
    if row.get("decision"):
        decision = row["decision"]
        print(f'判断: {decision["at"]}  {decision["by"]}  {decision["reason"]}')
    target = row.get("execution_ref") or row.get("registration") or row.get("append_target")
    if target:
        print(f"登録先: {json.dumps(target, ensure_ascii=False)}")
    progress = {"pending": "承認待ち", "approved": "登録待ち", "rejected": "却下"}
    print("処理状況: " + ("登録済み" if row.get("execution_ref") else progress[row["status"]]))
    if row.get("registration_reason"):
        print(f'登録待ちの理由: {row["registration_reason"]}')


def cmd_proposals(a) -> int:
    try:
        d = Path(a.dir).resolve()
        work_path(d)
        if not a.dir.strip() or not isinstance(read_json(d / "state.json", None), dict):
            raise ValueError(f"有効な state.json がありません: {d}")
        desired = {"approve": "approved", "reject": "rejected"}.get(a.operation)
        if desired and not a.reason.strip():
            raise ValueError("--reason に空白以外の文字を指定してください")
    except (OSError, ValueError, RuntimeError) as e:
        print(f"proposals: {e}", file=sys.stderr)
        return EXIT_USAGE

    try:
        discovery.index_dir(d)
        with locked(d) if desired else contextlib.nullcontext():
            folder = discovery.index_dir(d) / "proposals"
            if folder.is_symlink():
                raise ValueError("提案キューにシンボリックリンクは使えません")
            if a.operation == "list":
                list_proposals(folder, a.status, a.json)
                return EXIT_OK
            if not re.fullmatch(r"p[a-zA-Z0-9]+", a.id):
                raise ValueError(f"提案 ID がありません: {a.id}")
            path = folder / f"{a.id}.json"
            if not path.exists() and not path.is_symlink():
                raise ValueError(f"提案 ID がありません: {a.id}")
            row = read_proposal(path)
            if a.operation == "show":
                if a.json:
                    print(json.dumps(row, ensure_ascii=False, indent=2))
                else:
                    show_proposal(row)
            elif row["status"] == desired:
                decision = row["decision"]
                print(f'{a.id} {desired}（{decision["at"]} {decision["by"]}: {decision["reason"]}）')
            elif row["status"] == "pending":
                if path.with_name(path.name + ".tmp").is_symlink():
                    raise ValueError(f"一時ファイルにシンボリックリンクは使えません: {path}")
                row.update(status=desired, decision={"at": iso(now_ts()), "by": getpass.getuser(),
                                                     "reason": a.reason.strip()})
                write_json(path, row)
                print(f"{a.id} {desired}（次の act で{'登録' if desired == 'approved' else '却下'}を確認）")
            else:
                raise ValueError(f'{a.id} は {row["status"]} として記録済みです')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
        print(f"proposals: {e}", file=sys.stderr)
        return 1
    return EXIT_OK


# ---------------------------------------------------------------- status


def cmd_review(a) -> int:
    d = Path(a.dir)
    ts = a.now if a.now is not None else now_ts()
    with locked(d):
        policy = load_policy(d)
        if not routines.enabled(policy):
            print("業務の見直しには policy.json の routines.skills_dirs の設定が必要です", file=sys.stderr)
            return EXIT_USAGE
        state = load_state(d)
        findings = routines.review_routines(d, state, policy, ts)["findings"]
        if any(f["action"] == "queued" for f in findings):
            save_state(d, state)
    applied, queued, needs_review = (
        sum(f["action"] == action for f in findings) for action in ("applied", "queued", "needs_review")
    )
    print(f"業務の見直し: 整理・安定 {applied} 件、改善の予定 {queued} 件、要見直し {needs_review} 件")
    return EXIT_OK


def cmd_status(a) -> int:
    d = Path(a.dir)
    state = load_state(d)
    nxt = state.get("next_wake")
    print(f"名前: {state.get('name')}")
    print(f"状態: {state.get('status')}")
    print(f"最後に起きた: {state.get('last_wake') or 'まだ'}")
    print(f"次に起きる: {iso(float(nxt)) if nxt is not None else '返事が来たとき'}")
    print(f"届いている出来事: {len(sorted_files(d / 'inbox', '.json'))} 件")
    print(f"取りかかり中: {'はい' if state.get('inflight') or job_busy(d) else 'いいえ'}")
    pending = [x for x in state.get("approvals") or [] if x.get("status") == "pending"]
    print(f"承認待ち: {len(pending)} 件")
    for x in pending:
        print(f"  {x['id']} {x.get('summary', '')}（{x.get('created', '')}）")
    open_qs = [q for q in state.get("questions") or [] if not q.get("answer")]
    print(f"返事待ちの質問: {len(open_qs)} 件")
    for q in open_qs:
        print(f"  {q['id']} {q.get('question', '')}")
    for line in routines.status_lines(d, state, load_policy(d)):
        print(line)
    if state.get("held"):
        print(f"静かにする時間帯に溜めたコメント: {len(state['held'])} 件")
    return EXIT_OK


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goalie.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("job", help="受け箱 runner のコマンドを実行する（job --help）")

    p = sub.add_parser("init", help="目標のフォルダを作る")
    p.add_argument("--dir", required=True, help="目標のフォルダ")
    p.add_argument("--name", help="名前（ハブの issue で【名前】と呼ぶ。既定はフォルダ名）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("event", help="出来事を inbox に置く")
    p.add_argument("--dir", help="目標のフォルダ")
    p.add_argument("--root", help="目標のフォルダを並べた親フォルダ（--change で【名前】から探す）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--change", help="ハブの issue のコメントの変化（JSON）")
    g.add_argument("--text", help="出来事の本文")
    g.add_argument("--file", help="出来事の本文のファイル（UTF-8）")
    p.add_argument("--source", default="manual", help="出来事の出どころ（--text / --file のとき）")
    p.set_defaults(func=cmd_event)

    for name, func, help_text in (
        ("tick", cmd_tick, "起こすかを決め、起こすなら依頼文を書く"),
        ("act", cmd_act, "outbox の提案を片付け、承認されたものを実行する"),
        ("review", cmd_review, "業務の実行記録を見直す"),
        ("harvest", cmd_harvest, "許可されたソースから候補を集める"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dir", required=True, help="目標のフォルダ")
        p.add_argument("--now", type=float, help=argparse.SUPPRESS)
        p.set_defaults(func=func)

    p = sub.add_parser("hook", help="Copilot の preToolUse から呼ばれる（範囲の外のツールを止める）")
    p.add_argument("--dir", help="目標のフォルダ（既定はフックの cwd から探す）")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("hooks", help="フックの設定を置く")
    p.add_argument("--dir", help="目標のフォルダ（.github/hooks/ に置く）")
    p.add_argument("--plugin", help="Copilot アプリのプラグインとして置くフォルダ")
    p.add_argument("--python", help="フックを動かす Python（既定はこのコマンドを動かした Python）")
    p.set_defaults(func=cmd_hooks)

    p = sub.add_parser("status", help="いまの状態を出す")
    p.add_argument("--dir", required=True, help="目標のフォルダ")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("proposals", help="Discovery の提案を見て採否を記録する")
    proposals = p.add_subparsers(dest="operation", required=True)
    for operation in ("list", "show", "approve", "reject"):
        p = proposals.add_parser(operation)
        p.add_argument("--dir", required=True, help="目標のフォルダ")
        if operation != "list":
            p.add_argument("id", help="提案 ID")
        if operation in ("list", "show"):
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--reason", required=True, help="判断の理由")
        if operation == "list":
            p.add_argument("--status", choices=(*PROPOSAL_STATUSES, "all"), default="pending")
        p.set_defaults(func=cmd_proposals)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "job":
        import runner

        return runner.main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.command == "event" and args.change is not None and not (args.dir or args.root):
        print("--change では --dir か --root を指定してください", file=sys.stderr)
        return EXIT_USAGE
    try:
        return args.func(args)
    except discovery.WorkDirMigrationError as e:
        print(e, file=sys.stderr)
        return EXIT_USAGE
    except Busy:
        print("ほかの処理が state.json を使っています。少し待ってやり直してください", file=sys.stderr)
        return EXIT_BUSY


if __name__ == "__main__":
    sys.exit(main())
