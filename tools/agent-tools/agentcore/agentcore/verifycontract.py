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

from agentcore import procgroup

PLAN_VERSION = 1
RECEIPT_VERSION = 1
INTEGRATION_VERSION = 2
# version 3 = workset（複数の書込先）を検証できる契約。`workspaces[]`（順序＝先頭が primary）・
# `commands[].cwd`（要素名。省略は primary）・`integration.targets{name: target}` を持つ。
# 1 要素の run は従来どおり version 1/2 のまま作る（同じ条件の検証を別 plan にしないため）。
WORKSET_VERSION = 3
PLAN_VERSIONS = (PLAN_VERSION, INTEGRATION_VERSION, WORKSET_VERSION)
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
    （task 固有 command と regression が同一のとき二重実行しないための正規化。順序は初出順）。

    workset（version 3）では `cwd`（実行する workset 要素の名前）まで含めて同一性を見る
    ——同じコマンド文字列でも別の repo で走らせるなら別の検証だからで、畳むと片方が
    黙って実行されなくなる。`cwd` 省略は primary 相対（既存の 1 要素契約と同じ）。"""
    out: "list[dict]" = []
    seen: "set[tuple]" = set()
    for c in commands or []:
        cwd = ""
        if isinstance(c, dict):
            cmd = str(c.get("command") or "").strip()
            source = str(c.get("source") or "user")
            cwd = str(c.get("cwd") or "").strip()
        else:
            cmd, source = str(c or "").strip(), "user"
        key = (cmd, cwd)
        if not cmd or key in seen:
            continue
        seen.add(key)
        if source not in COMMAND_SOURCES:
            source = "user"
        entry = {"command": cmd, "source": source}
        if cwd:
            entry["cwd"] = cwd
        out.append(entry)
    return out


def normalize_workspaces(workspaces, workspace: str = "") -> "list[str]":
    """workset 要素名の列を正規化する（順序＝先頭が primary。重複は初出だけ残す）。

    `workspace`（従来の 1 文字列）は列が空のときの補完に使う。名前は repos レジストリの
    エントリ名で、plan の `commands[].cwd` / `integration.targets` はこの語彙で要素を指す。"""
    out: "list[str]" = []
    for w in list(workspaces or []) + ([workspace] if workspace else []):
        name = str(w or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def plan_workspaces(plan) -> "list[str]":
    """plan が検証する workset 要素名（読み取りの 1 実装）。version 1/2 は `workspace` 1 件。"""
    if not isinstance(plan, dict):
        return []
    return normalize_workspaces(plan.get("workspaces"), str(plan.get("workspace") or ""))


def plan_command_cwd(command, plan) -> str:
    """command を実行する workset 要素名。省略・未知は primary（＝先頭要素、無ければ空）。"""
    names = plan_workspaces(plan)
    cwd = str((command or {}).get("cwd") or "").strip() if isinstance(command, dict) else ""
    if cwd and cwd in names:
        return cwd
    return names[0] if names else ""


def plan_targets(plan) -> "dict":
    """要素名 → 統合対象 target の写像（読み取りの 1 実装）。version 1 は空。

    version 2 は `integration.target` 1 件＝primary の target と読み替える（v2 の意味は
    「この plan の唯一の書込先の target」なので、workset の primary と同じもの）。"""
    if not isinstance(plan, dict):
        return {}
    spec = plan.get("integration")
    if not isinstance(spec, dict):
        return {}
    targets = spec.get("targets")
    if isinstance(targets, dict):
        return {str(k): str(v) for k, v in targets.items() if str(k) and str(v)}
    target = str(spec.get("target") or "").strip()
    if not target:
        return {}
    names = plan_workspaces(plan)
    return {names[0] if names else "": target} if (names or target) else {}


def plan_digest(plan: dict) -> str:
    """digest フィールドを除いた canonical JSON の SHA-256。"""
    body = {k: v for k, v in dict(plan).items() if k != "digest"}
    return _DIGEST_PREFIX + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def normalize_plan_agent(raw) -> "dict | None":
    """`policy.agent`（自然文基準の判定に使うエージェントの明示指定）を正規化する。

    plan は「何を確かめるか」だけでなく「何で確かめるか」も持つ。持たせないと、検証が
    決着しないタスクに対して人が降ろせる条件がノード全体の設定しか無くなり、1 件のために
    全プロジェクトの検証を高いモデルへ寄せることになる（設計:
    docs/plans/2026-08-09-verification-settlement-design.md §4）。

    `policy` は digest の計算対象なので、条件を変えた検証は**別の plan** になり、
    以前の条件で出た receipt は検算で落ちる。違う条件で確かめたものを同じ判定として
    混ぜないための性質で、意図した挙動である。

    空・型違い・値なしは None（指定なし＝runner 側の設定に従う）。壊れた指定で検証を
    落とさない——ここで例外にすると、人が打ち間違えた 1 文字でタスクが検証不能になる。
    """
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    cli = str(raw.get("agent_cli") or "").strip().lower()
    if cli:
        out["agent_cli"] = cli
    model = str(raw.get("model") or "").strip()
    if model:
        out["model"] = model
    if raw.get("timeout_sec") is not None:
        try:
            timeout = float(raw["timeout_sec"])
        except (TypeError, ValueError):
            timeout = 0.0
        if timeout > 0:
            out["timeout_sec"] = timeout
    return out or None


def plan_agent(plan) -> "dict | None":
    """plan が指定する検証エージェント（無ければ None）。読み取りの 1 実装。"""
    if not isinstance(plan, dict):
        return None
    return normalize_plan_agent((plan.get("policy") or {}).get("agent"))


def build_plan(task_id: str, *, criteria=None, commands=None, workspace: str = "",
               workspaces=None, policy: "dict | None" = None,
               integration: "dict | None" = None) -> dict:
    """verification_plan を正規化して digest 付きで返す。基準もコマンドも無ければ ValueError
    （plan は検証材料があるときだけ作る——空の plan は「verify 未定義」と区別が付かない）。

    `workspaces`（workset 要素名の順序付き列。先頭が primary）が 2 件以上、または
    `commands[].cwd` / `integration.targets` を使うときだけ version 3 になる。1 要素は
    従来どおり version 1/2 で作る——同じ条件の検証を版だけの理由で別 plan にしないため。"""
    crit = normalize_criteria(criteria)
    cmds = normalize_commands(commands)
    if not crit and not cmds:
        raise ValueError("verification_plan には基準か固定コマンドが最低 1 つ要る")
    names = normalize_workspaces(workspaces, str(workspace or ""))
    targets: "dict[str, str]" = {}
    if integration:
        raw_targets = integration.get("targets")
        if isinstance(raw_targets, dict):
            targets = {str(k).strip(): str(v).strip() for k, v in raw_targets.items()
                       if str(k).strip() and str(v).strip()}
            if not targets:
                raise ValueError("integration targets が空です")
        else:
            target = str(integration.get("target") or "").strip()
            if not target:
                raise ValueError("integration target が無い")
            targets = {(names[0] if names else ""): target}
    unknown = [n for n in targets if names and n not in names]
    if unknown:
        raise ValueError(f"integration targets に workset 外の要素がある: {sorted(unknown)}")
    bad_cwd = sorted({str(c["cwd"]) for c in cmds if c.get("cwd") and c["cwd"] not in names})
    if bad_cwd:
        raise ValueError(f"commands[].cwd に workset 外の要素がある: {bad_cwd}")
    workset = (len(names) > 1 or any(c.get("cwd") for c in cmds) or len(targets) > 1)
    version = (WORKSET_VERSION if workset else
               INTEGRATION_VERSION if targets else PLAN_VERSION)
    plan = {"version": version, "task_id": str(task_id or ""),
            "workspace": names[0] if names else "", "commands": cmds, "criteria": crit}
    if version == WORKSET_VERSION:
        plan["workspaces"] = names
    if targets:
        plan["integration"] = ({"targets": dict(sorted(targets.items()))}
                               if version == WORKSET_VERSION
                               else {"target": next(iter(targets.values()))})
    pol = {}
    if policy:
        if policy.get("confirm") is not None:
            confirm = int(policy["confirm"])
            if confirm < 1:
                raise ValueError("policy.confirm は 1 以上が必要です")
            pol["confirm"] = confirm
        if policy.get("timeout_sec") is not None:
            timeout_sec = float(policy["timeout_sec"])
            if timeout_sec <= 0:
                raise ValueError("policy.timeout_sec は正の数が必要です")
            pol["timeout_sec"] = timeout_sec
        agent = normalize_plan_agent(policy.get("agent"))
        if agent:
            pol["agent"] = agent
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
    version = plan.get("version")
    if version not in PLAN_VERSIONS:
        errs.append(f"未対応の plan version: {version!r}")
    integration = plan.get("integration")
    names = plan_workspaces(plan)
    if version == INTEGRATION_VERSION:
        if not isinstance(integration, dict) or not str(integration.get("target") or "").strip():
            errs.append("version 2 plan に integration target が無い")
    elif version == WORKSET_VERSION:
        # workset 契約は「どこを検証するか」を要素名の列で持つ。列が無ければ commands[].cwd も
        # integration.targets も指す先が無く、実行場所を推測することになる（fail-close）。
        if not isinstance(plan.get("workspaces"), list) or not names:
            errs.append("version 3 plan に workspaces が無い")
        if integration is not None:
            targets = (integration.get("targets") if isinstance(integration, dict) else None)
            if not isinstance(targets, dict) or not plan_targets(plan):
                errs.append("version 3 plan の integration に targets が無い")
            else:
                unknown = sorted(n for n in targets if n not in names)
                if unknown:
                    errs.append(f"integration targets に workspaces 外の要素がある: {unknown}")
    elif integration is not None:
        errs.append("version 1 plan に integration がある")
    if version != WORKSET_VERSION and isinstance(plan.get("workspaces"), list):
        errs.append("version 3 以外の plan に workspaces がある")
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
        cwd = str(c.get("cwd") or "").strip()
        if not cwd:
            continue
        if version != WORKSET_VERSION:
            errs.append(f"commands[{i}].cwd は version 3 plan でのみ使えます")
            break
        if cwd not in names:
            errs.append(f"commands[{i}].cwd が workspaces に無い要素を指しています: {cwd}")
            break
    policy = plan.get("policy")
    if policy is not None:
        if not isinstance(policy, dict):
            errs.append("policy がオブジェクトでない")
        else:
            if "confirm" in policy:
                try:
                    if int(policy["confirm"]) < 1:
                        errs.append("policy.confirm は 1 以上が必要です")
                except (TypeError, ValueError):
                    errs.append("policy.confirm が整数でない")
            if "timeout_sec" in policy:
                try:
                    if float(policy["timeout_sec"]) <= 0:
                        errs.append("policy.timeout_sec は正の数が必要です")
                except (TypeError, ValueError):
                    errs.append("policy.timeout_sec が数値でない")
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
            # 上限は group ごと（`procgroup`）。検証コマンドは test runner や
            # コンテナを孫として起こすので、直接の子だけを殺すと孫が生き残る。
            proc = procgroup.run(cmd, shell=True, cwd=cwd, timeout=timeout, env=run_env,
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


def _check_integration(target: str, cwd: "str | None", result_rev: str,
                       timeout: float = 30) -> dict:
    """1 つの clone で「成果 revision が最新 target を含むか」を検査する。"""
    result = {"target": target, "target_rev": "", "verdict": "inconclusive",
              "conflict_files": []}
    if not target or not cwd or not os.path.isdir(cwd) or not result_rev:
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


def run_plan_integration(plan: dict, cwd: "str | None", result_rev: str,
                         timeout: float = 30) -> "dict | None":
    """v2 plan の成果 revision が最新 target を含むか検査する。v1 plan は None。"""
    spec = plan.get("integration")
    if not isinstance(spec, dict):
        return None
    return _check_integration(str(spec.get("target") or ""), cwd, result_rev, timeout)


def run_plan_integrations(plan: dict, clones: "dict[str, str]",
                          revisions: "dict[str, str]", timeout: float = 30) -> "list[dict] | None":
    """v3 plan の統合検査を要素ごとに行う（plan.workspaces の順・targets のある要素だけ）。

    要素ごとに 1 レコードを返し、`name` で workset のどれを見たかを残す——1 つに畳むと
    「どちらの repo が target を含んでいないのか」が記録から消え、読み手が clone を開いて
    調べ直すことになる。整合しない要素が 1 つでもあれば receipt_overall が fail に倒す。"""
    targets = plan_targets(plan)
    if plan.get("version") != WORKSET_VERSION or not targets:
        return None
    out: "list[dict]" = []
    for name in plan_workspaces(plan):
        target = targets.get(name)
        if not target:
            continue
        out.append({"name": name,
                    **_check_integration(target, (clones or {}).get(name),
                                         str((revisions or {}).get(name) or ""), timeout)})
    return out


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
    checked: "list" = []
    if receipt.get("version") == INTEGRATION_VERSION:
        checked = [integration]
    elif receipt.get("version") == WORKSET_VERSION and receipt.get("integrations") is not None:
        # v3 は要素ごとの統合結果。1 要素でも整合していなければ run 全体を fail に倒す
        # （片方だけ最新 target を含む成果は「同時に成立する」検証になっていない）。
        # 空リストは「統合対象を持つ要素が無い」＝制約なし（plan との員数照合は
        # `receipt_errors` の仕事で、ここは「通ったか」だけを見る）。
        checked = (receipt["integrations"] if isinstance(receipt["integrations"], list) else [None])
    for record in checked:
        if not isinstance(record, dict):
            return "fail"
        integration_verdict = record.get("verdict")
        if integration_verdict == "fail":
            return "fail"
        if integration_verdict == "inconclusive":
            integration_inconclusive = True
        elif integration_verdict == "pass":
            if not str(record.get("target_rev") or ""):
                return "fail"
        else:
            return "fail"
    if not cmds and not crit:
        return "fail"
    inconclusive = integration_inconclusive
    for c in cmds:
        if not isinstance(c, dict):
            return "fail"
        # 終了コード非 0 は成果物の欠陥（fail）。inconclusive フラグより先に見る——
        # 両方立つ矛盾レコードを inconclusive に倒すと「失敗なのに再試行しない」になる。
        exit_code = c.get("exit_code")
        if exit_code is not None and exit_code != 0:
            return "fail"
        if c.get("inconclusive"):
            inconclusive = True
            continue
        if exit_code != 0 or c.get("flaky"):
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
                  verified_by: str = "", integration: "dict | None" = None,
                  integrations=None, revisions: "dict | None" = None,
                  verified_with: "dict | None" = None) -> dict:
    """runner が返す receipt を組み立てる。verdict は receipt_overall で導出する
    （返す側と採用側が同じ規則で判定する——自称と検算がずれない）。

    workset（v3）では要素ごとの成果 revision を `revisions{name: rev}` に、統合結果を
    `integrations[]` に残す。`result_rev` は primary の revision で、1 要素の run では
    従来と同じ値になる（採用側の照合キーを版で分岐させないため）。"""
    version = int(plan.get("version") or RECEIPT_VERSION)
    receipt = {
        "version": version,
        "task_id": str(plan.get("task_id") or ""),
        "plan_digest": str(plan.get("digest") or ""),
        "result_rev": str(result_rev or ""),
        "workspace": str(plan.get("workspace") or ""),
        "commands": list(commands or []),
        "criteria": list(criteria or []),
    }
    if version == WORKSET_VERSION:
        receipt["workspaces"] = plan_workspaces(plan)
        receipt["revisions"] = {str(k): str(v) for k, v in dict(revisions or {}).items()}
    if integrations is not None:
        receipt["integrations"] = [dict(r) for r in integrations]
    if integration is not None:
        receipt["integration"] = dict(integration)
    receipt["verdict"] = receipt_overall(receipt)
    if started_at:
        receipt["started_at"] = str(started_at)
    if finished_at:
        receipt["finished_at"] = str(finished_at)
    if verified_by:
        receipt["verified_by"] = str(verified_by)
    if verified_with:
        # 「何で・どれだけ待って確かめたか」。次の一手（同じ条件で再試行か、条件を変えるか、
        # 未検証で締めるか）を人が推測ではなく記録から選ぶための材料で、採否の判定には
        # 使わない——受理は「何を出したか」で決まる（C6）。
        receipt["verified_with"] = {k: v for k, v in dict(verified_with).items()
                                    if v not in (None, "")}
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
    if receipt.get("version") not in PLAN_VERSIONS:
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
        if want_integration and plan.get("version") == WORKSET_VERSION:
            want_targets = plan_targets(plan)
            got = receipt.get("integrations")
            if not isinstance(got, list):
                errs.append("integration 結果が無い")
            else:
                got_targets = {str(r.get("name") or ""): str(r.get("target") or "")
                               for r in got if isinstance(r, dict)}
                if got_targets != want_targets:
                    errs.append("integration targets が plan と一致しない")
                for r in got:
                    errs.extend(_integration_record_errors(r))
        elif want_integration:
            got_integration = receipt.get("integration")
            if not isinstance(got_integration, dict):
                errs.append("integration 結果が無い")
            else:
                if got_integration.get("target") != want_integration.get("target"):
                    errs.append("integration target が plan と一致しない")
                errs.extend(_integration_record_errors(got_integration))
        if plan.get("version") == WORKSET_VERSION:
            want_names = plan_workspaces(plan)
            if [str(w) for w in (receipt.get("workspaces") or [])] != want_names:
                errs.append("workspaces が plan と一致しない")
            revisions = receipt.get("revisions")
            if not isinstance(revisions, dict):
                errs.append("revisions が無い")
            elif sorted(str(k) for k in revisions) != sorted(want_names):
                errs.append("revisions の要素が workspaces と一致しない")
    return errs


def _integration_record_errors(record) -> "list[str]":
    """統合結果 1 件の形式検査（v2 の単数・v3 の要素ごとで同じ規則を使う）。"""
    if not isinstance(record, dict):
        return ["integration 結果が dict でない"]
    errs: "list[str]" = []
    verdict = record.get("verdict")
    if verdict not in CRITERION_VERDICTS:
        errs.append("integration verdict が不正")
    if verdict in ("pass", "fail") and not str(record.get("target_rev") or ""):
        errs.append("integration target_rev が無い")
    return errs
