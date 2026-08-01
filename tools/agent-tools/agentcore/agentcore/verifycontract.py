"""verification plan / receipt 契約 — 統一 verify の共通語彙と digest の 1 実装。

agent-project が plan を確定して digest を付け、agent-flow の専用 runner が成果 revision 上で
一度だけ実行して receipt を返し、agent-project が検算して状態を確定する（正典設計:
docs/plans/2026-07-30-unified-task-verify-design.md、schema: schemas/verification-plan.schema.json /
schemas/verification-receipt.schema.json）。

digest の計算をここに 1 実装で置くのは、生成側（agent-project）と実行側（agent-flow）で
canonical JSON の作り方が割れると「同じ plan なのに digest 不一致」が receipt 検算の偽 fail に
なるため。criterion id の採番規則（出現順 C1, C2, …）も同じ理由でここが正典。

receipt の全体判定は自称の `verdict` を信用せず `receipt_overall` が中身から再導出する。
証跡の無い pass を fail へ落とすフェイルクローズもここに置く——採用側の 1 実装
（agent-project）だけでなく、返す側（runner）も同じ関数で自分の receipt を自己検算できる。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

PLAN_VERSION = 1
RECEIPT_VERSION = 1
INTEGRATION_VERSION = 2
CRITERION_VERDICTS = ("pass", "fail", "inconclusive")
COMMAND_SOURCES = ("user", "legacy", "template", "policy")
EVIDENCE_KINDS = ("command", "file", "diff", "log", "screen")

_DIGEST_PREFIX = "sha256:"


def canonical_json(obj) -> str:
    """決定的な JSON 直列化（キー昇順・区切り最小・非 ASCII 素通し）。digest の入力。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def criterion_id(index: int) -> str:
    """採番規則の正典: plan 内の出現順で 1 始まりの C1, C2, …。"""
    return f"C{index + 1}"


def normalize_criteria(texts) -> "list[dict]":
    """自然文基準の列 → criterion レコード列。空行は落とし、id を出現順に採番する。"""
    out = []
    for t in texts or []:
        text = str(t or "").strip()
        if text:
            out.append({"id": criterion_id(len(out)), "text": text})
    return out


def normalize_commands(commands) -> "list[dict]":
    """固定コマンド列 → command レコード列。文字列と dict の両方を受け、完全一致は 1 つへ畳む
    （task 固有 command と regression が同一のとき二重実行しないための正規化。順序は初出順）。"""
    out: "list[dict]" = []
    seen: "set[str]" = set()
    for c in commands or []:
        if isinstance(c, dict):
            cmd = str(c.get("command") or "").strip()
            source = str(c.get("source") or "user")
        else:
            cmd, source = str(c or "").strip(), "user"
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        if source not in COMMAND_SOURCES:
            source = "user"
        out.append({"command": cmd, "source": source})
    return out


def plan_digest(plan: dict) -> str:
    """digest フィールドを除いた canonical JSON の SHA-256。"""
    body = {k: v for k, v in dict(plan).items() if k != "digest"}
    return _DIGEST_PREFIX + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def build_plan(task_id: str, *, criteria=None, commands=None, workspace: str = "",
               policy: "dict | None" = None, integration: "dict | None" = None) -> dict:
    """verification_plan を正規化して digest 付きで返す。基準もコマンドも無ければ ValueError
    （plan は検証材料があるときだけ作る——空の plan は「verify 未定義」と区別が付かない）。"""
    crit = normalize_criteria(criteria)
    cmds = normalize_commands(commands)
    if not crit and not cmds:
        raise ValueError("verification_plan には基準か固定コマンドが最低 1 つ要る")
    plan = {"version": INTEGRATION_VERSION if integration else PLAN_VERSION,
            "task_id": str(task_id or ""),
            "workspace": str(workspace or ""), "commands": cmds, "criteria": crit}
    if integration:
        target = str(integration.get("target") or "").strip()
        if not target:
            raise ValueError("integration target が無い")
        plan["integration"] = {"target": target}
    pol = {}
    if policy:
        if policy.get("confirm") is not None:
            pol["confirm"] = int(policy["confirm"])
        if policy.get("timeout_sec") is not None:
            pol["timeout_sec"] = float(policy["timeout_sec"])
    if pol:
        plan["policy"] = pol
    plan["digest"] = plan_digest(plan)
    return plan


def plan_errors(plan) -> "list[str]":
    """plan の決定的検査。空リスト＝実行してよい。版・digest・採番のいずれかが崩れていれば
    理由を返す（解釈できない plan は実行しない fail-close の判定材料）。"""
    errs: "list[str]" = []
    if not isinstance(plan, dict):
        return ["plan が dict でない"]
    if plan.get("version") not in (PLAN_VERSION, INTEGRATION_VERSION):
        errs.append(f"未対応の plan version: {plan.get('version')!r}")
    integration = plan.get("integration")
    if plan.get("version") == INTEGRATION_VERSION:
        if not isinstance(integration, dict) or not str(integration.get("target") or "").strip():
            errs.append("version 2 plan に integration target が無い")
    elif integration is not None:
        errs.append("version 1 plan に integration がある")
    if not str(plan.get("task_id") or ""):
        errs.append("task_id が無い")
    cmds = plan.get("commands")
    crit = plan.get("criteria")
    if not isinstance(cmds, list) or not isinstance(crit, list):
        errs.append("commands / criteria が配列でない")
        return errs
    if not cmds and not crit:
        errs.append("基準も固定コマンドも無い")
    for i, c in enumerate(crit):
        if not isinstance(c, dict) or c.get("id") != criterion_id(i) \
                or not str(c.get("text") or "").strip():
            errs.append(f"criteria[{i}] が採番規則（出現順 C1, C2, …）または形に合わない")
            break
    for i, c in enumerate(cmds):
        if not isinstance(c, dict) or not str(c.get("command") or "").strip():
            errs.append(f"commands[{i}] に command が無い")
            break
    digest = str(plan.get("digest") or "")
    if not digest.startswith(_DIGEST_PREFIX):
        errs.append("digest が無い")
    elif plan_digest(plan) != digest:
        errs.append("digest が本文と一致しない")
    return errs


def run_plan_command(cmd: str, cwd: "str | None", timeout: float,
                     env: "dict | None" = None, confirm: int = 1) -> dict:
    """plan の固定コマンドを 1 件、文字列を変えずに POSIX sh で実行し receipt の command
    レコードを返す（実行セマンティクスの 1 実装。agent-flow runner と agent-project の
    local runner が共有する——割れると同じ plan で判定が変わる）。

    規則: 終了コード非 0 は fail（成果物の欠陥）、起動できない（実行場所が無い・コマンド不在
    exit 127）は inconclusive（環境。修正リトライを消費させない）、タイムアウトは exit 124 の
    fail。env は `$AGENT_BASE_REV`（差分基準）等の受け渡し。confirm>1 は同じコマンドを最大
    confirm 回実行し、PASS/FAIL を跨いだら flaky を立てる（flaky な pass は receipt_overall が
    fail に落とす）。"""
    entry: dict = {"command": cmd}
    if not cwd or not os.path.isdir(cwd):
        entry.update(inconclusive=True, note="検証する場所（作業ツリー）が無い")
        return entry
    run_env = {**os.environ, **env} if env else None

    def _once() -> "tuple[dict, int | None]":
        e: dict = {}
        try:
            proc = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout, env=run_env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")
        except subprocess.TimeoutExpired:
            e.update(exit_code=124, note=f"タイムアウト（{int(timeout)}s）")
            return e, 124
        except (OSError, subprocess.SubprocessError) as exc:
            e.update(inconclusive=True, note=f"起動できませんでした: {str(exc)[:200]}")
            return e, None
        tail = ((proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]).strip()
        if proc.returncode == 127:
            # シェルの 127 = command not found。成果物の欠陥ではなく環境の欠落。
            e.update(inconclusive=True, note=f"コマンド不在（exit 127）: {tail[:200]}")
            return e, None
        e.update(exit_code=int(proc.returncode), output_tail=tail[:800])
        return e, int(proc.returncode)

    first, code = _once()
    entry.update(first)
    if code is None:                              # inconclusive / 起動不能は再実行しない
        return entry
    for _ in range(max(1, int(confirm or 1)) - 1):
        _again, code2 = _once()
        if code2 is not None and (code2 == 0) != (code == 0):
            entry.update(flaky=True,
                         note=f"flaky: 実行結果が不安定（exit={code} と exit={code2} が混在）")
            break
    return entry


def run_plan_integration(plan: dict, cwd: "str | None", result_rev: str,
                         timeout: float = 30) -> "dict | None":
    """v2 plan の成果 revision が最新 target を含むか検査する。v1 plan は None。"""
    spec = plan.get("integration")
    if not isinstance(spec, dict):
        return None
    target = str(spec.get("target") or "")
    result = {"target": target, "target_rev": "", "verdict": "inconclusive",
              "conflict_files": []}
    if not cwd or not os.path.isdir(cwd) or not result_rev:
        return result

    def git(*args):
        try:
            return subprocess.run(
                ["git", "-C", cwd, *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return None

    fetched = git("fetch", "--quiet", "origin", target)
    if fetched is None or fetched.returncode != 0:
        return result
    resolved = git("rev-parse", "FETCH_HEAD")
    target_rev = resolved.stdout.strip() if resolved and resolved.returncode == 0 else ""
    if not target_rev:
        return result
    ancestor = git("merge-base", "--is-ancestor", target_rev, result_rev)
    result["target_rev"] = target_rev
    if ancestor is not None and ancestor.returncode in (0, 1):
        result["verdict"] = "pass" if ancestor.returncode == 0 else "fail"
    return result


def _evidence_ok(ev) -> bool:
    return isinstance(ev, dict) and str(ev.get("kind") or "") in EVIDENCE_KINDS


def criterion_has_evidence(c: dict) -> bool:
    """pass の採用条件: コマンド・ファイル・差分・ログ・画面のいずれかの証拠が最低 1 つ。"""
    evs = c.get("evidence")
    return isinstance(evs, list) and any(_evidence_ok(e) for e in evs)


def receipt_overall(receipt: dict) -> str:
    """receipt の中身から全体判定を再導出する（自称 verdict は使わない）。

    fail-close の規則:
      - 固定コマンドの終了コード非 0、または flaky → fail
      - 固定コマンドが起動できなかった（`inconclusive: true`。コマンド不在・環境未到達）→ inconclusive
        （成果物の欠陥と混同しない——修正リトライを消費させない）
      - criterion の fail → fail
      - criterion の pass に証跡が無い → fail（「確認しました」だけの pass を塞ぐ）
      - 未知の verdict → fail
      - fail が無く inconclusive がある → inconclusive
      - commands も criteria も空 → fail（判定材料の無い receipt を pass にしない）
    """
    cmds = receipt.get("commands") if isinstance(receipt.get("commands"), list) else []
    crit = receipt.get("criteria") if isinstance(receipt.get("criteria"), list) else []
    integration = receipt.get("integration")
    integration_inconclusive = False
    if receipt.get("version") == INTEGRATION_VERSION:
        if not isinstance(integration, dict):
            return "fail"
        integration_verdict = integration.get("verdict")
        if integration_verdict == "fail":
            if not str(integration.get("target_rev") or ""):
                return "fail"
            return "fail"
        if integration_verdict == "inconclusive":
            integration_inconclusive = True
        elif integration_verdict == "pass":
            if not str(integration.get("target_rev") or ""):
                return "fail"
        else:
            return "fail"
    if not cmds and not crit:
        return "fail"
    inconclusive = integration_inconclusive
    for c in cmds:
        if not isinstance(c, dict):
            return "fail"
        if c.get("inconclusive"):
            inconclusive = True
            continue
        if c.get("exit_code") != 0 or c.get("flaky"):
            return "fail"
    for c in crit:
        if not isinstance(c, dict):
            return "fail"
        v = str(c.get("verdict") or "").strip().lower()
        if v == "fail" or v not in CRITERION_VERDICTS:
            return "fail"
        if v == "pass" and not criterion_has_evidence(c):
            return "fail"
        if v == "inconclusive":
            inconclusive = True
    return "inconclusive" if inconclusive else "pass"


def build_receipt(plan: dict, *, result_rev: str, commands=None, criteria=None,
                  started_at: str = "", finished_at: str = "",
                  verified_by: str = "", integration: "dict | None" = None) -> dict:
    """runner が返す receipt を組み立てる。verdict は receipt_overall で導出する
    （返す側と採用側が同じ規則で判定する——自称と検算がずれない）。"""
    receipt = {
        "version": int(plan.get("version") or RECEIPT_VERSION),
        "task_id": str(plan.get("task_id") or ""),
        "plan_digest": str(plan.get("digest") or ""),
        "result_rev": str(result_rev or ""),
        "workspace": str(plan.get("workspace") or ""),
        "commands": list(commands or []),
        "criteria": list(criteria or []),
    }
    if integration is not None:
        receipt["integration"] = dict(integration)
    receipt["verdict"] = receipt_overall(receipt)
    if started_at:
        receipt["started_at"] = str(started_at)
    if finished_at:
        receipt["finished_at"] = str(finished_at)
    if verified_by:
        receipt["verified_by"] = str(verified_by)
    return receipt


def receipt_errors(receipt, *, plan: "dict | None" = None,
                   expected_digest: str = "", expected_rev: str = "") -> "list[str]":
    """receipt 採否の決定的検査（採用側の 1 実装）。空リスト＝採用してよい receipt。

    検査は W3 の決定的チェックに対応する:
      1. 版が解釈でき、plan_digest が投入時の正本と一致する
      2. result_rev が採用対象の成果 revision と一致する
      3. 固定コマンドが plan と同数・同順で全件そろっている
      4. criterion が plan と同 id で全件そろっている
    （終了コード 0・証跡付き pass の判定は receipt_overall が担う——ここは「照合できるか」、
    あちらは「通ったか」。無い・古い・壊れた receipt はここで落ちる。）
    """
    errs: "list[str]" = []
    if not isinstance(receipt, dict):
        return ["receipt が dict でない"]
    if receipt.get("version") not in (RECEIPT_VERSION, INTEGRATION_VERSION):
        errs.append(f"未対応の receipt version: {receipt.get('version')!r}")
    if plan and receipt.get("version") != plan.get("version"):
        errs.append("receipt version が plan と一致しない")
    digest = expected_digest or (str(plan.get("digest") or "") if plan else "")
    if digest and str(receipt.get("plan_digest") or "") != digest:
        errs.append("plan_digest が投入時の正本と一致しない")
    rev = str(receipt.get("result_rev") or "")
    if not rev:
        errs.append("result_rev が無い")
    elif expected_rev and rev != str(expected_rev):
        errs.append(f"result_rev が採用対象と一致しない（receipt={rev[:12]} 対象={str(expected_rev)[:12]}）")
    cmds = receipt.get("commands") if isinstance(receipt.get("commands"), list) else None
    crit = receipt.get("criteria") if isinstance(receipt.get("criteria"), list) else None
    if cmds is None or crit is None:
        errs.append("commands / criteria が配列でない")
        return errs
    if plan:
        want_cmds = [str(c.get("command") or "") for c in (plan.get("commands") or [])]
        got_cmds = [str(c.get("command") or "") if isinstance(c, dict) else "" for c in cmds]
        if got_cmds != want_cmds:
            errs.append("固定コマンドが plan と同数・同順でない")
        want_ids = [c.get("id") for c in plan.get("criteria") or []]
        got_ids = [c.get("id") if isinstance(c, dict) else None for c in crit]
        if got_ids != want_ids:
            errs.append("criterion の id 列が plan と一致しない")
        want_integration = plan.get("integration")
        if want_integration:
            got_integration = receipt.get("integration")
            if not isinstance(got_integration, dict):
                errs.append("integration 結果が無い")
            else:
                if got_integration.get("target") != want_integration.get("target"):
                    errs.append("integration target が plan と一致しない")
                verdict = got_integration.get("verdict")
                if verdict not in CRITERION_VERDICTS:
                    errs.append("integration verdict が不正")
                if verdict in ("pass", "fail") and not str(got_integration.get("target_rev") or ""):
                    errs.append("integration target_rev が無い")
    return errs
