#!/usr/bin/env python3
"""codd_gate_detect — codd-gate CLI の実在検出（tools/kiro-project 配下の新規モジュール）。

kiro-project.py（`resolve_kiro_flow`, `kiro-project.py:3477`）が kiro-flow の実体を
explicit → PATH → 同梱パスの順で解決するのと同型の解決連鎖を、codd-gate 向けに提供する。

このモジュールの責務は「CLI が実在するか・どう起動すればよいか」の判定だけに絞る
（設計は .kiro-project/bus/runs/run-20260712-213419-5922/artifacts/d1, d2 を参照）。
以下は意図的にこのモジュールへ含めない（同一 run の別タスクの責務）:
  - バージョン取得・schemas 互換判定・能力フラグ（a2）
  - プロセス内キャッシュ（a3）
  - finding 化・no-op 縮退を含む CoddGateStatus 相当の値オブジェクト（a4, d2）
  - kiro-project.py 本体への結線（b1-b3 / c1-c2 / e1-e2）

依存は標準ライブラリのみ（kiro-project.py 全体の方針に合わせる）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BINARY_NAME = "codd-gate"


def resolve_codd_gate(explicit: "str | None" = None, which=shutil.which) -> "list[str] | None":
    """codd-gate の起動 argv prefix を解決する。見つからなければ None。

    resolve_kiro_flow と対称の解決連鎖（explicit → PATH → 同梱パス）を辿るが、
    kiro-flow と異なり codd-gate は任意機能（無くても kiro-project は動く）なので、
    同梱パスにも実体が無ければ「不明な起動コマンドを組み立てない」意味で None を返す。
    戻り値が str ではなく argv prefix の list なのは、同梱パス経由の起動時に
    Python インタプリタを明示する必要があるため（resolve_kiro_flow と同じ理由）。
    """
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = which(BINARY_NAME)
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent / "codd-gate" / "codd-gate.py"
    if local.exists():
        return [sys.executable, str(local)]
    return None
