from __future__ import annotations
# verifyplan.py — 統一 verify の専用 runner（P1-A2。正典設計:
# docs/plans/2026-07-30-unified-task-verify-design.md / schemas/verification-{plan,receipt}.schema.json）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
#
# agent-project が確定した verification_plan（meta.verification_plan）を、成果 revision が
# 確定したあとに一度だけ実行して receipt（runs/<run-id>/receipt.json）を返す。
# 固定コマンドは文字列を変えずに実行し、自然文 criterion は verifier セッション 1 回が
# 証跡付きで判定する。verifier は基準を緩めず、成果物も変更しない。
# `fail` は同じ run の修正ループへ（orchestrate 側）、`inconclusive` は修正リトライを
# 消費せず上位（agent-project）へ返す。digest / 判定の規則は agentcore.verifycontract の
# 1 実装（_verifycontract）を使う——生成側と割れると偽 fail になる。
# ---------------------------------------------------------------------------


def cmd_verify_plan(args) -> int:
    """`verify-plan` サブコマンド: 検証計画を組み立てて stdout へ JSON で返す（バス不要・読み取り専用）。

    digest は agentcore.verifycontract の 1 実装で計算する。投入側（dashboard の一貫性ゲート等）が
    canonical JSON を再実装すると「同じ plan なのに digest 不一致」の偽 fail を作るため、
    組み立てをここへ寄せて JSON をそのまま inbox 要求の verification_plan に運ばせる。"""
    names = [str(w).strip() for w in (args.plan_workspace or []) if str(w or "").strip()]
    try:
        plan = _verifycontract.build_plan(
            str(args.task_id), criteria=args.criteria or [], commands=args.commands or [],
            workspaces=names)
    except ValueError as e:
        print(f"[agent-flow] verify-plan: {e}", file=sys.stderr)
        return 2
    print(json.dumps(plan, ensure_ascii=False))
    return 0


def parse_verification_plan(raw) -> "dict | None":
    """CLI 引数 / inbox 要求の verification_plan（JSON 文字列または dict）を読む。壊れていれば None。"""
    if isinstance(raw, dict):
        return raw
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        data = json.loads(s)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _env_repo_key(name: str) -> str:
    """workset 要素名 → 環境変数名の後半（`AGENT_REPO_<KEY>`）。英数以外は `_` に畳む。"""
    return "".join(c if c.isalnum() else "_" for c in str(name or "")).upper() or "REPO"


def _vp_timeout(plan: dict) -> float:
    try:
        t = float((plan.get("policy") or {}).get("timeout_sec") or 0)
    except (TypeError, ValueError):
        t = 0
    return t if t > 0 else 600.0


def _vp_run_command(cmd: str, cwd: "str | None", timeout: float,
                    env: "dict | None" = None, confirm: int = 1) -> dict:
    """固定コマンドを 1 件実行する（実行セマンティクスは agentcore.verifycontract の 1 実装。
    終了コード非 0 = fail / 実行場所無し・コマンド不在 exit 127 = inconclusive /
    confirm>1 で PASS/FAIL を跨いだら flaky——統一設計 §4.3）。"""
    return _verifycontract.run_plan_command(cmd, cwd, timeout, env, confirm)


def _vp_where_block(cwd: str, rev: str, clones: "dict | None" = None,
                    revisions: "dict | None" = None) -> str:
    """「どこを・どの revision で確かめるか」の節。workset では要素ごとに並べる。"""
    lines = [f"- 作業ディレクトリ: {cwd}", f"- 成果 revision: {rev or '(不明)'}"]
    if clones and len(clones) > 1:
        lines.append("- 検証対象リポジトリ（上の作業ディレクトリは primary）:")
        for name, path in clones.items():
            got = str((revisions or {}).get(name) or "")
            lines.append(f"  - [{name}] {path}（revision {got[:12] or '不明'}）")
    return "\n".join(lines)


def _vp_verifier_prompt(plan: dict, cwd: str, rev: str, request: str,
                        clones: "dict | None" = None, revisions: "dict | None" = None) -> str:
    """criterion 判定の verifier プロンプト（組み込み）。基準の変更・緩和と成果物の修正を禁じる。"""
    crit = "\n".join(f"{c['id']}. {c['text']}" for c in plan.get("criteria") or [])
    return (
        "あなたは成果物の検証エージェントです。下の受入基準それぞれについて、実際にコマンドや"
        "ファイル・差分の確認を行い、証跡付きで判定してください。\n\n"
        "重要な原則:\n"
        "- 判定の根拠は実行・確認した結果です。印象や「妥当に見える」は根拠になりません。\n"
        "- 基準を言い換えたり緩めたりしないでください。判定するのは書かれた基準そのものです。\n"
        "- 成果物を修正しないでください（作業ツリーへの変更は破棄されます）。commit / push も禁止です。\n"
        "- 確かめられない基準は inconclusive とし、note に何が足りないかを書いてください。\n\n"
        f"## 検証する場所\n{_vp_where_block(cwd, rev, clones, revisions)}\n\n"
        f"## 元の要求（文脈）\n{str(request or '')[:1500]}\n\n"
        f"## 受入基準（この順に判定する）\n{crit}\n\n"
        "## 出力\n"
        "まず人が読む本文を書き、末尾に次の形の JSON を必ず 1 つ添えてください。\n"
        '{"criteria": [{"id": "C1", "verdict": "pass|fail|inconclusive", '
        '"evidence": [{"kind": "command|file|diff|log|screen", "command": "", "exit_code": 0, '
        '"path": "", "summary": ""}], "note": ""}]}\n'
        "- criteria は上の基準と同じ id ですべて含めてください。\n"
        "- verdict=pass には必ず evidence を 1 件以上入れてください"
        "（証跡の無い pass は機械的に fail へ落とされます）。\n"
    )


def _vp_normalize_evidence(raw) -> "list[dict]":
    out: "list[dict]" = []
    for e in (raw if isinstance(raw, list) else []):
        if not isinstance(e, dict):
            continue
        kind = str(e.get("kind") or "").strip().lower()
        if kind not in _verifycontract.EVIDENCE_KINDS:
            continue
        ev = {"kind": kind}
        for k in ("command", "path", "summary", "output_tail"):
            if str(e.get(k) or "").strip():
                ev[k] = str(e[k])[:500]
        if isinstance(e.get("exit_code"), int):
            ev["exit_code"] = e["exit_code"]
        out.append(ev)
    return out[:10]


def _vp_inconclusive_criteria(plan: dict, note: str) -> "list[dict]":
    return [{"id": c["id"], "text": c.get("text", ""), "verdict": "inconclusive",
             "note": note[:300]} for c in plan.get("criteria") or []]


def _vp_verified_with(args, plan: dict, elapsed: "float | None" = None) -> dict:
    """この検証を「何で・どれだけ待って」行ったかの記録（receipt.verified_with）。

    判定には使わない。人が次の一手を選ぶ材料で、これが無いと「クラウドでやり直せば通るのか」
    を推測で決めることになる（設計: docs/plans/2026-08-09-verification-settlement-design.md §6）。"""
    explicit = _verifycontract.plan_agent(plan)
    cli, model = _effective_agent("verify", getattr(args, "model", None) or None, explicit)
    rec = {"agent_cli": cli, "model": model or "",
           "source": "plan" if explicit else "node"}
    timeout = (explicit or {}).get("timeout_sec") or _agent_timeout("verify")
    if timeout:
        rec["timeout_sec"] = float(timeout)
    if elapsed is not None:
        rec["elapsed_sec"] = round(float(elapsed), 1)
    return rec


def _vp_judge_criteria(args, plan: dict, cwd: "str | None", rev: str,
                       clones: "dict | None" = None,
                       revisions: "dict | None" = None) -> "list[dict]":
    """criterion を verifier セッション 1 回で判定する（フェイルクローズ正規化込み）。

    使うエージェントは plan の `policy.agent`（タスク単位の明示指定）＞ ノードの設定。
    plan 側を強くしてあるのは、検証が決着しないタスク 1 件のためにノード全体の検証を
    高いモデルへ寄せずに済ませるため（設計:
    docs/plans/2026-08-09-verification-settlement-design.md §4）。

    呼び出し失敗（CLI 不在・上限・タイムアウト）は環境要因なので全基準 inconclusive
    ——「検証できなかった」を fail（＝修正リトライを焼く）と混同しない。"""
    if not plan.get("criteria"):
        return []
    if not cwd or not os.path.isdir(cwd):
        return _vp_inconclusive_criteria(plan, "検証する場所（workspace clone）が無い")
    meta_request = str(getattr(args, "request", "") or "")
    prompt = _vp_verifier_prompt(plan, cwd, rev, meta_request, clones, revisions)
    try:
        text = run_agent(prompt, getattr(args, "model", None) or None, purpose="verify",
                         agent=_verifycontract.plan_agent(plan))
    except Exception as e:  # noqa: BLE001 — CLI 不在・上限・タイムアウトは環境要因
        return _vp_inconclusive_criteria(plan, f"検証エージェントを実行できませんでした: {str(e)[:200]}")
    try:
        data = extract_json(strip_ansi(text))
    except Exception:  # noqa: BLE001
        data = None
    raw = (data or {}).get("criteria") if isinstance(data, dict) else None
    by_id = {str(item.get("id") or ""): item
             for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)}
    out: "list[dict]" = []
    for c in plan.get("criteria") or []:
        item = by_id.get(c["id"], {})
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in _verifycontract.CRITERION_VERDICTS:
            verdict = "fail"                      # 判定が読めない基準はフェイルクローズ
        rec = {"id": c["id"], "text": c.get("text", ""), "verdict": verdict,
               "evidence": _vp_normalize_evidence(item.get("evidence"))}
        note = str(item.get("note") or "").strip()
        if note:
            rec["note"] = note[:500]
        out.append(rec)
    return out


def _vp_result_rev(clone: "str | None") -> str:
    if not clone or not os.path.isdir(clone):
        return ""
    try:
        proc = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _vp_refresh_result(clone: "str | None", branch: str) -> None:
    """別 worker が push した最新成果へ verifier 専用 clone を進める。"""
    if not clone or not branch:
        return
    fetched = _ws_git(clone, "fetch", "--quiet", "origin", branch)
    if fetched.returncode != 0:
        raise RuntimeError(f"成果ブランチ {branch} を更新できません")
    if _ws_git(clone, "reset", "--hard", "FETCH_HEAD").returncode != 0:
        raise RuntimeError(f"検証 clone を成果ブランチ {branch} へ更新できません")


def _vp_discard_local_changes(clone: "str | None") -> None:
    """verifier が残した作業ツリーの変更を破棄する（verifier は成果物を変更しない契約の後始末）。"""
    if not clone or not os.path.isdir(clone):
        return
    subprocess.run(["git", "-C", clone, "checkout", "--", "."], capture_output=True, timeout=60)
    subprocess.run(["git", "-C", clone, "clean", "-fd"], capture_output=True, timeout=60)


def run_verification_plan(bus: "Bus", args, who: str, *, heartbeat=None,
                          lease_window: float = 120.0) -> "dict | None":
    """meta.verification_plan を成果 revision 上で実行し receipt を書いて返す。

    plan が無ければ None（従来どおり）。壊れた plan は実行しない（receipt を書かない
    フェイルクローズ——agent-project は receipt 欠落を done にしない）。同じ plan digest ×
    同じ成果 revision の receipt が既にあれば再実行しない（command 実行は一回だけ）。

    実行場所: workspace 宣言のある run は該当 repo の clone。**workspace の無い run
    （ローカル実行・成果は投入ノードの作業ツリーに直接出る）はプロセスの cwd**——旧
    agent-project verify が workdir で走らせていた対象を受け継ぐ（P1-A8 で旧経路を撤去し、
    receipt がこの層でも唯一の検証根拠になるため）。workspace 宣言があるのに clone を
    用意できなかった run は従来どおり実行しない（成果の無い場所で誤判定しない）。
    `$AGENT_BASE_REV`（差分基準）は clone なら成果 HEAD（旧 _task_verify_cwd の一時 clone と
    同じ規則）、cwd なら投入時に固定した meta.base_rev（act 前 HEAD）を渡す。
    `$KIRO_BASE_REV` は後方互換の別名として同じ値を渡す。"""
    meta = bus.run_meta(args.run_id) or {}
    plan = meta.get("verification_plan")
    if not isinstance(plan, dict):
        return None
    errs = _verifycontract.plan_errors(plan)
    if errs:
        log(who, f"verification_plan が不正のため検証を実行しません（fail-close）: {errs[:3]}")
        bus.event(who, "verify-plan-invalid", errors=errs[:5])
        return None

    def while_alive(fn):
        return (_with_run_heartbeat(heartbeat, lease_window, fn) if heartbeat else fn())

    workset = bus.run_workset()
    in_clone = bool(workset)
    specs: "list[dict]" = []
    clones: "dict[str, str]" = {}
    revisions: "dict[str, str]" = {}
    if in_clone:
        try:
            specs = while_alive(lambda: ensure_workset(workset, args.run_id))
        except RuntimeError as exc:
            log(who, f"統一 verify の workspace を用意できません（inconclusive）: {exc}")
            specs = []
        for spec in specs:
            while_alive(lambda s=spec: _vp_refresh_result(
                str(s.get("clone") or ""), str(s.get("branch") or "")))
        vcwd = str((specs[0] if specs else {}).get("clone") or "")
        for spec in specs:
            name = str(spec.get("name") or "") or _repo_name(str(spec.get("url") or ""))
            clones[name] = str(spec.get("clone") or "")
            revisions[name] = _vp_result_rev(clones[name])
    else:
        vcwd = os.getcwd()
    rev = _vp_result_rev(vcwd)
    prev = bus.run_receipt(args.run_id)
    if prev and prev.get("plan_digest") == plan.get("digest") \
            and prev.get("result_rev") == rev and rev \
            and (plan.get("version") != _verifycontract.WORKSET_VERSION
                 or prev.get("revisions") == revisions):
        # 同じ成果への再検証はしない（冪等）。workset では primary の revision が同じでも
        # 他の要素だけが進んでいることがあるので、要素ごとの revision まで見て判断する。
        return prev
    started = now_iso()
    if len(workset) > 1 and plan.get("version") != _verifycontract.WORKSET_VERSION:
        # 書込先が複数ある run に、検証場所を 1 つしか持たない plan（version 1/2）が来た。
        # primary だけで判定すると「もう片方の repo は見ていない pass」を作るので、
        # 判定材料が足りないこと自体を receipt に残して上位（agent-project）へ返す（§5.4）。
        note = (f"検証場所が不足しています: この run の書込先は {len(workset)} つ"
                f"（{', '.join(workset_names(workset))}）ですが、"
                f"検証計画は version {plan.get('version')}（1 か所）です")
        log(who, f"統一 verify: {note} → inconclusive")
        receipt = _verifycontract.build_receipt(
            plan, result_rev=rev,
            commands=[{"command": c.get("command", ""), "inconclusive": True, "note": note[:300]}
                      for c in plan.get("commands") or []],
            criteria=_vp_inconclusive_criteria(plan, note),
            started_at=started, finished_at=now_iso(),
            verified_by=str(getattr(args, "node_id", "") or ""),
            # version 2 plan は統合結果を要求する。確かめていないので inconclusive で置く
            # ——空にすると receipt_overall が fail に倒れ、「検証できなかった」が
            # 「成果物の欠陥」に化ける（修正リトライを 1 回焼く）。
            integration=({"target": str((plan.get("integration") or {}).get("target") or ""),
                          "target_rev": "", "verdict": "inconclusive", "conflict_files": []}
                         if plan.get("version") == _verifycontract.INTEGRATION_VERSION else None))
        bus.write_receipt(args.run_id, receipt)
        bus.event(who, "verify", verdict=receipt["verdict"], rev=rev[:12],
                  digest=str(plan.get("digest") or "")[:19])
        bus.sync_push(f"verify run {args.run_id}: {receipt['verdict']}")
        return receipt
    timeout = _vp_timeout(plan)
    base_rev = rev if in_clone else str(meta.get("base_rev") or rev or "")
    env = {"AGENT_BASE_REV": base_rev, "KIRO_BASE_REV": base_rev} if base_rev else None
    if len(clones) > 1:
        # 横断の統合テストが 2 つ目以降の repo を参照できる唯一の口。要素名を環境変数名へ
        # 写すので、検証コマンドは `$AGENT_REPO_WEB/...` のように決定的に書ける（§5.4）。
        env = dict(env or {})
        env["AGENT_WORKSET_ROOT"] = _workspace_root or (os.path.dirname(vcwd) if vcwd else "")
        for name, path in clones.items():
            env[f"AGENT_REPO_{_env_repo_key(name)}"] = path
    try:
        confirm = int((plan.get("policy") or {}).get("confirm") or 1)
    except (TypeError, ValueError):
        confirm = 1
    commands = [while_alive(lambda c=c: _vp_run_command(
        c["command"], clones.get(_verifycontract.plan_command_cwd(c, plan), vcwd),
        timeout, env, confirm)) for c in plan.get("commands") or []]
    judged_at = time.monotonic()
    criteria = while_alive(lambda: _vp_judge_criteria(args, plan, vcwd, rev, clones, revisions))
    verified_with = (_vp_verified_with(args, plan, time.monotonic() - judged_at)
                     if plan.get("criteria") else None)
    if plan.get("version") == _verifycontract.WORKSET_VERSION:
        integration = None
        integrations = _verifycontract.run_plan_integrations(plan, clones, revisions)
    else:
        integration = _verifycontract.run_plan_integration(plan, vcwd, rev)
        integrations = None
    if in_clone:
        # clone の成果は push 済みコミットなので verifier の残骸を破棄してよい。cwd（投入
        # ノードの作業ツリー）は未コミットの成果そのものを含みうるため破棄しない。
        for path in (list(clones.values()) or [vcwd]):
            _vp_discard_local_changes(path)
    receipt = _verifycontract.build_receipt(
        plan, result_rev=rev, commands=commands, criteria=criteria,
        started_at=started, finished_at=now_iso(),
        verified_by=str(getattr(args, "node_id", "") or ""), integration=integration,
        integrations=integrations, revisions=revisions,
        verified_with=verified_with)
    bus.write_receipt(args.run_id, receipt)
    bus.event(who, "verify", verdict=receipt["verdict"], rev=rev[:12],
              digest=str(plan.get("digest") or "")[:19])
    bus.sync_push(f"verify run {args.run_id}: {receipt['verdict']}")
    log(who, f"統一 verify: {receipt['verdict']}（rev={rev[:12] or '不明'}・"
             f"commands={len(commands)}・criteria={len(criteria)}）")
    return receipt


def verify_fix_task(receipt: dict, iteration: int) -> dict:
    """criterion fail を同じ run の修正ループへ戻すための work ノードを作る（決定的・LLM 不要）。"""
    # 統合の不足（成果が最新 target を含まない）は修正でなく再統合で直す。workset では
    # 整合していない要素だけを base-sync させる——全 repo を無条件に取り込み直さない。
    failed = [r for r in (receipt.get("integrations") or [])
              if isinstance(r, dict) and r.get("verdict") == "fail"]
    if failed:
        names = [str(r.get("name") or "") for r in failed if str(r.get("name") or "")]
        targets = ", ".join(sorted({str(r.get("target") or "target") for r in failed}))
        task = {"id": f"base-sync-{iteration + 1}", "kind": "base-sync",
                "goal": f"最新 {targets} を作業ブランチへ統合し、競合を解消する"
                        f"（{', '.join(names)}）", "deps": []}
        if names:
            task["workspaces"] = names
        return task
    integration = receipt.get("integration")
    if isinstance(integration, dict) and integration.get("verdict") == "fail":
        target = str(integration.get("target") or "target")
        return {"id": f"base-sync-{iteration + 1}", "kind": "base-sync",
                "goal": f"最新 {target} を作業ブランチへ統合し、競合を解消する", "deps": []}
    lines = []
    for c in receipt.get("commands") or []:
        if not c.get("inconclusive") and c.get("exit_code") != 0:
            lines.append(f"- 固定検証コマンド `{c.get('command','')}` が exit={c.get('exit_code')} "
                         f"で失敗: {str(c.get('output_tail') or '')[:300]}")
    for c in receipt.get("criteria") or []:
        if c.get("verdict") == "fail":
            note = str(c.get("note") or "")[:200]
            lines.append(f"- 基準 {c.get('id')}「{str(c.get('text') or '')[:120]}」が fail"
                         + (f": {note}" if note else ""))
    goal = ("検証ゲート（verification plan）に不合格でした。次の不合格点を解消するよう"
            "成果物を修正してください。基準そのものを変更・緩和してはいけません。\n"
            + "\n".join(lines[:12]))
    return {"id": f"verify-fix-{iteration + 1}", "kind": "work", "goal": goal, "deps": []}
