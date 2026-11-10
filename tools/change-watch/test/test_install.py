"""インストーラのテスト: 1 つの実行可能な zip（.pyz）を作り、そのファイルだけで取得から受け渡しまで動くこと。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from helpers import ROOT, Env

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
        self.assertFalse([n for n in names if "__pycache__" in n or n.startswith(("test/", "tests/"))])
        # 置き場所を変えても、その 1 ファイルだけで動く（ソースの木を見ない）
        far = env.tmp / "elsewhere" / "aw.pyz"
        far.parent.mkdir()
        pyz.replace(far)
        env.api.put(1, status="a")
        env.write_config({"items": env.source("/all", key="id")})
        base = [sys.executable, str(far), "--config", str(env.config_path), "--connections", str(env.conn_file)]
        got = []
        for _ in range(2):
            p = run(base + ["get", "--source", "items", "--consumer", "c"], cwd=str(far.parent))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            got.append(json.loads(p.stdout))
            env.api.put(1, status="b")
        self.assertEqual([[c["kind"] for c in g] for g in got], [[], ["changed"]])

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
                       "      url: http://127.0.0.1:9/\n", encoding="utf-8")
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


class AgentInstallTest(unittest.TestCase):
    """--agent: エージェントのプログラム・設定の雛形・ワークフローを、どのエージェントも同じ決まりの場所に置く。"""

    def setUp(self):
        self.env = Env(self)
        self.home = self.env.tmp / "home"
        self.bin = self.env.tmp / "bin"
        self.home.mkdir()
        self.osenv = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))

    def install(self, *args):
        p = run([sys.executable, str(INSTALL), *args, "--dir", str(self.bin), "--python", sys.executable],
                env=self.osenv)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def agents_dir(self):
        return self.bin / "agents" if os.name == "nt" else self.home / ".local" / "share" / "change-watch" / "agents"

    def test_every_agent_lands_in_the_same_layout(self):
        import install
        names = install.available_agents()
        self.assertEqual(names, ["gitlab-issue-agent", "gitlab-issue-hub-agent", "gitlab-mr-review-agent",
                                 "redmine-ticket-agent"])
        out = self.install("install", *[x for n in names for x in ("--agent", n)])
        cfg_root = self.home / ".config" / "change-watch"
        dags = self.home / ".config" / "dagu" / "dags"
        for n in names:
            with self.subTest(n):
                programs, templates, workflows = install.agent_files(n)
                for f in programs:
                    self.assertEqual((self.agents_dir() / n / f.name).read_bytes(), f.read_bytes())
                for f in templates:
                    self.assertTrue((cfg_root / n / f.name[:-len(".example")]).is_file(), f)
                self.assertTrue((cfg_root / n / "change-watch.yaml").is_file())
                self.assertIn(f"change-watch-{n}-maintenance.yaml", [f.name[:-len(".example")] for f in workflows])
                for f in workflows:
                    text = (dags / f.name[:-len(".example")]).read_text(encoding="utf-8")
                    self.assertIn(f"{(cfg_root / n / 'change-watch.yaml').as_posix()}", text.replace("\\", "/"))
                cfg = (cfg_root / n / "change-watch.yaml").read_text(encoding="utf-8")
                self.assertIn(f"state_dir: ~/.local/state/change-watch/{n}", cfg)
        # 置き換えずに残るのは、作業フォルダなど自分で決めるものだけ
        left = [line for line in out.splitlines() if line.startswith("  ") and "/home/you" in line]
        self.assertTrue(left)
        self.assertFalse([x for x in left if "/home/you/work/" not in x], left)

    def test_installed_workflow_command_line_runs(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("ワークフローを読むのに PyYAML が要る")
        self.install("install", "--agent", "redmine-ticket-agent")
        conn = self.home / ".github" / "connections.yaml"
        conn.parent.mkdir()
        conn.write_text("redmine:\n  - label: default\n    url: http://127.0.0.1:9\n    token: t\n", encoding="utf-8")
        dag = yaml.safe_load((self.home / ".config" / "dagu" / "dags" / "change-watch-auto-close.yaml")
                             .read_text(encoding="utf-8"))
        step = dag["steps"][0]["with"]
        args = step["args"]
        argv = [step["command"]] + args[:args.index("get")] + ["check"]     # ワークフローのとおりの argv で設定を確かめる
        p = run(argv, env=self.osenv)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("問題ありません", p.stdout + p.stderr)

    def test_reinstall_updates_programs_but_keeps_edited_files(self):
        self.install("install", "--agent", "gitlab-mr-review-agent")
        cfg = self.home / ".config" / "change-watch" / "gitlab-mr-review-agent" / "change-watch.yaml"
        prog = self.agents_dir() / "gitlab-mr-review-agent" / "gitlab_mr.py"
        cfg.write_text("# 直した設定\n", encoding="utf-8")
        prog.write_text("# 古い版\n", encoding="utf-8")
        out = self.install("install", "--agent", "gitlab-mr-review-agent")
        self.assertIn("既にあるので触りません", out)
        self.assertEqual(cfg.read_text(encoding="utf-8"), "# 直した設定\n")
        self.assertNotEqual(prog.read_text(encoding="utf-8"), "# 古い版\n")
        p = run([sys.executable, str(INSTALL), "uninstall", "--agent", "gitlab-mr-review-agent", "--dir", str(self.bin)],
                env=self.osenv)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(prog.parent.exists())
        self.assertTrue(cfg.exists())                                        # 設定は残す

    def test_unknown_agent_is_refused(self):
        p = run([sys.executable, str(INSTALL), "install", "--agent", "nope", "--dir", str(self.bin)], env=self.osenv)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("redmine-ticket-agent", p.stdout + p.stderr)

    def test_windows_rendering(self):
        import install
        home, bin_dir = Path("C:/Users/me"), Path("C:/Users/me/AppData/Local/Programs/change-watch")
        kw = dict(windows=True, home=home, bin_dir=bin_dir, python="C:/Python311/python.exe")
        dag = install.render((install.AGENTS / "redmine-ticket-agent" / "change-watch-auto-close.yaml.example")
                             .read_text(encoding="utf-8"), is_dag=True, **kw)
        self.assertNotIn("/home/you/.local/bin", dag)
        self.assertIn('command: "C:/Python311/python.exe"\n      args:\n'
                      '        - "C:/Users/me/AppData/Local/Programs/change-watch/change-watch.pyz"\n'
                      '        - "--config"\n        - "C:/Users/me/.config/change-watch/redmine-ticket-agent/change-watch.yaml"', dag)
        env = install.render((install.AGENTS / "gitlab-mr-review-agent" / "gitlab-mr-review-agent.env.example")
                             .read_text(encoding="utf-8"), is_dag=False, **kw)
        self.assertIn("MR_REVIEW_CLI=C:/Python311/python.exe C:/Users/me/AppData/Local/Programs/change-watch/agents/"
                      "gitlab-mr-review-agent/gitlab_mr.py", env)
        hub = install.render((install.AGENTS / "gitlab-issue-hub-agent" / "change-watch-issue-hub.yaml.example")
                             .read_text(encoding="utf-8"), is_dag=True, **kw)
        self.assertIn('command: "C:/Python311/python.exe"\n      args:\n'
                      '        - "C:/Users/me/AppData/Local/Programs/change-watch/agents/gitlab-issue-hub-agent/gitlab_hub.py"', hub)
        self.assertNotIn('"python3"', hub)                              # ワークフローの中の Python も、この PC の Python に


if __name__ == "__main__":
    unittest.main()
