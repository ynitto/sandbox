"""agent-loop — tmux 上でエージェント CLI を定期駆動するループ。

元は単一ファイル agent-loop.py を、LLM ワーカーが1ファイルを丸ごと読んでも context を
圧迫しない大きさの断片 (*.py) に分割したパッケージ。

分割方式は「単一名前空間フラグメント合成」（agent-project / agent-flow と同じ）:
  各断片は独立 import せず、この __init__ が依存順に **1つの共有名前空間（このモジュールの
  globals）へ exec** して合成する。合成後の実行時名前空間は元の単一ファイルと同一。
"""
import os as _os
import pkgutil as _pkgutil
import sys as _sys

_agentcore_dir = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "agent-tools", "agentcore")
if _agentcore_dir not in _sys.path:
    _sys.path.insert(0, _agentcore_dir)

_FRAGMENTS = (
    "_head",
    "cliprofile",
    "turnhooks",
    "semaphore",
    "dispatch",
    "execution",
    "inbox",
    "cron",
    "config",
    "instructions",
    "session_commands",
    "tuning",
    "control",
    "tmux_util",
    "session",
    "sandbox",
    "scheduler",
    "repository_ui",
    "webhook",
    "doctor",
    "interactive",
    "sendcmd",
    "toolloop",
    "statemachine",
    "update",
    "cli",
)

_g = globals()
for _name in _FRAGMENTS:
    _src = _pkgutil.get_data(__name__, _name + ".py")
    _code = compile(_src, _name + ".py", "exec")
    exec(_code, _g)

del _os, _pkgutil, _sys, _agentcore_dir, _g, _name, _src, _code
