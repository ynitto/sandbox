from __future__ import annotations
# work.py — 元 agent-flow.py の 4553-4714 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# work
# --------------------------------------------------------------------------
def deps_satisfied(bus: Bus, node) -> bool:
    return all(
        (bus.read_result(d) or {}).get("status") == "done"
        for d in node.get("deps", [])
    )


def _quiesced(bus: Bus, nodes: dict) -> bool:
    """run が静止したか: 実行中(claimed)も、park 待機中(waiting)も、今すぐ claim 可能な
    pending も無い状態。依存が失敗してブロックされた pending は静止扱い（継続判断で付け替えられる）。
    waiting（承認待ち等で park 済み）は in-flight 扱い＝静止させない。これにより orchestrator は
    park 中のノードを見て早まって再計画/完了せず、service_waits が決着を書くまで待つ。"""
    for nid, node in nodes.items():
        st = bus.node_state(nid)
        if st in ("claimed", "waiting"):
            return False
        if st == "pending" and deps_satisfied(bus, node):
            return False
    return True


def pick_claimable(bus: Bus):
    graph = bus.read_graph()
    if not graph:
        return None
    items = list(graph["nodes"].items())
    random.shuffle(items)  # ワーカー間の衝突を減らす
    for nid, node in items:
        if bus.node_state(nid) == "pending" and deps_satisfied(bus, node):
            return nid, node
    return None


def cmd_work(args) -> int:
    who = args.node_id
    bus = make_bus(args, who)
    idle_exit = getattr(args, "idle_exit", False)
    log(who, f"ワーカー起動 (executor={args.executor}, keep_alive={args.keep_alive}, "
             f"idle_exit={idle_exit})")
    # executor を一度だけ解決する（組み込み agent/stub or プラグイン）。
    execute = make_executor(args)
    # park & poll: 親（daemon/run）が service_waits で面倒を見るときだけ deferral を有効化する。
    # 無効時（standalone work 等）は executor が従来どおりブロック待機へフォールバックする。
    defer_enabled = os.environ.get("AGENT_FLOW_DEFER_WAITS") == "1"
    ecfg = _executor_cfg_from_env()
    issue_cap = int(ecfg.get("max_open_issues", 0) or 0)   # 同時イシュー上限（0=無制限）
    watch_interval = _watch_interval(ecfg)
    # 親（run/daemon）からの SIGTERM でもワークスペースの clone を消してから抜ける
    signal.signal(signal.SIGTERM, lambda *_: (cleanup_workspace(), sys.exit(143)))
    time.sleep(random.uniform(0, args.poll))  # 負荷分散: 起動位相をずらす

    idle_polls = 0
    while True:
        bus.sync_pull()
        status = bus.get_status()

        # 終端後は claim しない。canceled で waits が消えて pending に戻ったノードを
        # 拾い直し、人が止めた run を進めてしまう事故を防ぐ（終端判定を「仕事が無いとき」
        # だけにすると、claim 可能な残骸があると永遠に動き続ける）。
        if status in TERMINAL:
            if not args.keep_alive:
                log(who, f"run が {status}。終了します。")
                return 0
            time.sleep(args.poll)
            continue

        candidate = pick_claimable(bus)
        if candidate is None:
            if not args.keep_alive:
                # デーモン起動の短命ワーカー: 仕事が無くなったら少し待って終了（オンデマンド）
                if idle_exit and status not in (None,):
                    idle_polls += 1
                    if idle_polls >= 2:
                        log(who, "claim 可能タスクが無いため終了します（idle-exit）。")
                        return 0
            time.sleep(args.poll)
            continue

        idle_polls = 0
        nid, node = candidate
        kind = node.get("kind", "work")
        if not bus.try_claim(nid, who, args.lease):
            continue  # 競り負け
        log(who, f"claim 成功: {nid} [{kind}] — {node['goal'][:55]}")
        bus.event(who, "claimed", node=nid)

        # throttle（バックプレッシャ）: 同時未決着イシューが上限に達していたら、起票せず
        # throttled park して claim を解放する。エラーにはしない＝人のレビュー速度に発行を
        # ペーシングするだけ（枠が空けば service_waits が解除 → 通常起票）。deferring executor
        # かつ max_open_issues>0 のときだけ働く（kiro/stub 等は waits が空なので発火しない）。
        if defer_enabled and issue_cap > 0 and bus.open_wait_count() >= issue_cap:
            rec = build_wait_record(nid, who, kind,
                                    {"executor": args.executor, "issue": None,
                                     "task_token": None, "throttled": True,
                                     "reason": "throttled:max_open_issues"}, watch_interval)
            park_node(bus, nid, who, rec)
            log(who, f"throttle: 同時イシュー上限({issue_cap})到達 → {nid} を park（起票見送り）")
            time.sleep(random.uniform(0, 0.3))
            continue

        # 依存の成果は構造化データ込みの完全な result dict で渡す
        dep_results = _collect_dep_results(bus, node, kind)
        # run の元要求（全体文脈）。対応 executor（agent の flow-worker プロンプト等）へ渡す。
        run_request = str((read_json(bus.meta_path) or {}).get("request", ""))
        # 中間成果物プロトコル: 自ノードの出力先を用意し、依存ノードの成果物パスを集める。
        # これにより大きな成果物は output/data に貼らずファイル参照で受け渡せる。
        art_dir = bus.ensure_artifact_dir(nid)
        dep_arts = {d: bus.node_artifact_dir(d) for d in node.get("deps", [])}
        # ワークスペース（この run の唯一の書込先）を temp 領域へ clone し、作業ブランチ af/<run_id>
        # を base から作ってエージェントへ渡す（書込先が無ければ読み取り専用 run）。
        goal = node["goal"]
        ws = ensure_workspace_clone(bus.run_workspace(), args.run_id)
        # 作業指示は goal に結合せず別引数で渡す（goal を汚さない）。対応 executor は本来の goal を
        # そのまま使い（gitlab はタイトル/目的に出す）、ワークスペース指示・spec は別枠で扱う。
        # 参照リポジトリ（読むだけ）は run メタから取り、ワークスペース指示に続けてエージェントへ伝える。
        references = bus.run_references()
        ref_note = reference_instruction(references)
        instruction = "\n".join(s for s in (workspace_instruction(ws) if ws else "", ref_note) if s)
        # 実行中は心拍で lease を延長し続け、長時間タスクでも再 claim されないようにする
        hb = Heartbeat(bus, nid, who, args.lease)
        hb.start()
        rdata = None
        delivery = None
        try:
            output, rdata = call_executor(execute, kind, goal, dep_results, args.model,
                                          art_dir, dep_arts, instruction, workspace=ws,
                                          references=references, request=run_request)
            # エージェントが編集したらワークスペースの作業ブランチへ commit して push する
            # （変更が無ければ何もしない＝調査タスク等ではブランチを作らない）。
            delivery = finalize_workspace(ws, args.run_id, nid)
            rstatus = "done"
        except Exception as e:  # noqa: BLE001 — 結果として記録する
            # park シグナル（DeferDecision.defer）: 承認待ち等で未決着＝終端 result を書かず、
            # 心拍を止めてから wait を書き claim を解放する（この順序で claim の書き戻し競合を防ぐ）。
            # スロットを空けて次の claim 可能タスクへ回り、決着は service_waits が書く。
            defer = getattr(e, "defer", None)
            if isinstance(defer, dict):
                hb.stop()
                rec = build_wait_record(nid, who, kind, defer, watch_interval)
                park_node(bus, nid, who, rec)
                log(who, f"park: {nid}（{defer.get('reason', 'wait')}）— claim 解放しスロットを空ける")
                if ws:
                    cleanup_workspace()   # park 中は clone を持たない（ディスク解放）
                time.sleep(random.uniform(0, 0.3))
                continue
            output = f"実行エラー: {e}"
            rstatus = "failed"
            # executor が例外に載せた構造化データ（gitlab 却下の issue_iid / guidance 等）は
            # 承認と対称に failed result の data として残す（消費側の文字列マッチ依存を無くす）
            edata = getattr(e, "data", None)
            if isinstance(edata, dict):
                rdata = edata
        finally:
            hb.stop()

        # 生成された中間成果物を run_dir 相対パスで記録（後続・status から発見できる）
        artifacts = [os.path.relpath(p, bus.run_dir) for p in bus.list_artifacts(nid)]
        if delivery:  # ワークスペースへ push したブランチ/コミットを result に残す（消費側が追跡）
            rdata = {**(rdata if isinstance(rdata, dict) else {}), "delivery": delivery}
        bus.write_result(nid, who, rstatus, output, rdata, artifacts=artifacts)
        bus.event(who, "result", node=nid, status=rstatus)
        bus.sync_push(f"result {nid} [{rstatus}] by {who}")
        log(who, f"完了: {nid} [{rstatus}]")
        if getattr(args, "cleanup_per_node", False) and ws:
            cleanup_workspace()  # ノード完了/失敗ごとに clone を即削除（長命 worker のディスク抑制）
        time.sleep(random.uniform(0, 0.3))  # 負荷分散: 他ノードに claim の機会を渡す

