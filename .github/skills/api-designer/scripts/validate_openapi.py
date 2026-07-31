#!/usr/bin/env python3
"""
validate_openapi.py - OpenAPI 3.x スキーマのバリデーション

api-designer が生成した OpenAPI 3.0/3.1 YAML または JSON ファイルを検証する。
PyYAML が利用可能な場合は YAML を解析し、なければ JSON のみを処理する。
外部のバリデーションライブラリ（openapi-spec-validator 等）は任意インポート。

使い方:
  # ファイルを指定して検証
  python validate_openapi.py openapi.yaml
  python validate_openapi.py --file openapi.json

  # 警告も表示（descriptions 未設定等）
  python validate_openapi.py --strict openapi.yaml

  # JSON 形式で出力
  python validate_openapi.py --json openapi.yaml

終了コード:
  0 = バリデーション通過（警告のみの場合も含む）
  1 = バリデーションエラーあり
  2 = ファイル読み取り / パースエラー
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


# ─── YAML / JSON ローダー ────────────────────────────────────

def _load_yaml_or_json(text: str, filename: str) -> dict:
    """YAML または JSON テキストをパースして dict を返す。"""
    # まず JSON を試みる
    if filename.endswith(".json") or text.lstrip().startswith("{"):
        return json.loads(text)
    # YAML を試みる（PyYAML）
    try:
        import yaml  # type: ignore
        result = yaml.safe_load(text)
        if not isinstance(result, dict):
            raise ValueError(f"YAML の最上位要素が dict ではありません: {type(result)}")
        return result
    except ImportError:
        # PyYAML がない場合は簡易フォールバック
        raise ImportError(
            "YAML ファイルの解析には PyYAML が必要です。\n"
            "  pip install pyyaml\n"
            "または JSON 形式の OpenAPI ファイルを使用してください。"
        )


# ─── バリデーション ───────────────────────────────────────────

@dataclass
class Issue:
    level: str    # "error" | "warning"
    code: str
    path: str     # ドット区切りパス（例: "paths./users.get.responses"）
    message: str


def _get(data: dict, *keys, default=None):
    """ネストされた dict から安全に値を取得する。"""
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is default:
            return default
    return cur


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
PATH_PARAM_PATTERN = re.compile(r"\{([^}]+)\}")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+")


def validate_openapi(spec: dict, strict: bool) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, path: str, msg: str):
        issues.append(Issue("error", code, path, msg))

    def warn(code: str, path: str, msg: str):
        if strict:
            issues.append(Issue("warning", code, path, msg))

    # ── トップレベル必須フィールド ────────────────────────────
    openapi_ver = spec.get("openapi")
    if not openapi_ver:
        err("E001", "openapi", "'openapi' フィールドが必須です（例: '3.0.3'）")
    elif not SEMVER_PATTERN.match(str(openapi_ver)):
        err("E002", "openapi", f"'openapi' の値 '{openapi_ver}' は semver 形式でなければなりません")
    elif not str(openapi_ver).startswith("3."):
        err("E003", "openapi", f"このバリデータは OpenAPI 3.x に対応しています（検出: {openapi_ver}）")

    info = spec.get("info")
    if not info:
        err("E004", "info", "'info' フィールドが必須です")
    elif isinstance(info, dict):
        for field in ("title", "version"):
            if not info.get(field):
                err("E005", f"info.{field}", f"info.{field} は必須です")
        if strict and not info.get("description"):
            warn("W001", "info.description", "info.description が未設定です（推奨）")

    paths = spec.get("paths")
    if paths is None:
        err("E006", "paths", "'paths' フィールドが必須です")
    elif not isinstance(paths, dict):
        err("E007", "paths", "'paths' はオブジェクトでなければなりません")
    else:
        if len(paths) == 0:
            warn("W002", "paths", "paths が空です（エンドポイントが1件もありません）")
        for path_key, path_item in paths.items():
            _validate_path(path_key, path_item, spec, strict, err, warn, issues)

    # セキュリティスキーム
    components = spec.get("components", {})
    security_schemes = _get(components, "securitySchemes") if isinstance(components, dict) else None
    global_security = spec.get("security")

    if global_security and not security_schemes:
        err(
            "E020", "components.securitySchemes",
            "グローバル security が設定されていますが、components.securitySchemes が定義されていません"
        )

    # servers
    servers = spec.get("servers")
    if strict and not servers:
        warn("W010", "servers", "'servers' が未設定です（デプロイ先 URL の明記を推奨）")
    if isinstance(servers, list):
        for i, srv in enumerate(servers):
            if isinstance(srv, dict):
                url = srv.get("url", "")
                if url and not url.startswith("http") and not url.startswith("/"):
                    err("E030", f"servers[{i}].url", f"servers[{i}].url は有効な URL でなければなりません: {url}")
                if strict and not srv.get("description"):
                    warn("W011", f"servers[{i}].description", f"servers[{i}].description が未設定です")

    return issues


def _validate_path(
    path_key: str,
    path_item: dict | None,
    spec: dict,
    strict: bool,
    err,
    warn,
    issues: list[Issue],
) -> None:
    if not path_key.startswith("/"):
        err("E010", f"paths.{path_key}", f"パスは '/' で始まる必要があります: {path_key}")

    if not isinstance(path_item, dict):
        if path_item is not None:
            err("E011", f"paths.{path_key}", f"パスアイテムはオブジェクトでなければなりません")
        return

    # パスパラメータの整合性チェック
    declared_path_params = set(PATH_PARAM_PATTERN.findall(path_key))

    for method, operation in path_item.items():
        if method not in HTTP_METHODS:
            continue
        if not isinstance(operation, dict):
            err("E012", f"paths.{path_key}.{method}", "オペレーションはオブジェクトでなければなりません")
            continue

        op_path = f"paths.{path_key}.{method}"

        # responses は必須
        responses = operation.get("responses")
        if responses is None:
            err("E013", f"{op_path}.responses", f"responses は必須です")
        elif isinstance(responses, dict):
            if len(responses) == 0:
                err("E014", f"{op_path}.responses", "responses が空です（少なくとも1つのステータスコードが必要）")
            for status_code, response in responses.items():
                if not (str(status_code).isdigit() or str(status_code) == "default"):
                    err(
                        "E015", f"{op_path}.responses.{status_code}",
                        f"レスポンスのキーは HTTP ステータスコードまたは 'default' でなければなりません: {status_code}"
                    )
                if strict and isinstance(response, dict) and not response.get("description"):
                    issues.append(Issue(
                        "warning", "W020",
                        f"{op_path}.responses.{status_code}.description",
                        f"response[{status_code}].description が未設定です",
                    ))

        # operationId の推奨
        if strict and not operation.get("operationId"):
            issues.append(Issue(
                "warning", "W021", f"{op_path}.operationId",
                "operationId が未設定です（コード生成時に必要）"
            ))

        # パスパラメータの定義チェック
        params = operation.get("parameters", [])
        # path レベルのパラメータも結合
        path_level_params = path_item.get("parameters", [])
        all_params = (path_level_params if isinstance(path_level_params, list) else []) + \
                     (params if isinstance(params, list) else [])

        defined_path_params = {
            p.get("name")
            for p in all_params
            if isinstance(p, dict) and p.get("in") == "path"
        }
        for pp in declared_path_params:
            if pp not in defined_path_params:
                err(
                    "E016", f"{op_path}.parameters",
                    f"パスパラメータ '{{{pp}}}' が parameters に定義されていません"
                )

        # セキュリティスキームの存在確認（参照チェック）
        op_security = operation.get("security")
        global_components = spec.get("components", {})
        security_schemes = _get(global_components, "securitySchemes") or {}
        if isinstance(op_security, list):
            for sec_req in op_security:
                if isinstance(sec_req, dict):
                    for scheme_name in sec_req:
                        if scheme_name not in security_schemes and spec.get("security") is None:
                            err(
                                "E017", f"{op_path}.security",
                                f"セキュリティスキーム '{scheme_name}' が components.securitySchemes に未定義です"
                            )


# ─── 出力 ─────────────────────────────────────────────────────

def print_text_report(issues: list[Issue], filename: str) -> None:
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    if not issues:
        print(f"✅ {filename}: バリデーション通過（エラー・警告なし）")
        return

    if warnings:
        print(f"⚠️  警告 ({len(warnings)} 件):")
        for w in warnings:
            print(f"  [{w.code}] {w.path}")
            print(f"    → {w.message}")

    if errors:
        print(f"\n❌ エラー ({len(errors)} 件):")
        for e in errors:
            print(f"  [{e.code}] {e.path}")
            print(f"    → {e.message}")
        print(f"\nバリデーション失敗: {len(errors)} 件のエラー")
    else:
        print(f"\n✅ {filename}: バリデーション通過（警告 {len(warnings)} 件）")


def print_json_report(issues: list[Issue], filename: str) -> None:
    errors = [i for i in issues if i.level == "error"]
    output = {
        "file": filename,
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "issues": [
            {"level": i.level, "code": i.code, "path": i.path, "message": i.message}
            for i in issues
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ─── エントリポイント ──────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 3.x スキーマのバリデーション")
    parser.add_argument(
        "positional",
        nargs="?",
        metavar="FILE",
        help="バリデーション対象の YAML / JSON ファイル（--file でも指定可）",
    )
    parser.add_argument("--file", help="バリデーション対象のファイルパス")
    parser.add_argument("--strict", action="store_true", help="警告（descriptions 未設定等）も報告する")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON 形式で出力")
    args = parser.parse_args()

    target = args.file or args.positional
    if not target:
        # デフォルトファイルを探す
        for candidate in ("openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.json"):
            if Path(candidate).exists():
                target = candidate
                break
        if not target:
            print(
                "❌ バリデーション対象ファイルが指定されていません。\n"
                "使い方: python validate_openapi.py openapi.yaml",
                file=sys.stderr,
            )
            return 2

    fpath = Path(target)
    if not fpath.exists():
        print(f"❌ ファイルが見つかりません: {fpath}", file=sys.stderr)
        return 2

    try:
        text = fpath.read_text(encoding="utf-8")
    except OSError as e:
        print(f"❌ 読み取りエラー: {e}", file=sys.stderr)
        return 2

    try:
        spec = _load_yaml_or_json(text, fpath.name)
    except ImportError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError, Exception) as e:
        print(f"❌ パースエラー: {e}", file=sys.stderr)
        return 2

    issues = validate_openapi(spec, strict=args.strict)

    if args.as_json:
        print_json_report(issues, str(fpath))
    else:
        print_text_report(issues, str(fpath))

    has_errors = any(i.level == "error" for i in issues)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
