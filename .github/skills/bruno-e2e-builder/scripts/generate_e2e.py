#!/usr/bin/env python3
"""OpenAPI仕様からBruno E2Eテストファイル（e2e/api/）を生成するスクリプト。

使用方法:
    python generate_e2e.py <openapi_file> [options]

例:
    python generate_e2e.py openapi.yaml
    python generate_e2e.py api/spec.yaml --output-dir ./e2e --env staging --base-url https://api.example.com

生成されるフォルダ構造:
    e2e/api/{tag}/{method}-{path}/
        001. {summary}.bru           # 正常系テスト（シーケンシャル）
        101. error - 400 ....bru     # エラー系テスト（OpenAPIの4xx/5xxから生成）
        102. error - 401 ....bru
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# --- 定数 ---

HTTP_METHODS = ["get", "post", "put", "patch", "delete"]

# --- ユーティリティ ---


def load_openapi(path: str) -> dict:
    """OpenAPI仕様を読み込む（YAML / JSON 対応）。"""
    filepath = Path(path).resolve()
    if not filepath.exists():
        print(f"Error: ファイルが見つかりません: {path}", file=sys.stderr)
        sys.exit(1)
    if filepath.suffix.lower() in (".yaml", ".yml"):
        if not HAS_YAML:
            print(
                "Error: YAMLファイルの解析にPyYAMLが必要です。"
                "  pip install pyyaml",
                file=sys.stderr,
            )
            sys.exit(1)
        content = filepath.read_text(encoding="utf-8")
        return yaml.safe_load(content)
    else:
        content = filepath.read_text(encoding="utf-8")
        return json.loads(content)


def sanitize_filename(name: str) -> str:
    """ファイルシステムで使えない文字を除去する。"""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()


def sanitize_dir_name(name: str) -> str:
    """ディレクトリ名として安全な文字列に変換する（lowercase + アンダースコア）。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]', "_", name)
    return name.strip("_").lower()


def bru_url(path: str) -> str:
    """`{param}` を Bruno の `{{param}}` 記法に変換する。"""
    return re.sub(r"\{(\w+)\}", r"{{\1}}", path)


def _endpoint_folder_name(method: str, path: str) -> str:
    """エンドポイントフォルダ名を生成する: {method}-{sanitized-path}。

    例:
        GET  /users        → get-users
        POST /users        → post-users
        GET  /users/{id}   → get-users-id
        DELETE /users/{id} → delete-users-id
    """
    # {param} からパラメータ名だけ取り出す（ブレース除去）
    clean = re.sub(r"\{([^}]+)\}", r"\1", path)
    # 先頭スラッシュ除去、/ を - に変換、英数字・ハイフン・アンダースコア以外除去
    clean = clean.lstrip("/")
    clean = re.sub(r"[/\s]+", "-", clean)
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", clean)
    clean = clean.strip("-").lower()
    return f"{method.lower()}-{clean}" if clean else method.lower()


# --- スキーマからサンプル値を生成 ---


def _example_value(schema: dict):
    """スキーマ定義から単一サンプル値を返す（再帰対応）。"""
    if "example" in schema:
        return schema["example"]
    t = schema.get("type", "string")
    fmt = schema.get("format", "")
    if t == "string":
        mapping = {
            "date-time": "2024-01-01T00:00:00Z",
            "date": "2024-01-01",
            "email": "user@example.com",
            "uuid": "00000000-0000-0000-0000-000000000000",
            "uri": "https://example.com",
        }
        return mapping.get(fmt, "string")
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return True
    if t == "array":
        items = schema.get("items", {})
        return [_example_value(items)] if items else []
    if t == "object":
        return {k: _example_value(v) for k, v in schema.get("properties", {}).items()}
    return None


def schema_to_example_json(schema: dict) -> str:
    """オブジェクトスキーマからサンプルJSONを生成する。"""
    if schema.get("type") == "object" or "properties" in schema:
        example = {k: _example_value(v) for k, v in schema.get("properties", {}).items()}
    else:
        example = _example_value(schema) or {}
    return json.dumps(example, indent=2, ensure_ascii=False)


# --- BRUファイル生成 ---


def _get_tag_folder(operation: dict, path: str) -> str:
    """オペレーションのタグ or パス先頭セグメントからフォルダ名を決定する。"""
    tags = operation.get("tags", [])
    if tags:
        return sanitize_dir_name(tags[0])
    parts = [p for p in path.split("/") if p and not p.startswith("{")]
    return sanitize_dir_name(parts[0]) if parts else "default"


def _expected_status(responses: dict) -> int:
    """レスポンス定義から期待する成功ステータスコードを返す。"""
    for code in ["200", "201", "204", "202"]:
        if code in responses:
            return int(code)
    success = [int(c) for c in responses if str(c).isdigit() and str(c).startswith("2")]
    return min(success) if success else 200


def _get_error_responses(operation: dict) -> list:
    """OpenAPI operationから4xx/5xxエラーレスポンスの一覧を取得する。

    Returns:
        [(status_code: int, description: str), ...] をステータスコード昇順で返す。
    """
    responses = operation.get("responses", {})
    errors = []
    for code, response in responses.items():
        code_str = str(code)
        if code_str.startswith("4") or code_str.startswith("5"):
            try:
                status_code = int(code_str)
                description = response.get("description", f"Error {code_str}")
                errors.append((status_code, description))
            except ValueError:
                continue  # "default" など非数値キーはスキップ
    return sorted(errors)


def operation_to_bru(method: str, path: str, operation: dict, seq: int = 1) -> str:
    """正常系: 1つのOpenAPI operationをBRU形式のテキストに変換する。"""
    name = operation.get("summary", f"{method.upper()} {path}")
    url = f"{{{{baseUrl}}}}{bru_url(path)}"

    # リクエストボディ
    request_body = operation.get("requestBody", {})
    body_content = request_body.get("content", {})
    has_json_body = "application/json" in body_content and method.lower() in (
        "post",
        "put",
        "patch",
    )

    body_type = "json" if has_json_body else "none"

    # サンプルボディを生成
    example_json = "{}"
    if has_json_body:
        json_schema = body_content["application/json"]
        if "example" in json_schema:
            example_json = json.dumps(json_schema["example"], indent=2, ensure_ascii=False)
        elif "schema" in json_schema:
            example_json = schema_to_example_json(json_schema["schema"])

    # パラメータ分類
    params = operation.get("parameters", [])
    query_params = [p for p in params if p.get("in") == "query"]
    header_params = [p for p in params if p.get("in") == "header"]

    # 期待ステータスコード
    expected = _expected_status(operation.get("responses", {}))

    lines = []

    # meta
    lines += [f"meta {{", f"  name: {name}", f"  type: http", f"  seq: {seq}", f"}}", ""]

    # HTTPメソッドブロック
    lines += [
        f"{method.lower()} {{",
        f"  url: {url}",
        f"  body: {body_type}",
        f"  auth: none",
        f"}}",
        "",
    ]

    # クエリパラメータ
    if query_params:
        lines.append("params:query {")
        for p in query_params:
            required_mark = "" if p.get("required", False) else "~"
            ex = p.get("example", p.get("schema", {}).get("example", ""))
            lines.append(f"  {required_mark}{p['name']}: {ex}")
        lines += ["}", ""]

    # ヘッダー
    lines.append("headers {")
    if has_json_body:
        lines.append("  Content-Type: application/json")
    lines.append("  Authorization: Bearer {{accessToken}}")
    for p in header_params:
        lines.append(f"  {p['name']}: {{{{{p['name']}}}}}")
    lines += ["}", ""]

    # ボディ
    if has_json_body:
        lines.append("body:json {")
        for bline in example_json.splitlines():
            lines.append(f"  {bline}")
        lines += ["}", ""]

    # テスト
    lines += [
        "tests {",
        f'  test("should return {expected}", function() {{',
        f"    expect(res.status).to.equal({expected});",
        "  });",
        "}",
    ]

    return "\n".join(lines) + "\n"


def operation_to_error_bru(
    method: str, path: str, operation: dict, status_code: int, description: str, seq: int
) -> str:
    """エラー系: 特定のHTTPエラーに対応するBRU形式のテキストを生成する。"""
    name = f"error - {status_code} {description}"
    url = f"{{{{baseUrl}}}}{bru_url(path)}"

    has_json_body = method.lower() in ("post", "put", "patch")
    body_type = "json" if has_json_body else "none"

    lines = []

    # meta
    lines += [f"meta {{", f"  name: {name}", f"  type: http", f"  seq: {seq}", f"}}", ""]

    # HTTPメソッドブロック
    lines += [
        f"{method.lower()} {{",
        f"  url: {url}",
        f"  body: {body_type}",
        f"  auth: none",
        f"}}",
        "",
    ]

    # ヘッダー（401の場合はaccessTokenを送らない）
    lines.append("headers {")
    if has_json_body:
        lines.append("  Content-Type: application/json")
    if status_code != 401:
        lines.append("  Authorization: Bearer {{accessToken}}")
    lines += ["}", ""]

    # エラーを引き起こすボディ（TODO付き）
    if has_json_body:
        lines += [
            "body:json {",
            "  {",
            "    // TODO: エラーを引き起こすリクエストボディを記述",
            "  }",
            "}",
            "",
        ]

    # テスト
    lines += [
        "tests {",
        f'  test("should return {status_code}", function() {{',
        f"    expect(res.status).to.equal({status_code});",
        "  });",
        "}",
    ]

    return "\n".join(lines) + "\n"


# --- ファイル生成 ---


def generate_collection_config(spec: dict, output_dir: Path) -> None:
    """bruno.json を生成する。"""
    name = spec.get("info", {}).get("title", "API E2E Tests")
    config = {"version": "1", "name": name, "type": "collection"}
    path = output_dir / "bruno.json"
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _log_created(path, output_dir.parent)


def generate_environment(env_name: str, base_url: str, output_dir: Path) -> None:
    """environments/{env_name}.bru を生成する。"""
    env_dir = output_dir / "environments"
    env_dir.mkdir(parents=True, exist_ok=True)
    content = f"vars {{\n  baseUrl: {base_url}\n}}\n\nvars:secret [\n  accessToken\n]\n"
    path = env_dir / f"{env_name}.bru"
    path.write_text(content, encoding="utf-8")
    _log_created(path, output_dir.parent)


def generate_api_tests(spec: dict, output_dir: Path) -> None:
    """e2e/api/{tag}/{endpoint}/ 配下にAPIテストを生成する。

    フォルダ構造:
        api/{tag}/{method}-{path}/
            001. {summary}.bru           # 正常系（シーケンシャル）
            101. error - {status} ....bru # エラー系（OpenAPIの4xx/5xxから生成）
            102. error - {status} ....bru
    """
    api_dir = output_dir / "api"
    paths = spec.get("paths", {})

    # タグごとにオペレーションをグループ化
    tag_groups: dict = {}
    for path, path_item in paths.items():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not operation:
                continue
            tag = _get_tag_folder(operation, path)
            tag_groups.setdefault(tag, []).append((path, method, operation))

    for tag, operations in sorted(tag_groups.items()):
        tag_dir = api_dir / tag

        for path, method, operation in operations:
            # エンドポイント毎のフォルダを作成
            endpoint_folder = _endpoint_folder_name(method, path)
            endpoint_dir = tag_dir / endpoint_folder
            endpoint_dir.mkdir(parents=True, exist_ok=True)

            # 正常系テスト (001.)
            name = operation.get("summary", f"{method.upper()} {path}")
            filename = f"001. {sanitize_filename(name)}.bru"
            filepath = endpoint_dir / filename
            filepath.write_text(operation_to_bru(method, path, operation, seq=1), encoding="utf-8")
            _log_created(filepath, output_dir.parent)

            # エラー系テスト (101., 102., ...)
            error_responses = _get_error_responses(operation)
            for error_idx, (status_code, description) in enumerate(error_responses, start=1):
                seq = 100 + error_idx
                error_name = f"error - {status_code} {sanitize_filename(description)}"
                error_filename = f"{seq:03d}. {error_name}.bru"
                error_filepath = endpoint_dir / error_filename
                content = operation_to_error_bru(
                    method, path, operation, status_code, description, seq
                )
                error_filepath.write_text(content, encoding="utf-8")
                _log_created(error_filepath, output_dir.parent)


def _log_created(path: Path, base: Path) -> None:
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = path
    print(f"  Created: {rel}")


# --- エントリポイント ---


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenAPI仕様からBruno E2Eテストファイルを生成します",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("openapi_file", help="OpenAPI仕様ファイルのパス（YAML / JSON）")
    parser.add_argument(
        "--output-dir", default="./e2e", help="出力先ディレクトリ（デフォルト: ./e2e）"
    )
    parser.add_argument("--env", default="local", help="環境ファイル名（デフォルト: local）")
    parser.add_argument(
        "--base-url",
        default="",
        help="ベースURL（省略時はOpenAPIのservers[0]を使用、それも無ければ http://localhost:8080）",
    )
    args = parser.parse_args()

    # 出力先をCWD配下に制限（パストラバーサル防止）
    output_dir = Path(args.output_dir).resolve()
    cwd = Path.cwd().resolve()
    if not str(output_dir).startswith(str(cwd)):
        print(
            f"Error: --output-dir はカレントディレクトリ ({cwd}) 配下のパスを指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n📂 Loading: {args.openapi_file}")
    spec = load_openapi(args.openapi_file)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ベースURLの決定
    base_url = args.base_url
    if not base_url:
        servers = spec.get("servers", [])
        base_url = servers[0].get("url", "http://localhost:8080") if servers else "http://localhost:8080"

    print("\n🔧 Setting up Bruno collection...")
    generate_collection_config(spec, output_dir)
    generate_environment(args.env, base_url, output_dir)

    print("\n🔨 Generating API tests (e2e/api/)...")
    generate_api_tests(spec, output_dir)

    print(
        f"\n✅ Done!\n"
        f"  環境変数を設定: {output_dir}/environments/{args.env}.bru\n"
        f"  テスト実行: bru run {args.output_dir}/api --env {args.env} --recursive\n"
    )


if __name__ == "__main__":
    main()
