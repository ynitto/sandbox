#!/usr/bin/env python3
"""既存のエンドポイントフォルダに足りないテストを追加するスクリプト。

使用方法:
    python add_test.py --dir <endpoint-dir> --type normal|error \\
                       --name <name> --method <METHOD> --path <path> [--status <code>]

テストの種類:
    normal  : 正常系（001-099の範囲。シーケンシャルに実行される）
    error   : エラー系（101-199の範囲。独立した単発テスト）

例:
    # 正常系を追加（オプションフィールドを含むバリエーション）
    python add_test.py \\
      --dir e2e/api/users/post-users \\
      --type normal \\
      --name "Create User - with optional fields" \\
      --method POST --path /users --status 201

    # エラー系を追加
    python add_test.py \\
      --dir e2e/api/users/post-users \\
      --type error \\
      --name "403 Forbidden" \\
      --method POST --path /users --status 403

    # シナリオフォルダにステップを追加（--type scenario で 001〜 から採番）
    python add_test.py \\
      --dir e2e/scenario/user-registration \\
      --type scenario \\
      --name "verify - get profile" \\
      --method GET --path /users/{id} --status 200
"""

import argparse
import re
import sys
from pathlib import Path

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# シーケンス番号の範囲定義
SEQ_RANGES = {
    "normal": (1, 99),
    "error": (101, 199),
    "scenario": (1, 999),
}


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()


def bru_url(path: str) -> str:
    return re.sub(r"\{(\w+)\}", r"{{\1}}", path)


def _next_seq(directory: Path, test_type: str) -> int:
    """次のシーケンス番号を決定する。

    test_type に応じた範囲内で既存ファイルを走査し、空いている最小番号を返す。
    """
    start, end = SEQ_RANGES[test_type]
    existing: set = set()
    for f in directory.glob("*.bru"):
        m = re.match(r"^(\d+)\.", f.name)
        if m:
            n = int(m.group(1))
            if start <= n <= end:
                existing.add(n)

    for seq in range(start, end + 1):
        if seq not in existing:
            return seq

    raise RuntimeError(
        f"シーケンス番号が上限に達しています（範囲: {start}-{end}）。"
        "既存ファイルを整理してから再試行してください。"
    )


def _make_display_name(name: str, test_type: str) -> str:
    """表示名を正規化する。"""
    if test_type == "error":
        if not re.match(r"^error\s*[-–]", name, re.IGNORECASE):
            return f"error - {name}"
    return name


def make_bru_content(
    seq: int, display_name: str, method: str, path: str, status: int, test_type: str
) -> str:
    """BRUファイルのコンテンツを生成する。"""
    method_lower = method.lower()
    url = f"{{{{baseUrl}}}}{bru_url(path)}"
    has_body = method_lower in ("post", "put", "patch")
    body_type = "json" if has_body else "none"

    lines = [
        "meta {",
        f"  name: {display_name}",
        "  type: http",
        f"  seq: {seq}",
        "}",
        "",
        f"{method_lower} {{",
        f"  url: {url}",
        f"  body: {body_type}",
        "  auth: none",
        "}",
        "",
        "headers {",
    ]
    if has_body:
        lines.append("  Content-Type: application/json")
    # 401エラーテストはAuthorizationヘッダーを送らない
    if not (test_type == "error" and status == 401):
        lines.append("  Authorization: Bearer {{accessToken}}")
    lines += ["}", ""]

    if has_body:
        if test_type == "error":
            lines += [
                "body:json {",
                "  {",
                "    // TODO: エラーを引き起こすリクエストボディを記述",
                "  }",
                "}",
                "",
            ]
        else:
            lines += [
                "body:json {",
                "  {",
                "    // TODO: リクエストボディを入力してください",
                "  }",
                "}",
                "",
            ]

    # 正常系・シナリオには変数引き渡しのサンプルを追加
    if test_type in ("normal", "scenario"):
        lines += [
            "script:post-response {",
            "  // TODO: 後続ステップへ変数を渡す場合はここに記述",
            '  // 例: bru.setVar("resourceId", res.body.id);',
            "}",
            "",
        ]

    lines += [
        "tests {",
        f'  test("should return {status}", function() {{',
        f"    expect(res.status).to.equal({status});",
        "  });",
        "}",
    ]

    return "\n".join(lines) + "\n"


def _default_status(method: str, test_type: str) -> int:
    """テスト種別とHTTPメソッドからデフォルトのステータスコードを返す。"""
    if test_type == "error":
        return 400
    method_upper = method.upper()
    return {"POST": 201, "DELETE": 204}.get(method_upper, 200)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="既存のエンドポイントフォルダにBrunoテストを追加します",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="追加先のフォルダパス（例: e2e/api/users/post-users）",
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=["normal", "error", "scenario"],
        help="テストの種類: normal（正常系）/ error（エラー系）/ scenario（シナリオステップ）",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="テスト名（ファイル名と meta.name に使用）",
    )
    parser.add_argument(
        "--method",
        required=True,
        help="HTTPメソッド（GET / POST / PUT / PATCH / DELETE）",
    )
    parser.add_argument(
        "--path",
        required=True,
        help="APIパス（例: /users/{id}）",
    )
    parser.add_argument(
        "--status",
        type=int,
        default=None,
        help="期待するHTTPステータスコード（省略時: 正常系=200/201/204、エラー系=400）",
    )
    args = parser.parse_args()

    method = args.method.upper()
    if method not in HTTP_METHODS:
        print(
            f"Error: 不正なHTTPメソッド: '{args.method}'\n"
            f"使用可能なメソッド: {sorted(HTTP_METHODS)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # デフォルトステータス
    status = args.status if args.status is not None else _default_status(method, args.type)

    # 出力先の検証（CWD配下のみ許可）
    endpoint_dir = Path(args.dir).resolve()
    cwd = Path.cwd().resolve()
    if not str(endpoint_dir).startswith(str(cwd)):
        print(
            f"Error: --dir はカレントディレクトリ ({cwd}) 配下のパスを指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    if not endpoint_dir.exists():
        print(
            f"Error: ディレクトリが見つかりません: {args.dir}\n"
            "先に generate_e2e.py でベーステストを生成してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    if not endpoint_dir.is_dir():
        print(f"Error: ディレクトリではありません: {args.dir}", file=sys.stderr)
        sys.exit(1)

    # シーケンス番号を決定
    try:
        seq = _next_seq(endpoint_dir, args.type)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # 表示名・ファイル名を決定
    display_name = _make_display_name(args.name, args.type)
    filename = f"{seq:03d}. {sanitize_filename(display_name)}.bru"
    filepath = endpoint_dir / filename

    if filepath.exists():
        print(f"Error: ファイルが既に存在します: {filepath}", file=sys.stderr)
        sys.exit(1)

    # コンテンツ生成・書き込み
    content = make_bru_content(seq, display_name, method, args.path, status, args.type)
    filepath.write_text(content, encoding="utf-8")

    try:
        rel = filepath.relative_to(cwd)
    except ValueError:
        rel = filepath

    print(f"\n✅ Created: {rel}")
    print(f"  テスト実行: bru run \"{filepath}\" --env local\n")


if __name__ == "__main__":
    main()
