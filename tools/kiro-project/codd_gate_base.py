#!/usr/bin/env python3
"""codd_gate_base — 差分ゲート（regression_cmd）向け base rev 解決（tools/kiro-project 配下）。

d2（.kiro-project/bus/runs/run-20260712-213419-5922/artifacts/d2/
codd-gate-status-interface-design.md）4.1 節は regression_cmd の argv を
`--base "$KIRO_BASE_REV"` という**シェル変数参照のまま**組み立てる設計だが、これは
`_settle_task`（kiro-project.py:4906-）が venv 経由で `KIRO_BASE_REV` を注入できた場合にしか
機能しない。`git_change_baseline`/`_task_verify_cwd`（kiro-project.py:831, 5514-5519）は
`_git_out` が空文字を返す（非 git ワークスペース・初回コミット前 等）と `KIRO_BASE_REV` を
一切注入しない（`venv = None`）ため、regression_cmd の `$KIRO_BASE_REV` はシェル側で未定義
→ 空文字に展開され、codd-gate の `--base ""` が `_die` する（tools/codd-gate/codd-gate.py:1078-1080）。

本モジュールはこの穴を埋める、regression フック配線（b3）が使う**純粋関数**を1つだけ提供する。
kiro-project.py 側の型（Task/Charter）には依存せず、呼び出し側が `charter_repo_spec_map(ch)
.get(task.get("workspace"), {}).get("base")` などで取り出した文字列を渡す（a1/a4 と同じ、
最小依存・単体テスト容易性を優先する設計）。

このモジュールが意図的に含めないもの（同一 run の別タスクの責務）:
  - `cfg.regression_cmd` への自動配線・呼び出し（b3）
  - repos.json パス／`--repo-dir` の組み立て（b2）
  - codd-gate 検出・no-op 縮退（a1/a4）

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
