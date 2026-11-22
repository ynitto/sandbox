#!/usr/bin/env python3
"""目標を 1 つ預かり、裏で進めて、変化と質問だけをハブの issue に返す（常駐型エージェントの D0）。

1 つの目標 = 1 つのフォルダ。フォルダを GitHub Copilot のデスクトップアプリのプロジェクトとして開き、
copilot-app-jobs の受け箱で依頼を渡す。起こすかどうか（tick）と、外へ作用するかどうか（act）は
このスクリプトが決め、LLM は使わない。エージェントは outbox に提案を書くだけで、外へは書かない。

    goal.md          目標と完了の条件（人が書く）
    policy.json      やってよいこと・1 日に起きてよい回数・完了の検査
    state.json       いまの状態（このスクリプトだけが書く）
    inbox/           新しい出来事（event が置く）
    outbox/          エージェントの提案（1 件 1 ファイルの JSON）
    notes/           エージェントの自分へのメモ（日ごと）
    journal.jsonl    やったこと・捨てたことの記録（追記のみ）
    .dot/            作業用（依頼文・送るコメント・取りかかり中の出来事）

コマンド:
    init    フォルダを作る
    event   出来事を inbox に置く（ハブの issue の変化 JSON、または文）
    tick    起こすかを決め、起こすなら依頼文を .dot/prompt.md に書いて理由を 1 行出す（起こさないなら何も出さない）
    act     outbox の提案を境界と照らして片付け、ハブに送るコメントがあれば、そのファイルのパスを出す
    status  いまの状態を出す

標準ライブラリだけで動く（Windows の Python 3.9 以上）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2

JOB_BOX = ".copilot-jobs"          # copilot-app-jobs の受け箱（作業フォルダ直下）
JOB_LABEL = "dot"                  # 受け箱に置く依頼の名前（copilot_app_job.py run --name）
WORK = ".dot"
INFLIGHT_STALE_SEC = 3 * 3600      # 取りかかり中のまま、これより古ければ捨ててやり直す
MAX_RETRIES = 2                    # 提案が 1 件も無かった回を、出来事を戻してやり直す回数
NOTE_LINES = 20                    # 依頼文に載せるメモの行数
QUESTION_KEEP = 5                  # 依頼文に載せる、最近の質問の数

KNOWN_ACTIONS = {
    # 作用の名前 → 必須の項目。ここに無い作用は境界に書いても通さない。
    "hub.comment": ("body",),
}
LEVELS = ("auto", "deny")

DEFAULT_POLICY = {
    "actions": {"hub.comment": "auto"},
    "wake_per_day": 24,
    "done_check": [],
}

GOAL_TEMPLATE = """# {name}

何を、いつまでに終わらせたいかを書く。

## 完了の条件

- 終わったと言える状態を、確かめられる形で書く

## 前提・使ってよいもの

- 読んでよいフォルダや資料
"""

NAME_RE = re.compile(r"^[^\s【】]{1,40}$")


# ---------------------------------------------------------------- ファイル


def now_ts() -> float:
    return time.time()


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def today(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


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
    state = read_json(d / "state.json", None)
    if state is None:
        raise SystemExit(f"目標のフォルダではありません（state.json が無い）: {d}")
    return state


def load_policy(d: Path) -> dict:
    policy = read_json(d / "policy.json", {})
    merged = dict(DEFAULT_POLICY)
    merged.update(policy if isinstance(policy, dict) else {})
    return merged


def sorted_files(folder: Path, suffix: str) -> "list[Path]":
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name.endswith(suffix))


# ---------------------------------------------------------------- init


def cmd_init(a) -> int:
    d = Path(a.dir)
    name = a.name or d.name
    if not NAME_RE.match(name):
        print(f"名前に空白と【】は使えません（40 文字まで）: {name}", file=sys.stderr)
        return EXIT_USAGE
    if (d / "state.json").exists():
        print(f"既に目標のフォルダです: {d}", file=sys.stderr)
        return EXIT_USAGE
    for sub in ("inbox", "outbox", "notes", WORK):
        (d / sub).mkdir(parents=True, exist_ok=True)
    if not (d / "goal.md").exists():
        (d / "goal.md").write_text(GOAL_TEMPLATE.format(name=name), encoding="utf-8")
    if not (d / "policy.json").exists():
        write_json(d / "policy.json", DEFAULT_POLICY)
    write_json(d / "state.json", {
        "name": name,
        "status": "active",
        "created": iso(now_ts()),
        "last_wake": None,
        "next_wake": None,
        "wakes": {},
        "inflight": None,
        "questions": [],
        "seq": 0,
    })
    print(f"作りました: {d}")
    print("goal.md に目標と完了の条件を書いてから、このフォルダを Copilot アプリのプロジェクトとして開いてください。")
    return EXIT_OK


# ---------------------------------------------------------------- event


def find_dot(root: Path, name: str) -> "Path | None":
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
        d = Path(a.dir) if a.dir else find_dot(Path(a.root), name)
        if d is None:
            return EXIT_OK                       # 知らない名前（ほかの用途のコメント）
        state = load_state(d)
        if state.get("name") != name:
            return EXIT_OK
        author = (record.get("author") or {}).get("username") or ""
        event = {"from": "hub", "author": author, "id": record.get("id"), "text": text}
    else:
        if not a.dir:
            print("--text / --file では --dir を指定してください", file=sys.stderr)
            return EXIT_USAGE
        d = Path(a.dir)
        state = load_state(d)
        if a.file:
            text = Path(a.file).read_text(encoding="utf-8")
        else:
            text = a.text or ""
        if not text.strip():
            print("出来事の本文が空です", file=sys.stderr)
            return EXIT_USAGE
        event = {"from": a.source, "text": text.strip()}
    state["seq"] = int(state.get("seq") or 0) + 1
    event["at"] = iso(ts)
    path = d / "inbox" / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(ts))}-{state['seq']:05d}.json"
    write_json(path, event)
    if state.get("status") in ("done", "review"):
        state["status"] = "active"               # 終えた目標に話しかけたら、また動かす
    write_json(d / "state.json", state)
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


def read_notes(d: Path, limit: int = NOTE_LINES) -> "list[str]":
    lines: list[str] = []
    for p in reversed(sorted_files(d / "notes", ".md")):
        part = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        lines = [f"{p.stem} {ln}" for ln in part] + lines
        if len(lines) >= limit:
            break
    return lines[-limit:]


def build_prompt(d: Path, state: dict, policy: dict, events: "list[dict]", reasons: "list[str]", ts: float) -> str:
    goal = (d / "goal.md").read_text(encoding="utf-8").strip() if (d / "goal.md").exists() else "（goal.md がありません）"
    allowed = [k for k, v in (policy.get("actions") or {}).items() if v == "auto" and k in KNOWN_ACTIONS]
    out = []
    out.append(f"あなたは目標「{state['name']}」を、何日かかけて進めるエージェントです。今回起きた理由: {', '.join(reasons)}。")
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
    out.extend(f"- {n}" for n in notes) if notes else out.append("- なし")
    qs = state.get("questions") or []
    if qs:
        out.append("")
        out.append("## 最近こちらから聞いたこと")
        out.extend(f"- {q.get('at', '')} {q.get('question', '')}" for q in qs[-QUESTION_KEEP:])
    out.append("")
    out.append("## やり方")
    out.append("1. 目標とメモと出来事を読み、今回進められることを進める。調べる・下書きを作るのはこのフォルダの中で行う。")
    out.append("2. 外へは何も書かない（メール・チャット・issue・push などは直接しない）。外へ出したいことは提案として outbox/ に書く。")
    out.append("3. 終える前に、outbox/ に JSON のファイルを 1 件以上書く（1 件 1 ファイル、名前は 01.json, 02.json …）。")
    out.append("")
    out.append("書ける提案:")
    out.append('- メモ: {"kind": "note", "body": "次に読むべきは仕様書の 3 章"}')
    if "hub.comment" in allowed:
        out.append('- 利用者への報告: {"kind": "act", "action": "hub.comment", "body": "下書きを drafts/plan.md に置きました"}')
    out.append('- 利用者への質問: {"kind": "ask", "question": "A と B のどちらで進めますか", "options": ["A", "B"]}')
    out.append('- 次に起きる時刻: {"kind": "wait", "until": "2026-11-25T09:00", "why": "先方の返事待ち"}')
    out.append('- 完了の報告: {"kind": "done", "evidence": ["drafts/plan.md に完了の条件の 3 点を書いた"]}')
    out.append("")
    out.append(f"利用者は、ハブの issue に「【{state['name']}】」で始まるコメントを書いて返事をします。返事は次の出来事として届きます。")
    out.append("wait を書かなければ、次は返事が来るまで起きません。")
    return "\n".join(out) + "\n"


def cmd_tick(a) -> int:
    d = Path(a.dir)
    ts = a.now if a.now is not None else now_ts()
    state = load_state(d)
    policy = load_policy(d)

    if job_busy(d):
        return EXIT_OK                            # 前の依頼がまだ受け箱にある
    inflight = state.get("inflight")
    if inflight:
        if ts - float(inflight.get("since") or 0) < INFLIGHT_STALE_SEC:
            return EXIT_OK                        # act がまだ片付けていない
        restore_inflight(d, state)
        append_journal(d, {"kind": "tick", "result": "stale", "why": "取りかかり中のまま古くなったので出来事を戻した"}, ts)

    reasons = wake_reasons(d, state, ts)
    if not reasons:
        write_json(d / "state.json", state)
        return EXIT_OK

    day = today(ts)
    used = int((state.get("wakes") or {}).get(day) or 0)
    limit = int(policy.get("wake_per_day") or 0)
    if limit and used >= limit:
        if state.get("budget_noted") != day:
            state["budget_noted"] = day
            append_journal(d, {"kind": "tick", "result": "budget", "why": f"今日は {limit} 回起きたので止める"}, ts)
        write_json(d / "state.json", state)
        return EXIT_OK

    held = d / WORK / "inflight"
    held.mkdir(parents=True, exist_ok=True)
    events = []
    for p in sorted_files(d / "inbox", ".json"):
        events.append(read_json(p, {}))
        os.replace(p, held / p.name)

    (d / WORK).mkdir(parents=True, exist_ok=True)
    (d / WORK / "prompt.md").write_text(build_prompt(d, state, policy, events, reasons, ts), encoding="utf-8")
    state["wakes"] = {day: used + 1}              # 今日の分だけ残す
    state["last_wake"] = iso(ts)
    if "due" in reasons:
        state["next_wake"] = None
    state["inflight"] = {"since": ts, "reasons": reasons, "tries": int(state.get("pending_tries") or 0)}
    write_json(d / "state.json", state)
    append_journal(d, {"kind": "tick", "result": "wake", "reasons": reasons, "events": len(events)}, ts)
    print("wake: " + ",".join(reasons))
    return EXIT_OK


# ---------------------------------------------------------------- act


def parse_until(value: str) -> float:
    t = dt.datetime.fromisoformat(str(value).strip())
    if t.tzinfo is None:
        t = t.astimezone()                        # タイムゾーンが無ければこの PC の時刻
    return t.timestamp()


def run_done_check(d: Path, command: "list[str]") -> "tuple[bool, str]":
    try:
        proc = subprocess.run(command, cwd=str(d), capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"検査を動かせませんでした: {e}"
    out = (proc.stdout or b"") + (proc.stderr or b"")
    text = out.decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, text[-500:]


def handle(d: Path, state: dict, policy: dict, prop: dict, ts: float, comments: "list[str]") -> dict:
    """1 件の提案を片付け、journal に残す中身を返す。"""
    kind = prop.get("kind")
    if kind == "note":
        body = str(prop.get("body") or "").strip()
        if not body:
            return {"result": "denied", "why": "body が空"}
        path = d / "notes" / f"{today(ts)}.md"
        with path.open("a", encoding="utf-8") as f:
            stamp = dt.datetime.fromtimestamp(ts).strftime("%H:%M")
            f.write(f"- {stamp} " + body.replace("\n", " ") + "\n")
        return {"result": "done"}
    if kind == "act":
        action = prop.get("action")
        if action not in KNOWN_ACTIONS:
            return {"result": "denied", "why": f"知らない作用: {action}"}
        level = (policy.get("actions") or {}).get(action, "deny")
        if level not in LEVELS or level != "auto":
            return {"result": "denied", "why": f"境界で許していない: {action}"}
        missing = [k for k in KNOWN_ACTIONS[action] if not str(prop.get(k) or "").strip()]
        if missing:
            return {"result": "denied", "why": "項目が足りない: " + ", ".join(missing)}
        if action == "hub.comment":
            comments.append(str(prop["body"]).strip())
        return {"result": "done"}
    if kind == "ask":
        question = str(prop.get("question") or "").strip()
        if not question:
            return {"result": "denied", "why": "question が空"}
        options = [str(o) for o in (prop.get("options") or []) if str(o).strip()]
        lines = ["質問: " + question]
        lines.extend(f"- {o}" for o in options)
        lines.append(f"（「【{state['name']}】」で始めて返信してください）")
        comments.append("\n".join(lines))
        qs = list(state.get("questions") or [])
        qs.append({"at": iso(ts), "question": question, "options": options})
        state["questions"] = qs[-QUESTION_KEEP:]
        return {"result": "done"}
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
            ok, out = run_done_check(d, [str(c) for c in check])
            if not ok:
                comments.append("完了と報告しましたが、完了の検査が通りませんでした。続けます。\n" + out)
                return {"result": "denied", "why": "完了の検査が通らない", "check": out}
            state["status"] = "done"
            comments.append("完了しました。\n" + "\n".join(f"- {e}" for e in evidence))
            return {"result": "done"}
        state["status"] = "review"
        comments.append("完了したと報告しています。確かめてください（続けるなら「【"
                        + state["name"] + "】」で始めて返信）。\n" + "\n".join(f"- {e}" for e in evidence))
        return {"result": "done", "status": "review"}
    return {"result": "denied", "why": f"知らない提案: {kind}"}


def cmd_act(a) -> int:
    d = Path(a.dir)
    ts = a.now if a.now is not None else now_ts()
    state = load_state(d)
    policy = load_policy(d)
    comment_file = d / WORK / "hub-comment.txt"
    if comment_file.exists():
        comment_file.unlink()

    if job_busy(d):
        return EXIT_OK                            # まだ終わっていない。次の act で片付ける

    proposals = sorted_files(d / "outbox", ".json")
    if not proposals:
        inflight = state.get("inflight")
        if inflight:
            tries = int(inflight.get("tries") or 0) + 1
            moved = restore_inflight(d, state)
            if tries > MAX_RETRIES:
                dropped = d / WORK / "dropped"
                dropped.mkdir(parents=True, exist_ok=True)
                for p in moved:
                    os.replace(p, dropped / p.name)
                state.pop("pending_tries", None)
                append_journal(d, {"kind": "act", "result": "dropped", "why": "提案が無い回が続いたので出来事を捨てた", "events": len(moved)}, ts)
            else:
                state["pending_tries"] = tries
                reasons = inflight.get("reasons") or []
                if "start" in reasons:
                    state["last_wake"] = None     # 始めの回をやり直す
                if "due" in reasons and state.get("next_wake") is None:
                    state["next_wake"] = ts       # 期限の回をやり直す
                append_journal(d, {"kind": "act", "result": "retry", "why": "提案が 1 件も無かった", "tries": tries}, ts)
            write_json(d / "state.json", state)
        return EXIT_OK

    comments: list[str] = []
    done_dir = d / WORK / "outbox-done"
    done_dir.mkdir(parents=True, exist_ok=True)
    for p in proposals:
        try:
            prop = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            entry = {"result": "denied", "why": f"JSON が読めない: {e}"}
            prop = {}
        else:
            entry = handle(d, state, policy, prop, ts, comments) if isinstance(prop, dict) else {"result": "denied", "why": "JSON がオブジェクトでない"}
        append_journal(d, {"kind": "proposal", "file": p.name, "proposal": prop if isinstance(prop, dict) else None, **entry}, ts)
        os.replace(p, done_dir / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(ts))}-{p.name}")

    held = d / WORK / "inflight"
    for p in sorted_files(held, ".json"):
        p.unlink()
    state["inflight"] = None
    state.pop("pending_tries", None)
    write_json(d / "state.json", state)

    if comments:
        text = f"【{state['name']}より】\n\n" + "\n\n".join(comments) + "\n"
        comment_file.write_text(text, encoding="utf-8")
        print(str(comment_file))
    return EXIT_OK


# ---------------------------------------------------------------- status


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
    return EXIT_OK


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dot.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

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
        ("act", cmd_act, "outbox の提案を片付ける"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dir", required=True, help="目標のフォルダ")
        p.add_argument("--now", type=float, help=argparse.SUPPRESS)
        p.set_defaults(func=func)

    p = sub.add_parser("status", help="いまの状態を出す")
    p.add_argument("--dir", required=True, help="目標のフォルダ")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "event" and args.change is not None and not (args.dir or args.root):
        print("--change では --dir か --root を指定してください", file=sys.stderr)
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
