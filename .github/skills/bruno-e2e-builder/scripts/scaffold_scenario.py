#!/usr/bin/env python3
"""シナリオテスト用のBrunoスキャフォールドを生成するスクリプト。

使用方法:
    python scaffold_scenario.py --name <scenario-name> --step "<type> <name> <METHOD> <path>" ...

ステップ形式:
    "<type> <name> <METHOD> <path>"

    type  : setup / main / verify / teardown（ファイル名プレフィックスに使用）
    name  : スラッグ形式の名前（スペース不可 → ハイフン推奨）
    METHOD: GET / POST / PUT / PATCH / DELETE
    path  : APIパス（例: /users/{id}）

例:
    python scaffold_scenario.py \\
      --name user-registration \\
      --output-dir ./e2e/scenario \\
      --step "setup  create-test-data   POST   /users" \\
      --step "main   register-user      POST   /auth/register" \\
      --step "verify get-profile        GET    /users/{id}" \\
      --step "teardown delete-user      DELETE /users/{id}"
"""

import argparse
import json
import re
import sys
from pathlib import Path

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

_TYPE_PREFIX = {
    "setup": "setup - ",
    "teardown": "teardown - ",
    "main": "",
    "verify": "verify - ",
}


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()


def bru_url(path: str) -> str:
    return re.sub(r"\{(\w+)\}", r"{{\1}}", path)


def parse_step(raw: str) -> dict:
    """ステップ文字列をパースする。形式: '<type> <name> <METHOD> <path>'"""
    parts = raw.split()
    if len(parts) < 4:
        raise ValueError(
            f"ステップの形式が不正です: '{raw}'\n"
            "正しい形式: \"<type> <name> <METHOD> <path>\""
        )
    step_type = parts[0].lower()
    method = parts[-2].upper()
    path = parts[-1]
    name = " ".join(parts[1:-2])

    if step_type not in _TYPE_PREFIX:
        raise ValueError(
            f"不正なステップタイプ: '{step_type}'\n"
            f"使用可能なタイプ: {list(_TYPE_PREFIX.keys())}"
        )
    if method not in HTTP_METHODS:
        raise ValueError(
            f"不正なHTTPメソッド: '{method}'\n"
            f"使用可能なメソッド: {sorted(HTTP_METHODS)}"
        )
    return {"type": step_type, "name": name, "method": method, "path": path}


def make_bru_content(seq: int, step: dict) -> str:
    """ステップ定義からBRUファイルの内容を生成する。"""
    prefix = _TYPE_PREFIX[step["type"]]
    display_name = f"{prefix}{step['name']}"
    method = step["method"].lower()
    url = f"{{{{baseUrl}}}}{bru_url(step['path'])}"

    has_body = method in ("post", "put", "patch")
    body_type = "json" if has_body else "none"

    lines = [
        "meta {",
        f"  name: {display_name}",
        "  type: http",
        f"  seq: {seq}",
        "}",
        "",
        f"{method} {{",
        f"  url: {url}",
        f"  body: {body_type}",
        "  auth: none",
        "}",
        "",
        "headers {",
    ]
    if has_body:
        lines.append("  Content-Type: application/json")
    lines += [
        "  Authorization: Bearer {{accessToken}}",
        "}",
        "",
    ]

    if has_body:
        lines += [
            "body:json {",
            "  {",
            '    // TODO: リクエストボディを入力してください',
            "  }",
            "}",
            "",
        ]

    # setup / main / verify には変数引き渡しのサンプルを追加
    if step["type"] in ("setup", "main"):
        lines += [
            "script:post-response {",
            "  // TODO: 後続ステップへ変数を渡す場合はここに記述",
            "  // 例: bru.setVar(\"resourceId\", res.body.id);",
            "}",
            "",
        ]

    lines += [
        "tests {",
        f'  test("should succeed", function() {{',
        "    // TODO: 期待するステータスコードに修正",
        "    expect(res.status).to.be.oneOf([200, 201, 204]);",
        "  });",
        "}",
    ]

    return "\n".join(lines) + "\n"


def generate_scenario(
    name: str, steps: list[dict], output_dir: Path
) -> None:
    # 出力先パスの安全性確認
    scenario_dir = (output_dir / name).resolve()
    safe_base = output_dir.resolve()
    if not str(scenario_dir).startswith(str(safe_base)):
        print(
            f"Error: シナリオ名にパストラバーサルが含まれています: {name}",
            file=sys.stderr,
        )
        sys.exit(1)

    scenario_dir.mkdir(parents=True, exist_ok=True)

    for idx, step in enumerate(steps, start=1):
        prefix = _TYPE_PREFIX[step["type"]]
        file_title = f"{prefix}{step['name']}"
        filename = f"{idx:03d}. {sanitize_filename(file_title)}.bru"
        filepath = scenario_dir / filename
        filepath.write_text(make_bru_content(idx, step), encoding="utf-8")
        try:
            rel = filepath.relative_to(output_dir.parent.parent)
        except ValueError:
            rel = filepath
        print(f"  Created: {rel}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bruno シナリオテストのスキャフォールドを生成します",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--name", required=True, help="シナリオ名（フォルダ名になります）")
    parser.add_argument(
        "--output-dir",
        default="./e2e/scenario",
        help="出力先ディレクトリ（デフォルト: ./e2e/scenario）",
    )
    parser.add_argument(
        "--step",
        action="append",
        dest="steps",
        metavar='"<type> <name> <METHOD> <path>"',
        required=True,
        help=(
            "ステップ定義（複数指定可）。"
            '形式: "<type> <name> <METHOD> <path>" '
            "例: \"setup create-user POST /users\""
        ),
    )
    args = parser.parse_args()

    # 出力先をCWD配下に制限
    output_dir = Path(args.output_dir).resolve()
    cwd = Path.cwd().resolve()
    if not str(output_dir).startswith(str(cwd)):
        print(
            f"Error: --output-dir はカレントディレクトリ ({cwd}) 配下のパスを指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    # ステップのパース
    steps = []
    for raw in args.steps:
        try:
            steps.append(parse_step(raw))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"\n🔨 Generating scenario: {args.name}")
    generate_scenario(args.name, steps, output_dir)
    print(
        f"\n✅ Done!\n"
        f"  テスト実行: bru run {args.output_dir}/{args.name} --env local\n"
    )


if __name__ == "__main__":
    main()
