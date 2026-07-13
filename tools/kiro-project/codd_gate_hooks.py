#!/usr/bin/env python3
"""codd_gate_hooks — regression/acceptance/enqueue の3フック共通の結線ヘルパ
（tools/kiro-project 配下、タスク kiro-project-codd-gate-171537）。

t8（.kiro-project/bus/runs/req-21481022-kiro-project-codd-gate-171537-r9/artifacts/t8/
codd-gate-hook-interface-spec.md）が確定した契約をそのまま実装する。既存の
`codd_gate_status`/`codd_gate_base`/`codd_gate_routing`/`codd_gate_invoke`/`codd_gate_debt`
を合成する「合流点」であり、検出→引数組み立て→実行→パースの手順を自前で再実装しない。

`kiro-project.py` の型（Config/Task/Charter）には依存しない。呼び出し側（regression/acceptance/
enqueue の3フック）が `cfg`/`task`/`ch` から取り出したプリミティブ値（str/Path/dict）を渡す
（`codd_gate_base.py`/`codd_gate_routing.py` と同じ設計判断）。

ファイル I/O は一切行わない（repos.json は読み取り専用。`build_routing_args` が path 演算のみ
行い、書き込みは `export_repo_registry`（kiro-project.py 既存）の専管で、このモジュールは
`cfg` を受け取らない以上それを呼びようがない）。

依存は標準ライブラリと同梱の codd_gate_status/codd_gate_base/codd_gate_routing/
codd_gate_invoke/codd_gate_debt のみ。
"""
from __future__ import annotations

import shutil
import subprocess

from codd_gate_base import resolve_base_rev
from codd_gate_debt import parse_debt_output
from codd_gate_invoke import DEFAULT_TIMEOUT, invoke_codd_gate
from codd_gate_routing import DEFAULT_REPO_DIR, build_routing_args
from codd_gate_status import CoddGateStatus, detect_status


def run_diff_gate(
    repos_path: "str",
    name: str,
    vcwd: "str",
    task_base_branch: "str | None" = None,
    *,
    status: "CoddGateStatus | None" = None,
    codd_gate_bin: "str | None" = None,
    dir: str = DEFAULT_REPO_DIR,
    env: "dict[str, str] | None" = None,
    which=shutil.which,
    run=subprocess.run,
    timeout: float = DEFAULT_TIMEOUT,
) -> "tuple[bool, str]":
    """`codd-gate verify --strict` を1回実行し pass/fail を得る（regression/acceptance 共通）。

    未検出・非互換（`status.usable == False`）なら無音の no-op（`(True, "")`）。usable だが
    実行時に縮退した（`invoke_codd_gate` が "skipped"）場合のみ理由付きで可視化する
    （t8 3節の意図的な非対称: 未導入環境でのログノイズを避けつつ、導入済み環境の実行時異常は
    追跡できるようにする）。本物のゲート失敗（"failed"）だけが `(False, ...)` を返す。
    """
    if status is None:
        status = detect_status(explicit=codd_gate_bin, which=which, run=run)
    if not status.usable:
        return True, ""
    base_rev = resolve_base_rev(task_base_branch, env=env)
    routing = build_routing_args(repos_path, name, vcwd, dir)
    result = invoke_codd_gate(status, "verify", *routing, "--base", base_rev, "--strict",
                               run=run, timeout=timeout)
    if result.status == "skipped":
        return True, f"codd-gate: {result.reason}"
    if result.status == "failed":
        return False, f"codd-gate: {result.reason}"
    return True, ""


def collect_debt_specs(
    repos_path: "str",
    name: str,
    vcwd: "str",
    *,
    status: "CoddGateStatus | None" = None,
    codd_gate_bin: "str | None" = None,
    dir: str = DEFAULT_REPO_DIR,
    which=shutil.which,
    run=subprocess.run,
    timeout: float = DEFAULT_TIMEOUT,
) -> "tuple[list, str]":
    """`codd-gate tasks --debt` を1回実行し、`run_intake`/`enqueue_task` へそのまま渡せる
    spec dict のリストを得る（enqueue 専用。`--base` は取らない）。

    未検出・非互換・実行時の縮退（skipped/failed）はいずれも空リストへ倒す
    （enqueue 側は「今回は負債0件」と区別しない — t8 3節）。`DriftItem.to_spec()` が
    schemas/task.schema.json 準拠の dict を返すため、変換はここでは行わない。
    """
    if status is None:
        status = detect_status(explicit=codd_gate_bin, which=which, run=run)
    if not status.usable:
        return [], ""
    routing = build_routing_args(repos_path, name, vcwd, dir)
    result = invoke_codd_gate(status, "tasks", "--debt", *routing, run=run, timeout=timeout)
    if result.status != "ok":
        return [], f"codd-gate: {result.reason}"
    parsed = parse_debt_output(result.stdout)
    specs = [item.to_spec() for item in parsed.items]
    return specs, "; ".join(parsed.errors)
