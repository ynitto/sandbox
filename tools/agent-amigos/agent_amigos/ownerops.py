"""オーナー操作 — 受入・差し戻しと、acceptance: agent の自動判定（設計書 §8.2、P2）。

不変条件「done を作れるのはオーナーの accept のみ」は維持する: acceptance: agent は
**オーナーノード上で**オーナーの権限として動く自動判定であり、final.json を書けるのは
従来どおりオーナーノードだけ。判定が review_rounds 回差し戻しても受入に至らない場合は
人へエスカレーションして止まる（無限の差し戻しループを作らない）。
"""
from __future__ import annotations

import json
import os
import time

from . import agentcli, nodebudget
from .bus import Bus, MissionPaths
from .messages import build_message, message_path, read_inbox
from .mission import (active_roles, current_round, load_mission, load_roles,
                      load_statuses, normalize_added_roles, role_status)
from .util import append_jsonl, extract_json, log, now_iso, read_json, write_json_atomic


def restaff_mission(bus: Bus, mp: MissionPaths, add: "list | None" = None,
                    prune: "list | None" = None, by: str = "owner") -> dict:
    """実行中のチーム編成を変更する（G5・owner-only）。

    - add: 追加する役割ミッション表（正規化・席展開して roles/<id>.json を書く）。
      新ロールは通常どおり募集・充足される（1 ノードなら self-staff が拾う）。
    - prune: 停止するロール id（`pruned/<id>.json` を書く）。剪定ロールは収束計算・募集・
      ターン実行から外れる（担当 amigo は次ターンで exit）。既に完了したミッションには効かない。
    戻り値: {"added": [...], "pruned": [...]}。
    """
    bus.sync_pull()
    existing = load_roles(mp)
    added_ids, pruned_ids = [], []
    if add:
        for role in normalize_added_roles(add, set(existing)):
            write_json_atomic(mp.role_json(role["id"]), role)
            added_ids.append(role["id"])
    for rid in (prune or []):
        rid = str(rid)
        if rid not in existing:
            raise SystemExit(f"[agent-amigos] 剪定対象のロールが存在しません: {rid!r}")
        write_json_atomic(mp.pruned(rid), {"role": rid, "ts": now_iso(), "by": by})
        pruned_ids.append(rid)
    append_jsonl(mp.decisions(), {"ts": now_iso(), "kind": "restaff",
                                  "body": f"add={added_ids} prune={pruned_ids} by={by}"})
    if added_ids or pruned_ids:
        _mid, msg = build_message("owner", "all", "info",
                                 subject="チーム編成を変更しました",
                                 body=f"追加: {added_ids or 'なし'} / 停止: {pruned_ids or 'なし'}")
        write_json_atomic(message_path(mp, msg), msg)
    bus.sync_push(f"restaff {mp.mission_id}")
    return {"added": added_ids, "pruned": pruned_ids}


def _save_conductor(bus: Bus, mp: MissionPaths, state: dict) -> None:
    write_json_atomic(mp.conductor_state(), state)
    bus.sync_push("conductor")


def _latest_reject_feedback(mp: MissionPaths) -> str:
    try:
        names = sorted(n for n in os.listdir(mp.rejections_dir()) if n.endswith(".json"))
    except FileNotFoundError:
        return ""
    if not names:
        return ""
    return str((read_json(os.path.join(mp.rejections_dir(), names[-1])) or {}).get("feedback") or "")


def _safe_prune(prune: list, roles: dict, mission: dict) -> list:
    """剪定してよいロールだけに絞る（integrator・唯一の承認者・最後の必須ワーカーは守る）。"""
    done_when = (mission.get("convergence") or {}).get("done_when")
    approvers = {rid for rid, r in roles.items() if r.get("approver")}
    req_workers = [rid for rid, r in roles.items()
                   if r.get("required") and r.get("builtin") != "integrator"]
    out = []
    for pid in prune:
        pid = str(pid)
        r = roles.get(pid)
        if not r or r.get("builtin") == "integrator":
            continue
        if done_when == "reviewer-approved" and pid in approvers and len(approvers) <= 1:
            continue
        remaining = [w for w in req_workers if w != pid and w not in out]
        if pid in req_workers and not remaining:
            continue                         # 必須ワーカーを全滅させない
        out.append(pid)
    return out


def _ask_conductor(mp: MissionPaths, mission: dict, roles: dict, node_id: str, cli: str) -> dict:
    design = ""
    try:
        with open(mp.design_doc(), encoding="utf-8") as f:
            design = f.read()
    except OSError:
        pass
    statuses = load_statuses(mp)
    lines = []
    for rid, r in sorted(roles.items()):
        st = role_status(mp, statuses, rid) or {}
        lines.append(f"- {rid}（{r.get('title')}） required={r.get('required')} "
                     f"approver={r.get('approver')} seats={r.get('seat_count', 1)} "
                     f"turn={st.get('turn', '-')} done={st.get('done_round') is not None} "
                     f"note={(st.get('note') or '')[:60]}")
    prompt = f"""あなたは実行中のミッションのコンダクタ（オーナー代理）です。現在のチームと進捗を見て、
チーム編成を調整すべきか判断してください。過不足を直す最小限の変更だけを提案します（不要なら空）。

# ゴール
{mission.get('goal')}

# design doc（抜粋）
{design[:3000]}

# 現在のロールと状態
{chr(10).join(lines)}

# 直近の差し戻し（あれば）
{_latest_reject_feedback(mp) or '（なし）'}

# 判断の指針
- 専門性が不足・進捗が滞るなら role を add（AgentVerse / meta-prompting 的な招集）。
- 明らかに不要・重複・機能していない role は prune（DyLAN 的な剪定）。integrator は消さない。
- 変更が不要なら add も prune も空にする。過剰・頻繁な変更はしない。

# 出力契約（これ以外を出力しないこと）
次の JSON だけを出力してください:
{{"add": [ {{"id":"...","title":"...","mission":"...","required":true,"approver":false}} ],
 "prune": ["role-id"], "reason": "簡潔な理由"}}
"""
    t0 = time.monotonic()
    text = agentcli.run_agent(prompt, cli)
    nodebudget.record(time.monotonic() - t0, ref=f"{mp.mission_id}/conductor", node=node_id)
    data = extract_json(text)
    if not isinstance(data, dict):
        raise RuntimeError("conductor 出力を JSON オブジェクトとして解釈できません")
    return data


def conductor_turn(bus: Bus, mp: MissionPaths, mission: dict, node_id: str,
                   agent_cli: "str | None" = None) -> str:
    """自律コンダクタの 1 評価（オーナーノードで phase=working/open のとき呼ぶ）。

    1 ラウンドにつき 1 回だけ team-builder 的判断で restaff（add/prune）する。LLM を毎サイクル
    呼ばないようラウンドで律速し、総操作数を max_total_ops で頭打ちにする（暴走止め）。
    返り値: acted | idle | skipped。"""
    conf = mission.get("conductor") or {}
    if not conf.get("enabled"):
        return "skipped"
    roles = active_roles(load_roles(mp), mp)
    rnd = current_round(mp)
    state = read_json(mp.conductor_state()) or {"last_round": -1, "ops": 0}
    if int(state.get("last_round", -1)) == rnd:
        return "idle"                        # このラウンドは評価済み
    interval = max(1, int(conf.get("interval_rounds") or 1))
    if rnd % interval != 0 or int(state.get("ops") or 0) >= int(conf.get("max_total_ops", 12)):
        state["last_round"] = rnd
        _save_conductor(bus, mp, state)
        return "idle"
    cli = (conf.get("cli") or agent_cli or "stub").strip().lower()
    state["last_round"] = rnd                 # no-op でも再評価しない（LLM churn 防止）
    if cli == "stub":
        _save_conductor(bus, mp, state)
        return "idle"                         # stub は判断しない（配線検証は run_agent 差し替え）
    try:
        decision = _ask_conductor(mp, mission, roles, node_id, cli)
    except RuntimeError as e:
        log("owner", f"{mp.mission_id}: conductor 判断に失敗（スキップ）: {str(e)[:120]}")
        _save_conductor(bus, mp, state)
        return "idle"
    add = decision.get("add") if isinstance(decision.get("add"), list) else []
    prune = _safe_prune(decision.get("prune") if isinstance(decision.get("prune"), list) else [],
                        roles, mission)
    cap = max(0, int(conf.get("max_ops", 3)))
    prune = prune[:cap]                        # 合計 cap（prune 優先）
    add = add[:max(0, cap - len(prune))]
    if not add and not prune:
        _save_conductor(bus, mp, state)
        return "idle"
    try:
        result = restaff_mission(bus, mp, add=add or None, prune=prune or None,
                                 by=f"conductor:{node_id}")
    except SystemExit as e:
        log("owner", f"{mp.mission_id}: conductor restaff 失敗（スキップ）: {str(e)[:120]}")
        _save_conductor(bus, mp, state)
        return "idle"
    state["ops"] = int(state.get("ops") or 0) + len(result["added"]) + len(result["pruned"])
    _save_conductor(bus, mp, state)
    log("owner", f"{mp.mission_id}: conductor 編成変更 追加={result['added']} 停止={result['pruned']}"
                 f"（{str(decision.get('reason') or '')[:80]}）")
    return "acted" if (result["added"] or result["pruned"]) else "idle"


def accept_mission(bus: Bus, mp: MissionPaths, by: str,
                   home: "str | None" = None, mission: "dict | None" = None) -> None:
    """受入を確定し、home が与えられていれば納品棚へ搬出する（push 型納品）。

    搬出はバスへの書き込みではなくオーナーのローカル操作なので、final.json を
    push した後に行う。搬出に失敗しても受入自体は成立させる（納品棚は再搬出できるが、
    accept の取り消しはできない）。"""
    bus.sync_pull()
    write_json_atomic(mp.final(), {"accepted": True, "ts": now_iso(), "by": by,
                                   "round": current_round(mp)})
    bus.sync_push(f"accept {mp.mission_id}")
    if not home:
        return
    from .delivery import export_delivery
    try:
        export_delivery(mp, mission or load_mission(mp), home, by)
    except OSError as e:
        log("owner", f"{mp.mission_id}: 納品棚への搬出に失敗しました（受入は成立）: {e}")


def reject_mission(bus: Bus, mp: MissionPaths, feedback: str, by: str) -> int:
    """差し戻し: ラウンドを上げ、フィードバックを全体へ流して decisions に記録する。
    戻り値は新ラウンド番号。"""
    bus.sync_pull()
    rnd = current_round(mp)
    write_json_atomic(os.path.join(mp.rejections_dir(), f"{rnd:04d}.json"),
                      {"round": rnd, "feedback": feedback, "ts": now_iso(), "by": by})
    _mid, msg = build_message("owner", "all", "feedback",
                             subject=f"差し戻し round={rnd + 1}", body=feedback)
    write_json_atomic(message_path(mp, msg), msg)
    append_jsonl(mp.decisions(), {"ts": now_iso(), "kind": "reject",
                                  "body": f"round={rnd} を差し戻し（{by}）: {feedback}"})
    bus.sync_push(f"reject {mp.mission_id}")
    return rnd + 1


def _agent_rejection_count(mp: MissionPaths) -> int:
    count = 0
    try:
        names = sorted(os.listdir(mp.rejections_dir()))
    except FileNotFoundError:
        return 0
    for name in names:
        if not name.endswith(".json"):
            continue
        data = read_json(os.path.join(mp.rejections_dir(), name)) or {}
        if str(data.get("by") or "").startswith("agent:"):
            count += 1
    return count


def _escalated(mp: MissionPaths, subject: str) -> bool:
    return any(m.get("subject") == subject for m in read_inbox(mp, "owner"))


def _deliverable_digest(mp: MissionPaths, max_files: int = 20, max_chars: int = 4000) -> str:
    """deliverable の中身を判定プロンプト用に有界で読む。"""
    parts = []
    base = mp.deliverable_dir()
    count = 0
    for dirpath, _dirs, names in os.walk(base):
        for name in sorted(names):
            if name == "MANIFEST.json" or ".tmp." in name:
                continue
            if count >= max_files:
                parts.append(f"…（以降 {max_files} 件超は省略）")
                return "\n\n".join(parts)
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base)
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    text = f.read(max_chars + 1)
            except OSError:
                continue
            clipped = "（…以降省略）" if len(text) > max_chars else ""
            parts.append(f"--- {rel} ---\n{text[:max_chars]}{clipped}")
            count += 1
    return "\n\n".join(parts) or "（deliverable にファイルがありません）"


def acceptance_turn(bus: Bus, mp: MissionPaths, mission: dict, node_id: str,
                    agent_cli: "str | None" = None, home: "str | None" = None) -> str:
    """acceptance: agent の 1 判定。phase が reviewing のときだけ呼ぶ。
    返り値: accepted | rejected | escalated | skipped"""
    manifest = read_json(mp.manifest())
    final = read_json(mp.final())
    if not manifest or (final and final.get("accepted")):
        return "skipped"
    rnd = current_round(mp)
    if int(manifest.get("round", -1)) != rnd:
        return "skipped"        # 差し戻し後の再統合待ち

    review_rounds = int((mission.get("convergence") or {}).get("review_rounds") or 2)
    if _agent_rejection_count(mp) >= review_rounds:
        # 自動判定の往復上限 → 人へ（無限ループを作らない）。final は書かない。
        subject = f"受入の自動判定が上限に達しました round={rnd}"
        if not _escalated(mp, subject):
            _mid, msg = build_message("system", "owner", "decision-request",
                                     subject=subject,
                                     body=f"acceptance: agent が {review_rounds} 回差し戻しても"
                                          "受入に至りませんでした。accept / reject を人が"
                                          "判断してください。")
            write_json_atomic(message_path(mp, msg), msg)
            bus.sync_push("acceptance escalation")
            log("owner", f"{mp.mission_id}: 受入の自動判定を人へエスカレーションしました")
        return "escalated"

    cli = (agent_cli or "stub").strip().lower()
    if cli == "stub":
        # LLM なしの決定的判定（プロトコル検証用）: partial は差し戻し、完全なら受入
        accept = not manifest.get("partial")
        feedback = "stub 判定: partial 納品のため差し戻します（未達項目を完了してください）。"
    else:
        design = ""
        try:
            with open(mp.design_doc(), encoding="utf-8") as f:
                design = f.read()
        except OSError:
            pass
        prompt = f"""あなたはミッションの受入判定者（オーナー代理）です。design doc の受入基準と
納品物を突き合わせ、受入可否を判定してください。

# design doc（受入基準の正典）
{design}

# 納品 MANIFEST
{json.dumps(manifest, ensure_ascii=False)}

# 納品物の内容（有界抜粋）
{_deliverable_digest(mp)}

# 出力契約（これ以外を出力しないこと）
次の JSON だけを出力してください:
{{"accept": true/false, "feedback": "差し戻す場合の具体的な修正指示（受入時は空でよい）"}}
"""
        t0 = time.monotonic()
        text = agentcli.run_agent(prompt, cli)
        nodebudget.record(time.monotonic() - t0, ref=f"{mp.mission_id}/acceptance",
                          node=node_id)
        data = extract_json(text)
        accept = bool(data.get("accept"))
        feedback = str(data.get("feedback") or "受入基準を満たしていません。")

    if accept:
        accept_mission(bus, mp, by=f"agent:{node_id}", home=home, mission=mission)
        log("owner", f"{mp.mission_id}: 自動受入しました（round={rnd}）")
        return "accepted"
    reject_mission(bus, mp, feedback, by=f"agent:{node_id}")
    log("owner", f"{mp.mission_id}: 自動判定で差し戻しました（round={rnd + 1} で再作業）")
    return "rejected"
