"""オフライン再生ハーネスの単体テスト。

推論サーバは呼ばない（`generate` を差し替える）。ここで守りたいのは 3 つ:

1. 記録済み JSONL から**入力が正しく取り出せる**こと（道具ありのログでも最初の 1 通だけ）
2. 再生が**道具を持たない**こと（記録されたコマンドを再実行しない）
3. 集計が空応答・失敗・一致率を素直に数えること
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentcore import ollama_adapter, ollama_replay


def _write_log(directory: Path, name: str, events: "list[dict]") -> Path:
    path = directory / name
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def _plain_log(prompt: str = "これを分類してください", *, ts: float = 100.0) -> "list[dict]":
    return [
        {"ts": ts, "kind": "run_start", "model": "qwen3", "mode": "plain",
         "think": False, "format": "json"},
        {"ts": ts + 1, "kind": "message", "role": "user", "content": prompt},
        {"ts": ts + 2, "kind": "message", "role": "assistant", "content": '{"label": "a"}'},
        {"ts": ts + 3, "kind": "run_end", "status": "done"},
    ]


def _tools_log(prompt: str = "リポジトリを調べて", *, ts: float = 200.0) -> "list[dict]":
    """道具ありの実行。user メッセージが 3 通あるが、入力は最初の 1 通だけ。"""
    return [
        {"ts": ts, "kind": "run_start", "model": "qwen3", "mode": "tools", "toolset": "bash"},
        {"ts": ts + 1, "kind": "message", "role": "user", "content": prompt},
        {"ts": ts + 2, "kind": "message", "role": "assistant", "content": "```bash\nls\n```"},
        {"ts": ts + 3, "kind": "tool_exec", "round": 1, "command": "rm -rf /tmp/x"},
        {"ts": ts + 4, "kind": "message", "role": "user", "content": "実行結果（終了コード 0）"},
        {"ts": ts + 5, "kind": "run_end", "status": "max_rounds"},
    ]


class TestCollect(unittest.TestCase):
    def test_case_of_takes_first_user_message_only(self):
        """道具ありのログでも、入力はツール結果の差し戻しでなく最初のプロンプト。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_log(Path(tmp), "a.jsonl", _tools_log())
            case = ollama_replay.case_of(path)
        self.assertIsNotNone(case)
        self.assertEqual(case["prompt"], "リポジトリを調べて")
        self.assertEqual(case["origin_mode"], "tools")
        self.assertEqual(case["origin_status"], "max_rounds")

    def test_case_of_skips_logs_without_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_log(Path(tmp), "a.jsonl", [
                {"ts": 1.0, "kind": "run_start", "model": "qwen3"},
                {"ts": 2.0, "kind": "run_end", "status": "failed"}])
            self.assertIsNone(ollama_replay.case_of(path))

    def test_collect_dedupes_and_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_log(directory, "a.jsonl", _plain_log("同じ質問", ts=100.0))
            _write_log(directory, "b.jsonl", _plain_log("同じ質問", ts=110.0))
            _write_log(directory, "c.jsonl", _plain_log("別の質問", ts=300.0))
            cases = ollama_replay.collect_cases(directory)
        self.assertEqual([c["prompt"] for c in cases], ["別の質問", "同じ質問"])
        same = [c for c in cases if c["prompt"] == "同じ質問"][0]
        self.assertEqual(same["occurrences"], 2, "同じプロンプトは 1 件へまとめて本数を持つ")

    def test_collect_limit_takes_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_log(directory, "a.jsonl", _plain_log("古い", ts=1.0))
            _write_log(directory, "b.jsonl", _plain_log("新しい", ts=900.0))
            cases = ollama_replay.collect_cases(directory, limit=1)
        self.assertEqual([c["prompt"] for c in cases], ["新しい"])

    def test_broken_lines_do_not_break_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = _write_log(directory, "a.jsonl", _plain_log("生きている"))
            with path.open("a", encoding="utf-8") as fh:
                fh.write("{壊れた行\n")
            cases = ollama_replay.collect_cases(directory)
        self.assertEqual([c["prompt"] for c in cases], ["生きている"])


class TestArms(unittest.TestCase):
    def test_parse_arm_reads_keys(self):
        arm = ollama_replay.parse_arm("model=qwen3,think=off,format=json,repeat=3")
        self.assertEqual(arm["model"], "qwen3")
        self.assertIs(arm["think"], False)
        self.assertEqual(arm["format"], "json")
        self.assertEqual(arm["repeat"], 3)

    def test_format_text_means_no_constraint(self):
        """text は「強制しない」。API へ渡さない形（None）で表す——アダプタ側と同じ意味。"""
        self.assertIsNone(ollama_replay.parse_arm("format=text")["format"])

    def test_unknown_key_and_bad_values_are_errors(self):
        for spec in ("tools=bash", "think=maybe", "format=yaml", "model", "repeat=x"):
            with self.subTest(spec=spec):
                with self.assertRaises(ollama_replay.ArmError):
                    ollama_replay.parse_arm(spec)

    def test_default_label_describes_the_arm(self):
        arm = ollama_replay.parse_arm("model=qwen3,think=on")
        self.assertEqual(arm["label"], "qwen3 think=on format=text")


class TestReplay(unittest.TestCase):
    def test_replay_never_uses_tools(self):
        """記録されたコマンドを再実行しない（再生は測定であって再現ではない）。

        既定の生成口が `run_plain` であることを、`run_loop` を落とし穴に変えて示す。
        """
        calls = []

        def fake_run_plain(model, prompt, *, think=None, fmt=None, **kwargs):
            calls.append((model, prompt, think, fmt))
            return {"text": "ok", "tokens_in": 3, "tokens_out": 4}

        def explode(*_args, **_kwargs):
            raise AssertionError("再生でツールループを回してはいけない")

        with tempfile.TemporaryDirectory() as tmp:
            _write_log(Path(tmp), "a.jsonl", _tools_log("道具ありの記録"))
            cases = ollama_replay.collect_cases(Path(tmp))
            with mock.patch.object(ollama_replay.ollama_loop, "run_plain", fake_run_plain), \
                    mock.patch.object(ollama_replay.ollama_loop, "run_loop", explode):
                records = ollama_replay.replay(cases, [ollama_replay.parse_arm("model=m")])
        self.assertEqual(len(records), 1)
        self.assertEqual(calls[0][0], "m")
        self.assertEqual(calls[0][1], "道具ありの記録")

    def test_arm_model_falls_back_to_the_recorded_model(self):
        case = {"log": "L", "prompt": "P", "origin_model": "記録側のモデル"}
        record = ollama_replay.replay_case(
            case, ollama_replay.parse_arm("think=off"),
            generate=lambda model, prompt, *, think, fmt: {"text": model})
        self.assertEqual(record["model"], "記録側のモデル")

    def test_repeat_runs_the_same_arm_多重に(self):
        case = {"log": "L", "prompt": "P", "origin_model": "m"}
        seen = []
        records = ollama_replay.replay(
            [case], [ollama_replay.parse_arm("model=m,repeat=3")],
            generate=lambda model, prompt, *, think, fmt: seen.append(1) or {"text": "x"})
        self.assertEqual(len(records), 3)
        self.assertEqual([r["attempt"] for r in records], [1, 2, 3])

    def test_empty_output_is_recorded_as_empty(self):
        """空応答は最初に潰した症状。設定を変えたとき最初に再発するので必ず数える。"""
        record = ollama_replay.replay_case(
            {"log": "L", "prompt": "P", "origin_model": "m"}, ollama_replay.parse_arm(""),
            generate=lambda model, prompt, *, think, fmt: {"text": "   "})
        self.assertTrue(record["empty"])
        self.assertTrue(record["ok"], "空応答は「失敗」ではなく「空」として数える")

    def test_failure_is_recorded_and_does_not_stop_the_run(self):
        cases = [{"log": "A", "prompt": "P", "origin_model": "m"},
                 {"log": "B", "prompt": "Q", "origin_model": "m"}]

        def flaky(model, prompt, *, think, fmt):
            if prompt == "P":
                raise RuntimeError("ollama に接続できません")
            return {"text": "ok"}

        records = ollama_replay.replay(cases, [ollama_replay.parse_arm("")], generate=flaky)
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0]["ok"])
        self.assertIn("接続できません", records[0]["error"])
        self.assertTrue(records[1]["ok"])


class TestSummarize(unittest.TestCase):
    def test_counts_per_arm(self):
        records = [
            {"log": "A", "arm": "x", "ok": True, "empty": False, "duration_sec": 1.0,
             "tokens_out": 10, "text": "a"},
            {"log": "B", "arm": "x", "ok": True, "empty": True, "duration_sec": 3.0,
             "tokens_out": 0, "text": ""},
            {"log": "C", "arm": "x", "ok": False, "empty": True, "duration_sec": 2.0,
             "tokens_out": 0, "text": ""},
        ]
        summary = ollama_replay.summarize(records)
        arm = summary["arms"][0]
        self.assertEqual(arm["n"], 3)
        self.assertEqual(arm["failed"], 1)
        self.assertEqual(arm["ok"], 2)
        # 失敗した呼び出しは空応答に数えない（サーバが落ちているだけの日に
        # 空応答率が跳ね、設定の良し悪しに見えてしまう）。母数も ok の数。
        self.assertEqual(arm["empty"], 1)
        self.assertEqual(arm["empty_pct"], 50.0)
        self.assertEqual(summary["failed"], 1)
        # ok の 2 件（1.0 / 3.0）から採る。失敗の 2.0 が混ざっていれば中央は 2.0 になる。
        self.assertEqual(arm["duration_median_sec"], 3.0, "失敗は所要時間にも混ぜない")

    def test_agreement_ignores_json_key_order(self):
        """JSON 契約ではキー順の差は同じ答え。素の文字列比較で不一致に数えない。"""
        records = [
            {"log": "A", "arm": "x", "ok": True, "empty": False, "text": '{"a":1,"b":2}'},
            {"log": "A", "arm": "y", "ok": True, "empty": False, "text": '{"b": 2, "a": 1}'},
        ]
        summary = ollama_replay.summarize(records)
        self.assertEqual(summary["agreement"]["agreement_pct"], 100.0)

    def test_failed_records_are_not_compared(self):
        """落ちた腕の分だけ「不一致」を積まない（比較できるのは返ってきた出力だけ）。"""
        records = [
            {"log": "A", "arm": "x", "ok": True, "empty": False, "text": "same"},
            {"log": "A", "arm": "y", "ok": True, "empty": False, "text": "same"},
            {"log": "A", "arm": "z", "ok": False, "empty": True, "text": ""},
        ]
        summary = ollama_replay.summarize(records)
        self.assertEqual(summary["agreement"]["agreement_pct"], 100.0)

    def test_agreement_counts_only_comparable_cases(self):
        records = [
            {"log": "A", "arm": "x", "ok": True, "empty": False, "text": "same"},
            {"log": "A", "arm": "y", "ok": True, "empty": False, "text": "other"},
            {"log": "B", "arm": "x", "ok": True, "empty": False, "text": "lonely"},
        ]
        summary = ollama_replay.summarize(records)
        self.assertEqual(summary["agreement"]["comparable_cases"], 1, "1 通りだけの件は母数外")
        self.assertEqual(summary["agreement"]["agreed_cases"], 0)


class TestCli(unittest.TestCase):
    def test_parse_args_reads_replay_options(self):
        opts = ollama_adapter.parse_args(
            ["--replay", "/logs", "--arm", "model=a", "--arm", "model=b",
             "--replay-limit", "5", "--replay-out", "/tmp/out.jsonl"])
        self.assertTrue(opts["replay"])
        self.assertEqual(opts["replay_target"], "/logs")
        self.assertEqual(opts["arms"], ["model=a", "model=b"])
        self.assertEqual(opts["replay_limit"], 5)
        self.assertEqual(opts["replay_out"], "/tmp/out.jsonl")

    def test_replay_target_does_not_collide_with_status_target(self):
        """`--status LOG` はログ 1 本、`--replay DIR` は置き場。鍵を分けてある。"""
        opts = ollama_adapter.parse_args(["--replay", "/logs"])
        self.assertIsNone(opts["log_target"])

    def test_run_replay_writes_records_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_log(directory, "a.jsonl", _plain_log("質問"))
            records_path = directory / "out.jsonl"
            out, err = io.StringIO(), io.StringIO()
            rc = ollama_adapter.run_replay(
                {"replay_target": str(directory), "arms": ["model=m1", "model=m2"],
                 "replay_limit": 0, "replay_out": str(records_path)},
                generate=lambda model, prompt, *, think, fmt: {"text": f"answer-{model}"},
                out=out, err=err)
            written = [json.loads(line) for line in
                       records_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rc, 0)
        self.assertEqual(len(written), 2, "1 件 × 腕 2 本")
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["records"], 2)
        self.assertEqual(summary["agreement"]["agreement_pct"], 0.0, "腕ごとに違う答え")
        self.assertIn("@agent-log", err.getvalue())

    def test_total_failure_is_reported_through_the_exit_code(self):
        """全滅は測定結果ではなく環境の問題。空の結果を測定として流通させない。"""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_log(directory, "a.jsonl", _plain_log("質問"))
            out, err = io.StringIO(), io.StringIO()

            def dead(model, prompt, *, think, fmt):
                raise RuntimeError("ollama に接続できません")

            rc = ollama_adapter.run_replay(
                {"replay_target": str(directory), "arms": ["model=m"],
                 "replay_out": str(directory / "out.jsonl")},
                generate=dead, out=out, err=err)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out.getvalue())["failed"], 1, "集計は出したうえで rc で伝える")

    def test_run_replay_reports_when_nothing_to_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            rc = ollama_adapter.run_replay(
                {"replay_target": tmp, "arms": [], "model": "m"}, out=out, err=err)
        self.assertEqual(rc, 1)
        self.assertIn("再生できる記録がありません", err.getvalue())

    def test_bad_arm_spec_is_an_argument_error(self):
        out, err = io.StringIO(), io.StringIO()
        rc = ollama_adapter.run_replay({"arms": ["tools=bash"]}, out=out, err=err)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
