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
    selection_policies = {}
    for workload, settings in (control_doc.get("workloads") or {}).items():
        policy = settings.get("selection_policy") if isinstance(settings, dict) else None
        if isinstance(policy, dict):
            selection_policies[str(workload)] = policy
    return {
        "control_version": int(control_doc.get("version") or 1),
        "control_revision": int(control_doc.get("revision") or 0),
        "valid_until": str(control_doc.get("valid_until") or ""),
        "selection_policies": selection_policies,
    }


def build_execution_envelope(cfg: "Config", task: Task, reason: str = "", *, approved: bool = True) -> dict:
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
        "approval": {
            "status": "approved" if approved else "proposed",
            "actor": cfg.actor if approved else "",
            "reason": str(reason or ""),
        },
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
    if approved:
        document["approved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest_source = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    document["digest"] = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    return document


def _save_execution_envelope(cfg: "Config", task: Task, document: dict) -> dict:
    target = execution_envelope_path(cfg, task.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    task.set("execution_envelope", f"backlog/{target.name}")
    task.set("execution_envelope_digest", document["digest"])
    return document


def propose_execution_envelope(cfg: "Config", task: Task, reason: str = "") -> dict:
    return _save_execution_envelope(
        cfg, task, build_execution_envelope(cfg, task, reason, approved=False))


def approve_execution_envelope(cfg: "Config", task: Task, reason: str = "") -> dict:
    return _save_execution_envelope(cfg, task, build_execution_envelope(cfg, task, reason))


def load_execution_envelope(cfg: "Config", task: Task) -> "dict | None":
    candidates = [execution_envelope_path(cfg, task.id)]
    configured = str(task.get("execution_envelope") or "").strip()
    if configured:
        configured_path = Path(configured)
        candidates.insert(0, configured_path if configured_path.is_absolute()
                          else cfg.backlog.parent / configured_path)
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("digest") == task.get("execution_envelope_digest"):
            return value
    return None


def snapshot_execution_envelope_to_run(cfg: "Config", task: Task, run_id: str,
                                       use_git: bool = False) -> bool:
    """承認済み Envelope を run meta へ最初の一度だけ転記する。

    run の実行中に control や backlog が変わっても、監査画面が「その run が承認時に従った
    契約」を表示できるようにする。既に snapshot があれば絶対に更新しない。
    """
    meta_path = _flow_run_bus(cfg, use_git) / "runs" / str(run_id) / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(meta, dict) or isinstance(meta.get("execution_envelope"), dict):
        return False
    envelope = load_execution_envelope(cfg, task)
    if not envelope or (envelope.get("approval") or {}).get("status") != "approved":
        return False
    meta["execution_envelope"] = envelope
    meta["execution_envelope_digest"] = str(envelope.get("digest") or "")
    write_json_atomic(str(meta_path), meta)
    return True


def archive_execution_envelope(cfg: "Config", task: Task, archived_task_path: Path,
                               document: "dict | None" = None) -> "Path | None":
    """完了記録と同じstemへEnvelopeを移し、backlog側のsidecarを退役させる。"""
    document = document or load_execution_envelope(cfg, task)
    if not document:
        return None
    target = archived_task_path.with_suffix(".envelope.json")
    write_json_atomic(str(target), document)
    source = execution_envelope_path(cfg, task.id)
    if source != target:
        with contextlib.suppress(OSError):
            source.unlink()
    return target
