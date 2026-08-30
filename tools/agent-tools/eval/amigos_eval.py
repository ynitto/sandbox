#!/usr/bin/env python3
"""agent-amigos の LLM 呼び出し 5 面を測る（`coverage.json` の missing を埋める）。

**本番の関数をそのまま走らせる。** agent-amigos の LLM 呼び出しは
`agentcli.run_agent` の単一チョークポイントを通るので、そこを差し替えれば
プロンプト組み立て・受け方（`extract_json` と契約検査）・失敗時の扱いまで本番のコードが回る。
ハーネスがやるのは argv を本番と同じに組んで実行することだけで、判定は**本番の戻り値**に対して
行う。

測るのは決定的に判定できるものだけ——**契約**（封筒・必須キー・型）と**捏造**（材料に無い
role / 成果物を作らないか）、そして合議の**手続き**（打ち切り・整合）。文章の質は測らない。

使い方: python3 amigos_eval.py [--model gemma4:e4b] [--repeat 5] [--cases TB1,CO1] [--selfcheck]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools/agent-amigos"))
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))

try:
    from agent_amigos import agentcli as am_agentcli  # noqa: E402
    from agent_amigos import ownerops, runner, teambuilding  # noqa: E402
    from agent_amigos.bus import Bus  # noqa: E402
    from agent_amigos.mission import (current_round, load_mission, load_roles,  # noqa: E402
                                      post_mission)
    from agent_amigos.util import write_json_atomic  # noqa: E402
except Exception as exc:  # noqa: BLE001 — 本番が読めない木では測らない
    raise SystemExit(f"agent_amigos を import できません: {exc}")

LEDGER_DIR = pathlib.Path(os.environ.get("AMIGOS_EVAL_DIR", "/tmp/agent-amigos-eval"))
MODEL = "gemma4:e4b"
BASE_CLI = "ollama"
WALL_LIMIT = 600.0

_LAST_CALLS: "list[dict]" = []

# ------------------------------------------------------------------ 素材

GOAL = "run ログから日次のトークン消費レポートを作る仕組みを用意する（収集・集計・出力の 3 段）。"

DESIGN_DOC = """# design doc: 日次トークンレポート

## 目的
run ログから日次のトークン消費を集計し、Markdown のレポートを出す。

## 受入基準
- `reports/digest.md` に日次の表がある
- 収集・集計・出力の 3 段がそれぞれテストで確認できる
- 追加の依存を増やさない（標準ライブラリのみ）
"""

ROLES = {
    "collector": {"id": "collector", "title": "収集担当",
                  "mission": "run ログを読み込む", "required": True, "approver": False,
                  "seat_count": 1},
    "aggregator": {"id": "aggregator", "title": "集計担当",
                   "mission": "日次のトークン数を集計する", "required": True,
                   "approver": False, "seat_count": 1},
    "integrator": {"id": "integrator", "title": "統合担当",
                   "mission": "成果を 1 つの納品物へまとめる", "required": True,
                   "approver": True, "seat_count": 1},
}
ROLE_IDS = set(ROLES)

BRIEF = {"goal": GOAL,
         "constraints": ["追加の依存を増やさない", "3 段（収集・集計・出力）で構成する"],
         "deliverables": ["reports/digest.md"]}

MANIFEST = {"mission": "digest", "artifacts": ["reports/digest.md", "src/aggregate.py"],
            "note": "出力段は未実装のまま提出"}


MISSION_ID = "am-eval"


def mission(cwd: str):
    """本番の投函口（`post_mission`）でミッションを立てる（レイアウトを写さない）。

    実ホームの agent-control / node-budget / 手番マーカーは一時ディレクトリへ隔離する
    ——control に workloads.amigos の上書きがあると、宣言した base CLI が別の CLI へ
    差し替わって「測ったつもりの起動形」が変わる（amigos のテストが同じ理由で隔離している）。
    """
    root = pathlib.Path(cwd)
    for key, sub in (("AGENT_CONTROL_DIR", "control"), ("AGENT_BUDGET_DIR", "node-budget"),
                     ("AGENT_AMIGOS_TURNS_DIR", "turns")):
        os.environ[key] = str(root / sub)
    design = root / "design.md"
    design.write_text(DESIGN_DOC, encoding="utf-8")
    roles_path = root / "roles.json"
    roles_path.write_text(json.dumps(
        {"mission": {"title": "日次トークンレポート", "goal": GOAL, "staffing_timeout": 0,
                     "convergence": {"done_when": "reviewer-approved", "quiescence_turns": 5},
                     "budget": {"execution_minutes": 10}},
         "roles": [{"id": r["id"], "mission": r["mission"], "approver": r.get("approver", False)}
                   for r in ROLES.values()]}, ensure_ascii=False), encoding="utf-8")
    bus = Bus(str(root / "bus"))
    post_mission(bus, str(design), str(roles_path), "owner-node", MISSION_ID)
    mp = bus.mission(MISSION_ID)
    # 納品物: 受入基準の 3 つ目（出力段）を満たしていない状態を作る
    art = pathlib.Path(mp.artifacts_dir("integrator"))
    art.mkdir(parents=True, exist_ok=True)
    (art / "aggregate.py").write_text("def daily(rows):\n    return {}\n", encoding="utf-8")
    return bus, mp


# ------------------------------------------------------------------ 本番を走らせる土台


def _agent_runner(cwd: str):
    """本番の `agentcli.run_agent(prompt, cli, model=None, timeout=None)` と同じ形。"""
    def run(prompt: str, cli: str = "", model: "str | None" = None, timeout=None) -> str:
        argv, _src = cmd_for()
        rc, out, err, wall = call(prompt, argv, cwd)
        _LAST_CALLS.append({"rc": rc, "wall": round(wall, 1), "prompt_chars": len(prompt),
                            "out_chars": len(out), "err": (err or "").strip()[-200:],
                            # 本番が握った失敗の中身を後から読めるようにする（契約違反の
                            # 「何を返したか」が分からないと、モデルの外し方を直せない）
                            "tail": (out or "").strip()[-300:]})
        if rc != 0 or not out.strip():
            raise RuntimeError(err.strip()[-160:] or f"rc={rc}")
        return out
    return run


def cmd_for() -> "tuple[list[str], str]":
    """本番がこの呼び出しで起こす argv。amigos は用途別の変種を宣言していないので base。"""
    return engine.production_argv(BASE_CLI, MODEL, False)


def call(prompt: str, argv: "list[str]", cwd: str) -> "tuple[int, str, str, float]":
    started = time.monotonic()
    try:
        p = engine.run_process(argv, input=prompt, capture_output=True, text=True,
                               timeout=WALL_LIMIT, cwd=cwd,
                               env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", "TIMEOUT"
    return rc, out, err, time.monotonic() - started


# ------------------------------------------------------------------ チェッカー


def check_team(result):
    """チーム編成。本番は `(mission, roles, meta)` を返し、**返す前に `normalize_mission` で
    妥当性検証まで済ませる**（不正なら例外＝ここには来ない）。

    だからここで見るのは、機械検査を通ったうえで協働として成立しているか——
    (1) role が 2 件以上か (2) 各 role に id と mission があるか
    (3) **承認者（approver）が 1 人以上いるか**（居ないと収束条件が満たせない）。
    """
    _mission, roles, meta = result
    if not roles:
        return False, "role が 0 件"
    if len(roles) < 2:
        return False, f"role が {len(roles)} 件（協働にならない）"
    for r in roles:
        rid = str(r.get("id") or "")
        if not rid or any(c.isspace() for c in rid):
            return False, f"id が機械可読でない: {rid!r}"
        if not str(r.get("mission") or "").strip():
            return False, f"{rid}: mission が空"
    if not any(r.get("approver") for r in roles):
        return False, "承認者（approver）が 1 人もいない（収束条件を満たせない）"
    return True, f"{len(roles)} role・pattern={meta.get('chosen_pattern')}"


def check_conductor(data, want_empty: bool):
    """コンダクタ。本番は `{"add": [...], "prune": [...], "reason": ...}` を受ける。

    整っているチームでは**何もしない**のが正解（本番が「変更が不要なら空」と明示）。
    prune では integrator を消さないことも見る（本番の指針）。
    """
    add = data.get("add") or []
    prune = [str(p) for p in (data.get("prune") or [])]
    if "integrator" in prune:
        return False, "integrator を prune した（本番は消さないと明示）"
    stray = [p for p in prune if p not in ROLE_IDS]
    if stray:
        return False, f"存在しない role を prune: {stray[0]}"
    if want_empty and (add or prune):
        return False, f"変更不要なのに add={len(add)} prune={prune}"
    if not want_empty and not (add or prune):
        return False, "不足があるのに add も prune も空"
    return True, f"add={len(add)} prune={prune}"


def check_acceptance(outcome: str, want: str):
    """受入判定。本番の 1 ターンの戻り値（accepted / rejected / escalated / skipped）を見る。

    `skipped` は判定に至っていない（round 不一致・manifest 不在）＝**ハーネスの配線ミス**なので、
    モデルの失点と混ぜずに別の様式として落とす。
    """
    if outcome == "skipped":
        return False, "skipped（判定に到達していない＝配線の問題）"
    if outcome != want:
        return False, f"{outcome}（期待 {want}）"
    return True, outcome


def check_debate(text: str, peers: "list[str]"):
    """討議。出力契約は**本文のみ**（JSON もコードフェンスも不要）。

    見るのは (1) 本文が空でない (2) 契約どおり JSON 封筒で返していない
    (3) 他者の主張へ触れている（前ラウンドを踏まえる、が本番の依頼文）。
    """
    body = (text or "").strip()
    if not body:
        return False, "本文が空"
    if body.startswith("{") or body.startswith("```json"):
        return False, "本文のみの契約に反して JSON で返した"
    hit = [p for p in peers if p in body]
    if not hit:
        return False, f"他者の主張に触れていない（{peers} のどれにも触れず）"
    return True, f"{len(body)} 字・{len(hit)} 件の主張へ言及"


# 本番の適用側（`AmigoRunner._apply`）が解釈する種別。**識別子は `kind`** で、`type` は
# `send` の中のメッセージ種別である（写しではなく実装から拾った綴り——ここを取り違えると、
# 正しい封筒を「契約違反」と数える。実測 2026-08-30 に 0/5 を出した原因がこれだった）。
ACTION_KINDS = {"send", "write_artifact", "update_status", "declare_done"}


def check_actions(actions, allowed: "set[str]"):
    """役割の 1 ターン。本番は `{"actions": [...]}` の封筒を要求する。

    見るのは (1) 封筒が取れたか (2) 各アクションの `kind` が本番の解釈できる種別か
    (3) **宛先が実在する role か**（捏造した相手へ送ると板に届かない）。
    """
    if not isinstance(actions, list) or not actions:
        return False, "アクションが 0 件"
    for act in actions:
        kind = str(act.get("kind") or "").strip() if isinstance(act, dict) else ""
        if not kind:
            return False, f"kind の無いアクション: {str(act)[:40]}"
        if kind not in ACTION_KINDS:
            return False, f"本番が解釈できない kind: {kind}"
        to = str(act.get("to") or "").strip()
        if to and to not in allowed:
            return False, f"存在しない宛先: {to}"
    return True, f"{len(actions)} アクション（{', '.join(sorted({str(a.get('kind')) for a in actions}))}）"


# ------------------------------------------------------------------ ケース定義


def _team_driver(cwd: str):
    return teambuilding.build_team(BRIEF, cli=BASE_CLI, model=MODEL)


def _conductor_driver(cwd: str):
    _bus, mp = mission(cwd)
    return ownerops._ask_conductor(mp, load_mission(mp), load_roles(mp), "owner-node", BASE_CLI)


def _acceptance_driver(cwd: str):
    """受入判定は本番の 1 ターン（`acceptance_turn`）をそのまま回す。

    納品 MANIFEST は**本番が読む位置**へ本番の書き込み関数で置く。判定の戻り値
    （accepted / rejected / …）が本番の決定そのものなので、それを採点する。
    """
    bus, mp = mission(cwd)
    write_json_atomic(mp.manifest(), {**MANIFEST, "round": current_round(mp)})
    return ownerops.acceptance_turn(bus, mp, load_mission(mp), "owner-node",
                                    agent_cli=BASE_CLI)


# 討議の相手の主張。判定は**綴りの揺れに強い語**で行う（言い換えは討議として正常なので、
# 逐語一致を求めると文章の書き方を測ることになる）。
DEBATE_PEERS = {"collector": "収集段のログ形式が揃っていない。タイムスタンプの粒度が混在している。",
                "integrator": "集計は月またぎで壊れる。月境界のテストが無い。"}
DEBATE_TOKENS = ["ログ形式", "月またぎ", "月境界", "タイムスタンプ"]


def _runner_for(cwd: str):
    bus, mp = mission(cwd)
    return runner.AmigoRunner(bus, MISSION_ID, "aggregator", "owner-node",
                              agent_cli=BASE_CLI, model=MODEL), mp


def _debate_driver(cwd: str):
    r, mp = _runner_for(cwd)
    text, _secs, _raw = r._llm_debate(load_mission(mp), load_roles(mp)["aggregator"], 1,
                                      DEBATE_PEERS, BASE_CLI, MODEL)
    return text


def _actions_driver(cwd: str):
    r, mp = _runner_for(cwd)
    actions, _secs, _text = r._llm_actions(load_mission(mp), load_roles(mp),
                                           load_roles(mp)["aggregator"], {}, [], 1, False,
                                           BASE_CLI, MODEL)
    return actions


CASES = {
    "TB1": dict(purpose="team-builder", expect="2 role 以上・契約充足",
                driver=_team_driver, check=check_team),
    "CO1": dict(purpose="conductor", expect="整ったチームでは無変更",
                driver=_conductor_driver, check=lambda d: check_conductor(d, want_empty=True)),
    "AC1": dict(purpose="acceptance", expect="未達（出力段が無い）で rejected",
                driver=_acceptance_driver, check=lambda r: check_acceptance(r, "rejected")),
    "DB1": dict(purpose="debate", expect="本文のみ・他者の主張へ言及",
                driver=_debate_driver,
                check=lambda t: check_debate(t, DEBATE_TOKENS)),
    "RA1": dict(purpose="role-actions", expect="封筒充足・宛先は実在する role",
                driver=_actions_driver, check=lambda a: check_actions(a, ROLE_IDS)),
}

# ------------------------------------------------------------------ 実行


def workdir_for(cid: str, i: int) -> str:
    root = LEDGER_DIR / "work" / f"{cid}-{i}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    cwd = workdir_for(cid, i)
    _LAST_CALLS.clear()
    original = am_agentcli.run_agent
    am_agentcli.run_agent = _agent_runner(cwd)
    try:
        result = case["driver"](cwd)
    except Exception as exc:  # noqa: BLE001 — 本番も握る失敗
        ok, note, mode, result = False, f"{type(exc).__name__}: {exc}"[:140], "cli_error", None
    else:
        ok, note = case["check"](result)
        mode = "correct" if ok else "wrong"
    finally:
        am_agentcli.run_agent = original
    wall = sum(c["wall"] for c in _LAST_CALLS)
    failed = [c for c in _LAST_CALLS if c["rc"] != 0 or c["out_chars"] == 0]
    if failed and not ok:
        note = f"{note}｜CLI 失敗 {len(failed)}/{len(_LAST_CALLS)}: {failed[0]['err'][:70]}"
    if not _LAST_CALLS:
        ok, note, mode = False, "本番が LLM を 1 回も呼ばなかった", "no_call"
    rec = dict(case=cid, purpose=case["purpose"], iter=i, model=MODEL, ok=ok, mode=mode,
               wall=round(wall, 1), note=note, calls=len(_LAST_CALLS),
               prompt_chars=max((c["prompt_chars"] for c in _LAST_CALLS), default=0),
               out_chars=max((c["out_chars"] for c in _LAST_CALLS), default=0),
               answer=json.dumps(result, ensure_ascii=False, default=str)[:300],
               tail=(_LAST_CALLS[-1]["tail"] if _LAST_CALLS else ""))
    if engine.missing():
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  "
          f"呼び出し {len(_LAST_CALLS)}  {note[:60]}", flush=True)
    return rec


def selfcheck() -> int:
    good = {
        "TB1": ({}, [ROLES["collector"], ROLES["integrator"]], {"chosen_pattern": None}),
        "CO1": {"add": [], "prune": [], "reason": "編成は足りている"},
        "AC1": "rejected",
        "DB1": "月またぎで壊れるという指摘に同意する。月境界のテストを足したい。",
        "RA1": [{"kind": "update_status", "note": "集計を実装した"},
                {"kind": "send", "to": "integrator", "type": "info", "body": "集計できました"}],
    }
    bad = {
        "TB1": [({}, [], {}), ({}, [ROLES["collector"]], {}),
                ({}, [{"id": "bad id", "mission": "x", "approver": True},
                      ROLES["integrator"]], {}),
                ({}, [ROLES["collector"], ROLES["aggregator"]], {})],   # 承認者不在
        "CO1": [{"add": [{"id": "x"}], "prune": []},
                {"add": [], "prune": ["integrator"]},
                {"add": [], "prune": ["ghost"]}],
        "AC1": ["accepted", "skipped", "escalated"],
        "DB1": ['{"claim": "x"}', "", "特に意見はありません。"],
        "RA1": [[], [{"body": "kind が無い"}],
                [{"kind": "send", "to": "ghost", "body": "x"}],
                [{"kind": "teleport"}]],          # 本番が解釈できない種別
    }
    fails = []
    for cid, case in CASES.items():
        ok, note = case["check"](good[cid])
        if not ok:
            fails.append(f"{cid}: 正解が落ちた（{note}）")
        for n, wrong in enumerate(bad.get(cid, []), 1):
            ok, note = case["check"](wrong)
            if ok:
                fails.append(f"{cid}: 不正解 #{n} を通した（{note}）")
        print(f"  {cid:<4} {case['purpose']:<13} 本番の関数を実行  期待 {case['expect']}")
    for f in fails:
        print(f"  NG {f}")
    print(f"\nチェッカー自己診断: {'OK' if not fails else f'{len(fails)} 件 NG'}")
    return 1 if fails else 0


def main() -> None:
    global MODEL, BASE_CLI, WALL_LIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--base-cli", default=BASE_CLI)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, BASE_CLI, WALL_LIMIT = args.model, args.base_cli, args.wall
    cids = [c.strip() for c in args.cases.split(",") if c.strip() in CASES]

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    argv, source = cmd_for()
    for gap in engine.missing():
        print(f"   ⚠ この木にはエンジン機能が無い: {gap}（その分は測れていない）")
    print(f"model={MODEL}\n  amigos: {' '.join(argv)} （出所: {source}）"
          f"\nwall_limit={WALL_LIMIT:.0f}s cases={cids} repeat={args.repeat}\n")

    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("\n=== 正解一致率（構成的ラベル）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        same = collections.Counter(x["answer"] for x in r).most_common(1)[0][1]
        print(f"  {cid} ({CASES[cid]['purpose']}): {sum(1 for x in r if x['ok'])}/{len(r)}  "
              f"中央値 {sorted(x['wall'] for x in r)[len(r) // 2]:.0f}s  "
              f"自己一貫性 {same}/{len(r)}  様式 {sorted(set(x['mode'] for x in r))}")
    print(f"\n  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}\n\n台帳: {ledger}")


if __name__ == "__main__":
    main()
