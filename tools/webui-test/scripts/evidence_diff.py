#!/usr/bin/env python3
"""webui-test の evidence v1 を比較し、夜間集約用の差分を出力する。

JSON v1 のトップレベルは version, source（webui-test-evidence-diff）,
generated_at（UTC）, baseline_missing, summary, changes。
summary は new_failures, recovered, still_failing, screen_changes, added,
removed, failed_total, passed_total の件数を持つ。
changes の new_failures は {id, title, error}、recovered と screen_changes は
{id, title}、added と removed は {id, kind, title} の配列。error がなければ null。
なくなった画面の screen_changes には status: "removed" も付く。
初回は現在の failed_total だけを数え、ほかの件数は 0、一覧は空になる。
Markdown には新規失敗・回復・画面変化だけを載せる。
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def read_items(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise ValueError(f"{path}: evidence の items が配列ではありません")
    items = {}
    for item in doc["items"]:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(field), str) for field in ("id", "kind")
        ):
            raise ValueError(f"{path}: evidence の item に id または kind がありません")
        key = (item["id"], item["kind"])
        if key in items:
            raise ValueError(f"{path}: evidence の item が重複しています (id={key[0]!r}, kind={key[1]!r})")
        items[key] = item
    return items


def describe(item):
    return {"id": item["id"], "title": item.get("title") or item["id"]}


def compare(before, after):
    changes = {key: [] for key in (
        "new_failures", "recovered", "screen_changes", "added", "removed"
    )}
    still_failing = 0
    if before is not None:
        for key in sorted(after):
            item = after[key]
            previous = before.get(key, {})
            status = item.get("status")
            if key not in before:
                changes["added"].append({**describe(item), "kind": item["kind"]})
            if item["kind"] == "behavior":
                was_failing = previous.get("status") == "failed"
                if status == "failed":
                    if was_failing:
                        still_failing += 1
                    else:
                        changes["new_failures"].append({**describe(item), "error": item.get("error")})
                elif status == "passed" and was_failing:
                    changes["recovered"].append(describe(item))
            elif item["kind"] == "image" and status == "removed":
                changes["screen_changes"].append({**describe(item), "status": "removed"})
            elif item["kind"] == "image" and (
                status == "changed" or (previous and previous.get("sha256") != item.get("sha256"))
            ):
                changes["screen_changes"].append(describe(item))
        for key in sorted(before.keys() - after.keys()):
            item = before[key]
            changes["removed"].append({**describe(item), "kind": item["kind"]})

    statuses = [item.get("status") for item in after.values() if item["kind"] == "behavior"]
    return {
        "version": 1,
        "source": "webui-test-evidence-diff",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_missing": before is None,
        "summary": {
            **{key: len(entries) for key, entries in changes.items()},
            "still_failing": still_failing,
            "failed_total": statuses.count("failed"),
            "passed_total": 0 if before is None else statuses.count("passed"),
        },
        "changes": changes,
    }


def render_markdown(changes):
    sections = []
    for key, heading in (
        ("new_failures", "新規失敗"),
        ("recovered", "回復"),
        ("screen_changes", "画面変化"),
    ):
        if not changes[key]:
            continue
        lines = [f"## {heading}", ""]
        for item in changes[key]:
            label = f"{item['title']}（{item['id']}）"
            if item.get("error"):
                label += f" — {item['error']}"
            if item.get("status") == "removed":
                label += " — 画面がなくなった"
            lines.append("- " + " ".join(label.splitlines()))
        sections.append("\n".join(lines))
    return ("\n\n".join(sections) or "変化なし") + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()

    outputs = [args.output]
    if args.md_out is not None:
        outputs.append(args.md_out)
    try:
        paths = [path.resolve() for path in outputs]
        if len(set(paths)) != len(paths) or set(paths) & {args.before.resolve(), args.after.resolve()}:
            raise ValueError("入力と出力にはそれぞれ別のパスを指定してください")
        for path in outputs:
            path.unlink(missing_ok=True)
        after = read_items(args.after)
        try:
            before = read_items(args.before)
        except FileNotFoundError:
            before = None

        report = compare(before, after)
        for path in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if args.md_out is not None:
                args.md_out.write_text(render_markdown(report["changes"]), encoding="utf-8")
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            for path in outputs:
                path.unlink(missing_ok=True)
            raise
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
