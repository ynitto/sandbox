#!/usr/bin/env python3
"""agent-opencode — 実体は :mod:`agentcore.opencode_adapter`（agent-herd と同じ 1 実装）。

このファイルは**開発木から直接叩くための入口**であって実装ではない。実装を agentcore へ
移したのは、`~/.profile` からの環境補完（`agentcore.hostenv`）を 3 adapter の複製ではなく
1 実装にするためで、単体ファイルで配っていた頃の写しはもう無い。

配布形態も単体ファイルのコピーではなく、agentcore を同梱した zipapp になった
（`tools/opencode/install.sh` と `tools/agent-tools/install.sh` の双方）。

設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §1.1 / §6。
"""
from __future__ import annotations

import os
import sys

_AGENTCORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agent-tools", "agentcore")
if os.path.isdir(_AGENTCORE) and _AGENTCORE not in sys.path:
    sys.path.insert(0, _AGENTCORE)

import agentcore.opencode_adapter as _impl  # noqa: E402

# 実装の名前をこのモジュールへそのまま写す（private も含む）。既存の呼び出し側・
# テスト（tools/opencode/tests/）はこのファイルをパスから読み込んで中の名前を触るので、
# 委譲に変えたことをそれらに気づかせない。
_KEEP = {"__name__", "__file__", "__loader__", "__spec__", "__package__",
         "__builtins__", "__doc__"}
globals().update({k: v for k, v in vars(_impl).items() if k not in _KEEP})

main = _impl.main

if __name__ == "__main__":
    raise SystemExit(main())
