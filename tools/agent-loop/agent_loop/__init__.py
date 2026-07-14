"""agent-loop — tmux 上でエージェント CLI を定期駆動するループ。

元は単一ファイル agent-loop.py を、LLM ワーカーが1ファイルを丸ごと読んでも context を
圧迫しない大きさの断片 (*.py) に分割したパッケージ。

分割方式は「単一名前空間フラグメント合成」（agent-project / agent-flow と同じ）:
  各断片は独立 import せず、この __init__ が依存順に **1つの共有名前空間（このモジュールの
  globals）へ exec** して合成する。合成後の実行時名前空間は元の単一ファイルと同一。
"""
import pkgutil as _pkgutil

_FRAGMENTS = (
    "_head",
    "semaphore",
    "inbox",
    "cron",
    "config",
    "tmux_util",
    "session",
    "scheduler",
    "webhook",
    "interactive",
    "sendcmd",
    "cli",
)

_g = globals()
for _name in _FRAGMENTS:
    _src = _pkgutil.get_data(__name__, _name + ".py")
    _code = compile(_src, _name + ".py", "exec")
    exec(_code, _g)

del _pkgutil, _g, _name, _src, _code
