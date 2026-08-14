"""agentcore.executioncontract — 候補ベース実行 3 契約の語彙と形の 1 実装。

正典 schema（形の文書化と examples = 共通 fixture）:

- ``schemas/agent-candidate-qualifications.schema.json`` — 候補×処理種別の実測格付け
- ``schemas/agent-control.schema.json`` v2 — ``workloads.<wl>.selection_policy``
- ``schemas/execution-receipt.schema.json`` — 実行記録へ埋め込む共通ブロック

設計: docs/plans/2026-08-15-agent-tools-candidate-execution-policy-dashboard-design.md §6。
段A（契約の固定）の成果物であり、**判断ロジックは持たない**——候補の解決（Resolver）は
E1、方針のコンパイル（Compiler）は U2 が実装する。双方がこのモジュールの語彙・検証と
schema の examples を契約テストの共通 fixture として使う（語彙の 2 実装を作らない——C7。
schema 側 enum との一致は test_executioncontract が縛る）。

**新 reader の候補解決順（設計 §6.6 の正典。RESOLUTION_ORDER と同順）**:

1. ``envelope-pin`` — 当該 run の Execution Envelope にある明示固定
2. ``selection-policy`` — ``version >= 2`` の ``selection_policy``
3. ``purpose-override`` — 既存 purpose/role 別上書き（selection_policy が無い場合だけ）
4. ``workload-single`` — 既存 workload 直下の単一 ``agent_cli / model``
5. ``profiles-default`` — agent-profiles の既定候補

**dual-write（移行契約）**: 移行中の Compiler は ``selection_policy`` と、旧 reader 向けの
単一 fallback 候補（workload 直下の ``agent_cli / model``）を併記する。旧 reader は
新フィールドを無視し、新 reader は ``selection_policy`` がある限り legacy fallback を
再解釈しない。rollback は Compiler の v2 出力を止めて legacy fallback へ戻す。
未知の version を読んだエンジンは推測で実行せず、最後に対応できた control を
``valid_until`` まで使い、その後 park する。

**形に埋めてある不変条件**（執行は Resolver=E1。ここは語彙だけ提供する）:
``blocked / unknown`` を自動選択しない・``trial`` は明示承認 run 限定
（AUTO_SELECTABLE_STATUSES）・pin は lifecycle / hard budget / scope / gate を迂回しない・
候補枯渇は park（NO_CANDIDATE_ACTIONS——弱い候補へ黙って降格しない）。
"""
from __future__ import annotations

from agentcore.verifycontract import CRITERION_VERDICTS as VERIFICATION_VERDICTS  # noqa: F401

QUALIFICATIONS_VERSION = 1
# agent-control の version。1 = 単一候補（従来）/ 2 = selection_policy 付き。
# 設計書の「schema_version」は既存契約のこのフィールドに対応させる（第 2 の版数キーを足さない）。
CONTROL_VERSION_SELECTION = 2

QUALIFICATION_STATUSES = ("qualified", "trial", "blocked", "unknown")
# 通常方針で自動選択してよい status。trial は Execution Envelope で明示承認された run 限定、
# blocked / unknown は明示固定でも実行不可（試すなら agent-audit の隔離評価へ）。
AUTO_SELECTABLE_STATUSES = frozenset({"qualified"})
QUALIFICATION_SOURCES = ("eval-archive", "receipt")
EXECUTION_SITES = ("device", "external", "managed")
STRATEGIES = ("balanced", "economy", "quality")
NO_CANDIDATE_ACTIONS = ("park",)
SELECTION_SOURCES = ("qualified-candidate", "trial-candidate", "explicit-pin", "legacy-fallback")
RESOLUTION_ORDER = ("envelope-pin", "selection-policy", "purpose-override",
                    "workload-single", "profiles-default")


def candidate_id(agent_cli: str, model: str) -> str:
    """候補識別子の正典表記（receipt の eligible_candidate_ids / 台帳の集計キー）。"""
    return f"{agent_cli}/{model}"


def _str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _int(value, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _ratio(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def qualifications_errors(doc) -> "list[str]":
    """agent-candidate-qualifications 全体の形式検査。エラーの文字列リスト（空 = 合格）。"""
    if not isinstance(doc, dict):
        return ["qualifications はオブジェクトである必要があります"]
    errors: list[str] = []
    if doc.get("version") != QUALIFICATIONS_VERSION:
        errors.append(f"version は {QUALIFICATIONS_VERSION} である必要があります")
    if not _int(doc.get("revision")):
        errors.append("revision は 0 以上の整数である必要があります")
    profiles = doc.get("evaluation_profiles", {})
    if not isinstance(profiles, dict):
        errors.append("evaluation_profiles はオブジェクトである必要があります")
        profiles = {}
    for pid, profile in profiles.items():
        if not isinstance(profile, dict):
            errors.append(f"evaluation_profiles[{pid}] はオブジェクトである必要があります")
            continue
        if not _int(profile.get("min_samples"), minimum=1):
            errors.append(f"evaluation_profiles[{pid}].min_samples は 1 以上の整数である必要があります")
        if not _ratio(profile.get("min_pass_rate")):
            errors.append(f"evaluation_profiles[{pid}].min_pass_rate は 0..1 である必要があります")
        if not _int(profile.get("valid_for_days"), minimum=1):
            errors.append(f"evaluation_profiles[{pid}].valid_for_days は 1 以上の整数である必要があります")
        if "max_timeout_rate" in profile and not _ratio(profile.get("max_timeout_rate")):
            errors.append(f"evaluation_profiles[{pid}].max_timeout_rate は 0..1 である必要があります")
    candidates = doc.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidates は配列である必要があります")
        return errors
    for index, candidate in enumerate(candidates):
        label = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            errors.append(f"{label} はオブジェクトである必要があります")
            continue
        if not _str(candidate.get("agent_cli")) or not _str(candidate.get("model")):
            errors.append(f"{label} は agent_cli / model が必須です")
        site = candidate.get("execution_site")
        if site is not None and site not in EXECUTION_SITES:
            errors.append(f"{label}.execution_site が不正です: {site}")
        quals = candidate.get("qualifications", {})
        if not isinstance(quals, dict):
            errors.append(f"{label}.qualifications はオブジェクトである必要があります")
            continue
        for op_class, qual in quals.items():
            qlabel = f"{label}.qualifications[{op_class}]"
            if not isinstance(qual, dict):
                errors.append(f"{qlabel} はオブジェクトである必要があります")
                continue
            if not _str(qual.get("qualification_id")):
                errors.append(f"{qlabel}.qualification_id が必須です")
            if qual.get("status") not in QUALIFICATION_STATUSES:
                errors.append(f"{qlabel}.status が不正です: {qual.get('status')}")
            samples, passed = qual.get("samples"), qual.get("passed")
            if samples is not None and not _int(samples):
                errors.append(f"{qlabel}.samples は 0 以上の整数である必要があります")
            if passed is not None and not _int(passed):
                errors.append(f"{qlabel}.passed は 0 以上の整数である必要があります")
            if _int(samples) and _int(passed) and passed > samples:
                errors.append(f"{qlabel}.passed が samples を超えています")
            source = qual.get("source")
            if source is not None and source not in QUALIFICATION_SOURCES:
                errors.append(f"{qlabel}.source が不正です: {source}")
            if "failure_modes" in qual and not _str_list(qual.get("failure_modes")):
                errors.append(f"{qlabel}.failure_modes は文字列配列である必要があります")
            profile_id = qual.get("evaluation_profile_id")
            if profile_id is not None and profiles and profile_id not in profiles:
                errors.append(f"{qlabel}.evaluation_profile_id が evaluation_profiles にありません: {profile_id}")
    return errors


def selection_policy_errors(policy) -> "list[str]":
    """workloads.<wl>.selection_policy の形式検査。エラーの文字列リスト（空 = 合格）。"""
    if not isinstance(policy, dict):
        return ["selection_policy はオブジェクトである必要があります"]
    errors: list[str] = []
    if policy.get("strategy") not in STRATEGIES:
        errors.append(f"strategy が不正です: {policy.get('strategy')}")
    if not _int(policy.get("retry_limit")):
        errors.append("retry_limit は 0 以上の整数である必要があります")
    if policy.get("no_candidate") not in NO_CANDIDATE_ACTIONS:
        errors.append(f"no_candidate が不正です: {policy.get('no_candidate')}")
    if "ranking_formula_version" in policy and not _int(policy.get("ranking_formula_version"), minimum=1):
        errors.append("ranking_formula_version は 1 以上の整数である必要があります")
    if "qualification_revision" in policy and not _int(policy.get("qualification_revision")):
        errors.append("qualification_revision は 0 以上の整数である必要があります")
    candidates = policy.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        errors.append("candidates は 1 件以上の配列である必要があります")
        return errors
    for index, candidate in enumerate(candidates):
        label = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            errors.append(f"{label} はオブジェクトである必要があります")
            continue
        if not _str(candidate.get("agent_cli")) or not _str(candidate.get("model")):
            errors.append(f"{label} は agent_cli / model が必須です")
        # rank は Compiler が strategy から確定した順位。同順位は許す（同順位内は
        # 利用者設定順 → agent_cli/model 昇順で決める——順序付けは Resolver=E1 の仕事）。
        if not _int(candidate.get("rank"), minimum=1):
            errors.append(f"{label}.rank は 1 以上の整数である必要があります")
        if "qualification_refs" in candidate and not _str_list(candidate.get("qualification_refs")):
            errors.append(f"{label}.qualification_refs は文字列配列である必要があります")
    return errors


def execution_receipt_errors(record) -> "list[str]":
    """実行 receipt 共通ブロックの形式検査。エラーの文字列リスト（空 = 合格）。"""
    if not isinstance(record, dict):
        return ["receipt はオブジェクトである必要があります"]
    errors: list[str] = []
    if not _str(record.get("attempt_id")):
        errors.append("attempt_id が必須です（attempt は候補ごとに数える）")
    decision = record.get("execution_decision")
    if not isinstance(decision, dict):
        errors.append("execution_decision が必須です")
    else:
        if not _str(decision.get("agent_cli")) or not _str(decision.get("model")):
            errors.append("execution_decision は agent_cli / model が必須です")
        if decision.get("selection_source") not in SELECTION_SOURCES:
            errors.append(f"execution_decision.selection_source が不正です: {decision.get('selection_source')}")
        fallback = decision.get("fallback_from")
        if fallback is not None and not _str(fallback):
            errors.append("execution_decision.fallback_from は文字列または null である必要があります")
        if "rank" in decision and not _int(decision.get("rank"), minimum=1):
            errors.append("execution_decision.rank は 1 以上の整数である必要があります")
        if "eligible_candidate_ids" in decision and not _str_list(decision.get("eligible_candidate_ids")):
            errors.append("execution_decision.eligible_candidate_ids は文字列配列である必要があります")
    verification = record.get("verification")
    if verification is not None:
        if not isinstance(verification, dict):
            errors.append("verification はオブジェクトである必要があります")
        else:
            if not _str(verification.get("kind")):
                errors.append("verification.kind が必須です")
            if verification.get("verdict") not in VERIFICATION_VERDICTS:
                errors.append(f"verification.verdict が不正です: {verification.get('verdict')}")
            if "attempt" in verification and not _int(verification.get("attempt"), minimum=1):
                errors.append("verification.attempt は 1 以上の整数である必要があります")
    snapshot = record.get("resource_snapshot")
    if snapshot is not None:
        if not isinstance(snapshot, dict):
            errors.append("resource_snapshot はオブジェクトである必要があります")
        elif "budget_remaining" in snapshot and not _ratio(snapshot.get("budget_remaining")):
            errors.append("resource_snapshot.budget_remaining は 0..1 である必要があります")
    if "external_data_classes" in record and not _str_list(record.get("external_data_classes")):
        errors.append("external_data_classes は文字列配列である必要があります（本文・秘密は複製しない）")
    return errors
