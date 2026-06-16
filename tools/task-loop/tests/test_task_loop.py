"""task-loop の単体テスト（標準ライブラリ unittest）。

外側ループの状態機械・停止条件・verify ゲートを、kiro-flow を呼ばずに
検証する（act を注入 or --dry-run 相当）。1 件だけ実際に kiro-flow stub を
叩く統合テストも持つ（kiro-flow が無ければ skip）。

    python -m unittest discover -s tools/task-loop/tests
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

# task-loop.py をモジュールとして読み込む（ハイフン名のため importlib 経由）
_MOD = Path(__file__).resolve().parent.parent / "task-loop.py"
_spec = importlib.util.spec_from_file_location("task_loop", _MOD)
tl = importlib.util.module_from_spec(_spec)
sys.modules["task_loop"] = tl  # dataclass の前方参照解決に必要
_spec.loader.exec_module(tl)


def write_queue(d: Path, body: str) -> Path:
    q = d / "queue.md"
    q.write_text(body, encoding="utf-8")
    return q


class TestParse(unittest.TestCase):
    def test_roundtrip_and_fields(self):
        text = (
            "# Task Queue\n\n"
            "## T1: 見出しを追加\n"
            "- status: todo\n"
            "- verify: `grep -q hi out.txt`\n"
            "- retries: 0\n"
            "- note: メモ\n\n"
            "## T2: 何か\n"
            "- status: done\n"
            "- verify: `true`\n"
            "- retries: 1\n"
        )
        preamble, tasks = tl.parse_queue(text)
        self.assertIn("# Task Queue", preamble)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].id, "T1")
        self.assertEqual(tasks[0].title, "見出しを追加")
        self.assertEqual(tasks[0].verify, "grep -q hi out.txt")  # バッククォート除去
        self.assertEqual(tasks[0].extra, [("note", "メモ")])
        self.assertEqual(tasks[1].status, "done")
        self.assertEqual(tasks[1].retries, 1)
        # 書き戻しても再パースで等価
        s = tl.serialize_queue(preamble, tasks)
        _, tasks2 = tl.parse_queue(s)
        self.assertEqual([t.id for t in tasks2], ["T1", "T2"])
        self.assertEqual(tasks2[0].verify, "grep -q hi out.txt")
        self.assertEqual(tasks2[0].extra, [("note", "メモ")])


class TestVerifyGate(unittest.TestCase):
    def test_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as d:
            ok, _ = tl.run_verify("true", Path(d), 10)
            self.assertTrue(ok)
            ng, _ = tl.run_verify("false", Path(d), 10)
            self.assertFalse(ng)

    def test_empty_verify_is_fail(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = tl.run_verify("", Path(d), 10)
            self.assertFalse(ok)
            self.assertIn("verify", msg)


def make_cfg(d: Path, queue: Path, **kw) -> "tl.Config":
    base = dict(
        queue=queue, journal=d / "journal.md", workdir=d, bus=d / "bus",
        executor="stub", planner="stub", dry_run=True,
    )
    base.update(kw)
    return tl.Config(**base)


class TestLoop(unittest.TestCase):
    def test_drains_when_all_verify_pass(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            q = write_queue(d,
                "# Q\n\n"
                "## T1: a\n- status: todo\n- verify: `true`\n- retries: 0\n\n"
                "## T2: b\n- status: todo\n- verify: `true`\n- retries: 0\n")
            res = tl.run_loop(make_cfg(d, q))
            self.assertEqual(res["reason"], tl.REASON_DRAINED)
            self.assertEqual(res["counts"]["done"], 2)
            self.assertEqual(tl.exit_code_for(res), 0)

    def test_failing_task_becomes_blocked_after_retries(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            q = write_queue(d,
                "# Q\n\n## T1: a\n- status: todo\n- verify: `false`\n- retries: 0\n")
            res = tl.run_loop(make_cfg(d, q, max_retries=2))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertEqual(tl.exit_code_for(res), 1)
            # retries が max を超えて blocked に落ちている
            self.assertGreater(res["tasks"][0].retries, 2)

    def test_no_verify_blocks_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            q = write_queue(d,
                "# Q\n\n## T1: a\n- status: todo\n- verify: \n- retries: 0\n")
            res = tl.run_loop(make_cfg(d, q))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertEqual(res["tasks"][0].retries, 1)  # 1 回で即 blocked

    def test_max_cycles_guard(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # 常に false → 再試行で回り続けるが max-cycles で必ず止まる
            q = write_queue(d,
                "# Q\n\n## T1: a\n- status: todo\n- verify: `false`\n- retries: 0\n")
            res = tl.run_loop(make_cfg(d, q, max_retries=999, max_cycles=4,
                                       no_progress=999, blocked_ratio=1.1))
            self.assertEqual(res["reason"], tl.REASON_MAX_CYCLES)
            self.assertEqual(res["cycles"], 4)

    def test_no_progress_guard(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            q = write_queue(d,
                "# Q\n\n## T1: a\n- status: todo\n- verify: `false`\n- retries: 0\n")
            res = tl.run_loop(make_cfg(d, q, max_retries=999, max_cycles=999,
                                       no_progress=3, blocked_ratio=1.1))
            self.assertEqual(res["reason"], tl.REASON_NO_PROGRESS)

    def test_blocked_ratio_guard(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            q = write_queue(d,
                "# Q\n\n"
                "## T1: a\n- status: todo\n- verify: `false`\n- retries: 0\n\n"
                "## T2: b\n- status: todo\n- verify: `true`\n- retries: 0\n")
            res = tl.run_loop(make_cfg(d, q, max_retries=0, blocked_ratio=0.5,
                                       max_cycles=999, no_progress=999))
            # T1 が即 blocked（retries=0）→ 1/2=0.5 で停止
            self.assertEqual(res["reason"], tl.REASON_BLOCKED_RATIO)

    def test_act_injection_runs(self):
        """act を注入して、verify と独立に呼ばれることを確認。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            marker = d / "acted"
            q = write_queue(d,
                f"# Q\n\n## T1: a\n- status: todo\n- verify: `test -f {marker}`\n- retries: 0\n")
            calls = []

            def fake_act(task, cfg):
                calls.append(task.id)
                marker.write_text("x")  # act が成果物を作る
                return True, "ok"

            cfg = make_cfg(d, q, dry_run=False)
            res = tl.run_loop(cfg, act=fake_act)
            self.assertEqual(calls, ["T1"])
            self.assertEqual(res["counts"]["done"], 1)


class TestKiroFlowIntegration(unittest.TestCase):
    """実際に kiro-flow stub を 1 回叩く統合テスト（無ければ skip）。"""

    def test_stub_end_to_end(self):
        kf = Path(__file__).resolve().parents[2] / "kiro-flow" / "kiro-flow.py"
        if not kf.exists():
            self.skipTest("kiro-flow.py が見つからない")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            out = d / "out.txt"
            # verify は act 後にローカルで判定。act 成否に依らず成果物を用意して
            # 「verify ゲートが done を確定する」ことを確認する。
            out.write_text("done")
            q = write_queue(d,
                f"# Q\n\n## T1: 何か\n- status: todo\n- verify: `test -f {out}`\n- retries: 0\n")
            env = dict(os.environ, KIRO_FLOW_STUB_SLEEP_MAX="0")
            os.environ.update(env)
            cfg = tl.Config(
                queue=q, journal=d / "journal.md", workdir=d, bus=d / "bus",
                executor="stub", planner="stub", dry_run=False,
                act_timeout=120, max_cycles=3,
            )
            res = tl.run_loop(cfg)
            self.assertEqual(res["counts"]["done"], 1)
            self.assertEqual(res["reason"], tl.REASON_DRAINED)


if __name__ == "__main__":
    unittest.main()
