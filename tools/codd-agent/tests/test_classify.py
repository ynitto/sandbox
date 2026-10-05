"""止まった理由を「利用者に訊く側」と「訊かずにやり直す側」のどちらに分けるかを、代表的な文言で固定する。

種類の名前や件数は固定しない（種類を増やした日・文言を足した日に落ちないように）。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
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
)
ASK = (
    "ずれがあるのに、参照先の変更案が「なし」です（ずれを残すなら、ずれではなくその他に書く）",
    "テストで得たものが、文書の求めを満たしていません（実装を直すか、目安を変えるなら利用者に確かめて計画に挙げてから文書を直してください）",
    "文書の書式（見出しの並び）が今の書式から外れています: docs/api.md — 例（見本: docs/a.md。書式は決まりとして守る）",
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


if __name__ == "__main__":
    unittest.main()
