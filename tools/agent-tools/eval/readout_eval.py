#!/usr/bin/env python3
"""agent-herd judge（1 トークン目の分布の読み出し）で既存セルを引き直し、確度の当たり方を測る。

judge_eval.py とは**別物**なので、ファイルも台帳も分ける——あちらの「judge」は評価ハーネス
上の役割名（`kind="judge"` の J1 / J2 セル）で、こちらは `agentcore.judge`（選択肢の上の確率
分布を返す判断 AI）の読み出し経路。混ぜると台帳の行がどちらの judge なのか読めなくなる。

測るのは設計 §6 の 2 つだけ（壁時計の比較は別の日）:

1. `coverage` の分布。ラベルに落ちた質量が低い（< 0.8）割合。低ければプロンプトの形の問題で、
   確率を信じる前にそちらを直す。
2. `confidence` の区間ごとの正答率（信頼度図）。`--min-confidence` の既定の根拠になる。

ケースの入力と正解は既存セルから**借りる**（写さない）。judge_eval の F1 / J2 / CL1 / E1〜E3、
project_eval の RO1〜RO3——RO は judge_eval でなく project_eval にある。合否はそのセルの
`check` に通して決めるので、正解はこのファイルのどこにも書かない。このファイルが持つのは
「同じ入力をどう**問い**の形にするか」だけで、route は本番の問い（`_route_judge_questions`）を
そのまま呼ぶ。

ollama に届かない環境では 1 件も走らせず、台帳へ何も書かずに終了コード 0 で終わる。CI は
ollama を持たないので、**測っていないものを数字として残さない**（設計 §6 が「実測は未着手」と
書いているのと同じ作法）。

使い方: python3 readout_eval.py [--model gemma4:e4b] [--repeat 3] [--cases F1,RO1]
設計: docs/plans/2026-09-19-agent-herd-system-one-judge-design.md §4 / §6。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))

from agentcore import judge, ollama_loop  # noqa: E402

LEDGER_DIR = Path(os.environ.get("READOUT_EVAL_DIR", "/tmp/agent-readout-eval"))
MODEL = judge.DEFAULT_MODEL
# ラベルに落ちた質量の下限。これを割る回が多ければ、確率ではなくプロンプトの形を直す（§6 の 1）。
COVERAGE_FLOOR = 0.8
BINS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))


# --------------------------------------------------------------------------- 問いの立て方
# 既存セルの入力（goal / deps / results / task）を judge の問いへ写す。**正解は写さない**
# ——合否はセルの `check` がつける。

def _filter_cell(case: dict):
    """filter: 候補 1 件 = boolean の問い 1 つ（本番 agent-flow の `filter_judge` と同じ立て方）。"""
    text = case["deps"]["gen"]["output"]
    ids = re.findall(r"^\[([^\]]+)\]", text, re.M)
    criterion = " ".join(str(case["goal"]).split())
    questions = {i: {"type": "boolean",
                     "instructions": f"Does candidate [{i}] satisfy this criterion? {criterion}"}
                 for i in ids}
    return ("Candidates:\n" + text, questions,
            lambda answers: [i for i in ids if answers[i].get("value")])


def _judge_cell(case: dict):
    """judge: 候補の上の choice 1 問。選択肢の説明は候補の行そのもの。"""
    text = case["deps"]["gen"]["output"]
    criteria = dict(re.findall(r"^\[([^\]]+)\]\s*(.+)$", text, re.M))
    questions = {"winner": {"type": "choice", "criteria": criteria,
                            "instructions": " ".join(str(case["goal"]).split())}}
    return text, questions, lambda answers: {"winner": answers["winner"]["choice"]}


# 分類の 3 ラベルは**選択肢の空間**であって正解ではない（どれが正解かは `check` だけが知る）。
CLASSIFY_LABELS = {"bug": "不具合の報告（以前は動いていた・意図と違う）",
                   "feature": "機能の要望（無い機能を足してほしい）",
                   "question": "使い方の質問"}


def _classify_cell(case: dict):
    """classify: ラベルの上の choice 1 問。本番の下流が読む `class=<ラベル>` の形へ戻して渡す。"""
    questions = {"class": {"type": "choice", "criteria": CLASSIFY_LABELS,
                           "instructions": "この問い合わせはどの分類か。"}}
    return (str(case["goal"]), questions,
            lambda answers: f"class={answers['class']['choice']}")


DECISIONS = {"done": "要求を満たしており、これ以上の仕事は要らない",
             "replan": "足りない仕事があり、計画を足す必要がある"}


def _evaluator_cell(case: dict):
    """evaluator: done / replan の choice 1 問。結果要約は本番（continuation.py）と同じ 1 行形式。"""
    request = importlib.import_module("judge_eval").REQUEST
    state = "\n".join(f"- {nid} ({kind}) [{status}]: {out[:160]}"
                      for nid, kind, status, out in case["results"])
    questions = {"decision": {"type": "choice", "criteria": DECISIONS,
                              "instructions": f"要求は「{request}」。この結果で要求を"
                                              "満たしたか、計画を足すべきか。"}}
    return state, questions, lambda answers: {"decision": answers["decision"]["choice"]}


def _route_cell(case: dict):
    """route: 本番の問いと状態（`agent_project` の `_route_judge_*`）をそのまま呼ぶ。

    この面だけは本番に judge の配線が既にあるので、問いの立て方まで借りる。`check` は本番の
    受け方（`_extract_json_obj` → `workspace`）を通すので、答えを `{"workspace": …}` へ戻す。
    """
    project_eval = importlib.import_module("project_eval")
    ap = project_eval.ap
    questions = ap._route_judge_questions(project_eval.WORKSPACES)

    def to_check(answers):
        choice = str(answers["workspace"].get("choice") or "")
        return json.dumps({"workspace": "" if choice == judge.OTHER_KEY else choice})

    return ap._route_judge_state(case["task"]), questions, to_check


# セル → (ケース定義を持つモジュール, 問いの立て方)。モジュールは遅延 import する
# （project_eval は agent_project が読めない木で SystemExit する）。
CELLS = {
    "F1": ("judge_eval", _filter_cell),
    "J2": ("judge_eval", _judge_cell),
    "CL1": ("judge_eval", _classify_cell),
    "E1": ("judge_eval", _evaluator_cell),
    "E2": ("judge_eval", _evaluator_cell),
    "E3": ("judge_eval", _evaluator_cell),
    "RO1": ("project_eval", _route_cell),
    "RO2": ("project_eval", _route_cell),
    "RO3": ("project_eval", _route_cell),
}


# --------------------------------------------------------------------------- 集計（ollama を呼ばない）
def bin_of(confidence: float) -> "tuple[float, float]":
    """確度を 5 区間のどれかへ。区間は下を含み上を含まない——1.0 だけは最後の区間に入れる。"""
    for low, high in BINS:
        if confidence < high:
            return (low, high)
    return BINS[-1]


def _median(values: "list[float]") -> "float | None":
    return sorted(values)[len(values) // 2] if values else None


def summarize(rows: "list[dict]") -> dict:
    """台帳の行から (1) coverage の分布 (2) 信頼度図 を作る。

    `confidence` が無い行（judge が答えを読めずに落ちた回）は信頼度図から外す——確度の
    当たり方の話に、確度の無い回を混ぜない。外した件数は `errors` に残す。
    """
    scored = [r for r in rows if r.get("confidence") is not None]
    coverages = [float(r["coverage"]) for r in scored if r.get("coverage") is not None]
    below = [c for c in coverages if c < COVERAGE_FLOOR]
    reliability = []
    for low, high in BINS:
        hit = [r for r in scored if bin_of(float(r["confidence"])) == (low, high)]
        ok = sum(1 for r in hit if r.get("ok"))
        reliability.append({"bin": f"{low:.1f}-{high:.1f}", "n": len(hit), "ok": ok,
                            "accuracy": round(ok / len(hit), 4) if hit else None})
    return {
        "n": len(rows), "errors": len(rows) - len(scored),
        "ok": sum(1 for r in scored if r.get("ok")),
        "coverage": {"n": len(coverages), "floor": COVERAGE_FLOOR,
                     "below_floor": len(below),
                     "share_below_floor": round(len(below) / len(coverages), 4) if coverages else None,
                     "median": _median(coverages)},
        "reliability": reliability,
    }


def format_report(summary: dict) -> str:
    cov = summary["coverage"]
    lines = [f"=== coverage の分布（{cov['n']} 問）",
             f"  中央値 {cov['median']}  {cov['floor']} 未満 {cov['below_floor']}/{cov['n']}"
             f"（{cov['share_below_floor']}）",
             "", "=== 信頼度図（確度の区間ごとの正答率）"]
    for row in summary["reliability"]:
        share = "—" if row["accuracy"] is None else f"{row['ok']}/{row['n']} = {row['accuracy']}"
        lines.append(f"  {row['bin']}  {share}")
    lines.append(f"\n  合計 {summary['ok']}/{summary['n'] - summary['errors']}"
                 f"  読めなかった回 {summary['errors']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 実行
def ollama_reachable(timeout: float = 3.0) -> bool:
    """`/api/tags` に届くか。届かない木では 1 件も走らせない（CI はここで止まる）。"""
    try:
        with urllib.request.urlopen(ollama_loop.host_url().rstrip("/") + "/api/tags",
                                    timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def run_one(cid: str, run: int, model: str) -> dict:
    """1 セルを judge で 1 回引く。確度と coverage は**問いをまたいだ最小**を採る。

    棄権は全問一括（`abstained` に 1 つでも載れば呼び出し側は倒れる）なので、束を代表する
    のは最も弱い問い。1 セル = 1 点として信頼度図に載せる。
    """
    module, build = CELLS[cid]
    case = importlib.import_module(module).CASES[cid]
    state, questions, to_check = build(case)
    started = time.time()
    row = {"case": cid, "run": run, "model": model, "questions": len(questions)}
    try:
        result = judge.evaluate(state, questions, model=model)
    except judge.JudgeError as exc:
        return dict(row, ok=False, confidence=None, coverage=None, note=str(exc),
                    wall=round(time.time() - started, 2))
    answers = result["answers"]
    ok, note = case["check"](to_check(answers))
    return dict(row, ok=bool(ok), note=note,
                confidence=min(float(a["confidence"]) for a in answers.values()),
                coverage=min(float(a["coverage"]) for a in answers.values()),
                methods=sorted({str(a["method"]) for a in answers.values()}),
                abstained=judge.abstained(answers, 0.0),
                usage=result["usage"], wall=round(time.time() - started, 2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--cases", default=",".join(CELLS))
    args = parser.parse_args()
    cids = [c.strip() for c in args.cases.split(",") if c.strip() in CELLS]
    if not cids:
        print(f"測れるセルがありません（選べるのは {', '.join(CELLS)}）")
        return 2
    if not ollama_reachable():
        print(f"ollama に届かないので測っていない（{ollama_loop.host_url()}）。台帳は書かない。")
        return 0

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    print(f"model={args.model} cases={cids} repeat={args.repeat}\n")
    rows = []
    for cid in cids:
        for run in range(1, args.repeat + 1):
            row = run_one(cid, run, args.model)
            rows.append(row)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            mark = "○" if row["ok"] else "×"
            print(f"  {mark} {cid}-{run} 確度 {row['confidence']} coverage {row['coverage']}"
                  f"  {row['note'][:60]}")
    print("\n" + format_report(summarize(rows)))
    print(f"\n台帳: {ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
