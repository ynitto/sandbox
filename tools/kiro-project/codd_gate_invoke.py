#!/usr/bin/env python3
"""codd_gate_invoke — codd-gate 呼び出し1回分の結果を表す値オブジェクトと no-op 実行契約
（tools/kiro-project 配下、タスク kiro-project-codd-gate-171537）。

`codd_gate_status.CoddGateStatus` が「使ってよいか」（no-op 縮退の判定）を担うのに対し、
このモジュールは「実際に呼んで何が返ってきたか」を1つの値オブジェクト（`CoddGateResult`）に
閉じ込める。呼び出し側（regression/acceptance/enqueue の3フック）は `result.status` の3値
（`"ok" | "failed" | "skipped"`）だけを見れば済み、codd-gate が未インストール・非互換・
実行時エラーのいずれであっても例外が外へ漏れることはない——任意依存が「壊れない」ことを
この型と `invoke_codd_gate` の実装だけで担保する。

3値の使い分け:
  - `"ok"`     : プロセスが完走し exit code 0（codd-gate 自身の「一貫性ゲート通過」判定）。
  - `"failed"` : プロセスは完走したが exit code 非0（codd-gate 自身が「NG」と判定した、
    つまり呼び出し側が実際に受け止めるべき本物のゲート失敗シグナル）。
  - `"skipped"`: それ以外すべて——未検出・非互換（`CoddGateStatus.usable` が False）、
    起動失敗（バイナリが実行時に消えた等）、タイムアウトを含む。「わからない・起動できない」を
    「NG」と混同しない（codd_gate_detect.py / codd_gate_status.py が一貫して採る
    「不明・不足はすべて連携しない側に倒す」方針を呼び出し結果にもそのまま適用したもの）。
    呼び出し側は skipped を「ゲート無効時と同じ既存挙動」として扱えばよく、本体の done/regression
    判定を止めない。

このモジュールが意図的に含めないもの（同一 run の他タスクの責務）:
  - 実在解決・能力判定（codd_gate_detect.py）
  - no-op 判定そのもの（codd_gate_status.py。本モジュールは `CoddGateStatus.command()` を
    1回呼ぶだけで、usable 判定ロジックを重複させない）
  - kiro-project.py 本体（`_settle_task`/`evaluate_acceptance`/`run_intake`）への結線

依存は標準ライブラリのみ。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

# cfg.verify_timeout の既定値（kiro-project.py CONFIG_DEFAULTS）と揃え、
# 呼び出し側が明示指定しなくても既存の verify 系コマンドと同程度の猶予を持たせる。
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class CoddGateResult:
    """codd-gate 呼び出し1回分の結果（プロセス内一過性の値オブジェクト。ディスクには乗らない）。"""
    status: str                      # "ok" | "failed" | "skipped"
    exit_code: "int | None"          # プロセスが完走しなかった場合は None（skipped の一部）
    stdout: str
    reason: str = ""                 # skipped/failed の理由。ok なら空文字列

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _skipped(reason: str) -> CoddGateResult:
    return CoddGateResult(status="skipped", exit_code=None, stdout="", reason=reason)


def invoke_codd_gate(
    status, *args: str, run=subprocess.run, timeout: float = DEFAULT_TIMEOUT
) -> CoddGateResult:
    """`CoddGateStatus` と追加 argv から codd-gate を1回実行し `CoddGateResult` へ縮退させる。

    `status.usable` が False（未検出・非互換）なら実行そのものを試みず即座に skipped を返す
    （`status.reason` をそのまま転記——codd-gate プロセスは一切起動しない）。usable=True でも
    起動・完走に失敗する可能性（バイナリが実行時に消えた・timeout）は残るため、その経路も
    例外を外へ漏らさず skipped へ倒す。「実行できた上での NG」（exit 非0）だけを failed とし、
    それ以外の不確実性はすべて skipped に寄せる——呼び出し側が失敗理由を区別する必要をなくす、
    既存の `codd_gate_status.build_status` と同じ no-op 縮退の思想をそのまま踏襲する。
    """
    argv = status.command(*args)
    if argv is None:
        return _skipped(status.reason or "codd-gate は利用できない（未検出または非互換）")
    try:
        proc = run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _skipped(f"codd-gate の呼び出しがタイムアウトした（{timeout}s）")
    except (OSError, subprocess.SubprocessError) as exc:
        return _skipped(f"codd-gate の起動に失敗した: {exc}")
    if proc.returncode == 0:
        return CoddGateResult(status="ok", exit_code=0, stdout=proc.stdout or "")
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-500:]
    return CoddGateResult(
        status="failed", exit_code=proc.returncode, stdout=proc.stdout or "",
        reason=f"exit={proc.returncode} {tail.strip()}"[:500],
    )
