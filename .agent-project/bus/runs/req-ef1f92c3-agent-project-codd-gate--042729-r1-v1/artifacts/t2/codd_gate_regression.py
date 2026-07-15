#!/usr/bin/env python3
"""codd_gate_regression — regression_cmd の生成と .agent/agent-project.yaml への冪等注入
（tools/agent-project 配下）。

README.md「一貫性ゲート（codd-gate 連携・オプション）」節の規約どおり、有効化は
`regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'` を
agent-project.yaml に書くだけで足りる（実行時の自動配線は不要な設計）。本モジュールはこの
1行を人手のコピペではなく検出結果（codd_gate_status.detect_status、a4）駆動で組み立て、
既存ファイルへ冪等に upsert する。

責務は2つ:
  - build_regression_cmd: CoddGateStatus と --repos（codd_gate_routing.resolve_repos_arg、b2）
    を合成し regression_cmd の値そのものを組み立てる。status.usable が False（未検出・バージョン
    不適合・schema 不適合のいずれか）なら None を返し、壊れたコマンドを書き込まない
    （他 codd_gate_* モジュールと同じ no-op 縮退）。
  - upsert_config_text / apply_to_file: 得た値を agent-project.yaml の生テキストへ冪等に
    upsert する。PyYAML の load→dump を使わないのは、既存ファイルが人手のコメント
    （「# 一貫性ゲート（codd-gate 連携）」ブロック等）を持ち、ラウンドトリップで失われると
    ドキュメントとしての価値が失われるため——正規表現ベースの最小差分の行編集に留める。

このモジュールが意図的に含めないもの（他タスクの責務）:
  - repos.json 自体の生成（agent-project.py 本体・charter からの自動生成）
  - intake_cmd の生成・注入（対称の関数だが対象キーが異なる。intake/enqueue 結線の担当タスク）
  - cfg.regression_cmd の実行時自動配線（README.md の「有効化は設定だけ」方針により、
    静的な設定ファイル注入のみで完結させる）

依存は標準ライブラリと同梱の codd_gate_status／codd_gate_routing のみ。

CLI:
    python3 codd_gate_regression.py --config .agent/agent-project.yaml [--repos <path>] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from codd_gate_detect import BINARY_NAME
from codd_gate_routing import resolve_repos_arg
from codd_gate_status import CoddGateStatus, detect_status

KEY = "regression_cmd"
DEFAULT_BASE_PLACEHOLDER = '"$KIRO_BASE_REV"'
DEFAULT_REPOS_PATH = ".agent-project/repos.json"
# 新規キー挿入位置の探索順（README.md 記載順=regression_cmd→intake_cmd に揃える）。
# intake_cmd が既にあれば同じ「一貫性ゲート」ブロックの一員として直前に差し込み、
# 無ければ「グローバル既定」ブロック（agent_cli:）の直前に新規ブロックとして立てる。
_ANCHOR_KEYS = ("intake_cmd", "agent_cli")
_HEADER_COMMENT = (
    "# 一貫性ゲート（codd-gate 連携）: done 確定前の差分ゲート（regression）と\n"
    "# 負債の修復タスク自動投入（intake）。repos.json は agent-project が charter から\n"
    "# <root>/repos.json に自動生成する（tools/agent-project/README.md 参照）。\n"
)
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
    return " ".join([BINARY_NAME, "verify", "--base", base, "--repos", resolve_repos_arg(repos_path)])


def _yaml_single_quote(value: str) -> str:
    """YAML single-quoted scalar へのエスケープ（`'` の二重化のみで仕様上足りる）。"""
    return "'" + value.replace("'", "''") + "'"


def render_line(cmd: str, key: str = KEY) -> str:
    return f"{key}: {_yaml_single_quote(cmd)}"


def _key_pattern(key: str) -> "re.Pattern[str]":
    return re.compile(rf"^[ \t]*{re.escape(key)}:.*$", re.MULTILINE)


def upsert_config_text(text: str, cmd: "str | None", key: str = KEY) -> "tuple[str, bool]":
    """`text`（agent-project.yaml の生テキスト）へ `key: cmd` を冪等に upsert する。

    cmd が None（codd-gate 未検出・非互換）のときは既存内容を一切変更しない——「わからない・
    使えない」を「削除」に倒すと、人が意図して書いた regression_cmd（codd-gate 以外のコマンド
    も含む）を壊してしまうため（他 codd_gate_* モジュールと同じ no-op 縮退の方針をファイル編集
    にも適用する）。

    戻り値の2要素目は実際に内容が変わったかどうか。変わらなければ呼び出し側は書き込みを省略
    でき、mtime を無用に更新しない＝再実行しても diff が出ない冪等性が成り立つ。
    """
    if cmd is None:
        return text, False
    new_line = render_line(cmd, key)
    pattern = _key_pattern(key)
    m = pattern.search(text)
    if m:
        if m.group(0) == new_line:
            return text, False
        return pattern.sub(lambda _mo: new_line, text, count=1), True
    return _insert_new_line(text, new_line), True


def _insert_new_line(text: str, new_line: str) -> str:
    """新規キーをスキーマ上の既定位置へ挿入する（挿入位置の担保）。

    1. `intake_cmd:` が既にあれば、その直前（同じ「一貫性ゲート」ブロックの一員として）。
    2. 無ければ `agent_cli:`（グローバル既定ブロックの先頭）の直前へ、見出しコメント付きの
       新規ブロックとして。
    3. どちらも無ければファイル末尾へ見出しコメント付きで追記する。
    """
    for anchor, with_header in ((_ANCHOR_KEYS[0], False), (_ANCHOR_KEYS[1], True)):
        m = _key_pattern(anchor).search(text)
        if not m:
            continue
        insert_at = text.rfind("\n", 0, m.start()) + 1
        block = (_HEADER_COMMENT + new_line + "\n\n") if with_header else (new_line + "\n")
        return text[:insert_at] + block + text[insert_at:]
    sep = "" if (not text or text.endswith("\n")) else "\n"
    return text + sep + "\n" + _HEADER_COMMENT + new_line + "\n"


def infer_default_repos_path(text: str) -> str:
    """設定ファイルの `root:` から `<root>/repos.json` を推定する（README.md の規約どおり）。
    `root:` が見つからなければ既定 `.agent-project/repos.json` にフォールバックする。"""
    m = _ROOT_RE.search(text)
    if not m:
        return DEFAULT_REPOS_PATH
    root = m.group(1).strip().strip("'\"")
    return f"{root.rstrip('/')}/repos.json"


def apply_to_file(yaml_path: "str | Path", cmd: "str | None", key: str = KEY) -> bool:
    """ファイルへ実際に反映する。変更が無ければ書き込み自体を省略する（idempotent no-op）。"""
    path = Path(yaml_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    new_text, changed = upsert_config_text(text, cmd, key)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    return changed


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="codd-gate 検出結果から regression_cmd を生成し "
                                                  "agent-project.yaml へ冪等注入する")
    parser.add_argument("--config", default=".agent/agent-project.yaml",
                         help="注入先の agent-project.yaml（既定 .agent/agent-project.yaml）")
    parser.add_argument("--codd-gate", dest="codd_gate", default=None,
                         help="codd-gate の実体を明示指定（既定は PATH→同梱パスの順で自動解決）")
    parser.add_argument("--repos", default=None,
                         help="--repos に渡す repos.json パス（既定は設定の root: から推定）")
    parser.add_argument("--base", default=DEFAULT_BASE_PLACEHOLDER,
                         help='--base に渡す値（既定 "$KIRO_BASE_REV"）')
    parser.add_argument("--dry-run", action="store_true", help="書き込まず結果のみ表示する")
    args = parser.parse_args(argv)

    path = Path(args.config)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    repos_path = args.repos or infer_default_repos_path(text)

    status = detect_status(args.codd_gate)
    cmd = build_regression_cmd(status, repos_path, base=args.base)
    new_text, changed = upsert_config_text(text, cmd)
    if changed and not args.dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")

    print(json.dumps({
        "usable": status.usable, "reason": status.reason, "cmd": cmd,
        "changed": changed, "config": str(path), "dry_run": bool(args.dry_run),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
