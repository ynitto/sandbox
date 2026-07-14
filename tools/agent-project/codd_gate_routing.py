#!/usr/bin/env python3
"""codd_gate_routing — repos.json パスと --repo-dir マッピングの引数ビルダ（tools/agent-project 配下）。

d2（.agent-project/bus/runs/run-20260712-213419-5922/artifacts/d2/
codd-gate-status-interface-design.md）2節は「repos.json のパスや --repo-dir の実引数は
CoddGateStatus が持たず、各フックが自分の文脈から組み立てる」ことを明記し、その生成方法の仕様を
s6（同 run の artifacts/s6/repos-json-owns-and-repo-dir.md 2・3節）が確定している。本モジュールは
その仕様をそのまま実装する、regression/acceptance/enqueue の3フック（b3/c1/e1）が共通で使う
**純粋関数**。

agent-project.py 側の型（Config/Charter/Task）には依存しない（codd_gate_base.py と同じ設計判断:
呼び出し側が `cfg.backlog.parent`（repos.json の既定置き場）や `_task_verify_cwd(cfg, task)`
（verify 実行時の cwd）、ワークスペース spec の `name` から取り出した値を渡す。最小依存・単体
テスト容易性を優先する）。

このモジュールが意図的に含めないもの（同一 run の別タスクの責務）:
  - `cfg.regression_cmd`/`cfg.intake_cmd` への自動配線・`CoddGateStatus.command()` との合成
    （b3/c1/e1）
  - repos.json 自体の読み書き・既定パス解決（`repo_registry_path`/`export_repo_registry`、
    agent-project.py 既存）
  - ワークスペース spec の解決（`resolve_workspace` 等、agent-project.py 既存）— `name`/`vcwd` は
    呼び出し側が spec から取り出して渡す

依存は標準ライブラリのみ。
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_REPO_DIR = "."


def resolve_repos_arg(repos_path: "str | Path", vcwd: "str | Path | None" = None) -> str:
    """`--repos` に渡す値を解決する（s6 2節）。

    `vcwd`（regression/acceptance/enqueue が実行される cwd＝解決済みワークスペースのクローン
    ルート）配下に `repos_path` があれば `vcwd` からの相対パス（例 `./.agent-project/repos.json`）、
    配下になければ絶対パスへフォールバックする。相対パスが解決できるのは repos.json 自体が
    対象リポジトリに git 追跡されている「self-hosted」構成のときだけ（s6 4節）——それ以外の
    構成では新規クローンの中に repos.json が存在しないため、絶対パスでなければ壊れる。

    `vcwd` を渡さない呼び出し（agent-project プロセス自身が repos_path と同じ cwd で動く場合等）
    では `repos_path` をそのまま文字列化する。存在確認は行わない（純粋関数。ファイルが実在するかは
    codd-gate 側の起動時チェックに委ねる）。
    """
    if vcwd is None:
        return str(repos_path)
    rp = Path(repos_path)
    try:
        rel = rp.resolve().relative_to(Path(vcwd).resolve())
    except (ValueError, OSError):
        return str(rp.resolve())
    return f"./{rel.as_posix()}"


def resolve_repo_dir_arg(name: str, dir: str = DEFAULT_REPO_DIR) -> str:
    """`--repo-dir` に渡す `NAME=DIR` の1エントリを組み立てる（s6 3節）。

    `dir` の既定値 `.` は、regression/acceptance/enqueue が常に解決済みワークスペースの
    クローンルートを cwd として実行される規約（`_task_verify_cwd`）を前提にしたもの——
    絶対パスを焼き込むとクローン先が変わるたびに壊れるため、vcwd 自体を指す `.` で足りる
    （s6 3節）。
    """
    return f"{name}={dir}"


def build_routing_args(
    repos_path: "str | Path",
    name: str,
    vcwd: "str | Path | None" = None,
    dir: str = DEFAULT_REPO_DIR,
) -> "list[str]":
    """regression/acceptance/enqueue の3フック共通で使う引数ビルダ（b2 本体）。

    `status.command("verify", *build_routing_args(repos_path, name, vcwd), "--base", ..., "--strict")`
    のように `CoddGateStatus.command()`（d2 3節）へそのまま展開できる
    `["--repos", <値>, "--repo-dir", "<name>=<dir>"]` を返す。
    """
    return [
        "--repos", resolve_repos_arg(repos_path, vcwd),
        "--repo-dir", resolve_repo_dir_arg(name, dir),
    ]
