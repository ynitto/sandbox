#!/usr/bin/env python3
"""codd-gate の明示設定に使う推奨文字列と routing 引数を組み立てる純粋関数。

この sibling モジュールは、利用者または install 手順が ``regression_cmd`` /
``intake_cmd`` を明示設定するための文字列と、``--repos`` / ``--repo-dir`` の値だけを
提供する。codd-gate の検出、yaml への書き込み、doctor 所見、agent_project パッケージへの
結線は行わない。

呼び出し側が repos.json のパス、実行 cwd、ワークスペース名を解決して渡す。依存は標準
ライブラリのみ。
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_REPO_DIR = "."
DEFAULT_BASE_PLACEHOLDER = '"$KIRO_BASE_REV"'


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


def recommend_regression_cmd(
    repos_path: "str | Path",
    vcwd: "str | Path | None" = None,
    base: str = DEFAULT_BASE_PLACEHOLDER,
) -> str:
    """明示設定する ``regression_cmd`` の推奨値を返す。"""
    return f"codd-gate verify --base {base} --repos {resolve_repos_arg(repos_path, vcwd)}"


def recommend_intake_cmd(
    repos_path: "str | Path",
    vcwd: "str | Path | None" = None,
) -> str:
    """明示設定する ``intake_cmd`` の推奨値を返す。"""
    return f"codd-gate tasks --debt --repos {resolve_repos_arg(repos_path, vcwd)}"
