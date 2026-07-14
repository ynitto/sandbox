from __future__ import annotations
# webhook.py — 元 agent-loop.py の 1978-2183 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# inbound webhook サーバ（provider 非依存）
# ---------------------------------------------------------------------------

class _SafeDict(dict):
    """str.format_map 用。未定義キーは `{key}` のまま残し KeyError を出さない。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class _WebhookContext:
    """hook に渡す provider 非依存のリクエストコンテキスト。"""

    __slots__ = ("name", "method", "headers", "query", "raw", "payload")

    def __init__(self, name: str, method: str, headers: dict[str, str],
                 query: dict[str, Any], raw: bytes, payload: dict[str, Any]) -> None:
        self.name = name
        self.method = method
        self.headers = headers
        self.query = query
        self.raw = raw
        self.payload = payload


class WebhookServer:
    """agent-loop 稼働中だけ常駐する inbound webhook 受信サーバ。

    `POST <path_prefix>/<name>` を受け、<name> を PeriodicScheduler のエントリに
    解決し、hook（provider 固有）で payload を辞書化、エントリの prompt テンプレートへ
    注入して外部キューへ積む。GitLab 等の送信元固有知識はコアに持たない。
    """

    def __init__(self, scheduler: PeriodicScheduler, host: str, port: int,
                 path_prefix: str, secret: str, secret_header: str | None,
                 max_body_bytes: int) -> None:
        self._scheduler = scheduler
        self._host = host
        self._port = port
        self._path_prefix = "/" + path_prefix.strip("/")
        self._secret = secret or ""
        self._secret_header = (secret_header or "").lower()
        self._max_body = max_body_bytes
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler_cls = self._make_handler()
        try:
            self._httpd = http.server.ThreadingHTTPServer((self._host, self._port), handler_cls)
        except OSError as exc:
            log.warning("[WebhookServer] 起動に失敗しました (%s:%s): %s。webhook を無効化して継続します。",
                        self._host, self._port, exc)
            self._httpd = None
            return
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="webhook-server", daemon=True)
        self._thread.start()
        log.info("[WebhookServer] 起動しました: http://%s:%s%s/<name>",
                 self._host, self._port, self._path_prefix)
        if not self._secret:
            log.warning("[WebhookServer] secret 未設定です。共有シークレット検証をスキップします（開発用）。")

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception as exc:
                log.debug("[WebhookServer] 停止時エラー: %s", exc)
            self._httpd = None

    # -- 受信処理（provider 非依存） -----------------------------------------

    def _process(self, name: str, method: str, headers: dict[str, str],
                 query: dict[str, Any], raw: bytes) -> tuple[int, str]:
        """ルート解決→認証→hook→テンプレート注入→enqueue。(status, message) を返す。"""
        route = self._scheduler.resolve_webhook_route(name)
        if route is None:
            return 404, "unknown webhook route"

        # 汎用共有シークレット検証（照合ヘッダ名は可変）。
        if self._secret or route.get("secret"):
            expected = route.get("secret") or self._secret
            header_name = (route.get("secret_header") or self._secret_header or "").lower()
            got = headers.get(header_name, "") if header_name else ""
            if not header_name or not hmac.compare_digest(got, expected):
                return 401, "unauthorized"

        payload = self._parse_json(raw)
        ctx = _WebhookContext(name=route["name"], method=method,
                              headers=headers, query=query, raw=raw, payload=payload)

        # hook 実行（provider 固有の判定・パース）。例外は握って 200（#11: リトライ嵐回避）。
        try:
            params = self._invoke_hook(route, ctx)
        except Exception as exc:
            log.error("[WebhookServer] hook 実行エラー (%s): %s", name, exc, exc_info=True)
            return 200, "ignored (hook error)"
        if params is None:
            return 200, "ignored"

        inject = _SafeDict({"name": route["name"], **params})
        try:
            prompt_text = route["prompt_template"].format_map(inject)
        except Exception as exc:
            log.error("[WebhookServer] テンプレート注入エラー (%s): %s", name, exc, exc_info=True)
            return 500, "template error"

        if not self._scheduler.enqueue_external(route["name"], prompt_text):
            return 404, "route vanished"
        return 202, "accepted"

    def _invoke_hook(self, route: dict[str, Any], ctx: _WebhookContext) -> dict[str, Any] | None:
        hook = route.get("hook")
        if not hook:
            # hook 未指定: payload をそのままパラメータとする汎用パススルー。
            return dict(ctx.payload)
        module = self._scheduler._load_hook_module(Path(os.path.expanduser(hook)).resolve())
        if module is None:
            return None
        fn = getattr(module, "handle", None)
        if not callable(fn):
            log.warning("[WebhookServer] hook に handle() がありません: %s", hook)
            return None
        result = fn(ctx)
        if result is None:
            return None
        if not isinstance(result, dict):
            log.warning("[WebhookServer] handle() が dict/None 以外を返しました: %r", result)
            return None
        return result

    @staticmethod
    def _parse_json(raw: bytes) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {"_root": data}

    def _make_handler(self) -> type:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: D401 - stderr ノイズ抑制
                pass

            def _reply(self, code: int, msg: str) -> None:
                body = msg.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def _route_name(self) -> str | None:
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path.rstrip("/")
                prefix = server._path_prefix
                if not path.startswith(prefix + "/"):
                    return None
                name = path[len(prefix) + 1:]
                if not name or "/" in name:
                    return None
                return name

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path.rstrip("/") == server._path_prefix + "/_health":
                    self._reply(200, "ok")
                else:
                    self._reply(405, "method not allowed")

            def do_POST(self) -> None:
                name = self._route_name()
                if name is None:
                    self._reply(404, "not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length > server._max_body:
                    self._reply(413, "payload too large")
                    return
                raw = self.rfile.read(length) if length > 0 else b""
                parsed = urllib.parse.urlparse(self.path)
                query = {k: (v[0] if len(v) == 1 else v)
                         for k, v in urllib.parse.parse_qs(parsed.query).items()}
                headers = {k.lower(): v for k, v in self.headers.items()}
                status, msg = server._process(name, "POST", headers, query, raw)
                self._reply(status, msg)

        return Handler


