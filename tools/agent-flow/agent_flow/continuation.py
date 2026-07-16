from __future__ import annotations
# continuation.py — 元 agent-flow.py の 3955-4255 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# Continuation — パターンに応じて done / replan（タスク追加）を決める
# --------------------------------------------------------------------------
def _expand_splits(nodes: dict, results: dict, max_fanout: int,
                   review: bool = False, request: str = "", exemplar_first: bool = False):
    """データ駆動の動的 fan-out: 完了した split ノードの data(リスト)を見て、
    実行時に要素ごとの map タスクと、それらを集約する reduce タスクを生成する。
    （reduce は展開時に作るので、split 完了直後に reduce が先走り実行されない）
    review 時は map と reduce の間に検証 gate を挟む。
    map・reduce ゴールには元の要求（intent）を埋め込み、各要素への適用と最終整形
    （並べ替え・重複排除など要求由来の集約条件）が失われないようにする。

    exemplar_first=True のときは「見本先行」分解にする: まず先頭1件(pilot map)と
    その検証ゲートだけを出し、ゲート通過後に残りの map（pilot を範に取る = pilot に依存）
    と reduce を展開する。同様手順の繰り返しで、1件で手順を固めてから残りを流す。"""
    new = []
    have = set(nodes)
    for nid, node in nodes.items():
        if node.get("kind") != "split":
            continue
        r = results.get(nid, {})
        if r.get("status") != "done":
            continue
        if f"{nid}-reduce" in have:  # 既に完全展開済み
            continue
        items = r.get("data")
        if not isinstance(items, list) or not items:
            continue
        items = items[:max(1, max_fanout)]  # 暴走防止のクランプ
        intent = (request or node.get("goal", "")).strip()

        def _mgoal(i, item):
            return f"{intent}（対象要素: {item}）" if intent else f"{nid} 要素{i+1}: {item}"

        reduce_goal = (f"{intent}（各 map の結果を要求どおりに集約・整形して最終成果にまとめる）"
                       if intent else f"{nid} の結果を集約")
        pilot_gate = f"{nid}-pilot"
        m1 = f"{nid}-m1"

        if exemplar_first:
            if m1 not in have:
                # Stage 1: pilot map 1件＋その検証ゲートだけを出す（残りはまだ展開しない）
                new.append({"id": m1, "goal": _mgoal(0, items[0]), "deps": [], "kind": "map"})
                new.append({"id": pilot_gate,
                            "goal": f"先行1件(map)を検証し、残りに使う手順・基準を固める: {intent}"[:200],
                            "deps": [m1], "kind": "verify"})
                continue
            if results.get(pilot_gate, {}).get("status") != "done":
                continue  # pilot ゲート通過まで残りは展開しない
            # Stage 2: 残り map（pilot を範に取り、ゲート通過後に走る）＋ reduce
            map_ids = [m1]
            for i, item in enumerate(items[1:], start=1):
                mid = f"{nid}-m{i+1}"
                map_ids.append(mid)
                new.append({"id": mid, "goal": _mgoal(i, item),
                            "deps": [m1, pilot_gate], "kind": "map"})
        else:
            map_ids = []
            for i, item in enumerate(items):
                mid = f"{nid}-m{i+1}"
                map_ids.append(mid)
                # 要素だけでなく「何をするか」を渡さないと map が意図を失う
                new.append({"id": mid, "goal": _mgoal(i, item), "deps": [], "kind": "map"})

        reduce_deps = map_ids
        if review:  # 集約前の事前チェック / 敵対的レビュー。reduce は map＋gate に依存
            gid = f"{nid}-gate"
            new.append({"id": gid, "goal": f"{nid} の map 結果を集約前に検証",
                        "deps": map_ids, "kind": "verify"})
            reduce_deps = map_ids + [gid]
        new.append({"id": f"{nid}-reduce", "goal": reduce_goal,
                    "deps": reduce_deps, "kind": "reduce"})
    return new


def continue_stub(request: str, nodes: dict, results: dict, iteration: int,
                  max_fanout: int = 50, review: bool = False, exemplar_first: bool = False,
                  max_retries: int = 3):
    """パターン継続（LLM 無し版）:
       - データ駆動 fan-out: split 完了 → 要素ごとの map + reduce を生成
       - classify-and-act: 分類完了 → 振り分け先の専門タスクを追加
       - adversarial / loop-until-done: verify が fail → 作り直し + 再検証
       - 失敗タスク: retry を 1 回追加

    サーキットブレーカー: 同一系統の作り直し回数（retries）が max_retries に達したら、
    その系統の verify-fail / 失敗ノードに対する再タスクをこれ以上生成しない。達成不可能な
    完了条件で無限に再タスクを積み続けるのを防ぐ（node["retries"] で系統ごとに計上）。"""
    new = _expand_splits(nodes, results, max_fanout, review, request, exemplar_first)
    have = set(nodes)
    tripped = []  # サーキットブレーカーが作動した系統（理由表示用）

    def fresh(tid):
        return tid not in have and tid not in [t["id"] for t in new]

    for nid, node in nodes.items():
        r = results.get(nid, {})
        if r.get("status") != "done" and r.get("status") != "failed":
            continue
        kind = node.get("kind", "work")
        tries = int(node.get("retries", 0))  # この系統で既に作り直した回数
        # 1) classify → 専門タスクへルーティング（追加のみ）
        if kind == "classify" and r.get("status") == "done":
            actid = f"{nid}-act"
            if fresh(actid):
                label = str(r.get("output", "")).split("=")[-1].strip() or "general"
                new.append({"id": actid, "goal": f"{label} 専門処理: {request[:30]}",
                            "deps": [nid], "kind": "work"})
        # 2) verify が fail → 依存を作り直して再検証（loop-until-done / adversarial）
        #    replaces で依存元（gen/verify）を置き換え、後続の依存を付け替える
        if kind == "verify" and "fail" in str(r.get("output", "")):
            if tries >= max_retries:
                tripped.append(nid)  # サーキット開放: これ以上作り直さない（達成不可能とみなす）
            else:
                for dep in node.get("deps", []):
                    rid = f"{dep}-r{iteration+1}"
                    if fresh(rid):
                        goal = nodes.get(dep, {}).get("goal", "").replace("FLAKY", "ok")
                        new.append({"id": rid, "goal": f"[retry] {goal}", "deps": [],
                                    "kind": nodes.get(dep, {}).get("kind", "work"),
                                    "replaces": dep, "retries": tries + 1})
                vid = f"{nid}-r{iteration+1}"
                if fresh(vid):
                    new.append({"id": vid, "goal": "再検証",
                                "deps": [f"{dep}-r{iteration+1}" for dep in node.get("deps", [])],
                                "kind": "verify", "replaces": nid, "retries": tries + 1})
        # 3) 失敗タスクの retry（失敗ノードを置き換え、依存元を付け替える）
        if r.get("status") == "failed":
            if tries >= max_retries:
                tripped.append(nid)  # サーキット開放: 反復失敗するタスクは諦める
            else:
                rid = f"{nid}r"
                if fresh(rid):
                    goal = node.get("goal", "").replace("FAIL", "ok")
                    new.append({"id": rid, "goal": f"[retry] {goal}", "deps": [],
                                "kind": node.get("kind", "work"),
                                "replaces": nid, "retries": tries + 1})
    if new:
        return "replan", new, f"{len(new)} 件追加"
    if tripped:
        return "done", [], (f"サーキットブレーカー作動: {','.join(tripped)} は "
                            f"{max_retries} 回の作り直しでも未達のため打ち切り")
    return "done", [], "全パターン完了"


_RETRY_SUFFIX_RE = re.compile(r"-r\d+")


def _retry_depth(nid: str, node: dict) -> int:
    """ノードの作り直し回数（系統の深さ）。明示の retries カウンタを優先し、無ければ
    id の -rN 連鎖（例: gen1-r1-r2 → 2）から推定する。サーキットブレーカー判定に使う。"""
    if node and node.get("retries"):
        return int(node["retries"])
    return len(_RETRY_SUFFIX_RE.findall(nid or ""))


def _circuit_tripped(nodes: dict, results: dict, max_retries: int) -> list:
    """達成不可能な完了条件で打ち切るべき系統の id 一覧を返す。
    verify が fail し続ける／失敗を繰り返すノードのうち、作り直しが max_retries に
    達したものを「これ以上再タスクを積まない」対象として検出する。"""
    out = []
    for nid, node in nodes.items():
        r = results.get(nid, {})
        st = r.get("status")
        is_verify_fail = node.get("kind") == "verify" and "fail" in str(r.get("output", ""))
        if (st == "failed" or is_verify_fail) and _retry_depth(nid, node) >= max_retries:
            out.append(nid)
    return out


def human_feedback_from_results(results: dict, limit: int = 1200) -> str:
    """ノード結果の**構造化 data から人フィードバック**（`guidance` / `notes[].body`）を集める。
    executor 非依存: gitlab に限らず、委譲系 executor が結果コントラクトに載せた人の指摘を汎用に読む
    （`decision` の有無や executor 名で分岐しない）。評価役（replan）へ「人の指摘」として渡し、
    待機ノードの付け替え・ノード追加を人フィードバック駆動で決めさせるための材料。"""
    out: list[str] = []
    for nid, r in (results or {}).items():
        d = (r or {}).get("data")
        if not isinstance(d, dict):
            continue
        g = str(d.get("guidance") or "").strip()
        if g:
            out.append(f"[{nid}] {g}")
        for note in d.get("notes") or []:
            if isinstance(note, dict):
                b = str(note.get("body") or "").strip()
                if b:
                    out.append(f"[{nid}] {b}")
    return "\n".join(out)[:limit]


_INFLIGHT_FB_MARK = "\n\n[人からの指摘・反映すること]"


def _inflight_amend_pending(bus, graph, who, args, consumed_fb: set) -> int:
    """静止を待たず、settled ノードに新しく載った人フィードバック（`data.guidance`/`notes`・差し戻し含む）を
    **待機（pending）ノードの spec に即時反映**する。実行中(claimed)・監視中(waiting)・終端ノードは触らない
    ＝作業中は不変（安全）。決定的（LLM 不要）・冪等（同一発生源の指摘は二度入れない）。反映した待機ノード数を返す。
    executor 非依存: guidance/notes を汎用に読む（gitlab 固有の分岐は無い）。**ノード追加**は二重生成を避けるため
    静止時の評価役（continue_*）に委ね、本関数は既存待機ノードの書き換えに限定する。"""
    nodes = graph["nodes"]
    new_pieces = []
    for nid in list(nodes.keys()):
        d = (bus.read_result(nid) or {}).get("data")
        if not isinstance(d, dict):
            continue
        pieces = [str(d.get("guidance") or "").strip()] if str(d.get("guidance") or "").strip() else []
        for note in d.get("notes") or []:
            if isinstance(note, dict) and str(note.get("body") or "").strip():
                pieces.append(str(note["body"]).strip())
        if not pieces:
            continue
        text = " / ".join(pieces)
        k = f"{nid}:{hashlib.sha1(text.encode()).hexdigest()[:16]}"  # 内容ハッシュで冪等（同じ長さの別指摘を落とさない）
        if k in consumed_fb:
            continue
        consumed_fb.add(k)
        new_pieces.append(text)
    if not new_pieces:
        return 0
    inject = _INFLIGHT_FB_MARK + "\n" + "\n".join(f"- {p}" for p in new_pieces)
    amended = 0
    for nid, entry in list(nodes.items()):
        if bus.node_state(nid) != "pending":           # 待機ノードのみ（実行中/監視中/終端は不変）
            continue
        entry["goal"] = str(entry.get("goal") or "") + inject
        spec = {"id": nid, "goal": entry["goal"], "deps": entry.get("deps", []),
                "kind": entry.get("kind", "work")}
        if entry.get("retries"):
            spec["retries"] = entry["retries"]
        bus.write_task(spec)                           # 待機ノードの spec を書き換え（claim 前なので安全）
        amended += 1
    if amended:
        bus.write_graph(graph)
        bus.event(who, "inflight_amend", nodes=amended)
        bus.sync_push(f"in-flight 反映 run {args.run_id}: 待機 {amended} ノードへ人指摘")
        log(who, f"in-flight: 待機 {amended} ノードへ人の指摘を反映（実行中は不変）")
    return amended


def _evaluator_fallback(results: dict, why: str):
    """評価役（LLM）が判定を返せなかったときのフェイルクローズ判定。
    全ノードが done なら自明に done（評価は形式的なもの）。失敗・未達ノードが
    残っているのに done へ倒すと偽成功として消費者へ渡るため failed で終端する
    （resume / agent-project のリトライが done ノードを温存して続きから走る）。"""
    statuses = [r.get("status") for r in (results or {}).values()]
    if statuses and all(s == "done" for s in statuses):
        return "done", [], f"{why}（全ノード done のため done 終端）"
    bad = [nid for nid, r in (results or {}).items() if r.get("status") != "done"]
    return "failed", [], (f"{why}。未達ノード {','.join(bad[:5])} が残るため "
                          "done ではなく failed で終端します（再開で続きから実行）")


def continue_agent(request: str, nodes: dict, results: dict, iteration: int,
                  max_fanout: int = 50, review: bool = False, exemplar_first: bool = False,
                  max_retries: int = 3):
    # データ駆動 fan-out は機械的に展開（LLM 判断不要）。先に処理する。
    fanout_tasks = _expand_splits(nodes, results, max_fanout, review, request, exemplar_first)
    if fanout_tasks:
        return "replan", fanout_tasks, f"data-driven fan-out: +{len(fanout_tasks)}"
    # サーキットブレーカー: 作り直しが上限に達した系統は達成不可能とみなし打ち切る
    # （評価役 LLM が無限に再タスクを積み続けるのを防ぐ）。
    tripped = _circuit_tripped(nodes, results, max_retries)
    if tripped:
        return "done", [], (f"サーキットブレーカー作動: {','.join(tripped)} は "
                            f"{max_retries} 回の作り直しでも未達のため打ち切り")
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    summary = "\n".join(
        f"- {nid} ({nodes.get(nid, {}).get('kind','work')}) "
        f"[{r.get('status')}]: {str(r.get('output',''))[:160]}"
        for nid, r in results.items()
    )
    # 人フィードバック（委譲 executor の guidance/notes・差し戻し含む）を評価役へ明示する。
    # これにより replan を「人の指摘駆動」で決められる（待機ノードの付け替え／ノード追加）。
    hf = human_feedback_from_results(results)
    hf_block = (f"\n\n人からの指摘（最優先で反映すること。executor 非依存の結果コントラクト由来）:\n{hf}"
                if hf else "")
    # flow-worker スキルがあれば評価規律入りプロンプトを使う（無ければ従来の組み込み）。
    # decision JSON の出力契約はスキル側でも同一に保たれている。
    prompt = _flow_worker_prompt({
        "role": "evaluator", "request": request, "results_summary": summary,
        "human_feedback": hf, "patterns_catalog": catalog,
        "max_retries": max_retries, "iteration": iteration,
    })
    if not prompt:
        prompt = (
        "あなたは分散 Dynamic Workflow の評価役です。7 パターンを踏まえ、現在の結果が要求を満たすか判定し、"
        "必要なら次のタスクを追加してください（例: 分類結果に応じた専門タスク、検証 fail の作り直し、"
        "統合や追加候補の生成）。**人からの指摘があれば最優先で反映**し、必要なら新タスク追加や、"
        "まだ着手されていない**待機ノードの差し替え（replaces で置換）**で対応してください"
        "（実行中のノードは触らない＝評価は run が静止したときだけ行われます）。\n"
        f"ただし同じ完了条件のために作り直しを繰り返しても改善しない場合（達成不可能な条件など）は、"
        f"同一タスクの作り直しは最大 {max_retries} 回までとし、それを超えるなら無理に再タスクを足さず "
        '"done" を返してください。\n'
        f"パターン:\n{catalog}\n\n"
        "出力は JSON のみ: "
        '{"decision":"done"|"replan","reason":"...",'
        '"new_tasks":[{"id":"...","goal":"...","deps":[],"kind":"work","replaces":"<任意: 差し替える待機ノード id>"}]}\n'
        "既存 id と重複しない id を使うこと。done のとき new_tasks は空配列。\n\n"
        f"元の要求: {request}{hf_block}\n\n現在の結果:\n{summary}"
    )
    try:
        text = run_agent(prompt, None, purpose="evaluator")
    except Exception as e:  # noqa: BLE001
        # 評価役の LLM 呼び出し自体の失敗。transient / quota はレイヤ1（run_agent 内の再試行）を
        # 経てなお失敗している＝環境の一時不調であり、内容判定（fallback の done/failed 推定）に
        # 進まずタグ付きで failed 終端する → レイヤ4（auto-heal）/ 人の環境復旧が拾う。
        triage = classify_agent_failure(str(e))
        if triage and triage[0] in ("transient", "quota"):
            return "failed", [], (f"[agent-error:{triage[0]}] 評価役の呼び出しが失敗: {e}"
                                  "（done ノードは温存・自動/手動の再開で続きから）")
        return _evaluator_fallback(results, f"評価役を呼び出せず: {e}")
    try:
        data = extract_json(text)
    except Exception as e:  # noqa: BLE001
        # 出力契約違反（JSON 崩れ）→ レイヤ2: 契約違反を指摘して修復再呼び出し（有界）。
        data = _repair_json_output(prompt, text, "evaluator", e)
        if data is None:
            # フェイルクローズ: 評価役の失敗を done に倒すと、失敗ノードが残った run が
            # 「成功」として消費者へ渡る。全ノードが done ならその判定は自明なので done、
            # 未達・失敗ノードが残るなら failed で終端し、resume/リトライへ回す。
            return _evaluator_fallback(results, f"評価出力を解釈できず: {e}")
    # planner がオブジェクトでなくベア配列を返すことがある → new_tasks とみなす
    if isinstance(data, list):
        data = {"decision": "replan", "new_tasks": data}
    if not isinstance(data, dict):
        return _evaluator_fallback(results, "評価出力が想定形でない")
    new = _coerce_tasks(data.get("new_tasks"), existing=nodes)  # 既存 id と衝突しないよう正規化
    if data.get("decision") == "replan" and new:
        return "replan", new, str(data.get("reason", ""))
    return "done", [], str(data.get("reason", "done"))

