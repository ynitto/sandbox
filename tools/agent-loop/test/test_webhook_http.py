#!/usr/bin/env python3
"""WebhookServer の実 HTTP E2E（標準ライブラリのみ）。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _FakeScheduler:
    def __init__(self, route=None):
        self.route = route
        self.enqueued: list[tuple[str, str]] = []
        self._hook_cache = {}
        self._hook_cache_lock = threading.Lock()

    def resolve_webhook_route(self, name: str):
        if self.route is None:
            return None
        return dict(self.route)

    def enqueue_external(self, name: str, prompt_text: str) -> bool:
        self.enqueued.append((name, prompt_text))
        return True

    def _load_hook_module(self, hook_path):
        return al.PeriodicScheduler._load_hook_module(self, hook_path)


class WebhookHttpE2ETests(unittest.TestCase):
    def setUp(self):
        self.port = _free_port()
        self.prefix = "/hooks"
        self.scheduler = _FakeScheduler(
            {
                "name": "demo",
                "prompt_template": "got {x}",
                "hook": None,
                "secret": "",
                "secret_header": None,
            }
        )
        self.server = al.WebhookServer(
            scheduler=self.scheduler,
            host="127.0.0.1",
            port=self.port,
            path_prefix=self.prefix,
            secret="s3cret",
            secret_header="X-Token",
            max_body_bytes=64,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                self._get("/_health")
                return
            except Exception:
                time.sleep(0.05)
        self.fail("webhook server did not become ready")

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{self.prefix}{path}"

    def _get(self, path: str) -> tuple[int, str]:
        req = urllib.request.Request(self._url(path), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def _post(self, path: str, body: bytes, headers: dict | None = None) -> tuple[int, str]:
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(self._url(path), data=body, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_health_200(self):
        code, body = self._get("/_health")
        self.assertEqual(code, 200)
        self.assertEqual(body, "ok")

    def test_unknown_route_404(self):
        self.scheduler.route = None
        code, _ = self._post("/missing", b"{}")
        self.assertEqual(code, 404)

    def test_get_non_health_405(self):
        code, _ = self._get("/demo")
        self.assertEqual(code, 405)

    def test_secret_mismatch_401(self):
        code, _ = self._post("/demo", b'{"x":1}', headers={"X-Token": "wrong"})
        self.assertEqual(code, 401)

    def test_body_too_large_413(self):
        code, _ = self._post("/demo", b"x" * 65, headers={"X-Token": "s3cret"})
        self.assertEqual(code, 413)

    def test_enqueue_202(self):
        code, body = self._post("/demo", b'{"x":"hi"}', headers={"X-Token": "s3cret"})
        self.assertEqual(code, 202)
        self.assertEqual(body, "accepted")
        self.assertEqual(self.scheduler.enqueued, [("demo", "got hi")])

    def test_hook_ignore_200(self):
        with mock.patch.object(self.server, "_invoke_hook", return_value=None):
            code, body = self._post("/demo", b'{"x":1}', headers={"X-Token": "s3cret"})
        self.assertEqual(code, 200)
        self.assertIn("ignored", body)
        self.assertEqual(self.scheduler.enqueued, [])

    def test_hook_exception_200(self):
        with mock.patch.object(self.server, "_invoke_hook", side_effect=RuntimeError("boom")):
            code, body = self._post("/demo", b'{"x":1}', headers={"X-Token": "s3cret"})
        self.assertEqual(code, 200)
        self.assertIn("ignored", body)
        self.assertEqual(self.scheduler.enqueued, [])


if __name__ == "__main__":
    unittest.main()
