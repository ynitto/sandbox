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

    def test_unknown_wording_is_asked(self) -> None:
        # 目印を足し忘れた指摘は、訊く側に落ちる。
        for phase in ("plan", "apply"):
            with self.subTest(phase=phase):
                self.assertTrue(asks(phase, "まだどの目印にも当たらない新しい指摘"))


if __name__ == "__main__":
    unittest.main()
