#!/usr/bin/env python3
"""
kiro-cli-stub.py — kiro-cli の代わりに tmux ペインで動くダミーの対話エージェント。

エージェント CLI（kiro-cli）を入れずに kiro-loop の実動作 — 定期送信・処理中判定・
スロット解放・状態ファイルの last_sent_at・agent-dashboard からの復旧送信 — を
そのまま確認するためのもの。LLM は呼ばず、受け取った文章をそのまま読み上げて返す。

kiro-loop が依存している kiro-cli の振る舞いだけを真似る:
  1. 待機中は `>` だけの行を最終行に出す（kiro-loop はこれで「送信できる」と判断する）
  2. 入力を受けたらプロンプト行を消す（kiro-loop はこれで「処理中」と判断する）
  3. 一定時間ののち応答を出し、再び `>` を出す（kiro-loop はこれで「完了」と判断する）

使い方（通常は kiro-loop --stub が自動で起動する）:
  ./stub/kiro-cli-stub.py chat            # 単体で対話してみる
  KIRO_LOOP_STUB_DELAY=3 kiro-loop --stub # 応答までの秒数を変える（既定 5 秒）

kiro-cli 互換のため、chat などのサブコマンドと --trust-all-tools 等の未知の
オプションはすべて読み飛ばす（--delay だけ解釈する）。
"""

from __future__ import annotations   # 古い python3（3.9 等）でも起動できるようにする

import contextlib
import datetime as dt
import os
import select
import shutil
import subprocess
import sys
import termios
import time
from pathlib import Path

DEFAULT_DELAY = 5.0
# 複数行プロンプトは paste-buffer で 1 行ずつ届くため、この間隔で続きが来なければ
# 1 通のメッセージとして確定する。
_COALESCE_SEC = 0.3


def _delay_seconds(argv: list[str]) -> float:
    """--delay または KIRO_LOOP_STUB_DELAY から応答までの秒数を決める。"""
    for i, arg in enumerate(argv):
        if arg == "--delay" and i + 1 < len(argv):
            raw = argv[i + 1]
            break
        if arg.startswith("--delay="):
            raw = arg.split("=", 1)[1]
            break
    else:
        raw = os.environ.get("KIRO_LOOP_STUB_DELAY", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_DELAY
    return max(0.0, value)


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


@contextlib.contextmanager
def _no_line_editing():
    """端末の行編集（正準モード）を切る。

    正準モードのままだと 1 行の長さに上限（macOS は 1024 バイト）があり、それを超える
    プロンプトは端末ドライバに捨てられて改行が届かず、読み取りが永久に止まる。
    実際 `kiro-loop send` は複数行プロンプトを 1 行に連結して送るため、長いプロンプトで
    確実に踏む。kiro-cli のような TUI は自前で入力を扱うのでこの制限を受けない。
    """
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        mode = termios.tcgetattr(fd)
        mode[3] &= ~termios.ICANON        # lflag: 行編集を切る（ECHO と ISIG は残す）
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, saved)


def _read_message() -> str | None:
    """1 通分の入力を読む（続けて届いた行は同じメッセージとして束ねる）。"""
    fd = sys.stdin.fileno()
    buf = b""
    while True:
        # 改行まで来ていて、続きが途切れたら 1 通として確定する
        if buf and (b"\n" in buf or b"\r" in buf):
            if not select.select([fd], [], [], _COALESCE_SEC)[0]:
                break
        elif not select.select([fd], [], [], 1.0)[0]:
            continue                       # 入力待ち（人が打っている途中）
        chunk = os.read(fd, 65536)
        if not chunk:
            return buf.decode("utf-8", errors="replace").strip() or None
        if chunk.startswith(b"\x04") and not buf:
            return None                    # Ctrl+D
        buf += chunk
    text = buf.decode("utf-8", errors="replace").replace("\r", "\n")
    return "\n".join(line.strip() for line in text.split("\n")).strip()


_LOOP_SCRIPT = Path(__file__).resolve().parent.parent / "kiro-loop.py"


def _release_slot() -> None:
    """実行枠（スロット）を解放する。

    本番では kiro-cli の agent hook が `kiro-loop slot-release` を呼ぶ。スタブにはフックが
    無いので自分で呼ぶ。これをしないと、外部からの送信（agent-dashboard や kiro-loop send）
    で取られた枠が猶予時間（既定 2 時間）まで残り、そのペインが「応答中」のまま止まる。
    デーモン自身の定期送信は SlotMonitor が解放するので、そちらには影響しない。
    """
    if not os.environ.get("TMUX_PANE"):
        return
    cli = shutil.which("kiro-loop") or shutil.which("agent-loop")
    if cli:
        argv = [cli, "slot-release"]
    elif _LOOP_SCRIPT.is_file():
        argv = [sys.executable, str(_LOOP_SCRIPT), "slot-release"]
    else:
        return
    try:
        subprocess.run(argv, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass          # 解放できなくても応答は続ける（猶予時間で自動解放される）


def _respond(message: str, delay: float) -> None:
    """処理中の表示 → 待機 → 応答。この間はプロンプト行を出さない。"""
    print(f"\n[stub] {_now()} 受信しました（{len(message)} 文字）")
    head = message.splitlines()[0] if message.splitlines() else ""
    if head:
        print(f"[stub] 冒頭: {head[:80]}")
    print(f"[stub] 応答を作成しています…（{delay:.0f} 秒）")
    sys.stdout.flush()
    time.sleep(delay)
    print(f"[stub] {_now()} 完了しました。これはスタブの応答です（LLM は呼んでいません）。")
    _release_slot()


def _loop(delay: float) -> None:
    while True:
        # 最終行を `>` だけにする = kiro-loop から見た「待機中」
        print("\n>", end="", flush=True)
        try:
            message = _read_message()
        except KeyboardInterrupt:
            break
        if message is None:
            break          # stdin が閉じた（ペイン終了）
        if not message:
            continue       # 空 Enter は無視して待機に戻る
        if message in ("quit", "exit", "/quit", "/exit"):
            break
        try:
            _respond(message, delay)
        except KeyboardInterrupt:
            print("\n[stub] 中断しました。")


def main() -> int:
    delay = _delay_seconds(sys.argv[1:])
    print("kiro-cli スタブを起動しました（LLM は呼びません）。")
    print(f"受け取ったプロンプトに約 {delay:.0f} 秒で応答します。終了は Ctrl+C。")
    with _no_line_editing():
        _loop(delay)
    print("\n[stub] 終了します。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
