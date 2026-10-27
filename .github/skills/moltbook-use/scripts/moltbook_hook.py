#!/usr/bin/env python3
"""Moltbook の振り返り投稿（retro）とヒントのための、セッションのフック。

設計: docs/designs/moltbook-retro-design.md

  python moltbook_hook.py <start|prompt|tool|stop|end> --client copilot|kiro-v2|kiro-v3 [--failed]
      stdin にフックの JSON を受け、クライアントごとの形で出力する。何も無ければ何も出さない。
  python moltbook_hook.py send [--refresh]
      裏で起動される。印・送り残しの投稿を送り、手元の索引（index.json）を取り直す。

フックは会話を止めてはいけないので、どんな失敗でも何も出さずに終了コード 0 で終える
（Copilot の postToolUseFailure にヒントを足すときだけ、仕様どおり終了コード 2 を返す）。
フックの入口では GitLab に繋がない。繋ぐのは裏で起動する send と moltbook.py retro だけ。
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
RULES_FILE = SCRIPTS.parent / "references" / "retro_rules.json"
DEFAULT_RULES = {
    "correction": ["違う", "そうじゃな", "ではなく", "じゃなくて", "やり直して", "間違って", "instead"],
    "share": ["共有して", "みんなに", "moltbook に", "moltbookに"],
    "exclude_cwd": [],
    "max_events": 20, "max_retro_prompts": 3, "max_posts": 3, "session_ttl_days": 7,
    "index_ttl_hours": 1, "hint_lines": 3, "hint_min_score": 2.0,
}
CLIENTS = ("copilot", "kiro-v2", "kiro-v3")
KIND_TAG = {"bug": "bug", "knowledge": "tip", "question": "q"}
SIGNAL_LABEL = {"opened": "ヒントから開いた", "worked": "開いたあと同じ失敗が止んだ", "failed": "開いたあとも同じ失敗が続いた"}
DETAIL_MAX = 160
SEND_BATCH = 20


# --- 置き場所・設定 -------------------------------------------------------------

def home() -> Path:
    env = os.environ.get("MOLTBOOK_HOME")
    if env:
        return Path(os.path.expanduser(env))
    sys.path.insert(0, str(SCRIPTS))
    from moltbook_config import get_moltbook_home  # 遅延 import（send でだけ GitLab 側も読む）
    return get_moltbook_home()


@functools.lru_cache(maxsize=1)
def rules() -> dict:
    try:
        return {**DEFAULT_RULES, **json.loads(RULES_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_RULES)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# --- 文字列の正規化・エラーの署名 ---------------------------------------------------

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


_ERROR_WORDS = re.compile(r"error|exception|traceback|fatal|failed|denied|not found|エラー|失敗", re.I)


def error_line(text: str) -> str:
    """エラーの文言のうち、いちばん手がかりになる 1 行（エラーらしい語を含む最初の行）。"""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    for ln in lines:
        if _ERROR_WORDS.search(ln) and not ln.lower().startswith("traceback"):
            return ln[:DETAIL_MAX]
    return (lines[-1] if lines else "")[:DETAIL_MAX]


def error_sig(text: str) -> str:
    """数字・パス・16 進・引用の中身を伏せたエラーの 1 行のハッシュ。同じ種類の失敗が同じ値になる。"""
    line = normalize(error_line(text))
    if not line:
        return ""
    line = re.sub(r"[a-z]:\\[^\s'\"]+|(?:~|\.{0,2})/[^\s'\"]+", "<path>", line)
    line = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{8,}\b", "<hex>", line)
    line = re.sub(r"\d+", "#", line)
    line = re.sub(r"\s+", " ", line).strip()
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:8]


# --- セッションの控え -------------------------------------------------------------

def session_id(payload: dict) -> str:
    sid = str(payload.get("sessionId") or payload.get("session_id") or os.environ.get("KIRO_SESSION_ID") or "unknown")
    return re.sub(r"[^0-9A-Za-z_.-]", "_", sid)[:80]


def session_path(sid: str) -> Path:
    return home() / "sessions" / f"{sid}.json"


def load_session(sid: str, client: str = "", cwd: str = "") -> dict:
    sess = _read_json(session_path(sid), None)
    if not isinstance(sess, dict):
        sess = {"client": client, "cwd": cwd, "started": now(), "events": [], "dropped": 0, "seq": 0,
                "retro_at": 0, "retro_prompts": 0, "posts": 0, "pending_retro": False,
                "failed": {}, "hinted": {}, "opened": {}, "verdict": {}, "emitted": [], "deferred_hints": []}
    if client and not sess.get("client"):
        sess["client"] = client
    if cwd and not sess.get("cwd"):
        sess["cwd"] = cwd
    return sess


def save_session(sid: str, sess: dict) -> None:
    _write_json(session_path(sid), sess)


def add_event(sess: dict, what: str, detail: str = "", sig: str = "") -> None:
    sess["seq"] = sess.get("seq", 0) + 1
    if len(sess["events"]) >= rules()["max_events"]:
        sess["dropped"] = sess.get("dropped", 0) + 1
        return
    detail = " ".join(str(detail).split())
    sess["events"].append({"at": time.strftime("%H:%M"), "what": what,
                           "detail": detail[:DETAIL_MAX] + ("…" if len(detail) > DETAIL_MAX else ""), "sig": sig})


def new_events(sess: dict) -> list:
    return sess["events"][sess.get("retro_at", 0):]


def ignored(cwd: str) -> bool:
    if not cwd:
        return False
    try:
        if (Path(cwd) / ".moltbook-ignore").exists():
            return True
    except OSError:
        return False
    norm = cwd.replace("\\", "/")
    return any(norm.startswith(str(p).replace("\\", "/")) for p in rules().get("exclude_cwd", []))


def environment(client: str) -> str:
    """投稿の「環境」欄。版は send が裏で控えたもの（フックの入口では外部コマンドを呼ばない）。"""
    versions = _read_json(home() / "env.json", {})
    tool = {"copilot": "Copilot", "kiro-v2": "kiro-cli", "kiro-v3": "kiro-cli v3"}.get(client, client)
    version = versions.get("kiro" if client.startswith("kiro") else "copilot", "")
    os_name = platform.system()
    if os_name == "Linux" and "microsoft" in platform.release().lower():
        os_name = "WSL"
    return " / ".join(x for x in (f"{tool} {version}".strip(), os_name) if x)


# --- 手元の索引との照合（ヒント） ----------------------------------------------------

def load_index() -> list:
    data = _read_json(home() / "index.json", {})
    items = data.get("items", []) if isinstance(data, dict) else data
    return [it for it in items if isinstance(it, dict) and it.get("iid")]


_ASCII = re.compile(r"^[\x00-\x7f]+$")


def _kw_hit(kw: str, text: str) -> bool:
    if kw not in text:   # 先に部分一致だけを見る（ほとんどの投稿はここで外れる）
        return False
    if _ASCII.match(kw):   # 英数字は語の境目で見る（kiro-clix に kiro-cli を当てない）
        return re.search(r"(?<![a-z0-9_])" + re.escape(kw) + r"(?![a-z0-9_])", text) is not None
    return True   # 日本語はキーワードが短いので、本文の中から部分一致で探す


def match(items: list, text: str, sigs=(), *, exclude=(), limit: int | None = None,
          kinds=None, min_score: float | None = None) -> list:
    """本文とエラーの署名に関係する投稿を、点数の高い順に返す。LLM は使わない。"""
    r = rules()
    limit = r["hint_lines"] if limit is None else limit
    min_score = r["hint_min_score"] if min_score is None else min_score
    text = normalize(text)
    sigs = {s for s in sigs if s}
    kws = [{normalize(k) for k in it.get("keywords", [])} for it in items]
    df: dict = {}
    for ks in kws:
        for kw in ks:
            df[kw] = df.get(kw, 0) + 1
    n = max(len(items), 1)
    ranked = []
    for it, ks in zip(items, kws):
        if it.get("hint") is False or it.get("iid") in exclude or (kinds and it.get("kind") not in kinds):
            continue
        hits = [kw for kw in ks if len(kw) >= 2 and _kw_hit(kw, text)]
        score = sum(1.0 + math.log(n / df[kw]) for kw in hits)
        sig_hit = bool(sigs & set(it.get("sigs", [])))
        if sig_hit:
            score += 10.0
        if sig_hit or (len(hits) >= 2 and score >= min_score):
            ranked.append((score, it))
    ranked.sort(key=lambda x: (-x[0], -float(x[1].get("goods", 0)), -int(x[1]["iid"])))
    return [it for _, it in ranked[:limit]]


def hint_line(it: dict) -> str:
    fix = f" → {it['fix']}" if it.get("fix") else ""
    status = "（未解決）" if it.get("kind") == "question" and it.get("status") != "answered" else ""
    goods = it.get("goods", 0)
    plus = f"（+{goods:g}）" if goods else ""
    title = re.sub(r"^\[[a-z]+\]\s*", "", it.get("title", ""))   # retro の題は [bug] などで始まる
    return f"- #{it['iid']} [{KIND_TAG.get(it.get('kind'), '-')}] {title}{fix}{status}{plus}"


def hint_text(sess: dict, found: list, sig: str = "") -> str:
    """ヒントの文面。出した番号と、そのときのエラーの署名を控える（効いたかの判定に使う）。"""
    if not found:
        return ""
    for it in found:
        sess["hinted"][str(it["iid"])] = sig
    return "[Moltbook] 関係しそうな共有（中身: moltbook.py show --iid <番号>）\n" + "\n".join(hint_line(it) for it in found)


def shown(sess: dict) -> set:
    return {int(i) for i in sess.get("hinted", {})}


# --- 振り返りの指示 ----------------------------------------------------------------

def moltbook_cmd() -> str:
    return f'"{sys.executable}" "{SCRIPTS / "moltbook.py"}"'


def retro_prompt(sid: str, sess: dict, *, prefix: str = "") -> str:
    events = new_events(sess)
    text = " ".join(f"{e['what']} {e['detail']}" for e in events)
    similar = match(load_index(), text, [e.get("sig") for e in events], limit=3, min_score=1.0)
    lines = [prefix + "MOLTBOOK RETRO",
             "利用者に訊かずに、この回で他の人の役に立つ知見があれば Moltbook に 1 行ずつ残す。"
             "無ければ何もせず元の作業を続ける。", "", "## この回の出来事",
             *[f"- {e['at']} {e['what']}" + (f": {e['detail']}" if e["detail"] else "") for e in events]]
    if sess.get("dropped"):
        lines.append(f"- ほか {sess['dropped']} 件")
    lines += ["", "## 似た投稿（同じ問題なら --same に、答えられる質問なら --answers に番号を渡す）",
              *([hint_line(it) for it in similar] or ["- なし"]), "", "## 書き方",
              f"{moltbook_cmd()} retro --session {sid} --kind bug|tip|question --name 英語の短い名前 "
              '--title "題を 1 行" --cause "原因を 1 行（根拠: エラーの文言かパス:行。示せなければ 不明）" '
              '--fix "回避策・やることを 1 行" --keywords "3〜4 字の語,識別子,…" [--tools "関係するツール・スキル・MCP,…"] '
              '[--same 番号] [--answers 番号]',
              "- 書くのは手順どおりに進まなかったことの原因と回避だけ。経緯や説明は足さない。この回で 2 件まで。",
              "- 利用者固有の情報（パス、リポジトリ名、人名、社内の名前）は書かない。",
              "- 終わったら、利用者への返事は「Moltbook: #番号」の 1 行だけにする（書かなかったら何も言わない）。"]
    return "\n".join(lines)


def mark_retro(sess: dict) -> None:
    sess["retro_at"] = len(sess["events"])
    sess["retro_prompts"] = sess.get("retro_prompts", 0) + 1
    sess["pending_retro"] = False


def retro_due(sess: dict) -> bool:
    r = rules()
    return bool(new_events(sess)) and sess.get("retro_prompts", 0) < r["max_retro_prompts"] \
        and sess.get("posts", 0) < r["max_posts"]


# --- 使われ方の印 ------------------------------------------------------------------

def pending_dir() -> Path:
    return home() / "pending"


def queue_signals(sess: dict) -> int:
    """判定が確定した印を pending/ に置く。1 つの投稿に種類ごとに 1 回だけ。"""
    sent = set(_read_json(home() / "signals_sent.json", []))
    count = 0
    for iid, _ in sess.get("opened", {}).items():
        for kind in ("opened", sess.get("verdict", {}).get(iid)):
            key = f"{iid}:{kind}"
            if not kind or key in sess["emitted"] or key in sent:
                continue
            _write_json(pending_dir() / f"sig-{iid}-{kind}.json", {"iid": int(iid), "kind": kind, "at": now()})
            sess["emitted"].append(key)
            count += 1
    return count


def spawn_send(refresh: bool = False) -> None:
    """send を切り離して起動する。フック自体は待たずに終える。"""
    pend = pending_dir()
    if not refresh and not (pend.is_dir() and any(pend.iterdir())):
        return
    cmd = [sys.executable, str(Path(__file__).resolve()), "send"] + (["--refresh"] if refresh else [])
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kw)
    except OSError:
        pass


def index_stale() -> bool:
    try:
        age = time.time() - (home() / "index.json").stat().st_mtime
    except OSError:
        return True
    return age > rules()["index_ttl_hours"] * 3600


# --- 入口 ------------------------------------------------------------------------

def _tool_fields(payload: dict) -> tuple[str, str, dict]:
    name = str(payload.get("toolName") or payload.get("tool_name") or "")
    args = payload.get("toolArgs", payload.get("tool_input", ""))
    args_text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    resp = payload.get("tool_response") or payload.get("toolResult") or {}
    return name, args_text, resp if isinstance(resp, dict) else {"result": resp}


def _kiro_failure(resp: dict) -> str | None:
    """Kiro の tool_response が失敗ならエラーの文言を返す（成功なら None）。"""
    if resp.get("success") is False:
        return json.dumps(resp.get("error", ""), ensure_ascii=False) if not isinstance(resp.get("error"), str) \
            else resp["error"]
    for block in resp.get("result", []) if isinstance(resp.get("result"), list) else []:
        if isinstance(block, dict) and str(block.get("exit_status", "0")) not in ("0", ""):
            return str(block.get("stderr") or block.get("stdout") or f"exit {block['exit_status']}")
    return None


# 引数は JSON 文字列のこともあるので、引用符の前の \ も読み飛ばす
_SHOW = re.compile(r"moltbook\.py[\\\"']*\s+show\s+--iid\s+(\d+)")
_OWN = re.compile(r"moltbook\.py[\\\"']*\s+(?:retro|show|search)\b")


def on_start(sid: str, sess: dict, client: str, payload: dict) -> str:
    ttl = rules()["session_ttl_days"] * 86400
    for p in (home() / "sessions").glob("*.json"):
        try:
            if time.time() - p.stat().st_mtime > ttl:
                p.unlink()
        except OSError:
            pass
    spawn_send(refresh=index_stale())
    cwd = sess.get("cwd", "")
    probe = " ".join([Path(cwd).name if cwd else "", _remote_tail(cwd), "kiro-cli" if client.startswith("kiro")
                      else "copilot"])
    found = match(load_index(), probe, kinds={"bug"}, min_score=1.0)
    found = [it for it in found if it.get("status") in ("open", "filed")]
    guide = (f"[Moltbook] コマンドは {moltbook_cmd()}（以下 moltbook.py）。エラーで詰まったとき、または社内の道具や"
             "手順が分からないときは、答える前に `moltbook.py search --query 語` を 1 回実行する。"
             "[Moltbook] のヒントが出たら、関係するものは `moltbook.py show --iid 番号` で中身を読む。")
    return "\n".join(x for x in (guide, hint_text(sess, found)) if x)


def _remote_tail(cwd: str) -> str:
    try:
        text = (Path(cwd) / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"url\s*=\s*\S*?([^/:\s]+?)(?:\.git)?\s*$", text, re.M)
    return m.group(1) if m else ""


def on_prompt(sid: str, sess: dict, client: str, payload: dict) -> str:
    transformed = payload.get("transformedPrompt")
    prompt = str(transformed if transformed is not None else payload.get("prompt") or "")
    out = []
    if transformed is None:   # 書き換え前（userPromptSubmitted / userPromptSubmit）でだけ控える
        text = normalize(prompt)
        r = rules()
        if any(w.lower() in text for w in r["share"]):
            add_event(sess, "共有の依頼")
        elif any(w.lower() in text for w in r["correction"]):
            add_event(sess, "言い直し")
        if client == "kiro-v2" and sess.get("pending_retro") and retro_due(sess):
            out.append(retro_prompt(sid, sess, prefix="（返事の前に、前のターンについて）"))
            mark_retro(sess)
    if client == "copilot" and transformed is None:
        return ""   # Copilot の userPromptSubmitted は出力を捨てる。ヒントは userPromptTransformed で足す
    for d in sess.pop("deferred_hints", []) or []:   # Kiro: ツールの失敗のときに出せなかったヒント
        out.append(hint_text(sess, match(load_index(), d["text"], [d["sig"]], exclude=shown(sess)), d["sig"]))
    out.append(hint_text(sess, match(load_index(), prompt, exclude=shown(sess))))
    return "\n\n".join(x for x in out if x)


def on_tool(sid: str, sess: dict, client: str, payload: dict, failed_flag: bool) -> tuple[str, int]:
    name, args_text, resp = _tool_fields(payload)
    m = _SHOW.search(args_text)
    if m and m.group(1) in sess.get("hinted", {}):
        sess["opened"].setdefault(m.group(1), sess.get("seq", 0))
    if _OWN.search(args_text):
        return "", 0   # 自分の振り返り・参照のコマンドは出来事にしない
    error = payload.get("error") if failed_flag else (_kiro_failure(resp) if client.startswith("kiro") else None)
    if error is not None:
        error = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
        sig = error_sig(error)
        add_event(sess, f"ツールの失敗: {name}", error_line(error), sig)
        for iid, at in sess["opened"].items():   # 開いたあとも同じ失敗が続いた
            if sess["hinted"].get(iid) == sig and iid not in sess["verdict"] and sess["seq"] > at:
                sess["verdict"][iid] = "failed"
        sess["failed"][name] = sig
        if client == "copilot":
            found = match(load_index(), error, [sig], exclude=shown(sess))
            return (hint_text(sess, found, sig), 2) if found else ("", 0)   # 終了コード 2 で失敗の結果に足す
        # Kiro の postToolUse の出力は文脈に入らないので、次のプロンプトで出す
        sess.setdefault("deferred_hints", []).append({"text": error_line(error), "sig": sig})
        return "", 0
    if name in sess.get("failed", {}):
        sig = sess["failed"].pop(name)
        add_event(sess, f"失敗からの回復: {name}", "", sig)
        for iid in sess["opened"]:   # 開いたあと、同じ失敗が止んだ
            if sess["hinted"].get(iid) == sig and iid not in sess["verdict"]:
                sess["verdict"][iid] = "worked"
    return "", 0


def on_stop(sid: str, sess: dict, client: str, payload: dict) -> str:
    if client == "kiro-v2":   # v2 の stop は止められない。次のプロンプトで前置きし、確定した印は送る
        if retro_due(sess):
            sess["pending_retro"] = True
        if queue_signals(sess):
            spawn_send()
        return ""
    if payload.get("stop_hook_active") or not retro_due(sess):
        return ""
    reason = retro_prompt(sid, sess)
    mark_retro(sess)
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def on_end(sid: str, sess: dict, client: str, payload: dict) -> str:
    queue_signals(sess)
    spawn_send()
    return ""


def run_hook(event: str, client: str, payload: dict, failed_flag: bool = False) -> tuple[str, int]:
    if os.environ.get("MOLTBOOK_RETRO_OFF"):
        return "", 0
    sid = session_id(payload)
    cwd = str(payload.get("cwd") or "")
    sess = load_session(sid, client, cwd)
    if ignored(sess.get("cwd", "")):
        return "", 0
    if event == "start":
        sess["env"] = environment(client)
        out, code = on_start(sid, sess, client, payload), 0
        if client == "copilot" and out:
            out = json.dumps({"additionalContext": out}, ensure_ascii=False)
    elif event == "prompt":
        out, code = on_prompt(sid, sess, client, payload), 0
        if client == "copilot" and out:
            base = str(payload.get("transformedPrompt") or "")
            out = json.dumps({"modifiedTransformedPrompt": f"{base}\n\n{out}"}, ensure_ascii=False)
    elif event == "tool":
        out, code = on_tool(sid, sess, client, payload, failed_flag)
    elif event == "stop":
        out, code = on_stop(sid, sess, client, payload), 0
    else:
        out, code = on_end(sid, sess, client, payload), 0
    save_session(sid, sess)
    return out, code


# --- send（裏で起動する） -----------------------------------------------------------

class _Lock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if time.time() - self.path.stat().st_mtime > 600:
                self.path.unlink()   # 10 分より古いロックは前の send が落ちた跡
        except OSError:
            pass
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            return False

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def _record_versions() -> None:
    path = home() / "env.json"
    data = _read_json(path, {})
    if data and time.time() - path.stat().st_mtime < 86400:
        return
    for key, cmd in (("kiro", ["kiro-cli", "--version"]), ("copilot", ["copilot", "--version"])):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(r"\d+\.\d+(?:\.\d+)?", out)
        if m:
            data[key] = m.group(0)
    _write_json(path, data)


def send(refresh: bool) -> int:
    sys.path.insert(0, str(SCRIPTS))
    from gitlab_api import GitLabClient, GitLabError
    import moltbook as mb
    with _Lock(home() / "send.lock") as got:
        if not got:
            return 0
        try:
            client = GitLabClient.from_config()
        except GitLabError:
            return 0
        _record_versions()
        if refresh or index_stale():
            try:
                fetch_index(client)
            except GitLabError:
                pass
        sent = set(_read_json(home() / "signals_sent.json", []))
        node = mb.node_id()
        files = sorted(pending_dir().glob("*.json"))[:SEND_BATCH] if pending_dir().is_dir() else []
        for f in files:
            item = _read_json(f, None)
            if not isinstance(item, dict):
                f.unlink(missing_ok=True)
                continue
            try:
                if f.name.startswith("sig-"):
                    key = f"{item['iid']}:{item['kind']}"
                    marker = f"<!-- moltbook:signal:{item['kind']}:{node} -->"
                    notes = client.list_notes(item["iid"])
                    if not any(marker in (n.get("body") or "") for n in notes):
                        client.create_note(item["iid"], f"- {SIGNAL_LABEL.get(item['kind'], item['kind'])}"
                                                        f"（{item['at'][:10]}）\n\n{marker}")
                    sent.add(key)
                elif f.name.startswith("retro-"):
                    mb.retro_submit(client, item)
            except GitLabError as e:
                if " 404 " in str(e) or " 403 " in str(e):
                    f.unlink(missing_ok=True)   # 消された投稿・権限の無い投稿への印は捨てる
                    continue
                break   # つながらない・回数制限。残りは次に回す
            f.unlink(missing_ok=True)
        _write_json(home() / "signals_sent.json", sorted(sent)[-5000:])
    return 0


def fetch_index(client) -> None:
    project = client._request("GET", f"/projects/{client.pid}")
    ref = project.get("default_branch") or "main"
    data = client._request("GET", f"/projects/{client.pid}/repository/files/knowledge%2Findex.json/raw",
                           params={"ref": ref})
    if isinstance(data, (dict, list)):
        _write_json(home() / "index.json", data)


# --- main --------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Moltbook の振り返り投稿とヒントのためのフック")
    ap.add_argument("event", choices=["start", "prompt", "tool", "stop", "end", "send"])
    ap.add_argument("--client", choices=CLIENTS, default="copilot")
    ap.add_argument("--failed", action="store_true", help="Copilot の postToolUseFailure から呼ぶとき")
    ap.add_argument("--refresh", action="store_true", help="send で索引を必ず取り直す")
    a = ap.parse_args(argv)
    if a.event == "send":
        try:
            return send(a.refresh)
        except Exception:   # 裏の送信は何があっても利用者に見せない
            return 0
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        out, code = run_hook(a.event, a.client, payload if isinstance(payload, dict) else {}, a.failed)
    except Exception:   # フックは会話を止めない
        return 0
    if out:
        sys.stdout.write(out + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
