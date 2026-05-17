"""spec-value-finder — エントリポイント。

サーバ不要・GPU不要。元仕様書(Excel/Word)から「記入すべき値」を探す。

サブコマンド:
    init       依存ライブラリ（openpyxl / python-docx / PyYAML）をインストール
    extract    フォルダを部分一致で走査し、元仕様書を構造化Markdown化
    map-draft  自然文の対応記述ファイル → マッピングファイルのドラフト生成
    validate   マッピングファイルをスキーマ検証
    find       マッピング × 抽出結果 → 項目ごとの候補（候補JSON）を出力
    fill       記入シート例 + findings.json → 値を埋めた新規ファイルを生成

使用例:
    python run.py init
    python run.py extract ./specs --name-match HW仕様 --out ./.svf-cache
    python run.py map-draft ./対応表.xlsx --out mapping.yaml
    python run.py validate mapping.yaml
    python run.py find --mapping mapping.yaml --out candidates.json
    python run.py fill --template 記入例.xlsx --findings findings.json --out 結果.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import argparse
import json


def cmd_init(args: argparse.Namespace) -> None:
    import subprocess

    req = Path(__file__).parent / "requirements.txt"
    print(f"依存ライブラリをインストール中: {req.name} …")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[✗] pip install 失敗:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("[✓] openpyxl / python-docx / PyYAML: OK（サーバ・GPU不要）")


def cmd_extract(args: argparse.Namespace) -> None:
    from extract import extract_file, find_files, to_markdown

    files = find_files(Path(args.folder), args.name_match)
    if not files:
        print(f"[!] 一致するファイルがありません: {args.folder} (name-match='{args.name_match}')",
              file=sys.stderr)
        sys.exit(1)

    print(f"一致 {len(files)} 件:")
    out_dir = Path(args.out).expanduser() if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for path in files:
        try:
            doc = extract_file(path)
        except ValueError as e:
            print(f"  [skip] {path.name}: {e}")
            continue
        tables = sum(1 for b in doc.blocks if getattr(b, "kind", "") == "table")
        texts = sum(1 for b in doc.blocks if getattr(b, "kind", "") == "text")
        print(f"  - {path.name}  ({doc.fmt}: 表 {tables} / 段落 {texts})")
        if out_dir:
            (out_dir / f"{path.stem}.md").write_text(to_markdown(doc), encoding="utf-8")
            (out_dir / f"{path.stem}.json").write_text(
                json.dumps(doc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        elif len(files) == 1:
            print()
            print(to_markdown(doc))

    if out_dir:
        print(f"[✓] Markdown / JSON を出力: {out_dir}")


def cmd_map_draft(args: argparse.Namespace) -> None:
    from extract import extract_file, to_markdown
    from mapping import write_draft

    src = Path(args.file)
    if not src.exists():
        print(f"Error: ファイルが見つかりません: {src}", file=sys.stderr)
        sys.exit(1)
    doc = extract_file(src)
    md = to_markdown(doc)
    out = Path(args.out)
    write_draft(out, source_text=md)
    print(f"[✓] マッピングドラフトを生成: {out}")
    print("    下記『元記述』を読み、items を埋めてから `validate` を実行してください。")
    print()
    print("=" * 60)
    print(f"元記述（{src.name} から抽出）")
    print("=" * 60)
    print(md)


def cmd_validate(args: argparse.Namespace) -> None:
    from mapping import load_mapping, validate_mapping

    data = load_mapping(args.mapping)
    errors, warnings = validate_mapping(data)
    for w in warnings:
        print(f"[warn] {w}")
    for e in errors:
        print(f"[error] {e}", file=sys.stderr)
    if errors:
        print(f"[✗] マッピング検証 NG（{len(errors)} 件のエラー）", file=sys.stderr)
        sys.exit(1)
    n = len(data.get("items") or [])
    print(f"[✓] マッピング検証 OK（{n} 項目, 警告 {len(warnings)} 件）")


def cmd_find(args: argparse.Namespace) -> None:
    from extract import extract_file, find_files
    from finder import find_candidates, format_candidates
    from mapping import items_of, load_mapping, validate_mapping

    data = load_mapping(args.mapping)
    errors, _ = validate_mapping(data)
    if errors:
        for e in errors:
            print(f"[error] {e}", file=sys.stderr)
        print("[✗] マッピングが無効です。`validate` で修正してください。", file=sys.stderr)
        sys.exit(1)

    source = data.get("source") or {}
    folder = args.folder or source.get("folder", "")
    name_match = args.name_match if args.name_match is not None else source.get("name_match", "")
    if not folder:
        print("Error: 元仕様書フォルダが未指定です（mapping の source.folder か --folder）。",
              file=sys.stderr)
        sys.exit(1)

    files = find_files(Path(folder), name_match or "")
    if not files:
        print(f"[!] 一致するファイルがありません: {folder} (name-match='{name_match}')",
              file=sys.stderr)
        sys.exit(1)

    docs = []
    for path in files:
        try:
            docs.append(extract_file(path))
        except ValueError as e:
            print(f"  [skip] {path.name}: {e}", file=sys.stderr)

    result = find_candidates(docs, items_of(data), max_candidates=args.max_candidates)
    if args.out:
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[✓] 候補を出力: {args.out}")
    if args.json and not args.out:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_candidates(result))


def cmd_fill(args: argparse.Namespace) -> None:
    from filler import fill, load_findings

    findings = load_findings(args.findings)
    columns = {}
    for key, flag in (("item", args.col_item), ("value", args.col_value),
                      ("source", args.col_source), ("confidence", args.col_confidence),
                      ("review", args.col_review)):
        if flag:
            columns[key] = flag
    report = fill(args.template, findings, args.out,
                  columns=columns or None, sheet=args.sheet)
    print(f"[✓] 新規ファイルを生成: {report['out']}")
    if "matched" in report:
        print(f"    記入済み行: {report['matched']} / 末尾追記: {report['appended']}")
        if report["unmatched_findings"]:
            print(f"    [warn] テンプレートに項目行が無く追記した: "
                  f"{', '.join(map(str, report['unmatched_findings']))}")
    else:
        print(f"    プレースホルダ置換: {report['replaced']} 箇所")


def main() -> None:
    root = argparse.ArgumentParser(description="spec-value-finder")
    sub = root.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="依存ライブラリをインストール")

    p_ex = sub.add_parser("extract", help="元仕様書を構造化Markdown化")
    p_ex.add_argument("folder", help="走査するフォルダ")
    p_ex.add_argument("--name-match", default="", help="ファイル名の部分一致パターン")
    p_ex.add_argument("--out", default="", help="Markdown/JSON の出力先ディレクトリ")

    p_md = sub.add_parser("map-draft", help="自然文の対応記述 → マッピングドラフト")
    p_md.add_argument("file", help="自然文マッピング記述ファイル（Excel/Word）")
    p_md.add_argument("--out", default="mapping.yaml", help="出力する mapping ファイル")

    p_va = sub.add_parser("validate", help="マッピングファイルを検証")
    p_va.add_argument("mapping", help="mapping.yaml のパス")

    p_fi = sub.add_parser("find", help="マッピング × 抽出結果 → 候補抽出")
    p_fi.add_argument("--mapping", required=True, help="mapping.yaml のパス")
    p_fi.add_argument("--folder", default="", help="元仕様書フォルダ（mapping を上書き）")
    p_fi.add_argument("--name-match", default=None, help="部分一致パターン（mapping を上書き）")
    p_fi.add_argument("--max-candidates", type=int, default=8, help="項目あたりの最大候補数")
    p_fi.add_argument("--out", default="", help="候補JSONの出力先")
    p_fi.add_argument("--json", action="store_true", help="JSON形式で stdout 出力")

    p_fl = sub.add_parser("fill", help="記入シート例 + findings → 新規ファイル生成")
    p_fl.add_argument("--template", required=True, help="記入シート例（Excel/Word）")
    p_fl.add_argument("--findings", required=True, help="findings.json のパス")
    p_fl.add_argument("--out", required=True, help="生成する新規ファイル")
    p_fl.add_argument("--sheet", default=None, help="Excel: 対象シート名（既定=アクティブ）")
    p_fl.add_argument("--col-item", default="", help="Excel: 項目列の見出し（既定『項目』）")
    p_fl.add_argument("--col-value", default="", help="Excel: 値列の見出し（既定『値』）")
    p_fl.add_argument("--col-source", default="", help="Excel: 出典列の見出し（既定『出典』）")
    p_fl.add_argument("--col-confidence", default="", help="Excel: 確信度列の見出し（既定『確信度』）")
    p_fl.add_argument("--col-review", default="", help="Excel: 要確認列の見出し（既定『要確認』）")

    args = root.parse_args()
    {
        "init": cmd_init,
        "extract": cmd_extract,
        "map-draft": cmd_map_draft,
        "validate": cmd_validate,
        "find": cmd_find,
        "fill": cmd_fill,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
