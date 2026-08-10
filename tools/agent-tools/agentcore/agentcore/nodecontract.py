"""Shared agent-flow node vocabulary and result contracts."""
from __future__ import annotations


VALID_KINDS = frozenset({
    "work", "generate", "classify", "synthesize", "verify",
    "filter", "judge", "reduce", "split", "map",
    "human", "extract", "retrieve",
})
PLANNER_KINDS = VALID_KINDS - {"human"}
STRUCTURED_KINDS = frozenset({
    "split", "map", "reduce", "filter", "judge", "verify", "extract", "retrieve",
})


class NodeDataError(ValueError):
    pass


def _warnings(data: dict) -> None:
    warnings = data.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise NodeDataError("warnings は文字列配列である必要があります")


def validate_node_data(kind: str, data):
    if kind == "human":
        if not isinstance(data, dict):
            raise NodeDataError("human data はオブジェクトである必要があります")
        iid = str(data.get("interaction_id") or "")
        if len(iid) != 19 or not iid.startswith("ix-") or any(c not in "0123456789abcdef" for c in iid[3:]):
            raise NodeDataError("human interaction_id が不正です")
        if data.get("outcome") not in {"approved", "rejected", "selected", "submitted", "defaulted", "expired"}:
            raise NodeDataError("human outcome が不正です")
        if not str(data.get("actor") or "").strip() or not isinstance(data.get("answer"), dict):
            raise NodeDataError("human actor と answer は必須です")
        return data
    if kind not in ("extract", "retrieve"):
        return data
    key = "records" if kind == "extract" else "sources"
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        if kind == "retrieve":
            raise NodeDataError("retrieve data.sources は配列である必要があります")
        raise NodeDataError("extract data.records は配列である必要があります")
    _warnings(data)
    if kind == "retrieve":
        for source in data["sources"]:
            if not isinstance(source, dict):
                raise NodeDataError("retrieve source はオブジェクトである必要があります")
            for field in ("id", "uri", "title", "locator", "excerpt", "digest"):
                if not str(source.get(field) or "").strip():
                    raise NodeDataError(f"retrieve source.{field} は必須です")
        return data
    for record in data["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
            raise NodeDataError("extract record.fields はオブジェクトである必要があります")
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise NodeDataError("extract record.evidence は1件以上必要です")
        for item in evidence:
            if not isinstance(item, dict) or any(
                not str(item.get(key) or "").strip() for key in ("source_id", "locator", "excerpt")
            ):
                raise NodeDataError("extract evidence には source_id / locator / excerpt が必要です")
    return data
