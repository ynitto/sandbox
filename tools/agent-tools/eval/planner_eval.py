#!/usr/bin/env python3
"""planner（要求 → タスクグラフ）の実力を測る。P9 の最小 eval（計画 2026-08-14 / 2026-08-22 §4.2 B1）。

測るのは**本番の planner そのもの**——flow-planner スキルの `plan.py`（3 段パイプライン）を
本番と同じ引数で子プロセスとして呼び、返ってきたグラフを決定的チェッカーで判定する。
判定役（LLM）は使わない。正解はグラフの**構造**で、要求文を組んだ時点で決まっている
（構成的ラベル）: 順序がある 3 段は鎖になる、独立 3 件 + 統合は fan-out + 統合ノード、
ファイル列挙は split が 1 つだけ、1 行のタイポ修正は 1〜2 ノード。

worker_eval / text_eval と同じ規律:
  - 本番の実装を**呼ぶ**（写さない）。プロンプトはスキルの plan.py が組む。
  - 合否は決定的チェッカーだけ。`--selfcheck` で LLM 抜きに検証できる。
  - `--model` の差し替えだけで別モデルを測れる。

使い方: python3 planner_eval.py [--model gemma4:e4b] [--agent-cli ollama-json] [--repeat 3]
        [--cases PL1,PL2,PL3,PL4] [--granularity auto] [--selfcheck]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
# 本番 planner。既定はリポジトリの正典（配布物 ~/.claude/skills/flow-planner は同じ中身の写し）。
PLAN_SCRIPT = Path(os.environ.get("FLOW_PLANNER_SCRIPT",
                                  str(REPO / ".github/skills/flow-planner/scripts/plan.py")))
LEDGER_DIR = Path(os.environ.get("PLANNER_EVAL_DIR", "/tmp/agent-planner-eval"))
MODEL = "gemma4:e4b"
AGENT_CLI = "ollama-json"     # 本番の planner 変種（agents/aider.json: planner → ollama-json）
WALL_LIMIT = 900.0            # 3 段 × 1 呼び出し。agent-flow は agent_timeout × 3 を見込む
GRANULARITY = "auto"
VALID_KINDS = {"classify", "extract", "filter", "generate", "human", "judge", "map",
               "reduce", "retrieve", "split", "synthesize", "verify", "work"}
PATTERNS = {"classify-and-act", "fan-out-and-synthesize", "adversarial-verification",
            "generate-and-filter", "tournament", "loop-until-done", "map-reduce"}
try:  # 正典があれば上書き（engine と同じ「揃っている限り本番を使う」）
    from agentcore.nodecontract import VALID_KINDS as _VK  # noqa: E402
    VALID_KINDS = set(_VK)
except Exception:  # noqa: BLE001
    pass
if getattr(engine, "_FLOW", None) is not None and hasattr(engine._FLOW, "PATTERNS"):
    PATTERNS = set(engine._FLOW.PATTERNS)

# ------------------------------------------------------------------ 共通の構造検査


def _closure(tasks: list[dict]) -> dict[str, set]:
    """各タスクの推移的依存集合（循環があれば途中で止まる——循環は別に検出する）。"""
    deps = {t["id"]: set(t.get("deps") or []) for t in tasks}
    out: dict[str, set] = {}

    def walk(tid, seen):
        if tid in out:
            return out[tid]
        acc = set()
        for d in deps.get(tid, ()):
            if d in seen:
                continue
            acc.add(d)
            acc |= walk(d, seen | {d})
        out[tid] = acc
        return acc

    for t in tasks:
        walk(t["id"], {t["id"]})
    return out


def _has_cycle(tasks: list[dict]) -> bool:
    deps = {t["id"]: list(t.get("deps") or []) for t in tasks}
    state: dict[str, int] = {}

    def visit(n):
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(d) for d in deps.get(n, ()) if d in deps):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in deps)


def invariants(data: dict) -> list[str]:
    """どの要求でも満たすべき契約（planner の出力契約そのもの）。"""
    errors = []
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["tasks が空"]
    ids = [str(t.get("id")) for t in tasks if isinstance(t, dict)]
    if len(ids) != len(set(ids)):
        errors.append("id が重複")
    known = set(ids)
    for t in tasks:
        if not isinstance(t, dict):
            errors.append("task が dict でない")
            continue
        if not str(t.get("goal") or "").strip():
            errors.append(f"{t.get('id')}: goal が空")
        if str(t.get("kind", "work")) not in VALID_KINDS:
            errors.append(f"{t.get('id')}: 未知の kind {t.get('kind')}")
        bad = [d for d in (t.get("deps") or []) if str(d) not in known]
        if bad:
            errors.append(f"{t.get('id')}: 存在しない deps {bad}")
    if _has_cycle([t for t in tasks if isinstance(t, dict)]):
        errors.append("依存に循環")
    strategy = data.get("strategy") or {}
    unknown = [p for p in (strategy.get("patterns") or []) if p not in PATTERNS]
    if unknown:
        errors.append(f"カタログ外の pattern {unknown}")
    return errors


def _own_text(task: dict) -> str:
    """goal のうち、そのノード自身の仕事を書いた部分（`[out_of_scope]` 行は他ノードのラベルを
    引き合いに出すので外す）。"""
    return "\n".join(line for line in str(task.get("goal") or "").splitlines()
                     if not line.strip().lower().startswith("[out_of_scope]"))


def _norm_id(value: str) -> str:
    return str(value or "").lower().replace("-", "_")


def _find(tasks: list[dict], label: str, labels: tuple = ()) -> "dict | None":
    """ラベルを担うノードを 1 つに決める。goal 本文（out_of_scope 行を除く）に含む → 候補。
    複数なら、他のラベルを含まない（そのラベル専用の）ノードに絞る。goal に無ければ id で
    探す（`t_kirby_a` 等。要求は goal に書けと言っているが、同定できる限り拾う）。
    それでも 1 つに決まらなければ None＝「どれがそのラベルの仕事か機械に分からない」。"""
    hits = [t for t in tasks if label in _own_text(t)]
    if len(hits) > 1:
        others = [o for o in labels if o != label]
        hits = [t for t in hits if not any(o in _own_text(t) for o in others)]
    if not hits:
        hits = [t for t in tasks if _norm_id(label) in _norm_id(t.get("id"))]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ ケース
# 正解がラベルから従うように、ラベルは 1 度だけ定義して要求文へ流し込む。
# 「goal にラベルを含めよ」は要求側の契約であり、planner がそれを守るかも測定の一部。

PL1_LABELS = ("KIRBY-A", "KIRBY-B", "KIRBY-C")
PL1_REQUEST = (
    "三段階で進める。まず {a} として設定ファイルの読込処理を実装する。それが終わったら "
    "{b} として、読込結果を使う変換処理を実装する。最後に {c} として変換処理の単体テストを追加する。"
    "各段は前の段の成果物が無いと着手できない。各タスクの goal にはそのラベル（{a} / {b} / {c}）を"
    "必ずそのまま含めること。"
).format(a=PL1_LABELS[0], b=PL1_LABELS[1], c=PL1_LABELS[2])

PL2_LABELS = ("LIB-X", "LIB-Y", "LIB-Z")
PL2_REQUEST = (
    "候補ライブラリ {x}・{y}・{z} の 3 つを、それぞれ独立に調べる（互いの結果に依存しない。"
    "並行してよい）。3 つの調査が揃ったら、最後に 3 つを同じ観点で比較した表を 1 つにまとめる。"
    "各調査タスクの goal にはそのラベル（{x} / {y} / {z}）を必ずそのまま含めること。"
).format(x=PL2_LABELS[0], y=PL2_LABELS[1], z=PL2_LABELS[2])

PL3_REQUEST = (
    "ディレクトリ notes/ にある各 Markdown ファイル（ITEM-01.md 〜 ITEM-12.md の 12 件）について、"
    "ファイルごとに見出し一覧を抽出する。ファイルごとの処理は互いに独立で、並列にできる。"
    "最後に全ファイル分の見出しを 1 つの索引ファイルにまとめる。"
)

PL4_REQUEST = "README.md の 1 行目にあるタイポ（'Instal' → 'Install'）を直す。それだけ。"


def check_pl1(data: dict) -> tuple[bool, str]:
    tasks = data["tasks"]
    found = {lbl: _find(tasks, lbl, PL1_LABELS) for lbl in PL1_LABELS}
    missing = [lbl for lbl, t in found.items() if t is None]
    if missing:
        return False, f"ラベルを 1 つだけ含むタスクが無い: {missing}"
    closure = _closure(tasks)
    a, b, c = (found[l]["id"] for l in PL1_LABELS)
    if a not in closure[b]:
        return False, f"{PL1_LABELS[1]} が {PL1_LABELS[0]} に依存していない"
    if b not in closure[c]:
        return False, f"{PL1_LABELS[2]} が {PL1_LABELS[1]} に依存していない"
    if any(t.get("kind") == "split" for t in tasks):
        return False, "順序課題に split がある"
    return True, f"鎖 {a}→{b}→{c}（{len(tasks)} ノード）"


def check_pl2(data: dict) -> tuple[bool, str]:
    tasks = data["tasks"]
    found = {lbl: _find(tasks, lbl, PL2_LABELS) for lbl in PL2_LABELS}
    missing = [lbl for lbl, t in found.items() if t is None]
    if missing:
        return False, f"ラベルを 1 つだけ含むタスクが無い: {missing}"
    closure = _closure(tasks)
    ids = [found[l]["id"] for l in PL2_LABELS]
    for i in ids:
        for j in ids:
            if i != j and j in closure[i]:
                return False, f"独立であるべき {i} が {j} に依存"
    joiners = [t for t in tasks if t["id"] not in ids and set(ids) <= closure[t["id"]]]
    if not joiners:
        return False, "3 件すべてに依存する統合ノードが無い"
    patterns = set((data.get("strategy") or {}).get("patterns") or [])
    if "fan-out-and-synthesize" not in patterns:
        return False, f"pattern に fan-out-and-synthesize が無い: {sorted(patterns)}"
    return True, f"fan-out 3 + 統合 {joiners[0]['id']}（{len(tasks)} ノード）"


def check_pl3(data: dict) -> tuple[bool, str]:
    tasks = data["tasks"]
    splits = [t for t in tasks if t.get("kind") == "split"]
    if len(splits) != 1:
        return False, f"split ノードが {len(splits)} 個（1 個であるべき）"
    sid = splits[0]["id"]
    chained = [t["id"] for t in tasks if sid in (t.get("deps") or [])]
    if chained:
        return False, f"split の後ろに静的チェーン {chained}（map/reduce は実行時展開）"
    patterns = set((data.get("strategy") or {}).get("patterns") or [])
    if "map-reduce" not in patterns:
        return False, f"pattern に map-reduce が無い: {sorted(patterns)}"
    return True, f"split {sid} のみ（{len(tasks)} ノード）"


def check_pl4(data: dict) -> tuple[bool, str]:
    """成果物は 1 つ（1 行の修正）。成果ノード（work/generate/map）は 2 つまで許し、
    verify を 1 つ足すまでは過分解と呼ばない（flow-planner の coarse は成果ノード 1〜3 の
    範囲だが、「読む」「特定する」を別ノードに切るのは範囲内でも過分解である）。"""
    tasks = data["tasks"]
    kinds = [t.get("kind", "work") for t in tasks]
    produce = [k for k in kinds if k in ("work", "generate", "map")]
    if not produce:
        return False, "成果ノード（work/generate）が無い"
    if len(produce) > 2:
        return False, f"1 行の修正に成果ノード {len(produce)} 個（過分解）"
    extra = [k for k in kinds if k not in ("work", "generate", "map", "verify")]
    if extra:
        return False, f"成果・verify 以外の kind: {sorted(set(extra))}"
    if len(tasks) > 3:
        return False, f"1 行の修正に {len(tasks)} ノード（過分解）"
    return True, f"{len(tasks)} ノード {sorted(set(kinds))}"


CASES = {
    "PL1": dict(genre="順序（鎖）", request=PL1_REQUEST, check=check_pl1),
    "PL2": dict(genre="fan-out + 統合", request=PL2_REQUEST, check=check_pl2),
    "PL3": dict(genre="列挙（map-reduce）", request=PL3_REQUEST, check=check_pl3,
                probe_files=[f"notes/ITEM-{i:02d}.md" for i in range(1, 13)]),
    "PL4": dict(genre="単一（過分解の検出）", request=PL4_REQUEST, check=check_pl4),
}

# ------------------------------------------------------------------ 実行


def _probe_root(case: dict) -> str:
    """列挙 probe の起点。ケースが要求する実ファイルを一時ディレクトリに置く（LLM 無しの走査用）。"""
    root = tempfile.mkdtemp(prefix="planner-eval-")
    for rel in case.get("probe_files") or ():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {p.stem}\n\n## 見出し\n", encoding="utf-8")
    return root


def _skill_env() -> dict:
    """agent-flow が plan.py へ渡すのと同じ環境（agentcore を import できる PYTHONPATH）。
    これが無いと plan.py は組み込み 4 種しか知らず、agents/<name>.json の CLI を組めない。"""
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "AGENT_OLLAMA_THINK": "off"}
    root = str(REPO / "tools/agent-tools/agentcore")
    prev = env.get("PYTHONPATH", "")
    if root not in prev.split(os.pathsep):
        env["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
    return env


def call(case: dict) -> "tuple[int, str, str, float]":
    root = _probe_root(case)
    cmd = [sys.executable, str(PLAN_SCRIPT), case["request"], "--agent-cli", AGENT_CLI,
           "--model", MODEL, "--granularity", GRANULARITY, "--review", "false",
           "--probe-root", root]
    started = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=WALL_LIMIT,
                           cwd=root, env=_skill_env())
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.time() - started


def judge(case: dict, rc: int, out: str, err: str, wall: float):
    data = None
    if wall >= WALL_LIMIT:
        mode, ok, note = "timeout", False, "上限超過"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-160:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        try:
            data = json.loads(out)
        except Exception as e:  # noqa: BLE001
            mode, ok, note = "unparsable", False, f"JSON を読めない: {e}"
        else:
            broken = invariants(data)
            if broken:
                mode, ok, note = "contract", False, "契約違反: " + "; ".join(broken[:3])
            else:
                ok, note = case["check"](data)
                mode = "correct" if ok else "wrong"
    return data, mode, ok, note


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    rc, out, err, wall = call(case)
    data, mode, ok, note = judge(case, rc, out, err, wall)
    tasks = (data or {}).get("tasks") or [] if isinstance(data, dict) else []
    strategy = (data or {}).get("strategy") or {} if isinstance(data, dict) else {}
    rec = dict(case=cid, genre=case["genre"], iter=i, model=MODEL, agent_cli=AGENT_CLI,
               granularity=GRANULARITY, ok=ok, mode=mode, wall=round(wall, 1), note=note,
               n_tasks=len(tasks), kinds=sorted({str(t.get("kind")) for t in tasks}),
               patterns=list(strategy.get("patterns") or []),
               granularity_resolved=strategy.get("granularity"),
               # goal は全文で残す（チェッカーを直したとき台帳から再判定できるように）。
               graph=[{"id": t.get("id"), "kind": t.get("kind"), "deps": t.get("deps"),
                       "goal": str(t.get("goal") or "")} for t in tasks],
               stderr_tail=err.strip()[-300:])
    if engine.missing():
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  "
          f"{len(tasks)} ノード  {note[:70]}", flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


def selfcheck() -> int:
    """チェッカーを LLM 抜きで検証する（正解は通り、典型的な外し方は落ちる）。"""
    def g(tasks, patterns):
        return {"strategy": {"patterns": patterns}, "tasks": tasks}

    good = {
        "PL1": g([{"id": "a", "goal": "KIRBY-A 読込\n[out_of_scope] KIRBY-B の変換", "deps": [],
                   "kind": "work"},
                  {"id": "b", "goal": "[scope] 変換（KIRBY-A の出力を使う）\nKIRBY-B 変換",
                   "deps": ["a"], "kind": "work"},
                  {"id": "t_kirby_c", "goal": "[scope] 変換のテスト", "deps": ["b"], "kind": "work"}],
                 ["fan-out-and-synthesize"]),      # out_of_scope の言及・id だけのラベルも同定できる
        "PL2": g([{"id": "x", "goal": "LIB-X 調査", "deps": [], "kind": "work"},
                  {"id": "y", "goal": "LIB-Y 調査", "deps": [], "kind": "work"},
                  {"id": "z", "goal": "LIB-Z 調査", "deps": [], "kind": "work"},
                  {"id": "s", "goal": "比較表", "deps": ["x", "y", "z"], "kind": "synthesize"}],
                 ["fan-out-and-synthesize"]),
        "PL3": g([{"id": "s", "goal": "notes/ の md を列挙", "deps": [], "kind": "split"}],
                 ["map-reduce"]),
        "PL4": g([{"id": "t", "goal": "README のタイポ修正", "deps": [], "kind": "work"}],
                 ["fan-out-and-synthesize"]),
    }
    bad = {
        "PL1": [g([{"id": "a", "goal": "KIRBY-A", "deps": [], "kind": "work"},
                   {"id": "b", "goal": "KIRBY-B", "deps": [], "kind": "work"},
                   {"id": "c", "goal": "KIRBY-C", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"]),                       # 鎖が無い（全部並列）
                g([{"id": "a", "goal": "KIRBY-A と KIRBY-B", "deps": [], "kind": "work"},
                   {"id": "c", "goal": "KIRBY-C", "deps": ["a"], "kind": "work"}],
                  ["fan-out-and-synthesize"])],                      # ラベルを 1 タスクに混ぜた
        "PL2": [g([{"id": "x", "goal": "LIB-X", "deps": [], "kind": "work"},
                   {"id": "y", "goal": "LIB-Y", "deps": ["x"], "kind": "work"},
                   {"id": "z", "goal": "LIB-Z", "deps": ["y"], "kind": "work"},
                   {"id": "s", "goal": "表", "deps": ["z"], "kind": "synthesize"}],
                  ["fan-out-and-synthesize"]),                       # 直列にした
                g([{"id": "x", "goal": "LIB-X", "deps": [], "kind": "work"},
                   {"id": "y", "goal": "LIB-Y", "deps": [], "kind": "work"},
                   {"id": "z", "goal": "LIB-Z", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"])],                      # 統合が無い
        "PL3": [g([{"id": "s", "goal": "列挙", "deps": [], "kind": "split"},
                   {"id": "m", "goal": "抽出", "deps": ["s"], "kind": "work"},
                   {"id": "r", "goal": "集約", "deps": ["m"], "kind": "reduce"}],
                  ["map-reduce"]),                                   # 静的チェーン
                g([{"id": "w1", "goal": "ITEM-01", "deps": [], "kind": "work"},
                   {"id": "w2", "goal": "ITEM-02", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"])],                      # split が無い
        "PL4": [g([{"id": "a", "goal": "読む", "deps": [], "kind": "work"},
                   {"id": "b", "goal": "特定", "deps": ["a"], "kind": "generate"},
                   {"id": "c", "goal": "直す", "deps": ["b"], "kind": "work"},
                   {"id": "d", "goal": "確認", "deps": ["c"], "kind": "verify"}],
                  ["fan-out-and-synthesize"]),                       # 過分解（実測 e4b の形）
                g([{"id": "a", "goal": "分類", "deps": [], "kind": "classify"},
                   {"id": "b", "goal": "直す", "deps": ["a"], "kind": "work"}],
                  ["classify-and-act"])],                            # 余計な判定ノード
    }
    contract_bad = [
        g([{"id": "a", "goal": "x", "deps": ["zz"], "kind": "work"}], []),      # 無い deps
        g([{"id": "a", "goal": "x", "deps": ["b"], "kind": "work"},
           {"id": "b", "goal": "y", "deps": ["a"], "kind": "work"}], []),        # 循環
        g([{"id": "a", "goal": "x", "deps": [], "kind": "work"}], ["panel"]),    # カタログ外
        g([{"id": "a", "goal": "x", "deps": [], "kind": "work"},
           {"id": "a", "goal": "y", "deps": [], "kind": "work"}], []),           # id 重複
    ]
    failures = 0
    for cid, case in CASES.items():
        assert not invariants(good[cid]), (cid, invariants(good[cid]))
        ok, note = case["check"](good[cid])
        if not ok:
            print(f"  NG {cid}: 正解が落ちた: {note}"); failures += 1
        for k, sample in enumerate(bad[cid]):
            ok, note = case["check"](sample)
            if ok:
                print(f"  NG {cid} bad#{k}: 外し方が通った"); failures += 1
    for k, sample in enumerate(contract_bad):
        if not invariants(sample):
            print(f"  NG contract#{k}: 契約違反が通った"); failures += 1
    print("selfcheck:", "OK" if not failures else f"{failures} 件 NG")
    return int(bool(failures))


def main() -> None:
    global MODEL, AGENT_CLI, WALL_LIMIT, GRANULARITY
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-cli", default=AGENT_CLI,
                    help="planner に使う CLI 定義名（本番の planner 変種は ollama-json）")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--granularity", default=GRANULARITY,
                    choices=("auto", "coarse", "fine", "finest"))
    ap.add_argument("--selfcheck", action="store_true",
                    help="チェッカーの自己検証だけを行う（LLM を呼ばない）")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, AGENT_CLI, WALL_LIMIT, GRANULARITY = (args.model, args.agent_cli, args.wall,
                                                 args.granularity)
    if not PLAN_SCRIPT.exists():
        raise SystemExit(f"planner が見つかりません: {PLAN_SCRIPT}（FLOW_PLANNER_SCRIPT で指定）")
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    cids = [c.strip() for c in args.cases.split(",") if c.strip()]
    print(f"model={MODEL} agent_cli={AGENT_CLI} planner={PLAN_SCRIPT}")
    print(f"wall_limit={WALL_LIMIT:.0f}s granularity={GRANULARITY} cases={cids} "
          f"repeat={args.repeat}\n")
    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("\n=== 正解率（決定的チェッカー）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        if not r:
            continue
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        print(f"  {cid} ({CASES[cid]['genre']}): {ok}/{n}  中央値 {walls[len(walls)//2]:.0f}s  "
              f"様式 {sorted(set(x['mode'] for x in r))}")
    print(f"  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    main()
