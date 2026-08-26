"""本番 receipt → agent-candidate-qualifications の自動昇格・降格・期限切れ（E5、LLM 不使用）。

設計: docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md §6.2 /
実装計画 2026-08-15 E5。柱3 / C7・C9 — 候補の適格性は実測だけから機械的に更新し、
書き手は agent-audit の 1 つに保つ（**control.json は書かない**。適格性の変更は次回の
Compiler 評価で selection_policy へ反映される）。

入力は collect が拾った候補単位レコード:

- flow result（``execution_decision`` / ``operation_class`` 付き・E2）
- amigos turn event（同・E4）
- statemachine run（``statemachine-log``・E3）

集計キーは (agent_cli, model, operation_class)。**operation_class の無いレコードは
purpose（flow の node kind）へフォールバックし、それも無ければ数えない**——
「モデル名だけで広い適格を与えない」（§2.2）を集計側でも守る。

昇格・降格は evaluation profile（qualifications.json が所有）で決める:

- 観測窓 ``window_days`` 内のサンプルだけを数える
- ``forbidden_failure_modes`` を 1 件でも観測 → ``blocked``
- ``min_samples`` / ``min_pass_rate`` / ``max_timeout_rate`` を満たす → ``qualified``
  （``valid_for_days`` から ``valid_until`` を刻む）
- 実測はあるが基準未達 → ``trial``（Envelope 明示承認 run でだけ選べる）
- 期限切れ（``valid_until`` < now）で新実測なし → ``unknown`` へ降格

既存 doc の eval-archive seed（U1）は保持し、同じ (候補, 処理種別) に receipt 実測が
揃ったら receipt 側で置き換える（source フィールドで由来が追える）。
"""
from __future__ import annotations

import datetime as dt
import os
import re

from .configfile import agent_home_dir
from .util import now_iso, parse_iso, read_json, write_json_atomic

# 既定 evaluation profile（doc に無い operation_class 用）。保守的な側に倒す。
DEFAULT_PROFILE_ID = "receipt-default"
DEFAULT_PROFILE = {"min_samples": 5, "min_pass_rate": 0.8, "max_timeout_rate": 0.2,
                   "window_days": 30, "valid_for_days": 14}

_PASS_STATUSES = frozenset({"done"})
_FAIL_STATUSES = frozenset({"failed", "escalate", "cancelled"})
_TIMEOUT_MODES = frozenset({"timeout", "transient"})


def qualifications_file(args) -> str:
    return os.path.abspath(os.path.expanduser(
        getattr(args, "qualifications_file", "") or
        os.path.join(agent_home_dir(), "control", "qualifications.json")))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def collect_candidate_stats(store, *, now_epoch: float, window_days: int) -> dict:
    """(agent_cli, model, operation_class) → {samples, passed, timeouts, failure_modes}。

    対象は execution_decision を持つレコード（候補ベース実行の receipt）だけ。
    旧レコード（選択の根拠が残っていない）を混ぜると、legacy 経路の成否まで
    候補の実測として数えてしまう。
    """
    stats: dict = {}
    since = now_epoch - window_days * 86400
    for rec in store.iter_records(since_epoch=since):
        if not isinstance(rec.get("execution_decision"), dict):
            continue
        cli = str(rec.get("agent_cli") or rec["execution_decision"].get("agent_cli") or "")
        model = str(rec.get("model") or rec["execution_decision"].get("model") or "")
        op = str(rec.get("operation_class") or rec.get("purpose") or "")
        status = str(rec.get("status") or "")
        if not cli or not model or not op or status not in _PASS_STATUSES | _FAIL_STATUSES:
            continue
        entry = stats.setdefault((cli, model, op), {
            "samples": 0, "passed": 0, "timeouts": 0, "failure_modes": set()})
        entry["samples"] += 1
        if status in _PASS_STATUSES:
            entry["passed"] += 1
        else:
            mode = str(rec.get("error_class") or status)
            entry["failure_modes"].add(mode)
            if mode in _TIMEOUT_MODES:
                entry["timeouts"] += 1
    return stats


def _judge(entry: dict, profile: dict) -> str:
    forbidden = set(profile.get("forbidden_failure_modes") or [])
    if forbidden & entry["failure_modes"]:
        return "blocked"
    samples, passed = entry["samples"], entry["passed"]
    if (samples >= int(profile.get("min_samples", DEFAULT_PROFILE["min_samples"]))
            and passed / samples >= float(profile.get("min_pass_rate",
                                                      DEFAULT_PROFILE["min_pass_rate"]))
            and entry["timeouts"] / samples <= float(profile.get("max_timeout_rate",
                                                                 DEFAULT_PROFILE["max_timeout_rate"]))):
        return "qualified"
    return "trial"


def build_qualifications(existing: dict, stats: dict, *, now: dt.datetime) -> dict:
    """既存 doc + 実測 → 次の qualifications doc（revision +1・決定的）。"""
    existing = existing if isinstance(existing, dict) else {}
    profiles = dict(existing.get("evaluation_profiles") or {})
    profiles.setdefault(DEFAULT_PROFILE_ID, dict(DEFAULT_PROFILE))
    by_candidate: dict = {}
    for cand in existing.get("candidates") or []:
        if isinstance(cand, dict) and cand.get("agent_cli") and cand.get("model"):
            key = (str(cand["agent_cli"]), str(cand["model"]))
            by_candidate[key] = {"meta": {k: v for k, v in cand.items() if k != "qualifications"},
                                 "qualifications": dict(cand.get("qualifications") or {})}

    now_iso_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # 1) 期限切れ: qualified で valid_until を過ぎ、新実測が無いものは unknown へ。
    for (cli, model), cand in by_candidate.items():
        for op, qual in cand["qualifications"].items():
            if not isinstance(qual, dict) or qual.get("status") != "qualified":
                continue
            expires = parse_iso(str(qual.get("valid_until") or "")) or None
            if expires and expires < now.timestamp() and (cli, model, op) not in stats:
                qual["status"] = "unknown"
                qual["expired_at"] = now_iso_str

    # 2) 実測からの昇格・降格（receipt が同じセルの旧 seed を置き換える）。
    for (cli, model, op), entry in sorted(stats.items()):
        cand = by_candidate.setdefault((cli, model), {
            "meta": {"agent_cli": cli, "model": model}, "qualifications": {}})
        profile_id = op if op in profiles else DEFAULT_PROFILE_ID
        profile = profiles[profile_id]
        status = _judge(entry, profile)
        qual = {
            "qualification_id": f"{_slug(cli)}-{_slug(model)}-{_slug(op)}-receipt",
            "status": status,
            "samples": entry["samples"],
            "passed": entry["passed"],
            "timeouts": entry["timeouts"],
            "source": "receipt",
            "evaluation_profile_id": profile_id,
            "updated_at": now_iso_str,
        }
        if entry["failure_modes"]:
            qual["failure_modes"] = sorted(entry["failure_modes"])
        if status == "qualified":
            valid_days = int(profile.get("valid_for_days", DEFAULT_PROFILE["valid_for_days"]))
            qual["valid_until"] = (now + dt.timedelta(days=valid_days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        cand["qualifications"][op] = qual

    candidates = []
    for (cli, model) in sorted(by_candidate):
        cand = by_candidate[(cli, model)]
        candidates.append({**cand["meta"], "qualifications": cand["qualifications"]})
    return {
        "version": 1,
        "revision": int(existing.get("revision") or 0) + 1,
        "generated_at": now_iso_str,
        "evaluation_profiles": profiles,
        "candidates": candidates,
    }


def _recommendation_qualifications(document) -> "tuple[dict | None, str]":
    """推奨（agent-recommendation）から適格性ブロックを取り出す。

    推奨は**読み取り専用の配布物**で制御面ではない。ここは「その中身を検証して
    control へ置く」だけの入口であり、**生成はしない**——writer を agent-audit の
    1 つに保ったまま、GUI（agent-dashboard）から起動できるようにするための口である
    （2026-08-23 提案 §2.5 が「入れるなら起動口だけ」と残していた案）。
    """
    if not isinstance(document, dict):
        return None, "推奨がオブジェクトではありません"
    if document.get("version") != 1:
        return None, f"未知の推奨 version です: {document.get('version')!r}（推測で適用しない）"
    block = document.get("qualifications")
    if not isinstance(block, dict):
        return None, "推奨に qualifications ブロックがありません"
    return block, ""


def cmd_seed(args, store=None) -> dict:
    """seed サブコマンド本体。推奨の適格性ブロックを qualifications.json へ置く。

    dry-run 既定・``--apply`` で原子書換。qualify と同じ規律を通す:
    契約検査 → revision の楽観検査 → 原子書換。

    **既存を黙って踏み潰さない。** 置き場所に既に適格性があるときは、本番 receipt 由来の
    実績（source が eval-archive でないもの）を上書きしないよう ``--force`` を要求する
    ——seed は初期値であって、運用開始後の観測より新しい根拠ではない。
    """
    path = qualifications_file(args)
    # **空文字を abspath へ通さない。** `os.path.abspath("")` は現在地になるので、
    # 指定なしが「現在地の推奨を読む」に化けて、意図しないファイルを根拠にしうる。
    raw = str(getattr(args, "from_recommendation", "") or "").strip()
    if not raw:
        return {"applied": False, "error": "--from-recommendation が必要です", "file": path}
    source = os.path.abspath(os.path.expanduser(raw))
    document = read_json(source)
    if document is None:
        return {"applied": False, "error": f"推奨を読めません: {source}", "file": path}
    block, error = _recommendation_qualifications(document)
    if error:
        return {"applied": False, "error": error, "file": path, "source": source}

    from agentcore import executioncontract
    errors = executioncontract.qualifications_errors(block)
    if errors:
        return {"applied": False, "error": "; ".join(errors[:3]), "file": path, "source": source}

    existing = read_json(path) or {}
    measured = [
        {"agent_cli": cand.get("agent_cli"), "model": cand.get("model"),
         "operation_class": op}
        for cand in (existing.get("candidates") or []) if isinstance(cand, dict)
        for op, qual in (cand.get("qualifications") or {}).items()
        if isinstance(qual, dict) and qual.get("source") and qual.get("source") != "eval-archive"
    ]
    summary = {
        "file": path, "source": source,
        "revision": block.get("revision"),
        "candidates": len(block.get("candidates") or []),
        "replaces_measured": measured,
        "applied": False,
    }
    if measured and not getattr(args, "force", False):
        return {**summary,
                "error": "本番 receipt 由来の適格性があります（seed は初期値なので "
                         "--force なしでは上書きしません）"}
    if not getattr(args, "apply", False):
        return summary
    # 楽観的並行性: 書く直前に revision を読み直す（qualify / tune --apply と同じ規律）。
    current = read_json(path) or {}
    if int(current.get("revision") or 0) != int(existing.get("revision") or 0):
        return {**summary, "error": "target-changed-concurrently"}
    write_json_atomic(path, block)
    return {**summary, "applied": True}


def cmd_qualify(args, store) -> dict:
    """qualify サブコマンド本体。dry-run 既定・--apply で原子書換（revision 楽観検査）。"""
    now = dt.datetime.now(dt.timezone.utc)
    path = qualifications_file(args)
    existing = read_json(path) or {}
    window = int(getattr(args, "window_days", 0) or
                 (existing.get("evaluation_profiles") or {}).get(
                     DEFAULT_PROFILE_ID, {}).get("window_days") or
                 DEFAULT_PROFILE["window_days"])
    stats = collect_candidate_stats(store, now_epoch=now.timestamp(), window_days=window)
    document = build_qualifications(existing, stats, now=now)

    from agentcore import executioncontract
    errors = executioncontract.qualifications_errors(document)
    if errors:
        return {"applied": False, "error": "; ".join(errors[:3]), "file": path}

    changed = []
    old_index = {(c.get("agent_cli"), c.get("model")): c.get("qualifications") or {}
                 for c in (existing.get("candidates") or []) if isinstance(c, dict)}
    for cand in document["candidates"]:
        for op, qual in (cand.get("qualifications") or {}).items():
            before = (old_index.get((cand["agent_cli"], cand["model"])) or {}).get(op) or {}
            if before.get("status") != qual.get("status"):
                changed.append({"agent_cli": cand["agent_cli"], "model": cand["model"],
                                "operation_class": op,
                                "from": before.get("status") or "(none)",
                                "to": qual["status"],
                                "samples": qual.get("samples")})
    summary = {"file": path, "revision": document["revision"], "window_days": window,
               "observed_cells": len(stats), "status_changes": changed,
               "applied": False}
    if not getattr(args, "apply", False):
        return summary
    # 楽観的並行性: 書く直前に revision を読み直す（tune --apply と同じ規律）。
    current = read_json(path) or {}
    if int(current.get("revision") or 0) != int(existing.get("revision") or 0):
        return {**summary, "error": "target-changed-concurrently"}
    write_json_atomic(path, document)
    return {**summary, "applied": True}
