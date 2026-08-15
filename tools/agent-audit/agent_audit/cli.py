"""CLI — サブコマンドのディスパッチ（すべて単発・有界。設計 §8）。

終了コード: 0=成功 / 1=検出あり・LLM 段の停止 / 2=源泉が読めない・使い方の誤り。
"""
from __future__ import annotations

import argparse

from .configfile import resolve_config


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-audit",
        description="実行証跡・エージェント CLI セッションログの収集と知見蒸留"
                    "（設計: docs/designs/agent-audit-design.md）")
    p.add_argument("--config", help="設定ファイル（既定は agent-audit.yaml を探索）")
    p.add_argument("--audit-dir", dest="audit_dir",
                   help="書き先（既定 ~/.agents/audit。引数 > 設定 > 既定）")
    p.add_argument("--budget-dir", dest="budget_dir",
                   help="node-budget の場所（既定 ~/.agents/budget）")
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("collect", help="源泉と対応CLI quotaの増分収集・正規化（LLM不使用）")
    c.add_argument("--source", action="append",
                   help="収集する源泉を絞る（budget-ledger / cli-native / cli-quota / flow-bus / "
                        "project-root / amigos-bus / loop-log。複数可）")
    c.add_argument("--since", help="この時刻（ISO8601）以降のセッションだけ収集")
    c.add_argument("--with-transcripts", action="store_true", dest="with_transcripts",
                   help="transcript 本文もローカル保存する（ノード外へは出さない）")

    u = sub.add_parser("usage", help="トークン・コスト集計（measured / estimated 別掲）")
    u.add_argument("--period", choices=["day", "month", "total"], default=None)
    u.add_argument("--by", choices=["workload", "tool", "agent_cli", "model", "purpose", "ref", "node"],
                   default=None)
    u.add_argument("--json", action="store_true")

    s = sub.add_parser("stats", help="実行品質集計（status・失敗クラス・verify）")
    s.add_argument("--period", choices=["day", "month", "total"], default=None)
    s.add_argument("--json", action="store_true")

    rating = sub.add_parser("ratings", help="仕事種別×モデルの PASS 率と平均消費の格付け")
    rating.add_argument("--period", choices=["day", "month", "total"], default=None)
    rating.add_argument("--methods", action="store_true", help="適用手法セットを集計軸に加える")
    rating.add_argument("--json", action="store_true")

    trials = sub.add_parser("trials", help="手法 trial の variant 別 PASS 率・平均消費を比較")
    trials.add_argument("--period", choices=["day", "month", "total"], default=None)
    trials.add_argument("--min-outcomes", type=int, dest="trial_min_outcomes", default=None,
                        help="判定に要る片側あたりの結果サンプル下限（既定 3）")
    trials.add_argument("--json", action="store_true")

    cal = sub.add_parser("calibrate", help="rates 較正の提案（--write で budget config へ反映）")
    cal.add_argument("--write", action="store_true")

    e = sub.add_parser("extract", help="レコード → 観測（LLM map。ゲートを通ったときだけ実行）")
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--force", action="store_true",
                   help="間隔・蓄積ゲートを飛ばす（段別上限と予算は飛ばせない）")

    d = sub.add_parser("distill", help="観測クラスタ → 洞察（LLM reduce）")
    d.add_argument("--limit", type=int, default=0)
    d.add_argument("--review", action="store_true", help="洞察を review purpose で検証する")
    d.add_argument("--force", action="store_true",
                   help="間隔・蓄積ゲートを飛ばす（段別上限と予算は飛ばせない）")

    r = sub.add_parser("report", help="Markdown レポート")
    r.add_argument("--kind", choices=["usage", "quality", "insights", "all"], default="all")
    r.add_argument("--out", help="出力先（既定は <audit>/reports/）")

    t = sub.add_parser("tasks", help="洞察 → 改善タスク（task.schema.json 形を stdout へ）")
    t.add_argument("--mark-exported", action="store_true", dest="mark_exported",
                   help="出力した洞察を exported=true にする")

    tune = sub.add_parser("tune", help="洞察から型付き調整候補を生成し、条件を満たせば昇格・退役")
    tune.add_argument("--apply", action="store_true", help="再現・品質・予算ゲートを通った候補を宣言へ反映")
    tune.add_argument("--period", choices=["day", "month", "total"], default=None)
    tune.add_argument("--json", action="store_true")

    qualify = sub.add_parser(
        "qualify", help="本番 receipt から候補適格性（qualifications.json）を昇格・降格・期限切れ")
    qualify.add_argument("--apply", action="store_true",
                         help="qualifications.json へ原子書換（既定は dry-run）")
    qualify.add_argument("--window-days", type=int, default=0,
                         help="観測窓の上書き（既定は evaluation profile の window_days）")
    qualify.add_argument("--qualifications-file", default="",
                         help="出力先の上書き（既定 ~/.agents/control/qualifications.json）")

    g = sub.add_parser("gc", help="種別別保持日数での掃除（insights は対象外）")
    g.add_argument("--dry-run", action="store_true", dest="dry_run")

    rc = sub.add_parser("reclean", help="clean ルール改訂後に既存 transcript を再生成する"
                                        "（records・処理済み管理は不変）")
    rc.add_argument("--agent-cli", action="append", dest="only_agent_cli",
                     help="対象を絞る CLI 名（複数可。省略時は transcript のある全 CLI）")
    rc.add_argument("--dry-run", action="store_true", dest="dry_run")

    se = sub.add_parser("sessions", help="CLI ネイティブセッションの検索・本文取得（JSON 出力）")
    se.add_argument("--cli", help="対象の CLI 名（agents/<name>.json の name）")
    se.add_argument("--since", help="この時刻（ISO8601）以降に更新のあったセッションだけ")
    se.add_argument("--until", help="この時刻（ISO8601）以前に開始したセッションだけ")
    se.add_argument("--cwd-contains", dest="cwd_contains",
                    help="cwd にこの文字列を含むセッションだけ")
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--messages", metavar="NATIVE_ID",
                    help="このセッションの会話本文（messages）も含めて返す")

    sub.add_parser("doctor", help="源泉の到達性・session_log 宣言の棚卸し")

    up = sub.add_parser("update", help="自己更新（スキルリポジトリから取り込み）")
    up.add_argument("--check", action="store_true", help="確認だけ（取り込まない）")
    up.add_argument("--now", action="store_true", help="今すぐ取り込む")

    return p


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    resolve_config(args)
    if args.command == "collect":
        from .collect import cmd_collect
        return cmd_collect(args)
    if args.command == "usage":
        from .usage import cmd_usage
        return cmd_usage(args)
    if args.command == "stats":
        from .stats import cmd_stats
        return cmd_stats(args)
    if args.command == "ratings":
        from .stats import cmd_ratings
        return cmd_ratings(args)
    if args.command == "trials":
        from .stats import cmd_trials
        return cmd_trials(args)
    if args.command == "calibrate":
        from .usage import cmd_calibrate
        return cmd_calibrate(args)
    if args.command == "extract":
        from .extract import cmd_extract
        return cmd_extract(args)
    if args.command == "distill":
        from .distill import cmd_distill
        return cmd_distill(args)
    if args.command == "report":
        from .report import cmd_report
        return cmd_report(args)
    if args.command == "tasks":
        from .tasksout import cmd_tasks
        return cmd_tasks(args)
    if args.command == "tune":
        from .tuning import cmd_tune
        return cmd_tune(args)
    if args.command == "qualify":
        import json as _json
        from .configfile import resolve_audit_dir
        from .qualifications import cmd_qualify
        from .store import Store
        summary = cmd_qualify(args, Store(resolve_audit_dir(args)))
        print(_json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary.get("error") else 0
    if args.command == "gc":
        from .gccmd import cmd_gc
        return cmd_gc(args)
    if args.command == "reclean":
        from .reclean import cmd_reclean
        return cmd_reclean(args)
    if args.command == "sessions":
        from .sessions import cmd_sessions
        return cmd_sessions(args)
    if args.command == "doctor":
        from .doctor import cmd_doctor
        return cmd_doctor(args)
    if args.command == "update":
        from .update import cmd_update
        return cmd_update(args)
    parser.print_help()
    return 2
