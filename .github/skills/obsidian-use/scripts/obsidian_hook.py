#!/usr/bin/env python3
"""Obsidian の wiki にメモを集めるための、セッションのフック（obsidian-use）。

設計: docs/designs/obsidian-wiki-design.md §5

  python obsidian_hook.py <start|prompt|tool|stop> --client copilot|kiro-v2|kiro-v3|claude [--failed]
      stdin にフックの JSON を受け、クライアントごとの形で出力する。何も無ければ何も出さない。

moltbook-use のフック（moltbook_hook.py）と同じ作り。違いは、書き先が自分のボルトであることと、
プロジェクト・環境に固有のメモも残すこと（他の人に見せないので、パスやリポジトリ名を書いてよい）。

- 控える: ツールの失敗、失敗からの回復、言い直し、「メモして」の依頼、まとまった作業（ツールを一定回数使った）。LLM は使わない
- 開始: このプロジェクト・この環境の既存のメモの題を数行だけ足す（次の作業で読み直せるように）
- 終わり: 控えた出来事があれば、エージェントに振り返りの 1 ターンを足させ（OBSIDIAN NOTE）、vault.py memo で書かせる

フックは会話を止めてはいけないので、どんな失敗でも何も出さずに終了コード 0 で終える。
Obsidian（CLI）も呼ばない。読むのはボルトのフォルダの一覧だけ。
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import vault  # noqa: E402

RULES_FILE = SCRIPTS.parent / "references" / "memo_rules.json"
DEFAULT_RULES = {
    "correction": ["違う", "そうじゃな", "ではなく", "じゃなくて", "やり直して", "間違って", "instead"],
    "remember": ["メモして", "覚えておいて", "残しておいて", "obsidian に", "ノートに"],
    "exclude_cwd": [], "work_tools": 15, "max_events": 20, "max_retro_prompts": 3, "max_notes": 5,
    "start_notes": 5, "session_ttl_days": 7,
}
CLIENTS = ("copilot", "kiro-v2", "kiro-v3", "claude")
DETAIL_MAX = 160


@functools.lru_cache(maxsize=1)
def rules() -> dict:
    try:
        return {**DEFAULT_RULES, **json.loads(RULES_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_RULES)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


_ERROR_WORDS = re.compile(r"error|exception|fatal|failed|denied|not found|エラー|失敗", re.I)


def error_line(text: str) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    for ln in lines:
        if _ERROR_WORDS.search(ln) and not ln.lower().startswith("traceback"):
            return ln[:DETAIL_MAX]
    return (lines[-1] if lines else "")[:DETAIL_MAX]


# --- セッションの控え -------------------------------------------------------------

def session_id(payload: dict) -> str:
    sid = str(payload.get("sessionId") or payload.get("session_id") or os.environ.get("KIRO_SESSION_ID") or "unknown")
    return re.sub(r"[^0-9A-Za-z_.-]", "_", sid)[:80]


def load_session(sid: str, client: str, cwd: str) -> dict:
    sess = vault._read_json(vault.session_file(sid), None)
    if not isinstance(sess, dict):
        sess = {"client": client, "cwd": cwd, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "events": [],
                "dropped": 0, "tools": 0, "work_noted": False, "retro_at": 0, "retro_prompts": 0, "notes": 0,
                "pending_retro": False, "failed": {}, "written": []}
    if client and not sess.get("client"):
        sess["client"] = client
    if cwd and not sess.get("cwd"):
        sess["cwd"] = cwd
    return sess


def save_session(sid: str, sess: dict) -> None:
    vault._write_json(vault.session_file(sid), sess)


def add_event(sess: dict, what: str, detail: str = "") -> None:
    if len(sess["events"]) >= rules()["max_events"]:
        sess["dropped"] = sess.get("dropped", 0) + 1
        return
    detail = " ".join(str(detail).split())
    sess["events"].append({"at": time.strftime("%H:%M"), "what": what,
                           "detail": detail[:DETAIL_MAX] + ("…" if len(detail) > DETAIL_MAX else "")})


def new_events(sess: dict) -> list:
    return sess["events"][sess.get("retro_at", 0):]


def ignored(cwd: str) -> bool:
    if not cwd:
        return False
    try:
        if (Path(cwd) / ".obsidian-memo-ignore").exists():
            return True
    except OSError:
        return False
    norm = cwd.replace("\\", "/")
    return any(norm.startswith(str(p).replace("\\", "/")) for p in rules().get("exclude_cwd", []))


def client_label(client: str) -> str:
    return {"copilot": "Copilot", "kiro-v2": "kiro-cli", "kiro-v3": "kiro-cli v3", "claude": "Claude Code"}.get(client, client)


# --- 文面 ----------------------------------------------------------------------

def vault_cmd() -> str:
    return f'"{sys.executable}" "{SCRIPTS / "vault.py"}"'


def _note_lines(items: list) -> list[str]:
    out = []
    for it in items:
        name = f"（--name {it['name']}）" if it.get("name") else ""
        out.append(f"- [[{it['title']}]]{name}" + (f" — {it['line'][:80]}" if it.get("line") else ""))
    return out


def start_text(cfg: dict, sess: dict) -> str:
    cwd = sess.get("cwd", "")
    n = rules()["start_notes"]
    proj = vault.list_notes(vault.scope_dir(cfg, "project", cwd), n)
    env = vault.list_notes(vault.scope_dir(cfg, "env", cwd), max(n // 2, 1))
    lines = [f"[Obsidian] コマンドは {vault_cmd()}（以下 vault.py）。分からないことは答える前に "
             "`vault.py search 語` で wiki を 1 回探す。"]
    if proj:
        lines += [f"[Obsidian] このプロジェクト（{vault.project_name(cwd)}）のメモ（中身は Obsidian の "
                  f"{vault.rel(cfg, vault.scope_dir(cfg, 'project', cwd))}/ にある）:", *_note_lines(proj)]
    if env:
        lines += [f"[Obsidian] この環境（{vault.env_name()}）のメモ:", *_note_lines(env)]
    return "\n".join(lines)


def retro_prompt(cfg: dict, sid: str, sess: dict, *, prefix: str = "") -> str:
    cwd = sess.get("cwd", "")
    events = new_events(sess)
    existing = vault.list_notes(vault.scope_dir(cfg, "project", cwd), 8) + \
        vault.list_notes(vault.scope_dir(cfg, "env", cwd), 4)
    left = max(rules()["max_notes"] - sess.get("notes", 0), 0)
    lines = [prefix + "OBSIDIAN NOTE",
             "利用者に訊かずに、この回のことで、次に同じ作業をするときに役立つことがあれば Obsidian に 1 件ずつ残す。"
             "無ければ何もせず元の作業を続ける。", "", "## この回の出来事",
             *[f"- {e['at']} {e['what']}" + (f": {e['detail']}" if e["detail"] else "") for e in events]]
    if sess.get("dropped"):
        lines.append(f"- ほか {sess['dropped']} 件")
    lines += ["", f"## 既にあるメモ（プロジェクト {vault.project_name(cwd)} / 環境 {vault.env_name()}。"
              "同じことなら同じ --name で書くと追記になる）", *(_note_lines(existing) or ["- なし"]), "",
              "## 書き方",
              f"{vault_cmd()} memo --session {sid} --scope project|env|knowledge --kind bug|tip|fact|decision "
              '--name 英語の短い名前 --title "題を 1 行" --detail "回避・やること・事実・決めたことを 1 行" '
              '[--reason "原因・理由・根拠を 1 行"] --keywords "語,識別子,…" [--tools "関係するツール,…"]',
              "- project: このリポジトリだけで通じること（ビルド・テストの手順、置き場所、決めたこと、詰まりと回避）。"
              "パス・ブランチ名・リポジトリ名を書いてよい。",
              "- env: この PC・OS・ツールの版だけで起きること。",
              "- knowledge: どこでも通じる知識。記事や資料の中身の整理は ingest（SKILL.md）でする。",
              f"- トークン・パスワード・鍵は書かない。経緯は書かない。この回で {left} 件まで。",
              "- 終わったら、利用者への返事は「Obsidian: <書いたパス>」の 1 行だけにする（書かなかったら何も言わない）。"]
    return "\n".join(lines)


def mark_retro(sess: dict) -> None:
    sess["retro_at"] = len(sess["events"])
    sess["retro_prompts"] = sess.get("retro_prompts", 0) + 1
    sess["pending_retro"] = False


def retro_due(sess: dict) -> bool:
    r = rules()
    return bool(new_events(sess)) and sess.get("retro_prompts", 0) < r["max_retro_prompts"] \
        and sess.get("notes", 0) < r["max_notes"]


# --- 入口 ------------------------------------------------------------------------

def _tool_fields(payload: dict) -> tuple[str, str, dict]:
    name = str(payload.get("toolName") or payload.get("tool_name") or "")
    args = payload.get("toolArgs", payload.get("tool_input", ""))
    args_text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    resp = payload.get("tool_response") or payload.get("toolResult") or {}
    return name, args_text, resp if isinstance(resp, dict) else {"result": resp}


def _kiro_failure(resp: dict) -> str | None:
    if resp.get("success") is False:
        err = resp.get("error", "")
        return err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
    for block in resp.get("result", []) if isinstance(resp.get("result"), list) else []:
        if isinstance(block, dict) and str(block.get("exit_status", "0")) not in ("0", ""):
            return str(block.get("stderr") or block.get("stdout") or f"exit {block['exit_status']}")
    return None


_OWN = re.compile(r"vault\.py[\\\"']*\s+(?:memo|search|notes|note|ingested|links|log|lint|config)\b")


def on_prompt(cfg: dict, sid: str, sess: dict, client: str, payload: dict) -> str:
    text = normalize(str(payload.get("prompt") or ""))
    r = rules()
    if any(w.lower() in text for w in r["remember"]):
        add_event(sess, "メモの依頼", str(payload.get("prompt") or "")[:DETAIL_MAX])
    elif any(w.lower() in text for w in r["correction"]):
        add_event(sess, "言い直し")
    if client == "kiro-v2" and sess.get("pending_retro") and retro_due(sess):
        out = retro_prompt(cfg, sid, sess, prefix="（返事の前に、前のターンについて）")
        mark_retro(sess)
        return out
    return ""


def on_tool(sess: dict, client: str, payload: dict, failed_flag: bool) -> None:
    name, args_text, resp = _tool_fields(payload)
    if _OWN.search(args_text):
        return   # 自分のメモ・検索のコマンドは出来事にしない
    sess["tools"] = sess.get("tools", 0) + 1
    error = payload.get("error") if failed_flag else (_kiro_failure(resp) if client.startswith("kiro") else None)
    if failed_flag and error is None:
        error = "失敗"
    if error is not None:
        error = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
        add_event(sess, f"ツールの失敗: {name}", error_line(error))
        sess["failed"][name] = True
    elif sess.get("failed", {}).pop(name, None):
        add_event(sess, f"失敗からの回復: {name}")
    if not sess.get("work_noted") and sess["tools"] >= rules()["work_tools"]:
        sess["work_noted"] = True
        add_event(sess, "まとまった作業", f"ツールを {sess['tools']} 回使った")


def on_stop(cfg: dict, sid: str, sess: dict, client: str, payload: dict) -> str:
    if client == "kiro-v2":   # v2 の stop は止められない。次のプロンプトで前置きする
        if retro_due(sess):
            sess["pending_retro"] = True
        return ""
    if payload.get("stop_hook_active") or not retro_due(sess):
        return ""
    reason = retro_prompt(cfg, sid, sess)
    mark_retro(sess)
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def cleanup() -> None:
    ttl = rules()["session_ttl_days"] * 86400
    for p in (vault.state_home() / "sessions").glob("*.json"):
        try:
            if time.time() - p.stat().st_mtime > ttl:
                p.unlink()
        except OSError:
            pass


def run_hook(event: str, client: str, payload: dict, failed_flag: bool = False) -> str:
    if os.environ.get("OBSIDIAN_MEMO_OFF"):
        return ""
    cfg = vault.load_config()
    if not cfg:
        return ""   # ボルトが未設定なら何もしない
    sid = session_id(payload)
    sess = load_session(sid, client, str(payload.get("cwd") or os.getcwd()))
    if ignored(sess.get("cwd", "")):
        return ""
    out = ""
    if event == "start":
        cleanup()
        sess["env"] = f"{client_label(client)} / {vault.env_name()}"
        out = start_text(cfg, sess)
        if client == "copilot" and out:
            out = json.dumps({"additionalContext": out}, ensure_ascii=False)
    elif event == "prompt":
        out = on_prompt(cfg, sid, sess, client, payload)
    elif event == "tool":
        on_tool(sess, client, payload, failed_flag)
    elif event == "stop":
        out = on_stop(cfg, sid, sess, client, payload)
    # 振り返りのターンで vault.py memo が書いた件数を消さないよう、読み直して足し合わせる
    latest = vault._read_json(vault.session_file(sid), {})
    if isinstance(latest, dict):
        sess["notes"] = max(sess.get("notes", 0), latest.get("notes", 0))
        sess["written"] = latest.get("written", sess.get("written", []))
    save_session(sid, sess)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Obsidian の wiki にメモを集めるフック")
    ap.add_argument("event", choices=["start", "prompt", "tool", "stop", "end"])
    ap.add_argument("--client", choices=CLIENTS, default="copilot")
    ap.add_argument("--failed", action="store_true", help="ツールの失敗のフックから呼ぶとき")
    a = ap.parse_args(argv)
    if a.event == "end":
        return 0
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        out = run_hook(a.event, a.client, payload if isinstance(payload, dict) else {}, a.failed)
    except Exception:   # フックは会話を止めない
        return 0
    if out:
        sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
