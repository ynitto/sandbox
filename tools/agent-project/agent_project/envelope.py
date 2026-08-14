from __future__ import annotations
# Execution Envelope — plan approval freezes the safety/scope contract for one task/run lineage.


def execution_envelope_path(cfg: "Config", task_id: str) -> Path:
    return cfg.backlog / f"{task_id}.envelope.json"


def _envelope_values(task: Task, key: str) -> "list[str]":
    values = [str(value).strip() for name, value in task.extra if name == key and str(value).strip()]
    out: list[str] = []
    for value in values:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, list):
            out.extend(str(item).strip() for item in decoded if str(item).strip())
        else:
            out.extend(item.strip() for item in re.split(r"\s*(?:,|⏎)\s*", value) if item.strip())
    return list(dict.fromkeys(out))


def _envelope_json(task: Task, key: str, default):
    value = task.get(key)
    if not value:
        return default
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return decoded


def _envelope_bool(task: Task, key: str, default: bool = False) -> bool:
    value = str(task.get(key, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "allow", "allowed"}


def _control_policy_snapshot() -> dict:
    root = Path(os.environ.get("AGENT_CONTROL_DIR") or (Path.home() / ".agents" / "control"))
    try:
        control_doc = json.loads((root / "control.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        control_doc = {}
    return {
        "control_version": int(control_doc.get("version") or 1),
        "control_revision": int(control_doc.get("revision") or 0),
        "valid_until": str(control_doc.get("valid_until") or ""),
    }


def build_execution_envelope(cfg: "Config", task: Task, reason: str = "") -> dict:
    retry_raw = task.get("candidate_retry_limit", "1")
    try:
        retry_limit = max(0, int(retry_raw))
    except (TypeError, ValueError):
        retry_limit = 1
    pins = _envelope_json(task, "candidate_pins", [])
    trials = _envelope_json(task, "trial_candidates", [])
    if not isinstance(pins, list):
        pins = []
    if not isinstance(trials, list):
        trials = []
    verification = build_task_verification_plan(cfg, task)
    document = {
        "version": 1,
        "task_id": task.id,
        "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "approval": {"actor": cfg.actor, "reason": str(reason or "")},
        "policy_snapshot": _control_policy_snapshot(),
        "scope": {
            "repositories": _envelope_values(task, "repos") + _envelope_values(task, "workspace"),
            "paths": _envelope_values(task, "paths"),
            "protected": _envelope_values(task, "protected_paths"),
        },
        "acceptance": task_acceptance(task),
        "verification": verification,
        "candidate_permissions": {
            "pins": [item for item in pins if isinstance(item, dict)],
            "trials": [item for item in trials if isinstance(item, dict)],
            "tier_ceiling_override": str(task.get("tier_ceiling_override") or ""),
            "retry_limit": retry_limit,
        },
        "external_execution": {
            "allowed": _envelope_bool(task, "external_execution_allowed"),
            "repositories": _envelope_values(task, "external_repositories"),
            "paths": _envelope_values(task, "external_paths"),
            "data_classes": _envelope_values(task, "external_data_classes"),
            "denied_paths": _envelope_values(task, "external_denied_paths"),
            "redaction": str(task.get("external_redaction") or "required"),
        },
        "replan_when": _envelope_values(task, "replan_when") or [
            "scope expansion is required",
            "no qualified candidate is available",
            "hard budget increase is required",
        ],
    }
    digest_source = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    document["digest"] = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    return document


def approve_execution_envelope(cfg: "Config", task: Task, reason: str = "") -> dict:
    document = build_execution_envelope(cfg, task, reason)
    target = execution_envelope_path(cfg, task.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    task.set("execution_envelope", f"backlog/{target.name}")
    task.set("execution_envelope_digest", document["digest"])
    return document


def load_execution_envelope(cfg: "Config", task: Task) -> "dict | None":
    candidates = [execution_envelope_path(cfg, task.id)]
    configured = str(task.get("execution_envelope") or "").strip()
    if configured:
        candidates.insert(0, cfg.root / configured)
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("digest") == task.get("execution_envelope_digest"):
            return value
    return None
