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


def _send_chat_to_pane(pane_id: str, text: str) -> tuple[bool, str]:
    """Send a session-start chat command, including Codex skill completion confirmation."""
    key_interval_sec = 3
    text = re.sub(r"\r?\n", " ", str(text)).strip()
    result = _tmux_cmd("send-keys", "-t", pane_id, "-l", "--", text)
    if result.returncode != 0:
        return False, (result.stderr or "").strip() or "tmux send-keys(text) に失敗しました。"
    time.sleep(key_interval_sec)
    enter_count = 2 if re.match(r"^\$[A-Za-z0-9_.:-]+(?:\s|$)", text) else 1
    for _ in range(enter_count):
        result = _tmux_cmd("send-keys", "-t", pane_id, "Enter")
        if result.returncode != 0:
            return False, (result.stderr or "").strip() or "tmux send-keys(Enter) に失敗しました。"
        time.sleep(key_interval_sec)
    return True, ""


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


# ---------------------------------------------------------------------------
# headless 実行ログを追うウィンドウ
# ---------------------------------------------------------------------------

def _log_window_name(label: str) -> str:
    """tmux ウィンドウ名。`.` と `:` は tmux のターゲット構文に食われるので落とす。"""
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label or "run")).strip("-")
    return f"log-{(safe or 'run')[:20]}"


def open_log_window(session_name: str, label: str, log_file: str) -> bool:
    """headless 実行の JSONL を `tail -F` で追うウィンドウを開く（開けたら True）。

    headless 経路は tmux ペインを使わないので、実行の様子はログファイルにしか出ない。
    人が置き場を知って自分で開くまで何も見えないのは、対話経路との落差が大きい。

    **ウィンドウは entry ごとに 1 枚**を使い回す。実行ごとに増やすと、5 分間隔の
    定期プロンプトが 1 日で 288 枚のウィンドウを作る。同じ entry の次の実行では
    `respawn-window -k` で新しいログへ張り替える。

    失敗は握りつぶす。ログを覗く窓が開かないことは実行の失敗ではないので、tmux が
    無い環境（CI・コンテナ）やサーバ運用で実行そのものを止めない。
    """
    if not log_file:
        return False
    try:
        if shutil.which("tmux") is None:
            return False
        window = _log_window_name(label)
        target = f"{session_name}:{window}"
        # tail -F は「まだ存在しないファイル」も待てる（-f と違い作成を待つ）。
        cmd = f"tail -n +1 -F {shlex.quote(str(log_file))}"
        if _tmux_cmd("has-session", "-t", session_name).returncode != 0:
            if _tmux_cmd("new-session", "-d", "-s", session_name,
                         "-n", window, cmd).returncode != 0:
                return False
            log.info("headless ログ用に tmux セッション '%s' を作成しました。", session_name)
            return True
        if _tmux_cmd("has-session", "-t", target).returncode == 0:
            # 既にある窓は張り替える（-k で中の tail を落としてから起こし直す）
            return _tmux_cmd("respawn-window", "-k", "-t", target, cmd).returncode == 0
        return _tmux_cmd("new-window", "-d", "-t", session_name,
                         "-n", window, cmd).returncode == 0
    except Exception:   # tmux の不在・権限・競合はすべて「窓が開かない」で足りる
        log.debug("headless ログ用ウィンドウを開けませんでした: %s", log_file, exc_info=True)
        return False
