from __future__ import annotations
# tmux_util.py — 元 agent-loop.py の 941-995 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# tmux ヘルパー（SessionManager より前に定義）
# ---------------------------------------------------------------------------

def _tmux_cmd(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    tmux_bin = shutil.which("tmux")
    if tmux_bin is None:
        raise RuntimeError("tmux が PATH に見つかりません。")
    if capture:
        return subprocess.run([tmux_bin, *args], capture_output=True, text=True)
    return subprocess.run(
        [tmux_bin, *args],
        check=False,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _send_to_pane(pane_id: str, text: str) -> tuple[bool, str]:
    """set-buffer + paste-buffer でペインにテキストを安全送信する。"""
    buffer_name = f"agent-loop-{uuid.uuid4().hex[:8]}"
    try:
        result = _tmux_cmd("set-buffer", "-b", buffer_name, "--", text)
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux set-buffer に失敗しました。"
            return False, err

        result = _tmux_cmd("paste-buffer", "-t", pane_id, "-b", buffer_name)
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux paste-buffer に失敗しました。"
            return False, err

        result = _tmux_cmd("send-keys", "-t", pane_id, "Enter")
        if result.returncode != 0:
            err = (result.stderr or "").strip() or "tmux send-keys(Enter) に失敗しました。"
            return False, err

        return True, ""
    finally:
        _tmux_cmd("delete-buffer", "-b", buffer_name)


def _tmux_cmd_or_raise(*args: str, error_label: str) -> str:
    """_tmux_cmd を実行し、失敗または空出力なら RuntimeError を送出する。"""
    result = _tmux_cmd(*args)
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        raise RuntimeError(f"{error_label}に失敗しました: {err}")
    output = (result.stdout or "").strip()
    if not output:
        raise RuntimeError(f"{error_label}に失敗しました: 空の結果")
    return output


