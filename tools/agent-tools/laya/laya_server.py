"""laya_server — laya を CPU だけで動かし、本家 Jev と同じ `/v1/systemone` で答える小さなサーバ。

laya 付属の `laya-serve` と同じ口（POST /v1/systemone・GET /health・任意の Bearer 認証）を
**標準ライブラリの http.server だけ**で出す。fastapi / uvicorn を入れない分、導入が軽く、
入る部品のライセンスも少なくて済む（`install_laya.py` が入れる）。

- CPU 固定: torch を読み込む前に GPU を隠し、`Router(device="cpu")` で組む。
- 日本語: 既定のモデルは多言語版（multilingual）。依頼に英語の説明が混ざっても日本語を
  英語用のモデルへ流さない。
- 推論は 1 本ずつ（CPU 1 台に同時に積んでも速くならない）。受付は並行で、/health は
  推論中も答える。

使い方:
  python laya_server.py [--host 127.0.0.1] [--port 8000] [--model multilingual] [--threads N]
  python laya_server.py --selftest      # 日本語の問いを 1 つ解いて結果を出す（導入の確認）

環境変数: LAYA_API_KEY（付けると Authorization: Bearer が要る）。
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KNOWN_MODELS = ("multilingual", "english", "typed-decisions")
DEFAULT_MODEL = "multilingual"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_QUESTIONS = 64
MAX_TOKEN_BUDGET = 8192

SELFTEST_STATE = {"request": "README の誤字を直して、日本語の言い回しを整えてください。"}
SELFTEST_QUESTIONS = {
    "kind": {"type": "choice", "instructions": "この依頼は何の作業か",
             "criteria": {"docs": "文書の修正・校正", "code": "プログラムの実装・修正",
                          "other": "それ以外"}},
    "japanese": {"type": "noul", "instructions": "依頼は日本語で書かれているか"},
}


def force_cpu() -> None:
    """torch を読み込む前に CUDA を見えなくする（Apple の MPS は device="cpu" の指定で使わない）。"""
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def build_router(model: str, threads: "int | None" = None):
    """CPU の Router を組み、使うモデルを先に読み込む。"""
    force_cpu()
    if threads:
        import torch
        torch.set_num_threads(int(threads))
    from laya import Router
    router = Router(device="cpu", max_loaded=1)
    router.preload([model])
    return router


class Predictor:
    """Router への窓口。推論は 1 本ずつ通す。"""

    def __init__(self, router, model: str):
        self.router = router
        self.model = model
        self.lock = threading.Lock()
        self.started = time.time()

    def resolve_model(self, requested) -> str:
        name = str(requested or "").strip().lower()
        return name if name in KNOWN_MODELS else self.model

    def predict(self, body: dict) -> dict:
        state = body.get("state")
        questions = body.get("questions")
        if state is None:
            raise BadRequest(400, "'state' is required")
        if not isinstance(questions, dict) or not questions:
            raise BadRequest(400, "'questions' must be a non-empty object")
        if len(questions) > MAX_QUESTIONS:
            raise BadRequest(413, f"too many questions ({len(questions)} > {MAX_QUESTIONS})")
        kwargs = {}
        for key in ("max_len", "head_max_len"):
            if body.get(key) is None:
                continue
            value = body[key]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_TOKEN_BUDGET:
                raise BadRequest(422, f"{key} must be an integer 1-{MAX_TOKEN_BUDGET}")
            kwargs[key] = value
        model = self.resolve_model(body.get("model"))
        with self.lock:
            try:
                return self.router.predict(state, questions, model=model, **kwargs)
            except ValueError as exc:
                raise BadRequest(422, str(exc)[:500]) from exc

    def health(self) -> dict:
        return {"status": "ok", "model": self.model, "device": "cpu",
                "loaded": list(getattr(self.router, "loaded", []) or []),
                "uptime_sec": round(time.time() - self.started, 1)}


class BadRequest(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def make_handler(predictor: Predictor, api_key: "str | None"):
    expected = ("Bearer " + api_key).encode("utf-8", "surrogateescape") if api_key else b""

    class Handler(BaseHTTPRequestHandler):
        server_version = "laya-cpu"

        def log_message(self, fmt, *args):  # 1 行だけ標準エラーへ
            sys.stderr.write("[laya] " + (fmt % args) + "\n")

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") == "/health":
                self._send(200, predictor.health())
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/v1/systemone":
                self._send(404, {"detail": "not found"})
                return
            if api_key:
                supplied = (self.headers.get("Authorization") or "").encode("utf-8", "surrogateescape")
                if not hmac.compare_digest(supplied, expected):
                    self._send(401, {"detail": "invalid or missing bearer token"})
                    return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0:
                self._send(400, {"detail": "request body must be valid JSON"})
                return
            if length > MAX_BODY_BYTES:
                self._send(413, {"detail": "request body too large"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, RecursionError):
                self._send(400, {"detail": "request body must be valid JSON"})
                return
            if not isinstance(body, dict):
                self._send(400, {"detail": "request body must be an object"})
                return
            started = time.perf_counter()
            try:
                result = predictor.predict(body)
            except BadRequest as exc:
                self._send(exc.status, {"detail": exc.detail})
                return
            except Exception as exc:  # noqa: BLE001 — 中身は返さずログにだけ残す
                sys.stderr.write(f"[laya] inference failed: {exc!r}\n")
                self._send(500, {"detail": "inference failed"})
                return
            if isinstance(result, dict):
                result.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
                result["inference_ms"] = round((time.perf_counter() - started) * 1000, 1)
            self._send(200, result)

    return Handler


def make_server(predictor: Predictor, host: str, port: int, api_key: "str | None" = None):
    return ThreadingHTTPServer((host, port), make_handler(predictor, api_key))


def selftest(router, model: str) -> int:
    started = time.perf_counter()
    result = router.predict(SELFTEST_STATE, SELFTEST_QUESTIONS, model=model)
    answers = result.get("answers") or {}
    print(json.dumps({"model": model, "seconds": round(time.perf_counter() - started, 2),
                      "kind": (answers.get("kind") or {}).get("choice"),
                      "japanese": (answers.get("japanese") or {}).get("noul"),
                      "routing": result.get("routing")}, ensure_ascii=False))
    return 0 if answers.get("kind") and answers.get("japanese") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="laya_server", description="laya を CPU で動かす /v1/systemone サーバ")
    parser.add_argument("--host", default=os.environ.get("LAYA_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAYA_PORT", DEFAULT_PORT)))
    parser.add_argument("--model", default=os.environ.get("LAYA_MODEL", DEFAULT_MODEL), choices=KNOWN_MODELS)
    parser.add_argument("--threads", type=int, default=int(os.environ.get("LAYA_THREADS") or 0) or None,
                        help="推論に使う CPU スレッド数（物理コア数以下。省略時は torch の既定）")
    parser.add_argument("--selftest", action="store_true", help="日本語の問いを 1 つ解いて終わる")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows の古いコンソールでも日本語で落ちない
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    router = build_router(args.model, args.threads)
    if args.selftest:
        return selftest(router, args.model)
    predictor = Predictor(router, args.model)
    server = make_server(predictor, args.host, args.port, os.environ.get("LAYA_API_KEY") or None)
    print(f"[laya] http://{args.host}:{args.port}/v1/systemone（CPU・{args.model}）", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
