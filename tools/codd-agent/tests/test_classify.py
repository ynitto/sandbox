"""止まった理由を「利用者に訊く側」と「訊かずにやり直す側」のどちらに分けるかを、代表的な文言で固定する。

種類の名前や件数は固定しない（種類を増やした日・文言を足した日に落ちないように）。
"""

from __future__ import annotations

import importlib.util
import sys
import io
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

NO_ASK = (
    "見出しがありません: ## やりたいこと",
    "参照先を探していません: docs（`python3 .statemachine/codd/codd.py explore --term 語` で、やりたいことに固有の語を探してください）",
    "## テストの変更案 が「なし」です。コード・仕様書と同じく、足す・直すテストを挙げてください（要らないなら `- 変更不要: 理由`）",
    "## テストの変更案 の項目に、テストのファイルのパスがありません: - ログを確かめる",
    "## テストの変更案 の変更不要の後に理由がありません: - tests/a.py — 変更不要",
    "新しく足す `log` を確かめるテストが ## テストの変更案 にありません。足すテストのパスと確かめることを書いてください",
    "変更が響くテストの結果を写している文書が、計画にありません（写し直すなら変えるファイルに）: docs/perf.md",
    "終わった回の計画の記録を書き換えています（判断の記録なので変えない）: .plans/2026-10-01-0000-x.md",
    "作る・変えるファイルに決められた手順を、「従う手順」に挙げていません（…）: `py-style`（src/app.py）",
    "ファイルに決められた手順の文書を読み込んでいません（…）: docs/py.md",
    "変えたファイルに決められた道具を使った記録がありません（…）: `sqlfmt`（src/a.sql）",
    "手順の確かめることに答えていません（…）: `py-style`: 戻り値を変えていない",
    "手順の確かめることの根拠に書いたパスが、どの側にもありません: `py-style`: 戻り値を変えていない — src/x.py",
)
ASK = (
    "ずれがあるのに、参照先の変更案が「なし」です（ずれを残すなら、ずれではなくその他に書く）",
    "テストで得たものが、文書の求めを満たしていません（実装を直すか、目安を変えるなら利用者に確かめて計画に挙げてから文書を直してください）",
    "文書の書式（見出しの並び）が今の書式から外れています: docs/api.md — 例（見本: docs/a.md。書式は決まりとして守る）",
)
# 人の承認が要るファイル（protect）に触れた指摘は、変えたあとの検査でしか出ず、いつも訊く。
ASK_APPLY = (
    codd.PROTECTED + "（戻してください。…）: docs/requirements.md",
    codd.PROTECTED_HIT + "（変えずに利用者に確かめます。…）: legacy/old.py",
)


def asks(phase: str, text: str) -> bool:
    return codd.classify(phase, text) not in codd.AUTO_KINDS[phase]


class ClassifyTest(unittest.TestCase):
    def test_fixable_wording_is_retried_without_asking(self) -> None:
        for phase in ("plan", "apply"):
            for text in NO_ASK:
                with self.subTest(phase=phase, text=text):
                    self.assertFalse(asks(phase, text))

    def test_design_decisions_are_asked(self) -> None:
        for phase in ("plan", "apply"):
            for text in ASK:
                with self.subTest(phase=phase, text=text):
                    self.assertTrue(asks(phase, text))

    def test_protected_files_are_asked(self) -> None:
        for text in ASK_APPLY:
            with self.subTest(text=text):
                self.assertTrue(asks("apply", text))
                self.assertIn(codd.classify("apply", text), ("protected", "protected-hit"))

    def test_same_spelling_names_are_retried_after_changing(self) -> None:
        # 変えたあとに、名前が参照先の別のファイルに同じ綴りで出てくるだけなら、申告で済むので訊かない。
        text = codd.NAMES_TOUCH_REFS + "（…）: docs/zoom.md"
        self.assertFalse(asks("apply", text))
        # 消した名前を参照先がまだ書いているのも、直すか申告すれば済む。
        self.assertFalse(asks("apply", codd.STALE_NAMES + "（…）: `calc_total` — docs/api/orders.md:9"))
        # パスでつながっているファイルの扱い漏れは、これまでどおり訊く。
        self.assertTrue(asks("apply", "自分の変えたファイルとパスでつながっている参照先のファイルを、計画で扱っていません"))

    def test_unknown_wording_is_asked(self) -> None:
        # 目印を足し忘れた指摘は、訊く側に落ちる。書き方の目印に似た言い回しを含んでいても同じ。
        for phase in ("plan", "apply"):
            for text in ("まだどの目印にも当たらない新しい指摘",
                         "目安が「なし」です（変えてよいか利用者に確かめてください）",
                         "## 今回やらないこと の項目に、残す理由のパスがありません"):
                with self.subTest(phase=phase, text=text):
                    self.assertTrue(asks(phase, text))


class ReplayFindingsTest(unittest.TestCase):
    def test_a_removed_name_in_an_unplanned_file_is_asked(self) -> None:
        self.assertTrue(asks("apply", codd.STALE_UNPLANNED + "（…）: `hello` — docs/api.md:3"))
        self.assertFalse(asks("apply", codd.STALE_NAMES + "（…）: `hello` — docs/api.md:3"))

    def test_advice_follows_a_protected_file_before_a_failing_test(self) -> None:
        # テストの失敗が先に出ても、原因が承認の要るファイルなら、勧めは計画を直す（PLAN）
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / ".codd"
            data.mkdir()
            problems = [{"kind": codd.classify("apply", t), "text": t} for t in (
                "実装のテストの検査が失敗しました（1）: python3 -m unittest", codd.PROTECTED_HIT + "（…）: legacy/old.py")]
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

    def test_an_untested_new_name_is_retried_without_asking(self) -> None:
        # テストを足すか「変更不要」と書くかはエージェントが決められる（計画の段の同じ指摘も訊かない）
        self.assertFalse(asks("apply", "新しく足した名前を確かめるテストがありません（…）: `today`（handler.go）"))

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
