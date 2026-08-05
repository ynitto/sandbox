from __future__ import annotations
# daemon.py — 元 agent-flow.py の 5174-5422 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# participate — 参加のみ 1 巡（受理・回収・板巡回）。実行はノード常駐体が行う
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# tick 関数群（実装計画 W1-2）: 旧 daemon のループ本体から挙動不変で抽出したもの。
# 常駐一本化（W1-9）で駆動主体はノード常駐体になり、ここは `cmd_participate` が
# 単体呼び出し・単体テスト可能な部品として使う。
# --------------------------------------------------------------------------
def _tick_cancel(bus, args, daemon_id, orchestrators, workers) -> None:
    """cancel 指示の受理: マーカーのある run を cancelled に終端化し、その run の
    orchestrator/worker を止め、park の再ポーリングを止める（--close-issues ならイシューも
    後始末）。承認待ちで park 中の run も、暴走中の run も、run スコープで恒久停止できる。

    **順序制約**: 孤児回収（`_adopt_orphan_runs`）より必ず先に呼ぶこと。cancel 済み（TERMINAL）
    の run は `run_is_orphaned` が False を返すため孤児回収の対象から自然に外れるが、逆順で
    呼ぶと cancel 予定の run を無駄に resume（再起動）してしまう。

    `orchestrators` / `workers` は「この呼び出し側が Popen ハンドルを持っている子」。
    `cmd_participate` からは空で渡る——実行中プロセスの停止は `cmd_run` 自身が cancel
    マーカーを見て行うので、ハンドルを持たない経路でも cancel は効く。"""
    for rid in bus.list_cancels():
        meta = bus.run_meta(rid)
        info = bus.cancel_info(rid)
        reason = info.get("reason") or "cancel 指示"
        # run 化前: マーカーを残し、下の inbox ループの cancel_request_run に任せる。
        # ここで clear すると受理前にマーカーが消え、要求がそのまま起動してしまう。
        if not bus.run_exists(rid):
            continue
        # 既に終端でも waits 掃除は行う（orchestrator が先に mark_canceled した場合、
        # ここをスキップすると park が残り service_waits が動き続ける）。
        if meta.get("status") in TERMINAL:
            # close_issues 要求があるのに orch が先に終端＋waits を消す前にここに来た場合、
            # waits が残っていれば先に on_cancel してから掃除する。
            if info.get("close_issues"):
                _apply_on_cancel(bus, args, rid)
            cleared = bus.clear_waits_for_run(rid)
            if cleared or info.get("close_issues"):
                bus.sync_push(f"cancel cleanup waits {rid}")
            continue
        if info.get("close_issues"):
            _apply_on_cancel(bus, args, rid)      # waits を消す前にイシューを後始末
        bus.clear_waits_for_run(rid)
        # 呼び出し側が駆動中の子を止める（run スコープ。ハンドルが無ければ cmd_run が自ら止まる）
        if rid in orchestrators and orchestrators[rid].poll() is None:
            orchestrators[rid].terminate()
        for _, wp in [(r, p) for r, p in workers if r == rid]:
            if wp.poll() is None:
                wp.terminate()
        marked = bus.mark_canceled(rid, reason)
        bus.run_view(rid).event(daemon_id, "cancelled", run=rid, reason=reason)
        bus.sync_push(f"cancel run {rid}: {reason}")
        if marked:
            log(daemon_id, f"cancel 受理: {rid} を cancelled に終端化（{reason}）")


def _tick_board(bus, args, daemon_id) -> None:
    """委譲公示板（agent-board）の巡回: workload=flow の公示に repos/tags 照合で入札し、
    勝てば自分の inbox へ submit_request で取り込む。板未設定なら no-op。
    板の巡回失敗は巡回全体を止めない（板が落ちても自バスは動く）。"""
    if getattr(args, "board", None):
        try:
            poll_board(bus, args, daemon_id)
        except Exception as e:  # noqa: BLE001 — 板の巡回失敗は巡回全体を止めない
            log(daemon_id, f"board 巡回でエラー（無視して継続）: {e}")


def _default_daemon_id(args) -> str:
    """node_id は PC 名で安定させる（実装計画 W1-10・設計 §3.1「名義は node_id = PC 名」）。
    以前は hostname-pid で daemon 再起動ごとに変わり、板の bid/status がその都度別名義になって
    残骸が積もっていた（同一 PC の板上の身元は 1 つであるべき）。

    正規化は `agentcore.nodeid` の共通実装に委ねる（`_safe` ではない）。`_safe` は不正文字を
    `_` に倒し大小文字も保つため、同じ PC が flow で `Mac`・amigos で `mac` になり、板に
    2 ノードとして現れていた（大小文字を区別しないファイルシステムでは互いを上書きする）。

    ホスト名の**取り方**まで含めて `agentcore.nodeid.default_node_id` の 1 実装に寄せる
    （P0-3 — ホスト名が空になる環境での落ち方がツールごとに違うと、そこでも名義が割れる）。"""
    return args.node_id or default_node_id()


class _Deferred:
    """`participate` の擬似 spawn 戻り値。`_adopt_orphan_runs` / `_heal_failed_runs` は
    「spawn の戻り値が None でなければ引き継ぎ成立」で判断するため、実プロセスを起こさずに
    引き継ぎだけ成立させるための印。実行はノード常駐体が `run --from-inbox` で行う。"""

    __slots__ = ("run_id",)

    def __init__(self, run_id: str):
        self.run_id = run_id


def cmd_participate(args) -> int:
    """参加のみ 1 巡（設計 §4.2・実装計画 W1-9）: cancel の受理・park の再確認・孤児 /
    auto-heal の引き継ぎ判断・板の巡回・inbox の受理を行い、**run は実行しない**。
    実行すべき run-id を出力する（呼び出し側が `agent-flow run --run-id <id> --from-inbox`
    をディスパッチする材料）。

    ノード常駐体（agent-project resident）の短周期 tick から都度起動される想定。バスが
    調停役なのでプロセス境界を跨いでも claim/lease は安全に進む。R9: 常駐なしの単発 CLI
    としても成立する（`agent-flow run` 単発は自分でリースを張り park も面倒を見るので、
    このコマンドが動いていなくても 1 本の run は完走する）。

    `--running` には「この呼び出し側が今まさに走らせている / これから走らせる run-id」を渡す。
    渡さないと、起動待ちの run を孤児と誤判定して再開回数（max_resumes）を無駄に焼き、
    上限に達したところで failed に確定してしまう。
    """
    # log()（cancel 受理・板巡回・引き継ぎの逐次ログ）は素の print で stdout に出る。
    # 呼び出し側（常駐体の flow tick）は stdout を機械可読な結果として json.loads するため、
    # ログが先頭に混ざると必ずパース失敗する。この巡回の間だけ stdout を奪って stderr へ
    # 転送し、結果（JSON / 平文一覧）だけを最後に本来の stdout へ書く（amigos participate と同じ流儀）。
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        dispatch = _participate_once(args)
    sys.stderr.write(buf.getvalue())
    if getattr(args, "json", False):
        print(json.dumps([{"run_id": r} for r in dispatch], ensure_ascii=False))
    else:
        for rid in dispatch:
            print(rid)
    return 0


def _participate_once(args) -> "list[str]":
    """`cmd_participate` の本体。実行すべき run-id を返す（stdout は呼び出し側が奪っている）。"""
    node = _default_daemon_id(args)
    bus = make_bus(args, f"node-{_safe(node)}")
    base = _child_base(args, os.path.abspath(args.bus))
    running = {r for r in (str(getattr(args, "running", "") or "")).split(",") if r}
    lease_window = _run_lease_window(args)
    # 同時実行数は agent-control（管理面の宣言）が最優先。宣言が無ければ従来どおり
    # CLI 引数 → 設定ファイル → 既定 8（agent-control 契約の優先順位そのまま）。
    max_runs = control_max_runs(int(getattr(args, "max_runs", 0) or 0))
    # 受理枠: 呼び出し側が既に走らせている分を差し引いた残り。0 以下（無制限）は None。
    slots = max(0, max_runs - len(running)) if max_runs > 0 else None

    bus.sync_pull()
    state_sync(args)   # 状態 git: バス状態の共有と inbox 投入の取り込み（間隔律速・ローカルバス時のみ）
    # cancel 受理は孤児回収より必ず先に呼ぶ（W1-2 順序制約）。実行中プロセスの停止は
    # `cmd_run` 自身が cancel マーカーを見て行うので、ここでは Popen ハンドルを持たない。
    _tick_cancel(bus, args, node, {}, [])
    # park & poll: 監視は**この呼び出し側が駆動している run だけ**（分散時に N 台が全 park を
    # 重複ポーリングしないよう、1 run の監視は駆動オーナー 1 台に分担する）。
    try:
        service_waits(bus, args, only_runs=sorted(running), daemon_id=node)
    except Exception as e:  # noqa: BLE001 — 監視失敗は tick 全体を止めない
        log(node, f"service_waits でエラー（無視して継続）: {e}")

    def _defer(_base, _args, req_id, _req):
        return _Deferred(req_id)

    dispatch: "list[str]" = []
    # 孤児 run の引き継ぎ: 駆動していたプロセスが消失した非終端 run（PC シャットダウン・
    # クラッシュ等）を同じ run-id で続きから再開する。再開できないものだけ failed に確定する。
    adopted, orphan_failed = _adopt_orphan_runs(bus, node, running, lease_window, args, base,
                                                spawn=_defer, slots=slots)
    for rid in adopted:
        log(node, f"孤児 run を引き継ぎ: {rid}（resume #{bus.run_meta(rid).get('resume_count', '?')}）")
    for rid in orphan_failed:
        log(node, f"孤児 run を回収: {rid} → failed（駆動プロセス消失・再開不可）")
    dispatch += list(adopted)
    # auto-heal（レイヤ4）: transient 起因の failed run を cooldown 後に自動再開する。
    healed = _heal_failed_runs(bus, node, running, lease_window, args, base,
                              spawn=_defer, slots=slots)
    for rid in healed:
        log(node, f"auto-heal: {rid}（heal #{bus.run_meta(rid).get('heal_count', '?')}）")
    dispatch += list(healed)
    # 委譲公示板の巡回: workload=flow の公示に入札し、勝てば自分の inbox へ取り込む
    # （＝下の受理ループが拾う）。
    _tick_board(bus, args, node)
    # 新しい要求の受理。max_runs>0 なら「実行中（全 park を除く）の run 数」で律速する。
    # 超過した要求は inbox に残り、枠が空いた次の巡回で受理される。cancel の受理は枠と無関係。
    busy = _busy_run_count(bus, running) if max_runs > 0 else None
    for req_id in bus.list_inbox():
        if bus.run_exists(req_id) or req_id in running or req_id in dispatch:
            continue
        if bus.is_canceled_requested(req_id):
            # run 化前に cancel された要求は起動せず cancelled で終端化する（＝受理しない）。
            if bus.cancel_request_run(req_id, bus.cancel_info(req_id).get("reason") or ""):
                bus.clear_cancel(req_id)
                bus.sync_push(f"cancel request {req_id}（run 化前）")
                log(node, f"cancel: 要求 {req_id} を run 化前に cancelled で終端化")
            continue
        if busy is not None and busy >= max_runs:
            continue   # 受理枠なし → inbox に残す（取りこぼさない。枠が空いた巡回で受理）
        if not bus.read_inbox(req_id):
            continue
        if bus.claim_request(req_id, node, args.lease):
            # ここで生存リース（touch_run）は張れない: run はまだ作られておらず meta.json が
            # 無いためである。孤児回収は `run_exists` で先に弾くので誤判定は起きないが、受理
            # から `run --from-inbox` の起動までに呼び出し側が落ちると、その要求は inbox claim の
            # lease（`--lease`・既定 1800 秒）が失効するまで誰も拾い直さない。短くすると、
            # ワーカープールで起動待ちの run を別ノードが二重に拾うので、この窓は縮めない。
            dispatch.append(req_id)
            if busy is not None:
                busy += 1
            log(node, f"要求 {req_id} を受理（実行は呼び出し側へ引き渡す）")
    # アイドル（受理も引き継ぎも無い巡回）なら自己更新を確認する。取り込み自体は切り離した
    # 子プロセスへ渡すので、この巡回が呼び出し側のタイムアウトで殺されても中断されない。
    if not dispatch:
        try:
            maybe_self_update(args, idle=True, state={"last": 0.0})
        except Exception as e:  # noqa: BLE001 — 更新確認の失敗は巡回を止めない
            log(node, f"自己更新の確認でエラー（無視して継続）: {e}")
    return dispatch
