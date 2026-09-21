#!/usr/bin/env python3
"""route（依頼の振り分け。`agentcore.route`）の較正セル。readout_eval の `RT1〜RT4` が読む。

標本は `data/route/corpus.json`（依頼 1 件 = 1 ケース。候補はタスク 8 / ワークフロー 3 /
スキル 6 の固定集合で、agent-app が絞る前の「候補が多い側」を測る）。正解は人が付けたもの。

セルは問いの種類ごとに分ける——readout の oracle は「正解を通す割り当てがちょうど 1 つ」を
総当たりで確かめるので、6 問を束ねると組み合わせが 4096 を超える。

| 族 | 問い | 正解 |
|---|---|---|
| RT1 | `handling` choice 1 問 | answer / converse / task / flow |
| RT2 | `task` choice 1 問（候補 8 + other） | 流用するタスクの id。流用しない依頼は other |
| RT3 | `skill:<name>` boolean × 6 | 添えるスキルの集合 |
| RT4 | `routine` boolean 1 問 | 入力だけ替えて繰り返す形か |

`hold_sweep` は RT1 と RT2 の台帳を依頼ごとに突き合わせ、hold 下限（`route.hold_min_confidence`）
ごとに「会話を止めた件数」と「止めて正しかった率」を出す。設計 §6 の 2 がこれで、`route` の
`hold` は handling と流用先の**両方**が下限以上のときだけ真（`agentcore.route.shape`）。

使い方:
  python3 readout_eval.py --calibration --cases RT1,RT2,RT3,RT4 --repeat 1 --output-dir results/<run>
  python3 route_cells.py --hold-sweep results/<run>/ledger.jsonl
設計: docs/plans/2026-09-21-agent-app-judge-request-routing-design.md §6。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agentcore"))

from agentcore import judge  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent / "data" / "route" / "corpus.json"
FAMILIES = ("RT1", "RT2", "RT3", "RT4")
KINDS = {"RT1": "handling", "RT2": "task", "RT3": "skills", "RT4": "routine"}
HOLD_THRESHOLDS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9)


def load_corpus(path: Path = CORPUS_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("corpus の id が重複しています")
    for case in data["cases"]:
        if case["handling"] not in ("answer", "converse", "task", "flow"):
            raise ValueError(f"{case['id']}: handling が不正です")
        if (case["handling"] == "task") != bool(case.get("task")):
            raise ValueError(f"{case['id']}: handling=task と task の有無が食い違います")
    return data


def _check_eq(expected):
    return lambda got: (got == expected, f"got {got!r} expected {expected!r}")


def _cases(corpus: dict) -> dict:
    out: dict = {}
    for case in corpus["cases"]:
        base = {"request": case["request"], "candidates": corpus["candidates"], "id": case["id"],
                "gold": {"handling": case["handling"], "task": case.get("task"),
                         "skills": sorted(case.get("skills") or []), "routine": bool(case.get("routine"))}}
        expected = {"RT1": case["handling"],
                    "RT2": case.get("task") or judge.OTHER_KEY,
                    "RT3": sorted(case.get("skills") or []),
                    "RT4": "yes" if case.get("routine") else "no"}
        for family in FAMILIES:
            out[f"{family}-{case['id']}"] = dict(base, kind=KINDS[family], expected=expected[family],
                                                 check=_check_eq(expected[family]))
    return out


CORPUS = load_corpus()
CASES = _cases(CORPUS)


def cell_ids(family: str) -> "list[str]":
    return [cid for cid in CASES if cid.startswith(f"{family}-")]


# --------------------------------------------------------------------------- hold の掃引
def _answer(row: dict, name: str) -> "dict | None":
    answer = (row.get("answers") or {}).get(name)
    return answer if isinstance(answer, dict) and answer.get("method") != judge.METHOD_TEXT else None


def hold_sweep(rows: "list[dict]", thresholds=HOLD_THRESHOLDS) -> dict:
    """RT1（handling）と RT2（task）の行を依頼 id で突き合わせ、hold 下限ごとに数える。

    held        … handling が task で、handling と task の確度がどちらも下限以上（会話を止める）
    correct     … held のうち、正解も task で流用先も一致
    wrong_hold  … held のうち、正解が task でない、または流用先が違う（人の手数が増える誤り）
    missed      … 正解が task なのに held にならなかった（従来どおり会話で実行。害は小さい）
    flow は RT2 に無いので数えない（標本 4 件。別に測る）。
    """
    by_id: dict = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        family, _, rid = str(row["case"]).partition("-")
        if family in ("RT1", "RT2"):
            by_id.setdefault((rid, row.get("run", 1)), {})[family] = row
    pairs = [(v["RT1"], v["RT2"]) for v in by_id.values() if "RT1" in v and "RT2" in v]
    out = {"pairs": len(pairs), "thresholds": []}
    for threshold in thresholds:
        held = correct = wrong = missed = 0
        for h_row, t_row in pairs:
            gold = CASES[h_row["case"]]["gold"]
            h, t = _answer(h_row, "handling"), _answer(t_row, "task")
            holds = (h and t and h.get("choice") == "task" and t.get("choice") != judge.OTHER_KEY
                     and float(h.get("confidence") or 0) >= threshold
                     and float(t.get("confidence") or 0) >= threshold)
            if holds:
                held += 1
                if gold["handling"] == "task" and t["choice"] == gold["task"]:
                    correct += 1
                else:
                    wrong += 1
            elif gold["handling"] == "task":
                missed += 1
        out["thresholds"].append({"hold_min_confidence": threshold, "held": held, "correct": correct,
                                  "wrong_hold": wrong, "missed": missed,
                                  "precision": (correct / held) if held else None})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="route 較正セルの補助（hold の掃引）")
    parser.add_argument("--hold-sweep", metavar="LEDGER", help="readout_eval --calibration の ledger.jsonl")
    args = parser.parse_args()
    if not args.hold_sweep:
        print(f"cells: {len(CASES)}（{', '.join(f'{f}={len(cell_ids(f))}' for f in FAMILIES)}）")
        return 0
    rows = [json.loads(line) for line in Path(args.hold_sweep).read_text(encoding="utf-8").splitlines() if line.strip()]
    result = hold_sweep(rows)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
