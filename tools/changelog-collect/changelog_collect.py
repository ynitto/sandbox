#!/usr/bin/env python3
"""未リリースのフラグメントを CHANGELOG.md に取り込む。"""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
UNRELEASED = "## [Unreleased]"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    parser.add_argument("--fragments-dir", type=Path, default=ROOT / "changelog" / "unreleased")
    parser.add_argument("--dry-run", action="store_true", help="取り込み予定を表示する")
    args = parser.parse_args(argv)

    try:
        with args.changelog.open(encoding="utf-8", newline="") as source:
            lines = source.read().split("\n")
        if UNRELEASED not in lines:
            print(f"エラー: {args.changelog} に {UNRELEASED} がありません", file=sys.stderr)
            return 1

        existing_headings = {line for line in lines if line.startswith("### ")}
        fragments = []
        bodies = []
        for path in sorted(args.fragments_dir.glob("*.md")):
            if path.name == "README.md" or not path.is_file():
                continue
            body = path.read_text(encoding="utf-8").strip()
            duplicate = next((line for line in body.split("\n") if line in existing_headings), None)
            if duplicate is not None:
                print(f"警告: {path.name} の見出しは取り込み済みです: {duplicate}", file=sys.stderr)
                continue
            fragments.append(path)
            if body:
                bodies.append(body)

        if not fragments:
            print("取り込むフラグメントはありません")
            return 0

        if bodies:
            index = lines.index(UNRELEASED)
            before = "\n".join(lines[:index + 1])
            after = "\n".join(lines[index + 1:])
            block = "\n\n".join(bodies) + "\n"
            if not after.startswith("\n"):
                block += "\n"
            if args.dry_run:
                print(block, end="")
            else:
                args.changelog.write_text(before + "\n" + block + after, encoding="utf-8", newline="\n")

        if not args.dry_run:
            for path in fragments:
                path.unlink()

    except (OSError, UnicodeError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    print("取り込み予定:" if args.dry_run else "取り込み完了:")
    for path in fragments:
        print(path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
