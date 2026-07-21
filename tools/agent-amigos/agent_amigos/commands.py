"""指示のファイル取り込み — `<home>/.agent/agent-amigos/commands/*.json`。

agent-project の `commands/` と同じ結合方式: 外部操作者（agent-dashboard・人・スキル）は
JSON を 1 ファイル置くだけで、常駐デーモンが次のサイクルで取り込む。プロセス間 API を
持たない（結合は常にデータ×一方向）。

コマンド形式（1 ファイル 1 コマンド）:
    {"command": "post",   "title": "...", "goal": "...", "design": "<design doc 本文>",
     "roles": [ {...役割ミッション表の 1 行...} ], "mission": {…任意の mission 上書き…}}
    {"command": "build-team", "title": "...", "goal": "...", "design": "<任意>",
     "constraints": "<任意>", "capabilities": ["python", ...], "agent_cli": "claude"}
        # ロール未指定。team-builder スキルで最適なロール表を設計してから post する
    {"command": "claim",  "mission": "<mid>", "role": "<role-id>"}      # 手動引き受け
    {"command": "assign", "mission": "<mid>", "role": "...", "node": "..."}  # owner-picks 確定
    {"command": "restaff", "mission": "<mid>", "add": [ {…役割…} ], "prune": ["<role-id>"]}
        # 実行中のチーム編成変更（G5・owner-only）: ロール追加 / 停止（剪定）
    {"command": "accept", "mission": "<mid>"}
    {"command": "reject", "mission": "<mid>", "feedback": "..."}
    {"command": "cancel", "mission": "<mid>", "reason": "..."}
    {"command": "say",    "mission": "<mid>", "to": "<role|all|owner>", "body": "..."}

処理できたファイルは削除、失敗は `<name>.rejected` へ改名して理由をログに残す
（次サイクルで再試行しない — 壊れた指示を無限に噛み続けない）。
"""
from __future__ import annotations

import json
import os

from .assign import apply_role, claim_role, confirm_assignment
from .bus import Bus
from .configfile import commands_dir, state_dir
from .messages import build_message, message_path, valid_target
from .mission import load_mission, new_mission_id, post_mission
from .util import log, now_iso, read_json, write_json_atomic


def _designs_dir(home: str) -> str:
    return os.path.join(state_dir(home), "designs")


def _do_post(bus: Bus, node_id: str, home: str, rec: dict) -> str:
    roles = rec.get("roles")
    if not isinstance(roles, list) or not roles:
        raise ValueError("post には roles（役割ミッション表の配列）が必要です")
    design = rec.get("design")
    design_file = rec.get("design_file")
    mid = str(rec.get("mission_id") or new_mission_id())
    if design_file:
        design_path = os.path.expanduser(str(design_file))
        if not os.path.isfile(design_path):
            raise ValueError(f"design_file が見つかりません: {design_file}")
    else:
        if not isinstance(design, str) or not design.strip():
            raise ValueError("post には design（design doc 本文）か design_file が必要です")
        # 取り込んだ design doc は home の状態領域へ永続化する（後から参照できる）
        design_path = os.path.join(_designs_dir(home), f"{mid}.md")
        os.makedirs(os.path.dirname(design_path), exist_ok=True)
        with open(design_path, "w", encoding="utf-8") as f:
            f.write(design)
    mission_over = dict(rec.get("mission") or {})
    for key in ("title", "goal"):
        if rec.get(key) is not None:
            mission_over[key] = rec[key]
    spec = {"mission": mission_over, "roles": roles}
    spec_path = os.path.join(_designs_dir(home), f"{mid}-roles.json")
    os.makedirs(os.path.dirname(spec_path), exist_ok=True)
    write_json_atomic(spec_path, spec)
    post_mission(bus, design_path, spec_path, node_id, mid)
    return f"post {mid}（{mission_over.get('title') or 'untitled'}）"


def _do_build_team(bus: Bus, node_id: str, agent_cli: "str | None", home: str,
                   rec: dict) -> str:
    """チームビルディング: ロール未指定のミッションから team-builder スキルで
    最適なロールミッション表を設計し、そのまま従来の post 経路へ流す。"""
    from . import teambuilding
    design = rec.get("design")
    design_file = rec.get("design_file")
    if design_file:
        design_path = os.path.expanduser(str(design_file))
        if not os.path.isfile(design_path):
            raise ValueError(f"design_file が見つかりません: {design_file}")
        with open(design_path, encoding="utf-8") as f:
            design = f.read()
    brief = {
        "title": rec.get("title"),
        "goal": rec.get("goal"),
        "design": design,
        "constraints": rec.get("constraints"),
        "capabilities": rec.get("capabilities"),
        "agent_cli": rec.get("agent_cli") or agent_cli,
    }
    cli = str(rec.get("agent_cli") or agent_cli or "")
    mission_over, roles, meta = teambuilding.build_team(
        brief, cli, model=rec.get("model"), pattern=rec.get("pattern"))

    # target=agent-flow: amigos へは公示せず、委譲封筒を状態領域へ書く（G4）。
    # amigos デーモンは flow を実行しない — dashboard の委譲アダプタ / agent-flow が拾う。
    if meta.get("target") == "agent-flow":
        deleg = meta["delegation"]
        path = os.path.join(_designs_dir(home), f"{deleg['id']}-delegation.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, deleg)
        return (f"build-team → agent-flow 委譲（探索木・動的分解）: {deleg['id']} を "
                f"{path} に出力（amigos へは公示しません。agent-flow submit / 委譲アダプタで実行）")
    # 明示 mission 上書き（rec.mission）は設計値より優先する
    merged_mission = {**mission_over, **dict(rec.get("mission") or {})}
    post_rec = {"command": "post", "roles": roles, "mission": merged_mission,
                "mission_id": rec.get("mission_id")}
    for key in ("title", "goal"):
        if rec.get(key) is not None:
            post_rec[key] = rec[key]
    if design_file:
        post_rec["design_file"] = design_file
    elif design:
        post_rec["design"] = design
    else:
        post_rec["design"] = teambuilding.brief_to_design_doc(brief)
    result = _do_post(bus, node_id, home, post_rec)
    return (f"build-team → {result}（roles={len(roles)}, "
            f"pattern={meta.get('chosen_pattern') or '-'}, skill={meta.get('skill_source')}）")


def _do_claim(bus: Bus, node_id: str, agent_cli: "str | None", rec: dict) -> str:
    mid = str(rec.get("mission") or "")
    role = str(rec.get("role") or "")
    mp = bus.mission(mid)
    mission = load_mission(mp)
    policy = str(mission.get("assignment_policy") or "first-come")
    if policy == "owner-picks":
        apply_role(bus, mp, role, node_id, agent_cli)
        if mission.get("owner_node") == node_id:
            # オーナー自身の手動引き受けは応募 + 即時確定（自分で選んだ = picked）
            confirm_assignment(bus, mp, role, node_id)
            return f"claim {mid}/{role}: 応募し確定しました（owner-picks・オーナー自身）"
        return f"claim {mid}/{role}: 応募しました（確定はオーナーの assign 待ち）"
    if claim_role(bus, mp, role, node_id, agent_cli):
        return f"claim {mid}/{role}: 引き受けました"
    return f"claim {mid}/{role}: 引き受けられませんでした（先着者あり）"


def _require_owner(mission: dict, node_id: str) -> None:
    if mission.get("owner_node") != node_id:
        raise ValueError(f"オーナー（{mission.get('owner_node')}）のみ実行できます")


def _dispatch(bus: Bus, node_id: str, agent_cli: "str | None", home: str, rec: dict) -> str:
    cmd = str(rec.get("command") or "")
    if cmd == "post":
        return _do_post(bus, node_id, home, rec)
    if cmd == "build-team":
        return _do_build_team(bus, node_id, agent_cli, home, rec)
    if cmd == "claim":
        return _do_claim(bus, node_id, agent_cli, rec)
    mid = str(rec.get("mission") or "")
    mp = bus.mission(mid)
    mission = load_mission(mp)
    if cmd == "assign":
        _require_owner(mission, node_id)
        confirm_assignment(bus, mp, str(rec.get("role") or ""), str(rec.get("node") or ""))
        return f"assign {mid}/{rec.get('role')} → {rec.get('node')}"
    if cmd == "restaff":
        from .ownerops import restaff_mission
        _require_owner(mission, node_id)
        add = rec.get("add") if isinstance(rec.get("add"), list) else None
        prune = rec.get("prune") if isinstance(rec.get("prune"), list) else None
        if not add and not prune:
            raise ValueError("restaff には add（役割配列）か prune（id 配列）が必要です")
        result = restaff_mission(bus, mp, add=add, prune=prune, by=node_id)
        return f"restaff {mid}（追加 {result['added']} / 停止 {result['pruned']}）"
    if cmd == "accept":
        from .ownerops import accept_mission
        _require_owner(mission, node_id)
        if not read_json(mp.manifest()):
            raise ValueError("deliverable がまだありません（受入対象なし）")
        accept_mission(bus, mp, by=node_id, home=home, mission=mission)
        return f"accept {mid}（納品先: deliveries/{mid}/）"
    if cmd == "reject":
        from .ownerops import reject_mission
        _require_owner(mission, node_id)
        rnd = reject_mission(bus, mp, str(rec.get("feedback") or "差し戻し"), by=node_id)
        return f"reject {mid}（round={rnd} で再作業）"
    if cmd == "cancel":
        _require_owner(mission, node_id)
        write_json_atomic(mp.cancelled(), {"ts": now_iso(), "by": node_id,
                                           "reason": str(rec.get("reason") or "")})
        bus.sync_push(f"cancel {mid}")
        return f"cancel {mid}"
    if cmd == "say":
        to = str(rec.get("to") or "all")
        from .mission import load_roles
        if not valid_target(to, load_roles(mp)):
            raise ValueError(f"宛先が不正です: {to!r}")
        frm = "owner" if mission.get("owner_node") == node_id else f"human:{node_id}"
        _mid, msg = build_message(frm, to, str(rec.get("type") or "info"),
                                 str(rec.get("subject") or ""), str(rec.get("body") or ""))
        write_json_atomic(message_path(mp, msg), msg)
        bus.sync_push(f"say {mid}")
        return f"say {mid} → {to}"
    raise ValueError(f"未知のコマンドです: {cmd!r}")


def ingest_commands(bus: Bus, node_id: str, home: str,
                    agent_cli: "str | None" = None) -> "list[str]":
    """commands/*.json を取り込む。処理できたファイルは削除、失敗は .rejected へ改名。
    戻り値は処理ログ（1 件 1 行）。"""
    cdir = commands_dir(home)
    try:
        names = sorted(n for n in os.listdir(cdir)
                       if n.endswith(".json") and ".tmp." not in n)
    except FileNotFoundError:
        return []
    done = []
    for name in names:
        path = os.path.join(cdir, name)
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("JSON オブジェクトが必要です")
            result = _dispatch(bus, node_id, agent_cli, home, rec)
            os.remove(path)
            done.append(f"{name}: {result}")
            log(node_id, f"commands 取り込み: {name}: {result}")
        except (ValueError, RuntimeError, OSError, SystemExit, KeyError) as e:
            try:
                os.replace(path, path + ".rejected")
            except OSError:
                pass
            done.append(f"{name}: 失敗: {e}")
            log(node_id, f"commands 取り込み失敗（{name} → .rejected）: {e}")
    return done
