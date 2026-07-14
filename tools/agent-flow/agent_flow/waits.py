from __future__ import annotations
# waits.py — 元 agent-flow.py の 3662-3953 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# park & poll — 承認待ち等の長い外部待機を worker スロットから切り離す
# --------------------------------------------------------------------------
# 設計: executor が決着していないとき DeferDecision を投げ、worker は claim を解放して
# waits/<node>.json に park 記録を残す（node_state は "waiting"）。監視主体（daemon/run）の
# service_waits が全 park をバッチで再確認し、決着なら終端 result を直接書く。これにより
# 「ブロック worker N 台 ×(1/poll)」を「監視 1 本 ×(1/watch_interval) のバッチ」へ畳み、
# worker スロット占有と GitLab ポーリングの二重負荷を同時に消す。gitlab は承認時にローカル
# workspace を finalize する必要がない（成果はマージ済み MR にある）ため、service_waits が
# worker/clone 無しで終端 result を材料化できるのが成立の鍵。
def _executor_module(args):
    """executor プラグインのモジュールを返す（組み込み agent/stub や未解決は None）。
    service_waits が poll()/on_cancel() フックを取り出すために使う。"""
    spec = getattr(args, "executor", None) or "agent"
    if spec in BUILTIN_EXECUTORS:
        return None
    path = _resolve_executor_plugin(spec)
    if not path:
        return None
    try:
        return _load_executor_module(path)
    except RuntimeError:
        return None


def executor_hook(args, name: str):
    """executor プラグインの任意フック（poll / on_cancel）を返す。無ければ None。
    これらは execute() と同じくプラグイン側にあり executor 非依存の本体からは任意。"""
    mod = _executor_module(args)
    fn = getattr(mod, name, None) if mod else None
    return fn if callable(fn) else None


def _executor_cfg(args) -> dict:
    """executor 名と同名の設定ブロック（例 gitlab:）を dict で返す。max_open_issues /
    watch_interval など park & poll のパラメータをここから読む。無ければ空 dict。"""
    spec = getattr(args, "executor", None) or "agent"
    cfg = getattr(args, spec, None)
    return cfg if isinstance(cfg, dict) else {}


def _executor_cfg_from_env() -> dict:
    """worker プロセス内では設定は環境変数 AGENT_FLOW_EXECUTOR_CONFIG(JSON) で届く。
    cmd_work の throttle 判定（max_open_issues）用にそれを読む。無ければ空 dict。"""
    raw = os.environ.get("AGENT_FLOW_EXECUTOR_CONFIG")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _defer_enabled(args) -> bool:
    """park & poll（deferral）を有効にするか。executor 設定 defer_waits（既定 true）で決まる。
    false なら従来モード（worker がイシューを監視してブロック待機）へ戻す。daemon/run が
    この判定で worker への環境変数 AGENT_FLOW_DEFER_WAITS を出し分け、service_waits も出番が無くなる。"""
    return bool(_executor_cfg(args).get("defer_waits", True))


def _watch_interval(cfg: dict) -> float:
    """service_waits が park をバッチ再確認する間隔（秒）。既定 90。"""
    try:
        v = float(cfg.get("watch_interval", 90.0))
        return v if v > 0 else 90.0
    except (TypeError, ValueError):
        return 90.0


def _wait_lease_window(watch_interval: float) -> float:
    """park 記録の生存リース秒。健康な監視主体は watch_interval 毎に更新するので、その数倍を
    確保すれば一過性の遅延で誤って pending へ縮退させない。逆に監視が数回分止まれば失効し、
    node_state が pending へ落ちて full worker の token 再アタッチに引き継がれる（行き止まり回避）。"""
    return max(watch_interval * 3.0, 300.0)


def _wait_deadline(rec: dict):
    """park 記録から現在の締切（絶対エポック）を導く。人の作業を検知(active_seen)後は
    approved_timeout、未検知なら timeout。当該 timeout が 0 以下なら無限（None）。
    ブロック版 _wait_for_decision の猶予延長ロジックと同じ意味を service_waits 側で再現する。"""
    started = float(rec.get("started_at", 0) or 0)
    if rec.get("active_seen"):
        since = float(rec.get("active_since", started) or started)
        at = float(rec.get("approved_timeout", 0) or 0)
        return (since + at) if at > 0 else None
    to = float(rec.get("timeout", 0) or 0)
    return (started + to) if to > 0 else None


def build_wait_record(nid, who, kind, defer: dict, watch_interval: float) -> dict:
    """DeferDecision.defer と現在時刻から waits/<node>.json のレコードを組み立てる。
    started_at は「park を開始した時刻」＝ブロック版が time.time() から締切を測るのと同じ基準。"""
    now = time.time()
    pi = float(defer.get("poll_interval", 30.0) or 30.0)
    return {
        "id": nid, "who": who, "kind": kind,
        "executor": defer.get("executor", ""),
        "issue": defer.get("issue"),                 # throttled は None（イシュー未作成）
        "task_token": defer.get("task_token"),       # 秘密ではない（再アタッチ用の決定的トークン）
        "expected_target": defer.get("expected_target", ""),  # MR ターゲット検証（park を跨いで保つ）
        "throttled": bool(defer.get("throttled")),
        "reason": defer.get("reason", "wait"),
        "active_seen": bool(defer.get("active_seen")),
        "active_since": now if defer.get("active_seen") else None,
        "poll_interval": pi,
        "timeout": float(defer.get("timeout", 0.0) or 0.0),
        "approved_timeout": float(defer.get("approved_timeout", 0.0) or 0.0),
        "started_at": now,
        "next_poll_at": now + pi,
        "wait_lease_until": now + _wait_lease_window(watch_interval),
        "created_at": now_iso(),
    }


def park_node(bus: Bus, nid: str, who: str, rec: dict) -> None:
    """ノードを park（保留）する: waits 記録を先に書き、その後 claim を解放する。
    この順序が肝——先に解放すると crash 窓で wait を失う。書いてから解放すれば、途中で
    死んでも claim（lease）が残り、失効後に wait が governing する（wait を失わない）。"""
    bus.write_wait(nid, rec)
    bus.release_claim(nid, who)
    bus.event(who, "parked", node=nid, reason=rec.get("reason", "wait"))
    bus.sync_push(f"park {nid} by {who} ({rec.get('reason','wait')})")


def _finish_wait(v: Bus, rec: dict, status: str, text: str, data) -> None:
    """park の決着を終端 result として書き、wait 記録を消す（service_waits から）。"""
    nid = rec["id"]
    v.write_result(nid, rec.get("who", "service_waits"), status, text, data)
    v.clear_wait(nid)
    v.event("service_waits", "result", node=nid, status=status)
    v.sync_push(f"result {nid} [{status}] by service_waits")


def _service_one_wait(v: Bus, rec: dict, poll, watch_interval: float,
                      wait_lease: float, daemon_id: str) -> None:
    """park 済み（起票済み）ノードを 1 件 poll して決着/未決着を反映する。"""
    nid = rec["id"]
    # 締切超過（人が動かないまま timeout / approved_timeout）→ failed（消費者の永久待機を防ぐ）
    dl = _wait_deadline(rec)
    if dl is not None and time.time() >= dl:
        iid = (rec.get("issue") or {}).get("iid")
        phase = "MR の決着" if rec.get("active_seen") else "レビュー/MR 作成"
        _finish_wait(v, rec, "failed",
                     f"[gitlab] park タイムアウト: イシュー #{iid} が期限内に {phase} に至らず",
                     {"decision": "rejected", "reason": "park-timeout", "issue_iid": iid})
        log(daemon_id, f"park タイムアウト: {nid}（#{iid}）→ failed")
        return
    try:
        r = poll({"issue": rec.get("issue"), "active_seen": rec.get("active_seen", False),
                  "expected_target": rec.get("expected_target", "")})
    except Exception as e:  # noqa: BLE001 — poll 失敗は run を止めない。lease を更新して次回再試行
        log(daemon_id, f"service_waits poll 失敗（無視して次回再試行）: {nid}: {e}")
        rec["next_poll_at"] = time.time() + max(watch_interval, float(rec.get("poll_interval", 30) or 30))
        rec["wait_lease_until"] = time.time() + wait_lease
        v.write_wait(nid, rec)
        return
    decision = (r or {}).get("decision")
    if decision == "approved":
        _finish_wait(v, rec, "done", (r or {}).get("text", ""), (r or {}).get("data"))
        log(daemon_id, f"park 決着（承認）: {nid} → done")
        return
    if decision == "rejected":
        _finish_wait(v, rec, "failed", (r or {}).get("text", ""), (r or {}).get("data"))
        log(daemon_id, f"park 決着（却下）: {nid} → failed")
        return
    # 未決着 → active_seen/締切/次回時刻/lease を更新して据え置く
    active_now = bool((r or {}).get("active_seen"))
    if active_now and not rec.get("active_seen"):
        rec["active_since"] = time.time()
        log(daemon_id, f"park: {nid} 人の作業を検知（猶予を approved_timeout へ延長）")
    rec["active_seen"] = rec.get("active_seen") or active_now
    rec["next_poll_at"] = time.time() + max(watch_interval, float(rec.get("poll_interval", 30) or 30))
    rec["wait_lease_until"] = time.time() + wait_lease
    v.write_wait(nid, rec)


def _service_throttled(v: Bus, rec: dict, cap: int, wait_lease: float, daemon_id: str) -> None:
    """throttled park（同時イシュー上限で起票を見送ったノード）を面倒見る。枠が空いたら解除
    （clear_wait → node は pending に戻り worker が通常起票）。まだ満杯なら lease を延ばして
    pending への無用な flap を防ぐ。エラーにはしない＝バックプレッシャで発行がペーシングされるだけ。"""
    nid = rec["id"]
    if cap <= 0 or v.open_wait_count() < cap:
        v.clear_wait(nid)
        v.sync_push(f"throttle release {nid}")
        log(daemon_id, f"throttle 解除: {nid}（同時イシューの枠が空いた）")
        return
    rec["wait_lease_until"] = time.time() + wait_lease
    v.write_wait(nid, rec)


def service_waits(bus: Bus, args, only_runs: "list | None" = None,
                  daemon_id: str = "service_waits") -> int:
    """監視主体（daemon/run）が park 済みノードをバッチ再確認する単一ポーラ。処理した run 数を返す。
    起動モード非依存（daemon でも cmd_run でも同じこれを回す）。executor が poll() を持たない
    （kiro/stub）なら何もしない＝park & poll は deferring executor（gitlab）だけで働き、他は不変。

    分散（git バス）で監視を**公平に分担**するため、`only_runs` に「この監視主体が担当する run」を
    渡す（daemon は自分が orchestrator を駆動している run、cmd_run は自分の run 1 件）。渡すと
    その run だけを監視する＝**1 run の park は駆動オーナー 1 台だけがポーリング**し、N 台が全 park を
    重複ポーリングするのを防ぐ。run 自体は request-claim で各 PC に分散するため監視も自然に分散する。
    オーナーが消えても孤児 reclaim が run（＝監視）を別 PC へ移すのでクラッシュ耐性はそのまま。
    None（担当未指定）のときは全 active run を見る（単一 PC / 後方互換）。"""
    if not _defer_enabled(args):
        return 0                       # 従来モード（deferral 無効）＝park は無いので監視も不要
    poll = executor_hook(args, "poll")
    if poll is None:
        return 0
    cfg = _executor_cfg(args)
    # poll() は自プロセス内で走るので、executor 設定（起票先/接続ラベル等）を環境変数で届ける
    # （daemon/run は make_executor を経由しないため、ここで明示的に渡す）。
    if cfg:
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(cfg, ensure_ascii=False)
    watch_interval = _watch_interval(cfg)
    wait_lease = _wait_lease_window(watch_interval)
    cap = int(cfg.get("max_open_issues", 0) or 0)
    now = time.time()
    run_ids = list(only_runs) if only_runs is not None else bus.active_runs()
    serviced = 0
    for rid in run_ids:
        v = bus.run_view(rid)
        waits = v.list_waits()
        if not waits:
            continue
        serviced += 1
        for rec in waits:
            nid = rec.get("id")
            if not nid or v.has_result(nid):
                v.clear_wait(nid)                    # 別経路で決着済み → 記録を掃除
                continue
            if rec.get("throttled"):
                _service_throttled(v, rec, cap, wait_lease, daemon_id)
                continue
            if float(rec.get("next_poll_at", 0) or 0) > now:
                # まだ再確認時刻でない（per-issue バックオフ）。poll は飛ばすが、
                # 監視主体が生きている証拠として lease だけ更新する。
                # gitlab 既定は poll_interval≈lease 下限（300s）なので、更新しないと
                # バックオフの合間に wait_lease が切れ、node_state が pending へ縮退して
                # worker が再クレームする（コメントの「watch_interval 毎に更新」契約と矛盾）。
                rec["wait_lease_until"] = now + wait_lease
                v.write_wait(nid, rec)
                continue
            _service_one_wait(v, rec, poll, watch_interval, wait_lease, daemon_id)
    return serviced


def _is_gate_result(r: dict) -> bool:
    """verify gate の結果か（data が {"ok": ...} を持つ）。集約対象から除くのに使う。"""
    dv = _dep_data(r)
    return isinstance(dv, dict) and "ok" in dv


def _collect_dep_results(bus, node: dict, kind: str) -> dict:
    """ノードの依存成果を集める。集約系（reduce/synthesize/filter/judge）では、
    planner が work→gate→synth と直列にして集約役の依存が gate だけになっても入力が
    空にならないよう、gate が検証した上流の成果も透過して渡す（gate 判定自体は
    execute 側で集約対象から除外される）。"""
    dep_results = {d: (bus.read_result(d) or {}) for d in node.get("deps", [])}
    if kind in ("reduce", "synthesize", "filter", "judge"):
        gnodes = (bus.read_graph() or {}).get("nodes", {})
        for d in list(dep_results):
            if _is_gate_result(dep_results[d]):
                for up in gnodes.get(d, {}).get("deps", []):
                    dep_results.setdefault(up, bus.read_result(up) or {})
    return dep_results


def _normalize_verify(text: str, data):
    """verify 成果を {"ok": bool, ...} 形へ正規化する。
    LLM が JSON を欠いても、本文の verify=pass/fail から ok を導いて gate を機能させる。"""
    if isinstance(data, dict) and "ok" in data:
        return data
    low = text.lower()
    ok = ("verify=pass" in low) or ("verify=fail" not in low and "fail" not in low)
    out = {"ok": ok}
    if isinstance(data, dict):
        out.update(data)
        out["ok"] = ok
    return out


def _reconcile_count(data):
    """reduce 成果の count を実リスト長へ補正する。
    dict に count(int) と単一のリスト値があれば、count = len(list) に揃える
    （LLM 自己申告の件数とリスト実体の不整合を機械的に解消）。"""
    if not isinstance(data, dict) or "count" not in data:
        return data
    lists = [v for v in data.values() if isinstance(v, list)]
    if len(lists) == 1 and isinstance(data.get("count"), int):
        data["count"] = len(lists[0])
    return data

