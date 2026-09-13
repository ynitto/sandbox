"""`command:` の許す終了コード（`allow_status`）と未導入の飛ばし（`skip_if_missing`）。

純バッチのフックが持っていた 2 つの判断——「この段は 1 で正常」と「スクリプトが無ければ
何もしない」——を宣言へ出すための項目で、これが無いと使用量較正・記憶メンテナンス・
Moltbook 巡回は `command:` へ移せない。どちらも宣言の読み取り（`command_spec`）と
実行（`run_command`）の両側に効くので、両側を通して見る。

仕様: docs/specs/agent-loop-spec.md §2.3.2。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402


def _exit_script(root: Path, name: str, status: int) -> str:
    """終了コードだけを返すスクリプト（シェル記号を argv に書けないので用意する）。"""
    path = root / name
    path.write_text(f"import sys\nsys.exit({status})\n", encoding="utf-8")
    return str(path)


class AllowStatusTest(unittest.TestCase):
    """`allow_status` — 成功として扱う終了コードを宣言する。"""

    def test_a_listed_status_is_a_success(self):
        with tempfile.TemporaryDirectory() as td:
            script = _exit_script(Path(td), "one.py", 1)
            spec = al._loopentry.command_spec({"command": {
                "argv": [sys.executable, script], "allow_status": [0, 1], "timeout_sec": 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], 1)
            self.assertEqual(result["stopReason"], "command_exit")
            self.assertEqual(result["error"], "")

    def test_without_a_declaration_only_zero_is_a_success(self):
        with tempfile.TemporaryDirectory() as td:
            script = _exit_script(Path(td), "one.py", 1)
            spec = al._loopentry.command_spec({"command": {
                "argv": [sys.executable, script], "timeout_sec": 30}})
            self.assertEqual(spec["allow_status"], [0])
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], 1)

    def test_a_list_without_zero_is_accepted(self):
        # 「見つからなければ 1」のように、0 以外を正常とするコマンドがある。
        spec = al._loopentry.command_spec({"command": {"argv": ["a"], "allow_status": [1]}})
        self.assertEqual(spec["allow_status"], [1])

    def test_a_non_integer_is_refused_at_load_time(self):
        for bad in (["1"], [1.5], [True], [-1], [], "0"):
            with self.subTest(bad=bad):
                with self.assertRaises(al._loopentry.LoopEntryError):
                    al._loopentry.command_spec({"command": {"argv": ["a"], "allow_status": bad}})

    def test_every_step_of_a_sequence_inherits_the_allowance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = _exit_script(root, "first.py", 1)
            second = _exit_script(root, "second.py", 1)
            spec = al._loopentry.command_spec({"command": {
                "commands": [f'"{sys.executable}" {first}', f'"{sys.executable}" {second}'],
                "allow_status": [0, 1], "timeout_sec": 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["completedCommands"], 2)

    def test_a_shell_script_is_judged_by_the_status_of_the_whole_script(self):
        with tempfile.TemporaryDirectory() as td:
            spec = al._loopentry.command_spec({"command": {
                "shell": "echo one\nexit 1", "allow_status": [0, 1], "timeout_sec": 30}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], 1)

    def test_a_timeout_stays_a_failure_whatever_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "sleep.py"
            script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            spec = al._loopentry.command_spec({"command": {
                "argv": [sys.executable, str(script)], "allow_status": [0, 1], "timeout_sec": 1}})
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertFalse(result["ok"])
            self.assertEqual(result["stopReason"], "command_timeout")


class SkipIfMissingTest(unittest.TestCase):
    """`skip_if_missing` — 前提が無いノードでは起こさずに終える。"""

    def _marker_spec(self, td: str, missing: str):
        """走れば `ran` を作るコマンド（＝走っていないことを確かめられる）。"""
        script = Path(td) / "mark.py"
        script.write_text("from pathlib import Path\nPath('ran').touch()\n", encoding="utf-8")
        return al._loopentry.command_spec({"command": {
            "argv": [sys.executable, str(script)], "skip_if_missing": missing,
            "timeout_sec": 30}})

    def test_a_missing_path_skips_the_command_without_running_it(self):
        with tempfile.TemporaryDirectory() as td:
            spec = self._marker_spec(td, os.path.join(td, "absent", "batch.py"))
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertIsNone(result["status"])
            self.assertEqual(result["stopReason"], "command_skipped")
            self.assertEqual(result["durationSec"], 0.0)
            self.assertEqual((result["stdout"], result["stderr"], result["error"]), ("", "", ""))
            self.assertFalse((Path(td) / "ran").exists())

    def test_an_existing_path_runs_the_command_as_usual(self):
        with tempfile.TemporaryDirectory() as td:
            present = Path(td) / "batch.py"
            present.write_text("", encoding="utf-8")
            spec = self._marker_spec(td, str(present))
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], 0)
            self.assertEqual(result["stopReason"], "command_exit")
            self.assertTrue((Path(td) / "ran").exists())

    def test_one_missing_path_out_of_several_is_enough_to_skip(self):
        with tempfile.TemporaryDirectory() as td:
            present = Path(td) / "batch.py"
            present.write_text("", encoding="utf-8")
            spec = self._marker_spec(td, [str(present), os.path.join(td, "absent.py")])
            result = al._commandrun.run_command(spec, cwd=td)
            self.assertEqual(result["stopReason"], "command_skipped")
            self.assertFalse((Path(td) / "ran").exists())

    def test_the_skip_is_written_to_the_run_log(self):
        # 「回っているつもりで何もしていない」を後から見つけるための 1 行。
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "run.jsonl")
            spec = self._marker_spec(td, os.path.join(td, "absent.py"))
            al._commandrun.run_command(spec, cwd=td, log_file=log)
            lines = Path(log).read_text(encoding="utf-8").splitlines()
            events = [json.loads(line)["event"] for line in lines]
            self.assertEqual(events, ["command_skipped"])

    def test_a_relative_path_is_read_from_the_working_directory(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "batch.py").write_text("", encoding="utf-8")
            self.assertEqual(
                al._commandrun.run_command(self._marker_spec(td, "batch.py"), cwd=td)["status"], 0)
            self.assertEqual(
                al._commandrun.run_command(self._marker_spec(td, "nope.py"), cwd=td)["stopReason"],
                "command_skipped")

    def test_a_placeholder_is_filled_in_like_an_argument(self):
        spec = al._loopentry.command_spec({"command": {
            "argv": ["python3", "{home}/batch.py"], "skip_if_missing": "{home}/batch.py"}})
        self.assertEqual(spec["skip_if_missing"], ["{home}/batch.py"])
        rendered = al._loopentry.render_command(spec, {"home": "/opt/skills"})
        self.assertEqual(rendered["skip_if_missing"], ["/opt/skills/batch.py"])

    def test_a_non_string_is_refused_at_load_time(self):
        for bad in ([1], [""], {"path": "x"}, 3):
            with self.subTest(bad=bad):
                with self.assertRaises(al._loopentry.LoopEntryError):
                    al._loopentry.command_spec({"command": {"argv": ["a"], "skip_if_missing": bad}})


if __name__ == "__main__":
    unittest.main()
