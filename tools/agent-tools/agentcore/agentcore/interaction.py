"""Validation and deterministic settlement for human interactions."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone


MODES = frozenset({"approval", "choice", "input"})
DEFAULT_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
MAX_DOCUMENT_BYTES = 64 * 1024
_RESPONSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class InteractionError(ValueError):
    pass


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def interaction_id(run_id: str, node_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{node_id}".encode("utf-8")).hexdigest()[:16]
    return f"ix-{digest}"


def _check_size(value: dict, label: str) -> None:
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise InteractionError(f"{label} は {MAX_DOCUMENT_BYTES} bytes 以下である必要があります")


def normalize_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise InteractionError("interaction はオブジェクトである必要があります")
    mode = str(spec.get("mode") or "approval").strip()
    prompt = str(spec.get("prompt") or "").strip()
    if mode not in MODES:
        raise InteractionError(f"interaction.mode が不正です: {mode}")
    if not prompt:
        raise InteractionError("interaction.prompt は必須です")
    try:
        timeout = int(spec.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        raise InteractionError("interaction.timeout_seconds は正数で指定してください") from None
    if timeout <= 0:
        raise InteractionError("interaction.timeout_seconds は正数で指定してください")
    audience = [str(v).strip() for v in (spec.get("audience") or ["reviewer"]) if str(v).strip()]
    if not audience:
        raise InteractionError("interaction.audience は1件以上必要です")
    options = []
    default_option = None
    if mode == "choice":
        options = [str(v).strip() for v in (spec.get("options") or []) if str(v).strip()]
        if len(options) < 2 or len(set(options)) != len(options):
            raise InteractionError("choice には重複しない options が2件以上必要です")
        default_option = str(spec.get("default_option") or "").strip() or None
        if default_option is not None and default_option not in options:
            raise InteractionError("default_option は options から選んでください")
    elif spec.get("options") or spec.get("default_option"):
        raise InteractionError("options と default_option は choice だけで指定できます")
    normalized = {
        "mode": mode,
        "prompt": prompt,
        "audience": audience,
        "timeout_seconds": timeout,
    }
    if mode == "choice":
        normalized["options"] = options
        if default_option is not None:
            normalized["default_option"] = default_option
    return normalized


def build_request(run_id: str, node_id: str, spec: dict, *, now: "datetime | None" = None) -> dict:
    normalized = normalize_spec(spec)
    created = now or datetime.now(timezone.utc)
    request = {
        "version": 1,
        "interaction_id": interaction_id(str(run_id), str(node_id)),
        "run_id": str(run_id),
        "node_id": str(node_id),
        "mode": normalized["mode"],
        "prompt": normalized["prompt"],
        "audience": normalized["audience"],
        "options": normalized.get("options", []),
        "default_option": normalized.get("default_option"),
        "created_at": _iso(created),
        "expires_at": _iso(created + timedelta(seconds=normalized["timeout_seconds"])),
    }
    _check_size(request, "interaction request")
    return request


def _parse_iso(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise InteractionError(f"{label} は ISO8601 で指定してください") from None
    if parsed.tzinfo is None:
        raise InteractionError(f"{label} には timezone が必要です")
    return parsed.astimezone(timezone.utc)


def validate_response(request: dict, raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise InteractionError("interaction response はオブジェクトである必要があります")
    if str(raw.get("interaction_id") or "") != str(request.get("interaction_id") or ""):
        raise InteractionError("interaction_id が request と一致しません")
    response_id = str(raw.get("response_id") or "").strip()
    actor = str(raw.get("actor") or "").strip()
    if not _RESPONSE_ID.fullmatch(response_id):
        raise InteractionError("response_id が不正です")
    if not actor:
        raise InteractionError("actor は必須です")
    submitted_at = _iso(_parse_iso(raw.get("submitted_at"), "submitted_at"))
    answer = raw.get("answer")
    if not isinstance(answer, dict):
        raise InteractionError("answer はオブジェクトである必要があります")
    mode = str(request.get("mode") or "")
    comment = str(answer.get("comment") or "").strip()
    if mode == "approval":
        decision = str(answer.get("decision") or "").strip()
        if decision not in ("approved", "rejected"):
            raise InteractionError("approval answer は approved または rejected です")
        normalized = {"decision": decision, "comment": comment}
    elif mode == "choice":
        option = str(answer.get("option") or "").strip()
        if option not in (request.get("options") or []):
            raise InteractionError("choice answer は options から選んでください")
        normalized = {"option": option, "comment": comment}
    elif mode == "input":
        text = str(answer.get("text") or "").strip()
        if not text:
            raise InteractionError("input answer の text は必須です")
        normalized = {"text": text}
    else:
        raise InteractionError(f"request.mode が不正です: {mode}")
    response = {
        "version": 1,
        "interaction_id": request["interaction_id"],
        "response_id": response_id,
        "actor": actor,
        "answer": normalized,
        "submitted_at": submitted_at,
    }
    _check_size(response, "interaction response")
    return response


def resolve(request: dict, responses: list, *, now: "datetime | None" = None) -> "dict | None":
    expires_at = _parse_iso(request.get("expires_at"), "expires_at")
    valid = []
    for raw in responses or []:
        try:
            response = validate_response(request, raw)
        except InteractionError:
            continue
        if _parse_iso(response["submitted_at"], "submitted_at") <= expires_at:
            valid.append(response)
    if not valid:
        resolved_at = now or datetime.now(timezone.utc)
        if resolved_at < expires_at:
            return None
        default_option = request.get("default_option") if request.get("mode") == "choice" else None
        resolution = {
            "version": 1,
            "interaction_id": request["interaction_id"],
            "outcome": "defaulted" if default_option else "expired",
            "response_id": None,
            "actor": "system",
            "answer": {"option": default_option} if default_option else {},
            "resolved_at": _iso(resolved_at),
        }
        _check_size(resolution, "interaction resolution")
        return resolution
    winner = min(valid, key=lambda item: item["response_id"])
    mode = request["mode"]
    if mode == "approval":
        outcome = winner["answer"]["decision"]
    elif mode == "choice":
        outcome = "selected"
    else:
        outcome = "submitted"
    resolution = {
        "version": 1,
        "interaction_id": request["interaction_id"],
        "outcome": outcome,
        "response_id": winner["response_id"],
        "actor": winner["actor"],
        "answer": winner["answer"],
        "resolved_at": _iso(now or datetime.now(timezone.utc)),
    }
    _check_size(resolution, "interaction resolution")
    return resolution
