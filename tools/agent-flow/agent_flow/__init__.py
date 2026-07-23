#!/usr/bin/env python3
"""agent-flow — git 共有型・分散 Dynamic Workflow。

元は単一ファイル agent-flow.py（約6,800行）だったものを、LLM ワーカーが1ファイルを丸ごと
読んでも context を圧迫しない大きさの断片 (*.py) に分割したパッケージ。

分割方式は「単一名前空間フラグメント合成」（agent-project と同じ）:
  各断片は独立 import せず、この __init__ が依存順に **1つの共有名前空間（このモジュールの
  globals）へ exec** して合成する。合成後の実行時名前空間は元の単一ファイルと完全に同一なので、
  テストのモンキーパッチ（kf.<name> = ...）・global 再束縛・モジュールレベルのキャッシュ・
  private シンボル（kf._foo）参照はすべて元と1バイトも変わらず動く。

断片は _FRAGMENTS の順で exec される（＝元ファイルの記述順）。各断片の先頭には
`from __future__ import annotations` を置くこと（注釈を文字列化し、後方定義シンボルへの
前方参照が def 時に評価されないようにするため）。
"""
import pkgutil as _pkgutil

# exec する断片を依存順（＝元ファイルの記述順）に並べる。この順序を保つ限り、元ファイルが
# top-to-bottom で NameError なく実行できた以上、import 時の前方参照はすべて満たされる。
_FRAGMENTS = (
    "_head",         # 共有 import・TERMINAL・file lock
    "config",        # 設定ファイル / CONFIG_DEFAULTS / resolve
    "util",          # 小道具（now_iso / read_json / write_json_atomic）
    "instructions",  # グローバル指示（agent-instructions 契約）の読取・描画
    "session_commands",  # セッション開始コマンド（agent-session-commands 契約）の読取・実行
    "bus",           # Bus（ローカルメッセージバス）
    "gitbus",        # GitBus + make_bus / cleanup_active_clones
    "stategit",      # StateGit + daemon 状態ヘルパ
    "gitcache",      # 共有 git キャッシュ + worktree
    "workspace",     # workspace 指示 + Heartbeat
    "patterns",      # PATTERNS + granularity + plan_strategy_*
    "agent",         # run_agent / execute_* / triage / agent プラグイン
    "plugins",       # executor プラグイン loader
    "waits",         # park & poll / service_waits
    "continuation",  # Continuation（再計画）
    "orchestrate",   # cmd_orchestrate
    "work",          # cmd_work
    "run",           # cmd_run + child spawn
    "submit",        # cmd_submit + cmd_cancel
    "board",         # 委譲公示板（agent-board）への参加（入札・引き渡し）
    "daemon",        # daemon lock + cmd_daemon
    "cleanup",       # sweep / cmd_gc
    "status",        # cmd_status / cmd_result
    "doctor",        # cmd_doctor
    "update",        # self_path + 自動アップデート
    "cli",           # build_parser + main
)

_g = globals()
for _name in _FRAGMENTS:
    # pkgutil.get_data はファイルシステム配置でも zipapp（zip 内）でも断片ソースを読める。
    # open(__file__) は zipapp 内で機能しないため使わない。
    _src = _pkgutil.get_data(__name__, _name + ".py")
    _code = compile(_src, _name + ".py", "exec")
    exec(_code, _g)

del _pkgutil, _g, _name, _src, _code
