"""agentcore.harness.toolloop — 限定ツール契約のハーネス（agent_loop からの移植）。

ツールループを持たない headless CLI（`agents/<name>.json` の `headless_autonomy:
single-shot`）へ、read_files / write_files / run / final の 4 つだけを許す契約で
ツール実行を供給する。層の判定（`run_prompt`）もここが持つ。

**本文は `agent_loop/toolloop.py` の逐語コピーである。** 変えたのは下の前置きだけで、
断片が共有名前空間から借りていた名前を `_borrowed` から供給している。一致は
`tests/test_harness_parity.py` が AST で縛る。移植の背景は :mod:`agentcore.harness`。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from agentcore.harness import _borrowed

# 断片が共有名前空間から借りていた名前（借用はこの 3 つと stdlib だけ）。
# 同じ綴りで供給するので、以下の本文は agent_loop の断片と 1 バイトも変わらない。
agent_home_subdir = _borrowed.agent_home_subdir
_import_agentcli = _borrowed.import_agentcli


def _node_budget_record(*args, **kwargs):
    """記帳は差し替え可能なフック経由（既定は何もしない）。呼ぶたびに現在値を引く。"""
    return _borrowed.node_budget_record(*args, **kwargs)


# --------------------------------------------------------------------------
# 本文は _toolloop_body.py（agent_loop と共有するデータ）。写しは持たない。
# 実パスで compile する。traceback と inspect.getsource がこの 1200 行超の本文を
# 正しく辿れるかどうかは、落ちたときの調査しやすさに直結する。
_body_path = __import__("os").path.join(__import__("os").path.dirname(__file__),
                                        "_toolloop_body.py")
with open(_body_path, encoding="utf-8") as _fh:
    exec(compile(_fh.read(), _body_path, "exec"), globals())
del _body_path, _fh
