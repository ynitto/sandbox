#!/usr/bin/env python3
"""スタブが kiro-loop の判定契約を満たすかを検証する。

kiro-loop は kiro-cli の画面を見て「送れる／処理中」を判断する（_PROMPT_RE）。
スタブがこの契約を破ると、定期送信が止まったりスロットが解放されなくなったりするが、
tmux 越しの症状としてしか現れず原因が追いにくい。ここで直接押さえておく。

    python3 test/test_stub.py
"""

from __future__ import annotations

import os
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

    print(f"\n{passed} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
