"""環境補完が 1 実装であること、その振る舞いが複製時代の正典と同じであることを縛る。

かつては `ollama_adapter.py` を正典とし、単体ファイルで配っていた 2 つ（`agent-aider` /
`agent-opencode`）が同じコードを**複製**して持っていた。旧 `test_adapter_env_parity.py` は
その 3 者を AST で突き合わせ、「片方だけ直しても両方のテストが緑」を防いでいた。

agent-herd が 3 adapter を 1 つの zipapp に畳んだので複製の理由が消え、実装は
`agentcore.hostenv` 1 つになった。だから縛るものが変わる——**一致**ではなく
**同一オブジェクトであること**（＝写しが復活していないこと）を縛る。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import aider_adapter, hostenv, ollama_adapter, opencode_adapter  # noqa: E402

SHARED = ("_complete_ollama_env", "_import_profile_env", "load_profile_env")
CONSTANTS = ("_PROFILE_ENV_PREFIXES", "_PROFILE_ENV_EXACT")
USERS = {"ollama": ollama_adapter, "aider": aider_adapter, "opencode": opencode_adapter}


class HostenvIsTheOnlyImplementationTests(unittest.TestCase):
    def test_every_adapter_uses_the_same_objects(self):
        """写しではなく同一オブジェクト。`is` で見るので、複製が戻れば必ず落ちる。"""
        for label, module in USERS.items():
            for name in SHARED:
                self.assertIs(getattr(module, name), getattr(hostenv, name),
                              f"{label} の {name} が hostenv と別物です"
                              "（環境補完の複製は作らない — 直すときは hostenv だけ）")
            for name in CONSTANTS:
                self.assertIs(getattr(module, name), getattr(hostenv, name),
                              f"{label} の {name} が hostenv と別物です")

    def test_no_adapter_redefines_the_shared_names(self):
        """再輸出であって再定義でないこと（import 経由なら __module__ は hostenv）。"""
        for label, module in USERS.items():
            for name in SHARED:
                self.assertEqual(getattr(module, name).__module__, "agentcore.hostenv",
                                 f"{label} が {name} を自前で定義しています")


class CompleteOllamaEnvTests(unittest.TestCase):
    """複製時代の正典と同じ振る舞いであることの確認（移設で挙動を変えていない証明）。"""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("OLLAMA_HOST", "OLLAMA_API_BASE", "NO_PROXY", "no_proxy")}
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_host_fills_in_api_base_with_a_scheme(self):
        os.environ["OLLAMA_HOST"] = "192.168.1.20:11434"
        hostenv._complete_ollama_env()
        self.assertEqual(os.environ["OLLAMA_API_BASE"], "http://192.168.1.20:11434")

    def test_api_base_fills_in_host(self):
        os.environ["OLLAMA_API_BASE"] = "http://gpu-pc:11434"
        hostenv._complete_ollama_env()
        self.assertEqual(os.environ["OLLAMA_HOST"], "http://gpu-pc:11434")

    def test_the_inference_host_always_bypasses_the_proxy(self):
        """親環境が不完全な NO_PROXY を持っていても、推論ホストは必ず追記される。

        ここが抜けると接続がプロキシへ流れ、504 Gateway Timeout という
        「設定はしてあるのに動かない」形で出る。
        """
        os.environ["OLLAMA_API_BASE"] = "http://gpu-pc:11434"
        os.environ["NO_PROXY"] = "localhost"
        hostenv._complete_ollama_env()
        self.assertIn("gpu-pc", os.environ["NO_PROXY"].split(","))
        self.assertIn("localhost", os.environ["NO_PROXY"].split(","))

    def test_both_spellings_of_no_proxy_end_up_identical(self):
        """urllib は小文字を見る。読み手ごとに違う値を見せない。"""
        os.environ["OLLAMA_API_BASE"] = "http://gpu-pc:11434"
        os.environ["NO_PROXY"] = "a"
        os.environ["no_proxy"] = "b"
        hostenv._complete_ollama_env()
        self.assertEqual(os.environ["NO_PROXY"], os.environ["no_proxy"])
        self.assertEqual(set(os.environ["NO_PROXY"].split(",")), {"a", "b", "gpu-pc"})

    def test_unset_host_falls_back_to_loopback(self):
        hostenv._complete_ollama_env()
        entries = os.environ["NO_PROXY"].split(",")
        self.assertIn("localhost", entries)
        self.assertIn("127.0.0.1", entries)

    def test_a_configured_environment_does_not_read_the_profile(self):
        """全部そろっていれば profile を読まない（余計な subprocess を足さない）。"""
        os.environ["OLLAMA_HOST"] = "http://gpu-pc:11434"
        os.environ["OLLAMA_API_BASE"] = "http://gpu-pc:11434"
        os.environ["NO_PROXY"] = "gpu-pc"
        calls = []
        original = hostenv._import_profile_env
        hostenv._import_profile_env = lambda path: calls.append(path) or {}
        try:
            self.assertEqual(hostenv.load_profile_env(), {})
        finally:
            hostenv._import_profile_env = original
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
