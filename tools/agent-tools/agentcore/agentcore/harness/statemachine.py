"""agentcore.harness.statemachine — ステートマシン実行ハーネス（agent_loop からの移植）。

限定ツール契約（:mod:`agentcore.harness.toolloop`）の上に、状態遷移・出力契約・
テンプレート展開を載せる。遷移の検証は statemachine-use スキルのスクリプト
（`run_machine.py` / `next_state.py`）が正典で、ここはそれを呼ぶ側である。

**本文は `agent_loop/statemachine.py` の逐語コピーである。** 変えたのは下の前置きだけ。
一致は `tests/test_harness_parity.py` が AST で縛る。背景は :mod:`agentcore.harness`。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from agentcore.harness import _borrowed
from agentcore.harness.toolloop import (  # noqa: F401  (断片が共有名前空間で見ていた名前)
    ToolLoopError,
    _TL_FAILURE_RE,
    _TL_HARNESS_TIMEOUT_SEC,
    _TL_MAX_AUTO_READ_BYTES,
    _TL_MAX_TOOL_ROUNDS,
    _TL_MAX_TOOL_TIMEOUT_SEC,
    _tl_action_project_files,
    _tl_action_skill_names,
    _tl_append_log,
    _tl_control_agent,
    _tl_exec_argv,
    _tl_executable_on_path,
    _tl_file_stamp,
    _tl_final_evidence_error,
    _tl_history_has_run,
    _tl_inside,
    _tl_parse_json_object,
    _tl_parse_tool_request,
    _tl_progress,
    _tl_project_path,
    _tl_python_command,
    _tl_python_ok,
    _tl_resolve_agent,
    _tl_resolve_skill,
    _tl_run_agent,
    _tl_run_control,
    _tl_skill_declared_scripts,
    _tl_skill_scripts,
    _tl_skill_search_dirs,
    _tl_validate_tool_request,
)


def _control_policy_decision(*args, **kwargs):
    """control 解決は差し替え可能なフック経由（既定は None＝selection_policy 無し）。"""
    return _borrowed.control_policy_decision(*args, **kwargs)


# --------------------------------------------------------------------------
# 本文は _statemachine_body.py（agent_loop と共有するデータ）。写しは持たない。
# 実パスで compile する。traceback と inspect.getsource がこの 1200 行超の本文を
# 正しく辿れるかどうかは、落ちたときの調査しやすさに直結する。
_body_path = __import__("os").path.join(__import__("os").path.dirname(__file__),
                                        "_statemachine_body.py")
with open(_body_path, encoding="utf-8") as _fh:
    exec(compile(_fh.read(), _body_path, "exec"), globals())
del _body_path, _fh
