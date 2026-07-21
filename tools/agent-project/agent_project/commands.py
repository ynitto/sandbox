from __future__ import annotations
# commands.py — 元 agent-project.py の 7069-7604 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 人の操作コマンド（いずれも案件毎の決定記録を残す）
# ---------------------------------------------------------------------------
def approve_review_done(cfg: Config, t: Task, reason: str) -> "tuple[bool, str]":
    """review（検収待ち）タスクの承認を done 確定させる共通経路。
    CLI の approve と needs の空 [x]（チェックのみ＝承認）の両方から呼ばれる——
    経路によって「done 確定」と「最初から再実行」に解釈が割れると、承認したはずの
    検証済み成果が丸ごと作り直される手戻りになる。(ok, message) を返す。
    タスク MR があれば Stage 2 と同一規則で自動決着（クリーンならマージ・未クリーンなら
    差し戻しコメントを付けて review のまま needs 票を書き直す）。"""
    tid = t.id
    mr_ok, mr_msg = finalize_task_delivery(cfg, t)
    if not mr_ok:
        mr_url = str(t.get("mr_url") or "")
        delivery = delivery_entries(cfg, t, mr_url=mr_url)
        try:
            evidence = delivery_evidence(cfg, "", None, "local", task=t, mr_url=mr_url)
        except Exception:  # noqa: BLE001 — 統合失敗の票は必ず残す
            evidence = f"- MR: {mr_url}" if mr_url else ""
        write_needs_file(cfg, t, f"承認されたが成果ブランチを統合できない: {mr_msg}", review=True,
                         evidence=evidence, mr_url=mr_url, delivery=delivery)
        return (False, f"{tid}: 成果ブランチをターゲットへ統合できないため done にできません"
                       f"（{mr_msg}）。解消後に再度 approve してください。")
    lines = [f"{tid}: {mr_msg}"] if mr_msg else []
    # 検収ゲートの承認 = done 確定（verify は実行済み。保持した成果参照で納品書を書く）
    ex = dict(t.extra)
    ref = ex.get("gate_ref", "")
    ts = ex.get("gate_ts") or _now_ts()
    vmsg = ex.get("gate_vmsg", "")
    gate_branch = ex.get("gate_branch", "")
    t.status = "done"
    autonomy_record(cfg, t, clean=True)          # 検収承認＝手戻りなし。track の信頼を上げる
    t.drop("gate_ref", "gate_ts", "gate_vmsg", "gate_branch")
    # review 時に保持した所在（ref/ブランチ）を受領書へ引き継ぐ（どこに成果物があるかを残す）
    gate_ev = (f"- 成果物: {ref}\n- 所在: {cfg.workdir}"
               + (f" / ブランチ {gate_branch}" if gate_branch else "")) if ref else ""
    append_delivery(cfg, t, ref, ts, branch=gate_branch)
    disp = "done（承認・納品書）"
    if cfg.do_archive:
        archive_task(cfg, t, vmsg or f"承認: {reason}", ref, ts, evidence=gate_ev)
    else:
        delete_task_file(cfg, t)
        disp = "done（承認・削除）"
    clear_needs_file(cfg, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を検収承認",
                         action="approve-done", reason=reason, affects=f"{tid} → done",
                         # 承認理由を learn 化して類似案件の判断材料に残す（approve-and-fix と対称）
                         learn=(t.title, reason) if reason and cfg.learn_capture else None)
    lines.append(f"{dr}: {tid} を承認し {disp} 確定しました。")
    # cohort の pilot 承認なら、固めた定義から残りのタスクを生成して ready にする
    if t.get("cohort_role") == "pilot":
        members = materialize_cohort_rest(cfg, t, feedback=reason)
        if members:
            lines.append(f"cohort {t.get('cohort')}: 残り {len(members)} 件を生成しました "
                         f"（{', '.join(m.id for m in members[:6])}{' …' if len(members) > 6 else ''}）。")
    return (True, "\n".join(lines))


def cmd_approve(cfg: Config, tid: str, reason: str, complete: bool = False) -> int:
    """判断待ちの承認。

    complete=True は「この成果を受け入れて完了（done 確定）にする」という人の明示。
    検収待ち（blocked / review）で有効で、承認理由の文面には依存しない。
    complete=False（既定）は従来どおり「ブロックを解いて積み直す」。
    """
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        # プロジェクト milestone の承認（収束候補 → done 確定）。backlog タスクではない。
        # 複数 charter 運用では project.json の charters マップから該当 charter を探す。
        data = load_project_state(cfg)
        candidates = [(None, data)] if data.get("id") else []
        for cname, st in (data.get("charters") or {}).items():
            candidates.append((cname, st))
        for cname, st in candidates:
            if st.get("id") != tid:
                continue
            status = str(st.get("status") or "")
            if status == REASON_PROJECT_CONVERGED:
                ch = _load_named_charter(cfg, cname)
                finalize_project(cfg, st, reason, charter=ch, charter_name=cname)
                print(f"プロジェクト done（承認・最終納品書）: {tid}")
                return 0
            if status == REASON_PROJECT_ACCEPTED:
                # 二度押し・取り込み遅延による再送は冪等に成功扱いする（.err に退避させない）
                print(f"プロジェクト {tid} は承認済み（accepted）です（何もしません）。")
                return 0
            # milestone の id には一致したが収束候補ではない＝実行中/ブロック等の古いカードからの
            # 承認。従来の「タスクが見つかりません」では原因が分からなかったため状態を明示する。
            print(f"エラー: プロジェクト {tid} は収束候補（converged）ではありません"
                  f"（現在: {status or '未実行'}）。実行中の再評価か needs のタスク対応を待って"
                  f"から承認してください。", file=sys.stderr)
            return 2
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    # 人手の承認はタスクを consumable/doing から確定遷移させる。worker のクラッシュや
    # review/blocked 滞留で残った古い claim ロック（claims/<id>.lock）を先に掃除しておく。
    # release_claim は冪等（無ければ no-op）なので、新鮮なロックが無い通常ケースでも無害。
    release_claim(cfg, t)
    if t.norm_status() == "proposed":
        # 実行前レビューの承認（実行を許可）。done 確定ではない
        _plan_approve(cfg, t, reason)
        print(f"plan-review: {tid} を承認しました（→ {t.status}）。")
        return 0
    if t.norm_status() == "review":
        ok, msg = approve_review_done(cfg, t, reason)
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        if msg:
            print(msg)
        return 0
    # verify 未定義、または成果生成 run は完了したが最終検証で失敗して人へ回った blocked
    # タスクの承認 = done 確定。後者は検証差異を人が確認・受容した明示的な納品判断である。
    # 工程は完了済みで、needs 票も「成果を確認し、問題なければ approve してください」と
    # 案内している——ここで ready へ積み直すと同じ工程が再実行され、また verify 未定義で
    # blocked に戻る無限往復になる（承認で完了できないと報告された不具合）。
    # env_resume は通常の環境障害だけでなく、完了run後の回帰検証失敗にも付く。
    # 一律除外せず、完了runと「検証差異を受容して完了」の明示理由がそろった場合だけ
    # done 確定する。[agent-error:*] の純粋な環境障害は従来どおり再開扱いに残す。
    # 「承認 = 完了」か「承認 = 積み直し」かは、**呼び出し側が complete で明示する**。
    # 以前は承認理由の文面（"検証"・"受容" 等のキーワード）から推定していたが、
    # 推定が外れると黙って ready へ積み直し、同じ工程が再実行されてまた blocked に戻る
    # 無限往復になっていた（承認して完了にできない、と繰り返し報告された不具合）。
    # 検収物があるかの判断は人が画面で行う。ここで別の材料から再判定して食い違わせない。
    needs_reason = str(t.get("needs_reason") or "")
    reason_lower = needs_reason.lower()
    approval_lower = str(reason or "").lower()
    verify_undefined = (not t.verify and "verify 未定義" in needs_reason)
    # complete フラグ対応前の agent-dashboard も「承認して完了にする」
    # ボタンからこの決定的文言を送っていた。Electron は更新後も再起動まで
    # 旧 renderer が動くため、その明示意図だけは complete=True と同じに扱う。
    # 一般の approve 理由まで完了扱いにしないよう、完全一致に限定する。
    legacy_dashboard_complete = str(reason or "").strip().rstrip("。") == "成果を確認して完了を承認"
    # 旧経路（complete を渡さない CLI・古いドロップ）のための後方互換の推定。
    legacy_verification_acceptance = (
        bool(_completed_last_run(cfg, t))
        and any(word in approval_lower for word in ("verify", "検証", "テスト", "test", "回帰"))
        and any(word in approval_lower for word in ("受容", "承認", "完了", "accept"))
        and "[agent-error:" not in reason_lower
    )
    if t.norm_status() == "blocked" and (
            complete
            or legacy_dashboard_complete
            or legacy_verification_acceptance
            or (verify_undefined and not t.get("env_resume")
                and "[agent-error:" not in reason_lower)):
        ok, msg = approve_review_done(cfg, t, reason)
        if not ok:
            print(msg, file=sys.stderr)
            return 1
        if msg:
            print(msg)
        return 0
    # 委譲中の approve = 人は「このまま続行」ではなく「ブロックを解いてやり直す／進める」。
    # flow を止めないと ap/<task-id> へ二重書き込みし、次の act と競合する。
    if t.norm_status() == "offloaded" or t.get("flow_run"):
        detached = detach_flow_run(cfg, t, reason[:120] or "approve により委譲から切り離し")
        if detached:
            t.retries += 1
    t.status = "ready"
    # hold が積んだ deny を解除する。これをしないと承認が一方通行で無効になる: status を ready に
    # 戻しても policy の deny が残り続け、次の triage が policy:deny を見て即 blocked へ引き戻す。
    # 承認したはずのタスクが永久に実行されない（実際そうなっていた）。
    unheld = remove_policy(cfg.policy, "deny", tid)
    persist_task(cfg, t)
    clear_needs_file(cfg, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を人の判断から復帰",
                         action="approve-and-fix", reason=reason, affects=f"{tid} → ready",
                         learn=(t.title, reason))
    print(f"{dr}: {tid} を ready に積み直しました。"
          + (f"（policy の deny を解除）" if unheld else ""))
    return 0


def cmd_hold(cfg: Config, tid: str, reason: str) -> int:
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    append_policy(cfg.policy, "deny", tid)
    _block(cfg, t, f"hold（人が保留）: {reason}", {})
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} を保留（denylist 化）",
                         action="hold(deny)", reason=reason,
                         affects=f"{tid} → blocked, policy.deny += {tid}",
                         # hold 理由を avoid 化＝『この種は自動実行させない』予防知識として蓄積
                         # （policy.deny はこの id 限定だが、avoid は類似の新規タスクも投入時に捕まえる）
                         avoid=(t.title, reason) if reason and cfg.learn_capture else None)
    print(f"{dr}: {tid} を hold（policy.deny 追加）しました。")
    return 0


def cmd_resume_run(cfg: Config, tid: str, run_id: str, reason: str) -> int:
    """停滞・失敗した run を「続きから」やり直す（人の明示指示）。

    last_run を run_id に固定して ready へ積み直す。次の act は run_id_for がこの run を再開し、
    agent-flow が失敗ノードだけを pending へ戻して done のノードは温存する。viewer の
    「失敗した工程だけやり直す」ボタンの正規の口——従来は viewer が backlog ファイルを直接
    書き換えており、分散構成では状態リポジトリへの第二の書き手＝コミット競合の源だった。"""
    rid = str(run_id or "").strip()
    if not rid or rid != os.path.basename(rid):
        print(f"エラー: run-id が不正です: {run_id!r}", file=sys.stderr)
        return 2
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    # 実行中（orchestrator の生存リースが有効）の run への再開指示だけは拒否する。
    # run が bus に無い場合は拒否しない: bus 掃除後でも agent-flow は作業ブランチ
    # ap/<task-id> から続きを解決できる。
    # canceled / done は続きから再開できないので last_run を固定せず retries を進め、新 run にする。
    meta_path = cfg.bus / "runs" / rid / "meta.json"
    st = ""
    if meta_path.exists():
        try:
            st = str(json.loads(meta_path.read_text(encoding="utf-8")).get("status") or "")
        except (OSError, ValueError):
            st = "?"
        if st in ("canceled", "done"):
            release_claim(cfg, t)
            t.retries += 1
            t.drop("feedback", "revised", "last_run")
            t.status = "ready"
            persist_task(cfg, t)
            clear_needs_file(cfg, tid)
            dr = append_decision(cfg, tid, cfg.actor,
                                 context=f"{tid} を新 run でやり直し（{rid} は {st}）",
                                 action="resume-run", reason=reason,
                                 affects=f"{tid} → ready (retries={t.retries})")
            print(f"{dr}: {tid} を ready に積み直しました"
                  f"（{rid} は {st} のため続きからではなく新しい実行）。")
            return 0
        if not _run_resumable(cfg, rid) and st not in _FLOW_TERMINAL:
            print(f"エラー: run {rid} は実行中です（status={st}）。停止・失敗・応答なしの run "
                  f"だけ再開できます。", file=sys.stderr)
            return 2
    release_claim(cfg, t)
    # 委譲中に「続きから」を押しても、走っている flow を止めないと二重駆動になる。
    if t.norm_status() == "offloaded" or t.get("flow_run"):
        detach_flow_run(cfg, t, reason[:120] or "resume-run により委譲から切り離し")
    t.set("last_run", rid)
    # feedback / revised は「計画が変わった＝新しい run を作る」シグナルなので外す
    # （人が『この run の続きから』と明示した以上、続きからが正）。
    t.drop("feedback", "revised")
    t.status = "ready"
    persist_task(cfg, t)
    clear_needs_file(cfg, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} を run {rid} の続きから再開",
                         action="resume-run", reason=reason,
                         affects=f"{tid} → ready (last_run={rid})")
    print(f"{dr}: {tid} を ready に積み直しました（{rid} の失敗ノードだけをやり直します）。")
    return 0


def cmd_reprioritize(cfg: Config, tid: str, kind: str, reason: str) -> int:
    append_policy(cfg.policy, kind, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} の優先度を変更",
                         action=f"reprioritize({kind})", reason=reason,
                         affects=f"policy.{kind} += {tid}")
    print(f"{dr}: {tid} を {kind}（policy.{kind} 追加）しました。")
    return 0


def cmd_replan(cfg: Config, reason: str, charter_name: str = "") -> int:
    """charter からのバックログ再分解を要求する（エラー回復用の一発の口）。
    次の project パスで plan を強制し、charter を分解し直して backlog の差分を投入する。
    冪等照合は「done 以外」（現行処理中のバックログ＋却下済み）と行う＝処理中タスクとの二重投入や
    却下済みの復活はさせず、done と類似のタスクだけやり直しとして再作成を許可する（エラー回復の口）。
    複数 charter 運用では charter_name でその charter だけを対象にできる（未指定はどの charter でも消化）。
    charter が無い（backlog ループ）プロジェクトでは再分解の対象が無いためエラー。"""
    charter = _load_named_charter(cfg, charter_name or None)
    if charter is None:
        print(f"エラー: charter がありません（再分解の対象なし）: {cfg.charter}", file=sys.stderr)
        return 2
    pid = _project_id(cfg, charter) + (f"-{charter_name}" if _is_multi_charter(cfg, charter_name) else "")
    write_replan_request(cfg, reason, charter=charter_name)
    dr = append_decision(cfg, pid, cfg.actor,
                         context=f"{charter.name}: charter からのバックログ再分解を要求",
                         action="replan", reason=reason,
                         affects="次パスで charter を再分解（現行処理中のバックログと重複するものは"
                                 "投入しない。done と同種のやり直しは再作成する）")
    print(f"{dr}: charter からのバックログ再分解を要求しました（次パスで反映）。")
    return 0


# ---------------------------------------------------------------------------
# revise（人の即時フィードバック）— ループがブロックする前に、人が気づいた時点で
#   タスクの内容（title/verify/accept/依存 after/優先度 等）を修正し、自由記述の
#   feedback を次の act に必ず届ける口。needs（ループ起点・受動）の対になる能動ルート。
#   実行中（doing・新鮮なクレームあり）のタスクは `revised` マーカーを付けて予約し、
#   現在の試行の結果は確定させず（done にせず）修正内容で積み直す＝早い軌道修正。
# ---------------------------------------------------------------------------
REVISE_FIELDS = ("title", "priority", "verify", "accept", "after",
                 "note", "level", "track", *TASK_GUIDE_KEYS)
_CLEAR_VALUES = ("", "-", "none")      # フィールド削除の明示値（revise の置換規約）


def _claim_fresh(cfg: "Config", tid: str) -> bool:
    """claims/<id>.lock が生きている（= 誰かが実行中）か。stale/欠損は False（実行者不在）。

    `_claim_alive` に寄せる: 同一ホストは pid の生死で即断し、別ホストだけ TTL に従う。
    かつての TTL 専用判定は、クラッシュ直後でも最大 act_timeout+verify_timeout+60
    （~41 分、さらに act_timeout=0 なら永久）「実行中」と誤認し、revise が
    revised 予約だけして ready へ積み直せなかった（死んだ owner の claim 居座り修正の
    取りこぼし）。"""
    return _claim_alive(cfg, tid)


def _after_introduces_cycle(tasks: "list[Task]", start: "Task") -> bool:
    """start の after 依存を辿って start 自身へ戻るか（DAG を壊す循環の検知）。"""
    by_id = {t.id: t for t in tasks}
    seen: set = set()
    stack = list(task_deps(start))
    while stack:
        d = stack.pop()
        if d == start.id:
            return True
        if d in seen:
            continue
        seen.add(d)
        nxt = by_id.get(d)
        if nxt is not None:
            stack.extend(task_deps(nxt))
    return False


def _apply_revise_fields(t: Task, tasks: "list[Task]", fields: dict) -> "list[str]":
    """revise のフィールド編集を Task へ適用し、変更内容の一覧を返す。
    規約: 値が None のキーは触らない。''/'-'/'none' は削除（置換の明示規約）。
    ValueError = 人へ返す入力エラー（level 不正・after 循環/自己依存）。"""
    changes: list[str] = []
    for key in REVISE_FIELDS:
        if key not in fields or fields[key] is None:
            continue
        val = str(fields[key]).strip()
        if key == "title":
            if val and val != t.title:
                changes.append(f"title: {t.title} → {val}")
                t.title = val
        elif key == "priority":
            try:
                pv = int(val)
            except ValueError:
                raise ValueError(f"priority は整数で指定してください: {val!r}")
            if pv != t.priority:
                changes.append(f"priority: {t.priority} → {pv}")
                t.priority = pv
        elif key == "verify":
            v = _strip_code(val)
            if v.lower() in _CLEAR_VALUES:
                v = ""
            if v != t.verify:
                changes.append(f"verify: {v or '（削除）'}")
                t.verify = v
        else:                                   # extra フィールド（after/accept/note/level/track）
            if val.lower() in _CLEAR_VALUES:
                if t.get(key) is not None:
                    t.drop(key)
                    changes.append(f"{key}: （削除）")
                continue
            if key == "level" and val not in LEVELS:
                raise ValueError(f"level は {'/'.join(LEVELS)} のいずれかです: {val!r}")
            if val != t.get(key, ""):
                t.set(key, val)
                changes.append(f"{key}: {val}")
            if key == "after":
                deps = task_deps(t)
                if t.id in deps:
                    raise ValueError(f"after に自分自身は指定できません: {t.id}")
                if _after_introduces_cycle(tasks, t):
                    raise ValueError(f"after が循環します（DAG を壊すため拒否）: {val}")
    return changes


def _completed_last_run(cfg: "Config", t: "Task") -> str:
    """ローカル bus 上で完了が確定している直前 run の ID を返す。

    verify だけの修正では成果生成をやり直す必要がないため、この機械状態を根拠に
    agent-flow の再計画・再実行を省略できる。meta が無い、壊れている、または終端が
    done でない場合は安全側に倒して通常の新規 run にする。
    """
    run_id = str(t.get("last_run") or "").strip()
    if not run_id:
        return ""
    try:
        meta = json.loads(
            (cfg.bus / "runs" / run_id / "meta.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return ""
    return run_id if str(meta.get("status") or "") == "done" else ""


def recover_revised(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """実行側が settle できなかった `revised` マーカーの回収（クラッシュ後の自己回復）。
    doing かつ実行者不在（stale claim）は修正内容のまま ready に積み直す。
    実行中（新鮮なクレーム）は settle 側の積み直しに任せて触らない。
    それ以外に残ったマーカーは、内容が既にファイルへ反映済みのため落とすだけでよい。"""
    out: list[str] = []
    for t in tasks:
        if not t.get("revised"):
            continue
        st = t.norm_status()
        if st == "doing" and _claim_fresh(cfg, t.id):
            continue
        t.drop("revised")
        if st == "doing":
            release_claim(cfg, t)
            t.status = "ready"
            append_journal(cfg.journal, f"revise 回収: {t.id} を ready に積み直し（実行者不在）")
        persist_task(cfg, t)
        out.append(t.id)
    return out


def _print_impact_note(tasks: "list[Task]", tid: str) -> None:
    """revise/reject 時に、影響を受ける依存先（after 逆辺・推移）を人へ提示する。"""
    downs = dependents_of(tasks, tid)
    if downs:
        print(f"影響範囲（{tid} に依存するタスク・推移）: "
              + ", ".join(f"{t.id}[{t.norm_status()}]" for t in downs))


def cmd_revise(cfg: Config, tid: str, fields: dict, feedback: str, reason: str) -> int:
    """バックログのタスクを人が即時修正する（内容・依存・優先度＋feedback 注入。決定記録）。

      ready/inbox/draft  : 即時にファイルへ反映（次の選択・実行から効く）
      blocked/review     : 反映して ready に積み直す（needs 記入＋[x] と同じ復帰。needs は消す）
      doing（実行中）    : 反映して `revised` マーカーを付ける。実行側は現在の試行の結果を
                           確定せず（verify も done もしない）修正内容で積み直す
    done の確定には一切触れない（「done は verify のみが根拠」の不変条件を保つ）。"""
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    try:
        changes = _apply_revise_fields(t, tasks, fields or {})
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    fb = str(feedback or "").strip()
    if fb:
        t.drop("feedback")
        t.extra.append(("feedback", fb.replace("\n", " ⏎ ")))
        changes.append("feedback 注入")
        # 差し戻し（revise）の意図を run ブリーフへ蓄積（追記のみ）。次 run 以降の全分散ノードへ伝播する。
        append_brief_item(cfg, t, fb, source="revise")
    if not changes:
        print("エラー: 変更がありません（フィールドか --feedback を指定してください）", file=sys.stderr)
        return 2

    status = t.norm_status()
    doing = status == "doing" and _claim_fresh(cfg, tid)
    # 前回の再利用予約が残っている状態で別の revise が来た場合は、古い予約を必ず破棄する。
    t.drop("reuse_done_run")
    verify_only = (not fb and len(changes) == 1 and changes[0].startswith("verify:"))
    reuse_done_run = (_completed_last_run(cfg, t)
                      if (verify_only and status in ("blocked", "review")
                          and not t.get("flow_run")) else "")
    # approve / feedback / _block と同じ: flow_run があれば status によらず切り離す。
    # dashboard cancel→revise は sync 待ちの doing（flow_run ピン）でも来る。
    detached = False
    if status == "offloaded" or t.get("flow_run"):
        detach_flow_run(cfg, t, reason or fb[:120] or "revise により委譲から切り離し")
        detached = True
    disp = ""
    if reuse_done_run and not detached:
        # 成果生成は完了済みなので世代を上げない。次の loop は既存成果に対して
        # 新しい外側 verify だけを実行し、agent-flow（タスクグラフ）を呼ばない。
        release_claim(cfg, t)
        clear_needs_file(cfg, tid)
        if status == "review":
            autonomy_record(cfg, t, clean=False)
        t.status = "ready"
        t.set("reuse_done_run", reuse_done_run)
        disp = f"完了済み run {reuse_done_run} を流用し、verify だけ再実行します"
    else:
        # rev は act 試行の世代番号（req_id に載る）。成果内容が変わる revise は
        # 実行中の古い run に合流させず、新しい run を強制する。
        t.set("rev", int(str(t.get("rev", "0") or "0")) + 1)
    if doing:
        t.set("revised", _now_ts())     # 実行側が settle 時に検知して積み直す（結果は確定しない）
        disp = "実行中のため現在の試行は確定せず、修正内容で積み直されます"
    elif not reuse_done_run and (detached or status in ("blocked", "review", "doing")):
        # detached（offloaded / flow_run）か、doing でも実行者不在（stale claim）
        release_claim(cfg, t)            # 残骸クレームの掃除（無ければ no-op）
        clear_needs_file(cfg, tid)
        if status == "review":
            autonomy_record(cfg, t, clean=False)     # 検収からの修正＝差し戻し（手戻り）
        t.status = "ready"
        disp = ("委譲中の実行を中止し ready に積み直しました" if detached
                else "ready に積み直しました")
    persist_task(cfg, t)
    affects = "; ".join(changes)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を人が修正（revise）",
                         action="revise", reason=reason or fb[:200] or "revise",
                         affects=(affects[:200] + (f"; {tid} → ready" if disp and not doing else "")),
                         learn=(t.title, fb) if fb else None)
    append_journal(cfg.journal, f"revise: {tid} — {affects}"
                   + ("（実行中→積み直し予約）" if doing
                      else ("（委譲切り離し）" if detached else "")))
    print(f"{dr}: {tid} を修正しました（{affects}）。" + (disp and f"{disp}。"))
    _print_impact_note(tasks, tid)     # 依存先（after 逆辺・推移）を提示＝変更の影響範囲を人が辿れる
    return 0


# ---------------------------------------------------------------------------
# 指示のファイル取り込み（commands/<name>.json）
# ---------------------------------------------------------------------------
# CLI を実行できない環境（ビュアーが Windows・本体が WSL 内で稼働、など）から、
# approve / hold / reprioritize と同じ人の指示をファイルだけで渡すための口。
# inbox/（タスク投入）・needs/（フィードバック）と同じ push 型の入力契約で、
# watch がこの口を監視して起こす。実行は CLI と同一の関数へ委譲する
# （ロジックの二重実装はしない＝効果・決定記録 DR も CLI と同一）。

COMMAND_ACTIONS = ("approve", "hold", "pin", "defer", "revise", "reject")


def commands_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "commands"


def _reject_command(cfg: "Config", f: Path, why: str) -> None:
    """処理できない指示ファイルは .err に退避して journal に残す（無限再試行を防ぐ）。

    退避先には失敗理由も書く。以前は元の JSON をそのまま改名するだけだったので、
    「なぜ通らなかったか」は journal を grep しないと分からず、画面には成功トーストだけが
    出ていた（承認を押しても何も起きない、と繰り返し報告された不具合の一因）。
    元の指示は `command` として保持し、消費側が理由と一緒に読めるようにする。"""
    append_journal(cfg.journal, f"commands 取り込み失敗: {f.name}: {why}")
    dest = f.with_name(f.name + ".err")
    try:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
        payload = {"error": why, "failed_at": _now_ts(), "command": rec}
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        f.unlink()
    except OSError:
        try:
            f.rename(dest)
        except OSError:
            try:
                f.unlink()
            except OSError:
                pass


def _clear_rejected_commands(cfg: "Config", tid: str) -> None:
    """同じタスクへの指示が通ったら、過去の失敗退避（*.err）を掃除する。

    .err は viewer が「直前の指示は失敗した」バナーを出す根拠になる。成功後も残すと、
    解決済みの失敗が同じタスクの次の要対応カードに出続ける（直ったのに失敗表示）。
    掃除は成功時だけ——失敗の履歴を先に消すと、失敗が誰にも見えない元の不具合に戻る。"""
    if not tid:
        return
    for e in commands_dir(cfg).glob("*.err"):
        try:
            payload = json.loads(e.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        cmd = payload.get("command")
        # 新形式は {"command": {..., "id": ...}}、旧 _reject_command は元の指示 JSON を
        # そのまま改名したので {"command": "approve", "id": ...}（command が文字列, id は最上位）。
        # 両形式から task id を取り出す（旧 .err を str.get で踏むと AttributeError で落ちていた）。
        err_tid = cmd.get("id") if isinstance(cmd, dict) else payload.get("id")
        if str(err_tid or "") != tid:
            continue
        try:
            e.unlink()
        except OSError:
            pass


# 受理レシート（processed/<name>.json）— 指示を取り込んだ結果の痕跡。
#   成功時にファイルを消すだけだと、リモート閲覧者からは「保留中（エンジン未取り込み）」と
#   「処理済み」が区別できず、承認を押しても何も起きないように見える（原因不明の停滞）。
#   成功も痕跡として残し、viewer が「送信済み → 受理済み」を表示できるようにする。失敗は従来
#   どおり <name>.json.err に残す（.err は viewer の失敗バナーの根拠なので二重化しない）。
#   commands/ 配下なので状態同期でそのまま全 PC へ届く。放置すると溜まるため件数/期限で掃除する。
_RECEIPT_KEEP = 200                       # 直近この件数だけ残す
_RECEIPT_TTL_SEC = 24 * 3600              # かつ、これより古い受理レシートは消す（同期越しの閲覧猶予）


def commands_receipts_dir(cfg: "Config") -> Path:
    return commands_dir(cfg) / "processed"


def _prune_command_receipts(cfg: "Config") -> None:
    """受理レシートを件数上限と TTL で掃除する（commands/ 履歴の肥大を防ぐ）。"""
    rdir = commands_receipts_dir(cfg)
    try:
        files = sorted(rdir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    now = time.time()
    survivors: "list[Path]" = []
    for p in files:
        try:
            if now - p.stat().st_mtime > _RECEIPT_TTL_SEC:
                p.unlink()
            else:
                survivors.append(p)
        except OSError:
            pass
    for p in survivors[:max(0, len(survivors) - _RECEIPT_KEEP)]:
        try:
            p.unlink()
        except OSError:
            pass


def _write_command_receipt(cfg: "Config", f: Path, action: str, tid: str,
                           detail: str = "") -> None:
    """取り込みに成功した指示の受理レシートを processed/<name>.json にアトミックに残す。
    viewer が元ファイル名（source）で自分の『送信済み』表示を『受理済み』へ更新できる。"""
    rdir = commands_receipts_dir(cfg)
    try:
        rdir.mkdir(parents=True, exist_ok=True)
        payload = {"ok": True, "action": action, "id": tid,
                   "processed_at": _now_ts(), "source": f.name}
        if detail:
            payload["detail"] = detail[:500]
        dest = rdir / f.name
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, dest)
        _prune_command_receipts(cfg)
    except OSError:
        pass                                  # レシートは best-effort（本処理は既に成功している）


def _read_command(f: Path) -> "tuple[dict | None, str]":
    """指示ファイルを読む。(rec, why) を返す。rec が None なら why が理由（未完・不正）。

    「取り込めるか」の唯一の判定点。has_work（起床するか）と ingest_commands（処理するか）が
    同じ述語を共有するために切り出してある。両者が食い違うと、起床したのに取り込めないパスが
    生まれ、そのパスが charter を再評価して承認済みマイルストーンを書き直してしまう。"""
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"JSON 解析失敗: {e}"
    if not isinstance(rec, dict):
        return None, "オブジェクトではない"
    return rec, ""


def ingest_commands(cfg: "Config") -> "list[str]":
    """commands/*.json（{"command": "approve|hold|pin|defer|revise|replan|pause|resume|stop",
    "id": ..., "reason": ...}）を読み、CLI と同一のロジック（cmd_approve / cmd_hold /
    cmd_reprioritize / cmd_revise / cmd_replan）を実行する。
    revise は加えて title/priority/verify/accept/after/note/level/track/feedback キーを受ける。
    replan / pause / resume / stop はプロジェクト単位（id 不要）: replan は charter からの
    バックログ再分解を次パスに要求、pause/resume は watch の消化を一時停止/再開（監視は継続）、
    stop はプロセスの graceful 停止（リモート viewer が git 越しに操作する口）。
    処理できたらファイルを消す。実行した指示（"action:tid"）の一覧を返す。

    読める指示は watch 中でも即座に取り込む。debounce は「読めなかったファイル」だけの再試行
    猶予として使う（書きかけを .err へ飛ばして指示を失わないため）。読める指示まで debounce で
    先送りすると、has_work が起こしたパスで承認が取り込まれず、そのパスが charter を再評価して
    マイルストーンを書き直す＝承認したのに要対応が復活する。"""
    cdir = commands_dir(cfg)
    done: "list[str]" = []
    if not cdir.exists():
        return done
    for f in sorted(cdir.glob("*.json")):
        rec, why = _read_command(f)
        if rec is None:
            # 書きかけ（アトミックに置かれなかった指示）かもしれない。watch 中は debounce 秒だけ
            # 猶予を与えて次パスで読み直す。猶予後もダメなら .err へ退避する（再試行ループにしない）。
            if cfg.watch and cfg.debounce > 0 and (time.time() - f.stat().st_mtime) < cfg.debounce:
                continue
            _reject_command(cfg, f, why)
            continue
        action = str(rec.get("command", "")).strip()
        tid = str(rec.get("id", "")).strip()
        reason = str(rec.get("reason", "") or "").strip() or "commands/ からの指示"
        if action == "replan":
            # プロジェクト単位（id 不要）: charter からのバックログ再分解を要求する
            # （複数 charter 運用は "charter" キーで対象を絞れる）
            rc = cmd_replan(cfg, reason, str(rec.get("charter", "") or "").strip())
            if rc == 0:
                _write_command_receipt(cfg, f, "replan", "")
                try:
                    f.unlink()
                except OSError:
                    pass
                append_journal(cfg.journal, f"commands 取り込み: replan（{f.name}）")
                done.append("replan:project")
            else:
                _reject_command(cfg, f, f"replan が失敗 (exit {rc})")
            continue
        if action in ("pause", "resume", "stop"):
            # プロジェクト単位のライフサイクル指示（id 不要）。リモート viewer の停止/回復の口。
            # stop は下で raise するため、受理レシートは unlink 前にここで残す。
            _write_command_receipt(cfg, f, action, "")
            try:
                f.unlink()
            except OSError:
                pass
            append_journal(cfg.journal, f"commands 取り込み: {action}（{f.name}・理由: {reason}）")
            done.append(f"{action}:project")
            if action == "pause":
                try:
                    pause_path(cfg).write_text(json.dumps(
                        {"reason": reason, "paused_iso": _now_ts()},
                        ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
                write_status(cfg)
            elif action == "resume":
                try:
                    pause_path(cfg).unlink()
                except OSError:
                    pass
                write_status(cfg)
            else:                        # stop: graceful 停止（レジストリ後始末は cmd_run の finally）
                state_sync(cfg, force=True)   # 停止前に journal/status の変更を押し出す（best-effort）
                raise _StopRequested()
            continue
        if action == "resume-run":
            # run の「続きから」再開（id + run）。viewer の再実行ボタンの正規の口。
            rc = cmd_resume_run(cfg, tid, str(rec.get("run", "") or ""), reason) if tid else 2
            if rc == 0:
                _write_command_receipt(cfg, f, "resume-run", tid)
                try:
                    f.unlink()
                except OSError:
                    pass
                append_journal(cfg.journal, f"commands 取り込み: resume-run {tid}（{f.name}）")
                _clear_rejected_commands(cfg, tid)
                done.append(f"resume-run:{tid}")
            else:
                _reject_command(cfg, f, f"resume-run {tid} が失敗 (exit {rc})")
            continue
        if action not in COMMAND_ACTIONS or not tid:
            _reject_command(cfg, f, f"未知の指示: command={action!r} id={tid!r}")
            continue
        # cmd_* は失敗理由を stderr に書く。それを拾って退避先と journal へ運ぶ——
        # 「approve が失敗 (exit 1)」だけでは何を直せば通るのか分からず、画面には
        # 成功トーストしか出ないため、失敗が誰にも見えないまま同じ操作が繰り返される。
        errbuf = io.StringIO()
        try:
            with contextlib.redirect_stderr(errbuf):
                if action == "approve":
                    # complete: 「成果を受け入れて完了にする」の明示（agent-dashboard の
                    # 「承認して完了にする」）。無ければ従来どおり積み直し。
                    rc = cmd_approve(cfg, tid, reason, complete=bool(rec.get("complete")))
                elif action == "reject":
                    rc = cmd_reject(cfg, tid, reason)
                elif action == "hold":
                    rc = cmd_hold(cfg, tid, reason)
                elif action == "revise":
                    fields = {k: rec[k] for k in REVISE_FIELDS if k in rec}
                    rc = cmd_revise(cfg, tid, fields, str(rec.get("feedback", "") or ""), reason)
                else:
                    rc = cmd_reprioritize(cfg, tid, action, reason)
        finally:
            errmsg = errbuf.getvalue().strip()
            if errmsg:
                sys.stderr.write(errmsg + "\n")   # 端末での従来の見え方は変えない
        if rc == 0:
            _write_command_receipt(cfg, f, action, tid)
            try:
                f.unlink()
            except OSError:
                pass
            append_journal(cfg.journal, f"commands 取り込み: {action} {tid}（{f.name}）")
            _clear_rejected_commands(cfg, tid)
            done.append(f"{action}:{tid}")
        else:
            detail = f": {errmsg.splitlines()[0][:300]}" if errmsg else ""
            _reject_command(cfg, f, f"{action} {tid} が失敗 (exit {rc}){detail}")
    return done


def cmd_needs(cfg: Config) -> int:
    tasks = load_tasks(cfg.backlog)
    blocked, intake, review, proposed = human_worklist(tasks)
    print(render_digest(blocked, intake, {}, budget_stop=False, review=review, proposed=proposed))
    if blocked or review or proposed:
        print(f"（各案件の詳細・フィードバック欄: {cfg.needs}/<id>.md）")
    return 1 if (blocked or review or proposed) else 0


def _decision_action_tally(decisions_dir: Path) -> "dict[str, int]":
    """decisions/*.md の `- action  : X` を数える（ループ計測の素）。"""
    tally: dict[str, int] = {}
    if not decisions_dir.exists():
        return tally
    pat = re.compile(r"^- action\s*:\s*(?P<a>.+)$")
    for f in decisions_dir.glob("*.md"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pat.match(line.strip())
            if m:
                a = m.group("a").strip()
                tally[a] = tally.get(a, 0) + 1
    return tally


def compute_stats(cfg: Config) -> dict:
    """archive・decisions・DELIVERY・backlog から決定的にループの KPI を集計する。"""
    tasks = load_tasks(cfg.backlog)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.norm_status()] = by_status.get(t.norm_status(), 0) + 1
    arch_dir = cfg.archive_dir()
    archived = sorted(arch_dir.glob("*.md")) if arch_dir.exists() else []
    arch_tasks = [parse_task(p.read_text(encoding="utf-8"), p.stem) for p in archived]
    deliv_rows = 0
    dp = Path(cfg.delivery) if cfg.delivery else None
    if dp and dp.exists():
        for line in dp.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("|") and not s.startswith("| id") and "---" not in s:
                deliv_rows += 1
    actions = _decision_action_tally(cfg.decisions)
    auto = actions.get("auto-resolve", 0) + actions.get("auto-adjudicate", 0)
    human = (actions.get("approve-done", 0) + actions.get("approve-and-fix", 0)
             + actions.get("hold(deny)", 0) + actions.get("feedback-resume", 0))
    routed = auto + human
    done = len(archived)
    pending_human = (by_status.get("blocked", 0) + by_status.get("review", 0)
                     + by_status.get("proposed", 0))
    tok_total, usd_total = 0, 0.0                         # 納品書の `- cost: tokens=.. usd=..` を集計
    for t in arch_tasks:
        dt, du = parse_cost("@cost " + t.get("cost", ""))
        tok_total += dt
        usd_total += du
    return {
        "backlog_pending": len(tasks),
        "by_status": by_status,
        "pending_human": pending_human,                 # blocked + review（要対応）
        "done_archived": done,
        "delivery_rows": deliv_rows,
        "decisions_total": sum(actions.values()),
        "actions": actions,
        "auto_resolved": auto,                           # auto-resolve + auto-adjudicate
        "human_actions": human,
        "automation_rate": (auto / routed) if routed else None,  # 機械で捌けた割合
        "retries_pending_sum": sum(t.retries for t in tasks),
        "retries_archived_sum": sum(t.retries for t in arch_tasks),
        "first_pass_done": sum(1 for t in arch_tasks if t.retries == 0),  # 一発 done
        "tokens_archived": tok_total,                     # archive 済みタスクの累計コスト
        "cost_archived": round(usd_total, 4),
    }


# ---------------------------------------------------------------------------
