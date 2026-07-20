#!/usr/bin/env python3
"""codd_gate_base — 差分ゲート（regression_cmd）向け base rev 解決（tools/agent-project 配下）。

推奨される regression_cmd は `--base "$KIRO_BASE_REV"` を**シェル変数参照のまま**埋め込む
（`codd_gate_wiring.recommend_regression_cmd` / `codd_gate_regression.build_regression_cmd`）。
これは agent-project 本体がタスク実行時に `KIRO_BASE_REV` を注入できた場合にしか機能しない。
非 git ワークスペース・初回コミット前など、本体が変更 baseline を取れず変数を注入しない状況では
シェル側で未定義 → 空文字に展開され、codd-gate の `--base ""` が失敗する。

本モジュールはその穴を埋める**純粋関数**を1つだけ提供する。ただし**誰も本モジュールを自動では
掴まない**——本体は base rev を Python 側で解決せず（推奨文字列に変数参照をそのまま残す設計）、
本モジュールは `agent_project/hooks.py` の能力契約（`HOOK_CAPABILITIES`）も満たさないため、
sibling 走査でも `hooks:` の明示指定でも本体には繋がらない。使うのは、シェル変数の展開に頼らず
具体的な rev を埋めた regression_cmd を組み立てたい呼び出し元が、明示的に import したときだけ。

本体側の型（Task/Charter）には依存せず、呼び出し側が charter の repo エントリから取り出した
base ブランチ名を文字列として渡す（他の codd_gate_* と同じ、最小依存・単体テスト容易性を
優先する設計）。

このモジュールが意図的に含めないもの:
  - `cfg.regression_cmd` への自動配線・呼び出し
  - repos.json パス／`--repo-dir` の組み立て（codd_gate_routing）
  - codd-gate 検出・no-op 縮退（codd_gate_detect / codd_gate_status）

依存は標準ライブラリのみ。
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
      2. タスクの base ブランチ — charter の repo エントリが持つ `base=`（例 `main`）。
         KIRO_BASE_REV が未注入の場合でも、担当リポジトリの基準ブランチとの差分は取れる。
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
