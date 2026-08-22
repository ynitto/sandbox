"""agent-audit — 実行証跡・エージェント CLI セッションログの収集と知見蒸留の独立 CLI。

設計の「なぜ」: docs/designs/agent-audit-design.md ／ 契約・設定キー・上限: docs/specs/agent-audit-spec.md。
4 ツール（agent-project / agent-flow / agent-amigos / agent-loop）と CLI ネイティブの
セッションストアを読み取り専用で収集・正規化し、トークン使用量の実測集計と
知見・スキル改善点の蒸留を行う。集計・相関・レポートは決定的（LLM 不使用）、
LLM は extract / distill / review の 3 purpose に限定する。
"""
import os as _os
import sys as _sys

# agentcore（agentcli 等の共通ライブラリ）への import 経路。
# 開発木・リポジトリ内直接実行では tools/agent-tools/agentcore にある
# （tools/agent-audit/agent_audit/__init__.py から見て ../../agent-tools/agentcore）。
# zipapp 配布では install.sh が agentcore/ を同じアーカイブへ同梱するため、下記の
# 追加パスは（存在しなくても無害に）素通りし、素の `import agentcore` が解決する。
_agentcore_dir = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "agent-tools", "agentcore")
if _agentcore_dir not in _sys.path:
    _sys.path.insert(0, _agentcore_dir)
del _os, _sys, _agentcore_dir

from .cli import main  # noqa: F401,E402

__version__ = "0.1.0"
