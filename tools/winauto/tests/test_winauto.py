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
  * record … 出力の形（JSONL）・打鍵の畳み・重複除去・上限。UIA も pywinauto も
    要らない層（RecordSink）だけを見る。読み手（agent-dashboard の recording.js）
    との契約はイベント種別のゴールデンで固定する。

    python -m unittest discover -s tools/winauto/tests
"""
import importlib.util
import io
import json
import re
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

    def test_record_output_path_is_converted(self):
        out = self._run("record", "--app", "kintai", "--output", "/tmp/events.jsonl")
        self.assertEqual(out, ["record", "--app", "kintai", "--output",
                               r"W:\tmp\events.jsonl"])

    def test_falls_back_to_cmd_exe_without_direct_python(self):
        """python.exe を直接 exec できない環境では cmd.exe 経路に倒す。"""
        wrapper = installer.render_wsl_wrapper(
            install_dir=r"C:\x", win_python=r"C:\Python312\python.exe",
            win_python_unix="", win_script=r"C:\x\winauto.py")
        self.assertIn('exec cmd.exe /c "$WIN_PYTHON"', wrapper)
        self.assertIn("WIN_PYTHON_UNIX=''", wrapper)


# ---------------------------------------------------------------------------
# record — 出力の形と畳み
# ---------------------------------------------------------------------------

_RECORDING_JS = (_ROOT.parent / "agent-dashboard" / "src" / "features" / "cowork"
                 / "main" / "recording.js")


class RecordSinkTest(unittest.TestCase):
    def sink(self, **kwargs):
        self.buf = io.StringIO()
        return winauto.RecordSink(self.buf, "勤怠管理", **kwargs)

    def lines(self):
        return [json.loads(line) for line in self.buf.getvalue().splitlines()]

    def test_launch_and_window_shape(self):
        sink = self.sink()
        sink.emit("launch", path=r"C:\Apps\kintai.exe")
        sink.emit("window", window="月次集計")
        sink.close()
        self.assertEqual(self.lines(), [
            {"event": "launch", "app": "勤怠管理", "path": r"C:\Apps\kintai.exe"},
            {"event": "window", "app": "勤怠管理", "window": "月次集計"},
        ])

    def test_element_fields_are_only_written_when_present(self):
        """空の項目は書かない。読み手は auto_id が無いときだけ name へ落ちるので、
        空文字の auto_id を書くと `auto_id:=` の空セレクタになってしまう。"""
        sink = self.sink()
        sink.emit("invoke", name="OK", control_type="Button", window="完了")
        sink.close()
        self.assertEqual(self.lines()[0], {
            "event": "invoke", "app": "勤怠管理", "window": "完了",
            "control_type": "Button", "name": "OK"})

    def test_typing_is_folded_to_the_last_value(self):
        sink = self.sink()
        for v in ("2", "20", "202", "2026-09"):
            sink.emit("value", name="対象月", auto_id="txtMonth",
                      control_type="Edit", window="w", value=v)
        sink.close()
        self.assertEqual([r["value"] for r in self.lines()], ["2026-09"])
        self.assertEqual(sink.count, 1)

    def test_folding_keeps_the_order_of_what_the_person_did(self):
        """溜めている value は、別の要素の value や value 以外が来たときに先に出す。"""
        sink = self.sink()
        sink.emit("value", name="A", control_type="Edit", value="a1")
        sink.emit("value", name="A", control_type="Edit", value="a2")
        sink.emit("value", name="B", control_type="Edit", value="b1")
        sink.emit("invoke", name="送信", control_type="Button")
        sink.emit("value", name="A", control_type="Edit", value="a3")
        sink.close()
        self.assertEqual([(r["event"], r.get("name"), r.get("value")) for r in self.lines()],
                         [("value", "A", "a2"), ("value", "B", "b1"),
                          ("invoke", "送信", None), ("value", "A", "a3")])

    def test_repeated_state_notifications_are_dropped_but_clicks_are_not(self):
        """ComboBox の選択は SelectionItem と Value の両方から二重に来る。押下は落とさない
        ——同じボタンを 2 回押すのは人の意図でありうる。"""
        sink = self.sink()
        sink.emit("select", name="種別", control_type="ComboBox", value="緊急")
        sink.emit("select", name="種別", control_type="ComboBox", value="緊急")
        sink.emit("toggle", name="同意", control_type="CheckBox", value="on")
        sink.emit("toggle", name="同意", control_type="CheckBox", value="on")
        sink.emit("invoke", name="次へ", control_type="Button")
        sink.emit("invoke", name="次へ", control_type="Button")
        sink.close()
        self.assertEqual([r["event"] for r in self.lines()],
                         ["select", "toggle", "invoke", "invoke"])

    def test_same_value_again_after_something_else_is_kept(self):
        """A→B→A は人が戻った記録。直前と同じときだけ落とす。"""
        sink = self.sink()
        sink.emit("toggle", name="同意", control_type="CheckBox", value="on")
        sink.emit("toggle", name="同意", control_type="CheckBox", value="off")
        sink.emit("toggle", name="同意", control_type="CheckBox", value="on")
        sink.close()
        self.assertEqual([r["value"] for r in self.lines()], ["on", "off", "on"])

    def test_max_events_stops_accepting(self):
        sink = self.sink(max_events=2)
        self.assertTrue(sink.emit("invoke", name="1", control_type="Button"))
        self.assertFalse(sink.emit("invoke", name="2", control_type="Button"))
        self.assertFalse(sink.emit("invoke", name="3", control_type="Button"))
        sink.close()
        self.assertEqual([r["name"] for r in self.lines()], ["1", "2"])

    def test_unknown_kind_is_refused(self):
        sink = self.sink()
        with self.assertRaises(ValueError):
            sink.emit("keylog", name="x")

    def test_each_line_is_flushed_so_a_crash_keeps_what_was_recorded(self):
        sink = self.sink()
        sink.emit("invoke", name="OK", control_type="Button")
        self.assertEqual(len(self.buf.getvalue().splitlines()), 1)

    def test_japanese_stays_readable(self):
        """人が中身を読んで貼る形式なので、非 ASCII をエスケープへ逃がさない。"""
        sink = self.sink()
        sink.emit("invoke", name="出力", control_type="Button")
        sink.close()
        self.assertIn("出力", self.buf.getvalue())


class RecordContractTest(unittest.TestCase):
    """読み手（agent-dashboard の recording.js）との契約を両端で固定する。"""

    def _reader_kinds(self):
        src = _RECORDING_JS.read_text(encoding="utf-8")
        m = re.search(r"const WINAUTO_EVENT_KINDS = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m, "recording.js に WINAUTO_EVENT_KINDS が無い")
        return set(re.findall(r"'([a-z]+)'", m.group(1)))

    @unittest.skipUnless(_RECORDING_JS.exists(), "agent-dashboard が同じ木に無い")
    def test_every_kind_written_is_a_kind_the_reader_accepts(self):
        self.assertTrue(set(winauto.RECORD_EVENT_KINDS) <= self._reader_kinds())

    @unittest.skipUnless(_RECORDING_JS.exists(), "agent-dashboard が同じ木に無い")
    def test_keys_is_the_only_kind_the_recorder_does_not_write(self):
        """打鍵はフックを取らないと拾えず、あれはデスクトップ全体のキーロガーになる。
        読み手は `keys` を受けるが（人が手で足せる）、record は書かない。"""
        self.assertEqual(self._reader_kinds() - set(winauto.RECORD_EVENT_KINDS), {"keys"})

    def test_recorder_never_installs_a_keyboard_hook(self):
        """打鍵そのものを拾う口を呼んでいないこと。低レベルキーボードフックは
        デスクトップ全体のキーロガーで、対象アプリ以外へ打ったパスワードまで JSONL に
        落ちる——その JSONL は人がそのまま AI へ貼る。"""
        src = _WINAUTO_PY.read_text(encoding="utf-8")
        for banned in ("SetWindowsHookEx(", "SetWindowsHookExW(", "SetWindowsHookExA(",
                       "GetAsyncKeyState(", "GetKeyboardState(", "RegisterRawInputDevices("):
            self.assertNotIn(banned, src, f"打鍵のフックは取らない: {banned}")
        # 呼ばない理由がソースに残っていること（次に触る人が同じ判断をたどれるように）。
        self.assertIn("WH_KEYBOARD_LL", src)

    def test_value_types_split_input_from_choice(self):
        self.assertEqual(winauto.RECORD_VALUE_TYPES["Edit"], "value")
        self.assertEqual(winauto.RECORD_VALUE_TYPES["ComboBox"], "select")

    def test_toggle_states_are_the_words_the_reader_reads(self):
        self.assertEqual(winauto.RECORD_TOGGLE_STATES[1], "on")
        self.assertEqual(winauto.RECORD_TOGGLE_STATES[0], "off")

    def test_uia_ids_fall_back_to_the_published_constants(self):
        class _NoConstants:
            pass
        self.assertEqual(winauto._uia_id(_NoConstants(), "UIA_Invoke_InvokedEventId"), 20009)

        class _WithConstant:
            UIA_Invoke_InvokedEventId = 12345
        self.assertEqual(winauto._uia_id(_WithConstant(), "UIA_Invoke_InvokedEventId"), 12345)


class RecordCommandWiringTest(unittest.TestCase):
    def test_record_is_wired_into_the_cli(self):
        self.assertIn("record", winauto.COMMAND_MAP)
        args = winauto.build_parser().parse_args(
            ["record", "--app", "kintai", "--output", "/tmp/e.jsonl"])
        self.assertEqual(args.command, "record")
        self.assertEqual(args.app, "kintai")
        self.assertEqual(args.duration, 0.0)
        self.assertEqual(args.max_events, 0)

    def test_app_is_required(self):
        """対象を絞らない記録はデスクトップ全体の録画になる。"""
        with self.assertRaises(SystemExit):
            winauto.build_parser().parse_args(["record"])

    def test_record_does_not_take_the_desktop_lock(self):
        """読むだけで入力を奪わない。人が数分操作する間ロックを占有させない。"""
        self.assertNotIn("record", winauto.LOCKED_COMMANDS)

    def test_uia_events_check_warns_instead_of_failing_doctor(self):
        """イベントを購読できなくても click / type は動く。doctor を error にしない
        （error は終了コード 1 ＝ 橋が壊れている、の意味に取ってある）。"""
        check = winauto._doctor_uia_events_check()
        self.assertEqual(check["name"], "uia_events")
        self.assertIn(check["status"], ("ok", "warn"))
        if sys.platform != "win32":
            self.assertEqual(check["status"], "warn")
            self.assertIn("record", check["detail"])


if __name__ == "__main__":
    unittest.main()
