#!/usr/bin/env python3
"""GitLab MR ポーリングフック（kiro-loop の event_hook として実行される）

kiro-loop の scheduler スレッド内で同期実行される。`check()` が:

  - **更新あり**: フィルター条件に合致するオープン MR のうち、前回チェックから
    「新規追加」または「更新（updated_at 変化 = コメント/コミット追加など）」
    されたものが 1 件でもあれば、その最新 1 件を送信プロンプトとして返す。
  - **更新なし + フォールバック有効**: 更新が無くても、フィルター条件に合致する
    MR からランダムに 1 件選んで送信プロンプトを返す（未解決スレッドの取り
    こぼし防止）。フォールバックの有効/無効は kiro-loop の YAML
    `event_hook_fallback: true`（→ 環境変数 `KIRO_LOOP_EVENT_HOOK_FALLBACK=1`）
    で制御する。
  - **更新なし + フォールバック無効**: None を返して今回はスキップ。

「更新なしフォールバック」はイベント検知のたび（check() の呼び出しごと）に
評価される。

scheduler スレッドをブロックしないよう、ネットワーク呼び出しには短い timeout
を設定している。設定値は主に環境変数で上書きできる。
"""
import json
import os
import random
import subprocess
from pathlib import Path

# --- 設定（環境変数で上書き可能）---------------------------------------
GL_PY = os.environ.get("KIRO_LOOP_GL_PY", "scripts/gl.py")
WORKDIR = os.environ.get("KIRO_LOOP_GL_CWD") or None
PYTHON = os.environ.get("KIRO_LOOP_PYTHON", "python")
TIMEOUT = int(os.environ.get("KIRO_LOOP_GL_TIMEOUT", "20"))

# --- フィルター条件（環境変数で上書き可能）-----------------------------
MR_STATE = os.environ.get("KIRO_LOOP_MR_STATE", "opened")
# 自分担当 MR に絞る場合は assignee のユーザー名を指定する（list-mrs は
# サーバー側 assignee フィルタを持たないためクライアント側で絞り込む）。
MR_ASSIGNEE = os.environ.get("KIRO_LOOP_MR_ASSIGNEE", "")
MR_SOURCE_BRANCH_PREFIX = os.environ.get("KIRO_LOOP_MR_SOURCE_BRANCH_PREFIX", "")

# 状態ファイル（iid -> updated_at を記録）。
STATE_FILE = Path(
    os.environ.get("KIRO_LOOP_MR_STATE_FILE", "")
    or (Path.home() / ".kiro" / "hooks" / "gitlab-mr-state.json")
)

# --- プロンプトテンプレート --------------------------------------------
_PROMPT = (
    "自分にアサインされたオープン MR の未解決ディスカッションを確認してください。\n"
    "対象 MR (iid={iid}): {title}\n{web_url}\n\n"
    "手順:\n"
    "1. python {gl_py} get-mr-discussions {iid} --unresolved で未解決スレッドを取得\n"
    "2. 各スレッドの指摘内容を確認し:\n"
    "   - 質問・説明要求 → python {gl_py} add-mr-comment {iid} --body \"返答内容\" で返答し、"
    "python {gl_py} resolve-mr-discussion {iid} DISCUSSION_ID でスレッドをクローズ\n"
    "   - コード修正要求 → 修正を実装して push し、python {gl_py} add-mr-comment {iid} "
    "--body \"修正しました: ...\" で報告してスレッドをクローズ\n"
    "3. 全スレッドがクローズしたら python {gl_py} add-mr-comment {iid} "
    "--body \"全指摘に対応しました。再レビューをお願いします。\" を投稿\n"
    "未解決スレッドが無ければ「対応待ちのコメントはありません」と報告して終了。\n\n"
    "MR の詳細:\n{mr_json}"
)
_FALLBACK_PREFIX = (
    "（フォールバック）新着の更新はありませんでした。取りこぼし防止のため、"
    "以下の MR の未解決スレッドを念のため確認してください。何も無ければそのまま"
    "終了して構いません。\n\n"
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


def _matches_assignee(mr: dict) -> bool:
    if not MR_ASSIGNEE:
        return True
    names = {a.get("username") for a in (mr.get("assignees") or [])}
    assignee = mr.get("assignee") or {}
    if assignee.get("username"):
        names.add(assignee["username"])
    return MR_ASSIGNEE in names


def _get_mrs() -> list[dict] | None:
    args = ["list-mrs", "--state", MR_STATE]
    if MR_SOURCE_BRANCH_PREFIX:
        args += ["--source-branch-prefix", MR_SOURCE_BRANCH_PREFIX]
    data = _run_gl(*args)
    if not isinstance(data, list):
        return None
    return [mr for mr in data if _matches_assignee(mr)]


def _load_state() -> dict[str, str]:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.get("mrs", {}).items()}
    except Exception:
        return {}


def _save_state(state: dict[str, str]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"mrs": state}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _format_prompt(mr: dict, *, fallback: bool) -> str:
    body = _PROMPT.format(
        iid=mr.get("iid", ""),
        title=mr.get("title", ""),
        web_url=mr.get("web_url", ""),
        gl_py=GL_PY,
        mr_json=json.dumps(mr, ensure_ascii=False, indent=2),
    )
    return (_FALLBACK_PREFIX + body) if fallback else body


def check() -> str | None:
    fallback_enabled = os.environ.get("KIRO_LOOP_EVENT_HOOK_FALLBACK") == "1"

    mrs = _get_mrs()
    if not mrs:
        return None

    prev = _load_state()
    curr = {str(m["iid"]): str(m.get("updated_at", "")) for m in mrs if "iid" in m}

    changed = [
        m for m in mrs
        if "iid" in m and prev.get(str(m["iid"])) != str(m.get("updated_at", ""))
    ]
    _save_state(curr)

    if changed:
        changed.sort(key=lambda m: str(m.get("updated_at", "")), reverse=True)
        return _format_prompt(changed[0], fallback=False)

    if fallback_enabled:
        return _format_prompt(random.choice(mrs), fallback=True)

    return None


if __name__ == "__main__":
    print(check())
