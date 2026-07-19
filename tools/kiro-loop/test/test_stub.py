#!/usr/bin/env python3
"""スタブが kiro-loop の判定契約を満たすかを検証する。

kiro-loop は kiro-cli の画面を見て「送れる／処理中」を判断する（_PROMPT_RE）。
スタブがこの契約を破ると、定期送信が止まったりスロットが解放されなくなったりするが、
tmux 越しの症状としてしか現れず原因が追いにくい。ここで直接押さえておく。

    python3 test/test_stub.py
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path

STUB = Path(__file__).resolve().parent.parent / "stub" / "kiro-cli-stub.py"
# kiro-loop.py の _PROMPT_RE と同じ（待機中と判定される行）
PROMPT_RE = re.compile(r"(^\s*[>?❯›]\s*$|!>)", re.MULTILINE)

passed = 0


def check(name: str, cond: bool) -> None:
    global passed
    if not cond:
        print(f"NG - {name}")
        sys.exit(1)
    passed += 1
    print(f"ok - {name}")


def read_until(proc: subprocess.Popen, needle: str, timeout: float = 15.0) -> str:
    """needle が現れるまで stdout を読む（タイムアウトしたら失敗させる）。

    待機中のプロンプトは改行を伴わないため、行単位ではなく生のバイトで読む。
    """
    fd = proc.stdout.fileno()
    out = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not select.select([fd], [], [], 0.2)[0]:
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        out += chunk.decode("utf-8", errors="replace")
        if needle in out:
            return out
    raise AssertionError(f"'{needle}' が {timeout} 秒以内に出力されませんでした:\n{out}")


def main() -> int:
    check("スタブが同梱されている", STUB.is_file())

    proc = subprocess.Popen(
        [sys.executable, str(STUB), "chat", "--trust-all-tools", "--delay", "1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=0,
    )
    try:
        # 1. 起動直後は待機中（プロンプト行が出る）
        banner = read_until(proc, ">")
        check("起動後に待機中のプロンプト行を出す", bool(PROMPT_RE.search(banner)))
        check("kiro-cli の未知オプションを受け付ける", proc.poll() is None)

        # 2. 送ると受信を表示し、処理中はプロンプト行を出さない
        proc.stdin.write("テスト送信\n")
        proc.stdin.flush()
        processing = read_until(proc, "応答を作成しています")
        check("受信した内容を表示する", "テスト送信" in processing)
        after_prompt = processing.split("応答を作成しています", 1)[1]
        check("処理中はプロンプト行を出さない", not PROMPT_RE.search(after_prompt))

        # 3. 応答後は再び待機中に戻る（kiro-loop がスロットを解放できる）
        done = read_until(proc, "完了しました")
        check("応答を返す", "スタブの応答" in done)
        # 応答とプロンプトが同じ read に入ることがあるので、足りないときだけ読み足す
        tail = done.split("完了しました", 1)[1]
        if not PROMPT_RE.search(tail):
            tail += read_until(proc, ">")
        check("応答後は待機中に戻る", bool(PROMPT_RE.search(tail)))
    finally:
        proc.kill()
        proc.wait(timeout=5)

    test_long_single_line()

    print(f"\n{passed} tests passed")
    return 0


def test_long_single_line() -> None:
    """1024 バイトを超える 1 行を端末経由で受け取れるか。

    `kiro-loop send` は複数行プロンプトを 1 行に連結して送る。端末の行編集（正準モード）が
    有効なままだと 1 行の上限（macOS は 1024 バイト）で入力が捨てられ、改行が届かず
    スタブが永久に固まる（= スロットを握ったままループ全体が止まる）。pty 越しでしか
    再現しないので、ここでは擬似端末を使う。
    """
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, str(STUB), "chat", "--delay", "0"],
        stdin=slave, stdout=slave, stderr=slave, close_fds=True,
    )
    os.close(slave)
    try:
        deadline = time.time() + 10
        out = ""
        while time.time() < deadline and ">" not in out:
            if select.select([master], [], [], 0.2)[0]:
                out += os.read(master, 4096).decode("utf-8", errors="replace")
        check("擬似端末でも待機中になる", bool(PROMPT_RE.search(out)))

        long_line = "あ" * 500 + " 最後まで届いたか"   # 約 1500 バイトの 1 行
        os.write(master, (long_line + "\n").encode("utf-8"))

        got = ""
        deadline = time.time() + 15
        while time.time() < deadline and "完了しました" not in got:
            if select.select([master], [], [], 0.2)[0]:
                got += os.read(master, 65536).decode("utf-8", errors="replace")
        check("1024 バイト超の 1 行でも固まらず応答する", "完了しました" in got)
        # スタブは受信文字数を報告する。全文字そろっていれば取りこぼしていない。
        check("行の末尾まで受け取れている", f"受信しました（{len(long_line)} 文字）" in got)
    finally:
        proc.kill()
        proc.wait(timeout=5)
        os.close(master)


if __name__ == "__main__":
    sys.exit(main())
