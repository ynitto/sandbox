#!/usr/bin/env python3
"""Archived evaluation ledgers -> agent-candidate-qualifications seed.

The mapping from benchmark cases to operation classes is intentionally explicit: a
model name alone never grants a broader qualification.  Output is deterministic for
the same archive, revision, and timestamp and can be copied to the control plane as
``qualifications.json``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
from pathlib import Path


TEXT_LEDGER = Path("worker/ledger-2026-08-14-text-eval-{model}.jsonl")
CODE_E4B_LEDGER = Path("worker/ledger-2026-08-14-followup-arms-gemma4-e4b.jsonl")
CODE_12B_LEDGER = Path("worker/ledger-2026-08-14-code-arms-gemma4-12b-wall600.jsonl")

TEXT_OPERATIONS = {
    "extract": "extract",
    "analyze": "bounded-analysis",
    "summarize": "constrained-summary",
    "propose": "bounded-proposal",
    "review": "bounded-review",
}

TEXT_CONSTRAINTS = {
    "extract": {"bounded_input": True, "requires_structured_output": True},
    "bounded-analysis": {"bounded_input": True},
    "constrained-summary": {"requires_deterministic_verification": True},
    "bounded-proposal": {"bounded_input": True},
    "bounded-review": {"bounded_input": True, "requires_structured_output": True},
}


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _stamp(value: dt.datetime) -> str:
    return _utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _records(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def _wilson_lower(passed: int, samples: int) -> float:
    if samples <= 0:
        return 0.0
    z = 1.96
    rate = passed / samples
    denominator = 1 + z * z / samples
    centre = rate + z * z / (2 * samples)
    margin = z * math.sqrt((rate * (1 - rate) + z * z / (4 * samples)) / samples)
    return round(max(0.0, (centre - margin) / denominator), 6)


def _profile(operation: str, min_samples: int) -> tuple[str, dict]:
    profile_id = f"{operation}-v1"
    return profile_id, {
        "operation_class": operation,
        "min_samples": min_samples,
        "min_pass_rate": 0.9,
        "max_timeout_rate": 0.1,
        "forbidden_failure_modes": ["required-deliverable-omitted", "scope-violation"],
        "window_days": 90,
        "valid_for_days": 90,
    }


def _status(samples: int, passed: int, timeouts: int, *, force_blocked: bool = False) -> str:
    if force_blocked:
        return "blocked"
    pass_rate = passed / samples if samples else 0
    timeout_rate = timeouts / samples if samples else 0
    if samples >= 6 and pass_rate >= 0.9 and timeout_rate <= 0.1:
        return "qualified"
    if samples >= 3 and pass_rate >= 2 / 3 and timeout_rate <= 1 / 3:
        return "trial"
    return "blocked"


def _qualification(agent_cli: str, model: str, operation: str, rows: list[dict],
                   generated_at: dt.datetime, profile_id: str, constraints: dict | None = None,
                   *, force_blocked: bool = False, failure_mode: str = "quality-failure") -> dict:
    samples = len(rows)
    passed = sum(bool(row.get("ok")) for row in rows)
    timeouts = sum(str(row.get("mode") or "") == "timeout" for row in rows)
    walls = [float(row["wall"]) for row in rows if isinstance(row.get("wall"), (int, float))]
    status = _status(samples, passed, timeouts, force_blocked=force_blocked)
    valid_until = generated_at + dt.timedelta(days=90)
    result = {
        "qualification_id": f"{agent_cli}-{model.replace(':', '-')}-{operation}-v1",
        "status": status,
        "evaluation_profile_id": profile_id,
        "samples": samples,
        "passed": passed,
        "timeout_rate": round(timeouts / samples, 6) if samples else 0,
        "success_rate_lower_bound": _wilson_lower(passed, samples),
        "p50_seconds": round(statistics.median(walls), 3) if walls else 0,
        "expected_calls_with_failures": round(samples / passed, 6) if passed else None,
        "critical_failure_risk": 1 if force_blocked else 0,
        "constraints": dict(constraints or {}),
        "source": "eval-archive",
        "last_evaluated_at": _stamp(generated_at),
        "valid_until": _stamp(valid_until),
    }
    failures = []
    if status == "blocked" and passed < samples:
        failures.append(failure_mode)
    if timeouts:
        failures.append("timeout")
    if failures:
        result["failure_modes"] = sorted(set(failures))
    return result


def _text_candidate(archive: Path, model: str, agent_cli: str,
                    generated_at: dt.datetime, profiles: dict) -> dict:
    rows = _records(archive / Path(str(TEXT_LEDGER).format(model=model.replace(":", "-"))))
    qualifications = {}
    for genre, operation in TEXT_OPERATIONS.items():
        selected = [row for row in rows if row.get("genre") == genre]
        profile_id, profile = _profile(operation, 3 if genre == "propose" else 6)
        profiles[profile_id] = profile
        qualifications[operation] = _qualification(
            agent_cli, model, operation, selected, generated_at, profile_id,
            TEXT_CONSTRAINTS.get(operation),
        )
    walls = [q["p50_seconds"] for q in qualifications.values() if q["p50_seconds"] > 0]
    return {
        "agent_cli": agent_cli,
        "model": model,
        "qualifications": qualifications,
        "execution_site": "device",
        "resource_group": "local-llm",
        "quota_scope": f"model:{model}",
        "economics": {"estimated_cost": 0, "currency": "JPY"},
        "latency": {"p50_seconds": round(statistics.median(walls), 3) if walls else 0},
    }


def _code_candidates(archive: Path, generated_at: dt.datetime, profiles: dict) -> list[dict]:
    e4b_rows = _records(archive / CODE_E4B_LEDGER)
    operations = {
        "single-symbol-edit": ([r for r in e4b_rows if str(r.get("task", "")).startswith("T4")], {
            "max_write_files": 1, "max_deliverables": 1, "max_diff_lines": 30,
            "requires_existing_verification": True, "forbids_new_contract_artifacts": True,
        }, False, "quality-failure"),
        "existing-test-repair": ([r for r in e4b_rows if str(r.get("task", "")).startswith("T2")], {
            "max_write_files": 1, "max_deliverables": 1, "max_diff_lines": 30,
            "requires_existing_verification": True, "forbids_new_contract_artifacts": True,
        }, False, "quality-failure"),
        "multi-artifact-contract-change": ([r for r in e4b_rows if str(r.get("task", "")).startswith("T3")], {
            "max_deliverables": 1,
        }, True, "required-deliverable-omitted"),
    }
    qualifications = {}
    for operation, (rows, constraints, blocked, failure_mode) in operations.items():
        profile_id, profile = _profile(operation, 9 if operation != "multi-artifact-contract-change" else 3)
        profiles[profile_id] = profile
        qualifications[operation] = _qualification(
            "aider", "gemma4:e4b", operation, rows, generated_at, profile_id, constraints,
            force_blocked=blocked, failure_mode=failure_mode,
        )

    twelve_rows = _records(archive / CODE_12B_LEDGER)
    profile_id, profile = _profile("code-worker", 6)
    profiles[profile_id] = profile
    code_12b = _qualification(
        "aider", "gemma4:12b", "code-worker", twelve_rows, generated_at, profile_id,
        {"disabled_until_stall_is_resolved": True}, force_blocked=True, failure_mode="non-termination",
    )
    common = {
        "execution_site": "device", "resource_group": "local-llm",
        "economics": {"estimated_cost": 0, "currency": "JPY"},
    }
    return [
        {"agent_cli": "aider", "model": "gemma4:e4b", "qualifications": qualifications, **common},
        {"agent_cli": "aider", "model": "gemma4:12b",
         "qualifications": {"code-worker": code_12b}, **common},
    ]


def build_seed(archive: Path, *, generated_at: dt.datetime, revision: int = 1) -> dict:
    archive = Path(archive)
    generated_at = _utc(generated_at)
    profiles: dict[str, dict] = {}
    candidates = _code_candidates(archive, generated_at, profiles)
    candidates.extend([
        _text_candidate(archive, "gemma4:e4b", "ollama", generated_at, profiles),
        # 12b のレビュー役は `ollama` の verify profile として走る。候補の agent_cli は
        # **正典名**でなければならない（agent-herd 仕様 §4.0）——profile 名（ollama-verify）で
        # 書くと tier 候補と照合せず selection_policy に一度も載らない一方、本番 receipt は
        # 正典名で記録されるので、同じ実行系の実測が偽候補へ割れる。用途の次元は
        # qualifications のキー（operation_class）が持っている。
        _text_candidate(archive, "gemma4:12b", "ollama", generated_at, profiles),
    ])
    return {
        "version": 1,
        "revision": int(revision),
        "generated_at": _stamp(generated_at),
        "evaluation_profiles": dict(sorted(profiles.items())),
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path(__file__).parent / "results" / "archive")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--generated-at", help="ISO8601 UTC (default: now)")
    args = parser.parse_args(argv)
    generated_at = (dt.datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
                    if args.generated_at else dt.datetime.now(dt.timezone.utc))
    document = build_seed(args.archive, generated_at=generated_at, revision=args.revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_name(f".{args.output.name}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
