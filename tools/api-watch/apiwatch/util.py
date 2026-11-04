"""共通の小物。"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any, Iterable

IS_WINDOWS = os.name == "nt"

# 終了コード。Dagu の retry_policy.exit_code で「一時的な失敗だけ再試行」を指定できるよう分ける。
EXIT_OK = 0
EXIT_ERROR = 1          # 設定の誤りなど、再試行しても直らないもの
EXIT_USAGE = 2
EXIT_TRANSIENT = 3      # 通信の失敗・タイムアウト・レート制限。再試行してよい
EXIT_PARTIAL = 4        # 一部のルール・受け渡しが失敗した（他は進めた。失敗分は状態に残る）


class ConfigError(Exception):
    """設定の誤り。"""


class TransientError(Exception):
    """しばらくして再試行すれば通るかもしれない失敗。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(Exception):
    """再試行しても通らない失敗（認証・権限・404・応答の形の誤りなど）。"""


def now() -> float:
    return time.time()


def iso(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def canonical(value: Any) -> str:
    """キー順に依らない JSON（ハッシュ・比較用）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text: str | bytes, n: int = 64) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:n]


def pack(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False).encode("utf-8"), 6)


def unpack(blob: bytes | None) -> Any:
    if blob is None:
        return None
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def hostname() -> str:
    return socket.gethostname()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        # os.kill(pid, 0) は Windows では生存確認にならず、相手を終了させてしまう
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5                # ERROR_ACCESS_DENIED = 生きているが見えない
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                         # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def setup_stdio() -> None:
    """ログは UTF-8 で出す（Windows のコンソール・UTF-8 でないロケールでも文字化けさせない）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# 秘密の伏せ字
# ---------------------------------------------------------------------------

_SECRET_NAME = re.compile(r"(token|secret|pass(word)?|api[_-]?key|apikey|auth|signature|sig|session|cookie|credential|private)",
                          re.I)
_QUERY_SECRET = re.compile(
    r"([?&](?:[^=&#]*(?:token|secret|password|passwd|api[_-]?key|apikey|signature|sig|auth|credential)[^=&#]*)=)([^&#]*)",
    re.I)


def is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name or ""))


class Redactor:
    """ログ・保存するエラー文から秘密を伏せる。値そのものを覚えて置き換える。"""

    MASK = "***"

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: Any) -> None:
        if isinstance(value, str) and len(value) >= 4:
            self._values.add(value)

    def add_all(self, values: Iterable[Any]) -> None:
        for v in values:
            self.add(v)

    def __call__(self, text: Any) -> str:
        s = str(text)
        for v in sorted(self._values, key=len, reverse=True):
            s = s.replace(v, self.MASK)
        return _QUERY_SECRET.sub(lambda m: m.group(1) + self.MASK, s)


REDACT = Redactor()


def log(msg: str) -> None:
    print(REDACT(msg), flush=True)


def warn(msg: str) -> None:
    print(REDACT("警告: " + msg), file=sys.stderr, flush=True)


def key_string(values: list) -> str:
    """識別 ID（1 つ以上の値）を、保存・比較に使う 1 本の文字列にする。"""
    if len(values) == 1 and isinstance(values[0], str):
        return values[0]
    if len(values) == 1 and isinstance(values[0], int) and not isinstance(values[0], bool):
        return str(values[0])
    return canonical(values)
