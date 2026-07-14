from __future__ import annotations
# submit.py — 元 agent-flow.py の 5100-5172 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# submit — 要求を inbox に投入（デーモンが拾って orchestrator を起動する）
# --------------------------------------------------------------------------
def cmd_submit(args) -> int:
    req_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    # ノード ID に pid を含め、並行 submit（agent-project の一括 offload 等）が同じ
    # クローン作業ツリーを共有して index.lock を取り合う事故を避ける（クローンは
    # 終了時に削除され、SIGKILL 残骸も daemon の cleanup が回収する）。
    bus = make_bus(args, f"submitter-{os.getpid()}")
    bus.sync_pull()
    bus.submit_request(req_id, args.request, f"{socket.gethostname()}-{os.getpid()}",
                       workspace=parse_workspace(getattr(args, "workspace", None)),
                       references=parse_references(getattr(args, "references", None)),
                       inherit_from=getattr(args, "inherit_from", None))
    bus.sync_push(f"submit request {req_id}")
    print(req_id)  # run-id を標準出力（スクリプトから拾える）
    print(f">>> 要求を投入しました: {req_id}（デーモンが拾います）", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# cancel — run スコープの恒久停止（人の明示指示による緊急回避手段）
# --------------------------------------------------------------------------
def _apply_on_cancel(bus: Bus, args, run_id: str) -> None:
    """--close-issues 指定時に、run の park 済みイシューを executor の on_cancel フックで後始末する。
    フック非対応の executor では何もしない。ベストエフォート（失敗は無視）。"""
    on_cancel = executor_hook(args, "on_cancel")
    if on_cancel is None:
        return
    cfg = _executor_cfg(args)
    if cfg:
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(cfg, ensure_ascii=False)
    records = [r for r in bus.run_view(run_id).list_waits() if (r.get("issue") or {}).get("iid")]
    if not records:
        return
    try:
        on_cancel(records)
        log("cancel", f"run {run_id}: park 済みイシュー {len(records)} 件を後始末（close-issues）")
    except Exception as e:  # noqa: BLE001
        log("cancel", f"run {run_id}: on_cancel 後始末で例外（無視）: {e}")


def cmd_cancel(args) -> int:
    """run を canceled に終端化する（人の明示指示による唯一の hard-stop）。
    cancel マーカーを inbox に置いて全 PC / daemon へ伝え、run が存在すれば即 status=canceled を
    確定する（監視主体が居なくても止まる）。park 済みノードの再ポーリングを止め、--close-issues なら
    起票済みイシューも後始末する。既に終端した run には効かない（done/failed/canceled は不可逆）。"""
    bus = make_bus(args, f"cancel-{os.getpid()}")
    bus.sync_pull()
    rid = args.run_id
    if not bus.run_exists(rid) and rid not in bus.list_inbox():
        print(f"[agent-flow] run {rid} が見つかりません（バス: {os.path.abspath(args.bus)}）",
              file=sys.stderr)
        return 2
    cur = bus.run_meta(rid).get("status")
    if cur in TERMINAL:
        print(f">>> run {rid} は既に終端（status={cur}）。cancel は不要です。")
        return 0
    reason = getattr(args, "reason", "") or "手動 cancel"
    bus.cancel_request(rid, socket.gethostname(), reason, bool(getattr(args, "close_issues", False)))
    # --close-issues は waits を消す前に実施する（イシュー座標は park 記録が握っているため）。
    if getattr(args, "close_issues", False):
        _apply_on_cancel(bus, args, rid)
    cleared = bus.clear_waits_for_run(rid)     # park 済みノードの再ポーリングを止める
    marked = bus.mark_canceled(rid, reason)    # run が存在すれば即終端化（監視主体が居なくても止まる）
    bus.sync_push(f"cancel run {rid}: {reason}")
    tail = "・status=canceled 確定" if marked else "（daemon が受理して終端化します）"
    print(f">>> run {rid} をキャンセルしました{tail}。park 解除 {cleared} 件、"
          f"理由: {reason}")
    if not marked and not bus.run_exists(rid):
        print(f">>> 注: 要求 {rid} はまだ run 化されていません。daemon が受理時に canceled で終端します。")
    return 0

