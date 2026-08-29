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


def _work_failure_class(kind: str, blob: str = "", data=None) -> str:
    """worker失敗の近因分類。base-syncでも観測済みの環境・一時障害を潰さない。"""
    existing = str(data.get("error_class") or "") if isinstance(data, dict) else ""
    if existing and not (kind == "base-sync" and existing == "content"):
        return existing
    triage = classify_agent_failure(blob)
    if triage:
        return triage[0]
    return "integration" if kind == "base-sync" else "content"


def _truncate_bytes(text: str, limit: int) -> str:
    """UTF-8 のバイト境界で安全に切り詰める（マルチバイト文字の途中で壊さない）。"""
    b = text.encode("utf-8")
    if len(b) <= limit:
        return text
    return b[:max(0, limit)].decode("utf-8", errors="ignore") + "…"


def repair_brief(bus: Bus, node: dict, args) -> "dict | None":
    """差分修復リトライ（案 B-1・オプトイン）のブリーフを実行直前に決定的に組み立てる。

    retry ノードは continuation.py の stub 経路（_continue_stub）と evaluator 経路
    （continue_agent が返す new_tasks）の両方から生まれる。修復材料（前回の出力・
    成果物・verify の指摘）はどちらもバス上の実状態なので、グラフへ持ち回さずここで
    `replaces` から引く——2 経路が同じ 1 実装を通り、プランナーの出力契約は増えない。

    修復は同一系統 1 回だけ（retries が 2 以上なら None＝従来の全作り直しへ戻す。
    壊れた前回に引きずられて収束しないケースの有界化。既存 max_retries の内側）。
    設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §2。"""
    if not bool(getattr(args, "repair_retry", False)):
        return None
    prev_id = node.get("replaces")
    if not prev_id:
        return None
    if _retry_depth(str(node.get("id") or ""), node) >= 2:
        return None
    prev = bus.read_result(prev_id)
    if not isinstance(prev, dict):
        return None
    limit = int(getattr(args, "repair_excerpt_bytes", 4000) or 4000)
    prev_output = str(prev.get("output") or "")
    prev_data = prev.get("data") if isinstance(prev.get("data"), dict) else {}

    # 指摘: prev_id を deps に含む verify ノードの issues（stub 経路では旧 verify ノードが
    # そのまま該当する——新 verify ノードは新しい retry dep 群に replaces で差し替わるため）。
    issues: "list[str]" = []
    graph = bus.read_graph() or {}
    gnodes = graph.get("nodes", {}) if isinstance(graph, dict) else {}
    for vnid, vnode in gnodes.items():
        if not isinstance(vnode, dict) or vnode.get("kind") != "verify":
            continue
        if prev_id not in (vnode.get("deps") or []):
            continue
        vres = bus.read_result(vnid)
        vdata = vres.get("data") if isinstance(vres, dict) else None
        if isinstance(vdata, dict) and isinstance(vdata.get("issues"), list):
            issues.extend(str(x) for x in vdata["issues"])
    m = re.search(r"\[agent-error:([a-z_]+)\]", prev_output)
    if m:
        issues.append(f"前回の失敗分類: {m.group(1)}")

    artifacts = bus.list_artifacts(prev_id)
    return {
        "of": prev_id,
        "output": _truncate_bytes(prev_output, limit),
        "artifact_dir": bus.node_artifact_dir(prev_id) if artifacts else None,
        "issues": issues,
        # finalize_workspace は変更が有ったときだけ delivery を rdata に積む（work.py 本体）。
        # 真なら「前回の変更はすでに作業ブランチへ反映済み＝作業ツリーの現状が前回の結果」と言える。
        "delivered": bool(prev_data.get("delivery")),
    }


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


def pick_claimable(bus: Bus, prefer_kind: str = ""):
    """claim できるノードを 1 つ選ぶ。`prefer_kind` は直前に消化した種別。

    柱3 / C9 — **同じ種別を続けて消化すると推論サーバの接頭辞キャッシュに当たる**。
    役割の骨格と固定 policy は呼び出し間で不変なので、直前と接頭辞が一致すればその分の
    prefill が丸ごと消える。実測（`eval/prefix_cache_probe.py`・gemma4:e4b・接頭辞 2904 tok）で
    prefill 中央値が **2.83s → 0.35s（3.0 倍差）**、20 呼び出しの合計で 52.0s → 17.2s だった。

    **順序の入れ替えはここだけで、モデル・プロンプト・契約はどれも触らない**（08-22 案 3）。
    元の順は `random.shuffle` ——つまり既に任意である。任意の順を、同じ任意さのまま
    キャッシュに優しい側へ寄せるだけなので、ワーカー間の衝突回避（shuffle の目的）は
    `sort` が安定であることで保たれる（同種別の中の順序は shuffle のまま）。

    **節約は接頭辞ぶんの秒数で固定**である点に注意する。1 周が数百秒のコード仕事では
    誤差だが、1 呼び出しが十数秒の判定・抽出系では効く帯に入る。
    """
    graph = bus.read_graph()
    if not graph:
        return None
    items = list(graph["nodes"].items())
    random.shuffle(items)  # ワーカー間の衝突を減らす
    if prefer_kind:
        items.sort(key=lambda kv: kv[1].get("kind", "work") != prefer_kind)
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
    # セッション開始コマンド（agent-session-commands）。agent-flow の「セッション」は
    # このワーカープロセス 1 つなので、ここで 1 回だけ走らせる（ノードごとの CLI 呼び出し
    # には入れない）。on_error='fail' が失敗したらワーカーを起こさない。
    if not run_session_commands(who, {
        "engine": "agent-flow", "workload": "flow", "cwd": os.getcwd(),
        "workspace": os.getcwd(), "agent_cli": getattr(args, "agent_cli", "") or "",
        "model": getattr(args, "model", "") or "", "run_id": getattr(args, "run_id", "") or "",
        "node_id": who,
    }):
        return 1
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
    # 直前に消化した種別。次の claim で同じ種別を優先し、接頭辞キャッシュに当てる
    # （`pick_claimable` の docstring に実測）。**claim できたときだけ**更新する
    # ——競り負けや park で更新すると、実際には走っていない種別へ寄せることになる。
    last_kind = ""
    while True:
        bus.sync_pull()
        status = bus.get_status()

        # 終端後は claim しない。cancelled で waits が消えて pending に戻ったノードを
        # 拾い直し、人が止めた run を進めてしまう事故を防ぐ（終端判定を「仕事が無いとき」
        # だけにすると、claim 可能な残骸があると永遠に動き続ける）。
        if status in TERMINAL:
            if not args.keep_alive:
                log(who, f"run が {status}。終了します。")
                return 0
            time.sleep(args.poll)
            continue

        candidate = pick_claimable(bus, last_kind)
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
        # run-level 契約は、split / evaluator が後から作ったノードにも適用する。
        node_readonly = bus.run_readonly() or node.get("readonly") is True
        _set_method_context(args.run_id, nid)
        if not bus.try_claim(nid, who, args.lease):
            continue  # 競り負け
        log(who, f"claim 成功: {nid} [{kind}] — {node['goal'][:55]}")
        last_kind = kind
        # 実行に使うエージェント（agent executor のみ）。実効解決（control 上書き・縮退込み）は
        # ここでしか分からないので、claimed イベントと result に事実として残す——読み手
        # （dashboard のノード詳細）に設定からの再解決（同じ規則の 2 実装）をさせない。
        # ponytail: claim 時の 1 回だけ解決。実行中に control が変わると数分ずれうる。
        agent_cli = agent_model = None
        node_agent = dict(node.get("agent")) if isinstance(node.get("agent"), dict) else None
        if node.get("tier"):
            node_agent = node_agent or {}
            node_agent["tier"] = str(node["tier"])
        if kind == "human":
            bus.event(who, "claimed", node=nid)
            try:
                request = park_human_interaction(bus, nid, node, who, watch_interval)
            except Exception as e:  # noqa: BLE001 — 壊れた user-plan も claim を残さず失敗終端する
                bus.write_result(nid, who, "failed", f"human interaction エラー: {e}",
                                 {"error_class": "content"}, kind="human")
                bus.release_claim(nid, who)
                bus.event(who, "result", node=nid, status="failed")
                bus.sync_push(f"result {nid} [failed] by {who}")
            else:
                log(who, f"human wait: {nid}（{request['interaction_id']}）— claim 解放")
            time.sleep(random.uniform(0, 0.3))
            continue
        # 承認済み Execution Envelope（agent-project の snapshot）を実行文脈へ据える。
        # candidate_permissions が Resolver の明示固定（pin / trial 承認）として効く。
        _set_execution_envelope(read_json(bus.meta_path))
        if args.executor == "agent":
            agent_cli, _model_ov = _effective_agent(kind, getattr(args, "model", None), node_agent)
            agent_model = _model_ov
        selection = _selection_meta(kind, node_agent)
        # 処理契約（node.operation）は claim / result に事実として残す。局所修正の適格は
        # 宣言（operation_class）でなく機械判定（nodecontract の 1 実装）で、満たさない
        # 理由（blockers）も残す——読み手（audit / E6 ハーネス）が再判定しないため（§8.3）。
        operation = node.get("operation") if isinstance(node.get("operation"), dict) else None
        op_meta = {}
        if operation:
            op_meta["operation_class"] = str(operation.get("operation_class") or "") or None
            if (operation.get("scope") or {}).get("write"):
                blockers = _nodecontract.local_patch_blockers(operation)
                if blockers:
                    op_meta["local_patch_blockers"] = blockers
        bus.event(who, "claimed", node=nid,
                  **({"agent_cli": agent_cli, "model": agent_model, **selection, **op_meta}
                     if agent_cli else {}))

        # throttle（バックプレッシャ）: 同時未決着イシューが上限に達していたら、起票せず
        # throttled park して claim を解放する。エラーにはしない＝人のレビュー速度に発行を
        # ペーシングするだけ（枠が空けば service_waits が解除 → 通常起票）。deferring executor
        # かつ max_open_issues>0 のときだけ働く（agent/stub 等は waits が空なので発火しない）。
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
        dep_results, dependency_context = _collect_dep_results(bus, node, kind)
        read_allocation = normalize_read_allocation(node.get("read_allocation"))
        # 差分修復リトライ（案 B-1・オプトイン）。node が retry ノード（replaces を持つ）
        # かつ設定で有効なときだけブリーフを組み立てる（既定 off は None＝プロンプト不変）。
        repair = repair_brief(bus, node, args)
        # run の元要求（全体文脈）。対応 executor（agent の flow-worker プロンプト等）へ渡す。
        _meta_now = read_json(bus.meta_path) or {}
        run_request = str(_meta_now.get("request", ""))
        # グローバル指示（agent-instructions）: 投入ノードが meta へ固定した描画済みブロック。
        # ワーカーはローカルの instructions.json を読まず、この run スナップショットを唯一の基準に使う。
        _gi = _meta_now.get("instructions")
        run_instructions = str(_gi.get("text", "")) if isinstance(_gi, dict) else ""
        _note_instructions_applied(_gi.get("revision") if isinstance(_gi, dict) else None)
        # プロジェクト文脈（案 H・オプトイン）: orchestrate が run 作成時に meta へ固定した
        # スナップショット。ワーカーはこれだけを基準にし、agent-project 側のファイルは読まない。
        _rc = _meta_now.get("context")
        run_context = str(_rc.get("text", "")) if isinstance(_rc, dict) else ""
        # 中間成果物プロトコル: 自ノードの出力先を用意し、依存ノードの成果物パスを集める。
        # これにより大きな成果物は output/data に貼らずファイル参照で受け渡せる。
        art_dir = bus.ensure_artifact_dir(nid)
        dep_arts = {d: bus.node_artifact_dir(d) for d in node.get("deps", [])}
        # ワークスペース（この run の唯一の書込先）を temp 領域へ clone し、作業ブランチ af/<run_id>
        # を base から作ってエージェントへ渡す（書込先が無ければ読み取り専用 run）。
        goal = node["goal"]
        ws = bus.run_workspace()
        references = bus.run_references()
        ref_note = reference_instruction(references)
        # 実行中は心拍で lease を延長し続け、長時間タスクでも再 claim されないようにする
        hb = Heartbeat(bus, nid, who, args.lease)
        hb.start()
        rdata = None
        delivery = None
        try:
            ws = ensure_workspace_clone(ws, args.run_id)
            # 作業指示は goal に結合せず別引数で渡す（goal を汚さない）。
            instruction = "\n".join(
                s for s in (workspace_instruction(ws) if ws else "", ref_note) if s)
            if kind == "base-sync":
                rdata = sync_workspace_base(ws)
                if rdata.get("status") == "conflict":
                    files = "\n".join(f"- {p}" for p in rdata.get("conflict_files", []))
                    sync_goal = (f"{goal}\n\n競合ファイル:\n{files}\n\n"
                                 "Git コマンドは実行せず、各ファイルの競合を内容に沿って解消してください。")
                    output, agent_data = call_executor(
                        execute_agent, "work", sync_goal, dep_results, args.model,
                        art_dir, dep_arts, instruction, workspace=ws,
                        references=references, request=run_request,
                        instructions=run_instructions,
                        prompt_table=bool(getattr(args, "prompt_table", False)),
                        repair=repair, context=run_context, read_allocation=read_allocation,
                        agent=node_agent, readonly=node_readonly)
                    if isinstance(agent_data, dict):
                        rdata.update(agent_data)
                else:
                    output = (f"target {rdata.get('target', '')} は統合済み"
                              if rdata.get("status") == "noop" else
                              f"target {rdata.get('target', '')} を競合なしで統合")
            else:
                output, rdata = call_executor(execute, kind, goal, dep_results, args.model,
                                              art_dir, dep_arts, instruction, workspace=ws,
                                              references=references, request=run_request,
                                              instructions=run_instructions,
                                              prompt_table=bool(getattr(args, "prompt_table", False)),
                                              repair=repair, context=run_context,
                                              read_allocation=read_allocation, agent=node_agent,
                                              readonly=node_readonly,
                                              decision=node.get("decision"))
            if kind != "verify" and isinstance(rdata, dict) and rdata.get("ok") is False:
                if kind == "base-sync":
                    failure_class = _work_failure_class(kind, output, rdata)
                    output = f"[agent-error:{failure_class}] {output}"
                    rdata["error_class"] = failure_class
                rstatus = "failed"
            else:
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
            rstatus = "failed"
            # executor が例外に載せた構造化データ（gitlab 却下の issue_iid / guidance 等）は
            # 承認と対称に failed result の data として残す（消費側の文字列マッチ依存を無くす）
            edata = getattr(e, "data", None)
            if isinstance(edata, dict):
                rdata = edata
            # 失敗トリアージの構造化: 評価役・viewer・agent-project が output 先頭の
            # 文字列タグに依存せず読めるよう、分類と in-place 試行数を data に載せる。
            # transient がここへ来た＝レイヤ1（run_agent 内の再試行）を使い切っている。
            failure_class = _work_failure_class(kind, str(e), rdata)
            tag = f"[agent-error:{failure_class}] " if kind == "base-sync" else ""
            output = f"{tag}実行エラー: {e}"
            chain = agent_error_chain(str(e))
            rdata = {**(rdata if isinstance(rdata, dict) else {}),
                     "error_class": failure_class}
            # 観測した分類を全部残す。error_class（先頭＝proximate cause）だけを保存すると、
            # 分類器が後で直っても保存済みの記録は誤ったままになる（実際そうなった）。
            if len(chain) > 1:
                rdata["error_chain"] = chain
            attempts = getattr(e, "attempts", None)
            if attempts:
                rdata["attempts"] = int(attempts)
        finally:
            hb.stop()

        # 心拍が claim 喪失を検知した場合（lease 失効中に他ワーカーが正当に獲得）、
        # 相手が既に結果を確定していたら自分の結果で上書きしない（後勝ち上書きの防止）。
        if hb.lost.is_set():
            bus.sync_pull()
            if bus.has_result(nid):
                log(who, f"警告: {nid} の claim を喪失し他ワーカーが結果を確定済み——"
                         "この実行の結果は破棄します")
                if getattr(args, "cleanup_per_node", False) and ws:
                    cleanup_workspace()
                time.sleep(random.uniform(0, 0.3))
                continue
            log(who, f"警告: {nid} の claim を喪失（他ワーカーが実行中の可能性）。"
                     "結果は未確定のため自分の結果を書き込みます")

        # 生成された中間成果物を run_dir 相対パスで記録（後続・status から発見できる）
        artifacts = [os.path.relpath(p, bus.run_dir) for p in bus.list_artifacts(nid)]
        if delivery:  # ワークスペースへ push したブランチ/コミットを result に残す（消費側が追跡）
            rdata = {**(rdata if isinstance(rdata, dict) else {}), "delivery": delivery}
        elif ws and rstatus == "done":
            # workspace は有るが commit 対象の差分が無い。読み手が「古い result で公開状態不明」と
            # 「公開不要」を推測で混同しないよう、事実を明示する。
            rdata = {**(rdata if isinstance(rdata, dict) else {}), "publication": {
                "state": "not-required", "url": ws.get("url"), "branch": ws.get("branch"),
                "attempted_at": now_iso(),
            }}
        output, context_allocation = extract_read_report(output, read_allocation)
        method_app = _last_methods(kind) if args.executor == "agent" else {"methods": [], "trial": None}
        # 候補ベースの縮退（run_agent が transient 上限で Resolver の次候補へ下りた）を result へ写す。
        # claim 時の解決のまま書くと「rank1 で実行した」と読める記録が残る。候補は receipt の
        # execution_decision（fallback_from 付き）、実効 CLI は agent_cli（変種振替後）に分けて持つ。
        fallback = last_execution_fallback(kind) if args.executor == "agent" else None
        if fallback:
            agent_cli, agent_model = _effective_agent(kind, getattr(args, "model", None), node_agent)
            # 結果と台帳へ残すのは正典名（用途は kind / operation_class 側が持つ）。
            agent_cli = _canonical_cli(agent_cli)
            if isinstance(selection.get("execution_decision"), dict):
                to_cli, to_model = fallback["to"].split("/", 1)
                selection["execution_decision"].update(
                    agent_cli=to_cli, model=to_model, fallback_from=fallback["from"])
        # 実行した PC を結果に残す（読み手が who の綴りを割って推測しないで済むように）
        bus.write_result(nid, who, rstatus, output, rdata, artifacts=artifacts,
                         node=this_pc(args), kind=kind,
                         agent_cli=agent_cli, model=agent_model,
                         context_allocation=context_allocation,
                         dependency_context=dependency_context, escalation=node_agent,
                         methods=method_app.get("methods"), trial=method_app.get("trial"),
                         **selection, **op_meta)
        if node_agent:
            _node_budget_record(0, ref=kind, agent_cli=agent_cli or "", model=agent_model or "",
                                extra={"event": "model_escalation", "escalation": node_agent})
        bus.event(who, "result", node=nid, status=rstatus)
        bus.sync_push(f"result {nid} [{rstatus}] by {who}")
        log(who, f"完了: {nid} [{rstatus}]")
        if getattr(args, "cleanup_per_node", False) and ws:
            cleanup_workspace()  # ノード完了/失敗ごとに clone を即削除（長命 worker のディスク抑制）
        time.sleep(random.uniform(0, 0.3))  # 負荷分散: 他ノードに claim の機会を渡す
