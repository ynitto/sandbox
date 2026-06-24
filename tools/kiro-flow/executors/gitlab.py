#!/usr/bin/env python3
"""gitlab — kiro-flow の executor プラグイン（opt-in のワーカーバス）

kiro-loop の event_hook と同じ流儀で、kiro-flow 本体から importlib で動的にロードされ、
`execute()` が呼び出される。タスクを **GitLab イシュー** にして委譲し、リモートの
（別マシン/別人の）ワーカーが拾って実装する。kiro-flow はイシューをポーリングし、
レビュアーが `status:approved` を付ける（= 受け入れ承認）まで待って完了とみなす。
ローカルに kiro-cli が無くても、GitLab 越しに作業を委譲できる。

プラグイン契約:
    execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None,
            repo_instruction="") -> (text, data)
    ※ goal は本来の目的のみ。成果物リポジトリの clone 指示は repo_instruction で別途受け取る
      （イシューのタイトル/目的が clone 指示で埋まらないようにするため）。

イシュー API は **GitLab REST API（v4）を stdlib だけで直叩き**する（gl.py 相当の
create-issue / get-issue / get-comments を移植）。外部の gitlab-idd スキル（gl.py）の
起動は不要で、gl.py へのフォールバックも行わない。

起票先プロジェクト URL とトークンの解決:
    - **repo_url（起票先 URL）**: kiro-flow.yaml の `gitlab.repo_url` が権威。これをそのまま
      使うため、git remote origin 等への曖昧なフォールバックは無い（誤起票を防ぐ）。
    - **トークン**: kiro-flow.yaml には置かず、gl.py と同じ場所から解決する。優先順は
      connections.yaml（接続ラベル `conn_label`）→ 環境変数 GITLAB_TOKEN / GL_TOKEN →
      シェル rc ファイル（~/.bashrc 等）。秘密情報を設定ファイルに残さない運用に合わせる。

設定の渡し方（優先度: 個別環境変数 > KIRO_FLOW_EXECUTOR_CONFIG(JSON) > 既定）:
    - kiro-flow 本体は設定ファイルの `gitlab:` ブロックを JSON 化して環境変数
      `KIRO_FLOW_EXECUTOR_CONFIG` で渡す（repo_url / conn_label / ラベル / ポーリング等）。
    - 個別の上書きは `KIRO_FLOW_GITLAB_<KEY>`（例: KIRO_FLOW_GITLAB_POLL_INTERVAL）。

※ ポーリングするのは kiro-flow（Python プロセス）であって LLM セッションではない。
   gitlab-idd の「LLM ポーリング禁止」はワーカー/レビュアー LLM への指針で、ここでの
   定期確認とは別物。
"""
from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

NAME = "gitlab"

# プラグインの既定設定（kiro-flow の CONFIG_DEFAULTS["gitlab"] と同値）。
# トークンはここに置かない（gl.py と同じ場所＝connections.yaml/環境変数/シェル rc から解決）。
_DEFAULTS = {
    "conn_label": "default",
    "repo_url": "",
    "labels": "status:open,assignee:any",
    "priority": "priority:normal",
    "poll_interval": 30.0,
    "timeout": 86400.0,
    "approved_label": "status:approved",
    "done_label": "status:done",
}


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [gitlab] {msg}", flush=True)


def _config() -> dict:
    """既定 ＜ KIRO_FLOW_EXECUTOR_CONFIG(JSON) ＜ 個別環境変数 の順に解決する。"""
    cfg = dict(_DEFAULTS)
    raw = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
    if raw:
        try:
            block = json.loads(raw)
            if isinstance(block, dict):
                cfg.update({k: v for k, v in block.items() if v is not None})
        except json.JSONDecodeError:
            pass
    # 個別環境変数 KIRO_FLOW_GITLAB_<KEY> による上書き
    for key in _DEFAULTS:
        env = os.environ.get(f"KIRO_FLOW_GITLAB_{key.upper()}")
        if env is not None and env != "":
            cfg[key] = env
    return cfg


def _as_float(v, default: float) -> float:
    """0 を有効値として尊重する（`x or default` は 0.0 を弾くため使わない）。"""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# --- 依存成果のアクセサ（kiro-flow 本体と同じ result dict 形を読む） -----------
def _dep_text(r: dict) -> str:
    return str((r or {}).get("output", ""))


def _dep_data(r: dict):
    return (r or {}).get("data")


def _is_gate_result(r: dict) -> bool:
    """verify gate の結果か（data が {"ok": ...} を持つ）。集約対象から除くのに使う。"""
    dv = _dep_data(r)
    return isinstance(dv, dict) and "ok" in dv


# --- トークン解決（gl.py と同じ場所: connections.yaml / 環境変数 / シェル rc） ----
def _find_gitlab_idd_scripts_dir():
    """gitlab-idd スキルの scripts/ ディレクトリ（config_loader.py 同梱）を探す。
    connections.yaml を gl.py と同じ流儀で読むために使う。
    検索順: .github/skills/ → git root/.github/skills/ → ~/.kiro/skills/ → skill_home。"""
    candidates = []
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, ".github", "skills", "gitlab-idd", "scripts"))
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
        if root:
            candidates.append(os.path.join(root, ".github", "skills", "gitlab-idd", "scripts"))
    except Exception:  # noqa: BLE001
        pass
    candidates.append(os.path.join(os.path.expanduser("~/.kiro/skills"),
                                   "gitlab-idd", "scripts"))
    for agent_dir in [os.path.expanduser("~/.kiro"), os.path.expanduser("~/.copilot"),
                      os.path.expanduser("~/.claude"), os.path.expanduser("~/.codex")]:
        reg = os.path.join(agent_dir, "skill-registry.json")
        if os.path.isfile(reg):
            try:
                with open(reg, encoding="utf-8") as f:
                    home = json.load(f).get("skill_home", "")
                if home:
                    candidates.append(os.path.join(home, "gitlab-idd", "scripts"))
            except Exception:  # noqa: BLE001
                pass
    for c in candidates:
        if os.path.isfile(os.path.join(c, "config_loader.py")):
            return c
    return None


def _token_from_connections(conn_label: str) -> str:
    """gl.py と同じ connections.yaml から接続ラベルのトークンを読む（config_loader 経由）。
    config_loader / connections.yaml / PyYAML が無ければ空文字（→ 次のソースへ）。"""
    scripts_dir = _find_gitlab_idd_scripts_dir()
    if not scripts_dir:
        return ""
    try:
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        config_loader = importlib.import_module("config_loader")
        conn = config_loader.get_connection("gitlab", conn_label)
        return str((conn or {}).get("token") or "").strip()
    except Exception:  # noqa: BLE001 — 不在/解析失敗は無視し、環境変数・シェル rc へ委ねる
        return ""


def _token_from_shell_files() -> str:
    """~/.bashrc 等から GITLAB_TOKEN / GL_TOKEN を読み込む（gl.py と同じフォールバック）。"""
    for fname in ("~/.bashrc", "~/.bash_profile", "~/.profile", "~/.zshrc"):
        path = os.path.expanduser(fname)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    m = re.match(
                        r'^(?:export\s+)?(GITLAB_TOKEN|GL_TOKEN)=["\']?([^\s"\'#]+)["\']?',
                        line,
                    )
                    if m:
                        return m.group(2)
        except OSError:
            continue
    return ""


def _resolve_token(cfg: dict) -> str:
    """トークンを gl.py と同じ場所・同じ優先順で解決する（kiro-flow.yaml には置かない）。
    優先順: connections.yaml（conn_label）→ 環境変数 GITLAB_TOKEN/GL_TOKEN → シェル rc ファイル。"""
    conn_label = str(cfg.get("conn_label") or "default")
    return (_token_from_connections(conn_label)
            or os.environ.get("GITLAB_TOKEN", "").strip()
            or os.environ.get("GL_TOKEN", "").strip()
            or _token_from_shell_files())


# --- native GitLab REST（gl.py 相当の必要処理を移植・stdlib のみ） -------------
def _parse_project_url(url: str) -> "tuple[str | None, str | None]":
    """http(s) の GitLab プロジェクト URL を (host, project_path) に分解する。
    解釈できなければ (None, None)。例: https://gitlab.com/group/sub/repo → ('gitlab.com','group/sub/repo')"""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    project = parsed.path.lstrip("/").rstrip("/")
    if project.endswith(".git"):
        project = project[:-4]
    if host and project:
        return host, project
    return None, None


def _resolve_project(cfg: dict) -> "tuple[str, str, str]":
    """起票先プロジェクトを kiro-flow.yaml の repo_url から解決する（権威・git origin へ流れない）。
    返り値 (host, project, repo_url)。未設定/解釈不能は RuntimeError。"""
    repo_url = str(cfg.get("repo_url") or "").strip()
    if not repo_url:
        raise RuntimeError(
            "gitlab executor: 起票先 URL が未設定です。kiro-flow.yaml の `gitlab.repo_url` に "
            "GitLab プロジェクト URL を設定してください（例: https://gitlab.com/group/repo）。")
    host, project = _parse_project_url(repo_url)
    if not (host and project):
        raise RuntimeError(
            f"gitlab.repo_url を GitLab プロジェクト URL として解釈できません: {repo_url}"
            "（例: https://gitlab.com/group/repo）")
    return host, project, repo_url


def _gl_headers(token: str) -> dict:
    return {"PRIVATE-TOKEN": token, "Content-Type": "application/json",
            "Accept": "application/json"}


def _encode_project(project: str) -> str:
    """namespace/repo を namespace%2Frepo に URL エンコード（API パス用）。"""
    return urllib.parse.quote(project, safe="")


def _http_error_detail(e: "urllib.error.HTTPError") -> str:
    try:
        return e.read().decode("utf-8", errors="replace")[:300]
    except Exception:  # noqa: BLE001
        return "(詳細なし)"


def gl_api(host: str, token: str, method: str, path: str,
           data: "dict | None" = None, params: "dict | None" = None):
    """GitLab REST API（v4）を 1 回叩いて JSON を返す。失敗は RuntimeError（→ failed 記録）。"""
    url = f"https://{host}/api/v4{path}"
    if params:
        url = url + "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=_gl_headers(token), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"GitLab API {method} {path} 失敗: HTTP {e.code} {e.reason} {_http_error_detail(e)}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitLab API {method} {path} へ接続できません: {e.reason}")


def gl_api_list(host: str, token: str, path: str, params: "dict | None" = None) -> list:
    """ページングする GET をすべて辿って 1 つのリストに結合して返す。"""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    results: list = []
    page = 1
    while True:
        params["page"] = page
        url = f"https://{host}/api/v4{path}?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers=_gl_headers(token), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
                page_data = json.loads(content) if content.strip() else []
                if not isinstance(page_data, list):
                    return page_data
                results.extend(page_data)
                nxt = (resp.headers.get("X-Next-Page", "") or "").strip()
                if not nxt:
                    break
                page = int(nxt)
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"GitLab API GET {path} 失敗: HTTP {e.code} {e.reason} {_http_error_detail(e)}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"GitLab API GET {path} へ接続できません: {e.reason}")
    return results


def _creator_tag() -> str:
    """gitlab-idd 規約の作成者ノード ID 隠しコメント（check-defer が作成者を識別する）。
    GITLAB_NODE_ID があれば尊重し、無ければ kiro-flow 由来の一意 ID を付す。"""
    node = os.environ.get("GITLAB_NODE_ID", "").strip() or f"kiro-flow-{uuid.uuid4().hex[:12]}"
    return f"<!-- gitlab-idd:creator-node-id:{node} -->"


# --- イシュー操作（GitLab REST v4 を直叩き） ----------------------------------
def _create_issue(host: str, token: str, project: str, title: str,
                  body: str, labels: str) -> dict:
    """イシューを起票して {iid, web_url, ...} を返す。"""
    ep = _encode_project(project)
    description = (body + "\n\n" + _creator_tag()) if body else _creator_tag()
    data = {"title": title, "description": description}
    if labels:
        data["labels"] = labels
    return gl_api(host, token, "POST", f"/projects/{ep}/issues", data=data)


def _get_issue(host: str, token: str, project: str, iid) -> dict:
    """イシュー 1 件を取得する（labels / state を含む）。"""
    ep = _encode_project(project)
    return gl_api(host, token, "GET", f"/projects/{ep}/issues/{iid}")


def _get_comments(host: str, token: str, project: str, iid) -> list:
    """イシューのコメント（notes）一覧を取得する。"""
    ep = _encode_project(project)
    res = gl_api_list(host, token, f"/projects/{ep}/issues/{iid}/notes")
    return res if isinstance(res, list) else []


def _add_issue_note(host: str, token: str, project: str, iid, body: str) -> dict:
    """イシューに完了報告コメント（note）を追記する。"""
    ep = _encode_project(project)
    return gl_api(host, token, "POST", f"/projects/{ep}/issues/{iid}/notes",
                  data={"body": body})


def _related_merge_requests(host: str, token: str, project: str, iid) -> list:
    """イシューに紐づく MR 一覧を取得する（『このイシューに関連する MR』）。"""
    ep = _encode_project(project)
    res = gl_api_list(host, token, f"/projects/{ep}/issues/{iid}/related_merge_requests")
    return res if isinstance(res, list) else []


def _get_mr(host: str, token: str, project: str, mr_iid) -> dict:
    """MR を 1 件取得する（state 確認＝冪等判定に使う）。"""
    ep = _encode_project(project)
    return gl_api(host, token, "GET", f"/projects/{ep}/merge_requests/{mr_iid}")


def _merge_mr(host: str, token: str, project: str, mr_iid) -> dict:
    """MR をマージする。既にマージ済みなら GitLab は 405/406 を返すので、呼び出し側で
    再取得して冪等に扱う（_finalize_merge を参照）。"""
    ep = _encode_project(project)
    return gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{mr_iid}/merge")


def _close_issue(host: str, token: str, project: str, iid,
                 labels: "list | None" = None) -> dict:
    """イシューを明示的にクローズする（state_event=close）。labels 指定時は同時に更新。
    GitLab では MR マージが必ずしもイシューをクローズしない（本文に `Closes #n` が無い等）ため、
    finalize はマージとは別個に必ずこのクローズを行う。"""
    ep = _encode_project(project)
    data: dict = {"state_event": "close"}
    if labels is not None:
        data["labels"] = ",".join(labels)
    return gl_api(host, token, "PUT", f"/projects/{ep}/issues/{iid}", data=data)


def _issue_body(kind: str, goal: str, dep_results: dict, repo_instruction: str = "") -> str:
    """イシュー本文（GitLab Markdown）を組み立てる。gitlab-idd 規約に従い
    『## 受け入れ条件』を必ず含める（ワーカー/レビュアーが完了判定に使う）。
    repo_instruction（成果物リポジトリの clone 指示）は『## 目的』とは別の節に置き、
    本来の goal が指示テキストで埋もれないようにする。"""
    # 集約・選別系では gate（verify 判定）を参考成果から除く（execute_kiro と同様）
    deps = dep_results
    if kind in ("reduce", "synthesize", "filter", "judge"):
        deps = {d: r for d, r in dep_results.items() if not _is_gate_result(r)}
    lines = ["## 目的", "", goal, ""]
    if repo_instruction.strip():     # 成果物リポジトリの指示は独立した節に（目的と混ぜない）
        lines += ["## 成果物リポジトリ", "", repo_instruction.strip(), ""]
    if deps:
        lines += ["## 依存タスクの成果（参考）", ""]
        for d, r in deps.items():
            lines.append(f"- **{d}**: {_dep_text(r)[:500]}")
            dv = _dep_data(r)
            if dv is not None:
                lines.append(f"  - `data`: `{json.dumps(dv, ensure_ascii=False)[:300]}`")
        lines.append("")
    lines += [
        "## 受け入れ条件", "",
        f"- [ ] 次のタスクが完了している: {goal}",
        "- [ ] 変更がブランチに push され、レビュー可能な状態（MR）になっている",
        "",
        "---",
        f"_kiro-flow ワーカーバス（kind=`{kind}`）により自動起票。"
        "完了したらレビュアーが `status:approved` を付与すると kiro-flow が完了とみなします。_",
    ]
    return "\n".join(lines)


def execute(kind: str, goal: str, dep_results: dict, model=None,
            art_dir=None, dep_arts=None, repo_instruction: str = ""):
    """opt-in のワーカーバス: タスクを GitLab イシューにして委譲し、approved を待つ。

    1. イシューを起票（status:open,assignee:any ＋ 優先度）
    2. ラベルをポーリングし、status:approved（または status:done / クローズ）で完了
    3. ワーカーの最終コメント（完了報告）を成果テキストとして取り込んで返す

    起票先プロジェクトは kiro-flow.yaml の `gitlab.repo_url` を権威に、トークンは gl.py と
    同じ場所（connections.yaml / 環境変数 / シェル rc）から解決し、GitLab REST を直叩きする。
    `goal` は本来の目的のみ（タイトル・『## 目的』に使う）。成果物リポジトリの clone 指示は
    `repo_instruction` で別途受け取り、本文の独立した節に載せる（goal を埋もれさせない）。
    """
    cfg = _config()
    # opt-in 前提チェック（誤って選んだときに無限待ちにしない）: 起票先 URL とトークンを起票前に解決。
    host, project, url_base = _resolve_project(cfg)
    token = _resolve_token(cfg)
    if not token:
        conn_label = str(cfg.get("conn_label") or "default")
        raise RuntimeError(
            "gitlab executor: GitLab トークンが見つかりません。connections.yaml の "
            f"gitlab/{conn_label}、環境変数 GITLAB_TOKEN/GL_TOKEN、または ~/.bashrc 等に "
            "設定してください（kiro-flow.yaml には置きません）。")

    title = f"[kiro-flow] {goal.strip()[:80]}"
    body = _issue_body(kind, goal, dep_results, repo_instruction)
    labels = str(cfg.get("labels") or "status:open,assignee:any")
    priority = str(cfg.get("priority") or "").strip()
    if priority:
        labels = f"{labels},{priority}"

    created = _create_issue(host, token, project, title, body, labels)

    iid = created.get("iid") or created.get("id")
    url = created.get("web_url", "")
    if not iid:
        raise RuntimeError(f"GitLab イシューの作成に失敗しました: {str(created)[:200]}")
    _log(f"イシュー #{iid} を起票し承認待ち（{url_base}）: {url}")

    approved = str(cfg.get("approved_label") or "status:approved")
    done = str(cfg.get("done_label") or "status:done")
    interval = _as_float(cfg.get("poll_interval"), 30.0)
    timeout = _as_float(cfg.get("timeout"), 0.0)
    deadline = (time.time() + timeout) if timeout > 0 else None

    labels_now: set = set()
    while True:
        issue = _get_issue(host, token, project, iid)
        labels_now = set(issue.get("labels") or [])
        state = issue.get("state")
        if approved in labels_now or done in labels_now or state == "closed":
            break
        if deadline is not None and time.time() >= deadline:
            raise RuntimeError(
                f"イシュー #{iid} が {timeout:.0f}s 以内に {approved} になりませんでした（{url}）")
        time.sleep(max(0.0, interval))

    # ワーカーの最終コメント（完了報告）を成果として取り込む（ベストエフォート）
    report = ""
    try:
        comments = _get_comments(host, token, project, iid)
        if isinstance(comments, list) and comments:
            report = str(comments[-1].get("body") or "").strip()
    except Exception:  # noqa: BLE001 — コメント取得失敗は致命的ではない
        report = ""

    text = f"[gitlab] イシュー #{iid} approved（{url}）"
    if report:
        text += "\n\n" + report[:1000]
    data = {"issue_iid": iid, "web_url": url,
            "labels": sorted(labels_now), "approved": True}
    return text, data


def finalize(delivery: dict, action: str = "merge") -> dict:
    """verify 通過後に呼ばれる決定的な納品アクション（kiro-flow finalize から委譲）。

    対称性の要点: gitlab executor では `status:approved` は『worker＋人の内容レビュー完了』
    （＝提案 = レビュー可能な MR が用意された状態）であって、done ではない。done は
    kiro-autonomous の verify（exit 0）だけが確定する（不変条件）。verify が通った帰結として、
    このアクションが **MR をマージ** し、**イシューを明示的にクローズ** する。人が手で
    マージするのではなく、verify 通過後にシステムが決定的に行う。

    GitLab では MR マージが必ずしもイシューをクローズしない（本文に `Closes #n` が無い等）ため、
    マージとクローズは **別個に** 行う（ご要望どおりイシューまでケアする）。

    冪等: 既にマージ済み MR / クローズ済みイシューでも成功扱いにする（人が先に手動マージ
    していても安全。`already` フラグで区別）。

    引数:
        delivery: ノードの result.data（execute が返したもの）。`issue_iid` を読む。
        action:   "merge"（MR マージ＋クローズ・既定）/ "close"（マージせずクローズのみ）。
    返り値: {issue_iid, merged, issue_closed, mr_iid, mr_url, already, action}
    """
    delivery = delivery or {}
    iid = delivery.get("issue_iid")
    out = {"issue_iid": iid, "merged": False, "issue_closed": False,
           "mr_iid": None, "mr_url": None, "already": False, "action": action}
    if not iid:
        return out  # gitlab へ委譲していないノード（kiro 等）→ no-op

    cfg = _config()
    host, project, _ = _resolve_project(cfg)
    token = _resolve_token(cfg)
    if not token:
        raise RuntimeError("gitlab finalize: GitLab トークンが見つかりません。")

    if action == "merge":
        _finalize_merge(host, token, project, iid, out)

    # --- イシューを明示的にクローズ（MR マージ≠クローズなので必ず別個に行う） ---
    done_label = str(cfg.get("done_label") or "status:done")
    note = "kiro-flow: verify 通過により納品を確定します。"
    if out["merged"]:
        note += f" MR !{out['mr_iid']} を{'（既に）' if out['already'] else ''}マージしました。"
    note += " このイシューをクローズします。"
    try:
        _add_issue_note(host, token, project, iid, note)
    except RuntimeError:
        pass  # コメント追記失敗は致命的でない（クローズは続行）

    issue = _get_issue(host, token, project, iid)
    if issue.get("state") == "closed":
        out["issue_closed"] = True
        out["already"] = True
    else:
        labels = sorted(set(issue.get("labels") or []) | {done_label})
        closed = _close_issue(host, token, project, iid, labels)
        out["issue_closed"] = closed.get("state") == "closed"
    _log(f"finalize: イシュー #{iid} をクローズ"
         + (f" / MR !{out['mr_iid']} マージ" if out["merged"] else "（マージ対象 MR なし）"))
    return out


def _finalize_merge(host: str, token: str, project: str, iid, out: dict) -> None:
    """イシューに紐づく MR を冪等にマージし、結果を out に書き込む。"""
    mrs = _related_merge_requests(host, token, project, iid)
    # open な MR を優先、無ければ先頭（既にマージ済みでも state を確認して冪等に扱う）
    mr = next((m for m in mrs if m.get("state") == "opened"), (mrs[0] if mrs else None))
    if not mr:
        return  # MR がまだ無い（worker が作らなかった）→ マージはスキップしクローズのみ
    mr_iid = mr.get("iid")
    out["mr_iid"] = mr_iid
    out["mr_url"] = mr.get("web_url", "")
    if mr.get("state") == "merged":
        out["merged"] = True
        out["already"] = True
        return
    try:
        merged = _merge_mr(host, token, project, mr_iid)
        out["merged"] = (merged.get("state") == "merged") if isinstance(merged, dict) else True
    except RuntimeError:
        # 既にマージ済み等で API がエラーを返した可能性 → 再取得して冪等判定
        cur = _get_mr(host, token, project, mr_iid)
        if cur.get("state") == "merged":
            out["merged"] = True
            out["already"] = True
        else:
            raise


if __name__ == "__main__":
    # 手動デバッグ用: 単体実行すると起票先・トークン解決結果を表示する。
    _cfg = _config()
    try:
        _host, _project, _repo_url = _resolve_project(_cfg)
        print(f"起票先: host={_host} project={_project}（repo_url={_repo_url}）")
    except RuntimeError as e:
        print(f"起票先: 解決エラー: {e}")
    print("token:", "あり" if _resolve_token(_cfg)
          else "なし（connections.yaml / 環境変数 / シェル rc を確認）")
    print("config:", json.dumps(_cfg, ensure_ascii=False))
