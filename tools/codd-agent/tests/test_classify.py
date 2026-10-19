"""止まった理由の種類を「利用者に訊く側」と「訊かずにやり直す側」のどちらに分けるかを固定する。"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

MACHINE = Path(__file__).resolve().parents[1] / "machine"


def load_codd():
    spec = importlib.util.spec_from_file_location("codd_machine", MACHINE / "codd.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclass が自分のモジュールを引けるように
    spec.loader.exec_module(mod)
    return mod


codd = load_codd()

# 訊かずにやり直してよい種類（エージェントが自分で直せる）と、利用者に訊く種類。
RETRY = {"check", "undone", "unfixed", "extra", "names", "paths", "rules", "shape"}
ASK = {
    "form",                                        # どの種類も付いていない指摘。付け忘れが訊かない側に落ちないように
    "protected", "protected-hit",                  # 人の承認が要るファイル（protect）
    "test-edit", "unrun", "preexisting",           # テストが通ったフリ（§4.2.1〜§4.2.3）
    "outside", "output", "ref-coverage", "stale-ref", "unconfirmed", "stale", "config",
}
SOURCE = (MACHINE / "codd.py").read_text(encoding="utf-8")


def asks(phase: str, kind: str) -> bool:
    return kind not in codd.AUTO_KINDS[phase]


class KindTest(unittest.TestCase):
    """止めた理由の種類は、指摘を出すところで付ける（Problem / tag）。文言から当て直さない。"""

    def test_every_kind_given_at_the_source_has_advice(self) -> None:
        kinds = set(re.findall(r'(?:Problem|tag)\("([a-z-]+)"', SOURCE))
        self.assertTrue(kinds)
        known = set(codd.ADVICE["plan"]) | set(codd.ADVICE["apply"])
        self.assertEqual(kinds - known, set())

    def test_fixable_kinds_are_retried_without_asking(self) -> None:
        for kind in RETRY:
            with self.subTest(kind=kind):
                self.assertFalse(asks("apply", kind))

    def test_plan_problems_are_asked_after_the_state_retries(self) -> None:
        # 計画の検査は同じステートのやり直し（check_retries）で直させる。stuck からも訊かずに戻すと二重に数える
        for kind in RETRY | ASK:
            with self.subTest(kind=kind):
                self.assertTrue(asks("plan", kind))

    def test_decisions_are_asked(self) -> None:
        for kind in ASK:
            with self.subTest(kind=kind):
                self.assertTrue(asks("apply", kind))

    def test_an_untagged_problem_is_recorded_as_form(self) -> None:
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            codd.record_problems(data, "apply", [codd.Problem("check", "落ちた"), "まだ種類の無い新しい指摘"])
            items = json.loads((data / "problems.json").read_text(encoding="utf-8"))["problems"]
        self.assertEqual([i["kind"] for i in items], ["check", "form"])

    def test_tag_keeps_a_kind_given_closer_to_the_source(self) -> None:
        out = codd.tag("shape", [codd.Problem("protected", "a"), "b"])
        self.assertEqual([p.kind for p in out], ["protected", "shape"])
        self.assertEqual(out, ["a", "b"])


class ReplayFindingsTest(unittest.TestCase):
    def test_the_same_problems_again_are_asked_even_with_retries_left(self) -> None:
        # 訊かずにやり直しても同じ指摘が残るなら、エージェントには直せない。回数が残っていても訊く
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            same = ["実装のテストの検査が失敗しました（1）: python3 -m unittest"]
            self.assertEqual(codd.auto_step(data, "apply", {"check"}, same), "APPLY")
            self.assertIsNone(codd.auto_step(data, "apply", {"check"}, same))
            other = ["自分の変更案のファイルをまだ変えていません: src/a.py"]
            self.assertEqual(codd.auto_step(data, "apply", {"undone"}, other), "APPLY")

    def test_advice_follows_a_protected_file_before_a_failing_test(self) -> None:
        # テストの失敗が先に出ても、原因が承認の要るファイルなら、勧めは計画を直す（PLAN）
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / ".codd"
            data.mkdir()
            problems = [{"kind": k, "text": t} for k, t in (
                ("check", "実装のテストの検査が失敗しました（1）: python3 -m unittest"),
                ("protected-hit", codd.PROTECTED_HIT + "（…）: legacy/old.py"))]
            (data / "problems.json").write_text(json.dumps({"phase": "apply", "problems": problems}), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                codd.cmd_advise(Path(tmp))
        self.assertIn("1. 変えた分は残して、計画を直す（勧め） → `PLAN`", out.getvalue())

    def test_compacted_plan_keeps_the_blank_line_before_a_heading(self) -> None:
        text = "## テストの変更案\n\n- a.py — 直す\n- b.py — 変更不要: 触れない\n\n## 今回やらないこと\n\nなし\n"
        self.assertIn("- a.py — 直す\n\n## 今回やらないこと", codd.compact_plan(text))

    def test_a_failed_check_shows_the_failure_not_only_the_summary(self) -> None:
        script = "print('not ok 1 - ' + 'broken'); [print(f'ok {i}') for i in range(40)]; raise SystemExit(1)"
        text = codd.run_check(Path.cwd(), [sys.executable, "-c", script], "x")[0]
        self.assertIn("not ok 1 - broken", text.split("\n", 1)[1])   # 1 行目はコマンド


class SecondReplayTest(unittest.TestCase):
    """2 回目の再生（tests/scenarios/）で見つけたもの。"""

    def test_a_no_change_reason_may_quote_names(self) -> None:
        j = codd.judgment("`Math.floor` — 変更不要: 標準の関数で、`roundMoney` のテストで確かめる")
        self.assertTrue(j.waived)
        self.assertEqual(j.reason, "標準の関数で、`roundMoney` のテストで確かめる")
        self.assertTrue(codd.judgment("tests/a.test.js — 変更不要: `node --test` で動く").waived)

    def test_a_nested_function_is_not_a_new_public_name(self) -> None:
        grab = lambda ln: [m.group(1) for p in codd._NEW_CODE_NAMES for m in [p.match(ln)] if m]
        self.assertEqual(grab("+export function welcome() {"), ["welcome"])
        self.assertEqual(grab("+  function handleSelect() {"), [])


if __name__ == "__main__":
    unittest.main()
