from __future__ import annotations
# coddgate.py — codd-gate 自動検出・結線用の新規断片（既存ファイルの機械分割ではない）。
# 単体 import しない。kiro_project/__init__.py が _FRAGMENTS 経由で共有名前空間へ exec 合成する
#（本ファイル単体は現時点で未結線。__init__.py 側の _FRAGMENTS 登録・shutil 等の import は
#  _head.py が担う前提で、このファイルではモジュールレベル import を行わない）。
# codd-gate バイナリの検出・存在判定。
# ---------------------------------------------------------------------------

CODD_GATE_BINARY_NAME = "codd-gate"


def _codd_gate_bin() -> "str | None":
    return shutil.which(CODD_GATE_BINARY_NAME)
