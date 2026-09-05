from __future__ import annotations
# repository_ui.py — repository 単位の実行 UI が使う機械可読の読み書き境界。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ exec 合成する。
# ---------------------------------------------------------------------------

_REPOSITORY_WORKFLOW_ROOT = ".statemachine"
_REPOSITORY_HISTORY_KEEP = 200
_REPOSITORY_HISTORY_TRIM_AT = 400
_SIMPLE_CRON_RE = re.compile(
    r"^(?P<minute>\d{1,2})\s+(?P<hour>\d{1,2})\s+\*\s+\*\s+(?P<days>\*|[0-7](?:,[0-7])*)$"
)
_REPOSITORY_RUNTIME_VALUES = {
    "last_output", "history", "step_count", "today", "now", "check_ok", "context",
}


def _repository_root(value: "str | Path") -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"作業ディレクトリが見つかりません: {root}")
    return root


def _repository_schedule(entry: "dict[str, Any] | None") -> "dict[str, Any] | None":
    if not entry:
        return None
    base = {
        "entryName": str(entry.get("name") or ""),
        "enabled": entry.get("enabled") is not False,
        "input": dict(entry.get("input") or {}) if isinstance(entry.get("input"), dict) else {},
        "agentCli": str(entry.get("agent_cli") or "") or None,
        "model": str(entry.get("model") or "") or None,
    }
    try:
        normalized = validate_entries([entry])
        next_run_at = normalized[0].get("next_run_at") if normalized else None
    except (TypeError, ValueError):
        next_run_at = None
    if next_run_at is not None and math.isfinite(float(next_run_at)):
        base["nextAt"] = (_dt.datetime.fromtimestamp(float(next_run_at), tz=_dt.timezone.utc)
                          .isoformat().replace("+00:00", "Z"))
    else:
        base["nextAt"] = None
    interval = entry.get("interval_minutes")
    if interval is not None:
        return {**base, "kind": "interval", "minutes": int(interval), "advanced": False}
    cron = str(entry.get("cron") or "").strip()
    match = _SIMPLE_CRON_RE.fullmatch(cron)
    if not match:
        return {**base, "kind": "advanced", "expression": cron, "advanced": True}
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    days = match.group("days")
    if hour > 23 or minute > 59:
        return {**base, "kind": "advanced", "expression": cron, "advanced": True}
    result = {**base, "kind": "daily" if days == "*" else "weekly",
              "time": f"{hour:02d}:{minute:02d}", "advanced": False}
    if days != "*":
        result["days"] = sorted({0 if int(day) == 7 else int(day) for day in days.split(",")})
    return result


def _repository_parameter_names(data: dict[str, Any]) -> list[str]:
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    required = {str(key) for key, value in context.items()
                if value is None or str(value).strip() == ""}
    defaults = {str(key) for key, value in context.items()
                if value is not None and str(value).strip() != ""}
    outputs: set[str] = set()
    templates: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            output_key = value.get("output_key")
            if isinstance(output_key, str) and output_key.strip():
                outputs.add(output_key.strip().split(".", 1)[0])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for match in re.finditer(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}", value):
                templates.add(match.group(1))

    visit(data.get("states") or {})
    for name in templates:
        top = name.split(".", 1)[0]
        if top not in _REPOSITORY_RUNTIME_VALUES and top not in outputs and top not in defaults:
            required.add(name)
    return sorted(required)


def _repository_workflow_summary(workflow_file: Path) -> dict[str, Any]:
    if yaml is None:
        raise ValueError("YAML を読むには PyYAML が必要です")
    try:
        data = yaml.safe_load(workflow_file.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"定義を読めません: {workflow_file}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"定義の形式が不正です: {workflow_file}")
    return {
        "name": str(data.get("name") or workflow_file.parent.name),
        "description": str(data.get("description") or ""),
        "parameters": _repository_parameter_names(data),
    }


def _repository_daemon(root: Path) -> dict[str, Any]:
    pid = _find_running_daemon(root)
    if pid is None:
        return {"running": False, "paused": False, "pid": None,
                "activeCount": 0, "queueDepth": 0}
    state = next((item for item in _read_all_states()
                  if int(item.get("pid") or 0) == int(pid)), {})
    return {
        "running": True,
        "paused": str(state.get("run_state") or "run") == "paused",
        "pid": int(pid),
        "activeCount": int(state.get("active_count") or 0),
        "queueDepth": int(state.get("queue_depth") or 0),
    }


def _repository_workflow_path(root: Path, reference: str) -> tuple[str, Path]:
    try:
        normalized = _loopentry.workflow_reference(reference)
    except _loopentry.LoopEntryError as exc:
        raise ValueError(str(exc)) from exc
    target = (root / normalized).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("ステートマシン定義は作業ディレクトリの内側に置いてください") from exc
    if not target.is_file():
        raise ValueError(f"ステートマシン定義が見つかりません: {normalized}")
    return normalized, target


def _repository_schedule_fields(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("実行予定を選んでください")
    kind = str(value.get("kind") or "")
    if kind == "interval":
        try:
            minutes = int(value.get("minutes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("実行間隔は1分以上の整数で入力してください") from exc
        if minutes < 1:
            raise ValueError("実行間隔は1分以上の整数で入力してください")
        return {"interval_minutes": minutes}
    if kind not in ("daily", "weekly"):
        raise ValueError("画面で設定できる実行予定ではありません")
    match = re.fullmatch(r"(\d{2}):(\d{2})", str(value.get("time") or ""))
    if not match:
        raise ValueError("実行時刻を時:分で入力してください")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("実行時刻が範囲外です")
    days = "*"
    if kind == "weekly":
        raw_days = value.get("days")
        if not isinstance(raw_days, list):
            raise ValueError("実行する曜日を選んでください")
        try:
            normalized_days = sorted({int(day) for day in raw_days})
        except (TypeError, ValueError) as exc:
            raise ValueError("実行する曜日が不正です") from exc
        if not normalized_days or any(day < 0 or day > 6 for day in normalized_days):
            raise ValueError("実行する曜日を選んでください")
        days = ",".join(str(day) for day in normalized_days)
    return {"cron": f"{minute} {hour} * * {days}"}


def _repository_write_config(path: Path, data: dict[str, Any]) -> None:
    if path.suffix.lower() in (".yaml", ".yml") and yaml is None:
        raise ValueError("設定の保存には PyYAML が必要です")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        if path.suffix.lower() == ".json":
            body = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        else:
            body = yaml.safe_dump(data, allow_unicode=True, default_flow_style=False,
                                  sort_keys=False)
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _repository_history_file(root: Path) -> Path:
    directory = agent_home_subdir("AGENT_LOOP_RUN_HISTORY_DIR", "run-history")
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
    return directory / f"{digest}.jsonl"


def _repository_history(root: Path, workflow: str, limit: int) -> list[dict[str, Any]]:
    try:
        lines = _repository_history_file(root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("workflow") == workflow:
            records.append(record)
    return records[-max(1, int(limit)):][::-1]


def record_repository_run(cwd: "str | Path", record: Any) -> dict[str, Any]:
    """statemachine 実行の短い索引を best-effort で repository 履歴へ追記する。"""
    root = _repository_root(cwd)
    if not isinstance(record, dict):
        raise ValueError("実行記録が不正です")
    workflow = _loopentry.workflow_reference(record.get("workflow"))
    allowed = (
        "runId", "entryName", "source", "startedAt", "finishedAt", "ok", "escalate",
        "finalState", "stopReason", "error", "logFile", "agentCli", "model",
    )
    normalized = {key: record[key] for key in allowed if key in record}
    normalized["runId"] = str(normalized.get("runId") or uuid.uuid4().hex)
    normalized["workflow"] = workflow
    normalized["source"] = "scheduled" if normalized.get("source") == "scheduled" else "manual"
    normalized["ok"] = normalized.get("ok") is True
    normalized["escalate"] = normalized.get("escalate") is True
    file = _repository_history_file(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
        if len(lines) > _REPOSITORY_HISTORY_TRIM_AT:
            temp = file.with_name(f".{file.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
            temp.write_text("\n".join(lines[-_REPOSITORY_HISTORY_KEEP:]) + "\n",
                            encoding="utf-8")
            os.replace(temp, file)
    except OSError:
        pass
    return normalized


def repository_run_log(cwd: "str | Path", payload: Any, max_bytes: int = 262_144) -> dict[str, Any]:
    """履歴が指す repository 内の実行ログだけを末尾から読む。"""
    root = _repository_root(cwd)
    if not isinstance(payload, dict):
        raise ValueError("ログの指定が不正です")
    workflow = _loopentry.workflow_reference(payload.get("workflow"))
    run_id = str(payload.get("runId") or "")
    record = next((item for item in _repository_history(root, workflow, _REPOSITORY_HISTORY_KEEP)
                   if str(item.get("runId") or "") == run_id), None)
    if not record:
        raise ValueError("実行履歴に対応するログが見つかりません")
    raw_path = str(record.get("logFile") or "").strip()
    if not raw_path:
        raise ValueError("この実行にはログがありません")
    log_root = (root / ".statemachine-use" / "logs").resolve()
    log_file = Path(raw_path).expanduser().resolve()
    try:
        log_file.relative_to(log_root)
    except ValueError as exc:
        raise ValueError("リポジトリ外のログは開けません") from exc
    if not log_file.is_file():
        raise ValueError("実行ログが見つかりません")
    limit = max(1, min(int(max_bytes), 1_048_576))
    size = log_file.stat().st_size
    with log_file.open("rb") as stream:
        if size > limit:
            stream.seek(size - limit)
        content = stream.read(limit)
    return {"text": content.decode("utf-8", errors="replace"), "truncated": size > limit}


def update_repository_schedule(cwd: "str | Path", payload: Any) -> dict[str, Any]:
    """UI が扱う単純な statemachine 定期設定だけを検査して保存する。"""
    root = _repository_root(cwd)
    if not isinstance(payload, dict):
        raise ValueError("定期実行の内容が不正です")
    workflow, workflow_file = _repository_workflow_path(root, str(payload.get("workflow") or ""))
    summary = _repository_workflow_summary(workflow_file)
    input_value = payload.get("input") or {}
    if not isinstance(input_value, dict):
        raise ValueError("実行条件は名前と値の組です")
    missing = [key for key in summary["parameters"]
               if key not in input_value or input_value[key] is None
               or str(input_value[key]).strip() == ""]
    if missing:
        raise ValueError("実行条件を入力してください: " + ", ".join(missing))

    path = _prompt_file(str(root))
    data = _read_config_file(path) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError("agent-loop の設定はマップである必要があります")
    raw_entries = data.get("prompts") or []
    if not isinstance(raw_entries, list):
        raise ValueError("agent-loop の prompts は配列である必要があります")
    entries = [dict(entry) if isinstance(entry, dict) else entry for entry in raw_entries]
    matches: list[int] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("statemachine"):
            continue
        try:
            spec = _loopentry.statemachine_spec(entry)
        except _loopentry.LoopEntryError:
            continue
        if spec and spec["workflow"] == workflow:
            matches.append(index)
    if len(matches) > 1:
        raise ValueError("同じステートマシンの定期設定が複数あります。設定ファイルで整理してください")

    entry = dict(entries[matches[0]]) if matches else {}
    entry.pop("cron", None)
    entry.pop("interval_minutes", None)
    entry.update({
        "name": str(payload.get("entryName") or entry.get("name")
                    or f"{summary['name']} の定期実行"),
        "statemachine": workflow,
        "input": {str(key): value for key, value in input_value.items()},
        **_repository_schedule_fields(payload.get("schedule")),
        "enabled": payload.get("enabled") is not False,
    })
    if str(payload.get("agentCli") or "").strip():
        entry["agent_cli"] = str(payload["agentCli"]).strip()
    if "model" in payload:
        model_value = str(payload.get("model") or "").strip()
        if model_value:
            entry["model"] = model_value
        else:
            entry.pop("model", None)
    try:
        _loopentry.statemachine_spec(entry)
        validate_entries([entry])
    except (_loopentry.LoopEntryError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if matches:
        entries[matches[0]] = entry
    else:
        entries.append(entry)
    data["prompts"] = entries
    _repository_write_config(path, data)

    daemon_pid = _find_running_daemon(root)
    if daemon_pid is not None:
        write_loop_command(daemon_pid, "reload", {
            "entries": entries,
            "external_panes": data.get("external_panes") or [],
            "environment_handoff": normalize_environment_handoff(data),
        })
    return {
        "saved": True,
        "applied": False,
        "daemonRunning": daemon_pid is not None,
        "workflow": workflow,
    }


def repository_snapshot(cwd: "str | Path", history_limit: int = 20) -> dict[str, Any]:
    """workflow、定期 entry、daemon 状態を repository 単位で返す。"""
    root = _repository_root(cwd)
    entries = _load_prompt_file_data(str(root)).get("prompts") or []
    by_workflow: dict[str, dict[str, Any]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            spec = _loopentry.statemachine_spec(entry)
        except _loopentry.LoopEntryError:
            continue
        if spec:
            by_workflow[str(spec["workflow"])] = entry

    machines = []
    base = root / _REPOSITORY_WORKFLOW_ROOT
    if base.is_dir():
        for directory in sorted((item for item in base.iterdir() if item.is_dir()),
                                key=lambda item: item.name):
            workflow_file = directory / "workflow.yaml"
            if not workflow_file.is_file():
                continue
            reference = workflow_file.relative_to(root).as_posix()
            summary = _repository_workflow_summary(workflow_file)
            machines.append({
                "machine": directory.name,
                "workflow": reference,
                **summary,
                "schedule": _repository_schedule(by_workflow.get(reference)),
                "active": None,
                "history": _repository_history(root, reference, history_limit),
            })
    return {"available": True, "machines": machines, "daemon": _repository_daemon(root)}


def cmd_repository_inspect(args: argparse.Namespace, cwd: Path) -> None:
    root = Path(getattr(args, "dir", None) or cwd)
    result = repository_snapshot(root)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
        return
    daemon = result["daemon"]
    print(f"agent-loop: {'実行中' if daemon['running'] else '停止中'}")
    for machine in result["machines"]:
        schedule = machine.get("schedule") or {}
        label = "定期実行" if schedule.get("enabled") else "手動"
        print(f"- {machine['name']} ({label})")


def cmd_repository_schedule(args: argparse.Namespace, cwd: Path) -> None:
    raw = sys.stdin.read(1_048_577)
    if len(raw.encode("utf-8")) > 1_048_576:
        print(json.dumps({"ok": False, "error": "入力が大きすぎます"}, ensure_ascii=False))
        sys.exit(1)
    try:
        request = json.loads(raw)
        result = update_repository_schedule(Path(getattr(args, "dir", None) or cwd), request)
    except (TypeError, ValueError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[agent-loop] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("定期実行を保存しました")


def cmd_repository_log(args: argparse.Namespace, cwd: Path) -> None:
    raw = sys.stdin.read(1_048_577)
    try:
        if len(raw.encode("utf-8")) > 1_048_576:
            raise ValueError("入力が大きすぎます")
        request = json.loads(raw)
        result = repository_run_log(Path(getattr(args, "dir", None) or cwd), request)
    except (TypeError, ValueError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[agent-loop] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["text"])


def cmd_repository_statemachine(args: argparse.Namespace, cwd: Path) -> None:
    started_at = _utc_iso()

    def record(work_dir, workflow, result, agent, plan):
        record_repository_run(work_dir, {
            "workflow": workflow,
            "entryName": str(getattr(args, "entry", "") or ""),
            "source": "manual",
            "startedAt": started_at,
            "finishedAt": _utc_iso(),
            "ok": result.get("ok") is True,
            "escalate": result.get("escalate") is True,
            "finalState": result.get("finalState") or "",
            "stopReason": result.get("stopReason") or "",
            "error": result.get("error") or "",
            "logFile": result.get("logFile") or "",
            "agentCli": (agent or {}).get("cli") or "",
            "model": (agent or {}).get("model") or "",
        })

    _harness_statemachine.cmd_statemachine(args, cwd, result_recorder=record)
