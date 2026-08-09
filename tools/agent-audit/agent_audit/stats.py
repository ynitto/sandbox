"""stats — 実行品質の決定的集計（設計 §5.2。LLM 不使用）。"""
from __future__ import annotations

import json

from .configfile import resolve_audit_dir
from .scrub import scrub_obj
from .store import Store
from .collect import correlate
from .usage import _period_floor, _rate_for, _rates, load_period_records
from .util import parse_iso


def aggregate_stats(store: Store, period: str) -> dict:
    _ledger, _session, runs = load_period_records(store, period)
    by_tool: "dict[str, dict]" = {}
    by_decision: "dict[str, dict]" = {}
    for rec in runs:
        tool = rec.get("tool") or "(不明)"
        b = by_tool.setdefault(tool, {
            "tool": tool, "runs": 0, "status": {}, "error_class": {},
            "retries": 0, "verify_pass": 0, "verify_fail": 0, "escalations": 0})
        b["runs"] += 1
        status = str(rec.get("status") or "(なし)")
        b["status"][status] = b["status"].get(status, 0) + 1
        ec = str(rec.get("error_class") or "")
        if ec:
            b["error_class"][ec] = b["error_class"].get(ec, 0) + 1
        try:
            b["retries"] += int(rec.get("retries") or 0)
        except (TypeError, ValueError):
            pass
        verify = str(rec.get("verify") or "")
        if verify == "pass":
            b["verify_pass"] += 1
        elif verify == "fail":
            b["verify_fail"] += 1
        try:
            b["escalations"] += int(rec.get("escalations") or 0)
        except (TypeError, ValueError):
            pass
        for item in rec.get("decision_comparisons") or []:
            if not isinstance(item, dict) or not item.get("decision"):
                continue
            name = str(item["decision"])
            d = by_decision.setdefault(name, {"decision": name, "samples": 0, "matches": 0})
            d["samples"] += 1
            d["matches"] += int(item.get("agree") is True)
    decisions = []
    for d in sorted(by_decision.values(), key=lambda d: d["decision"]):
        decisions.append({**d, "agreement_rate": round(d["matches"] / d["samples"], 4)})
    return {"period": period, "tools": sorted(by_tool.values(), key=lambda b: b["tool"]),
            "decisions": decisions}


def cmd_stats(args) -> int:
    store = Store(resolve_audit_dir(args))
    period = getattr(args, "period", None) or "month"
    data = aggregate_stats(store, period)
    if getattr(args, "json", False):
        print(json.dumps(scrub_obj(data), ensure_ascii=False, indent=1))
        return 0
    print(f"実行品質集計（period={period}）")
    if not data["tools"]:
        print("（run レコードがありません。まず agent-audit collect を実行してください）")
        return 0
    for b in data["tools"]:
        print(f"\n{b['tool']}: {b['runs']} run")
        for status, n in sorted(b["status"].items()):
            print(f"  status {status}: {n}")
        for ec, n in sorted(b["error_class"].items()):
            print(f"  error [{ec}]: {n}")
        if b["retries"]:
            print(f"  リトライ合計: {b['retries']}")
        if b["verify_pass"] or b["verify_fail"]:
            print(f"  verify: pass={b['verify_pass']} fail={b['verify_fail']}")
        if b["escalations"]:
            print(f"  needs エスカレーション: {b['escalations']}")
    if data["decisions"]:
        print("\nLLM判断と決定的ルールの一致率（計測のみ）")
        for d in data["decisions"]:
            print(f"  {d['decision']}: {d['agreement_rate']:.1%} "
                  f"({d['matches']}/{d['samples']})")
    return 0


def _method_key(rec: dict) -> "tuple[str, ...]":
    return tuple(sorted({str(v) for v in rec.get("methods") or [] if str(v)}))


def aggregate_ratings(args, store: Store, period: str, *, by_methods: bool = False) -> "list[dict]":
    """仕事種別×モデルで、台帳の消費と flow の**ノード単位**の結末を結合する。

    品質の入力は result レコードの `status`（done / failed）で、run 全体の統一 verify
    （`run_verify`）ではない。統一 verify は run に 1 回・node id 無しで出るので、それを
    ノードへ配ると planner とワーカーが別モデルでも同じ判定を貰い、モデル別の PASS 率が
    測れない。ここが測れないと F4 の格付けは印象に戻る。"""
    ledger, sessions, _runs = load_period_records(store, period)
    links = correlate(ledger, sessions, slack_sec=float(getattr(args, "join_slack_sec", 120.0)))
    sess_by_id = {s["id"]: s for s in sessions}
    default_rate, per_cli = _rates(args)
    groups: "dict[tuple, dict]" = {}

    def bucket(purpose: str, model: str, methods=()) -> dict:
        key = (purpose or "(なし)", model or "(不明)", tuple(methods) if by_methods else ())
        row = {
            "purpose": key[0], "model": key[1], "usage_runs": 0, "total_tokens": 0.0,
            "outcome_runs": 0, "outcome_ok": 0,
        }
        if by_methods:
            row["methods"] = list(key[2])
        return groups.setdefault(key, row)

    for led in ledger:
        purpose = str(led.get("purpose") or led.get("ref") or "")
        model = str(led.get("model") or led.get("agent_cli") or "")
        b = bucket(purpose, model, _method_key(led))
        b["usage_runs"] += 1
        sess = sess_by_id.get(links.get(led["id"], ""))
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
        if tin is not None or tout is not None:
            b["total_tokens"] += max(0, int(tin or 0)) + max(0, int(tout or 0))
        else:
            rate = _rate_for(str(led.get("agent_cli") or ""), str(led.get("model") or ""),
                             default_rate, per_cli)
            try:
                b["total_tokens"] += max(0.0, float(led.get("seconds") or 0.0)) * rate
            except (TypeError, ValueError):
                pass

    floor = _period_floor(period)
    for rec in store.iter_records(since_epoch=floor):
        if rec.get("kind") != "result":
            continue
        ts = parse_iso(rec.get("ts"))
        if floor and (ts is None or ts < floor):
            continue
        outcome = str(rec.get("status") or "")
        if outcome not in ("done", "failed"):
            continue
        b = bucket(str(rec.get("purpose") or ""),
                   str(rec.get("model") or rec.get("agent_cli") or ""), _method_key(rec))
        b["outcome_runs"] += 1
        b["outcome_ok"] += int(outcome == "done")

    rows = []
    for b in groups.values():
        usage_runs = b["usage_runs"]
        outcome_runs = b["outcome_runs"]
        rows.append({
            **b,
            "average_tokens": round(b["total_tokens"] / usage_runs, 1) if usage_runs else None,
            "pass_rate": round(b["outcome_ok"] / outcome_runs, 4) if outcome_runs else None,
        })
    out = []
    axes = sorted({(r["purpose"], tuple(r.get("methods") or [])) for r in rows})
    for purpose, methods in axes:
        ranked = sorted(
            (r for r in rows if r["purpose"] == purpose
             and tuple(r.get("methods") or []) == methods),
            key=lambda r: (-(r["pass_rate"] if r["pass_rate"] is not None else -1),
                           r["average_tokens"] if r["average_tokens"] is not None else float("inf"),
                           r["model"]),
        )
        for rank, row in enumerate(ranked, 1):
            out.append({"rank": rank, **row})
    return out


def cmd_ratings(args) -> int:
    store = Store(resolve_audit_dir(args))
    period = getattr(args, "period", None) or "month"
    by_methods = bool(getattr(args, "methods", False))
    rows = aggregate_ratings(args, store, period, by_methods=by_methods)
    data = {"period": period, "by_methods": by_methods, "rows": rows}
    if getattr(args, "json", False):
        print(json.dumps(scrub_obj(data), ensure_ascii=False, indent=1))
        return 0
    print(f"品質×消費格付け（period={period}）")
    method_col = " methods" if by_methods else ""
    print(f"{'#':>2} {'purpose':<16} {'model':<24} {'PASS':>7} {'avg tokens':>12} {'n':>5}{method_col}")
    for r in rows:
        pass_text = f"{r['pass_rate']:.1%}" if r["pass_rate"] is not None else "-"
        avg_text = f"{r['average_tokens']:.1f}" if r["average_tokens"] is not None else "-"
        suffix = " " + ",".join(r.get("methods") or []) if by_methods else ""
        print(f"{r['rank']:>2} {r['purpose']:<16} {r['model']:<24} {pass_text:>7} "
              f"{avg_text:>12} {r['outcome_runs']:>5}{suffix}")
    if not rows:
        print("（消費または結果レコードがありません）")
    return 0


def aggregate_trials(args, store: Store, period: str) -> dict:
    """trial variant ごとの PASS 率と平均 token、baseline 差分を決定的に比較する。"""
    ledger, sessions, _runs = load_period_records(store, period)
    links = correlate(ledger, sessions, slack_sec=float(getattr(args, "join_slack_sec", 120.0)))
    sess_by_id = {s["id"]: s for s in sessions}
    default_rate, per_cli = _rates(args)
    groups: "dict[tuple, dict]" = {}

    def bucket(rec: dict) -> "dict | None":
        trial = rec.get("trial")
        if not isinstance(trial, dict) or not trial.get("id") or not trial.get("variant"):
            return None
        key = (str(trial["id"]), str(trial["variant"]),
               str(rec.get("purpose") or "(なし)"),
               str(rec.get("model") or rec.get("agent_cli") or "(不明)"), _method_key(rec))
        return groups.setdefault(key, {
            "trial": key[0], "variant": key[1], "purpose": key[2], "model": key[3],
            "methods": list(key[4]), "usage_runs": 0, "total_tokens": 0.0,
            "outcome_runs": 0, "outcome_ok": 0,
        })

    for led in ledger:
        b = bucket(led)
        if not b:
            continue
        b["usage_runs"] += 1
        sess = sess_by_id.get(links.get(led["id"], ""))
        tin, tout = led.get("tokens_in"), led.get("tokens_out")
        if sess and sess.get("measured"):
            tin = sess.get("tokens_in") if sess.get("tokens_in") is not None else tin
            tout = sess.get("tokens_out") if sess.get("tokens_out") is not None else tout
        if tin is not None or tout is not None:
            b["total_tokens"] += max(0, int(tin or 0)) + max(0, int(tout or 0))
        else:
            rate = _rate_for(str(led.get("agent_cli") or ""), str(led.get("model") or ""),
                             default_rate, per_cli)
            try:
                b["total_tokens"] += max(0.0, float(led.get("seconds") or 0.0)) * rate
            except (TypeError, ValueError):
                pass

    floor = _period_floor(period)
    for rec in store.iter_records(since_epoch=floor):
        if rec.get("kind") != "result":
            continue
        ts = parse_iso(rec.get("ts"))
        if floor and (ts is None or ts < floor):
            continue
        status = str(rec.get("status") or "")
        if status not in ("done", "failed"):
            continue
        b = bucket(rec)
        if b:
            b["outcome_runs"] += 1
            b["outcome_ok"] += int(status == "done")

    rows = []
    for b in groups.values():
        rows.append({**b,
                     "average_tokens": (round(b["total_tokens"] / b["usage_runs"], 1)
                                        if b["usage_runs"] else None),
                     "pass_rate": (round(b["outcome_ok"] / b["outcome_runs"], 4)
                                   if b["outcome_runs"] else None)})
    rows.sort(key=lambda r: (r["trial"], r["purpose"], r["model"], r["variant"], r["methods"]))

    try:   # 設定未解決（None）や不正値でも既定へ落ちる。判定を止めないため
        floor_n = max(1, int(getattr(args, "trial_min_outcomes", None) or 3))
    except (TypeError, ValueError):
        floor_n = 3
    comparisons = []
    axes = sorted({(r["trial"], r["purpose"], r["model"]) for r in rows})
    for trial, purpose, model in axes:
        variants = [r for r in rows if (r["trial"], r["purpose"], r["model"]) ==
                    (trial, purpose, model)]
        row = {"trial": trial, "purpose": purpose, "model": model,
               "baseline": "", "candidate": "", "pass_rate_delta": None,
               "average_tokens_delta": None, "min_outcome_runs": min(
                   (v["outcome_runs"] for v in variants), default=0)}
        names = {v["variant"] for v in variants}
        base_names = names & {"baseline", "control"}
        # **落とすときも行は出す。** 黙って比較を消すと、出力の「証跡がありません」が
        # 「データが無い」と「捨てた」のどちらなのか読み手に区別できない。
        if len(variants) != 2:
            comparisons.append({**row, "verdict": "ambiguous",
                                "reason": f"variant 行が {len(variants)} 件"
                                          "（同じ trial で手法セットが分岐している）"})
            continue
        if not base_names:
            # どちらも baseline / control でないと、差分の符号が variant 名の辞書順で
            # 決まってしまう。基準が決まらない比較は数字を出さない。
            comparisons.append({**row, "baseline": "", "candidate": "",
                                "verdict": "no-baseline",
                                "reason": "variant に baseline / control が無い"})
            continue
        variants.sort(key=lambda r: (r["variant"] not in ("baseline", "control"), r["variant"]))
        base, candidate = variants
        row.update({"baseline": base["variant"], "candidate": candidate["variant"]})
        if any(v["pass_rate"] is None or v["average_tokens"] is None for v in variants):
            comparisons.append({**row, "verdict": "insufficient",
                                "reason": "片側に消費または結果のサンプルが無い"})
            continue
        if row["min_outcome_runs"] < floor_n:
            # n=1 でも pass_delta は ±1.0 になる。**標本の少なさは差の大きさに化ける**ので、
            # 下限を割る比較は判定語を出さない（S12 の昇格ゲートと同じ規律）。
            comparisons.append({**row, "verdict": "insufficient",
                                "reason": f"結果サンプルが {row['min_outcome_runs']} 件"
                                          f"（下限 {floor_n} 件）"})
            continue
        pass_delta = round(candidate["pass_rate"] - base["pass_rate"], 4)
        token_delta = round(candidate["average_tokens"] - base["average_tokens"], 1)
        if pass_delta < 0:
            verdict = "harmful"
        elif pass_delta > 0:
            # 品質が上がってもトークンが増えていれば、資源効率の判断としては人が決める。
            verdict = "effective" if token_delta <= 0 else "mixed"
        elif token_delta < 0:
            verdict = "effective"
        else:
            verdict = "ineffective"
        comparisons.append({**row, "pass_rate_delta": pass_delta,
                            "average_tokens_delta": token_delta, "verdict": verdict})
    return {"period": period, "trial_min_outcomes": floor_n,
            "rows": rows, "comparisons": comparisons}


def cmd_trials(args) -> int:
    data = aggregate_trials(args, Store(resolve_audit_dir(args)),
                            getattr(args, "period", None) or "month")
    if getattr(args, "json", False):
        print(json.dumps(scrub_obj(data), ensure_ascii=False, indent=1))
        return 0
    print(f"手法 trial 比較（period={data['period']}・結果サンプル下限 "
          f"{data['trial_min_outcomes']} 件）")
    for row in data["comparisons"]:
        head = f"{row['trial']} {row['purpose']}/{row['model']}: "
        if row["pass_rate_delta"] is None:
            print(f"{head}{row['verdict']} — {row.get('reason', '')}")
            continue
        print(f"{head}{row['candidate']} vs {row['baseline']} = {row['verdict']} "
              f"(PASS {row['pass_rate_delta']}, tokens {row['average_tokens_delta']}, "
              f"n={row['min_outcome_runs']})")
    if not data["comparisons"]:
        print("（trial を記録した証跡がありません）")
    return 0
