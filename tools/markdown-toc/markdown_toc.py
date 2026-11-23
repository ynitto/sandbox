"""Update the table of contents of a UTF-8 Markdown document."""

import argparse
import codecs
import io
from pathlib import Path
import re
import sys
import unicodedata


OPEN = "<!-- toc -->"
CLOSE = "<!-- /toc -->"
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def scan(lines):
    """Find headings and the first TOC pair, ignoring literal content."""
    headings = []
    start = end = None
    closing_without_opening = False
    fence = None
    in_comment = False
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if start is not None and end is None:
            if line.strip() == CLOSE:
                end = index
            continue

        fence_match = FENCE.match(line)
        if fence is not None:
            if fence_match:
                run, rest = fence_match.groups()
                if run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                    fence = None
            continue

        if not in_comment:
            if start is None and line.strip() == OPEN:
                start = index
                continue
            if start is None and line.strip() == CLOSE:
                closing_without_opening = True
                continue
            if fence_match:
                run, rest = fence_match.groups()
                if run[0] == "~" or "`" not in rest:
                    fence = run
                    continue
            heading = HEADING.match(line)
            if heading:
                hashes, title = heading.groups()
                headings.append((index, len(hashes), title or ""))

        position = 0
        while True:
            delimiter = "-->" if in_comment else "<!--"
            found = line.find(delimiter, position)
            if found == -1:
                break
            in_comment = not in_comment
            position = found + len(delimiter)

    return headings, start, end, closing_without_opening


def render_toc(headings, min_level, max_level, newline):
    used = set()
    suffixes = {}
    items = []
    for _, level, raw_title in headings:
        title = re.sub(r"<!--.*?(?:-->|$)", "", raw_title)
        title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", title).strip()
        slug = "".join(
            "-" if char.isspace() else char
            for char in title.lower()
            if char.isspace() or char in "-_" or unicodedata.category(char)[0] in "LMN"
        )
        anchor = slug
        suffix = suffixes.get(slug, 0)
        while anchor in used:
            suffix += 1
            anchor = f"{slug}-{suffix}"
        suffixes[slug] = suffix
        used.add(anchor)
        if min_level <= level <= max_level:
            label = re.sub(r"([\\\[\]])", r"\\\1", title)
            indent = "  " * (level - min_level)
            items.append(f"{indent}- [{label}](#{anchor}){newline}")
    return items


def main(argv=None):
    parser = argparse.ArgumentParser(description="Update a Markdown table of contents.")
    parser.add_argument("file", help="Markdown file, or - for stdin")
    parser.add_argument("--min-level", type=int, choices=range(1, 7), default=2)
    parser.add_argument("--max-level", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--create", action="store_true", help="insert missing TOC markers")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--check", action="store_true", help="check whether the TOC is current")
    output.add_argument("--stdout", action="store_true", help="print the updated document")
    args = parser.parse_args(argv)
    if args.min_level > args.max_level:
        parser.error("--min-level must not exceed --max-level")

    path = None if args.file == "-" else Path(args.file)
    try:
        original = sys.stdin.buffer.read() if path is None else path.read_bytes()
        document = original.decode("utf-8-sig")
        lines = io.StringIO(document, newline="").readlines()
        newline_match = re.search(r"\r\n|\n|\r", document)
        newline = newline_match.group() if newline_match else "\n"
        headings, start, end, closing_without_opening = scan(lines)
        if start is None and closing_without_opening:
            raise ValueError("TOC opening marker is missing")
        if start is None:
            if args.check:
                print("TOC has not been inserted", file=sys.stderr)
                return 1
            if not args.create:
                raise ValueError("TOC markers are missing (use --create to insert them)")
            if not headings:
                raise ValueError("no ATX heading found for TOC insertion")
        elif end is None:
            raise ValueError("TOC closing marker is missing")

        items = render_toc(headings, args.min_level, args.max_level, newline)
        if start is None:
            index = headings[0][0]
            if not lines[index].endswith(("\r", "\n")):
                lines[index] += newline
            after = lines[index + 1:]
            separator = [newline] if after and after[0].strip() else []
            lines[index + 1:index + 1] = [
                newline, OPEN + newline, *items, CLOSE + newline, *separator
            ]
        else:
            lines[start + 1:end] = items

        bom = codecs.BOM_UTF8 if original.startswith(codecs.BOM_UTF8) else b""
        updated = bom + "".join(lines).encode("utf-8")
        if args.check:
            if updated != original:
                print("TOC is out of date", file=sys.stderr)
                return 1
        elif path is None or args.stdout:
            sys.stdout.buffer.write(updated)
        elif updated != original:
            path.write_bytes(updated)
        return 0
    except (OSError, ValueError) as error:
        print(f"markdown-toc: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
