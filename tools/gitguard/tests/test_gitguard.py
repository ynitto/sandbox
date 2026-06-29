"""gitguard の単体テスト（標準ライブラリ unittest）。

    python -m unittest discover -s tools/gitguard/tests
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_MOD = Path(__file__).resolve().parent.parent / "gitguard.py"
_spec = importlib.util.spec_from_file_location("gitguard", _MOD)
gg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gg)

EP = "git:gitlab.example.com"


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class BreakerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gg-test-")
        os.environ["GITGUARD_DIR"] = self.dir
        os.environ["GITGUARD_THRESHOLD"] = "3"
        os.environ["GITGUARD_COOLDOWN"] = "60"
        os.environ["GITGUARD_WINDOW"] = "120"
        os.environ.pop("GITGUARD_ENFORCE", None)
        os.environ.pop("GITGUARD_DISABLE", None)
        self.clock = _Clock()
        self._real_time = gg._time
        gg._time = self.clock

    def tearDown(self):
        gg._time = self._real_time
        for k in ("GITGUARD_DIR", "GITGUARD_THRESHOLD", "GITGUARD_COOLDOWN",
                  "GITGUARD_WINDOW", "GITGUARD_ENFORCE", "GITGUARD_DISABLE"):
            os.environ.pop(k, None)

    def test_opens_after_threshold_consecutive_infra_failures(self):
        for _ in range(2):
            self.assertEqual(gg.report(EP, gg.INFRA_FAIL), gg.CLOSED)   # しきい値未満は閉じたまま
        self.assertEqual(gg.report(EP, gg.INFRA_FAIL), gg.OPEN)         # 3 回目で開く
        allowed, state = gg.decide(EP)
        self.assertFalse(allowed)
        self.assertEqual(state, gg.OPEN)

    def test_app_failures_do_not_trip(self):
        # アプリ起因（マージ衝突・4xx 等）は何回失敗してもブレーカーを開かない（誤爆しない）。
        for _ in range(10):
            self.assertEqual(gg.report(EP, gg.APP_FAIL), gg.CLOSED)
        self.assertTrue(gg.decide(EP)[0])

    def test_success_resets_consecutive(self):
        gg.report(EP, gg.INFRA_FAIL)
        gg.report(EP, gg.INFRA_FAIL)
        gg.report(EP, gg.SUCCESS)                                       # 連続カウントをリセット
        gg.report(EP, gg.INFRA_FAIL)
        gg.report(EP, gg.INFRA_FAIL)
        self.assertTrue(gg.decide(EP)[0])                              # まだ 2 連続 → 開かない

    def test_window_expiry_resets_consecutive(self):
        gg.report(EP, gg.INFRA_FAIL)
        gg.report(EP, gg.INFRA_FAIL)
        self.clock.t += 200                                            # window(120s) 超過
        gg.report(EP, gg.INFRA_FAIL)                                   # 古い連続は失効 → 1 から
        self.assertTrue(gg.decide(EP)[0])

    def test_half_open_then_close_on_success(self):
        for _ in range(3):
            gg.report(EP, gg.INFRA_FAIL)                               # OPEN
        self.assertFalse(gg.decide(EP)[0])
        self.clock.t += 61                                             # クールダウン明け
        allowed, state = gg.decide(EP)
        self.assertTrue(allowed)                                       # プローブ 1 本だけ通す
        self.assertEqual(state, gg.HALF_OPEN)
        self.assertFalse(gg.decide(EP)[0])                            # 2 本目は短絡（プローブ進行中）
        self.assertEqual(gg.report(EP, gg.SUCCESS), gg.CLOSED)        # プローブ成功 → 復帰
        self.assertTrue(gg.decide(EP)[0])

    def test_half_open_reopens_on_failure(self):
        for _ in range(3):
            gg.report(EP, gg.INFRA_FAIL)
        self.clock.t += 61
        self.assertEqual(gg.decide(EP)[1], gg.HALF_OPEN)
        self.assertEqual(gg.report(EP, gg.INFRA_FAIL), gg.OPEN)       # プローブ失敗 → 即再オープン
        self.assertFalse(gg.decide(EP)[0])

    def test_guard_observe_mode_does_not_block(self):
        for _ in range(3):
            gg.report(EP, gg.INFRA_FAIL)                               # OPEN
        ran = {"n": 0}
        with gg.guard(EP, "probe") as g:                              # enforce 既定オフ → 通す
            ran["n"] += 1
            g.success()
        self.assertEqual(ran["n"], 1)

    def test_guard_enforce_blocks_when_open(self):
        os.environ["GITGUARD_ENFORCE"] = "1"
        for _ in range(3):
            gg.report(EP, gg.INFRA_FAIL)
        with self.assertRaises(gg.CircuitOpenError):
            with gg.guard(EP, "probe"):
                self.fail("enforce 時は本体に入らない")

    def test_guard_http_status_classification(self):
        with gg.guard(EP, "GET /x") as g:
            g.http_status(503)
        self.assertEqual(gg.read_events()[-1]["outcome"], gg.INFRA_FAIL)
        with gg.guard(EP, "GET /y") as g:
            g.http_status(404)
        self.assertEqual(gg.read_events()[-1]["outcome"], gg.APP_FAIL)

    def test_disabled_is_passthrough(self):
        os.environ["GITGUARD_DISABLE"] = "1"
        for _ in range(10):
            gg.report(EP, gg.INFRA_FAIL)
        self.assertTrue(gg.decide(EP)[0])                             # 無効化時は常に通す

    def test_events_and_aggregate(self):
        gg.report(EP, gg.SUCCESS, op="fetch", latency_ms=100)
        gg.report(EP, gg.INFRA_FAIL, op="fetch", latency_ms=0)
        agg = gg.aggregate()
        self.assertEqual(agg[EP]["total"], 2)
        self.assertEqual(agg[EP][gg.SUCCESS], 1)
        self.assertEqual(agg[EP][gg.INFRA_FAIL], 1)

    def test_reset_clears_state(self):
        for _ in range(3):
            gg.report(EP, gg.INFRA_FAIL)
        self.assertFalse(gg.decide(EP)[0])
        self.assertEqual(gg.reset(EP), 1)
        self.assertTrue(gg.decide(EP)[0])


class GitClassifyTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(gg.classify_git(0, ""), gg.SUCCESS)
        self.assertEqual(gg.classify_git(128, "Could not resolve host: x"), gg.INFRA_FAIL)
        self.assertEqual(gg.classify_git(128, "The remote end hung up unexpectedly"), gg.INFRA_FAIL)
        self.assertEqual(gg.classify_git(1, "CONFLICT (content): Merge conflict"), gg.APP_FAIL)
        self.assertEqual(gg.classify_git(128, "Authentication failed"), gg.APP_FAIL)

    def test_endpoint_for_url(self):
        self.assertEqual(gg.endpoint_for_url("https://h.example.com/a/b.git"), "git:h.example.com")
        self.assertEqual(gg.endpoint_for_url("git@h.example.com:a/b.git"), "git:h.example.com")
        self.assertEqual(gg.endpoint_for_url("https://h.example.com", "gitlab"), "gitlab:h.example.com")


class GitlabApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gg-api-")
        os.environ["GITGUARD_DIR"] = self.dir
        os.environ["GITGUARD_THRESHOLD"] = "2"
        os.environ.pop("GITGUARD_ENFORCE", None)
        os.environ.pop("GITGUARD_DISABLE", None)

    def tearDown(self):
        for k in ("GITGUARD_DIR", "GITGUARD_THRESHOLD", "GITGUARD_ENFORCE", "GITGUARD_DISABLE"):
            os.environ.pop(k, None)

    def _patch_urlopen(self, fn):
        import urllib.request
        self._real = urllib.request.urlopen
        urllib.request.urlopen = fn
        self.addCleanup(lambda: setattr(urllib.request, "urlopen", self._real))

    def test_success_records_and_returns(self):
        import io

        class Resp:
            status = 200
            def read(self): return b'{"id": 1}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        self._patch_urlopen(lambda req, timeout=30: Resp())
        status, body = gg.gitlab_api("gitlab.example.com", "GET", "/projects/1", token="t")
        self.assertEqual((status, body), (200, {"id": 1}))
        self.assertEqual(gg.read_events()[-1]["outcome"], gg.SUCCESS)

    def test_5xx_trips_breaker(self):
        import urllib.error

        def boom(req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)
        self._patch_urlopen(boom)
        ep = gg.endpoint_for_url("https://gitlab.example.com", "gitlab")
        for _ in range(2):
            status, _ = gg.gitlab_api("gitlab.example.com", "GET", "/x")
            self.assertEqual(status, 503)
        self.assertFalse(gg.decide(ep)[0])           # 2 回の 5xx でブレーカーが開く

    def test_404_does_not_trip(self):
        import urllib.error

        def notfound(req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        self._patch_urlopen(notfound)
        ep = gg.endpoint_for_url("https://gitlab.example.com", "gitlab")
        for _ in range(5):
            gg.gitlab_api("gitlab.example.com", "GET", "/missing")
        self.assertTrue(gg.decide(ep)[0])            # 4xx はブレーカーを開かない


if __name__ == "__main__":
    unittest.main()
