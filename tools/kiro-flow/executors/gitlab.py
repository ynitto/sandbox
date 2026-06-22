#!/usr/bin/env python3
"""gitlab — kiro-flow の executor プラグイン（opt-in のワーカーバス）

kiro-loop の event_hook と同じ流儀で、kiro-flow 本体から importlib で動的にロードされ、
`execute()` が呼び出される。タスクを gitlab-idd スキルの `gl.py` で **GitLab イシュー**
にして委譲し、リモートの（別マシン/別人の）ワーカーが拾って実装する。kiro-flow は
イシューを `get-issue` でポーリングし、レビュアーが `status:approved` を付ける
（= 受け入れ承認）まで待って完了とみなす。ローカルに kiro-cli が無くても、GitLab
越しに作業を委譲できる。

プラグイン契約:
    execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None) -> (text, data)

設定の渡し方（優先度: 個別環境変数 > KIRO_FLOW_EXECUTOR_CONFIG(JSON) > 既定）:
    - kiro-flow 本体は設定ファイルの `gitlab:` ブロックを JSON 化して環境変数
      `KIRO_FLOW_EXECUTOR_CONFIG` で渡す。
    - 個別の上書きは `KIRO_FLOW_GITLAB_<KEY>`（例: KIRO_FLOW_GITLAB_POLL_INTERVAL）。

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
from datetime import datetime, timezone

NAME = "gitlab"

# プラグインの既定設定（kiro-flow の CONFIG_DEFAULTS["gitlab"] と同値）。
_DEFAULTS = {
    "conn_label": "default",
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


def run_gl(subargs, conn_label: str = "default", parse_json: bool = True):
    """gitlab-idd の gl.py を 1 回呼び出す。parse_json=True なら出力 JSON を返す。
    gl.py が見つからない / 失敗したときは RuntimeError を送出する（結果は failed 記録）。"""
    script = _find_gl_script()
    if script is None:
        raise RuntimeError(
            "gitlab executor には gitlab-idd スキルの scripts/gl.py が必要です。"
            "スキルを導入し connections.yaml で接続を設定してください（opt-in）。")
    cmd = [sys.executable, script, "--label-conn", conn_label] + list(subargs)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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

    1. gl.py create-issue でイシューを起票（status:open,assignee:any ＋ 優先度）
    2. gl.py get-issue でラベルをポーリングし、status:approved（または status:done /
       クローズ）に達したら完了
    3. ワーカーの最終コメント（完了報告）を成果テキストとして取り込んで返す
    """
    cfg = _config()
    conn = str(cfg.get("conn_label") or "default")
    # opt-in 前提チェック: gl.py が無ければ即失敗（誤って選んだときに無限待ちにしない）
    if _find_gl_script() is None:
        raise RuntimeError(
            "gitlab executor には gitlab-idd スキルの scripts/gl.py が必要です（opt-in）。")

    title = f"[kiro-flow] {goal.strip()[:80]}"
    body = _issue_body(kind, goal, dep_results)
    labels = str(cfg.get("labels") or "status:open,assignee:any")
    priority = str(cfg.get("priority") or "").strip()
    if priority:
        labels = f"{labels},{priority}"

    # 本文は argv 長制限を避けてファイル経由で渡す（依存成果が大きいときの起動失敗を防ぐ）
    fd, body_file = tempfile.mkstemp(prefix="kiro-flow-issue-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        created = run_gl(["create-issue", "--title", title,
                          "--body-file", body_file, "--labels", labels], conn)
    finally:
        with contextlib.suppress(OSError):
            os.remove(body_file)

    iid = created.get("iid") or created.get("id")
    url = created.get("web_url", "")
    if not iid:
        raise RuntimeError(f"GitLab イシューの作成に失敗しました: {str(created)[:200]}")
    _log(f"イシュー #{iid} を起票し承認待ち: {url}")

    approved = str(cfg.get("approved_label") or "status:approved")
    done = str(cfg.get("done_label") or "status:done")
    interval = _as_float(cfg.get("poll_interval"), 30.0)
    timeout = _as_float(cfg.get("timeout"), 0.0)
    deadline = (time.time() + timeout) if timeout > 0 else None

    labels_now: set = set()
    while True:
        issue = run_gl(["get-issue", str(iid)], conn)
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
        comments = run_gl(["get-comments", str(iid)], conn)
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
    # 手動デバッグ用: 単体実行すると簡単な接続確認をする。
    print("gl.py:", _find_gl_script() or "(見つかりません)")
    print("config:", json.dumps(_config(), ensure_ascii=False))
