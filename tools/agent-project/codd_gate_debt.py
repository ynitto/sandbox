#!/usr/bin/env python3
"""codd_gate_debt — `codd-gate tasks`/`--debt` の出力パースとドリフト項目の正規化
（tools/agent-project 配下）。

codd_gate_detect.py の docstring が明記する通り、`tasks`/`--debt` 出力の per-record 検証
（d1 = .agent-project/bus/runs/run-20260712-213419-5922/artifacts/d1/
codd-gate-autodetect-judgment-design.md の 2.3(b)）は CoddGateStatus（a4・セッション粒度・
1回計算してキャッシュ）に含めず、「実行時の防御的パースとして e 系の責務」と位置づけられている
（d2 の同 run/artifacts/d2/codd-gate-status-interface-design.md 5節も同じ境界を明記）。
理由: `tasks` は呼ぶ**都度**、返る配列の**要素ごとに**適否が変わり得る（ある回は全件 title 付き、
次の回は1件だけ欠落）。CoddGateStatus の「一度 usable=true になったら以後信用してよい」という
不変条件とは異なる粒度のチェックのため、別モジュールとして切り出す。

責務は1点: `codd-gate tasks --debt`（または差分モードの `tasks`）の stdout テキストを
schemas/task.schema.json の契約（`title` が必須。additionalProperties: true で未知キーは保持）
に従ってパースし、`DriftItem` のリストへ正規化すること。d1 の一貫方針（不明・不備はすべて
連携しない側へ倒す）をレコード単位に適用し、1件の不備（非 object・title 欠落）で全体を捨てず、
その1件だけ errors に落として残りは処理を続ける（呼び出し側のループを止めない）。

このモジュールが意図的に含めないもの（同一 run の別タスクの責務）:
  - `codd-gate` プロセス自体の起動・stdout 取得（b2/e2。ここでは受け取ったテキストだけを扱う）

agent-project 本体の intake 経路（`cfg.intake_cmd` → `agent_project/model.py` の `run_intake`）は
**この module に依存しない**。差し込み点は `intake_cmd` 設定そのものであり、本体側は検出器非依存の
`_parse_intake_records`（model.py 同梱の汎用パーサ）で同じレコード単位検証（非 object・title 欠落を
その1件だけ errors に落とす）を行う。id ベースの冪等排除（e2）も `run_intake` 側の責務。
本 module は `DriftItem` への正規化（`to_spec()` 経由の spec 化を含む）を必要とする呼び出し側のための
独立したアダプタとして残り、重複判定は持たない。

依存は標準ライブラリのみ。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DriftItem:
    """schemas/task.schema.json に正規化した1件のドリフト項目（プロセス内の中間表現）。

    `title`（必須）と `id`（e2 の重複投入防止キーとして直接使う想定なので昇格）だけを
    明示フィールドにし、それ以外の既知/未知キーは `fields` にそのまま保持する
    （additionalProperties: true＝前方互換を型の面でも崩さない）。
    """
    title: str
    id: "str | None" = None
    fields: "dict" = field(default_factory=dict)

    def to_spec(self) -> dict:
        """`enqueue_task(cfg, spec)` / `run_intake` がそのまま受け取れる dict へ戻す。"""
        spec = {"title": self.title}
        if self.id:
            spec["id"] = self.id
        spec.update(self.fields)
        return spec


@dataclass(frozen=True)
class DebtParseResult:
    """パース結果。`items` は正規化済みドリフト項目、`errors` は棄却したレコードの理由
    （1レコード1文字列。呼び出し側はこれを journal 等へそのまま流せる）。"""
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
    """`codd-gate tasks --debt`（差分モードの `tasks` も同形式）の stdout を
    `DriftItem` のリストへ正規化する。

    トップレベルは object（1件）でも array（複数件）でもよい——`codd-gate.py` の
    `_emit_tasks` は常に array を吐くが、task.schema.json は「`enqueue --json` と同形式」を
    契約にしており、agent-project.py の `run_intake` も
    `data if isinstance(data, list) else [data]` で両方を吸収している（それと対称に扱う）。
    空文字列・空白のみは「0件」として扱う（codd-gate 側に該当する負債が無いだけの正常系）。
    """
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
