from __future__ import annotations
# coordination.py — 複数 PC の controller・実行権・夜間 drain。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。

from zoneinfo import ZoneInfo

_DRAIN_REQUESTED = threading.Event()


def request_drain(cfg: "Config") -> None:
    """新規 claim を止め、controller を即時解放する。"""
    _DRAIN_REQUESTED.set()
    if getattr(cfg, "coordination", "") == "git-cas":
        release_controller_lease(cfg)


def _transaction_materialize(git: "DirectStateGit", branch: str, old: str, new: str) -> bool:
    """成功した remote CAS を、安全に fast-forward できるローカル clone へ反映する。"""
    local = git._git("rev-parse", "-q", "--verify", f"refs/heads/{branch}").stdout.strip()
    if local and git._git("merge-base", "--is-ancestor", local, old).returncode != 0:
        return False
    if not git._cas_branch(branch, new, local):
        return False
    top = git._git("rev-parse", "--show-toplevel").stdout.strip()
    git._materialize(local or old, new, top or str(git.root))
    return True


def state_transaction(cfg: "Config", mutate, message: str = "coordination update") -> bool:
    """remote HEAD を親に変更を作り、fast-forward push を CAS として使う。

    mutate は一時 worktree を受け取り、変更を採用するなら truthy、競合で中止するなら falsy を返す。
    push 競合時だけ最新 HEAD から作り直す。Git が使えない場合は fail closed で False。
    """
    if getattr(cfg, "coordination", "") != "git-cas":
        return False
    root = Path(cfg.backlog).parent
    git = DirectStateGit(root, interval=0.0)
    branch = str(getattr(cfg, "state_repo_branch", "main") or "main")
    if not (root / ".git").exists() or not git._has_remote():
        return False
    with _file_lock(git._sync_lock_path()):
        git._ensure_identity()
        for _ in range(max(1, int(getattr(cfg, "coordination_retries", 3) or 3))):
            fetched = git._git("fetch", "-q", "origin", branch)
            if fetched.returncode != 0:
                return False
            old = git._git("rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()
            local = git._git("rev-parse", "-q", "--verify", f"refs/heads/{branch}").stdout.strip()
            if not old or (local and git._git("merge-base", "--is-ancestor", local, old).returncode != 0):
                return False
            tmp = Path(tempfile.mkdtemp(prefix="agent-project-txn-"))
            worktree = tmp / "worktree"
            try:
                if git._git("worktree", "add", "--detach", "-q", str(worktree), old).returncode != 0:
                    return False
                if not mutate(worktree):
                    return False
                add = subprocess.run(["git", "-C", str(worktree), "add", "-A"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace")
                if add.returncode != 0:
                    return False
                changed = subprocess.run(["git", "-C", str(worktree), "diff", "--cached", "--quiet"])
                if changed.returncode == 0:
                    return True
                commit = subprocess.run(
                    ["git", "-C", str(worktree), "-c", "user.email=agent-project@local",
                     "-c", "user.name=agent-project", "commit", "-qm", f"agent-project: {message}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                if commit.returncode != 0:
                    return False
                new = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                                     capture_output=True, text=True, encoding="utf-8").stdout.strip()
                push = subprocess.run(["git", "-C", str(worktree), "push", "-q", "origin",
                                       f"HEAD:refs/heads/{branch}"], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")
                if push.returncode == 0:
                    return _transaction_materialize(git, branch, old, new)
            finally:
                git._git("worktree", "remove", "--force", str(worktree))
                shutil.rmtree(tmp, ignore_errors=True)
    return False


def controller_path(root: Path) -> Path:
    return root / "coordination" / "controller.json"


def renew_controller_lease(cfg: "Config", at: "datetime | None" = None) -> bool:
    """期限切れ lease を獲得するか、自ノードの lease を更新する。"""
    node = str(getattr(cfg, "node", "") or "").strip()
    if not node:
        return False
    if availability_state(cfg, at) != "active":
        release_controller_lease(cfg, at)
        return False
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def mutate(root: Path) -> bool:
        path = controller_path(root)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        holder = str(current.get("node", "") or "")
        expires = float(current.get("lease_until", 0.0) or 0.0)
        tolerance = float(getattr(cfg, "clock_skew_tolerance_sec", 30.0) or 0.0)
        if holder and holder != node and now.timestamp() <= expires + tolerance:
            return False
        generation = int(current.get("generation", 0) or 0) + (holder != node)
        record = {
            "schema_version": 1, "node": node, "generation": generation,
            "updated_iso": now.isoformat(),
            "lease_until": now.timestamp() + float(getattr(cfg, "controller_lease_sec", 120.0)),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    return state_transaction(cfg, mutate, "controller lease")


def release_controller_lease(cfg: "Config", at: "datetime | None" = None) -> bool:
    """自ノードが保持する lease を期限待ちせず解放する。"""
    node = str(getattr(cfg, "node", "") or "").strip()
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def mutate(root: Path) -> bool:
        path = controller_path(root)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if str(current.get("node", "") or "") != node:
            return False
        current.update({"node": "", "lease_until": now.timestamp(),
                        "updated_iso": now.isoformat(), "released_by": node})
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    return bool(node) and state_transaction(cfg, mutate, "release controller lease")


def start_controller_heartbeat(cfg: "Config") -> threading.Event:
    """長い act 中も controller lease を更新する daemon thread を開始する。"""
    stop = threading.Event()
    cfg._controller_active = renew_controller_lease(cfg)

    def heartbeat() -> None:
        interval = max(0.01, float(getattr(cfg, "controller_heartbeat_sec", 30.0) or 30.0))
        while not stop.wait(interval):
            if _DRAIN_REQUESTED.is_set():
                release_controller_lease(cfg)
                cfg._controller_active = False
            else:
                cfg._controller_active = renew_controller_lease(cfg)

    threading.Thread(target=heartbeat, name="agent-project-controller-heartbeat", daemon=True).start()
    return stop


def claim_distributed_task(cfg: "Config", task_id: str,
                           at: "datetime | None" = None) -> "str | None":
    """ready タスクを doing へ CAS 遷移し、結果確定に必要な fencing token を返す。"""
    node = str(getattr(cfg, "node", "") or "").strip()
    if _DRAIN_REQUESTED.is_set() or not node or availability_state(cfg, at) != "active":
        return None
    claimed: dict[str, str] = {}
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def mutate(root: Path) -> bool:
        path = root / "backlog" / f"{task_id}.md"
        try:
            task = parse_task(path.read_text(encoding="utf-8"), task_id)
        except OSError:
            return False
        if task.norm_status() not in CONSUMABLE:
            return False
        assigned = str(task.get("node") or getattr(cfg, "default_node", "") or "").strip()
        if assigned and assigned != node:
            return False
        generation = int(task.get("claim_generation") or 0) + 1
        token = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        task.status = "doing"
        if not task.get("node"):
            task.set("node", node)
            task.set("node_source", "claim")
        task.set("claim_owner", node)
        task.set("claim_token", token)
        task.set("claim_generation", str(generation))
        task.set("claimed_at", now.isoformat())
        path.write_text(serialize_task(task), encoding="utf-8")
        claimed["token"] = token
        return True

    if not state_transaction(cfg, mutate, f"claim {task_id}"):
        return None
    return claimed.get("token")


def _remote_task(cfg: "Config", task_id: str) -> "Task | None":
    root = Path(cfg.backlog).parent
    git = DirectStateGit(root, interval=0.0)
    branch = str(getattr(cfg, "state_repo_branch", "main") or "main")
    if git._git("fetch", "-q", "origin", branch).returncode != 0:
        return None
    spec = f"refs/remotes/origin/{branch}:backlog/{task_id}.md"
    result = git._git("show", spec)
    return parse_task(result.stdout, task_id) if result.returncode == 0 else None


def validate_distributed_claim(cfg: "Config", task: "Task") -> bool:
    """remote 正本が同じ owner/token/generation の doing である場合だけ settle を許可する。"""
    if getattr(cfg, "coordination", "") != "git-cas":
        return True
    current = _remote_task(cfg, task.id)
    if current is None or current.norm_status() != "doing":
        return False
    return all(str(current.get(key) or "") == str(task.get(key) or "")
               for key in ("claim_owner", "claim_token", "claim_generation"))


def refresh_distributed_task(cfg: "Config", task_id: str) -> bool:
    """fence 敗北時、stale なローカル task を remote 正本へ戻す。"""
    current = _remote_task(cfg, task_id)
    if current is None:
        return False
    persist_task(cfg, current)
    return True


def allocate_distributed_tasks(cfg: "Config", at: "datetime | None" = None) -> "dict[str, str]":
    """active ノードの ready+doing 件数が最小になるよう、未割当 ready を決定的に配る。"""
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    assigned: dict[str, str] = {}

    def mutate(root: Path) -> bool:
        # CAS push の競合で mutate が再実行された場合、失敗した試行の結果を返さない。
        assigned.clear()
        eligible: set[str] = set()
        status_dir = root / "status"
        for path in sorted(status_dir.glob("*.json")) if status_dir.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                updated = datetime.fromisoformat(str(record["updated_iso"]).replace("Z", "+00:00"))
                fresh = float(record.get("fresh_after_sec", 120.0) or 120.0)
                node = str(record.get("node", "") or "").strip()
            except (KeyError, OSError, TypeError, ValueError):
                continue
            if node and str(record.get("availability", "active")) == "active" \
                    and (now - updated.astimezone(timezone.utc)).total_seconds() <= fresh:
                eligible.add(node)
        own = str(getattr(cfg, "node", "") or "").strip()
        if own and availability_state(cfg, now) == "active":
            eligible.add(own)
        if not eligible:
            return False
        tasks = sorted(load_tasks(root / "backlog"), key=lambda task: task.id)
        load = {node: 0 for node in eligible}
        for task in tasks:
            node = str(task.get("node") or "").strip()
            if node in load and task.norm_status() in (*CONSUMABLE, "doing"):
                load[node] += 1
        changed = False
        for task in tasks:
            if task.norm_status() not in CONSUMABLE:
                continue
            node = str(task.get("node") or "").strip()
            source = str(task.get("node_source") or "").strip()
            if node and (source != "auto" or node in eligible):
                continue
            target = min(eligible, key=lambda name: (load[name], name))
            task.set("node", target)
            task.set("node_source", "auto")
            (root / "backlog" / f"{task.id}.md").write_text(serialize_task(task), encoding="utf-8")
            load[target] += 1
            assigned[task.id] = target
            changed = True
        return changed

    return assigned if state_transaction(cfg, mutate, "allocate ready tasks") else {}


def requeue_draining_tasks(cfg: "Config") -> "list[str]":
    """計画停止の期限で、自ノード所有doingをretry据え置きのreadyへCASで戻す。"""
    node = str(getattr(cfg, "node", "") or "").strip()
    requeued: list[str] = []

    def mutate(root: Path) -> bool:
        requeued.clear()
        for task in sorted(load_tasks(root / "backlog"), key=lambda item: item.id):
            if task.norm_status() != "doing" or str(task.get("claim_owner") or "") != node:
                continue
            task.status = "ready"
            task.set("claim_owner", "")
            task.set("claim_token", hashlib.sha256(os.urandom(32)).hexdigest()[:32])
            task.set("claim_generation", str(int(task.get("claim_generation") or 0) + 1))
            task.set("drain_requeued_at", datetime.now(timezone.utc).isoformat())
            (root / "backlog" / f"{task.id}.md").write_text(serialize_task(task), encoding="utf-8")
            requeued.append(task.id)
        return bool(requeued)

    if not node or not state_transaction(cfg, mutate, "requeue tasks for planned shutdown"):
        return []
    for task_id in requeued:
        release_claim(cfg, Task(id=task_id, title=task_id))
    return list(requeued)


def availability_state(cfg: "Config", at: "datetime | None" = None) -> str:
    """ノードのローカル時刻を active / draining / stopped に分類する。"""
    availability = getattr(cfg, "availability", {}) or {}
    daily_stop = str(availability.get("daily_stop", "") or "").strip()
    if not daily_stop:
        return "active"
    try:
        hour, minute = (int(part) for part in daily_stop.split(":"))
        zone = ZoneInfo(str(availability.get("timezone", "UTC") or "UTC"))
        local = (at or datetime.now(timezone.utc)).astimezone(zone)
        stop_second = hour * 3600 + minute * 60
        now_second = local.hour * 3600 + local.minute * 60 + local.second
        drain = max(0, int(availability.get("drain_before_sec", 0) or 0))
    except (KeyError, TypeError, ValueError):
        return "invalid"
    if now_second >= stop_second:
        return "stopped"
    return "draining" if now_second >= stop_second - drain else "active"


def shutdown_due(cfg: "Config", at: "datetime | None" = None) -> bool:
    """daily_stop 後の grace を使い切ったかをノードのローカル時刻で判定する。"""
    availability = getattr(cfg, "availability", {}) or {}
    daily_stop = str(availability.get("daily_stop", "") or "").strip()
    if not daily_stop:
        return False
    try:
        hour, minute = (int(part) for part in daily_stop.split(":"))
        zone = ZoneInfo(str(availability.get("timezone", "UTC") or "UTC"))
        local = (at or datetime.now(timezone.utc)).astimezone(zone)
        now_second = local.hour * 3600 + local.minute * 60 + local.second
        deadline = hour * 3600 + minute * 60 + max(
            0, int(availability.get("shutdown_grace_sec", 300) or 0))
    except (KeyError, TypeError, ValueError):
        return False
    return now_second >= deadline


def start_availability_monitor(cfg: "Config") -> threading.Event:
    """drain開始とshutdown grace超過をact中にも監視する。"""
    stop = threading.Event()

    def monitor() -> None:
        while not stop.wait(1.0):
            state = availability_state(cfg)
            if state in ("draining", "stopped") and not _DRAIN_REQUESTED.is_set():
                request_drain(cfg)
            if shutdown_due(cfg):
                requeue_draining_tasks(cfg)
                with contextlib.suppress(OSError):
                    os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=monitor, name="agent-project-availability", daemon=True).start()
    return stop
