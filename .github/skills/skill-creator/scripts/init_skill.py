#!/usr/bin/env python3
"""スキルディレクトリの初期化スクリプト。

テンプレート構造を持つ新しいスキルディレクトリを生成する。

使い方:
    python init_skill.py <skill-name> --path <output-directory>

例:
    python init_skill.py my-skill --path .github/skills
"""

import argparse
import os
import sys


def init_skill(name: str, base_path: str) -> None:
    skill_dir = os.path.join(base_path, name)

    if os.path.exists(skill_dir):
        print(f"エラー: '{skill_dir}' は既に存在します")
        sys.exit(1)

    # ディレクトリ作成
    for sub in ["scripts", "references", "assets"]:
        os.makedirs(os.path.join(skill_dir, sub), exist_ok=True)

    # SKILL.md テンプレート
    skill_md = f"""---
name: {name}
description: TODO - このスキルの説明を記述する。何をするか＋いつ使うかを含める。
---

# {name}

TODO - スキルの概要を記述する。

## ワークフロー

TODO - スキルの主要な手順やワークフローを記述する。
構造の選択肢:
- ワークフロー型: 順次ステップで導く
- タスク型: 独立したタスクごとに整理
- リファレンス型: ガイドラインや制約を中心に構成
- 機能型: 提供する機能ごとに整理

## リソース

- **scripts/**: 実行可能コード（決定論的な信頼性が必要な処理向け）
- **references/**: 必要時に読み込むドキュメント（スキーマ、API仕様等）
- **assets/**: 出力に使用するファイル（テンプレート、画像等）

不要なディレクトリは削除する。

## 実行後フィードバック（必須）

スキルの手順を全て完了したら、ユーザーに確認する:

「{name} の実行はいかがでしたか？
 1. 問題なかった (ok)
 2. 改善点がある (needs-improvement)
 3. うまくいかなかった (broken)」

回答に応じて以下を実行する（git-skill-manager がない環境ではスキップ）:
```
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'{name}','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"
```

スクリプトの出力に「💡 新しいスキル候補を発見できるかもしれません」が含まれる場合は、
ユーザーに `git-skill-manager discover` の実行を提案する。
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md)

    # サンプルファイル
    with open(
        os.path.join(skill_dir, "scripts", "example.py"), "w", encoding="utf-8"
    ) as f:
        f.write(
            '#!/usr/bin/env python3\n"""サンプルスクリプト。不要なら削除する。"""\n\nprint("Hello from script")\n'
        )

    with open(
        os.path.join(skill_dir, "references", "example.md"), "w", encoding="utf-8"
    ) as f:
        f.write(
            "# サンプルリファレンス\n\n必要時にコンテキストに読み込むドキュメント。不要なら削除する。\n"
        )

    with open(
        os.path.join(skill_dir, "assets", "README.txt"), "w", encoding="utf-8"
    ) as f:
        f.write(
            "出力に使用するファイル（テンプレート、画像等）を配置する。\nこのファイルは不要なら削除する。\n"
        )

    print(f"スキル '{name}' を作成しました: {skill_dir}")
    print(f"  {skill_dir}/SKILL.md")
    print(f"  {skill_dir}/scripts/example.py")
    print(f"  {skill_dir}/references/example.md")
    print(f"  {skill_dir}/assets/README.txt")
    print()
    print("次のステップ:")
    print("  1. SKILL.md の TODO を埋める")
    print("  2. 必要なリソースを scripts/, references/, assets/ に追加する")
    print("  3. 不要なサンプルファイルを削除する")


def main() -> None:
    parser = argparse.ArgumentParser(description="新しいスキルを初期化する")
    parser.add_argument("name", help="スキル名（kebab-case）")
    parser.add_argument("--path", required=True, help="出力先ディレクトリ")
    args = parser.parse_args()

    # 名前のバリデーション
    import re

    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", args.name):
        print(
            "エラー: スキル名はkebab-case（小文字・数字・ハイフン）で指定してください"
        )
        sys.exit(1)

    if len(args.name) > 64:
        print("エラー: スキル名は64文字以内にしてください")
        sys.exit(1)

    init_skill(args.name, args.path)


if __name__ == "__main__":
    main()
