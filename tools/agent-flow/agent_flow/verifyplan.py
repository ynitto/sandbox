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


def _vp_verifier_prompt(plan: dict, cwd: str, rev: str, request: str) -> str:
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
        f"## 検証する場所\n- 作業ディレクトリ: {cwd}\n- 成果 revision: {rev or '(不明)'}\n\n"
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


def _vp_judge_criteria(args, plan: dict, cwd: "str | None", rev: str) -> "list[dict]":
    """criterion を verifier セッション 1 回で判定する（フェイルクローズ正規化込み）。

    呼び出し失敗（CLI 不在・上限・タイムアウト）は環境要因なので全基準 inconclusive
    ——「検証できなかった」を fail（＝修正リトライを焼く）と混同しない。"""
    if not plan.get("criteria"):
        return []
    if not cwd or not os.path.isdir(cwd):
        return _vp_inconclusive_criteria(plan, "検証する場所（workspace clone）が無い")
    meta_request = str(getattr(args, "request", "") or "")
    prompt = _vp_verifier_prompt(plan, cwd, rev, meta_request)
    try:
        text = run_agent(prompt, getattr(args, "model", None) or None, purpose="verify")
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


def run_verification_plan(bus: "Bus", args, who: str) -> "dict | None":
    """meta.verification_plan を成果 revision 上で実行し receipt を書いて返す。

    plan が無ければ None（従来どおり）。壊れた plan は実行しない（receipt を書かない
    フェイルクローズ——agent-project は receipt 欠落を done にしない）。同じ plan digest ×
    同じ成果 revision の receipt が既にあれば再実行しない（command 実行は一回だけ）。

    実行場所: workspace 宣言のある run は該当 repo の clone。**workspace の無い run
    （ローカル実行・成果は投入ノードの作業ツリーに直接出る）はプロセスの cwd**——旧
    agent-project verify が workdir で走らせていた対象を受け継ぐ（P1-A8 で旧経路を撤去し、
    receipt がこの層でも唯一の検証根拠になるため）。workspace 宣言があるのに clone を
    用意できなかった run は従来どおり実行しない（成果の無い場所で誤判定しない）。
    `$KIRO_BASE_REV`（差分基準）は clone なら成果 HEAD（旧 _task_verify_cwd の一時 clone と
    同じ規則）、cwd なら投入時に固定した meta.base_rev（act 前 HEAD）を渡す。"""
    meta = bus.run_meta(args.run_id) or {}
    plan = meta.get("verification_plan")
    if not isinstance(plan, dict):
        return None
    errs = _verifycontract.plan_errors(plan)
    if errs:
        log(who, f"verification_plan が不正のため検証を実行しません（fail-close）: {errs[:3]}")
        bus.event(who, "verify-plan-invalid", errors=errs[:5])
        return None
    ws = bus.run_workspace()
    in_clone = ws is not None
    if in_clone:
        spec = ensure_workspace_clone(ws, args.run_id)
        vcwd = str((spec or {}).get("clone") or "")
        _vp_refresh_result(vcwd, str((spec or {}).get("branch") or ""))
    else:
        vcwd = os.getcwd()
    rev = _vp_result_rev(vcwd)
    prev = bus.run_receipt(args.run_id)
    if prev and prev.get("plan_digest") == plan.get("digest") \
            and prev.get("result_rev") == rev and rev:
        return prev                                # 同じ成果への再検証はしない（冪等）
    started = now_iso()
    timeout = _vp_timeout(plan)
    base_rev = rev if in_clone else str(meta.get("base_rev") or rev or "")
    env = {"KIRO_BASE_REV": base_rev} if base_rev else None
    try:
        confirm = int((plan.get("policy") or {}).get("confirm") or 1)
    except (TypeError, ValueError):
        confirm = 1
    commands = [_vp_run_command(c["command"], vcwd, timeout, env, confirm)
                for c in plan.get("commands") or []]
    criteria = _vp_judge_criteria(args, plan, vcwd, rev)
    integration = None
    integration_plan = plan.get("integration")
    if isinstance(integration_plan, dict):
        target = str(integration_plan.get("target") or "")
        fetched = _ws_git(vcwd, "fetch", "--quiet", "origin", target)
        if fetched.returncode != 0:
            integration = {"target": target, "target_rev": "", "verdict": "inconclusive",
                           "conflict_files": []}
        else:
            target_rev = _ws_git(vcwd, "rev-parse", "FETCH_HEAD").stdout.strip()
            integrated = bool(target_rev) and _ws_git(
                vcwd, "merge-base", "--is-ancestor", target_rev, rev).returncode == 0
            integration = {"target": target, "target_rev": target_rev,
                           "verdict": "pass" if integrated else "fail",
                           "conflict_files": []}
    if in_clone:
        # clone の成果は push 済みコミットなので verifier の残骸を破棄してよい。cwd（投入
        # ノードの作業ツリー）は未コミットの成果そのものを含みうるため破棄しない。
        _vp_discard_local_changes(vcwd)
    receipt = _verifycontract.build_receipt(
        plan, result_rev=rev, commands=commands, criteria=criteria,
        started_at=started, finished_at=now_iso(),
        verified_by=str(getattr(args, "node_id", "") or ""), integration=integration)
    bus.write_receipt(args.run_id, receipt)
    bus.event(who, "verify", verdict=receipt["verdict"], rev=rev[:12],
              digest=str(plan.get("digest") or "")[:19])
    bus.sync_push(f"verify run {args.run_id}: {receipt['verdict']}")
    log(who, f"統一 verify: {receipt['verdict']}（rev={rev[:12] or '不明'}・"
             f"commands={len(commands)}・criteria={len(criteria)}）")
    return receipt


def verify_fix_task(receipt: dict, iteration: int) -> dict:
    """criterion fail を同じ run の修正ループへ戻すための work ノードを作る（決定的・LLM 不要）。"""
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
