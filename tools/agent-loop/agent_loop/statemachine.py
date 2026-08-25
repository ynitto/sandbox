from __future__ import annotations
# statemachine.py — aider 等の headless CLI 向けステートマシン実行ハーネス。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# **本文はここに無い。** 正典は agentcore.harness の `_statemachine_body.py` で、agent-herd
# （agentcore.harness.statemachine）とこの断片が同じ本文を exec する。理由と経緯は
# toolloop.py 冒頭を見よ（共有名前空間へのモンキーパッチを保つため、import 委譲にしない）。
#
# 本文は toolloop の名前（_tl_* / _TL_* / ToolLoopError）を共有名前空間から見る。この断片は
# 合成順で toolloop の後に来るので、その前提はそのまま成り立つ。
#
# 設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5。

# 名前は共有名前空間で衝突しないものにする。__init__.py が合成中に使っている
# `_pkgutil` を消すと、次の断片を読む段で NameError になる。
from agentcore import harness as _harness  # noqa: E402
import os as _harness_os  # noqa: E402
import pkgutil as _harness_pkgutil  # noqa: E402

# 開発木では実パスで compile する（traceback が本文の行を指す）。zipapp では
# ファイルとして開けないので pkgutil で読む——そちらは行番号だけが頼りになる。
_harness_body = _harness_os.path.join(
    _harness_os.path.dirname(_harness.__file__), "_statemachine_body.py")
if _harness_os.path.isfile(_harness_body):
    with open(_harness_body, encoding="utf-8") as _harness_fh:
        _harness_src = _harness_fh.read()
    _harness_fh.close()
else:
    _harness_body = "agentcore/harness/_statemachine_body.py"
    _harness_src = _harness_pkgutil.get_data("agentcore.harness",
                                             "_statemachine_body.py").decode("utf-8")
exec(compile(_harness_src, _harness_body, "exec"), globals())
del _harness_body, _harness_src, _harness_pkgutil, _harness_os
