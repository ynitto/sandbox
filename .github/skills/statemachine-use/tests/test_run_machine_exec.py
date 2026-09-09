"""run_machine.py の exec バックエンドと RESULT 行の契約テスト。

agent-loop / agent-herd の無い環境で、呼び出し側（agent-app など）が agents/*.json から
組んだ argv を工程ごとに 1 回起こして回す口。見るのは 4 点——依頼文の渡し方（stdin /
argv / `{output_file}`）、共通指示がアクションにだけ前置されること、最後の行が機械可読な
RESULT であること、そして不正な argv を LLM を呼ぶ前に落とすこと。

pytest でも `python -m unittest` でも走る（fixture を使わない）。
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_machine.py"

WORKFLOW = textwrap.dedent("""
    name: exec-test
    initial_state: work
    states:
      work:
        description: 作業
        action: "入力 {{input}} を処理せよ"
        output_validator: "startswith:OK,FAILED"
      done:
        description: 完了
        terminal: true
      failed:
        description: 失敗
        terminal: true
    transitions:
      - from: work
        to: done
        condition_rule: "startswith:last_output:OK"
      - from: work
        to: failed
        condition_rule: "startswith:last_output:FAILED"
""")

# 依頼文を受け取り、どこから受け取ったかを含めて `OK …` を返す偽のエージェント。
FAKE_AGENT = textwrap.dedent("""
    import sys, os
    if len(sys.argv) > 1 and sys.argv[1] != "--out":
        prompt, via = sys.argv[-1], "argv"
    else:
        prompt, via = sys.stdin.read(), "stdin"
    answer = "OK via=%s prompt=%s" % (via, prompt.replace("\\n", "|"))
    if len(sys.argv) > 2 and sys.argv[1] == "--out":
        open(sys.argv[2], "w", encoding="utf-8").write(answer)
    else:
        sys.stdout.write(answer)
""")


def run(tmp: Path, *extra: str) -> subprocess.CompletedProcess:
    workflow = tmp / "workflow.yaml"
    workflow.write_text(WORKFLOW, encoding="utf-8")
    agent = tmp / "fake_agent.py"
    agent.write_text(FAKE_AGENT, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(workflow), "--result-line", *extra],
        capture_output=True, text=True, cwd=str(tmp),
        env={**os.environ, "FAKE_AGENT": str(agent)},
    )


def last_result(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, stdout
    return json.loads(lines[-1][len("RESULT "):])


class ExecBackendTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def argv(self, *tail: str) -> str:
        return json.dumps([sys.executable, str(self.tmp / "fake_agent.py"), *tail])

    def test_stdin_prompt_and_result_line(self):
        proc = run(self.tmp, "--agent", "exec", "--agent-command", self.argv(),
                   "--prompt-via", "stdin", "--input", "TASK-1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = last_result(proc.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finalState"], "done")
        self.assertIn("via=stdin", result["stdout"])
        self.assertIn("入力 TASK-1 を処理せよ", result["stdout"])
        self.assertEqual(proc.stdout.rstrip().splitlines()[-1][:7], "RESULT ", "RESULT は最後の行")

    def test_argv_prompt(self):
        proc = run(self.tmp, "--agent", "exec", "--agent-command", self.argv("--message"),
                   "--prompt-via", "argv", "--input", "X")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("via=argv", last_result(proc.stdout)["stdout"])

    def test_output_file_placeholder(self):
        proc = run(self.tmp, "--agent", "exec", "--agent-command", self.argv("--out", "{output_file}"),
                   "--prompt-via", "stdin", "--input", "X")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("via=stdin", last_result(proc.stdout)["stdout"])

    def test_instruction_precedes_action_only(self):
        proc = run(self.tmp, "--agent", "exec", "--agent-command", self.argv(),
                   "--instruction", "共通指示です", "--input", "X")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = last_result(proc.stdout)["stdout"]
        self.assertTrue(out.startswith("OK via=stdin prompt=共通指示です||## 今回の工程|入力 X"), out)

    def test_failed_label_reaches_result(self):
        agent = self.tmp / "fail_agent.py"
        agent.write_text("import sys; sys.stdout.write('FAILED reason')", encoding="utf-8")
        proc = run(self.tmp, "--agent", "exec", "--agent-command",
                   json.dumps([sys.executable, str(agent)]))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = last_result(proc.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["finalState"], "failed")

    def test_agent_error_is_reported_in_result(self):
        agent = self.tmp / "broken_agent.py"
        agent.write_text("import sys; sys.stderr.write('boom'); sys.exit(7)", encoding="utf-8")
        proc = run(self.tmp, "--agent", "exec", "--agent-command",
                   json.dumps([sys.executable, str(agent)]))
        self.assertNotEqual(proc.returncode, 0)
        result = last_result(proc.stdout)
        self.assertFalse(result["ok"])
        self.assertIn("code=7", result["error"])

    def test_invalid_agent_command_fails_before_running(self):
        proc = run(self.tmp, "--agent", "exec", "--agent-command", "not json")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--agent-command", proc.stdout)
        proc = run(self.tmp, "--agent", "exec", "--agent-command", "[]")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
