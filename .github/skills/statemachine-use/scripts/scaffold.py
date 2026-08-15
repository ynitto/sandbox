#!/usr/bin/env python3
"""
scaffold.py — ステートマシン定義の骨組み生成

状態列の宣言から workflow.yaml と actions/*.md スタブを生成する。作成モードの
手作業（フォルダとファイルを手で書く）を置き換え、新スキーマの口
（output_validator / check / check_retries / check_on_exhausted / write）を
コメント付きで骨組みに含める。生成直後に engine の検証を通し、通らない骨組みは残さない。

使い方:
  python scripts/scaffold.py 名前 --state implement --state review
  python scripts/scaffold.py 名前 --state "implement:仕様どおりに実装する" --terminal "done:完了"
  python scripts/scaffold.py 名前 --state fetch --dir /path/to/.statemachine --force

- `--state ID[:説明]` を並べた順が実行順になる。遷移は直列
  （第 1 行 OK → 次ステート）で生成し、分岐はあとから transitions を編集して足す。
- 終端ステートは `--terminal ID[:説明]`（省略時は complete を自動で足す）。
- check を宣言したステートからの遷移は `condition_rule: "equals:check_ok:true"` へ
  置き換える（モデルの自己申告ではなくハーネスが測った事実で進む）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# スクリプト単体実行 / スキルルートからの両方に対応
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.engine import load_workflow, validate_workflow  # noqa: E402

_STATE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def parse_state_arg(value: str) -> tuple[str, str]:
    """`ID[:説明]` を (id, 説明) へ分解する。説明省略時は ID を説明に使う。"""
    state_id, _, desc = value.partition(":")
    state_id = state_id.strip()
    if not _STATE_ID_RE.match(state_id):
        raise ValueError(
            f"ステートID が不正です: {state_id!r}（英字始まり・英数字とアンダースコアのみ）")
    return state_id, (desc.strip() or state_id)


def build_workflow_yaml(name: str, states: list[tuple[str, str]],
                        terminal: tuple[str, str]) -> str:
    lines: list[str] = [
        f'name: "{name}"',
        f"initial_state: {states[0][0]}",
        "",
        "config:",
        "  max_steps: 30",
        "  # 条件が一致しないときは止まる（既定）。フォールバック先が要るなら state_id を書く。",
        "  on_no_transition: error",
        "",
        "states:",
    ]
    for state_id, desc in states:
        lines += [
            f"  {state_id}:",
            f'    description: "{desc}"',
            f"    action_file: actions/{state_id}.md",
            "    # 書式の契約（モデルが書く第 1 行）。",
            '    output_validator: "startswith:OK,FAILED"',
            "    max_retries: 1",
            "    # 事実の契約（ハーネスが実行する検査コマンド）。このステートの成果を",
            "    # どのコマンドの終了コードで測るかを決めて宣言する（SKILL.md ステップ2）。",
            "    # 決められないなら、まだ割り方が正しくない。シェルは介さない——",
            "    # パイプやリダイレクトが要るならスクリプトにまとめる。",
            '    # check: "python3 -m pytest tests/test_x.py -q"',
            "    # check_retries: 2",
            "    # check_on_exhausted: escalate   # escalate（既定・上位の段へ回す）| continue | error",
            "    # 編集対象の割付。宣言すると headless 実行は制御周を挟まず編集 CLI へ直行する。",
            "    # write: src/target.py",
            "",
        ]
    terminal_id, terminal_desc = terminal
    lines += [
        f"  {terminal_id}:",
        f'    description: "{terminal_desc}"',
        "    terminal: true",
        "",
        "transitions:",
        "  # 直列の骨組み。check を宣言したステートからの遷移は",
        '  # condition_rule: "equals:check_ok:true" へ置き換える（測った事実で進む）。',
    ]
    chain = [state_id for state_id, _ in states] + [terminal_id]
    for src, dst in zip(chain, chain[1:]):
        lines += [
            f"  - from: {src}",
            f"    to: {dst}",
            '    condition_rule: "startswith:last_output:OK"',
            "    priority: 1",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def build_action_stub(state_id: str, desc: str) -> str:
    return f"""## [{state_id}: {desc}]

（ここに具体的な指示を書く。スキルへ移譲するときは `skill-name` スキル と明記する）

**入力:** {{{{input}}}}
**前のステートの出力:** {{{{last_output}}}}

**出力形式:** 第1行に OK（完了）または FAILED（できなかった）だけを返してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
"""


def scaffold(name: str, state_args: list[str], terminal_arg: str,
             base_dir: Path, force: bool) -> Path:
    """骨組みを生成して検証する。戻り値は生成したフォルダ。失敗は ValueError。"""
    states = [parse_state_arg(v) for v in state_args]
    seen: set[str] = set()
    terminal = parse_state_arg(terminal_arg)
    for state_id, _ in [*states, terminal]:
        if state_id in seen:
            raise ValueError(f"ステートID が重複しています: {state_id}")
        seen.add(state_id)

    target = base_dir / name
    workflow_path = target / "workflow.yaml"
    if workflow_path.exists() and not force:
        raise ValueError(f"既に存在します: {workflow_path}（上書きするなら --force）")

    (target / "actions").mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(build_workflow_yaml(name, states, terminal), encoding="utf-8")
    for state_id, desc in states:
        (target / "actions" / f"{state_id}.md").write_text(
            build_action_stub(state_id, desc), encoding="utf-8")

    # 生成直後に必ず検証する。通らない骨組みを残すと「作ったのに動かない」が
    # 利用者側で発覚する——生成側の責任で投入前に落とす。
    errors = validate_workflow(load_workflow(workflow_path))
    if errors:
        raise ValueError("生成した定義が検証を通りません:\n" + "\n".join(f"  ✗ {e}" for e in errors))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="ステートマシン定義の骨組みを生成する")
    parser.add_argument("name", help="ワークフロー名（.statemachine/{名前}/ に生成）")
    parser.add_argument("--state", action="append", default=[], metavar="ID[:説明]",
                        help="ステート（繰り返し指定。並べた順が実行順）")
    parser.add_argument("--terminal", default="complete:完了", metavar="ID[:説明]",
                        help="終端ステート（既定: complete）")
    parser.add_argument("--dir", default=".statemachine",
                        help="生成先の親フォルダ（既定: .statemachine）")
    parser.add_argument("--force", action="store_true", help="既存の workflow.yaml を上書きする")
    args = parser.parse_args()

    if not args.state:
        parser.error("--state を 1 つ以上指定してください")

    try:
        target = scaffold(args.name, args.state, args.terminal, Path(args.dir), args.force)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ 生成して検証しました: {target}/")
    for p in sorted(target.rglob("*")):
        if p.is_file():
            print(f"    {p.relative_to(target)}")
    print("次にやること:")
    print("  1. actions/*.md の指示本文を埋める")
    print("  2. 各ステートの check / write のコメントを外して実値にする（成果を測るコマンドが正）")
    print("  3. 分岐が要るなら transitions を編集する（仕様は references/schema.md）")


if __name__ == "__main__":
    main()
