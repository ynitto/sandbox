"""gitlab-mr-review-agent — GitLab の MR をエージェント CLI（kiro-cli など）にレビューさせるための、api-watch のプラグインと
エージェントが使うコマンド。

1 つのファイルを 2 つの使い方で使う:

    api-watch --plugin <このファイル> ...     プラグインとして読み込む（register(api)）
        フィルタ gitlab_mr_since    {{ event | gitlab_mr_since }}  前回レビューし終えたときの head の SHA（初めてなら空）
        フィルタ gitlab_mr_kind     {{ event | gitlab_mr_kind }}   初めて / 再レビュー
        チェック gitlab_mr.changed_during_review
                                    レビューのあいだに MR の head（コミット）・ドラフト・対象ブランチが変わったか。
                                    受け渡し先の handled_before_if に書くと、レビュー中に積まれたコミットを取りこぼさない
        チェック gitlab_mr.already_reviewed
                                    今の MR の差分の中身を既にレビューし終えているか（リベース・中身の同じ force push・
                                    閉じて開き直した MR）。受け渡し先の skip_if に書くと、kiro-cli を起こさずに飛ばす

    python3 <このファイル> <コマンド> ...    エージェント CLI がレビューの中で呼ぶ（GitLab への読み書きはこれだけで行う）
        context IID [--since SHA]     MR の概要・前回の指摘（このツールが書いた未解決のスレッド）・差分を出す。
                                      今の head を既にレビューし終えていれば、そう出して終わる
        show IID PATH [--from N --to M]
                                      head の時点のファイルを行番号つきで出す（指摘が直ったかを確かめる）
        comment IID --path P --line N (--body B | --body-file F)
                                      差分の行に指摘のスレッドを立てる。同じ指摘が既にあれば書かない。
                                      差分に無い行なら、場所を本文に書いた MR 全体のスレッドにする
        resolve IID DISCUSSION (--body B | --body-file F)
                                      このツールが立てた指摘のスレッドに返信し、解決にする（既に解決なら何もしない）
        summary IID (--body B | --body-file F)
                                      今の head のレビューのまとめを 1 回だけ書く（2 回目は書かない）

コマンドは GitLab の URL・トークン・プロジェクトを環境変数 GITLAB_URL / GITLAB_TOKEN / GITLAB_PROJECT から読む
（Dagu なら dags --dotenv の .env）。どの書き込みにも見えない印（HTML コメント）を付け、同じ指摘・同じまとめを
二度書かない。「レビューし終えた」はコミットではなく MR の差分の中身（足した行・消した行）で見分けるので、
リベースで head が変わっただけの MR はもう一度レビューしない。標準ライブラリだけで動く。設計は docs/designs/gitlab-mr-review-agent-design.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

OK = 0
FAILED = 1
USAGE = 2

MARK = "mr-review-agent"
FINDING_RE = re.compile(r"<!--\s*" + MARK + r":finding\s+([^>]*?)\s*-->")
SUMMARY_RE = re.compile(r"<!--\s*" + MARK + r":summary\s+sha=([0-9a-f]+)(?:\s+diff=([0-9a-f]+))?\s*-->")
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')

MAX_DIFF_CHARS = 150_000     # context が出す差分の上限（超えたファイルは名前だけを出し、show で見てもらう）
MAX_SHOW_LINES = 2000

# 変化として扱う MR の項目（api-watch の設定のルールの watch と同じにする）
WATCHED = ("sha", "draft", "target_branch")


# ---------------------------------------------------------------------------
# プラグイン（api-watch --plugin で読み込む）
# ---------------------------------------------------------------------------

def _last_sha(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    last = (event.get("refreshed") or {}).get("last_handled")
    return str(last.get("sha") or "") if isinstance(last, dict) else ""


def gitlab_mr_since(event: Any, _arg: Any = None) -> str:
    """前回レビューし終えたときの head の SHA。初めてのレビューなら空。"""
    if not isinstance(event, dict) or not isinstance(event.get("record"), dict):
        raise ValueError("gitlab_mr_since にはイベント（{{ event | gitlab_mr_since }}）を渡してください")
    return _last_sha(event)


def gitlab_mr_kind(event: Any, _arg: Any = None) -> str:
    last = _last_sha(event)
    return f"再レビュー（前回は {last[:8]}）" if last else "初めてのレビュー"


def changed_during_review(ctx) -> bool:
    """実行の前（取り直したレコード）と今とで、MR の head・ドラフト・対象ブランチが違えば真。

    エージェントはコメントしか書かないので、これらが変わったのは人の操作（コミットの追加・force push など）。
    真なら api-watch は実行前の状態までを処理済みにし、次の取得で新しいコミットをもう一度レビューさせる。
    GitLab に届かなければ例外のまま返し、api-watch が真とみなす（取りこぼさない側。そのときは同じ head を
    もう一度流すが、context が「レビューし終えています」と出すので、指摘は重ならない）。"""
    url = str(ctx.options.get("url") or ctx.conn.get("url") or "").rstrip("/")
    mr = ctx.event.get("record") or {}
    if not url or mr.get("project_id") is None or mr.get("iid") is None:
        raise ValueError("GitLab の URL（接続設定の url か options.url）と、レコードの project_id・iid が要ります")
    now = ctx.get_json(f"{url}/api/v4/projects/{mr['project_id']}/merge_requests/{mr['iid']}") or {}
    for name in WATCHED:
        if now.get(name) != mr.get(name):
            ctx.log(f"!{mr['iid']} はレビューのあいだに {name} が変わったので（{mr.get(name)} → {now.get(name)}）、"
                    "次の取得でもう一度レビューします")
            return True
    return False


def _pages(ctx, url: str, query: dict | None = None, limit: int = 50) -> list | None:
    """ページを順に GET してつなぐ（1 ページ目が 404 なら None）。"""
    out: list = []
    for page in range(1, limit + 1):
        body = ctx.get_json(url, query={**(query or {}), "per_page": "100", "page": str(page)})
        if body is None:
            return None if page == 1 else out
        items = body if isinstance(body, list) else []
        out.extend(items)
        if len(items) < 100:
            break
    return out


def already_reviewed(ctx) -> bool:
    """今の MR の差分の中身を、既にレビューし終えていれば真（skip_if に書く）。

    リベース・中身の同じ force push では head のコミットが変わるので、api-watch はイベントにして流そうとする。
    MR の差分（足した行・消した行）の指紋が、これまでに書いたまとめのどれかと同じなら、kiro-cli を起こさずに飛ばす。
    閉じて開き直した MR・ルールの名前を変えたあとの MR も、まとめが残っていれば同じく飛ばす。
    GitLab に届かなければ例外のまま返し、api-watch が偽とみなす（kiro-cli を起こすが、context が同じ判定で止める）。"""
    url = str(ctx.options.get("url") or ctx.conn.get("url") or "").rstrip("/")
    mr = ctx.event.get("record") or {}
    if not url or mr.get("project_id") is None or mr.get("iid") is None:
        raise ValueError("GitLab の URL（接続設定の url か options.url）と、レコードの project_id・iid が要ります")
    base = f"{url}/api/v4/projects/{mr['project_id']}/merge_requests/{mr['iid']}"
    diffs = _pages(ctx, f"{base}/diffs")
    if diffs is None:                                   # GitLab 15.7 より前
        diffs = (ctx.get_json(f"{base}/changes") or {}).get("changes") or []
    notes = _pages(ctx, f"{base}/notes", {"sort": "desc", "order_by": "created_at"}) or []
    head = str(mr.get("sha") or "")
    hit = reviewed(notes, head, diff_fingerprint(diffs, head))
    if hit:
        ctx.log(f"!{mr['iid']} の差分の中身は {hit[:8]} のときにレビューし終えているので（リベースなど）、kiro-cli を起こしません")
        return True
    return False


def register(api) -> None:
    api.add_filter("gitlab_mr_since", gitlab_mr_since)
    api.add_filter("gitlab_mr_kind", gitlab_mr_kind)
    api.add_check("gitlab_mr.changed_during_review", changed_during_review)
    api.add_check("gitlab_mr.already_reviewed", already_reviewed)


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------

class GitLabError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class GitLab:
    def __init__(self, url: str, token: str, project: str, timeout: float = 60):
        self.api = url.rstrip("/") + "/api/v4"
        self.token = token
        self.project = urllib.parse.quote(str(project), safe="")
        self.timeout = timeout

    def call(self, method: str, path: str, query: dict | None = None, body: Any = None,
             raw: bool = False) -> tuple[Any, dict]:
        url = f"{self.api}/projects/{self.project}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"PRIVATE-TOKEN": self.token, "Accept": "application/json",
                                              **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                msg = json.loads(e.read().decode("utf-8")).get("message")
                detail = f"（{msg}）" if msg else ""
            except (ValueError, AttributeError):
                pass
            raise GitLabError(f"{method} {path} が {e.code} で失敗しました{detail}", e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise GitLabError(f"{method} {path} が届きません: {getattr(e, 'reason', e)}") from None
        if raw:
            return content.decode("utf-8", errors="replace"), headers
        return (json.loads(content.decode("utf-8")) if content.strip() else None), headers

    def get(self, path: str, query: dict | None = None) -> Any:
        return self.call("GET", path, query)[0]

    def get_all(self, path: str, query: dict | None = None) -> list:
        out: list = []
        page = 1
        while True:
            body, headers = self.call("GET", path, {**(query or {}), "per_page": 100, "page": page})
            items = body if isinstance(body, list) else []
            out.extend(items)
            if "x-next-page" in headers:             # GitLab はページがまだあれば次の番号を、無ければ空を返す
                nxt = headers["x-next-page"].strip()
                if not nxt.isdigit():
                    return out
                page = int(nxt)
            elif len(items) < 100:
                return out
            else:
                page += 1

    def mr(self, iid: Any) -> dict:
        return self.get(f"/merge_requests/{iid}") or {}

    def mr_diffs(self, iid: Any) -> list[dict]:
        """MR 全体の差分（ファイルごと）。/diffs（GitLab 15.7 以降）が無ければ /changes。"""
        try:
            return self.get_all(f"/merge_requests/{iid}/diffs")
        except GitLabError as e:
            if e.status != 404:
                raise
        return (self.get(f"/merge_requests/{iid}/changes") or {}).get("changes") or []

    def compare(self, frm: str, to: str) -> list[dict]:
        return (self.get("/repository/compare", {"from": frm, "to": to, "straight": "true"}) or {}).get("diffs") or []

    def discussions(self, iid: Any) -> list[dict]:
        return self.get_all(f"/merge_requests/{iid}/discussions")

    def notes(self, iid: Any) -> list[dict]:
        return self.get_all(f"/merge_requests/{iid}/notes", {"sort": "desc", "order_by": "created_at"})

    def file(self, path: str, ref: str) -> str:
        enc = urllib.parse.quote(path, safe="")
        return self.call("GET", f"/repository/files/{enc}/raw", {"ref": ref}, raw=True)[0]


# ---------------------------------------------------------------------------
# 印（このツールが書いたものを見分ける）
# ---------------------------------------------------------------------------

def finding_key(path: str, body: str) -> str:
    return hashlib.sha256(f"{path}\n{body.strip()}".encode("utf-8")).hexdigest()[:12]


def finding_mark(key: str, path: str, line: int) -> str:
    return f'<!-- {MARK}:finding key="{key}" path="{path.replace(chr(34), "")}" line="{line}" -->'


def parse_finding(body: str) -> dict | None:
    m = FINDING_RE.search(body or "")
    if not m:
        return None
    return dict(ATTR_RE.findall(m.group(1)))


def strip_marks(body: str) -> str:
    return SUMMARY_RE.sub("", FINDING_RE.sub("", body or "")).strip()


def own_threads(discussions: list[dict]) -> list[dict]:
    """このツールが立てた指摘のスレッド。{id, resolved, key, path, line, body, replies}。"""
    out = []
    for d in discussions or []:
        notes = [n for n in d.get("notes") or [] if isinstance(n, dict)]
        if not notes:
            continue
        attrs = parse_finding(str(notes[0].get("body") or ""))
        if attrs is None:
            continue
        resolvable = [n for n in notes if n.get("resolvable")]
        resolved = bool(resolvable) and all(n.get("resolved") for n in resolvable)
        out.append({"id": d.get("id"), "resolved": resolved, "key": attrs.get("key", ""),
                    "path": attrs.get("path", ""), "line": attrs.get("line", ""),
                    "body": strip_marks(str(notes[0].get("body") or "")),
                    "replies": [{"author": (n.get("author") or {}).get("username") or "?",
                                 "body": strip_marks(str(n.get("body") or ""))}
                                for n in notes[1:] if not n.get("system")]})
    return out


def diff_fingerprint(diffs: list[dict], head: str) -> str:
    """MR の差分の中身の指紋。ファイルの名前・作った / 消した / 名前の変更・モードと、足した行・消した行だけを見る。

    行番号（@@ の行）と前後の行は見ないので、リベースで対象ブランチが進んだだけなら同じになる。GitLab が中身を返さない
    大きなファイルがあれば中身を比べられないので、head のコミットで代える（リベースでもう一度レビューする側に倒す）。"""
    h = hashlib.sha256()
    for f in sorted(diffs or [], key=lambda f: (str(f.get("new_path") or ""), str(f.get("old_path") or ""))):
        body = str(f.get("diff") or "")
        if not body.strip() and (f.get("too_large") or f.get("collapsed")):
            return hashlib.sha256(f"head:{head}".encode("utf-8")).hexdigest()[:16]
        meta = [str(f.get(k) or "") for k in ("old_path", "new_path", "new_file", "deleted_file", "renamed_file",
                                               "a_mode", "b_mode")]
        h.update(("\x00".join(meta) + "\n").encode("utf-8"))
        for line in body.splitlines():
            if line[:1] in ("+", "-", "\\"):
                h.update((line + "\n").encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()[:16]


def reviewed(notes: list[dict], head: str, fingerprint: str) -> str | None:
    """同じ中身の差分のまとめを既に書いていれば、そのときの head のコミット（無ければ None）。

    差分の指紋の無い古い印は、head のコミットが同じときだけ当たる。"""
    for n in notes or []:
        m = SUMMARY_RE.search(str((n or {}).get("body") or ""))
        if not m:
            continue
        if (m.group(2) and m.group(2) == fingerprint) or (not m.group(2) and m.group(1) == head):
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 差分
# ---------------------------------------------------------------------------

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def new_lines(diff: str) -> dict[int, int | None]:
    """差分の新しい側の行番号 → 古い側の行番号（足した行は None）。差分に出ている行だけ。"""
    out: dict[int, int | None] = {}
    old = new = 0
    for line in (diff or "").splitlines():
        m = HUNK_RE.match(line)
        if m:
            old, new = int(m.group(1)), int(m.group(2))
            continue
        if not new:
            continue
        if line.startswith("+"):
            out[new] = None
            new += 1
        elif line.startswith("-"):
            old += 1
        elif line.startswith("\\"):
            continue
        else:
            out[new] = old
            old += 1
            new += 1
    return out


def position_for(mr: dict, diffs: list[dict], path: str, line: int) -> dict | None:
    """差分の行に書くときの position。差分に無い行なら None。"""
    refs = mr.get("diff_refs") or {}
    if not all(refs.get(k) for k in ("base_sha", "start_sha", "head_sha")):
        return None
    for f in diffs:
        if f.get("new_path") != path or f.get("deleted_file"):
            continue
        lines = new_lines(str(f.get("diff") or ""))
        if line not in lines:
            return None
        pos = {"position_type": "text", "base_sha": refs["base_sha"], "start_sha": refs["start_sha"],
               "head_sha": refs["head_sha"], "new_path": path, "old_path": f.get("old_path") or path, "new_line": line}
        if lines[line] is not None:
            pos["old_line"] = lines[line]
        return pos
    return None


def render_diffs(diffs: list[dict], limit: int = MAX_DIFF_CHARS) -> str:
    parts: list[str] = []
    skipped: list[str] = []
    used = 0
    for f in diffs:
        old, new = f.get("old_path") or "", f.get("new_path") or ""
        head = f"--- {old if not f.get('new_file') else '/dev/null'}\n+++ {new if not f.get('deleted_file') else '/dev/null'}"
        body = str(f.get("diff") or "")
        if not body.strip():
            note = "（差分が大きすぎて GitLab が返しませんでした。show で中身を見てください）" \
                if f.get("too_large") or f.get("collapsed") else "（中身の変化なし: 名前・モードの変更かバイナリ）"
            body = note
        chunk = f"{head}\n{body.rstrip()}\n"
        if used + len(chunk) > limit:
            skipped.append(new or old)
            continue
        parts.append(chunk)
        used += len(chunk)
    if skipped:
        parts.append("差分が長いので、次のファイルは名前だけです（show で中身を見てください）:\n"
                     + "\n".join(f"- {p}" for p in skipped))
    return "\n".join(parts) if parts else "（差分なし）"


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------

def _excerpt(text: str, n: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + " …"


def cmd_context(gl: GitLab, iid: str, since: str, out=print) -> int:
    mr = gl.mr(iid)
    if not mr:
        raise GitLabError(f"MR !{iid} が見つかりません")
    head = str(mr.get("sha") or "")
    if mr.get("state") != "opened":
        out(f"MR !{iid} は {mr.get('state')} です。レビューせずに終えてください。")
        return OK
    whole = gl.mr_diffs(iid)
    hit = reviewed(gl.notes(iid), head, diff_fingerprint(whole, head))
    if hit:
        if hit == head:
            out(f"MR !{iid} の今のコミット（{head[:8]}）はレビューし終えています。何もせずに終えてください。")
        else:
            out(f"MR !{iid} の差分の中身は、レビューし終えた {hit[:8]} のときと同じです（リベースなど）。何もせずに終えてください。")
        return OK
    threads = own_threads(gl.discussions(iid))
    open_threads = [t for t in threads if not t["resolved"]]
    closed_threads = [t for t in threads if t["resolved"]]
    out(f"# MR !{iid}「{mr.get('title')}」")
    out(f"- URL: {mr.get('web_url')}")
    out(f"- 作成者: {(mr.get('author') or {}).get('username')}")
    out(f"- ブランチ: {mr.get('source_branch')} → {mr.get('target_branch')}")
    out(f"- 今のコミット: {head}")
    out("")
    out("## 説明")
    out(str(mr.get("description") or "（なし）").strip())
    out("")
    out(f"## 前回までの指摘（未解決 {len(open_threads)} 件・解決済み {len(threads) - len(open_threads)} 件）")
    if not open_threads:
        out("（未解決の指摘なし）")
    for t in open_threads:
        out(f"### スレッド {t['id']}  {t['path']}:{t['line']}")
        out(_excerpt(t["body"]))
        for r in t["replies"]:
            out(f"> 返信（{r['author']}）: {_excerpt(r['body'], 300)}")
        out("")
    if closed_threads:
        out("### 解決済みの指摘（同じ内容をもう一度指摘しない）")
        for t in closed_threads:
            out(f"- {t['path']}:{t['line']}  {_excerpt(t['body'].splitlines()[0] if t['body'] else '', 120)}")
    out("")
    diffs: list[dict] | None = None
    if since and since != head:
        try:
            # リベースしていると、前のコミットからの差分に対象ブランチの変更も混ざる。MR が触っているファイルだけにする
            files = {f.get("new_path") for f in whole} | {f.get("old_path") for f in whole}
            diffs = [f for f in gl.compare(since, head) if f.get("new_path") in files or f.get("old_path") in files]
            out(f"## 前回レビューしたコミット（{since[:8]}）からの変更（この MR が触っているファイルだけ）")
        except GitLabError as e:
            out(f"（前回のコミット {since[:8]} からの差分が取れないので（{e}）、MR 全体の差分を出します。force push で消えた可能性があります）")
    if diffs is None:
        diffs = whole
        out("## MR 全体の変更")
    out("```diff")
    out(render_diffs(diffs))
    out("```")
    return OK


def cmd_show(gl: GitLab, iid: str, path: str, frm: int | None, to: int | None, out=print) -> int:
    mr = gl.mr(iid)
    ref = str(mr.get("sha") or "")
    if not ref:
        raise GitLabError(f"MR !{iid} の今のコミットが分かりません")
    try:
        text = gl.file(path, ref)
    except GitLabError as e:
        if e.status == 404:
            out(f"{path} は今のコミット（{ref[:8]}）にありません（消されたか、名前が変わりました）")
            return OK
        raise
    lines = text.splitlines()
    start = max(1, frm or 1)
    end = min(len(lines), to or len(lines), start + MAX_SHOW_LINES - 1)
    out(f"# {path} @ {ref[:8]}（{start}〜{end} 行目 / 全 {len(lines)} 行）")
    for n in range(start, end + 1):
        out(f"{n:>6}  {lines[n - 1]}")
    return OK


def cmd_comment(gl: GitLab, iid: str, path: str, line: int, body: str, out=print) -> int:
    key = finding_key(path, body)
    for t in own_threads(gl.discussions(iid)):
        if t["key"] == key:
            out(f"同じ指摘が既にあります（スレッド {t['id']}・{'解決済み' if t['resolved'] else '未解決'}）。書きません。")
            return OK
        if not t["resolved"] and t["path"] == path and str(t["line"]) == str(line):
            out(f"{path}:{line} には未解決の指摘（スレッド {t['id']}）が既にあります。書きません"
                "（言い直しで同じ指摘を重ねないため。別の問題なら、その問題がある別の行に書いてください）。")
            return OK
    mr = gl.mr(iid)
    pos = position_for(mr, gl.mr_diffs(iid), path, line)
    mark = finding_mark(key, path, line)
    if pos is not None:
        try:
            d, _ = gl.call("POST", f"/merge_requests/{iid}/discussions", body={"body": f"{body.strip()}\n\n{mark}",
                                                                              "position": pos})
            out(f"{path}:{line} に指摘しました（スレッド {(d or {}).get('id')}）")
            return OK
        except GitLabError as e:
            if e.status not in (400, 422):
                raise
            out(f"{path}:{line} に行のコメントを付けられなかったので（{e}）、MR 全体のスレッドにします")
    text = f"**`{path}:{line}`**\n\n{body.strip()}\n\n{mark}"
    d, _ = gl.call("POST", f"/merge_requests/{iid}/discussions", body={"body": text})
    out(f"{path}:{line} は差分に無い行なので、MR 全体のスレッドとして指摘しました（スレッド {(d or {}).get('id')}）")
    return OK


def cmd_resolve(gl: GitLab, iid: str, discussion: str, body: str, out=print) -> int:
    t = next((t for t in own_threads(gl.discussions(iid)) if str(t["id"]) == str(discussion)), None)
    if t is None:
        out(f"スレッド {discussion} はこのツールが立てた指摘ではありません。解決にしません。")
        return FAILED
    if t["resolved"]:
        out(f"スレッド {discussion} は既に解決しています。")
        return OK
    gl.call("POST", f"/merge_requests/{iid}/discussions/{discussion}/notes", body={"body": body.strip()})
    gl.call("PUT", f"/merge_requests/{iid}/discussions/{discussion}", {"resolved": "true"})
    out(f"スレッド {discussion}（{t['path']}:{t['line']}）に返信して解決にしました")
    return OK


def cmd_summary(gl: GitLab, iid: str, body: str, out=print) -> int:
    mr = gl.mr(iid)
    head = str(mr.get("sha") or "")
    if not head:
        raise GitLabError(f"MR !{iid} の今のコミットが分かりません")
    fp = diff_fingerprint(gl.mr_diffs(iid), head)
    if reviewed(gl.notes(iid), head, fp):
        out(f"今の差分（{head[:8]}）のまとめは既にあります。書きません。")
        return OK
    gl.call("POST", f"/merge_requests/{iid}/notes",
            body={"body": f"{body.strip()}\n\n<!-- {MARK}:summary sha={head} diff={fp} -->"})
    out(f"MR !{iid} にレビューのまとめを書きました（{head[:8]}）")
    return OK


def _body(args) -> str:
    if getattr(args, "body_file", None):
        with open(args.body_file, encoding="utf-8") as f:
            return f.read().strip()
    return str(getattr(args, "body", "") or "").strip()


def _add_body(p) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--body", help="本文（Markdown）")
    g.add_argument("--body-file", help="本文のファイル（UTF-8）")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")     # Windows の cp932 / cp1252 でも日本語を出す
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="gitlab_mr.py", description="GitLab の MR のレビューで使うコマンド")
    p.add_argument("--project", default=os.environ.get("GITLAB_PROJECT", ""),
                   help="プロジェクト（group/project か ID。既定は環境変数 GITLAB_PROJECT）")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context", help="MR の概要・前回までの指摘・差分を出す")
    c.add_argument("iid")
    c.add_argument("--since", default="", help="前回レビューしたコミット（あれば、そこからの差分を出す）")
    s = sub.add_parser("show", help="今のコミットのファイルを行番号つきで出す")
    s.add_argument("iid")
    s.add_argument("path")
    s.add_argument("--from", dest="frm", type=int)
    s.add_argument("--to", type=int)
    m = sub.add_parser("comment", help="差分の行に指摘のスレッドを立てる（同じ指摘は書かない）")
    m.add_argument("iid")
    m.add_argument("--path", required=True, help="ファイルのパス（新しい側）")
    m.add_argument("--line", required=True, type=int, help="新しい側の行番号")
    _add_body(m)
    r = sub.add_parser("resolve", help="指摘のスレッドに返信して解決にする")
    r.add_argument("iid")
    r.add_argument("discussion", help="スレッドの ID（context の「スレッド」の横）")
    _add_body(r)
    u = sub.add_parser("summary", help="今のコミットのレビューのまとめを 1 回だけ書く")
    u.add_argument("iid")
    _add_body(u)
    args = p.parse_args(argv)

    url, token = os.environ.get("GITLAB_URL", ""), os.environ.get("GITLAB_TOKEN", "")
    if not url or not token or not args.project:
        print("環境変数 GITLAB_URL・GITLAB_TOKEN・GITLAB_PROJECT を設定してください", file=sys.stderr)
        return USAGE
    gl = GitLab(url, token, args.project)
    try:
        if args.cmd == "context":
            return cmd_context(gl, args.iid, args.since.strip())
        if args.cmd == "show":
            return cmd_show(gl, args.iid, args.path, args.frm, args.to)
        body = _body(args)
        if not body:
            print("本文が空です", file=sys.stderr)
            return USAGE
        if args.cmd == "comment":
            return cmd_comment(gl, args.iid, args.path, args.line, body)
        if args.cmd == "resolve":
            return cmd_resolve(gl, args.iid, args.discussion, body)
        return cmd_summary(gl, args.iid, body)
    except (OSError, ValueError, GitLabError) as e:
        print(str(e), file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
