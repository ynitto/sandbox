#!/usr/bin/env python3
"""GitLab イシューポーリングフック（agent-loop の event_hook として実行される）

agent-loop の scheduler スレッド内で同期実行される。`check()` が:

  - **更新あり**: フィルター条件に合致するイシューのうち、前回チェックから
    「新規追加」または「更新（updated_at 変化）」されたものが 1 件でもあれば、
    その最新 1 件を送信プロンプトとして返す。
  - **更新なし + フォールバック有効**: 更新が無くても、フィルター条件に合致する
    イシューからランダムに 1 件選んで送信プロンプトを返す。
    フォールバックの有効/無効は agent-loop の YAML `event_hook_fallback: true`
    （→ 環境変数 `AGENT_LOOP_EVENT_HOOK_FALLBACK=1`）で制御する。
  - **更新なし + フォールバック無効**: None を返して今回はスキップ。

「更新なしフォールバック」はイベント検知のたび（check() の呼び出しごと）に
評価される。つまり毎サイクル、更新が無ければランダム送信する。

scheduler スレッドをブロックしないよう、ネットワーク呼び出しには短い timeout
を設定している。設定値は主に環境変数で上書きできる（agent-loop プロセスへ
export しておく）。
"""
import json
import os
import random
import subprocess
from pathlib import Path

# --- 設定（環境変数で上書き可能）---------------------------------------
# gl.py のパス（gitlab-idd スキル同梱）。
GL_PY = os.environ.get("AGENT_LOOP_GL_PY", "scripts/gl.py")
# gl.py を実行する作業ディレクトリ（git remote から host/project を解決するため）。
WORKDIR = os.environ.get("AGENT_LOOP_GL_CWD") or None
# Python インタプリタ（環境により python3 / py に読み替え）。
PYTHON = os.environ.get("AGENT_LOOP_PYTHON", "python")
# サブプロセスのタイムアウト（秒）。
TIMEOUT = int(os.environ.get("AGENT_LOOP_GL_TIMEOUT", "20"))

# --- フィルター条件（環境変数で上書き可能）-----------------------------
ISSUE_STATE = os.environ.get("AGENT_LOOP_ISSUE_STATE", "opened")
ISSUE_LABELS = os.environ.get("AGENT_LOOP_ISSUE_LABELS", "")     # 例: "status:open,assignee:any"
ISSUE_ASSIGNEE = os.environ.get("AGENT_LOOP_ISSUE_ASSIGNEE", "")  # 例: "MY_USER"

# 状態ファイル（iid -> updated_at を記録）。
STATE_FILE = Path(
    os.environ.get("AGENT_LOOP_ISSUE_STATE_FILE", "")
    or (Path.home() / ".agent" / "hooks" / "gitlab-issue-state.json")
)

# --- プロンプトテンプレート --------------------------------------------
# ラベルに応じて切り替える。先にマッチしたものを採用する。
_LABEL_PROMPTS: dict[str, str] = {
    "priority:critical": (
        "緊急イシューが割り当てられました。最優先で gitlab-idd スキルの"
        "ワーカーロールを実行し、対応してください。\n\n{issue_json}"
    ),
    "type:bug": (
        "バグイシューがあります。gitlab-idd スキルのワーカーロールで再現手順を"
        "確認して修正してください。\n\n{issue_json}"
    ),
}
_DEFAULT_PROMPT = (
    "新しいイシューが割り当てられました。gitlab-idd スキルのワーカーロールを"
    "実行して、このイシューを実装・報告してください。\n\n{issue_json}"
)
# フォールバック（更新が無いとき）に付与する前置き。
_FALLBACK_PREFIX = (
    "（フォールバック）新着の更新はありませんでした。手の空きを利用して、"
    "以下の未対応イシューを 1 件進めてください。優先度が低ければ着手不要と"
    "判断して構いません。\n\n"
)


def _run_gl(*gl_args: str):
    """gl.py を実行して JSON をパースして返す。失敗時は None。"""
    cmd = [PYTHON, GL_PY, *gl_args]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=WORKDIR
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _get_issues() -> list[dict] | None:
    args = ["list-issues", "--state", ISSUE_STATE]
    if ISSUE_LABELS:
        args += ["--label", ISSUE_LABELS]
    if ISSUE_ASSIGNEE:
        args += ["--assignee", ISSUE_ASSIGNEE]
    data = _run_gl(*args)
    return data if isinstance(data, list) else None


def _load_state() -> dict[str, str]:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.get("issues", {}).items()}
    except Exception:
        return {}


def _save_state(state: dict[str, str]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"issues": state}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _format_prompt(issue: dict, *, fallback: bool) -> str:
    issue_json = json.dumps(issue, ensure_ascii=False, indent=2)
    template = _DEFAULT_PROMPT
    for label in issue.get("labels", []):
        if label in _LABEL_PROMPTS:
            template = _LABEL_PROMPTS[label]
            break
    body = template.format(issue_json=issue_json)
    return (_FALLBACK_PREFIX + body) if fallback else body


def check() -> str | None:
    fallback_enabled = os.environ.get("AGENT_LOOP_EVENT_HOOK_FALLBACK") == "1"

    issues = _get_issues()
    if not issues:
        return None

    prev = _load_state()
    curr = {str(i["iid"]): str(i.get("updated_at", "")) for i in issues if "iid" in i}

    # 新規 iid もしくは updated_at が変わったイシュー = 「更新あり」
    changed = [
        i for i in issues
        if "iid" in i and prev.get(str(i["iid"])) != str(i.get("updated_at", ""))
    ]
    _save_state(curr)

    if changed:
        changed.sort(key=lambda i: str(i.get("updated_at", "")), reverse=True)
        return _format_prompt(changed[0], fallback=False)

    # ここから先は「更新なし」。フォールバックが有効なら毎回ランダム送信する。
    if fallback_enabled:
        return _format_prompt(random.choice(issues), fallback=True)

    return None


if __name__ == "__main__":
    # 手動デバッグ用: 単体実行すると check() の結果を表示する。
    print(check())
