#!/usr/bin/env python3
"""明示 import 利用者との互換性のために base rev 解決 API だけを残す。

新しい sibling レイヤは推奨 ``regression_cmd`` に ``"$KIRO_BASE_REV"`` をそのまま
埋め込み、base rev の解決を共有処理にしない。このモジュールは既存の呼び出し契約を
保つ互換ヘルパーであり、自動検出、yaml 注入、CLI 所見、agent_project パッケージへの
結線は行わない。依存は標準ライブラリのみ。
"""
from __future__ import annotations

import os

FALLBACK_BASE_REV = "HEAD~1"


def resolve_base_rev(
    task_base_branch: "str | None" = None,
    env: "dict[str, str] | None" = None,
) -> str:
    """差分ゲートの基準 rev を解決する。

    優先順位（前段が空ならすぐ次段へ）:
      1. `KIRO_BASE_REV` 環境変数 — 既に注入済み（`git_change_baseline` 等）か
         人/呼び出し元が明示指定したなら、それを常に優先する。
      2. 呼び出し元が渡した base ブランチ（例 `main`）。
         KIRO_BASE_REV が未注入の場合でも、明示された基準ブランチとの差分は取れる。
      3. `HEAD~1` — 上記いずれも得られない最終フォールバック（直前1コミットとの差分）。

    例外は投げない（`env` は plain dict 前提。I/O は行わずローカル判断のみ）。
    """
    env = os.environ if env is None else env
    explicit = (env.get("KIRO_BASE_REV") or "").strip()
    if explicit:
        return explicit
    branch = (task_base_branch or "").strip()
    if branch:
        return branch
    return FALLBACK_BASE_REV
