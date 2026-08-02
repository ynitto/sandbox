# -*- coding: utf-8 -*-
"""winauto の単体テスト（標準ライブラリ unittest・Windows 不要）。

Windows/pywinauto を必要としない層だけを決定的に検証する:

  * デスクトップ排他ロック（G3）… 相互排他・読み取り専用コマンドの素通り・
    タイムアウト・解放後の再取得。POSIX の fcntl 経路で検証する
    （Windows では msvcrt 経路に切り替わるが、契約は同一）。
  * doctor（G1）… 非対応プラットフォームでの所見と終了コード、
    ノイズ混じり出力からの JSON 抽出。
  * WSL ラッパー（G1）… 生成される bash の引数変換。偽の wslpath と
    偽の Windows Python を PATH に置いて、実際にラッパーを実行して確かめる。

    python -m unittest discover -s tools/winauto/tests
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WINAUTO_PY = _ROOT / "winauto.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


winauto = _load("winauto_under_test", _WINAUTO_PY)
installer = _load("winauto_install_under_test", _ROOT / "install.py")


# ---------------------------------------------------------------------------
# デスクトップ排他ロック
# ---------------------------------------------------------------------------

# 別プロセスでロックを取る最小スクリプト。ロック取得を stdout で知らせてから
# hold 秒だけ保持する（親はこの合図を見てから競合を仕掛ける）。
_HOLDER = """
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("w", {winauto!r})
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
os.environ["WINAUTO_LOCK_FILE"] = {lock!r}
with w.desktop_lock({command!r}, 30):
    print("HELD", flush=True)
    time.sleep({hold})
print("RELEASED", flush=True)
"""


class DesktopLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self.tmp.name, "winauto-desktop.lock")
        os.environ["WINAUTO_LOCK_FILE"] = self.lock

    def tearDown(self):
        os.environ.pop("WINAUTO_LOCK_FILE", None)
        self.tmp.cleanup()

    def _reap(self, proc):
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def _spawn_holder(self, hold=1.0, command="click"):
        src = _HOLDER.format(winauto=str(_WINAUTO_PY), lock=self.lock,
                             command=command, hold=hold)
        proc = subprocess.Popen([sys.executable, "-c", src],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        self.addCleanup(self._reap, proc)
        # 「取れた」の合図を待つ。ここを sleep で代用すると競合が不安定になる。
        self.assertEqual(proc.stdout.readline().strip(), "HELD",
                         "holder がロックを取得できなかった")
        return proc

    def test_lock_path_honours_env_override(self):
        self.assertEqual(winauto._lock_path(), Path(self.lock))

    def test_second_process_waits_for_release(self):
        """GUI を触るコマンドは同時に走らない（agent-flow の並列ワーカー対策）。"""
        holder = self._spawn_holder(hold=1.0)
        try:
            started = time.time()
            with winauto.desktop_lock("click", 30):
                waited = time.time() - started
        finally:
            holder.wait(timeout=30)
        self.assertGreaterEqual(waited, 0.8,
                                f"保持中なのに待たずに取得した（{waited:.2f}s）")

    def test_readonly_commands_do_not_wait(self):
        """apps/tree/wait などの読み取り専用はロックを取らない（発行を止めない）。"""
        holder = self._spawn_holder(hold=1.5)
        try:
            for command in ("apps", "tree", "get-text", "wait", "doctor"):
                started = time.time()
                with winauto.desktop_lock(command, 30):
                    pass
                self.assertLess(time.time() - started, 0.5,
                                f"{command} がロック待ちしている")
        finally:
            holder.wait(timeout=30)

    def test_timeout_raises_with_actionable_message(self):
        holder = self._spawn_holder(hold=2.0)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                with winauto.desktop_lock("click", 0.5):
                    pass
        finally:
            holder.wait(timeout=30)
        message = str(ctx.exception)
        self.assertIn("--no-lock", message)
        self.assertIn("WINAUTO_LOCK_TIMEOUT", message)

    def test_disabled_lock_ignores_holder(self):
        holder = self._spawn_holder(hold=1.5)
        try:
            started = time.time()
            with winauto.desktop_lock("click", 30, enabled=False):
                pass
            self.assertLess(time.time() - started, 0.5)
        finally:
            holder.wait(timeout=30)

    def test_lock_is_reusable_after_release(self):
        for _ in range(3):
            with winauto.desktop_lock("click", 5):
                pass
        # 解放後は保持者情報も残さない（doctor が「空き」と言えること）。
        self.assertFalse(Path(self.lock + ".owner").exists())

    def test_owner_hint_names_the_holder(self):
        holder = self._spawn_holder(hold=1.0)
        try:
            hint = winauto._owner_hint(Path(self.lock))
            self.assertIn(f"pid={holder.pid}", hint)
            self.assertIn("command=click", hint)
        finally:
            holder.wait(timeout=30)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

class DoctorTest(unittest.TestCase):
    def test_parse_json_tail_skips_leading_noise(self):
        noisy = ("CMD does not support UNC paths as current directories.\n"
                 '{"scope": "windows", "ok": true, "checks": []}\n')
        self.assertEqual(winauto._parse_json_tail(noisy)["scope"], "windows")

    def test_parse_json_tail_returns_none_without_json(self):
        self.assertIsNone(winauto._parse_json_tail("no json here"))

    def test_parse_json_tail_handles_brace_inside_noise(self):
        noisy = 'warning: bad {token} here\n{"ok": true}'
        self.assertEqual(winauto._parse_json_tail(noisy), {"ok": True})

    @unittest.skipIf(sys.platform == "win32", "Windows では windows scope になる")
    def test_doctor_reports_unsupported_platform(self):
        proc = subprocess.run(
            [sys.executable, str(_WINAUTO_PY), "doctor", "--output", "json"],
            capture_output=True, text=True, timeout=120)
        payload = json.loads(proc.stdout)
        if payload["scope"] == "wsl":
            self.skipTest("WSL 上で実行されている（ブリッジ実機診断が走る）")
        self.assertEqual(payload["scope"], "unsupported")
        self.assertFalse(payload["ok"])
        self.assertEqual(proc.returncode, 1)

    def test_doctor_is_not_locked(self):
        """doctor 自身はロックを取らない（保持中でも診断できる必要がある）。"""
        self.assertNotIn("doctor", winauto.LOCKED_COMMANDS)

    def test_locked_commands_cover_input_stealing_commands(self):
        for command in ("click", "type", "keys", "launch", "screenshot", "run"):
            self.assertIn(command, winauto.LOCKED_COMMANDS)


# ---------------------------------------------------------------------------
# WSL ラッパー（引数のパス変換）
# ---------------------------------------------------------------------------

# 偽 wslpath: -w は POSIX パスを W:\... に、-u は素通し。
_FAKE_WSLPATH = r"""#!/bin/bash
case "$1" in
  -w) printf 'W:%s' "$(printf '%s' "$2" | tr '/' '\\')" ;;
  -u) printf '%s' "$2" ;;
  *)  printf '%s' "$1" ;;
esac
"""

# 偽 Windows Python: 第1引数（winauto.py のパス）を捨てて、残りを1行ずつ出す。
_FAKE_PYTHON = r"""#!/bin/bash
shift
for a in "$@"; do printf '%s\n' "$a"; done
"""


def _write_exec(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class WslWrapperTest(unittest.TestCase):
    """生成された bash ラッパーを実際に走らせて引数変換を確かめる。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        _write_exec(self.bin / "wslpath", _FAKE_WSLPATH)
        self.fake_python = _write_exec(root / "python.exe", _FAKE_PYTHON)
        self.cwd = root / "work"
        self.cwd.mkdir()

        self.wrapper = _write_exec(
            root / "winauto",
            installer.render_wsl_wrapper(
                install_dir=r"C:\Users\me\.local\bin\winauto",
                win_python=r"C:\Python312\python.exe",
                win_python_unix=str(self.fake_python),
                win_script=r"C:\Users\me\.local\bin\winauto\winauto.py",
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, env_extra=None):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(env_extra or {})
        proc = subprocess.run([str(self.wrapper), *args], capture_output=True,
                              text=True, cwd=str(self.cwd), env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.splitlines()

    def test_screenshot_output_path_is_converted(self):
        out = self._run("screenshot", "--app", "notepad", "--output", "/tmp/sc.png")
        self.assertEqual(out, ["screenshot", "--app", "notepad", "--output",
                               r"W:\tmp\sc.png"])

    def test_screenshot_output_equals_form_is_converted(self):
        out = self._run("screenshot", "--output=/tmp/sc.png")
        self.assertEqual(out, ["screenshot", r"--output=W:\tmp\sc.png"])

    def test_format_style_output_is_left_alone(self):
        """apps/tree の --output は text|json の書式指定。パス変換してはいけない。"""
        self.assertEqual(self._run("apps", "--output", "json"),
                         ["apps", "--output", "json"])
        self.assertEqual(self._run("tree", "--app", "notepad", "--output", "json"),
                         ["tree", "--app", "notepad", "--output", "json"])

    def test_run_script_positional_is_converted(self):
        script = self.cwd / "my_automation.py"
        script.write_text("# noop\n", encoding="utf-8")
        out = self._run("run", "my_automation.py")
        self.assertEqual(out[0], "run")
        self.assertTrue(out[1].startswith("W:"), out)
        self.assertTrue(out[1].endswith(r"\my_automation.py"), out)

    def test_selectors_and_text_are_never_converted(self):
        """セレクタや入力テキストは、同名ファイルが cwd にあっても変換しない。"""
        (self.cwd / "README.md").write_text("x", encoding="utf-8")
        out = self._run("type", "control:=Edit", "README.md", "--app", "notepad")
        self.assertEqual(out, ["type", "control:=Edit", "README.md",
                               "--app", "notepad"])

    def test_absolute_posix_paths_are_converted(self):
        out = self._run("run", "/tmp/a.py", "/tmp/data.json")
        self.assertEqual(out, ["run", r"W:\tmp\a.py", r"W:\tmp\data.json"])

    def test_nonexistent_relative_arg_is_left_alone(self):
        self.assertEqual(self._run("tree", "--app", "notepad"),
                         ["tree", "--app", "notepad"])

    def test_global_flags_before_subcommand_survive(self):
        out = self._run("--no-lock", "screenshot", "--output", "/tmp/sc.png")
        self.assertEqual(out, ["--no-lock", "screenshot", "--output",
                               r"W:\tmp\sc.png"])

    def test_path_conversion_can_be_disabled(self):
        out = self._run("screenshot", "--output", "/tmp/sc.png",
                        env_extra={"WINAUTO_NO_PATH_CONV": "1"})
        self.assertEqual(out, ["screenshot", "--output", "/tmp/sc.png"])

    def test_args_with_spaces_survive_the_wrapper(self):
        out = self._run("type", "name:=Search box", "hello world")
        self.assertEqual(out, ["type", "name:=Search box", "hello world"])

    def test_falls_back_to_cmd_exe_without_direct_python(self):
        """python.exe を直接 exec できない環境では cmd.exe 経路に倒す。"""
        wrapper = installer.render_wsl_wrapper(
            install_dir=r"C:\x", win_python=r"C:\Python312\python.exe",
            win_python_unix="", win_script=r"C:\x\winauto.py")
        self.assertIn('exec cmd.exe /c "$WIN_PYTHON"', wrapper)
        self.assertIn("WIN_PYTHON_UNIX=''", wrapper)


if __name__ == "__main__":
    unittest.main()
