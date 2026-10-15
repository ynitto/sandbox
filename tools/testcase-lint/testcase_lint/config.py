"""設定（任意）。既定のままでも動く。

```yaml
rules:            # 規則ごとに off / error / warning / info
  D11: off
  G04: warning
similarity:
  duplicate: 0.9    # これ以上似ていれば「ほぼ同じ」
  same_steps: 0.92  # 前提＋手順がこれ以上似ていれば「同じ手順」とみなして期待結果を比べる
vague_words: [見栄えがよい]         # 曖昧な表現を足す（正規表現）
negative_words: [差し戻し]          # 異常系とみなす語を足す（正規表現）
pairs:                              # 対になる条件を足す
  - {a_label: 会員, b_label: 非会員, a: [(?<!非)会員], b: [非会員, ゲスト]}
fields:                             # 項目名の綴りを足す
  expected: [確認ポイント]
```
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .model import RULES, SEVERITY_ORDER


@dataclass
class Config:
    rules: dict = field(default_factory=dict)
    duplicate: float = 0.9
    same_steps: float = 0.92
    vague_words: list = field(default_factory=list)
    negative_words: list = field(default_factory=list)
    pairs: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)

    def severity(self, code: str):
        """規則の重さ。None なら止めている。"""
        v = self.rules.get(code)
        if v is None:
            return RULES[code][1]
        v = str(v).lower()
        if v in ("off", "false", "none", "0"):
            return None
        return v


def load_config(path) -> Config:
    if not path:
        return Config()
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("設定ファイルは「キー: 値」の形で書きます")
    rules = {}
    for k, v in (raw.get("rules") or {}).items():
        k = str(k).upper()
        if k not in RULES:
            raise ValueError(f"知らない規則です: {k}（--list-rules で一覧）")
        if v is False:
            v = "off"
        if str(v).lower() not in ("off", *SEVERITY_ORDER):
            raise ValueError(f"{k} の値は off / error / warning / info のどれかです: {v}")
        rules[k] = v
    sim = raw.get("similarity") or {}
    return Config(
        rules=rules,
        duplicate=float(sim.get("duplicate", 0.9)),
        same_steps=float(sim.get("same_steps", 0.92)),
        vague_words=[str(x) for x in raw.get("vague_words") or []],
        negative_words=[str(x) for x in raw.get("negative_words") or []],
        pairs=list(raw.get("pairs") or []),
        fields={str(k): list(v or []) for k, v in (raw.get("fields") or {}).items()},
    )
