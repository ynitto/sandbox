#!/usr/bin/env python3
"""gitlab — kiro-flow の executor プラグイン（opt-in のワーカーバス）

kiro-loop の event_hook と同じ流儀で、kiro-flow 本体から importlib で動的にロードされ、
`execute()` が呼び出される。タスクを **GitLab イシュー** にして委譲し、リモートの
（別マシン/別人の）ワーカーが拾って実装する。kiro-flow はイシューをポーリングし、
レビュアーが `status:approved` を付ける（= 受け入れ承認）まで待って完了とみなす。
ローカルに kiro-cli が無くても、GitLab 越しに作業を委譲できる。

プラグイン契約:
    execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None) -> (text, data)

イシュー API のバックエンド（自動選択）:
    - **native**（既定・優先）: `kiro-flow.yaml` の `gitlab.repo_url` が設定され、トークンも
      解決できるとき、GitLab REST API（v4）を **stdlib だけで直叩き**する（gl.py 相当の
      create-issue / get-issue / get-comments を移植）。**起票先プロジェクトは repo_url を
      そのまま使う**ため、git remote origin 等への曖昧なフォールバックが無く確実。
      外部の gitlab-idd スキル（gl.py）が無くても動く。
    - **gl**（フォールバック）: repo_url かトークンが欠けるときだけ、従来どおり gitlab-idd
      スキルの `gl.py` へ委譲する（repo_url 指定時は `GL_PROJECT_URL` で確実に渡す）。

設定の渡し方（優先度: 個別環境変数 > KIRO_FLOW_EXECUTOR_CONFIG(JSON) > 既定）:
    - kiro-flow 本体は設定ファイルの `gitlab:` ブロックを JSON 化して環境変数
      `KIRO_FLOW_EXECUTOR_CONFIG` で渡す。
    - 個別の上書きは `KIRO_FLOW_GITLAB_<KEY>`（例: KIRO_FLOW_GITLAB_POLL_INTERVAL）。
    - `repo_url`（kiro-flow.yaml の `gitlab.repo_url`）が起票先プロジェクト URL の権威。
      native でも gl フォールバックでも、設定されていれば必ずこの URL が使われる。
    - トークンは `gitlab.token`（設定）/ 環境変数 `GITLAB_TOKEN` / `GL_TOKEN` から解決する
      （秘密情報なので環境変数推奨）。native はトークンが必須。

※ ポーリングするのは kiro-flow（Python プロセス）であって LLM セッションではない。
   gitlab-idd の「LLM ポーリング禁止」はワーカー/レビュアー LLM への指針で、ここでの
   定期確認とは別物。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

NAME = "gitlab"

# プラグインの既定設定（kiro-flow の CONFIG_DEFAULTS["gitlab"] と同値）。
_DEFAULTS = {
    "conn_label": "default",
    "repo_url": "",
    "token": "",
    "labels": "status:open,assignee:any",
    "priority": "priority:normal",
    "poll_interval": 30.0,
    "timeout": 86400.0,
    "approved_label": "status:approved",
    "done_label": "status:done",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


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


# --- gl.py の探索・実行（gitlab-idd スキル同梱） ------------------------------
def _find_gl_script():
    """gitlab-idd スキルの scripts/gl.py を探す。
    検索順: .github/skills/ → git root/.github/skills/ → ~/.kiro/skills/ → skill_home。"""
    candidates = []
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, ".github", "skills", "gitlab-idd", "scripts", "gl.py"))
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
        if root:
            candidates.append(os.path.join(root, ".github", "skills", "gitlab-idd", "scripts", "gl.py"))
    except Exception:  # noqa: BLE001
        pass
    candidates.append(os.path.join(os.path.expanduser("~/.kiro/skills"),
                                   "gitlab-idd", "scripts", "gl.py"))
    for agent_dir in [os.path.expanduser("~/.kiro"), os.path.expanduser("~/.copilot"),
                      os.path.expanduser("~/.claude"), os.path.expanduser("~/.codex")]:
        reg = os.path.join(agent_dir, "skill-registry.json")
        if os.path.isfile(reg):
            try:
                with open(reg, encoding="utf-8") as f:
                    home = json.load(f).get("skill_home", "")
                if home:
                    candidates.append(os.path.join(home, "gitlab-idd", "scripts", "gl.py"))
            except Exception:  # noqa: BLE001
                pass
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def run_gl(subargs, conn_label: str = "default", parse_json: bool = True,
           repo_url: str = ""):
    """gitlab-idd の gl.py を 1 回呼び出す。parse_json=True なら出力 JSON を返す。
    gl.py が見つからない / 失敗したときは RuntimeError を送出する（結果は failed 記録）。

    repo_url を指定すると、gl.py がそのリポジトリ（プロジェクト）を対象にするよう
    環境変数 GL_PROJECT_URL で渡す。connections.yaml や git remote origin より優先される。"""
    script = _find_gl_script()
    if script is None:
        raise RuntimeError(
            "gitlab executor には gitlab-idd スキルの scripts/gl.py が必要です。"
            "スキルを導入し connections.yaml で接続を設定してください（opt-in）。")
    cmd = [sys.executable, script, "--label-conn", conn_label] + list(subargs)
    env = os.environ.copy()
    if repo_url:
        env["GL_PROJECT_URL"] = repo_url
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gl.py タイムアウト: {' '.join(list(subargs)[:2])}")
    if proc.returncode != 0:
        raise RuntimeError(f"gl.py 失敗 ({subargs[0]}): {proc.stderr.strip()[:300]}")
    out_text = _strip_ansi(proc.stdout).strip()
    if not parse_json:
        return out_text
    try:
        return json.loads(out_text) if out_text else {}
    except json.JSONDecodeError:
        raise RuntimeError(f"gl.py の出力を JSON として解釈できません: {out_text[:200]}")


# --- native GitLab REST（gl.py 相当の必要処理を移植・stdlib のみ） -------------
# create-issue / get-issue / get-comments を gl.py に頼らず直接叩く。起票先プロジェクトは
# kiro-flow.yaml の repo_url から確実に解決し、git remote origin へはフォールバックしない。
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


def _resolve_token(cfg: dict) -> str:
    """トークンを 設定 gitlab.token ＜ 環境変数 GITLAB_TOKEN / GL_TOKEN の順で解決する。"""
    return (str(cfg.get("token") or "").strip()
            or os.environ.get("GITLAB_TOKEN", "").strip()
            or os.environ.get("GL_TOKEN", "").strip())


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


def _resolve_backend(cfg: dict) -> dict:
    """イシュー API のバックエンドを決める。
    repo_url（kiro-flow.yaml）とトークンが揃えば native（REST 直叩き・gl.py 不要）。
    どちらか欠ければ gl（gl.py 委譲・repo_url は GL_PROJECT_URL で渡す）。"""
    repo_url = str(cfg.get("repo_url") or "").strip()
    conn = str(cfg.get("conn_label") or "default")
    token = _resolve_token(cfg)
    if repo_url and token:
        host, project = _parse_project_url(repo_url)
        if not (host and project):
            raise RuntimeError(
                f"gitlab.repo_url を GitLab プロジェクト URL として解釈できません: {repo_url}"
                "（例: https://gitlab.com/group/repo）")
        return {"mode": "native", "host": host, "project": project,
                "token": token, "conn": conn, "repo_url": repo_url}
    return {"mode": "gl", "conn": conn, "repo_url": repo_url}


# --- バックエンド非依存のイシュー操作（native / gl を内部で切り替える） --------
def _create_issue(be: dict, title: str, body: str, labels: str) -> dict:
    """イシューを起票して {iid, web_url, ...} 相当を返す。"""
    if be["mode"] == "native":
        ep = _encode_project(be["project"])
        description = (body + "\n\n" + _creator_tag()) if body else _creator_tag()
        data = {"title": title, "description": description}
        if labels:
            data["labels"] = labels
        return gl_api(be["host"], be["token"], "POST", f"/projects/{ep}/issues", data=data)
    # gl フォールバック: 本文は argv 長制限を避けてファイル経由で渡す
    fd, body_file = tempfile.mkstemp(prefix="kiro-flow-issue-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        return run_gl(["create-issue", "--title", title, "--body-file", body_file,
                       "--labels", labels], be["conn"], repo_url=be["repo_url"])
    finally:
        with contextlib.suppress(OSError):
            os.remove(body_file)


def _get_issue(be: dict, iid) -> dict:
    """イシュー 1 件を取得する（labels / state を含む）。"""
    if be["mode"] == "native":
        ep = _encode_project(be["project"])
        return gl_api(be["host"], be["token"], "GET", f"/projects/{ep}/issues/{iid}")
    return run_gl(["get-issue", str(iid)], be["conn"], repo_url=be["repo_url"])


def _get_comments(be: dict, iid) -> list:
    """イシューのコメント（notes）一覧を取得する。"""
    if be["mode"] == "native":
        ep = _encode_project(be["project"])
        return gl_api_list(be["host"], be["token"], f"/projects/{ep}/issues/{iid}/notes")
    res = run_gl(["get-comments", str(iid)], be["conn"], repo_url=be["repo_url"])
    return res if isinstance(res, list) else []


def _issue_body(kind: str, goal: str, dep_results: dict) -> str:
    """イシュー本文（GitLab Markdown）を組み立てる。gitlab-idd 規約に従い
    『## 受け入れ条件』を必ず含める（ワーカー/レビュアーが完了判定に使う）。"""
    # 集約・選別系では gate（verify 判定）を参考成果から除く（execute_kiro と同様）
    deps = dep_results
    if kind in ("reduce", "synthesize", "filter", "judge"):
        deps = {d: r for d, r in dep_results.items() if not _is_gate_result(r)}
    lines = ["## 目的", "", goal, ""]
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
            art_dir=None, dep_arts=None):
    """opt-in のワーカーバス: タスクを GitLab イシューにして委譲し、approved を待つ。

    1. イシューを起票（status:open,assignee:any ＋ 優先度）
    2. ラベルをポーリングし、status:approved（または status:done / クローズ）で完了
    3. ワーカーの最終コメント（完了報告）を成果テキストとして取り込んで返す

    起票先プロジェクトは kiro-flow.yaml の `gitlab.repo_url` を権威とし、トークンも揃えば
    GitLab REST を直叩き（native）、欠ければ gl.py へ委譲（gl）する（`_resolve_backend`）。
    """
    cfg = _config()
    be = _resolve_backend(cfg)
    # opt-in 前提チェック: 誤って選んだときに無限待ちにしないため、起票前に到達可能性を確かめる。
    # native はトークン＋repo_url で自己完結。gl フォールバックは gl.py が無ければ即失敗。
    if be["mode"] == "gl" and _find_gl_script() is None:
        raise RuntimeError(
            "gitlab executor: 起票先を解決できません。kiro-flow.yaml の `gitlab.repo_url` と"
            "トークン（`gitlab.token` または環境変数 GITLAB_TOKEN）を設定するか、"
            "フォールバック用に gitlab-idd スキルの scripts/gl.py を導入してください（opt-in）。")

    title = f"[kiro-flow] {goal.strip()[:80]}"
    body = _issue_body(kind, goal, dep_results)
    labels = str(cfg.get("labels") or "status:open,assignee:any")
    priority = str(cfg.get("priority") or "").strip()
    if priority:
        labels = f"{labels},{priority}"

    created = _create_issue(be, title, body, labels)

    iid = created.get("iid") or created.get("id")
    url = created.get("web_url", "")
    if not iid:
        raise RuntimeError(f"GitLab イシューの作成に失敗しました: {str(created)[:200]}")
    _log(f"イシュー #{iid} を起票し承認待ち（{be['mode']}）: {url}")

    approved = str(cfg.get("approved_label") or "status:approved")
    done = str(cfg.get("done_label") or "status:done")
    interval = _as_float(cfg.get("poll_interval"), 30.0)
    timeout = _as_float(cfg.get("timeout"), 0.0)
    deadline = (time.time() + timeout) if timeout > 0 else None

    labels_now: set = set()
    while True:
        issue = _get_issue(be, iid)
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
        comments = _get_comments(be, iid)
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


if __name__ == "__main__":
    # 手動デバッグ用: 単体実行すると解決結果（バックエンド・起票先・gl.py）を表示する。
    _cfg = _config()
    try:
        _be = _resolve_backend(_cfg)
        if _be["mode"] == "native":
            print(f"backend: native（host={_be['host']} project={_be['project']}）")
        else:
            print(f"backend: gl（repo_url={_be.get('repo_url') or '(未設定)'}）")
    except RuntimeError as e:
        print(f"backend: 解決エラー: {e}")
    print("token:", "あり" if _resolve_token(_cfg) else "なし")
    print("gl.py:", _find_gl_script() or "(見つかりません)")
    _safe_cfg = {k: ("***" if k == "token" and v else v) for k, v in _cfg.items()}
    print("config:", json.dumps(_safe_cfg, ensure_ascii=False))
