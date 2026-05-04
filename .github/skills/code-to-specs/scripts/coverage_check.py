#!/usr/bin/env python3
"""
カバレッジチェックスクリプト。

inventory.json の全項目が .specs-work/drafts/ いずれかの章で言及されているか検証する。
Phase 4 の検証ステップで使用する。
"""

import json
import os
import sys
from pathlib import Path


def load_inventory(work_dir: Path) -> dict:
    inv_path = work_dir / "inventory.json"
    if not inv_path.exists():
        print(f"[ERROR] inventory.json が見つかりません: {inv_path}", file=sys.stderr)
        sys.exit(1)
    with open(inv_path, encoding="utf-8") as f:
        return json.load(f)


def load_drafts(work_dir: Path) -> dict[str, str]:
    drafts_dir = work_dir / "drafts"
    if not drafts_dir.exists():
        print(f"[ERROR] drafts/ ディレクトリが見つかりません: {drafts_dir}", file=sys.stderr)
        sys.exit(1)
    drafts = {}
    for md_file in sorted(drafts_dir.glob("*.md")):
        drafts[md_file.name] = md_file.read_text(encoding="utf-8")
    return drafts


def check_coverage(inventory: dict, drafts: dict[str, str]) -> tuple[list, list]:
    units = inventory.get("units", [])
    covered = []
    uncovered = []

    all_draft_text = "\n".join(drafts.values()).lower()

    for unit in units:
        name = unit.get("name", "")
        file_ref = unit.get("file", "")
        unit_id = unit.get("id", "?")

        # 名前またはファイルパスが章内に含まれているか確認
        name_found = name.lower() in all_draft_text
        file_found = file_ref.lower() in all_draft_text

        if name_found or file_found:
            covered.append(unit)
        else:
            uncovered.append(unit)

    return covered, uncovered


def update_inventory_coverage(work_dir: Path, inventory: dict, covered: list, uncovered: list):
    covered_ids = {u["id"] for u in covered}
    for unit in inventory.get("units", []):
        if unit["id"] in covered_ids:
            unit["covered_in_chapter"] = "covered"
        else:
            unit["covered_in_chapter"] = None

    inv_path = work_dir / "inventory.json"
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False, indent=2)


def write_report(work_dir: Path, covered: list, uncovered: list):
    total = len(covered) + len(uncovered)
    coverage_rate = (len(covered) / total * 100) if total > 0 else 0

    lines = [
        "# カバレッジレポート",
        "",
        f"- 総インベントリ数: {total}",
        f"- カバー済み: {len(covered)} ({coverage_rate:.1f}%)",
        f"- 未カバー: {len(uncovered)}",
        "",
    ]

    if uncovered:
        lines += [
            "## 未カバーのインベントリ項目",
            "",
            "以下の項目がいずれの章でも言及されていません。",
            "該当章に記述を追加するか、対象外として明示してください。",
            "",
        ]
        for unit in uncovered:
            lines.append(
                f"- [{unit.get('id')}] `{unit.get('name')}` "
                f"({unit.get('file', '')}:{unit.get('lines', '')})"
            )
        lines.append("")

    if covered:
        lines += [
            "## カバー済みインベントリ項目",
            "",
        ]
        for unit in covered:
            lines.append(
                f"- [{unit.get('id')}] `{unit.get('name')}` ✓"
            )
        lines.append("")

    report_path = work_dir / "coverage-report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポートを保存しました: {report_path}")


def main():
    work_dir = Path(".specs-work")
    if not work_dir.exists():
        print("[ERROR] .specs-work/ ディレクトリが見つかりません。", file=sys.stderr)
        print("Phase 0〜3 が完了しているか確認してください。", file=sys.stderr)
        sys.exit(1)

    inventory = load_inventory(work_dir)
    drafts = load_drafts(work_dir)

    if not drafts:
        print("[ERROR] drafts/ にMarkdownファイルがありません。", file=sys.stderr)
        sys.exit(1)

    covered, uncovered = check_coverage(inventory, drafts)
    update_inventory_coverage(work_dir, inventory, covered, uncovered)
    write_report(work_dir, covered, uncovered)

    total = len(covered) + len(uncovered)
    coverage_rate = (len(covered) / total * 100) if total > 0 else 0
    print(f"\nカバレッジ: {len(covered)}/{total} ({coverage_rate:.1f}%)")

    if uncovered:
        print(f"\n[WARNING] {len(uncovered)} 件の未カバー項目があります。")
        print("coverage-report.md を確認して対処してください。")
        sys.exit(1)
    else:
        print("\n[OK] 全インベントリ項目がカバーされています。")


if __name__ == "__main__":
    main()
