#!/usr/bin/env python3
"""agent-project — Loop Engineering MVP（単一プロジェクトのバックログを捌く制御層）。

元は単一ファイル agent-project.py（約11,500行）だったものを、LLM ワーカーが1ファイルを丸ごと
読んでも context を圧迫しない大きさの断片 (*.py) に分割したパッケージ。

分割方式は「単一名前空間フラグメント合成」:
  各断片は独立 import せず、この __init__ が依存順に **1つの共有名前空間（このモジュールの
  globals）へ exec** して合成する。合成後の実行時名前空間は元の単一ファイルと完全に同一なので、
  テストのモンキーパッチ（km.<name> = ...）・global 再束縛・モジュールレベルのキャッシュ・
  private シンボル（km._foo）参照はすべて元と1バイトも変わらず動く。

断片は _FRAGMENTS の順で exec される（＝元ファイルの記述順）。各断片の先頭には
`from __future__ import annotations` を置くこと（注釈を文字列化し、後方定義シンボルへの
前方参照が def 時に評価されないようにするため）。
"""
import pkgutil as _pkgutil

# exec する断片を依存順（＝元ファイルの記述順）に並べる。この順序を保つ限り、元ファイルが
# top-to-bottom で NameError なく実行できた以上、import 時の前方参照はすべて満たされる。
_FRAGMENTS = (
    "_head",       # 共有 import と最下層の定数
    "hooks",       # 任意フック（外部プロバイダ module）の能力ベース解決
    "model",       # Task / enqueue / cohort / intake
    "policy",      # Policy / 自律レベル / パス保護ゲート
    "decisions",   # 決定記録 / DR 学習 / ltm 昇格
    "instances",   # 稼働レジストリ / start・stop・restart
    "state",       # 状態 worktree
    "rules",       # rules.md（恒常ルール）
    "brief",       # run ブリーフ（run/branch スコープ・差し戻し意図とノード発見制約の蓄積・伝播）
    "needs",       # 通知・フィードバック / impact・reject
    "prioritize",  # 優先順位 / assess / spec ルーティング / triage
    "verify",      # verify ゲート / verify 合成
    "request",     # 実行要求の組み立て / ルーティング / workspace 解決
    "flow",        # agent-flow 連携 / act / 委譲 executor
    "config",      # Config / 納品 / journal / settle 補助
    "batch",       # 並列消費 / claims
    "mr",          # タスク MR
    "stategit",    # 状態の git 保存・共有
    "loop",        # 正準ループ run / watch
    "commands",    # 人の操作 / revise / commands 取り込み / stats
    "doctor",      # audit / doctor
    "charter",     # プロジェクト層 / repos / 複数 charter / replan
    "plan",        # repo-map / plan・review / spec 展開
    "gitcache",    # 共有 git キャッシュ + worktree
    "project",     # acceptance / milestone / finalize / cmd_project
    "configfile",  # 設定ファイル解決 / build_config / _add_common
    "update",      # 自動アップデート
    "cli",         # main / サブコマンドのディスパッチ
)

_g = globals()
for _name in _FRAGMENTS:
    # pkgutil.get_data はファイルシステム配置でも zipapp（zip 内）でも断片ソースを読める。
    # open(__file__) は zipapp 内で機能しないため使わない。
    _src = _pkgutil.get_data(__name__, _name + ".py")
    _code = compile(_src, _name + ".py", "exec")
    exec(_code, _g)

del _pkgutil, _g, _name, _src, _code
