"""インストーラのテスト: 1 つの実行可能な zip（.pyz）を作り、そのファイルだけで取得から受け渡しまで動くこと。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import zipfile

from helpers import FAKE_DAGU, ROOT, Env

INSTALL = ROOT / "install.py"


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", **kw)


class InstallTest(unittest.TestCase):
    def test_build_and_run_single_file(self):
        env = Env(self)
        pyz = env.tmp / "dist" / "change-watch.pyz"
        p = run([sys.executable, str(INSTALL), "build", "--out", str(pyz)])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        names = zipfile.ZipFile(pyz).namelist()
        self.assertIn("__main__.py", names)
        self.assertIn("changewatch/cli.py", names)
        self.assertIn("changewatch/plugins/dagu.py", names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.startswith(("test/", "tests/"))])
        # 置き場所を変えても、その 1 ファイルだけで動く（ソースの木を見ない）
        far = env.tmp / "elsewhere" / "aw.pyz"
        far.parent.mkdir()
        pyz.replace(far)
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all")}, {"r": {"source": "items", "id": "id", "watch": ["status"],
                                                               "targets": ["d"]}})
        base = [sys.executable, str(far), "--plugin", "dagu", "--config", str(env.config_path),
                "--connections", str(env.conn_file)]
        for _ in range(2):
            p = run(base + ["run"], cwd=str(far.parent))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            env.api.put(1, status="b")
        self.assertEqual(len(env.enqueued()), 1)

    def test_bundled_yaml_works_without_site_packages(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("同梱する PyYAML が無い")
        env = Env(self)
        pyz = env.tmp / "change-watch.pyz"
        p = run([sys.executable, str(INSTALL), "build", "--out", str(pyz)])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("yaml/__init__.py", zipfile.ZipFile(pyz).namelist())
        cfg = env.tmp / "c.yaml"
        cfg.write_text(f"state_dir: {json.dumps(str(env.tmp / 'st'))}\nsources:\n  s:\n    request:\n"
                       f"      url: http://127.0.0.1:9/\nrules: {{}}\n", encoding="utf-8")
        # -S で site-packages を読まない（PyYAML は zip の中のものだけ）
        p = run([sys.executable, "-S", str(pyz), "--config", str(cfg), "check"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = run([sys.executable, str(INSTALL), "build", "--out", str(pyz), "--no-yaml"])
        self.assertNotIn("yaml/__init__.py", zipfile.ZipFile(pyz).namelist())

    def test_output_survives_non_utf8_console(self):
        # Windows でパイプにつないだときの既定（cp1252）でも、日本語の表示で落ちない
        env = Env(self)
        p = subprocess.run([sys.executable, str(INSTALL), "build", "--out", str(env.tmp / "a.pyz"), "--no-yaml"],
                           capture_output=True, env=dict(os.environ, PYTHONIOENCODING="cp1252"))
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertIn("作りました", p.stdout.decode("utf-8"))

    def test_install_and_uninstall(self):
        env = Env(self)
        dest = env.tmp / "bin"
        p = run([sys.executable, str(INSTALL), "install", "--dir", str(dest), "--python", sys.executable])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        if os.name == "nt":
            launcher = dest / "change-watch.cmd"
            self.assertTrue((dest / "change-watch.pyz").is_file())
            p = run(["cmd", "/c", str(launcher), "--version"])
        else:
            launcher = dest / "change-watch"
            self.assertTrue(os.access(launcher, os.X_OK))
            self.assertTrue(launcher.read_bytes().startswith(f"#!{sys.executable}".encode()))
            p = run([str(launcher), "--version"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout.strip(), r"^\d+\.\d+\.\d+$")
        p = run([sys.executable, str(INSTALL), "uninstall", "--dir", str(dest)])
        self.assertEqual(p.returncode, 0)
        self.assertFalse(launcher.exists())


if __name__ == "__main__":
    unittest.main()
