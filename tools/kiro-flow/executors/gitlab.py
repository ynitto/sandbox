#!/usr/bin/env python3
"""gitlab — kiro-flow の executor プラグイン（opt-in のワーカーバス）

kiro-loop の event_hook と同じ流儀で、kiro-flow 本体から importlib で動的にロードされ、
`execute()` が呼び出される。タスクを **GitLab イシュー** にして委譲し、リモートの
（別マシン/別人の）ワーカーが拾って実装する。kiro-flow はイシューをポーリングし、
レビュアーが `status:approved` を付ける（= 受け入れ承認）まで待って完了とみなす。
ローカルに kiro-cli が無くても、GitLab 越しに作業を委譲できる。

プラグイン契約:
    execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None,
            repo_instruction="", workspace=None, references=None) -> (text, data)
    ※ goal は本来の目的のみ。workspace（その run の唯一の書込先 spec dict: url/path/base/target）は
      起票先プロジェクトの解決とイシューの『## 対象リポジトリ』節に使う。references（参照リポジトリ
      spec の列・読むだけ）は『## 参照リポジトリ』節に載せる。repo_instruction はローカルエージェント
      向けの指示なのでイシューには使わない。

イシュー API は **GitLab REST API（v4）を stdlib だけで直叩き**する（gl.py 相当の
create-issue / get-issue / get-comments を移植）。外部の gitlab-idd スキル（gl.py）の
起動は不要で、gl.py へのフォールバックも行わない。

起票先プロジェクト URL とトークンの解決:
    - **起票先 URL**: その run の唯一の書込先である **ワークスペース URL** が優先。無ければ
      kiro-flow.yaml の `gitlab.repo_url` をフォールバックに使う（git remote origin 等への
      曖昧なフォールバックは無い＝誤起票を防ぐ）。
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

import hashlib
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
    # 完了＝人がマージ＝イシュークローズ。人の確認は時間がかかるため待機は長めにする（0=無限）。
    "timeout": 604800.0,            # 全体タイムアウト（既定 7 日）。クローズに達するまでの上限
    "approved_timeout": 1209600.0,  # status:approved/status:done 検知後の猶予（既定 14 日・人のマージ待ち）
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
    """GitLab プロジェクト URL を (host, project_path) に分解する。http(s) と SSH 両形に対応。
    解釈できなければ (None, None)。
      https://gitlab.com/group/sub/repo.git → ('gitlab.com','group/sub/repo')
      git@gitlab.com:group/repo.git         → ('gitlab.com','group/repo')"""
    url = (url or "").strip()
    # SSH 形: [user@]host:group/project(.git)
    m = re.match(r"^(?:[^@/]+@)?([^/:]+):(.+)$", url) if "://" not in url else None
    if m:
        host, project = m.group(1), m.group(2).strip("/")
    else:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        project = parsed.path.lstrip("/").rstrip("/")
    if project.endswith(".git"):
        project = project[:-4]
    if host and project:
        return host, project
    return None, None


def _resolve_project(cfg: dict, workspace_url: str = "") -> "tuple[str, str, str]":
    """起票先プロジェクトを解決する。優先順は **ワークスペース URL（その run の唯一の書込先）** →
    kiro-flow.yaml の `gitlab.repo_url`（フォールバック）。返り値 (host, project, repo_url)。
    どちらも未設定/解釈不能なら RuntimeError。"""
    repo_url = str(workspace_url or "").strip() or str(cfg.get("repo_url") or "").strip()
    if not repo_url:
        raise RuntimeError(
            "gitlab executor: 起票先 URL が未設定です。kiro-flow へ --workspace を渡すか、"
            "kiro-flow.yaml の `gitlab.repo_url` に GitLab プロジェクト URL を設定してください"
            "（例: https://gitlab.com/group/repo）。")
    host, project = _parse_project_url(repo_url)
    if not (host and project):
        raise RuntimeError(
            f"起票先 URL を GitLab プロジェクト URL として解釈できません: {repo_url}"
            "（例: https://gitlab.com/group/repo / git@gitlab.com:group/repo.git）")
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


# --- 冪等性（再 claim 時の二重起票防止） --------------------------------------
#
# worker が長時間 MR の決着を待っている最中に夜間停止などで殺されると、result が
# 書かれないまま claim の lease が失効し、タスクは pending に戻って別の（リモートの）
# worker に再 claim される。そのとき execute が無条件にイシューを起票すると、同じ
# タスクのイシューが二重に立ってしまう。これを防ぐため、タスクごとに決定的なトークンを
# イシュー本文へ隠しマーカーとして埋め込み、起票前に「同じトークンを持つ open イシュー」を
# 検索して見つかれば再アタッチ（ポーリング再開）する。
def _task_token(art_dir: "str | None") -> "str | None":
    """art_dir（`runs/<run_id>/artifacts/<node_id>`）から (run_id, node_id) を割り出し、
    決定的な検索トークン `kf-<hex12>` を作る。再 claim でも同じ art_dir が渡るため同一トークンに
    なり、既存イシューへ再アタッチできる。想定形でなければ None（＝従来どおり毎回新規起票）。"""
    if not art_dir:
        return None
    parts = os.path.normpath(str(art_dir)).split(os.sep)
    try:
        i = len(parts) - 1 - parts[::-1].index("artifacts")
    except ValueError:
        return None
    node_id = parts[i + 1] if i + 1 < len(parts) else ""
    run_id = parts[i - 1] if i - 1 >= 0 else ""
    if not node_id:
        return None
    return "kf-" + hashlib.sha1(f"{run_id}/{node_id}".encode("utf-8")).hexdigest()[:12]


def _task_marker(task_token: str) -> str:
    """イシュー本文に埋め込む隠しマーカー（検索とマッチ検証に使う）。"""
    return f"<!-- kiro-flow:task-token:{task_token} -->"


def _find_open_issue_by_token(host: str, token: str, project: str,
                              task_token: str) -> "tuple | None":
    """同じタスクトークンを本文に持つ **open** イシューを探し、(iid, web_url) を返す。
    見つからなければ None。検索 API の取りこぼしや別タスクの誤ヒットを避けるため、検索後に
    マーカー文字列が description に実在することを必ず検証する。"""
    ep = _encode_project(project)
    try:
        issues = gl_api_list(host, token, f"/projects/{ep}/issues",
                             params={"state": "opened", "search": task_token})
    except RuntimeError:
        return None                                    # 検索失敗時は安全側（新規起票）へ倒す
    marker = _task_marker(task_token)
    hits = []
    for it in issues if isinstance(issues, list) else []:
        if marker in str(it.get("description") or ""):
            iid = it.get("iid") or it.get("id")
            if iid is not None:
                hits.append((iid, it.get("web_url", "")))
    # 万一複数あれば最小 iid（最初に起票されたもの）へ決定的に再アタッチする
    return min(hits, key=lambda h: h[0]) if hits else None


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


def _related_merge_requests(host: str, token: str, project: str, iid) -> list:
    """イシューに紐づく MR 一覧を取得する（人が管理する『関連 MR』）。各要素は state を持つ
    （opened / closed / merged / locked）。完了/却下の判定に使う。"""
    ep = _encode_project(project)
    res = gl_api_list(host, token, f"/projects/{ep}/issues/{iid}/related_merge_requests")
    return res if isinstance(res, list) else []


def _close_issue(host: str, token: str, project: str, iid,
                 labels: "list | None" = None) -> dict:
    """イシューを明示的にクローズする（state_event=close）。labels 指定時は同時に更新。
    kiro-flow は MR を**マージしない**（人が管理）。承認（全マージ）/却下（未マージクローズ）の
    どちらの決着でも、後始末としてこのイシューをクローズするだけ。"""
    ep = _encode_project(project)
    data: dict = {"state_event": "close"}
    if labels is not None:
        data["labels"] = ",".join(labels)
    return gl_api(host, token, "PUT", f"/projects/{ep}/issues/{iid}", data=data)


def _human_comments(host: str, token: str, project: str, iid) -> str:
    """イシューの**人間のコメント**を新しい順に連結して返す（却下時のやり直し指示に活かす）。
    GitLab の system note（ラベル変更等の自動記録）と、kiro-flow が付けた creator-node-id タグを含む
    自動コメントは除外する。人のコメントが無ければ空文字（呼び出し側は『自動で判断』に倒す）。"""
    try:
        notes = _get_comments(host, token, project, iid)
    except RuntimeError:
        return ""
    out = []
    for n in reversed(notes if isinstance(notes, list) else []):
        if n.get("system"):
            continue                                   # ラベル変更等の自動記録は除外
        body = str(n.get("body") or "").strip()
        if not body or "gitlab-idd:creator-node-id" in body or body.startswith("kiro-flow:"):
            continue                                   # kiro-flow 自身の自動コメントは除外
        out.append(body)
    return "\n\n".join(out)[:2000]


def _workspace_section(workspace: "dict | None") -> "list[str]":
    """対象リポジトリ節（GitLab Markdown）を構造化 workspace から組み立てる。
    リモートの人間ワーカー向けなので、ローカルの clone パス（作業ディレクトリ）は載せない。
    各項目は Markdown の箇条書き（`- **key**: value`）にして、レイアウト崩れを防ぐ。"""
    if not workspace or not workspace.get("url"):
        return []
    base = workspace.get("base") or ""
    target = workspace.get("target") or base
    lines = ["## 対象リポジトリ", "", f"- **リポジトリ**: {workspace['url']}"]
    if workspace.get("path"):
        lines.append(f"- **変更対象フォルダ**: `{workspace['path']}` 配下のみ")
    if base:
        br = f"- **作業ブランチ**: `{base}` から分岐"
        if target and target != base:
            br += f"し、`{target}` へ MR"
        lines.append(br)
    if workspace.get("desc"):
        lines.append(f"- **役割**: {workspace['desc']}")
    lines.append("")
    return lines


def _references_section(references: "list[dict] | None") -> "list[str]":
    """参照リポジトリ節（GitLab Markdown）。読むだけ・書き込まないリポジトリを箇条書きで載せる。"""
    refs = [r for r in (references or []) if r.get("url")]
    if not refs:
        return []
    lines = ["## 参照リポジトリ", "", "_読み取り専用。変更・push はしない。必要に応じて内容を参照する。_", ""]
    for r in refs:
        tags = []
        if r.get("path"):
            tags.append(f"フォルダ `{r['path']}`")
        if r.get("base"):
            tags.append(f"ブランチ `{r['base']}`")
        line = f"- **{r['url']}**" + ("（" + "・".join(tags) + "）" if tags else "")
        if r.get("desc"):
            line += f": {r['desc']}"
        lines.append(line)
    lines.append("")
    return lines


def _issue_body(kind: str, goal: str, dep_results: dict,
                workspace: "dict | None" = None,
                references: "list[dict] | None" = None) -> str:
    """イシュー本文（GitLab Markdown）を組み立てる。gitlab-idd 規約に従い
    『## 受け入れ条件』を必ず含める（ワーカー/レビュアーが完了判定に使う）。
    対象リポジトリ（書込先）は構造化 workspace から、参照リポジトリは references から、
    それぞれ『## 目的』とは別の節として整形して載せる（goal が埋もれず Markdown も崩れない）。"""
    # 集約・選別系では gate（verify 判定）を参考成果から除く（execute_kiro と同様）
    deps = dep_results
    if kind in ("reduce", "synthesize", "filter", "judge"):
        deps = {d: r for d, r in dep_results.items() if not _is_gate_result(r)}
    lines = ["## 目的", "", goal, ""]
    lines += _workspace_section(workspace)
    lines += _references_section(references)
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
        "- [ ] 変更を **MR** にして push し、レビュー可能にする（複数 MR 可）",
        "- [ ] レビュー後、**人が関連 MR を管理**する: 採用するなら**マージ**、却下するなら**未マージのままクローズ**",
        "",
        "---",
        f"_kiro-flow ワーカーバス（kind=`{kind}`）により自動起票。完了判定は**関連 MR の状態**で行う:_"
        "\n_・関連 MR が**すべてマージ**された → 承認とみなし、このイシューをクローズして完了。_"
        "\n_・関連 MR が**一つでも未マージでクローズ**された → 却下とみなし、やり直す"
        "（このイシューのコメントをやり直しの指示に使う）。kiro-flow は MR を自動マージしません。_"
        "\n_・MR で決着がつかないまま**このイシューが外部でクローズ**された場合は、"
        "`status:approved`/`status:done` ラベルやコメントの内容（承認/却下の語）から判断する。"
        "判断材料が無いクローズは取り下げ＝却下として扱う。_",
    ]
    return "\n".join(lines)


def execute(kind: str, goal: str, dep_results: dict, model=None,
            art_dir=None, dep_arts=None, repo_instruction: str = "",
            workspace: "dict | None" = None, references: "list[dict] | None" = None):
    """opt-in のワーカーバス: タスクを GitLab イシューにして委譲し、**人が関連 MR を管理**するのを待つ。

    1. イシューを起票（status:open,assignee:any ＋ 優先度）。ただし**冪等**で、同じタスク
       （art_dir 由来の決定的トークン）の open イシューが既にあれば新規起票せず再アタッチする
       （worker が夜間停止などで殺され lease 失効後に再 claim されても二重起票しない）。
    2. **関連 MR の状態**をポーリングして決着を待つ（executor 内で完結）:
       - 関連 MR が**すべてマージ** → 承認。イシューをクローズ（status:done）して **成功** を返す。
         （verify はこの後 kiro-autonomous が downstream で実施する。）
       - 関連 MR が**一つでも未マージでクローズ** → 却下。**人のコメント**を取り込み（無ければ空＝
         呼び出し側が自動判断）、元イシューをクローズして **RuntimeError（[gitlab-reject] …）** を送出。
         上位（kiro-autonomous）の通常リトライがコメントを活かして再委譲する。
       - MR がまだ open のうちは待機。

    タイムアウト（人の確認は時間がかかるため長め・設定可能。0 で無限）:
      - `timeout`（既定 7 日）… 全体上限。
      - `approved_timeout`（既定 14 日）… MR 出現または `status:approved`/`status:done` 検知後の猶予
        （人が能動的に作業中とみなして長く待つ）。

    起票先プロジェクトは **ワークスペース URL（その run の唯一の書込先）** を優先し、無ければ
    kiro-flow.yaml の `gitlab.repo_url` をフォールバックに解決する。トークンは gl.py と同じ場所
    （connections.yaml / 環境変数 / シェル rc）から解決し、GitLab REST を直叩きする。`goal` は
    本来の目的のみ（タイトル・『## 目的』に使う）。対象リポジトリは構造化 `workspace`（url/path/base/
    target/desc）から人間ワーカー向けに整形して別節に載せる（ローカルの clone パスは載せない。
    `repo_instruction` はローカルエージェント向けの指示なのでイシューには使わない）。
    """
    cfg = _config()
    # opt-in 前提チェック（誤って選んだときに無限待ちにしない）: 起票先 URL とトークンを起票前に解決。
    workspace_url = str((workspace or {}).get("url") or "")
    host, project, url_base = _resolve_project(cfg, workspace_url)
    token = _resolve_token(cfg)
    if not token:
        conn_label = str(cfg.get("conn_label") or "default")
        raise RuntimeError(
            "gitlab executor: GitLab トークンが見つかりません。connections.yaml の "
            f"gitlab/{conn_label}、環境変数 GITLAB_TOKEN/GL_TOKEN、または ~/.bashrc 等に "
            "設定してください（kiro-flow.yaml には置きません）。")

    # タスクトークン（art_dir 由来・決定的）。再 claim 時はまず同じトークンの open イシューを
    # 探し、あれば**再アタッチ**して二重起票を防ぐ。無ければマーカーを埋め込んで新規起票する。
    task_token = _task_token(art_dir)
    iid = url = None
    if task_token:
        found = _find_open_issue_by_token(host, token, project, task_token)
        if found:
            iid, url = found
            _log(f"既存の open イシュー #{iid} に再アタッチ（二重起票を回避, token={task_token}）: {url}")

    if iid is None:
        title = f"[kiro-flow] {goal.strip()[:80]}"
        body = _issue_body(kind, goal, dep_results, workspace, references)
        if task_token:
            body = f"{body}\n\n{_task_marker(task_token)}"
        labels = str(cfg.get("labels") or "status:open,assignee:any")
        priority = str(cfg.get("priority") or "").strip()
        if priority:
            labels = f"{labels},{priority}"

        created = _create_issue(host, token, project, title, body, labels)

        iid = created.get("iid") or created.get("id")
        url = created.get("web_url", "")
        if not iid:
            raise RuntimeError(f"GitLab イシューの作成に失敗しました: {str(created)[:200]}")
        _log(f"イシュー #{iid} を起票し関連 MR の決着待ち（{url_base}）: {url}")

    return _wait_for_decision(host, token, project, iid, url, cfg)


def _wait_for_decision(host, token, project, iid, url, cfg):
    """イシュー #iid の関連 MR の状態をポーリングし、承認（全マージ）/却下（未マージクローズ）の
    決着まで待つ。新規起票でも既存イシューへの再アタッチでも同じこのループで待機する。"""
    approved = str(cfg.get("approved_label") or "status:approved")
    done_label = str(cfg.get("done_label") or "status:done")
    interval = _as_float(cfg.get("poll_interval"), 30.0)
    timeout = _as_float(cfg.get("timeout"), 0.0)
    approved_timeout = _as_float(cfg.get("approved_timeout"), 0.0)
    deadline = (time.time() + timeout) if timeout > 0 else None
    active_seen = False  # MR 出現 or approved/done ラベル＝人が能動的に作業中

    while True:
        issue = _get_issue(host, token, project, iid)
        labels_now = set(issue.get("labels") or [])
        issue_closed = issue.get("state") == "closed"
        mrs = _related_merge_requests(host, token, project, iid)
        states = [str(m.get("state") or "") for m in mrs]
        # まず関連 MR の状態だけで判定する（全マージ＝承認 / 未マージクローズ＝却下）。
        decision = _mr_decision(states)
        reason = ""
        # MR で決着がつかないままイシューが**外部でクローズ**されたら、ラベル→コメントの順で
        # 承認/却下を推定し、タスクグラフに反映する（done なら下流へ、却下なら上位がやり直す）。
        if not decision and issue_closed:
            decision, reason = _closed_issue_decision(
                host, token, project, iid, labels_now, approved, done_label)

        if decision == "approved":
            return _finish_approved(host, token, project, iid, url, mrs,
                                    labels_now, done_label, reason)
        if decision == "rejected":
            _raise_rejected(host, token, project, iid, url, mrs, labels_now, done_label, reason)

        # まだ決着せず（MR が open / 未作成）。人が動き出したら長い猶予へ切り替える。
        if not active_seen and (mrs or approved in labels_now or done_label in labels_now):
            active_seen = True
            deadline = (time.time() + approved_timeout) if approved_timeout > 0 else None
            _log(f"イシュー #{iid}: 人の作業を検知（MR {len(mrs)} 件 / ラベル）。"
                 f"決着待ちの猶予を延長（{approved_timeout:.0f}s, 0=無限）")
        if deadline is not None and time.time() >= deadline:
            phase = "MR の決着（全マージ/却下クローズ）" if active_seen else "レビュー/MR 作成"
            raise RuntimeError(f"イシュー #{iid} が期限内に {phase} に至りませんでした（{url}）")
        time.sleep(max(0.0, interval))


def _mr_decision(states: "list[str]") -> str:
    """関連 MR の状態だけから決着を判定する（イシューの外部クローズは _closed_issue_decision が扱う）。
      - "approved": MR が 1 つ以上ありすべて merged（人が全採用）。
      - "rejected": MR に未マージの closed が 1 つでもある（人が却下）。
      - "": 未決着（open な MR がある / MR 未作成）。"""
    opened = [s for s in states if s in ("opened", "locked")]
    closed_unmerged = [s for s in states if s == "closed"]
    merged = [s for s in states if s == "merged"]
    if opened:
        return ""                                   # 人がまだ作業中（open な MR がある）
    if closed_unmerged:
        return "rejected"                           # 一つでも未マージクローズ＝却下
    if merged and len(merged) == len(states):
        return "approved"                           # すべてマージ＝承認
    return ""


# 外部クローズ時に承認/却下を推定するための手掛かり語（イシューコメント本文を新しい順に走査）。
# 同一コメントに両方あればやり直し指示とみなし、却下語を承認語より優先する。
_REJECT_HINTS = ("却下", "リジェクト", "取り下げ", "取下げ", "不採用", "やり直し", "作り直し",
                 "見送り", "reject", "wontfix", "won't fix", "not merging", "won't merge")
_APPROVE_HINTS = ("承認", "approve", "approved", "lgtm", "採用", "問題ありません", "問題なし",
                  "マージしました", "merged", "完了", "close as done")


def _decision_from_comments(host, token, project, iid) -> str:
    """イシューの**人間のコメント**を新しい順に走査し、承認/却下の手掛かりがある最初のコメントで
    判定する（却下語を承認語より優先）。system note / kiro-flow 自身の自動コメントは無視。
    手掛かりが無ければ ""。"""
    try:
        notes = _get_comments(host, token, project, iid)
    except RuntimeError:
        return ""
    for n in reversed(notes if isinstance(notes, list) else []):
        if n.get("system"):
            continue
        body = str(n.get("body") or "")
        if not body or "gitlab-idd:creator-node-id" in body or body.startswith("kiro-flow:"):
            continue
        low = body.lower()
        if any(h in body or h in low for h in _REJECT_HINTS):
            return "rejected"
        if any(h in body or h in low for h in _APPROVE_HINTS):
            return "approved"
    return ""


def _closed_issue_decision(host, token, project, iid, labels_now,
                           approved_label, done_label) -> "tuple[str, str]":
    """イシューが**外部でクローズ**され、関連 MR では決着がつかないときに承認/却下を推定する。
    優先順: ラベル（approved/done）→ イシューコメント → 手掛かり無しは取り下げ＝却下扱い。
    返り値 (decision, reason)。reason は承認/却下の根拠（ログ・成果テキストに出す）。"""
    if approved_label in labels_now:
        return "approved", f"クローズ済み＋ラベル {approved_label}"
    if done_label in labels_now:
        return "approved", f"クローズ済み＋ラベル {done_label}"
    c = _decision_from_comments(host, token, project, iid)
    if c == "approved":
        return "approved", "クローズ済み＋イシューコメントが承認を示唆"
    if c == "rejected":
        return "rejected", "クローズ済み＋イシューコメントが却下を示唆"
    return "rejected", "MR 無しのまま外部クローズ（取り下げ）"


def _finish_approved(host, token, project, iid, url, mrs, labels_now, done_label, reason=""):
    """承認: イシューをクローズ（status:done）して成果を返す。reason は承認の根拠
    （全 MR マージ / 外部クローズ＋ラベル / 外部クローズ＋コメント承認 のいずれか）。"""
    why = reason or "関連 MR を全マージ"
    try:
        _close_issue(host, token, project, iid, sorted(set(labels_now) | {done_label}))
    except RuntimeError as e:  # 既にクローズ済み等は致命的でない
        _log(f"イシュー #{iid} のクローズに失敗（無視）: {e}")
    _log(f"イシュー #{iid}: {why}＝承認。イシューをクローズ（{url}）")
    merged_urls = [m.get("web_url", "") for m in mrs]
    text = (f"[gitlab] イシュー #{iid} 承認（{why}）。イシューをクローズ（{url}）\n"
            f"マージ済み MR: {', '.join(u for u in merged_urls if u) or '(URL なし)'}")
    data = {"issue_iid": iid, "web_url": url, "decision": "approved", "reason": why,
            "merged_mrs": [m.get("iid") for m in mrs], "closed": True}
    return text, data


def _raise_rejected(host, token, project, iid, url, mrs, labels_now, done_label, reason=""):
    """却下: 人コメントを取り込み、元イシューをクローズして例外を送出する。reason は却下の根拠
    （未マージクローズ / 外部クローズ＋コメント却下 / MR 無しの取り下げ のいずれか）。
    例外メッセージ先頭の `[gitlab-reject]` を上位（kiro-autonomous）が検知し、やり直しに活かす。"""
    why = reason or "未マージクローズ"
    guidance = _human_comments(host, token, project, iid)
    try:
        _close_issue(host, token, project, iid, sorted(set(labels_now) | {done_label}))
    except RuntimeError as e:
        _log(f"イシュー #{iid} のクローズに失敗（無視）: {e}")
    if guidance:
        _log(f"イシュー #{iid}: 却下（{why}）。人コメントをやり直しに活かす。")
        raise RuntimeError(f"[gitlab-reject] 却下されました（{why}）（{url}）。やり直し指示: {guidance}")
    _log(f"イシュー #{iid}: 却下（{why}）。人コメント無し＝自動で判断してやり直す。")
    raise RuntimeError(f"[gitlab-reject] 却下されました（{why}）（{url}）。"
                       "人コメントが無いため自動で原因を判断してやり直してください。")


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
