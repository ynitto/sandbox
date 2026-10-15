"""ケース・指摘の型と、規則の一覧。

規則はここにだけ書く。コード・分類・既定の重さ・短い名前は、出力・`--list-rules`・設定の
上書きがすべてこの表を引く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DEFECT, DUPLICATE, CONFLICT, GAP = "不備", "重複", "矛盾", "漏れ"
ERROR, WARNING, INFO = "error", "warning", "info"
SEVERITY_ORDER = {INFO: 0, WARNING: 1, ERROR: 2}
SEVERITY_LABEL = {ERROR: "エラー", WARNING: "警告", INFO: "参考"}

# code: (分類, 既定の重さ, 短い名前)
RULES = {
    "D00": (DEFECT, ERROR, "ファイルを読めない"),
    "D01": (DEFECT, WARNING, "ID がない"),
    "D02": (DEFECT, ERROR, "ID が重複している"),
    "D03": (DEFECT, WARNING, "タイトルがない"),
    "D04": (DEFECT, WARNING, "手順がない"),
    "D05": (DEFECT, ERROR, "期待結果がない"),
    "D06": (DEFECT, WARNING, "合否を決められない曖昧な表現"),
    "D07": (DEFECT, ERROR, "書きかけ（TBD・要確認など）"),
    "D08": (DEFECT, WARNING, "テストデータが決まっていない"),
    "D09": (DEFECT, WARNING, "他のケースに依存している"),
    "D10": (DEFECT, WARNING, "期待結果に操作が書かれている"),
    "D11": (DEFECT, INFO, "手順に確認が混ざっている"),
    "D12": (DEFECT, INFO, "1 つの手順に操作が多い"),
    "D13": (DEFECT, WARNING, "要件の紐づけがない"),
    "D14": (DEFECT, WARNING, "要件一覧にない要件を指している"),
    "D15": (DEFECT, ERROR, "同じキーが 2 回書かれている"),
    "U01": (DUPLICATE, ERROR, "完全に同じケース"),
    "U02": (DUPLICATE, WARNING, "ほぼ同じケース"),
    "U03": (DUPLICATE, INFO, "値だけが違うケース（表にまとめられる）"),
    "U04": (DUPLICATE, INFO, "同じタイトルで中身が違う"),
    "C01": (CONFLICT, ERROR, "同じ手順で逆の期待結果"),
    "C02": (CONFLICT, ERROR, "ケースの中で食い違う"),
    "C03": (CONFLICT, WARNING, "同じ手順で期待結果が違う"),
    "C04": (CONFLICT, WARNING, "数値の条件が食い違う"),
    "G01": (GAP, ERROR, "ケースのない要件"),
    "G02": (GAP, WARNING, "異常系がない"),
    "G03": (GAP, WARNING, "境界値を試していない"),
    "G04": (GAP, INFO, "対になる条件を試していない"),
}


@dataclass
class Item:
    """ケースの中の 1 行（前提・手順・期待結果のどれか）。"""

    text: str
    line: int
    field: str  # 例: "steps[2]"


@dataclass
class Case:
    file: str
    line: int
    index: int  # 読み込み順の通し番号
    group: tuple = ()  # suite・feature などの入れ子の名前
    id: Optional[str] = None
    title: str = ""
    preconditions: list = field(default_factory=list)  # list[Item]
    steps: list = field(default_factory=list)
    expected: list = field(default_factory=list)
    requirements: list = field(default_factory=list)  # list[str]
    kind: str = "case"  # "case"（手順と期待結果を持つ）/ "check"（チェックリストの 1 項目）

    @property
    def label(self) -> str:
        return self.id or f"({self.line} 行目)"

    def items(self):
        return [*self.preconditions, *self.steps, *self.expected]

    def procedure_text(self) -> str:
        return "\n".join(i.text for i in (*self.preconditions, *self.steps))

    def expected_text(self) -> str:
        return "\n".join(i.text for i in self.expected)

    def full_text(self) -> str:
        return "\n".join(x for x in (self.title, self.procedure_text(), self.expected_text()) if x)


@dataclass
class Finding:
    code: str
    message: str
    file: str = ""
    line: int = 0
    cases: list = field(default_factory=list)  # ケースのラベル
    excerpt: str = ""
    hint: str = ""
    severity: str = ""  # 空なら規則の既定
    related: list = field(default_factory=list)  # [(file, line)] 2 件目以降のケースの場所

    @property
    def category(self) -> str:
        return RULES[self.code][0]

    @property
    def name(self) -> str:
        return RULES[self.code][2]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "category": self.category,
            "severity": self.severity,
            "name": self.name,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "cases": self.cases,
            "excerpt": self.excerpt,
            "hint": self.hint,
            "related": [{"file": f, "line": ln} for f, ln in self.related],
        }
