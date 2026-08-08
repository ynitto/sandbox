from __future__ import annotations
# dispatch.py — 内部 dispatch request と send-request 永続キュー。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。

_SEND_REQUESTS_DIR = agent_home_subdir("", "send-requests")
_SEND_RESPONSES_DIR = agent_home_subdir("", "send-responses")
_LOOP_COMMANDS_DIR = agent_home_subdir("", "loop-commands")
_LOOP_CONTROL_DIR = agent_home_subdir("", "loop-control")
_LOOP_ADAPTIVE_DIR = agent_home_subdir("", "loop-adaptive")
_DEBOUNCE_SECONDS = 3.0
_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """一時ファイルへ書いて os.replace する（原子書換）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _isolate_invalid(path: Path) -> None:
    """破損 JSON を削除せず .invalid/ へ隔離する。"""
    try:
        invalid_dir = path.parent / ".invalid"
        invalid_dir.mkdir(parents=True, exist_ok=True)
        dest = invalid_dir / path.name
        if dest.exists():
            dest = invalid_dir / f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
        os.replace(path, dest)
    except OSError as exc:
        log.warning("invalid file の隔離に失敗しました (%s): %s", path, exc)


def _dedupe_key(entry_id: str, prompt: str) -> str:
    raw = f"{entry_id}\0{prompt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def make_dispatch_request(
    *,
    source: str,
    entry_id: str,
    prompt: str,
    cwd: str | None = None,
    priority: str = "normal",
    ack: Any = None,
    meta: dict[str, Any] | None = None,
    request_id: str | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    """内部共通 dispatch request（外部公開スキーマではない）。"""
    pri = priority if priority in _PRIORITY_ORDER else "normal"
    ts = float(created_at if created_at is not None else time.time())
    rid = request_id or uuid.uuid4().hex
    return {
        "id": rid,
        "source": source,
        "entry_id": str(entry_id),
        "prompt": str(prompt),
        "cwd": cwd,
        "priority": pri,
        "created_at": ts,
        "dedupe_key": _dedupe_key(str(entry_id), str(prompt)),
        "ack": ack,
        "meta": dict(meta or {}),
    }


def _log_dispatch(event: str, req: dict[str, Any] | None = None, **extra: Any) -> None:
    """配送ログ。prompt 本文・token・環境変数値は出さない。"""
    parts = [f"event={event}"]
    if req is not None:
        parts.extend([
            f"request_id={req.get('id', '')}",
            f"source={req.get('source', '')}",
            f"entry_id={req.get('entry_id', '')}",
        ])
    for k, v in extra.items():
        parts.append(f"{k}={v}")
    log.info(" ".join(parts))


class RequestDebouncer:
    """同一 entry_id + prompt の短時間重複を成功扱いで破棄する。"""

    def __init__(self, window_seconds: float = _DEBOUNCE_SECONDS) -> None:
        self._window = window_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_duplicate(self, dedupe_key: str, now: float | None = None) -> bool:
        t = float(now if now is not None else time.time())
        with self._lock:
            last = self._seen.get(dedupe_key)
            if last is not None and (t - last) <= self._window:
                return True
            self._seen[dedupe_key] = t
            # 古いエントリを間引く
            cutoff = t - self._window * 4
            stale = [k for k, v in self._seen.items() if v < cutoff]
            for k in stale:
                self._seen.pop(k, None)
            return False


def write_send_request(
    *,
    entry_id: str,
    prompt: str,
    cwd: str | None = None,
    priority: str = "normal",
    meta: dict[str, Any] | None = None,
    request_id: str | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """CLI send 用の永続リクエストを atomic rename で書き込む。"""
    root = Path(base_dir) if base_dir is not None else _SEND_REQUESTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    req = make_dispatch_request(
        source="send",
        entry_id=entry_id,
        prompt=prompt,
        cwd=cwd,
        priority=priority,
        meta=meta,
        request_id=request_id,
    )
    name = f"{int(req['created_at'] * 1000):013d}_{req['id']}.json"
    final = root / name
    _atomic_write_json(final, req)
    return req


def load_send_requests(base_dir: Path | None = None) -> list[dict[str, Any]]:
    """send-requests ディレクトリから未処理リクエストを古い順で読む。"""
    root = Path(base_dir) if base_dir is not None else _SEND_REQUESTS_DIR
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            _isolate_invalid(path)
            continue
        if not isinstance(data, dict) or "prompt" not in data:
            _isolate_invalid(path)
            continue
        raw_meta = data.get("meta")
        if not isinstance(raw_meta, dict):
            raw_meta = {}
        data["meta"] = dict(raw_meta)
        data.setdefault("id", path.stem)
        data.setdefault("source", "send")
        data.setdefault("entry_id", str(data.get("entry_id") or raw_meta.get("target") or ""))
        data.setdefault("priority", "normal")
        if "created_at" not in data:
            try:
                data["created_at"] = path.stat().st_mtime
            except OSError:
                continue
        data.setdefault("dedupe_key", _dedupe_key(str(data["entry_id"]), str(data["prompt"])))
        data.setdefault("ack", None)
        data["meta"]["_path"] = str(path)
        out.append(data)
    return out


def claim_send_request(req: dict[str, Any], claimant: str) -> bool:
    """未処理requestをatomic renameでこのdaemonの所有にする。"""
    meta = req.get("meta") or {}
    raw_path = meta.get("_path")
    if not raw_path:
        return False
    path = Path(str(raw_path))
    token = hashlib.sha256(str(claimant).encode("utf-8")).hexdigest()[:8]
    claimed = path.with_name(f".{path.name}.{token}.claimed")
    try:
        os.replace(path, claimed)
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("send-request claim に失敗しました (%s): %s", path, exc)
        return False
    req["meta"] = dict(meta)
    req["meta"]["_original_path"] = str(path)
    req["meta"]["_path"] = str(claimed)
    return True


def release_send_request_claim(req: dict[str, Any]) -> None:
    """受付できなかったclaimを元のqueueへ戻す。"""
    meta = req.get("meta") or {}
    claimed = meta.get("_path")
    original = meta.get("_original_path")
    if not claimed or not original:
        return
    try:
        os.replace(str(claimed), str(original))
        req["meta"]["_path"] = str(original)
    except OSError as exc:
        log.warning("send-request claim の復元に失敗しました (%s): %s", claimed, exc)


def remove_send_request_file(req: dict[str, Any]) -> None:
    path = (req.get("meta") or {}).get("_path")
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("send-request 削除に失敗しました (%s): %s", path, exc)


def send_response_path(request_id: str, base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else _SEND_RESPONSES_DIR
    return root / f"{request_id}.json"


def write_send_response(
    request_id: str,
    status: str,
    *,
    pane_id: str | None = None,
    step: int | None = None,
    max_steps: int | None = None,
    sandbox_path: str | None = None,
    reason: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    """`send --wait`用のrequest単位状態をatomicに記録する。"""
    path = send_response_path(request_id, base_dir)
    prev = read_send_response(request_id, base_dir) or {}
    data = {
        "request_id": request_id,
        "status": status,
        "pane_id": pane_id if pane_id is not None else prev.get("pane_id"),
        "step": step if step is not None else prev.get("step"),
        "max_steps": max_steps if max_steps is not None else prev.get("max_steps"),
        "sandbox_path": sandbox_path if sandbox_path is not None else prev.get("sandbox_path"),
        "reason": reason if reason is not None else prev.get("reason"),
        "updated_at": time.time(),
    }
    _atomic_write_json(path, data)
    return path


def read_send_response(request_id: str, base_dir: Path | None = None) -> dict[str, Any] | None:
    path = send_response_path(request_id, base_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def remove_send_response(request_id: str, base_dir: Path | None = None) -> None:
    try:
        send_response_path(request_id, base_dir).unlink(missing_ok=True)
    except OSError:
        pass


def workspace_control_path(workspace: str, base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else _LOOP_CONTROL_DIR
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:16]
    return root / f"{digest}.json"


def load_local_pause(workspace: str, base_dir: Path | None = None) -> bool:
    path = workspace_control_path(workspace, base_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return bool(data.get("paused"))


def set_local_pause(workspace: str, paused: bool, base_dir: Path | None = None) -> Path:
    path = workspace_control_path(workspace, base_dir)
    _atomic_write_json(path, {"paused": bool(paused), "updated_at": time.time()})
    return path


def write_loop_command(pid: int, command: str, payload: dict[str, Any] | None = None,
                       base_dir: Path | None = None) -> Path:
    root = (Path(base_dir) if base_dir is not None else _LOOP_COMMANDS_DIR) / str(pid)
    root.mkdir(parents=True, exist_ok=True)
    data = {"command": command, "payload": dict(payload or {}), "created_at": time.time()}
    name = f"{int(data['created_at'] * 1000):013d}_{uuid.uuid4().hex[:8]}.json"
    path = root / name
    _atomic_write_json(path, data)
    return path


def drain_loop_commands(pid: int, base_dir: Path | None = None) -> list[dict[str, Any]]:
    root = (Path(base_dir) if base_dir is not None else _LOOP_COMMANDS_DIR) / str(pid)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            _isolate_invalid(path)
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def load_adaptive_state(entry_id: str, base_dir: Path | None = None) -> dict[str, Any]:
    root = Path(base_dir) if base_dir is not None else _LOOP_ADAPTIVE_DIR
    path = root / f"{entry_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def save_adaptive_state(entry_id: str, state: dict[str, Any],
                        base_dir: Path | None = None) -> None:
    root = Path(base_dir) if base_dir is not None else _LOOP_ADAPTIVE_DIR
    _atomic_write_json(root / f"{entry_id}.json", state)


def next_adaptive_interval(
    cfg: dict[str, Any],
    state: dict[str, Any],
    *,
    outcome: str,
) -> tuple[float, dict[str, Any]]:
    """adaptive interval の次回秒数と更新 state を返す。outcome: activity|idle|error。"""
    min_s = max(float(cfg.get("min_interval_seconds", 60)), 1.0)
    max_s = max(float(cfg.get("max_interval_seconds", 1800)), min_s)
    factor = max(float(cfg.get("backoff_factor", 1.5)), 1.0)
    threshold = max(int(cfg.get("idle_threshold", 3)), 1)
    jitter = max(0.0, min(float(cfg.get("jitter", 0.2)), 1.0))
    new_state = dict(state)
    current = float(new_state.get("interval_seconds") or min_s)
    idle_count = int(new_state.get("idle_count") or 0)

    if outcome == "activity":
        interval = min_s
        idle_count = 0
    elif outcome == "error":
        interval = min(min_s * factor, max_s)
        # error は idle 扱いにしない
    else:
        idle_count += 1
        if idle_count >= threshold:
            interval = min(current * factor, max_s)
        else:
            interval = current

    if jitter > 0:
        # ponytail: 決定論テスト向けに中央値付近の疑似ジッタ（uuid 由来）
        u = (int(uuid.uuid4().hex[:8], 16) / 0xFFFFFFFF) * 2 - 1
        interval = max(min_s, min(max_s, interval * (1.0 + u * jitter)))

    new_state["interval_seconds"] = interval
    new_state["idle_count"] = idle_count
    new_state["last_outcome"] = outcome
    new_state["updated_at"] = time.time()
    return interval, new_state
