"""cli — agent-board のサブコマンド。

  agent-board                サブコマンド省略 = serve（常駐入札デーモン）
  agent-board post           委譲を公示（--file 封筒 / --goal などから組み立て）
  agent-board register       このノードを板へ登録（能力宣言）
  agent-board serve          入札デーモン（ポーリング → 落札 → 引き渡し）
  agent-board status         公示と入札・落札・成果の一覧
  agent-board award          owner-picks の落札確定（依頼者）
  agent-board cancel         公示の中止
  agent-board gc             終端した委譲の掃除
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import board as _board
from . import config as _config
from . import daemon as _daemon
from .core import make_bus, now_iso


def _bus(args, settings, node_id):
    return make_bus(settings["board"], node_id, workdir=settings.get("board_workdir"),
                    branch=settings.get("board_branch", "main"),
                    interval=float(settings.get("interval") or 15.0))


def derive_phase(bus, did: str) -> str:
    if bus.is_cancelled(did):
        return "cancelled"
    res = bus.read_result(did)
    if res:
        return "failed" if res.get("status") == "failed" else "done"
    statuses = bus.list_status(did)
    win = bus.winner(did)
    if win and win in statuses:
        st = statuses[win].get("state")
        if st in ("working", "waiting", "reviewing"):
            return st
        return "working"
    if win:
        return "working"
    return "open"


def cmd_post(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    if args.file:
        with open(os.path.expanduser(args.file), encoding="utf-8") as f:
            env = json.load(f)
    else:
        if not args.goal or not args.workload:
            print("post には --file か（--goal かつ --workload）が必要です", file=sys.stderr)
            return 2
        env = {"op": "post", "version": 1, "workload": args.workload, "goal": args.goal}
        if args.title:
            env["title"] = args.title
        if args.design:
            with open(os.path.expanduser(args.design), encoding="utf-8") as f:
                env["design"] = f.read()
        if args.workspace:
            env["workspace"] = {"url": args.workspace}
        if args.roles:
            with open(os.path.expanduser(args.roles), encoding="utf-8") as f:
                roles = json.load(f)
            env.setdefault("engine", {}).setdefault("amigos", {})["roles"] = roles
    env.setdefault("op", "post")
    env.setdefault("version", 1)
    if not env.get("id"):
        env["id"] = _board.mint_id()
    env.setdefault("requested_by", f"agent-board:{node_id}")
    env = _board.validate_post(env)
    bus = _bus(args, settings, node_id)
    bus.ensure_root()
    bus.sync_pull()
    if bus.read_post(env["id"]) is not None:
        print(f"既に同一 id の公示があります（冪等・二重公示防止）: {env['id']}")
        return 0
    bus.write_post(env["id"], env)
    bus.sync_push(f"post {env['id']}")
    print(env["id"])
    print(f">>> 委譲を公示しました: {env['id']}（workload={env['workload']}）", file=sys.stderr)
    return 0


def cmd_register(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    bus = _bus(args, settings, node_id)
    _daemon.register_node(bus, node_id, settings)
    print(f"ノードを登録しました: {node_id}")
    return 0


def cmd_serve(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    bus = _bus(args, settings, node_id)
    _daemon.serve(bus, node_id, settings, cycles=args.cycles)
    return 0


def cmd_status(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    bus = _bus(args, settings, node_id)
    bus.sync_pull()
    dids = [args.id] if args.id else bus.list_delegations()
    for did in dids:
        env = bus.read_post(did) or {}
        phase = derive_phase(bus, did)
        win = bus.winner(did) or "-"
        bids = sorted(bus._list_bids(did).keys())
        print(f"{did}  [{phase}]  workload={env.get('workload', '?')}  "
              f"winner={win}  bids={bids or '[]'}  {env.get('title') or env.get('goal', '')[:40]}")
    if not dids:
        print("（公示なし）")
    return 0


def cmd_award(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    bus = _bus(args, settings, node_id)
    bus.sync_pull()
    bus.write_award(args.id, args.node, awarded_by=node_id)
    bus.sync_push(f"award {args.id} -> {args.node}")
    print(f"落札を確定しました: {args.id} -> {args.node}")
    return 0


def cmd_cancel(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    bus = _bus(args, settings, node_id)
    bus.sync_pull()
    bus.write_cancelled(args.id, args.reason or "", who=node_id)
    bus.sync_push(f"cancel {args.id}")
    print(f"公示を中止しました: {args.id}")
    return 0


def cmd_gc(args) -> int:
    settings = _config.load_settings(args)
    node_id = _config.resolve_node_id(args, settings)
    bus = _bus(args, settings, node_id)
    bus.sync_pull()
    removed = []
    for did in bus.list_delegations():
        if bus.is_cancelled(did) or bus.has_result(did):
            bus.remove_delegation(did)
            removed.append(did)
    if removed:
        bus.sync_push(f"gc {len(removed)} delegations")
    print(f"掃除しました: {len(removed)} 件 {removed}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-board",
                                description="委譲公示板 — 依頼の公示・入札・成果一本化の分散バックエンド")
    p.add_argument("--config", default=None, help="設定ファイル（既定は探索）")
    p.add_argument("--board", default=None, help="板の場所（ローカル dir / git+<url>）")
    p.add_argument("--node-id", dest="node_id", default=None, help="ノード id")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("post", help="委譲を公示する")
    sp.add_argument("--file", default=None, help="委譲封筒 JSON（delegation post）")
    sp.add_argument("--workload", choices=["flow", "amigos"], default=None)
    sp.add_argument("--goal", default=None)
    sp.add_argument("--title", default=None)
    sp.add_argument("--design", default=None, help="design doc ファイル")
    sp.add_argument("--workspace", default=None, help="成果物リポジトリ URL")
    sp.add_argument("--roles", default=None, help="役割ミッション表 JSON（amigos）")
    sp.set_defaults(func=cmd_post)

    sp = sub.add_parser("register", help="このノードを板へ登録する")
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("serve", help="入札デーモン（既定）")
    sp.add_argument("--cycles", type=int, default=None, help="巡回回数（省略 = 無限）")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("status", help="公示・入札・成果の一覧")
    sp.add_argument("id", nargs="?", default=None)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("award", help="owner-picks の落札確定")
    sp.add_argument("id")
    sp.add_argument("node")
    sp.set_defaults(func=cmd_award)

    sp = sub.add_parser("cancel", help="公示を中止する")
    sp.add_argument("id")
    sp.add_argument("--reason", default="")
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("gc", help="終端した委譲を掃除する")
    sp.set_defaults(func=cmd_gc)
    return p


_KNOWN = {"post", "register", "serve", "status", "award", "cancel", "gc"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # サブコマンド省略 = serve（グローバルフラグは先頭・サブコマンドは末尾。argparse の順序に従う）。
    has_sub = any(a in _KNOWN for a in argv)
    has_help = any(a in ("-h", "--help") for a in argv)
    if not has_sub and not has_help:
        argv = [*argv, "serve"]
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        args = build_parser().parse_args(["serve"])
    return args.func(args)
