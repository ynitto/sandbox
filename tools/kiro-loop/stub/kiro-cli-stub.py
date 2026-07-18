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

import datetime as dt
import os
import select
import sys
import time

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


def _read_message() -> str | None:
    """1 通分の入力を読む（続けて届いた行は同じメッセージとして束ねる）。"""
    first = sys.stdin.readline()
    if not first:
        return None
    lines = [first.rstrip("\n")]
    while select.select([sys.stdin], [], [], _COALESCE_SEC)[0]:
        nxt = sys.stdin.readline()
        if not nxt:
            break
        lines.append(nxt.rstrip("\n"))
    return "\n".join(lines).strip()


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


def main() -> int:
    delay = _delay_seconds(sys.argv[1:])
    print("kiro-cli スタブを起動しました（LLM は呼びません）。")
    print(f"受け取ったプロンプトに約 {delay:.0f} 秒で応答します。終了は Ctrl+C。")
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
    print("\n[stub] 終了します。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
