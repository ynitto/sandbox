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
from .mission import current_round, load_mission
from .util import append_jsonl, extract_json, log, now_iso, read_json, write_json_atomic


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
