from __future__ import annotations
# coordination.py — 複数 PC の controller・実行権・夜間 drain。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。

from zoneinfo import ZoneInfo

_DRAIN_REQUESTED = threading.Event()


_ORIGIN_PROBE_TTL_SEC = 30.0
_ORIGIN_PROBE: "dict[str, tuple[float, bool]]" = {}


def _has_origin(root: Path) -> bool:
    """状態ルートに origin があるか。`git remote get-url` はサブプロセスなので短命キャッシュを
    挟む——判定はタスクごと・poll ごとのホットパスから呼ばれる（`claim_task`・`has_work`）。
    origin は実行中にほぼ変わらないが、W1-7 の `_ensure_direct_state_git` が後から付けうるので
    恒久キャッシュにはしない。"""
    key = str(root)
    cached = _ORIGIN_PROBE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _ORIGIN_PROBE_TTL_SEC:
        return cached[1]
    value = (root / ".git").exists() and DirectStateGit(root, interval=0.0)._has_remote()
    _ORIGIN_PROBE[key] = (now, value)
    return value


def _peer_nodes(cfg: "Config", at: "datetime | None" = None) -> "set[str]":
    """自分以外に生存が観測されているノード名。

    `status/<node>.json` は同期対象の状態ファイルなので、**リモートが落ちていても
    「最後に同期できた時点のピア集合」がローカルに残る**。これが「今この状態を取り合う
    相手がいるか」を offline でも判定できる根拠になる。

    生存判定は鮮度（`updated_iso` + `fresh_after_sec`）だけで見る。`allocate_distributed_tasks`
    は配布先を選ぶために `availability == "active"` も要求するが、こちらは「排他が要るか」の
    判定なので、drain 中のノードも**まだ claim を握っている**以上ピアとして数える。"""
    root = Path(cfg.backlog).parent
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    own = str(getattr(cfg, "node", "") or "").strip()
    peers: set[str] = set()
    status_dir = root / "status"
    for path in sorted(status_dir.glob("*.json")) if status_dir.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(str(record["updated_iso"]).replace("Z", "+00:00"))
            fresh = float(record.get("fresh_after_sec", 120.0) or 120.0)
            node = str(record.get("node", "") or "").strip()
        except (KeyError, OSError, TypeError, ValueError):
            continue
        if node and node != own \
                and (now - updated.astimezone(timezone.utc)).total_seconds() <= fresh:
            peers.add(node)
    return peers


def _coordination_active(cfg: "Config") -> bool:
    """複数 PC の CAS 制御を通すべきか（実装計画 W1-8。`coordination:` 設定キーは廃止）。

    判定は「origin があるか」ではなく **CAS が守るべき不変条件が今そこにあるか** で決める。
    CAS が防ぐのは「他ノードが同じタスクを同時に取ること」なので、取り合う相手がいなければ
    通す意味が無い。origin の有無を代理指標にすると、W1-7 で `state_git:` から origin が
    自動設定されるようになった以降は**単独 PC のプロジェクトまで分散モードに入り**、
    リモートが落ちているだけで CAS が全て失敗して 1 件も claim できなくなる（実害）。

    したがって: origin があり、かつ自分以外の生存ノードが観測されているときだけ True。
    ピアが現れれば次のパスから自動で CAS に入るので「設定より規約」の狙いは保たれる。"""
    return _has_origin(Path(cfg.backlog).parent) and bool(_peer_nodes(cfg))


def request_drain(cfg: "Config") -> None:
    """新規 claim を止め、controller を即時解放する。"""
    _DRAIN_REQUESTED.set()
    if _coordination_active(cfg):
        release_controller_lease(cfg)


def state_transaction(cfg: "Config", mutate, message: str = "coordination update") -> bool:
    """remote HEAD を親に変更を作り、fast-forward push を CAS として使う。

    mutate は一時 worktree を受け取り、変更を採用するなら truthy、競合で中止するなら falsy を返す。
    push 競合時だけ最新 HEAD から作り直す。CAS を通す状況でなければ（`_coordination_active`
    が False＝origin が無い／取り合うピアがいない）fail closed で False
    （実装計画 W1-8: coordination は設定キーでなく観測で決まる）。
    """
    if not _coordination_active(cfg):
        return False
    root = Path(cfg.backlog).parent
    git = DirectStateGit(root, interval=0.0)
    branch = str(getattr(cfg, "state_repo_branch", "main") or "main")
    return git.transaction(
        branch, mutate, message,
        retries=max(1, int(getattr(cfg, "coordination_retries", 3) or 3)))


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


_BUDGET_SUMMARY_CONTRACT_VERSION = 1


def _budget_summary_enforce(cfg: "Config") -> bool:
    """budget_summary.enforce 既定 false（loop 射影と同契約。coordination は loop より先に合成）。"""
    raw = getattr(cfg, "budget_summary", None)
    if isinstance(raw, dict) and "enforce" in raw:
        return bool(raw.get("enforce"))
    return False


def status_budget_gate(record: dict, *, at: "datetime | None" = None,
                       enforce_default: bool = False) -> dict:
    """status/<node>.json 1 件の生存＋利用枠ゲート（allocate/claim と t7 eligible の単一実装）。

    戻り値:
      alive     … node あり・availability=active・鮮度内（従来の配布適格）
      kind      … "ok" | "exhausted" | "unknown"
                  （不明≠枯渇。stale / 版不一致 / source=unavailable / 射影欠落 → unknown）
      eligible  … alive かつ（enforce でないか kind==ok）
      reason_codes … 射影の固定語彙（欠落時は []）
    enforce は射影の budget.enforce、無ければ enforce_default（既定 false）。
    """
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    node = ""
    active = False
    fresh = False
    reason_codes: list[str] = []
    can_accept: "bool | None" = None
    enforce = bool(enforce_default)
    kind = "unknown"
    try:
        node = str(record.get("node", "") or "").strip()
        active = str(record.get("availability", "active")) == "active"
        updated = datetime.fromisoformat(str(record["updated_iso"]).replace("Z", "+00:00"))
        fresh_sec = float(record.get("fresh_after_sec", 120.0) or 120.0)
        fresh = (now - updated.astimezone(timezone.utc)).total_seconds() <= fresh_sec
    except (KeyError, TypeError, ValueError):
        return {"node": node, "active": False, "fresh": False, "alive": False,
                "enforce": enforce, "can_accept": None, "reason_codes": [],
                "kind": "unknown", "eligible": False}
    alive = bool(node) and active and fresh
    budget = record.get("budget")
    if isinstance(budget, dict):
        if "enforce" in budget:
            enforce = bool(budget.get("enforce"))
        reason_codes = [str(c) for c in list(budget.get("reason_codes") or [])]
        if "can_accept" in budget:
            can_accept = bool(budget.get("can_accept"))
        ver = budget.get("contract_version")
        source = str(budget.get("source", "") or "")
        if not fresh:
            kind = "unknown"
        elif ver != _BUDGET_SUMMARY_CONTRACT_VERSION:
            kind = "unknown"
        elif source == "unavailable":
            kind = "unknown"
        elif can_accept is False:
            kind = "exhausted"
        elif can_accept is True:
            kind = "ok"
        else:
            kind = "unknown"
    else:
        # 射影無し: enforce 中は不明（fail-close）。非 enforce では従来どおり alive のみ。
        kind = "unknown"
    eligible = alive and (not enforce or kind == "ok")
    return {"node": node, "active": active, "fresh": fresh, "alive": alive,
            "enforce": enforce, "can_accept": can_accept, "reason_codes": reason_codes,
            "kind": kind, "eligible": eligible}


def _read_node_status(root: Path, node: str) -> "dict | None":
    path = root / "status" / f"{node}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _gate_journal_line(action: str, gate: dict) -> str:
    codes = gate.get("reason_codes") or []
    return (f"budget gate: action={action} node={gate.get('node') or '-'} "
            f"kind={gate.get('kind')} enforce={bool(gate.get('enforce'))} "
            f"eligible={bool(gate.get('eligible'))} reason_codes={codes}")


def claim_distributed_task(cfg: "Config", task_id: str,
                           at: "datetime | None" = None) -> "str | None":
    """ready タスクを doing へ CAS 遷移し、結果確定に必要な fencing token を返す。"""
    node = str(getattr(cfg, "node", "") or "").strip()
    if _DRAIN_REQUESTED.is_set() or not node or availability_state(cfg, at) != "active":
        return None
    claimed: dict[str, str] = {}
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # 自ノード status 射影の can_accept + 鮮度。enforce false 既定では従来どおり通す
    # （status 鮮度欠落で claim を止めない）。enforce true のときだけ fail-close。
    own_record = _read_node_status(Path(cfg.backlog).parent, node)
    if own_record is None:
        gate = {"node": node, "active": True, "fresh": False, "alive": False,
                "enforce": _budget_summary_enforce(cfg), "can_accept": None,
                "reason_codes": [], "kind": "unknown", "eligible": False}
        has_budget = False
    else:
        gate = status_budget_gate(own_record, at=now,
                                  enforce_default=_budget_summary_enforce(cfg))
        has_budget = isinstance(own_record.get("budget"), dict)
    deny = bool(gate["enforce"]) and not gate["eligible"]
    if deny or (gate["kind"] != "ok" and (gate["enforce"] or has_budget)):
        append_journal(cfg.journal, _gate_journal_line(f"claim {task_id}", gate))
    if deny:
        return None

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


def _fetch_remote_task(cfg: "Config", task_id: str) -> "tuple[Task | None, bool]":
    """remote 正本のタスクを (task, reachable) で返す。

    「リモートに触れなかった」と「触れたがタスクが無い」を**呼び出し側が区別できる**ことが要点。
    両方を None に潰すと、一過性の通信断が fence 喪失と同じ扱いになり、完成した成果が
    破棄される（`_settle_task`）。一過性のブリップはここで吸収する——`coordination_retries`
    回まで指数バックオフで fetch を再試行してから不通と判定する。"""
    root = Path(cfg.backlog).parent
    git = DirectStateGit(root, interval=0.0)
    branch = str(getattr(cfg, "state_repo_branch", "main") or "main")
    attempts = max(1, int(getattr(cfg, "coordination_retries", 3) or 3))
    for i in range(attempts):
        if git._git("fetch", "-q", "origin", branch).returncode == 0:
            break
        if i < attempts - 1:
            backoff_sleep(2 ** i)
    else:
        return None, False
    spec = f"refs/remotes/origin/{branch}:backlog/{task_id}.md"
    result = git._git("show", spec)
    return (parse_task(result.stdout, task_id) if result.returncode == 0 else None), True


def _remote_task(cfg: "Config", task_id: str) -> "Task | None":
    return _fetch_remote_task(cfg, task_id)[0]


def claim_fence_state(cfg: "Config", task: "Task") -> str:
    """settle 前の claim 検証結果を 3 値で返す: "ok" | "lost" | "unknown"。

    - "ok"      … remote 正本が同じ owner/token/generation の doing（＝この試行が正当）
    - "lost"    … remote 正本が別物（他ノードが取り直した・状態が変わった）＝ fence 喪失
    - "unknown" … リモートに触れず検証できない。**"lost" と同一視してはいけない**——
                  通信断で完成した成果を捨てることになる（`_settle_task` が保留へ回す）。
    """
    if not _coordination_active(cfg):
        return "ok"
    current, reachable = _fetch_remote_task(cfg, task.id)
    if not reachable:
        return "unknown"
    if current is None or current.norm_status() != "doing":
        return "lost"
    return "ok" if all(str(current.get(key) or "") == str(task.get(key) or "")
                       for key in ("claim_owner", "claim_token", "claim_generation")) else "lost"



def requeue_unknown_once(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """unknown 隔離（W7）の自動再試行は**次パスの fencing 再確認 1 回だけ**。復帰させた id を返す。

    隔離時に token は回転・push 済みなので、「remote に触れて、remote がこの回転後の姿と一致」
    なら他ノードは取り直していない——ready へ戻して同じ run の続き（last_run 再開）に乗せる。
    触れない・一致しない場合は blocked のまま人待ちに固定する（fence_recheck 印で 2 回目はしない）。"""
    if not _coordination_active(cfg):
        return []
    out: "list[str]" = []
    for t in tasks:
        if (t.norm_status() != "blocked" or not t.get("fence_unknown")
                or t.get("fence_recheck")):
            continue
        t.set("fence_recheck", "1")
        current, reachable = _fetch_remote_task(cfg, t.id)
        matched = (reachable and current is not None
                   and all(str(current.get(k) or "") == str(t.get(k) or "")
                           for k in ("claim_owner", "claim_token", "claim_generation")))
        if matched:
            t.status = "ready"
            t.drop("fence_unknown", "fence_recheck")
            clear_needs_file(cfg, t.id)
            append_journal(cfg.journal,
                           f"unknown 復帰: {t.id} は再確認でリモートと一致（同じ run の続きへ）")
            out.append(t.id)
        persist_task(cfg, t)
    return out


def refresh_distributed_task(cfg: "Config", task_id: str) -> bool:
    """fence 敗北時、stale なローカル task を remote 正本へ戻す。"""
    current = _remote_task(cfg, task_id)
    if current is None:
        return False
    persist_task(cfg, current)
    return True


def allocate_distributed_tasks(cfg: "Config", at: "datetime | None" = None) -> "dict[str, str]":
    """active ノードの ready+doing 件数が最小になるよう、未割当 ready を決定的に配る。

    適格は status の生存（active+鮮度）に加え、射影の can_accept を見る。
    budget.enforce 既定 false の間は従来どおり生存のみで配り、kind≠ok は journal に残す。
    タイブレークは (load, name) のまま（残量を連続優先度にしない）。
    """
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    assigned: dict[str, str] = {}
    gate_notes: list[str] = []

    def mutate(root: Path) -> bool:
        # CAS push の競合で mutate が再実行された場合、失敗した試行の結果を返さない。
        assigned.clear()
        gate_notes.clear()
        eligible: set[str] = set()
        seen: set[str] = set()
        enforce_default = _budget_summary_enforce(cfg)
        status_dir = root / "status"
        for path in sorted(status_dir.glob("*.json")) if status_dir.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    continue
            except (OSError, ValueError, TypeError):
                continue
            gate = status_budget_gate(record, at=now, enforce_default=enforce_default)
            if not gate["node"]:
                continue
            seen.add(gate["node"])
            has_budget = isinstance(record.get("budget"), dict)
            if gate["eligible"]:
                eligible.add(gate["node"])
            if gate["kind"] != "ok" and (gate["enforce"] or has_budget or not gate["eligible"]):
                if gate["alive"] or gate["enforce"] or has_budget:
                    gate_notes.append(_gate_journal_line("allocate", gate))
        own = str(getattr(cfg, "node", "") or "").strip()
        # 自ノード: enforce false では従来どおりローカル active なら含める（status 鮮度より優先）。
        # enforce true では射影ゲートを通し、status 欠落は不明として非適格。
        if own and availability_state(cfg, now) == "active":
            if not enforce_default:
                eligible.add(own)
            elif own not in eligible and own not in seen:
                gate_notes.append(_gate_journal_line(
                    "allocate",
                    {"node": own, "kind": "unknown", "enforce": True,
                     "eligible": False, "reason_codes": []}))
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

    ok = state_transaction(cfg, mutate, "allocate ready tasks")
    for line in gate_notes:
        append_journal(cfg.journal, line)
    return assigned if ok else {}


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
    """drain 開始を act 中にも監視する（新規 claim を止め、controller を解放する）。

    **プロセスの停止そのものはここで決めない**（実装計画 W1-4「自殺型停止経路を
    親 → 子への指示へ置換」）。以前は `shutdown_due` で自分に SIGTERM を送っていたが、
    常駐体（`agent-project serve`）が子を監督する構成では、それが**クラッシュとしか
    読めない**——Supervisor は終了コードを見ずに死亡と判定して再起動し、上がった子が
    1 秒以内に同じ条件で再び自殺して、`quarantine_after` に達したところで隔離される。
    夜間の計画停止のたびにプロジェクトが隔離され、人が上げ直すまで止まる。

    停止は親の責務: 常駐体の availability tick が `shutdown_due` を評価して
    `Supervisor.pause()` を呼び、時間帯が戻れば `resume()` する
    （`resident_cli._availability_tick`）。常駐体を介さない単体起動
    （`agent-project run --watch` を人が直接叩く）では止め手が居ないので、
    drain（新規 claim の停止）までは従来どおりここで行い、プロセスは人が止める。"""
    stop = threading.Event()

    def monitor() -> None:
        while not stop.wait(1.0):
            state = availability_state(cfg)
            if state in ("draining", "stopped") and not _DRAIN_REQUESTED.is_set():
                request_drain(cfg)
                requeue_draining_tasks(cfg)   # 実行中の取り置きを戻す（停止を待たずに）

    threading.Thread(target=monitor, name="agent-project-availability", daemon=True).start()
    return stop
