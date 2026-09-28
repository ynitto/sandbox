"""laya の導入スクリプト（install_laya）と CPU サーバ（laya_server）の契約。

実物の laya・PyTorch・モデルは入れない（CI では落とせない）。サーバは偽の Router を
差して、本家 Jev と同じ `/v1/systemone` の往復・日本語・認証・入力検査を押さえる。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install_laya  # noqa: E402
import laya_server  # noqa: E402


class FakeRouter:
    def __init__(self):
        self.calls = []
        self.loaded = ["multilingual"]

    def predict(self, state, questions, model=None, **kwargs):
        self.calls.append({"state": state, "questions": questions, "model": model, "kwargs": kwargs})
        answers = {}
        for name, q in questions.items():
            if q.get("type") not in ("choice", "score", "noul"):
                raise ValueError(f"question {name!r}: unknown type {q.get('type')!r}")
            if q["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.9, "confidence": 0.9, "answer_confidence": 0.9}
            else:
                first = next(iter(q["criteria"]))
                answers[name] = {"type": "choice", "choice": first, "confidence": 0.5,
                                 "answer_confidence": 0.8, "probabilities": {first: 0.8}}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 12, "output_tokens": 0},
                "routing": {"model": model}}


class ServerTests(unittest.TestCase):
    def start(self, api_key=None):
        self.router = FakeRouter()
        server = laya_server.make_server(laya_server.Predictor(self.router, "multilingual"),
                                         "127.0.0.1", 0, api_key)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def post(self, body, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + "/v1/systemone", data=data, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_japanese_round_trip_on_the_jev_protocol_pins_multilingual(self):
        self.start()
        status, data = self.post({"model": "jev-latest", "max_len": 2048,
                                  "state": {"request": "日報をまとめて"},
                                  "questions": {"kind": {"type": "choice", "instructions": "何の作業か",
                                                         "criteria": {"docs": "文書", "code": "実装"}},
                                                "ja": {"type": "noul", "instructions": "日本語か"}}})
        self.assertEqual(status, 200)
        self.assertEqual(data["answers"]["kind"]["choice"], "docs")
        self.assertEqual(data["answers"]["ja"]["noul"], 0.9)
        call = self.router.calls[0]
        self.assertEqual(call["model"], "multilingual", "Jev のモデル名は無視して多言語版で答える")
        self.assertEqual(call["kwargs"], {"max_len": 2048})
        self.assertEqual(call["state"]["request"], "日報をまとめて")

    def test_named_checkpoint_is_honoured(self):
        self.start()
        self.post({"model": "english", "state": "x",
                   "questions": {"a": {"type": "noul", "instructions": "?"}}})
        self.assertEqual(self.router.calls[0]["model"], "english")

    def test_health_says_cpu(self):
        self.start()
        with urllib.request.urlopen(self.base + "/health", timeout=10) as res:
            data = json.loads(res.read())
        self.assertEqual((data["status"], data["device"], data["model"]), ("ok", "cpu", "multilingual"))

    def test_bad_requests_are_refused_before_inference(self):
        self.start()
        self.assertEqual(self.post({"questions": {"a": {"type": "noul"}}})[0], 400)
        self.assertEqual(self.post({"state": "x", "questions": {}})[0], 400)
        self.assertEqual(self.post(None, raw=b"{not json")[0], 400)
        self.assertEqual(self.post({"state": "x", "max_len": 99999,
                                    "questions": {"a": {"type": "noul", "instructions": "?"}}})[0], 422)
        self.assertEqual(self.post({"state": "x", "questions": {"a": {"type": "boolean"}}})[0], 422,
                         "laya が受け付けない型は 422（中身は返す）")
        self.assertEqual(len(self.router.calls), 1, "形の検査で落ちたものは推論しない")

    def test_bearer_token_when_configured(self):
        self.start(api_key="s3cret")
        body = {"state": "x", "questions": {"a": {"type": "noul", "instructions": "?"}}}
        self.assertEqual(self.post(body)[0], 401)
        self.assertEqual(self.post(body, headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.post(body, headers={"Authorization": "Bearer s3cret"})[0], 200)

    def test_build_router_hides_the_gpu_and_asks_for_cpu(self):
        built = {}

        class Router:
            def __init__(self, **kwargs):
                built.update(kwargs)

            def preload(self, names):
                built["preload"] = names

        with mock.patch.dict(sys.modules, {"laya": types.SimpleNamespace(Router=Router)}), \
                mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0"}):
            laya_server.build_router("multilingual")
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "")
        self.assertEqual(built, {"device": "cpu", "max_loaded": 1, "preload": ["multilingual"]})

    def test_selftest_asks_a_japanese_question(self):
        router = FakeRouter()
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(laya_server.selftest(router, "multilingual"), 0)
        self.assertIn("日本語", json.dumps(router.calls[0], ensure_ascii=False))
        self.assertEqual(json.loads(out.getvalue())["kind"], "docs")


class InstallerTests(unittest.TestCase):
    def test_torch_comes_from_the_cpu_index_except_on_macos(self):
        self.assertEqual(install_laya.torch_index("Linux"), install_laya.TORCH_CPU_INDEX)
        self.assertEqual(install_laya.torch_index("Windows"), install_laya.TORCH_CPU_INDEX)
        self.assertIsNone(install_laya.torch_index("Darwin"), "macOS の通常版は CUDA を含まない")
        self.assertEqual(install_laya.torch_index("Linux", "https://mirror/cpu"), "https://mirror/cpu")

    def test_pip_steps_install_no_server_extras_and_no_cache(self):
        cmds = install_laya.pip_commands(Path("/v/bin/python"), system="Linux")
        flat = " ".join(" ".join(c) for c in cmds)
        for word in ("fastapi", "uvicorn", "laya[", "onnx", "tilelang"):
            self.assertNotIn(word, flat)
        self.assertTrue(all("--no-cache-dir" in c for c in cmds))
        torch_cmd = next(c for c in cmds if any(a.startswith("torch") for a in c))
        self.assertIn(install_laya.TORCH_CPU_INDEX, torch_cmd)
        laya_cmd = cmds[-1]
        self.assertIn("--no-deps", laya_cmd)
        self.assertIn(f"laya=={install_laya.LAYA_VERSION}", laya_cmd)
        mac_torch = next(c for c in install_laya.pip_commands(Path("p"), system="Darwin")
                         if any(a.startswith("torch") for a in c))
        self.assertNotIn("--index-url", mac_torch)

    def test_paths_per_os(self):
        home = Path("/h/laya")
        self.assertEqual(install_laya.venv_python(home, "Windows"), home / "venv" / "Scripts" / "python.exe")
        self.assertEqual(install_laya.venv_python(home, "Linux"), home / "venv" / "bin" / "python")
        name, text = install_laya.launcher_text(home, 8123, "Windows")
        self.assertEqual(name, "laya-serve.cmd")
        self.assertIn("--port 8123", text)
        self.assertIn("\r\n", text)
        name, text = install_laya.launcher_text(home, 8000, "Darwin")
        self.assertEqual(name, "laya-serve.sh")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn('--model-dir "' + str(home / "models" / "multilingual") + '"', text)

    def test_default_home_follows_the_agents_home(self):
        with mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": "/x/agents"}):
            self.assertEqual(install_laya.default_home(), Path("/x/agents/laya"))

    def test_license_classes(self):
        cases = {
            "MIT": "preferred", "Apache-2.0": "preferred", "MIT OR Apache-2.0": "preferred",
            "Apache Software License AND MIT License": "preferred",
            "BSD-3-Clause": "permissive", "BSD-3-Clause AND MIT": "permissive",
            "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0": "permissive",
            "Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT":
                "permissive",
            "PSF-2.0": "permissive",
            "MPL-2.0 AND MIT": "weak-copyleft", "Mozilla Public License 2.0 (MPL 2.0)": "weak-copyleft",
            "GPL-3.0-or-later": "copyleft", "LGPL-2.1": "copyleft", "GPL-2.0 OR MIT": "preferred",
            "": "unknown", "Permission is granted": "unknown",
        }
        for text, expected in cases.items():
            self.assertEqual(install_laya.classify_license(text), expected, text)

    def test_license_report_prefers_the_spdx_expression(self):
        report = install_laya.license_report([
            {"name": "a", "version": "1", "expression": "MIT", "license": "", "classifiers": []},
            {"name": "b", "version": "1", "expression": "", "license": "",
             "classifiers": ["OSI Approved", "BSD License"]},
            {"name": "c", "version": "1", "expression": "", "license": "GPLv3", "classifiers": []},
        ])
        self.assertEqual([r["name"] for r in report["preferred"]], ["a"])
        self.assertEqual([r["name"] for r in report["permissive"]], ["b"])
        self.assertEqual([r["name"] for r in report["copyleft"]], ["c"])

    def test_copyleft_stops_the_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = install_laya.Installer(Path(tmp), out=io.StringIO())
            rows = [{"name": "evil", "version": "1", "expression": "AGPL-3.0", "license": "", "classifiers": []}]
            done = types.SimpleNamespace(stdout=json.dumps(rows))
            with mock.patch.object(install_laya.subprocess, "run", return_value=done):
                with self.assertRaises(SystemExit) as ctx:
                    inst.check_licenses()
            self.assertIn("evil", str(ctx.exception))
            self.assertTrue((Path(tmp) / "licenses.json").exists())

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "laya"
            out = io.StringIO()
            with mock.patch.object(install_laya.subprocess, "run", side_effect=AssertionError("ran")):
                code = install_laya.Installer(home, dry_run=True, configure=False, out=out).install()
            self.assertEqual(code, 0)
            self.assertFalse(home.exists())
            text = out.getvalue()
            self.assertIn("multilingual/model.safetensors", text)
            self.assertIn("--selftest --model-dir", text)

    def test_configure_points_agent_herd_at_laya(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = install_laya.Installer(Path(tmp), port=8123, out=io.StringIO())
            ran = []
            with mock.patch.object(install_laya.shutil, "which", return_value="/bin/agent-herd"), \
                    mock.patch.object(install_laya.subprocess, "run", side_effect=lambda cmd, **kw: ran.append(cmd)):
                inst.configure_herd()
        self.assertEqual(ran, [["/bin/agent-herd", "config", "set", "select.jev.backend", "laya"],
                               ["/bin/agent-herd", "config", "set", "select.jev.endpoint",
                                "http://127.0.0.1:8123/v1/systemone"]])



def _fake_checkpoint(root: Path) -> Path:
    for name in install_laya.MODEL_FILES:
        if "." in name:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("{}" if name.endswith(".json") else "weights", encoding="utf-8")
        else:
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / "config.json").write_text("{}", encoding="utf-8")
    return root


class OfflineTests(unittest.TestCase):
    """Hugging Face へつながらない PC: 持ち込んだモデルで入れ、つながずに動かす。"""

    def test_export_then_import_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            online = tmp / "online"
            _fake_checkpoint(install_laya.model_path(online))
            (install_laya.model_path(online) / ".cache").mkdir()
            (install_laya.model_path(online) / ".cache" / "lock").write_text("x")
            bundle = install_laya.export_model(install_laya.model_path(online), tmp / "laya.zip")
            names = zipfile.ZipFile(bundle).namelist()
            self.assertIn("multilingual/model.safetensors", names)
            self.assertIn("multilingual/tokenizer/config.json", names)
            self.assertFalse(any(".cache" in n for n in names))

            offline = tmp / "offline"
            offline.mkdir()
            inst = install_laya.Installer(offline, model_from=bundle, out=io.StringIO())
            ran = []
            with mock.patch.object(install_laya.subprocess, "run", side_effect=lambda cmd, **kw: ran.append(cmd)):
                inst.place_model()
            self.assertEqual(install_laya.missing_model_files(install_laya.model_path(offline)), [])
            self.assertEqual(len(ran), 1, "落とさずに自己テストだけ回す")
            self.assertIn("--model-dir", ran[0])

    def test_model_folder_is_found_at_common_depths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _fake_checkpoint(tmp / "download" / "laya" / "multilingual")
            self.assertEqual(install_laya.find_model_dir(tmp / "download"),
                             tmp / "download" / "laya" / "multilingual")
            self.assertEqual(install_laya.find_model_dir(tmp / "download" / "laya"),
                             tmp / "download" / "laya" / "multilingual")
            (tmp / "empty").mkdir()
            self.assertIsNone(install_laya.find_model_dir(tmp / "empty"))

    def test_incomplete_or_unsafe_bundles_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "home").mkdir()
            partial = tmp / "partial"
            _fake_checkpoint(partial)
            (partial / "model.safetensors").unlink()
            with self.assertRaises(SystemExit):
                install_laya.Installer(tmp / "home", model_from=partial, out=io.StringIO())._copy_model_from(partial)
            evil = tmp / "evil.zip"
            with zipfile.ZipFile(evil, "w") as zf:
                zf.writestr("../../escape.txt", "x")
            with self.assertRaises(SystemExit) as ctx:
                install_laya.Installer(tmp / "home", out=io.StringIO())._copy_model_from(evil)
            self.assertIn("不正なパス", str(ctx.exception))
            self.assertFalse((tmp / "escape.txt").exists())

    def test_mirror_and_wheel_folder(self):
        inst = install_laya.Installer(Path("/h"), hf_endpoint="https://mirror.example", out=io.StringIO())
        self.assertEqual(inst.model_env["HF_ENDPOINT"], "https://mirror.example")
        cmds = install_laya.pip_commands(Path("/v/python"), system="Linux", find_links="/wheels")
        self.assertTrue(all("--no-index" in c and "/wheels" in c for c in cmds))
        self.assertFalse(any(install_laya.TORCH_CPU_INDEX in c for c in cmds))

    def test_server_with_a_model_dir_never_goes_online(self):
        built = {}

        class Router:
            def __init__(self, **kwargs):
                built.update(kwargs)

            def preload(self, names):
                built["preload"] = names

        with tempfile.TemporaryDirectory() as tmp:
            model_dir = _fake_checkpoint(Path(tmp) / "multilingual")
            with mock.patch.dict(sys.modules, {"laya": types.SimpleNamespace(Router=Router)}), \
                    mock.patch.dict(os.environ, {}, clear=False):
                laya_server.build_router("multilingual", model_dir=str(model_dir))
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
                self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
            self.assertEqual(built["models"], {"multilingual": (str(model_dir.resolve()), None)})
            with self.assertRaises(SystemExit):
                laya_server.build_router("multilingual", model_dir=str(Path(tmp) / "nope"))
        predictor = laya_server.Predictor(FakeRouter(), "multilingual", offline=True)
        self.assertEqual(predictor.resolve_model("english"), "multilingual", "手元に無いモデルは取りに行かない")


if __name__ == "__main__":
    unittest.main()
