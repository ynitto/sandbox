from __future__ import annotations
# toolloop.py — ツールループを持たない headless CLI（agents/<name>.json の
# headless_autonomy: single-shot）へ、限定ツール契約でツール実行を供給する共用ハーネス。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# **本文はここに無い。** 正典は agentcore.harness の `_toolloop_body.py` で、agent-herd
# （agentcore.harness.toolloop）とこの断片が**同じ本文**を exec する。以前はここに 1275 行の
# 実体があり、移植先と 2 つ並んでいた（AST パリティテストでずれを縛っていた）。写しを畳んだ
# のがこの変更である。
#
# なぜ import による委譲にしないのか。agent_loop は「単一名前空間フラグメント合成」で動いて
# おり、テストは `mock.patch.object(agent_loop, "_tl_run_agent")` のように**共有名前空間を
# 差し替える**（57 箇所）。通常の import で委譲すると関数の __globals__ が agentcore 側に
# なるので、その差し替えが効かなくなる。本文をデータとして共有すれば、写しを持たずに
# ここの意味論（合成後の名前空間は元の単一ファイルと同一）をそのまま保てる。
#
# 設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5。

# 本文が使う借用名のうち、記帳（_node_budget_record）と control 解決
# （_control_policy_decision）は agent-loop 固有の状態を触る。agentcore 側の既定は
# 「何もしない / None」なので、こちらの実装をフックへ差し込んでから本文を読み込む。
# **exec より前に差し込む**——本文は _node_budget_record を自分の名前で呼ぶので、
# 差し込みが後だと最初の記帳が落ちる。
from agentcore import harness as _harness  # noqa: E402

_harness.set_hooks(node_budget_record=_node_budget_record,
                   control_policy_decision=_control_policy_decision)

# 名前は共有名前空間で衝突しないものにする。__init__.py が合成中に使っている
# `_pkgutil` を消すと、次の断片を読む段で NameError になる。
import os as _harness_os  # noqa: E402
import pkgutil as _harness_pkgutil  # noqa: E402

# 開発木では実パスで compile する（traceback が本文の行を指す）。zipapp では
# ファイルとして開けないので pkgutil で読む——そちらは行番号だけが頼りになる。
_harness_body = _harness_os.path.join(
    _harness_os.path.dirname(_harness.__file__), "_toolloop_body.py")
if _harness_os.path.isfile(_harness_body):
    with open(_harness_body, encoding="utf-8") as _harness_fh:
        _harness_src = _harness_fh.read()
    _harness_fh.close()
else:
    _harness_body = "agentcore/harness/_toolloop_body.py"
    _harness_src = _harness_pkgutil.get_data("agentcore.harness",
                                             "_toolloop_body.py").decode("utf-8")
exec(compile(_harness_src, _harness_body, "exec"), globals())
del _harness_body, _harness_src, _harness_pkgutil, _harness_os
