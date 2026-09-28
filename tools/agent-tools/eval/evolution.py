#!/usr/bin/env python3
"""Existing eval ledgersにimmutable checkpoint identityとprocess comparisonを足す薄い層。

Runnerやoracleは持たない。入力は既存evaluatorが出したcase resultsであり、LLMを呼ばない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

STATUSES = ("IMPROVED", "RETAINED", "REGRESSION", "MIXED", "INSUFFICIENT_DATA")
OUTCOMES = ("pass", "wrong", "abstain", "error")
IMMUTABLE_GIT = re.compile(r"^[0-9a-f]{40}$")


def content_fingerprint(path: Path) -> str:
    """artifactShareと同じ意図のcontent identity（mtimeを使わない、全内容SHA-256）。"""
    target = path.resolve()
    files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for file in files:
        rel = file.name if target.is_file() else file.relative_to(target).as_posix()
        data = file.read_bytes()
        digest.update(f"{rel}:{len(data)}\n".encode())
        digest.update(data)
    return f"{len(files)}-{digest.hexdigest()}"


def checkpoint_identity(*, artifact_kind: str, artifact_id: str, checkpoint: str,
                        suite: str, result_ref: str, fingerprint: str | None = None) -> dict:
    if not all((artifact_kind, artifact_id, suite, result_ref)):
        raise ValueError("checkpoint identity fields must not be empty")
    if not IMMUTABLE_GIT.fullmatch(checkpoint) and not fingerprint:
        raise ValueError("checkpoint must be a full immutable git SHA or have a content fingerprint")
    return {"artifact_kind": artifact_kind, "artifact_id": artifact_id, "checkpoint": checkpoint,
            "content_fingerprint": fingerprint, "eval_suite": suite, "result_ref": result_ref}


def _rate(n: int, total: int):
    return n / total if total else None


def team_cases_from_readout(rows: list[dict], *, min_confidence: float) -> list[dict]:
    """RT5 ledgerをqualification入力へ写す。otherは通常回答、低確度だけがabstain。"""
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be in [0,1]")
    cases = []
    for row in rows:
        if not str(row.get("case", "")).startswith("RT5-"):
            continue
        cid = row["case"][4:]
        if row.get("status") != "ok":
            expected = (row.get("expected") or {}).get("team")
            cases.append({"id": cid, "partition": "held-out", "expected": expected,
                          "predicted": None, "outcome": "error", "confidence": None})
            continue
        answer = row["answers"]["team"]
        expected = row["expected"]["team"]
        confidence = answer.get("confidence")
        predicted = answer.get("choice")
        abstained = (answer.get("method") == "text" or confidence is None
                     or float(confidence) < min_confidence)
        cases.append({"id": cid, "partition": "held-out", "expected": expected,
                      "predicted": predicted, "outcome": "abstain" if abstained else
                      "pass" if predicted == expected else "wrong", "confidence": confidence})
    return cases


def qualify(identity: dict, environment: dict, cases: list[dict], *, minimum_cases: int = 12,
            minimum_per_class: int = 3) -> dict:
    """Normalize deterministic case outcomes into independently visible process metrics."""
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("duplicate case id")
    for case in cases:
        if case.get("outcome") not in OUTCOMES or case.get("partition") not in ("held-out", "adaptation"):
            raise ValueError(f"invalid case: {case.get('id')}")
    held = [c for c in cases if c["partition"] == "held-out"]
    class_counts = Counter(c["expected"] for c in held)
    sufficient = len(held) >= minimum_cases and bool(class_counts) and min(class_counts.values()) >= minimum_per_class
    answered = [c for c in held if c["outcome"] in ("pass", "wrong")]
    confidences = [float(c["confidence"]) for c in held if c.get("confidence") is not None]
    labels = sorted(class_counts)
    confusion = {expected: {predicted: 0 for predicted in labels + ["ABSTAIN"]}
                 for expected in labels}
    for case in held:
        predicted = "ABSTAIN" if case["outcome"] in ("abstain", "error") else case.get("predicted")
        confusion[case["expected"]].setdefault(predicted, 0)
        confusion[case["expected"]][predicted] += 1
    adaptation = [c for c in cases if c["partition"] == "adaptation"]
    return {
        "schema_version": 1, "identity": identity, "environment": environment,
        "sample_status": "SUFFICIENT" if sufficient else "INSUFFICIENT_DATA",
        "current_capability": {
            "accuracy": _rate(sum(c["outcome"] == "pass" for c in answered), len(answered)),
            "answered": len(answered), "abstained": sum(c["outcome"] == "abstain" for c in held),
            "abstention_rate": _rate(sum(c["outcome"] == "abstain" for c in held), len(held)),
            "confidence": {"n": len(confidences), "min": min(confidences) if confidences else None,
                           "mean": _rate(sum(confidences), len(confidences)),
                           "max": max(confidences) if confidences else None},
            "confusion": confusion,
            "wrong_case_ids": [c["id"] for c in held if c["outcome"] == "wrong"],
            "abstained_case_ids": [c["id"] for c in held if c["outcome"] == "abstain"],
        },
        "generalization": {"status": "MEASURED" if sufficient else "INSUFFICIENT_DATA",
                           "partition": "held-out", "passed": sum(c["outcome"] == "pass" for c in held),
                           "total": len(held), "failed_case_ids": [c["id"] for c in held if c["outcome"] != "pass"]},
        "adaptation": ({"status": "NOT_APPLICABLE", "case_ids": []} if not adaptation else
                       {"status": "MEASURED", "passed": sum(c["outcome"] == "pass" for c in adaptation),
                        "total": len(adaptation),
                        "failed_case_ids": [c["id"] for c in adaptation if c["outcome"] != "pass"]}),
        "cases": cases,
    }


def compare(previous: dict, current: dict) -> dict:
    """Pure A/B comparison. Base model/tools/suite mismatches are rejected, not averaged."""
    for key in ("artifact_kind", "artifact_id", "eval_suite"):
        if previous["identity"].get(key) != current["identity"].get(key):
            raise ValueError(f"incomparable identity: {key}")
    if previous.get("environment") != current.get("environment"):
        raise ValueError("incomparable environment: base model/tools must be fixed")
    if "INSUFFICIENT_DATA" in (previous.get("sample_status"), current.get("sample_status")):
        return {"status": "INSUFFICIENT_DATA", "retention": {"status": "INSUFFICIENT_DATA",
                "rate": None, "eligible_case_ids": [], "regression_case_ids": []}, "new_pass_case_ids": []}
    old = {c["id"]: c for c in previous["cases"] if c["partition"] == "held-out"}
    new = {c["id"]: c for c in current["cases"] if c["partition"] == "held-out"}
    eligible = sorted(cid for cid in old.keys() & new.keys() if old[cid]["outcome"] == "pass")
    if not eligible:
        return {"status": "INSUFFICIENT_DATA", "retention": {"status": "INSUFFICIENT_DATA",
                "rate": None, "eligible_case_ids": [], "regression_case_ids": []}, "new_pass_case_ids": []}
    regressions = [cid for cid in eligible if new[cid]["outcome"] != "pass"]
    new_pass = sorted(cid for cid in old.keys() & new.keys()
                      if old[cid]["outcome"] != "pass" and new[cid]["outcome"] == "pass")
    status = "MIXED" if regressions and new_pass else "REGRESSION" if regressions else "IMPROVED" if new_pass else "RETAINED"
    return {"status": status, "retention": {"status": "MEASURED",
            "rate": (len(eligible) - len(regressions)) / len(eligible),
            "eligible_case_ids": eligible, "regression_case_ids": regressions},
            "new_pass_case_ids": new_pass}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    current = json.loads(args.current.read_text())
    result = compare(json.loads(args.previous.read_text()), current) if args.previous else current
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
