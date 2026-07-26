#!/usr/bin/env python3
"""codd_gate_regression — regression_cmd の生成と .agent/agent-project.yaml への冪等注入
（tools/agent-project 配下）。

README.md「一貫性ゲート（codd-gate 連携・オプション）」節の規約どおり、有効化は
`regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'` を
agent-project.yaml に書けば足りる。本モジュールはこの1行を人手のコピペではなく検出結果
（codd_gate_wiring.probe_wiring）駆動で組み立て、既存ファイルへ冪等に upsert する
（README が yaml 直書きと並べて案内する、もう一方の有効化経路）。

責務は2つ:
  - build_regression_cmd: CoddGateStatus と --repos を合成し、regression_cmd の値を組み立てる。
  - main: codd_gate_wiring の実測結果を受け、同モジュールの冪等 upsert で既存 yaml を更新する。
    未検出、バージョン不適合、schema 不適合、verify capability 不足では書き込まない。

YAML の行編集と書き込みは codd_gate_wiring.upsert_yaml_text / apply_yaml_file が唯一の実装である。
本モジュールは生成 CLI として前者を呼ぶだけで、別の書き込み API を持たない。

このモジュールが意図的に含めないもの（他タスクの責務）:
  - repos.json 自体の生成（agent-project.py 本体・charter からの自動生成）
  - intake_cmd の注入（成功時 JSON に codd_gate_routing の推奨値は出すが、設定するのは利用者）
  - cfg.regression_cmd の実行時の組み立て（README.md の有効化手順どおり、静的な設定ファイルへの
    注入だけで完結させる）

依存は標準ライブラリと同梱の codd_gate_status／codd_gate_routing／codd_gate_wiring のみ。

CLI:
    python3 tools/agent-project/codd_gate_regression.py \
      --config .agent/agent-project.yaml [--repos <path>] [--dry-run]

`--config` は自動配線に頼らずこの3機能（検出・推奨文字列の生成・yaml 冪等注入）へ到達する
唯一の入口。成功時 JSON は regression_cmd と intake_cmd の推奨値を併記するが、書き込むのは
regression_cmd の1行だけ。終了コードは `EXIT_*` 定数を参照（呼び出し側が「未導入だから飛ばす」と
「パスを間違えている」を区別できるよう別の値にしてある）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from codd_gate_routing import recommend_intake_cmd, recommend_regression_cmd
from codd_gate_status import CoddGateStatus
from codd_gate_wiring import apply_yaml_file, probe_wiring, upsert_yaml_text

# CLI の終了コード。argparse が使用法エラーに使う 2 は避ける（衝突すると呼び出し側が
# 「引数を間違えた」と「codd-gate が無い」を取り違える）。
EXIT_OK = 0
EXIT_CONFIG_MISSING = 1
EXIT_UNUSABLE = 3
DEFAULT_BASE_PLACEHOLDER = '"$KIRO_BASE_REV"'
DEFAULT_REPOS_PATH = ".agent-project/repos.json"
_ROOT_RE = re.compile(r"^root:\s*(\S+)\s*$", re.MULTILINE)


def build_regression_cmd(
    status: CoddGateStatus,
    repos_path: "str | Path" = DEFAULT_REPOS_PATH,
    base: str = DEFAULT_BASE_PLACEHOLDER,
) -> "str | None":
    """README.md 正準値と同形の regression_cmd 文字列を組み立てる。

    `status.command()` が返す実バイナリ（PATH 解決した絶対パス、または同梱パス実行用の
    `[sys.executable, <path>]`）はそのままでは埋め込まない——agent-project.yaml は複数マシン・
    複数ワークスペース間で共有される設定ファイルであり、生成した環境固有の絶対パスを焼き込むと
    codd-gate の導入先が違う別環境で壊れる。実行時の PATH 解決に委ねるため、常に固定の
    `codd_gate_detect.BINARY_NAME`（"codd-gate"）を使う——README.md／GUIDE.md／SKILL.md 等、
    このリポジトリ全体の規約とも一致する。`status.usable` は「codd-gate が利用可能か」の
    判定にのみ使う（no-op 縮退のゲート）。

    `--repo-dir` を含めないのは、この文字列がプロジェクトルート自身で実行される（`_settle_task`
    がタスク毎のワークスペース clone へ移す runtime hook とは異なる）ため、repos.json の各エントリ
    が持つ `dir` で足り、クローン先の上書きが要らないため（codd_gate_routing の docstring・
    実在の agent-project.yaml のいずれとも一致する）。

    status.usable が False（未検出・バージョン不適合・schema 不適合のいずれか）なら None を返す。
    """
    if not status.usable:
        return None
    return recommend_regression_cmd(repos_path, base=base)


def infer_default_repos_path(text: str) -> str:
    """設定ファイルの `root:` から `<root>/repos.json` を推定する（README.md の規約どおり）。
    `root:` が見つからなければ既定 `.agent-project/repos.json` にフォールバックする。"""
    m = _ROOT_RE.search(text)
    if not m:
        return DEFAULT_REPOS_PATH
    root = m.group(1).strip().strip("'\"")
    return f"{root.rstrip('/')}/repos.json"


_EPILOG = f"""\
終了コード:
  {EXIT_OK}  注入した、または既に同じ値が入っていた（冪等 no-op）
  {EXIT_CONFIG_MISSING}  --config の設定ファイルが無い・読めない
  2  引数の使い方が誤っている（argparse）
  {EXIT_UNUSABLE}  codd-gate が使えず regression_cmd を組み立てられない（何も書いていない）

例:
  python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml --dry-run
  python3 tools/agent-project/codd_gate_regression.py --config .agent/agent-project.yaml

成功時 JSON の regression_cmd がこの CLI の注入値。intake_cmd は案内のみで、設定ファイルへは
書き込まないため、人か install 手順が同じ YAML または --intake-cmd へ設定する。
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codd_gate_regression.py",
        description=("codd-gate を実測し、regression_cmd の1行だけを agent-project.yaml へ"
                     "冪等注入する（intake_cmd は成功時 JSON で案内）"),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=".agent/agent-project.yaml",
                        help="注入先の agent-project.yaml。既に存在している必要がある"
                             "（既定 .agent/agent-project.yaml）")
    parser.add_argument("--codd-gate", dest="codd_gate", default=None,
                        help="codd-gate の実体を明示指定（既定は PATH→同梱パスの順で自動解決）")
    parser.add_argument("--repos", default=None,
                        help="--repos に渡す repos.json パス（既定は設定の root: から推定）")
    parser.add_argument("--base", default=DEFAULT_BASE_PLACEHOLDER,
                        help='--base に渡す値（既定 "$KIRO_BASE_REV"）')
    parser.add_argument("--dry-run", action="store_true", help="書き込まず結果のみ表示する")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    """検出→推奨文字列の生成→yaml 冪等注入を1回の実行で通す（自動配線を経ない唯一の入口）。

    設定ファイルが無いときに空テキストから作らないのは、regression_cmd だけを持つ
    agent-project.yaml は `root:`／`agent_cli:` を欠いて本体が起動できない半端な設定になり、
    しかも「--config のパスを間違えた」という最も多い誤りが黙って新規ファイル生成として
    成功してしまうため——読み手が気づけない失敗を、その場のエラーに倒す。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    path = Path(args.config)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: 設定ファイルがありません: {path}\n"
              f"       agent-project の設定ファイル（既定 .agent/agent-project.yaml）を先に用意し、"
              f"--config でそのパスを指定してください。", file=sys.stderr)
        return EXIT_CONFIG_MISSING
    except OSError as exc:
        print(f"error: 設定ファイルを読めません: {path}: {exc}", file=sys.stderr)
        return EXIT_CONFIG_MISSING

    repos_path = args.repos or infer_default_repos_path(text)

    judgment = probe_wiring(repos_path=repos_path, explicit=args.codd_gate)
    cli_usable = judgment.usable and judgment.capabilities.get("verify", False)
    reason = judgment.status.reason if cli_usable else (
        "verify capability unavailable" if judgment.usable else judgment.status.reason
    )
    cmd = (
        build_regression_cmd(judgment.status, repos_path, base=args.base)
        if cli_usable else None
    )
    intake_cmd = (
        recommend_intake_cmd(repos_path)
        if judgment.usable and judgment.capabilities.get("debt", False) else None
    )
    _, changed = upsert_yaml_text(text, regression_cmd=cmd)
    if changed and not args.dry_run:
        apply_yaml_file(path, regression_cmd=cmd)

    print(json.dumps({
        "usable": cli_usable, "reason": reason,
        "cmd": cmd,  # 後方互換: regression_cmd の旧フィールド名
        "regression_cmd": cmd, "intake_cmd": intake_cmd,
        "changed": changed, "config": str(path), "dry_run": bool(args.dry_run),
    }, ensure_ascii=False))
    if cmd is None:
        print(f"error: codd-gate が使えないため regression_cmd を組み立てられません"
              f"（{reason}）。設定ファイルは変更していません。", file=sys.stderr)
        return EXIT_UNUSABLE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
