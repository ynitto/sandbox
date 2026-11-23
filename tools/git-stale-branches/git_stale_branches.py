#!/usr/bin/env python3
"""Report stale local Git branches and optionally delete merged candidates."""

import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys


def git(*args):
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, errors="surrogateescape"
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def inventory(base, days, protected):
    git("rev-parse", "--git-dir")
    base_ref = f"refs/heads/{base}"
    try:
        git("show-ref", "--verify", "--quiet", "--", base_ref)
    except RuntimeError as exc:
        raise RuntimeError(f"Cannot resolve local base branch {base!r}: {exc}") from exc

    merged = set(git(
        "for-each-ref", f"--merged={base_ref}", "--format=%(refname)", "refs/heads/"
    ).split("\n"))
    rows = git(
        "for-each-ref", "--sort=refname", "--sort=committerdate",
        "--format=%(refname)%09%(HEAD)%09%(committerdate:unix)", "refs/heads/",
    )
    now = datetime.now(timezone.utc)
    branches = []
    for row in rows.split("\n")[:-1]:
        ref, head, timestamp = row.split("\t")
        name = ref[len("refs/heads/"):]
        committed_at = datetime.fromtimestamp(int(timestamp), timezone.utc)
        age = now - committed_at
        current = head == "*"
        is_merged = ref in merged
        is_protected = current or name in protected
        branches.append({
            "name": name,
            "merged": is_merged,
            "last_commit_date": committed_at.isoformat(),
            "age_days": age.days,
            "current": current,
            "protected": is_protected,
            "candidate": not is_protected and (is_merged or age.days > days),
        })
    return branches


def print_report(branches):
    width = max([len("Branch"), *(len(branch["name"]) for branch in branches)])
    print(f"{'Branch':<{width}}  {'Merged':<6}  {'Last commit (UTC)':<25}  "
          f"{'Days':>6}  {'Current':<7}  Candidate")
    for branch in branches:
        print(f"{branch['name']:<{width}}  "
              f"{'yes' if branch['merged'] else 'no':<6}  "
              f"{branch['last_commit_date']:<25}  {branch['age_days']:>6}  "
              f"{'yes' if branch['current'] else 'no':<7}  "
              f"{'*' if branch['candidate'] else ''}".rstrip())
    candidates = [branch["name"] for branch in branches if branch["candidate"]]
    print("\nCandidates: " + (", ".join(candidates) or "none"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="local base branch (default: main)")
    parser.add_argument("--days", type=int, default=90, help="age threshold (default: 90)")
    parser.add_argument("--protect", action="append", default=[], metavar="BRANCH",
                        help="protect a branch (repeatable)")
    parser.add_argument("--delete-merged", action="store_true", help="delete merged candidates")
    parser.add_argument("--json", action="store_true", help="output a JSON array")
    args = parser.parse_args(argv)
    if args.days < 0:
        parser.error("--days must be non-negative")

    protected = {"main", "master", "develop", args.base, *args.protect}
    try:
        branches = inventory(args.base, args.days, protected)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(branches, indent=2))
    else:
        print_report(branches)

    failed = False
    if args.delete_merged:
        output = sys.stderr if args.json else sys.stdout
        deleted = []
        for branch in branches:
            if branch["candidate"] and branch["merged"]:
                name = branch["name"]
                try:
                    git("branch", "-d", "--", name)
                except (OSError, RuntimeError) as exc:
                    print(f"Failed to delete {name!r}: {exc}", file=sys.stderr)
                    failed = True
                else:
                    deleted.append(name)
        print("Deleted: " + (", ".join(deleted) or "none"), file=output)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
