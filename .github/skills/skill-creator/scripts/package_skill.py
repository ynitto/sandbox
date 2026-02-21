#!/usr/bin/env python3
"""スキルのパッケージングスクリプト。

スキルフォルダを配布用の .skill ファイル（ZIP形式）にパッケージする。
パッケージ前に自動でバリデーションを実行する。

使い方:
    python package_skill.py <path/to/skill-folder> [output-directory]
"""

import os
import sys
import zipfile

# 同ディレクトリの quick_validate をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quick_validate import validate_skill


def package_skill(skill_path: str, output_dir: str | None = None) -> str | None:
    """スキルをバリデーションしてパッケージする。成功時はファイルパスを返す。"""
    skill_path = os.path.abspath(skill_path)

    if not os.path.isdir(skill_path):
        print(f"エラー: '{skill_path}' はディレクトリではありません")
        return None

    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        print(f"エラー: SKILL.md が見つかりません: {skill_path}")
        return None

    # バリデーション
    print("バリデーション中...")
    errors, warnings = validate_skill(skill_path)
    if warnings:
        for w in warnings:
            print(f"  ⚠ {w}")
    if errors:
        print("バリデーション失敗:")
        for e in errors:
            print(f"  - {e}")
        return None
    print("バリデーション成功" + (" (警告あり)" if warnings else ""))

    # パッケージ
    skill_name = os.path.basename(skill_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = os.getcwd()

    output_file = os.path.join(output_dir, f"{skill_name}.skill")

    print(f"パッケージ中: {skill_name}")
    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(skill_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.join(
                    skill_name, os.path.relpath(file_path, skill_path)
                )
                zf.write(file_path, arcname)

    print(f"パッケージ完了: {output_file}")
    return output_file


def main() -> None:
    if len(sys.argv) < 2:
        print("使い方: python package_skill.py <path/to/skill-folder> [output-directory]")
        sys.exit(1)

    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    result = package_skill(skill_path, output_dir)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
