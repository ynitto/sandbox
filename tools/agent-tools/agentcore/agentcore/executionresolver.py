"""agentcore.executionresolver — 実行直前の候補決定（Execution Resolver）。

設計: docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md
§5.2 / §7。柱3 / C7・C9・C10 — 選択判断を各 Adapter へ複製せず 1 実装に置き、
仕事は足る最小の候補へ流し、枯渇では黙って降格せず park で止める。

語彙と形は :mod:`agentcore.executioncontract`（段A）を使う。**判断はここが所有し、
Adapter（flow / loop / amigos / project / audit）は入力の変換だけを行う**（§5.3）。

エンジンは agent-candidate-qualifications を読まない（管理面専用——schemas/README）。
したがって候補の適格性は Compiler が control へ焼いた ``selection_policy`` だけを
根拠にし、明示固定（pin）は次の 2 経路しか通らない:

- policy の candidates に載っている（= Compiler が適格を確認済み）→ ``explicit-pin``
- Execution Envelope が trial を明示承認している → ``trial-candidate``

どちらでもない pin は適格性を検証できないので park する（blocked / unknown を
「確認できないまま実行」しない——§5.2 の不変条件を安全側に倒す）。

不変条件（§5.2。テストは test_executionresolver）:

- lifecycle / hard budget / control 期限 / tier 上限は pin でも迂回できない。
- ``blocked / unknown`` を自動選択しない。``trial`` は明示承認 run 限定。
- 同じ失敗候補の再試行は retry_limit で止まる。
- 適格候補ゼロは park（legacy fallback や弱い候補へ黙って降格しない。
  ``selection_policy`` がある限り legacy を再解釈しない——§6.6）。
- 決定は入力の純関数（同じ入力・control revision・qualification revision から
  同じ候補）。時刻は ``now`` 引数で受け、内部で時計を読まない。
- 選択した候補と理由を receipt へ残せる情報を必ず返す
  （:func:`receipt_execution_decision`）。
"""
from __future__ import annotations

import datetime as _dt

from agentcore.executioncontract import (
    AUTO_SELECTABLE_STATUSES,
    CONTROL_VERSION_SELECTION,
    candidate_id,
    selection_policy_errors,
)

# tier の標準語彙（下から）。正典は schemas/agent-control.schema.json の workload.tier。
TIER_ORDER = ("basic", "small", "medium", "large")

# legacy 経路（selection_policy 無し）の既定再試行上限。policy がある場合は policy 側が正。
DEFAULT_RETRY_LIMIT = 1


def _tier_index(tier) -> "int | None":
    try:
        return TIER_ORDER.index(str(tier))
    except ValueError:
        return None


def _parse_utc(value) -> "_dt.datetime | None":
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _gate(execution_contract) -> "str | None":
    contract = execution_contract if isinstance(execution_contract, dict) else {}
    verification = contract.get("verification") or {}
    if isinstance(verification, dict) and verification.get("commands"):
        return "verification-command"
    return None


def _park(reason: str, detail: str, resume: str, *, base: dict) -> dict:
    decision = dict(base)
    decision.update({
        "selected": None,
        "parked": True,
        "park_reason": reason,
        "reason": detail,
        "resume_condition": resume,
    })
    return decision


def _selected(candidate: dict, source: str, reason: str, *, base: dict,
              fallback: "list[dict]" = (), rank=None, refs=()) -> dict:
    decision = dict(base)
    refs = list(refs or [])
    decision.update({
        "selected": {"agent_cli": str(candidate["agent_cli"]), "model": str(candidate["model"])},
        "parked": False,
        "selection_source": source,
        "qualification_refs": refs,
        "qualification_id": refs[0] if refs else None,
        "rank": rank,
        "reason": reason,
        "fallback_candidates": [
            {"agent_cli": str(c["agent_cli"]), "model": str(c["model"])} for c in fallback],
    })
    return decision


def _policy_order(candidates: "list[dict]") -> "list[dict]":
    # rank（Compiler 確定・revision 中は不変）→ 利用者設定の候補順（配列順）→
    # agent_cli/model 昇順。Resolver は availability 除外以外の再採点をしない（§7）。
    indexed = list(enumerate(candidates))
    indexed.sort(key=lambda pair: (
        int(pair[1].get("rank") or 0), pair[0],
        candidate_id(str(pair[1].get("agent_cli")), str(pair[1].get("model")))))
    return [candidate for _i, candidate in indexed]


def _auto_selectable(candidate: dict) -> bool:
    # policy 掲載 = Compiler が適格確認済み。status は Compiler が任意で残す防衛線で、
    # 明示されていて qualified 以外なら自動選択から外す（blocked / unknown / trial）。
    status = candidate.get("status")
    return status is None or status in AUTO_SELECTABLE_STATUSES


def resolve_execution(workload: str, *, purpose_or_role=None, execution_contract=None,
                      explicit_pin=None, budget_state=None, compiled_control=None,
                      profiles_default=None, unavailable=(), attempt_counts=None,
                      now=None) -> dict:
    """実行直前の候補決定。ExecutionDecision（dict）を返す。

    - ``explicit_pin``: Execution Envelope 由来の明示固定。
      ``{"agent_cli", "model"}`` に加え Envelope の承認内容を運ぶ:
      ``trial_approved``（bool）・``tier``・``tier_ceiling_override``・``retry_limit``。
    - ``budget_state``: ``{"hard_exhausted": bool, "lifecycle": str, ...}``。
      receipt の resource_snapshot は Adapter が別途埋める。
    - ``unavailable``: 実行時 availability で除外する candidate_id の集合（§7 手順5）。
    - ``attempt_counts``: candidate_id → これまでの失敗 attempt 数。
    - ``now``: control 期限判定に使う aware datetime。省略時は期限を判定しない
      （決定の再現性のため、内部で時計を読まない）。
    """
    control = compiled_control if isinstance(compiled_control, dict) else {}
    wl = (control.get("workloads") or {}).get(workload) or {}
    unavailable = set(unavailable or ())
    attempts = dict(attempt_counts or {})
    pin = explicit_pin if isinstance(explicit_pin, dict) else None
    base = {
        "workload": workload,
        "gate": _gate(execution_contract),
        "control_revision": control.get("revision"),
        "qualification_revision": None,
        "strategy": None,
        "retry_limit": DEFAULT_RETRY_LIMIT,
        "eligible_candidate_ids": [],
    }

    # --- 手順1: lifecycle / hard budget / control 期限。pin でも迂回できない ---
    version = control.get("version", 1)
    if (not isinstance(version, int) or isinstance(version, bool)
            or version > CONTROL_VERSION_SELECTION):
        return _park("unsupported-control-version",
                     f"未知の control version です: {version!r}（推測で実行しない）",
                     "対応する release へ更新するか、管理面が version を下げる", base=base)
    budget = budget_state if isinstance(budget_state, dict) else {}
    lifecycle = str(budget.get("lifecycle") or wl.get("lifecycle") or "run")
    if lifecycle != "run":
        return _park(f"lifecycle-{lifecycle}", f"workload の lifecycle が {lifecycle} です",
                     "lifecycle が run へ戻る", base=base)
    if budget.get("hard_exhausted"):
        return _park("hard-budget-exhausted", "hard budget を使い切っています",
                     "予算の補充または上限の変更", base=base)
    expires = _parse_utc(control.get("valid_until"))
    if expires is not None and now is not None and now >= expires:
        return _park("control-expired",
                     f"control の valid_until（{control.get('valid_until')}）を過ぎています",
                     "管理面が control を再発行する", base=base)

    # --- selection_policy（version 2）の読み取り ---
    policy = wl.get("selection_policy") if isinstance(version, int) and version >= 2 else None
    ordered: "list[dict]" = []
    if policy is not None:
        errors = selection_policy_errors(policy)
        if errors:
            # 壊れた policy を legacy へ静かに落とすと「selection_policy がある限り
            # legacy を再解釈しない」（§6.6）が破れる。park して管理面へ返す。
            return _park("invalid-selection-policy",
                         "selection_policy が契約に合いません: " + "; ".join(errors[:3]),
                         "Compiler が正しい selection_policy を再発行する", base=base)
        base["strategy"] = policy.get("strategy")
        base["qualification_revision"] = policy.get("qualification_revision")
        base["retry_limit"] = int(policy.get("retry_limit", DEFAULT_RETRY_LIMIT))
        ordered = _policy_order(list(policy.get("candidates") or []))
        base["eligible_candidate_ids"] = [
            candidate_id(str(c.get("agent_cli")), str(c.get("model"))) for c in ordered]

    def usable(cid: str, retry_limit: int) -> "str | None":
        """候補が今使えないなら理由コード、使えるなら None。"""
        if cid in unavailable:
            return "unavailable"
        if attempts.get(cid, 0) >= retry_limit + 1:
            return "retry-exhausted"
        return None

    # --- 手順2: 明示固定（Envelope）。scope / gate / tier / budget は迂回させない ---
    if pin is not None:
        pin_cli, pin_model = str(pin.get("agent_cli") or ""), str(pin.get("model") or "")
        if not pin_cli or not pin_model:
            return _park("invalid-pin", "explicit_pin は agent_cli / model が必須です",
                         "Envelope の pin を修正する", base=base)
        cid = candidate_id(pin_cli, pin_model)
        ceiling = pin.get("tier_ceiling_override") or wl.get("tier")
        pin_tier, ceiling_index = _tier_index(pin.get("tier")), _tier_index(ceiling)
        if pin_tier is not None and ceiling_index is not None and pin_tier > ceiling_index:
            return _park("pin-exceeds-tier",
                         f"pin の tier（{pin.get('tier')}）が上限（{ceiling}）を超えています",
                         "Envelope で tier_ceiling_override を承認する", base=base)
        retry_limit = min(int(base["retry_limit"]),
                          int(pin.get("retry_limit", base["retry_limit"])))
        base["retry_limit"] = retry_limit
        blocked = usable(cid, retry_limit)
        if blocked:
            return _park(f"pin-{blocked}", f"pin 候補 {cid} が {blocked} です",
                         "候補の回復を待つか Envelope の pin を変更する", base=base)
        matched = next((c for c in ordered
                        if str(c.get("agent_cli")) == pin_cli and str(c.get("model")) == pin_model),
                       None)
        if matched is not None:
            status = matched.get("status")
            if status == "trial":
                # policy 掲載の trial 候補は Envelope の明示承認がある run でだけ実行できる。
                if pin.get("trial_approved"):
                    return _selected(matched, "trial-candidate",
                                     "Envelope が trial を明示承認した run の固定（policy 掲載候補）",
                                     base=base, rank=matched.get("rank"),
                                     refs=matched.get("qualification_refs") or [])
                return _park("pin-not-qualified",
                             f"pin 候補 {cid} は trial です（Envelope の trial 承認が必要）",
                             "Envelope で trial を承認する", base=base)
            if status is not None and status not in AUTO_SELECTABLE_STATUSES:
                return _park("pin-not-qualified",
                             f"pin 候補 {cid} の status が {status} です（明示固定でも実行不可）",
                             "agent-audit の隔離評価で適格化する", base=base)
            return _selected(matched, "explicit-pin", "Envelope の明示固定（適格候補）",
                             base=base, rank=matched.get("rank"),
                             refs=matched.get("qualification_refs") or [])
        if pin.get("trial_approved"):
            return _selected({"agent_cli": pin_cli, "model": pin_model}, "trial-candidate",
                             "Envelope が trial を明示承認した run の固定", base=base)
        return _park("pin-not-qualified",
                     f"pin 候補 {cid} は selection_policy に無く trial 承認もありません"
                     "（適格性を検証できない候補は実行しない）",
                     "Compiler の候補へ載せるか Envelope で trial を承認する", base=base)

    # --- 手順3〜7: selection_policy からの自動選択 ---
    if policy is not None:
        eligible = [c for c in ordered if _auto_selectable(c)]
        remaining = [c for c in eligible
                     if not usable(candidate_id(str(c["agent_cli"]), str(c["model"])),
                                   int(base["retry_limit"]))]
        if remaining:
            chosen, fallback = remaining[0], remaining[1:]
            return _selected(
                chosen, "qualified-candidate",
                f"strategy={policy.get('strategy')} の順位 {chosen.get('rank')} 位",
                base=base, fallback=fallback, rank=chosen.get("rank"),
                refs=chosen.get("qualification_refs") or [])
        # --- 手順8: 候補枯渇。legacy fallback を再解釈せず park ---
        return _park("no-eligible-candidate",
                     "selection_policy の適格候補がすべて利用不可です（降格しない）",
                     "候補の回復・qualifications の更新・方針の変更のいずれか", base=base)

    # --- legacy 経路（§6.6 の 3〜5。selection_policy が無いときだけ）---
    defaults = control.get("defaults") or {}
    override = (wl.get("agents") or {}).get(purpose_or_role) if purpose_or_role else None
    for source in (override, wl, defaults, profiles_default):
        source = source if isinstance(source, dict) else {}
        cli = source.get("agent_cli") or wl.get("agent_cli") or defaults.get("agent_cli")
        model = source.get("model") or wl.get("model") or defaults.get("model")
        if source and cli and model:
            cid = candidate_id(str(cli), str(model))
            blocked = usable(cid, int(base["retry_limit"]))
            if blocked:
                return _park(f"legacy-{blocked}", f"legacy 候補 {cid} が {blocked} です"
                             "（弱い候補へ黙って降格しない）",
                             "候補の回復を待つか管理面が候補を変更する", base=base)
            base["eligible_candidate_ids"] = [cid]
            return _selected({"agent_cli": cli, "model": model}, "legacy-fallback",
                             "selection_policy が無いため既存設定で解決", base=base)
    return _park("no-candidate", "どの解決層にも候補がありません",
                 "管理面が workload の候補を設定する", base=base)


def receipt_execution_decision(decision: dict, *, fallback_from=None) -> dict:
    """ExecutionDecision → 実行 receipt の ``execution_decision`` ブロック（§6.5）。

    Adapter は attempt_id / verification / resource_snapshot を足して receipt を完成させる。
    形は executioncontract.execution_receipt_errors が縛る。
    """
    selected = decision.get("selected") or {}
    return {
        "agent_cli": selected.get("agent_cli"),
        "model": selected.get("model"),
        "selection_source": decision.get("selection_source"),
        "qualification_id": decision.get("qualification_id"),
        "control_revision": decision.get("control_revision"),
        "qualification_revision": decision.get("qualification_revision"),
        "rank": decision.get("rank"),
        "reason": decision.get("reason"),
        "fallback_from": fallback_from,
        "eligible_candidate_ids": list(decision.get("eligible_candidate_ids") or []),
    }
