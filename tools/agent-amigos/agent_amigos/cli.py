"""CLI — サブコマンド体系（設計書 §11）。"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

from .assign import unfilled_required
from .bus import make_bus
from .daemon import NodeDaemon, default_node_id
from .messages import build_message, message_path, unanswered_questions, valid_target
from .mission import (convergence_state, current_round, derive_phase,
                      load_mission, load_roles, post_mission)
from .util import now_iso, read_json, write_json_atomic


def _bus_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bus", required=False,
                   default=os.environ.get("AGENT_AMIGOS_BUS", ""),
                   help="バス指定: ローカル dir または git+<url>（専用バスリポジトリ）。"
                        "環境変数 AGENT_AMIGOS_BUS でも指定可")
    p.add_argument("--bus-workdir", default=None,
                   help="GitBus のクローン作業領域（既定: ~/.agent/amigos/bus/<hash>）")


def _node_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--node-id", default=None, help="ノード ID（既定: 自動採番）")


def _resolve(args) -> "tuple":
    bus = make_bus(args.bus, workdir=getattr(args, "bus_workdir", None))
    node = args.node_id or default_node_id()
    return bus, node


def _require_owner(mission: dict, node: str) -> None:
    if mission.get("owner_node") != node:
        raise SystemExit(f"[agent-amigos] このコマンドはオーナーノード"
                         f"（{mission.get('owner_node')}）のみ実行できます（自ノード: {node}）")


def _mission(bus, mid: str):
    mp = bus.mission(mid)
    mission = load_mission(mp)
    return mp, mission, load_roles(mp)


def cmd_init_bus(args) -> int:
    bus, _node = _resolve(args)
    if bus.kind == "local":
        os.makedirs(os.path.join(bus.root, "missions"), exist_ok=True)
    print(f"バスを初期化しました: {bus.root}"
          + (f"（git: {bus.url}）" if bus.kind == "git" else ""))
    return 0


def cmd_post(args) -> int:
    bus, node = _resolve(args)
    mid = post_mission(bus, args.design, args.roles, node, args.mission_id)
    print(f"ミッションを公示しました: {mid}（owner={node}）")
    if args.serve:
        NodeDaemon(bus, node, agent_cli=args.agent_cli, interval=args.interval,
                   resume_hours=args.resume_hours).run(cycles=args.cycles)
    return 0


def cmd_join(args) -> int:
    bus, node = _resolve(args)
    roles_filter = [r for r in (args.roles or "").split(",") if r]
    tags = [t for t in (args.tags or "").split(",") if t]
    NodeDaemon(bus, node, agent_cli=args.agent_cli, tags=tags,
               roles_filter=roles_filter, interval=args.interval,
               resume_hours=args.resume_hours).run(cycles=args.cycles)
    return 0


def cmd_run(args) -> int:
    from .runner import AmigoRunner
    bus, node = _resolve(args)
    runner = AmigoRunner(bus, args.mission, args.role, node, args.agent_cli)
    if args.once:
        print(runner.turn_once())
        return 0
    while True:
        result = runner.turn_once()
        print(result)
        if result in ("exit",):
            return 0
        time.sleep(args.interval)


def cmd_status(args) -> int:
    from . import nodebudget
    bus, _node = _resolve(args)
    nb = nodebudget.state()
    if nb["limit_s"] or nb["workload_limit_s"]:
        lim = f"{nb['limit_s'] / 60:.0f}m" if nb["limit_s"] else "∞"
        print(f"ノード予算（{nb['period']}）: {nb['spent_s'] / 60:.1f}m/{lim}"
              f"{'  ← 超過中（amigo は paused）' if nb['exceeded'] else ''}")
    mids = [args.mission] if args.mission else bus.list_missions()
    for mid in mids:
        mp, mission, roles = _mission(bus, mid)
        phase = derive_phase(mission, roles, mp)
        cs = convergence_state(mission, roles, mp)
        b = cs["budget"]
        budget_txt = (f"{b['spent_s'] / 60:.1f}m/{b['limit_s'] / 60:.0f}m"
                      if b["limit_s"] else f"{b['spent_s'] / 60:.1f}m/∞")
        print(f"{mid}  [{phase}]  {mission.get('title')}  "
              f"round={cs['round']} budget={budget_txt}"
              f"{' (soft)' if b['soft'] and not b['hard'] else ''}"
              f"{' (hard)' if b['hard'] else ''}")
        roster = read_json(mp.roster()) or {}
        for rid, role in sorted(roles.items()):
            ent = roster.get(rid)
            st = read_json(mp.status(f"{ent['node']}--{rid}")) if ent else None
            mark = "✔" if rid in cs["done_roles"] else (" " if ent else "?")
            who = ent["node"] if ent else "（募集中）"
            note = (st or {}).get("note") or ""
            print(f"  {mark} {rid:<14} {who:<20} "
                  f"turn={(st or {}).get('turn', '-')} {note[:40]}")
        qs = unanswered_questions(mp, roles)
        if qs:
            print(f"  未回答の質問: {len(qs)} 件 "
                  f"({', '.join(q['from'] + '→' + q['to'] for q in qs[:3])}…)"
                  if len(qs) > 3 else
                  f"  未回答の質問: {len(qs)} 件 "
                  f"({', '.join(q['from'] + '→' + q['to'] for q in qs)})")
        unfilled = unfilled_required(roles, roster)
        if unfilled:
            print(f"  未充足の必須ロール: {', '.join(unfilled)}")
    return 0


def cmd_collect(args) -> int:
    bus, _node = _resolve(args)
    mp, _mission_doc, _roles = _mission(bus, args.mission)
    manifest = read_json(mp.manifest())
    if not manifest:
        raise SystemExit("[agent-amigos] deliverable がまだありません（統合前です）")
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    shutil.copytree(mp.deliverable_dir(), out, dirs_exist_ok=True)
    partial = "（partial — 予算枯渇/静穏化による部分納品）" if manifest.get("partial") else ""
    print(f"deliverable を取り出しました → {out} {partial}")
    print(f"  round={manifest.get('round')} reason={manifest.get('reason')} "
          f"files={sum(len(v) for v in (manifest.get('files') or {}).values())}")
    return 0


def cmd_accept(args) -> int:
    bus, node = _resolve(args)
    mp, mission, roles = _mission(bus, args.mission)
    _require_owner(mission, node)
    if not read_json(mp.manifest()):
        raise SystemExit("[agent-amigos] deliverable がまだありません（受入対象がありません）")
    bus.sync_pull()
    write_json_atomic(mp.final(), {"accepted": True, "ts": now_iso(), "by": node,
                                   "round": current_round(mp)})
    bus.sync_push(f"accept {args.mission}")
    print(f"受入しました: {args.mission}（done）")
    return 0


def cmd_reject(args) -> int:
    bus, node = _resolve(args)
    mp, mission, roles = _mission(bus, args.mission)
    _require_owner(mission, node)
    bus.sync_pull()
    rnd = current_round(mp)
    write_json_atomic(os.path.join(mp.rejections_dir(), f"{rnd:04d}.json"),
                      {"round": rnd, "feedback": args.feedback, "ts": now_iso(), "by": node})
    _mid, msg = build_message("owner", "all", "feedback",
                             subject=f"差し戻し round={rnd + 1}", body=args.feedback)
    write_json_atomic(message_path(mp, msg), msg)
    from .util import append_jsonl
    append_jsonl(mp.decisions(), {"ts": now_iso(), "kind": "reject",
                                  "body": f"round={rnd} を差し戻し: {args.feedback}"})
    bus.sync_push(f"reject {args.mission}")
    print(f"差し戻しました: {args.mission}（round={rnd + 1} で再作業）")
    return 0


def cmd_budget(args) -> int:
    if args.action == "node":
        return _cmd_budget_node(args)
    if not args.mission:
        raise SystemExit("[agent-amigos] budget add にはミッション ID が必要です")
    if args.minutes is None:
        raise SystemExit("[agent-amigos] budget add には --minutes が必要です")
    bus, node = _resolve(args)
    mp, mission, _roles = _mission(bus, args.mission)
    _require_owner(mission, node)
    bus.sync_pull()
    budget = dict(mission.get("budget") or {})
    budget["execution_minutes"] = float(budget.get("execution_minutes") or 0) + args.minutes
    mission["budget"] = budget
    write_json_atomic(mp.mission_json(), mission)
    from .util import append_jsonl
    append_jsonl(mp.decisions(), {"ts": now_iso(), "kind": "budget",
                                  "body": f"予算を {args.minutes} 分追加 → "
                                          f"{budget['execution_minutes']} 分"})
    bus.sync_push(f"budget add {args.mission}")
    print(f"予算を追加しました: {budget['execution_minutes']} 分")
    return 0


def _cmd_budget_node(args) -> int:
    """ノード予算（請負側の上限、§3.3）の表示・設定。台帳・設定は
    $AGENT_BUDGET_DIR（既定 ~/.agent/budget/）のツール横断契約
    （schemas/node-budget.schema.json）。agent-dashboard も同じファイルを管理する。"""
    from . import nodebudget
    changed = False
    if args.limit_minutes is not None:
        nodebudget.save_config(execution_minutes=args.limit_minutes)
        changed = True
    if args.period:
        nodebudget.save_config(period=args.period)
        changed = True
    if args.amigos_minutes is not None:
        nodebudget.save_config(workload_minutes={"amigos": args.amigos_minutes})
        changed = True
    cfg = nodebudget.load_config()
    nb = nodebudget.state()
    lim = f"{cfg['execution_minutes']:.0f}m" if cfg["execution_minutes"] else "∞（0 = 無制限）"
    print(f"ノード予算{'を更新しました' if changed else ''}: 合計 {lim} / period={cfg['period']}")
    print(f"  消費（{nb['period']}・全ワークロード合計）: {nb['spent_s'] / 60:.1f}m"
          f"{'  ← 超過中' if nb['exceeded'] else ''}")
    for wl, mins in sorted(cfg.get("workloads", {}).items()):
        if mins:
            print(f"  内訳上限 {wl}: {mins:.0f}m"
                  f"（消費 {nodebudget.spent_seconds(cfg['period'], wl) / 60:.1f}m）")
    print(f"  設定/台帳: {nodebudget.budget_dir()}")
    return 0


def cmd_say(args) -> int:
    bus, node = _resolve(args)
    mp, mission, roles = _mission(bus, args.mission)
    frm = "owner" if mission.get("owner_node") == node else f"human:{node}"
    if not valid_target(args.to, roles):
        raise SystemExit(f"[agent-amigos] 宛先が不正です: {args.to!r}")
    bus.sync_pull()
    _mid, msg = build_message(frm, args.to, args.type, args.subject or "", args.body)
    write_json_atomic(message_path(mp, msg), msg)
    bus.sync_push(f"say {args.mission}")
    print(f"送信しました: {frm} → {args.to} ({msg['id']})")
    return 0


def cmd_cancel(args) -> int:
    bus, node = _resolve(args)
    mp, mission, _roles = _mission(bus, args.mission)
    _require_owner(mission, node)
    bus.sync_pull()
    write_json_atomic(mp.cancelled(), {"ts": now_iso(), "by": node,
                                       "reason": args.reason or ""})
    bus.sync_push(f"cancel {args.mission}")
    print(f"中止しました: {args.mission}")
    return 0


def cmd_gc(args) -> int:
    bus, _node = _resolve(args)
    keep_s = args.keep_days * 86400
    removed = 0
    for mid in bus.list_missions():
        mp = bus.mission(mid)
        end_file = None
        for p in (mp.final(), mp.cancelled()):
            data = read_json(p)
            if data and (data.get("accepted") or p == mp.cancelled()):
                end_file = p
        if not end_file:
            continue
        if time.time() - os.path.getmtime(end_file) < keep_s:
            continue
        bus.remove_mission(mid)     # GitBus はブランチ削除 + index 除去（§5.1）
        removed += 1
        print(f"削除: {mid}")
    print(f"gc 完了（{removed} 件削除）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="agent-amigos",
        description="役割駆動マルチエージェント協働ツール（設計書: docs/designs/agent-amigos-design.md）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-bus", help="バスを初期化する")
    _bus_arg(p); _node_arg(p)
    p.set_defaults(fn=cmd_init_bus)

    p = sub.add_parser("post", help="ミッションを公示する（オーナー）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("--design", required=True, help="design doc（Markdown）")
    p.add_argument("--roles", required=True, help="役割ミッション表（YAML/JSON）")
    p.add_argument("--mission-id", default=None)
    p.add_argument("--serve", action="store_true",
                   help="公示後そのままオーナーノードのデーモンとして常駐する")
    p.add_argument("--agent-cli", default=None)
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--cycles", type=int, default=0, help="デーモン巡回数（0=無限。テスト用）")
    p.add_argument("--resume-hours", type=float, default=12.0,
                   help="graceful offboard 時の resume_at（時間後。away 保持の期待復帰時刻）")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("join", help="参加ノードのデーモンを起動する")
    _bus_arg(p); _node_arg(p)
    p.add_argument("--roles", default="", help="応募するロールの絞り込み（カンマ区切り）")
    p.add_argument("--tags", default="", help="ノードの能力タグ（カンマ区切り）")
    p.add_argument("--agent-cli", default=None,
                   help="このノードの既定 agent CLI（kiro/claude/copilot/codex/stub/プラグイン名）")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--cycles", type=int, default=0)
    p.add_argument("--resume-hours", type=float, default=12.0,
                   help="graceful offboard 時の resume_at（時間後。away 保持の期待復帰時刻）")
    p.set_defaults(fn=cmd_join)

    p = sub.add_parser("run", help="単発 amigo（デバッグ用）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("--mission", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--agent-cli", default=None)
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("status", help="ミッションの状態を表示する")
    _bus_arg(p); _node_arg(p)
    p.add_argument("mission", nargs="?", default=None)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("collect", help="deliverable を取り出す（オーナー）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("mission")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("accept", help="受入する（オーナー）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("mission")
    p.set_defaults(fn=cmd_accept)

    p = sub.add_parser("reject", help="差し戻す（オーナー）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("mission")
    p.add_argument("--feedback", required=True)
    p.set_defaults(fn=cmd_reject)

    p = sub.add_parser("budget",
                       help="予算の管理: add = ミッション予算の追加（オーナー）、"
                            "node = このノードの上限の表示・設定（請負側）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("action", choices=["add", "node"])
    p.add_argument("mission", nargs="?", default=None)
    p.add_argument("--minutes", type=float, default=None, help="add: 追加する分数")
    p.add_argument("--limit-minutes", type=float, default=None,
                   help="node: 合計上限（分）。0 = 無制限")
    p.add_argument("--period", choices=["day", "month", "total"], default=None,
                   help="node: 上限の適用期間（既定 day）")
    p.add_argument("--amigos-minutes", type=float, default=None,
                   help="node: amigos ワークロードの内訳上限（分）。0 = 無制限")
    p.set_defaults(fn=cmd_budget)

    p = sub.add_parser("say", help="人がバスに直接発言する（介入）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("mission")
    p.add_argument("--to", required=True)
    p.add_argument("--type", default="info")
    p.add_argument("--subject", default="")
    p.add_argument("--body", required=True)
    p.set_defaults(fn=cmd_say)

    p = sub.add_parser("cancel", help="ミッションを中止する（オーナー）")
    _bus_arg(p); _node_arg(p)
    p.add_argument("mission")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("gc", help="終了済みミッションを掃除する")
    _bus_arg(p); _node_arg(p)
    p.add_argument("--keep-days", type=float, default=14)
    p.set_defaults(fn=cmd_gc)
    return ap


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except BrokenPipeError:      # `| head` 等でパイプが閉じられた場合は正常終了
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
