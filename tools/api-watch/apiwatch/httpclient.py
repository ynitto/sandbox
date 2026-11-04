"""1 回の HTTP 要求。一時的な失敗の再試行・Retry-After・取得元単位のレート制限・条件付き要求。"""
from __future__ import annotations

import email.utils
import gzip
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any, Callable

from .util import PermanentError, REDACT, TransientError, log, now

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
USER_AGENT = "api-watch"


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float


@dataclass
class Response:
    status: int
    headers: dict[str, str]     # 小文字のキー
    body: bytes

    def json(self) -> Any:
        if not self.body.strip():
            return None
        try:
            return json.loads(self.body.decode("utf-8-sig"))
        except ValueError as e:
            raise PermanentError(f"応答が JSON ではありません（{e}）: {self.body[:200]!r}") from e


def build_url(url: str, query: dict) -> str:
    if not query:
        return url
    parts = urllib.parse.urlsplit(url)
    existing = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    items = list(existing)
    for k, v in query.items():
        if v is None:
            continue
        if isinstance(v, list):
            items.extend((k, _qs(x)) for x in v)
        else:
            items.append((k, _qs(v)))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(items)))


def _qs(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - now())
    except (TypeError, ValueError):
        return None


def rate_limit_wait(headers: dict[str, str]) -> float | None:
    """残り回数が 0 と言われたとき、リセットまでの秒数。"""
    for prefix in ("x-ratelimit-", "ratelimit-", "x-rate-limit-"):
        rem = headers.get(prefix + "remaining")
        if rem is None:
            continue
        try:
            if float(rem) > 0:
                return None
        except ValueError:
            return None
        reset = headers.get(prefix + "reset")
        if reset is None:
            return None
        try:
            r = float(reset)
        except ValueError:
            return None
        return max(0.0, r - now()) if r > 1e9 else max(0.0, r)
    return None


def _decode(body: bytes, encoding: str | None) -> bytes:
    enc = (encoding or "").lower()
    if enc == "gzip":
        return gzip.decompress(body)
    if enc == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


class _NoRedirectAcrossHosts(urllib.request.HTTPRedirectHandler):
    """別のホストへのリダイレクトでは認証のヘッダーを持っていかない。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            for h in list(new.headers):
                if h.lower() in ("authorization", "cookie") or "token" in h.lower() or "key" in h.lower():
                    del new.headers[h]
        return new


_OPENER = urllib.request.build_opener(_NoRedirectAcrossHosts())


def send_once(req: Request) -> Response:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip, deflate"}
    headers.update(req.headers)
    r = urllib.request.Request(req.url, data=req.body, method=req.method, headers=headers)
    try:
        with _OPENER.open(r, timeout=req.timeout) as resp:
            raw = resp.read()
            h = {k.lower(): v for k, v in resp.headers.items()}
            return Response(resp.status, h, _decode(raw, h.get("content-encoding")))
    except urllib.error.HTTPError as e:
        h = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            raw = e.read()
        except Exception:  # noqa: BLE001
            raw = b""
        return Response(e.code, h, _decode(raw, h.get("content-encoding")) if raw else b"")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise TransientError(f"接続できません: {reason}") from e


class Client:
    """取得元 1 つ分の HTTP 送信。レート制限の間隔を守り、一時的な失敗は再試行する。

    on_request(t) は要求を送るたびに呼ばれる（最後に送った時刻を状態に残すため）。
    """

    def __init__(self, *, min_interval: float, per_minute: int, attempts: int, backoff: float, max_backoff: float,
                 max_wait: float, last_request_at: float | None, on_request: Callable[[float], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self.per_minute = per_minute
        self.attempts = attempts
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.max_wait = max_wait
        self.last = last_request_at or 0.0
        self.sent: list[float] = []
        self.on_request = on_request
        self.sleep = sleep
        self.requests = 0

    def _pace(self) -> None:
        t = now()
        wait = 0.0
        if self.min_interval:
            wait = max(wait, self.last + self.min_interval - t)
        if self.per_minute:
            recent = [s for s in self.sent if s > t - 60]
            self.sent = recent
            if len(recent) >= self.per_minute:
                wait = max(wait, recent[0] + 60 - t)
        if wait > 0:
            self.sleep(wait)

    def send(self, req: Request, ok_status: tuple[int, ...] = (200, 201, 202, 203, 204, 206, 304)) -> Response:
        last_err: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            self._pace()
            t = now()
            self.last = t
            self.sent.append(t)
            self.requests += 1
            if self.on_request:
                self.on_request(t)
            retry_after: float | None = None
            try:
                resp = send_once(req)
            except TransientError as e:
                last_err = e
            else:
                if resp.status in ok_status:
                    wait = rate_limit_wait(resp.headers)
                    if wait:
                        if wait > self.max_wait:
                            raise TransientError(f"レート制限の残りが 0 です（{wait:.0f} 秒後に再開）", retry_after=wait)
                        self.last = max(self.last, now() + wait - (self.min_interval or 0))
                        log(f"  レート制限の残りが 0 なので {wait:.0f} 秒待ちます")
                        self.sleep(wait)
                    return resp
                snippet = REDACT(resp.body[:300].decode("utf-8", "replace"))
                if resp.status not in RETRYABLE_STATUS:
                    raise PermanentError(f"HTTP {resp.status}: {snippet}")
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
                if retry_after is None and resp.status in (403, 429):
                    retry_after = rate_limit_wait(resp.headers)
                last_err = TransientError(f"HTTP {resp.status}: {snippet}", retry_after=retry_after)
            if attempt >= self.attempts:
                break
            delay = min(self.max_backoff, self.backoff * (2 ** (attempt - 1))) * (0.5 + random.random() / 2)
            if retry_after is not None:
                if retry_after > self.max_wait:
                    raise TransientError(f"{last_err}（Retry-After {retry_after:.0f} 秒は待てる上限を超えます）",
                                         retry_after=retry_after)
                delay = max(delay, retry_after)
            log(f"  一時的な失敗（{last_err}）。{delay:.1f} 秒後に再試行します（{attempt}/{self.attempts}）")
            self.sleep(delay)
        assert last_err is not None
        if isinstance(last_err, TransientError):
            raise last_err
        raise TransientError(str(last_err))
