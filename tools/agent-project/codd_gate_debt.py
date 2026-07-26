#!/usr/bin/env python3
"""`codd-gate tasks --debt` の stdout を検査し、レコード単位の所見を返す任意部品。

`tools/agent-project` 直下のパッケージ外 sibling として、task schema の最小契約
（object かつ空でない `title`）だけを検査する。不備な1件は `errors` に隔離し、正常な
レコードの検査を続ける。未知キーは保持する。

プロセスの起動、yaml への結線、重複排除は扱わない。`agent_project` パッケージからは
import されず、依存は標準ライブラリのみ。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DriftItem:
    """task schema の最小契約に正規化した1件のドリフト項目。

    `title` と任意の `id` だけを明示フィールドにし、それ以外は `fields` に保持する。
    """
    title: str
    id: "str | None" = None
    fields: "dict" = field(default_factory=dict)

    def to_spec(self) -> dict:
        """正規化前と同じ task dict 形式へ戻す。"""
        spec = {"title": self.title}
        if self.id:
            spec["id"] = self.id
        spec.update(self.fields)
        return spec


@dataclass(frozen=True)
class DebtParseResult:
    """検査結果。`errors` は棄却したレコードごとの所見。"""
    items: "list[DriftItem]"
    errors: "list[str]"


def _normalize_record(raw: object, index: int) -> "tuple[DriftItem | None, str | None]":
    if not isinstance(raw, dict):
        return None, f"[{index}] レコードが object ではない（{type(raw).__name__}）"
    title = str(raw.get("title", "") or "").strip()
    if not title:
        return None, f"[{index}] title が空/欠落している（task.schema.json の required を満たさない）"
    raw_id = raw.get("id")
    rid = str(raw_id).strip() or None if raw_id not in (None, "") else None
    fields = {k: v for k, v in raw.items() if k not in ("title", "id")}
    return DriftItem(title=title, id=rid, fields=fields), None


def parse_debt_output(text: str) -> DebtParseResult:
    """stdout の object/array を検査する。空文字列は正常な0件として扱う。"""
    stripped = (text or "").strip()
    if not stripped:
        return DebtParseResult(items=[], errors=[])
    try:
        data = json.loads(stripped)
    except ValueError as exc:
        return DebtParseResult(items=[], errors=[f"JSON として解釈できない: {exc}"])
    records = data if isinstance(data, list) else [data]
    items: "list[DriftItem]" = []
    errors: "list[str]" = []
    for i, raw in enumerate(records):
        item, err = _normalize_record(raw, i)
        if item is not None:
            items.append(item)
        else:
            errors.append(err)
    return DebtParseResult(items=items, errors=errors)
