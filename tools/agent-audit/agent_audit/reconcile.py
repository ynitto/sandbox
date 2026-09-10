"""Coverage reconciliation between native session identities and audit records (no LLM)."""
from __future__ import annotations

import json

from .collect import SourceError, agent_defs_with_session_log
from .configfile import resolve_audit_dir
from .store import Store, record_id
from .util import elog, parse_iso


def reconcile(store: Store, *, since: float = 0.0, only=None) -> list[dict]:
    from . import readers
    wanted = set(only or [])
    stored_by_source = {}
    stored_session_ids = {}
    for rec in store.iter_records(since):
        if rec.get("kind") != "session":
            continue
        if since and (parse_iso(rec.get("ts")) or 0.0) < since:
            continue
        source = str(rec.get("source") or "")
        stored_by_source.setdefault(source, set()).add(str(rec.get("id") or ""))
        stored_session_ids.setdefault(source, {})[str(rec.get("id") or "")] = str(
            rec.get("session_id") or rec.get("id") or "")
    reports = []
    known = set()
    for name, spec in agent_defs_with_session_log():
        if wanted and "cli-native" not in wanted and name not in wanted:
            continue
        source = f"{name}-native"
        known.add(source)
        identities = readers.session_identities(spec["session_log"], since=since)
        native = {record_id(f"cli-native:{name}", item["store"], item["native_id"]):
                  item["native_id"] for item in identities}
        stored = stored_by_source.get(source, set())
        missing = sorted(set(native) - stored)
        orphaned = sorted(stored - set(native))
        discovered = len(native)
        collected = len(set(native) & stored)
        reports.append({"source": source, "discovered": discovered,
                        "collected": collected, "missing": len(missing),
                        "orphaned": len(orphaned),
                        "coverage": (collected / discovered if discovered else 1.0),
                        "missing_session_ids": sorted(native[rid] for rid in missing),
                        "orphaned_session_ids": sorted(stored_session_ids[source][rid]
                                                       for rid in orphaned)})
    # Records from a removed CLI definition remain visible rather than silently disappearing.
    for source in sorted(set(stored_by_source) - known):
        if wanted and "cli-native" not in wanted and source.removesuffix("-native") not in wanted:
            continue
        ids = stored_by_source[source]
        reports.append({"source": source, "discovered": 0, "collected": 0,
                        "missing": 0, "orphaned": len(ids), "coverage": 1.0,
                        "missing_session_ids": [],
                        "orphaned_session_ids": sorted(stored_session_ids[source][rid]
                                                       for rid in ids)})
    return reports


def cmd_reconcile(args) -> int:
    store = Store(resolve_audit_dir(args))
    try:
        since = parse_iso(args.since) or 0.0 if getattr(args, "since", None) else 0.0
        reports = reconcile(store, since=since, only=getattr(args, "source", None))
    except (OSError, SourceError, ValueError) as exc:
        elog(f"reconcile: {exc}")
        return 2
    if getattr(args, "json", False):
        print(json.dumps({"sources": reports}, ensure_ascii=False, indent=2))
    else:
        print("source                 discovered collected missing orphaned coverage")
        for row in reports:
            print(f"{row['source']:<22} {row['discovered']:>10} {row['collected']:>9} "
                  f"{row['missing']:>7} {row['orphaned']:>8} {row['coverage']:>8.1%}")
            if row["missing_session_ids"]:
                print("  missing session IDs: " + ", ".join(row["missing_session_ids"]))
    return 1 if any(r["missing"] or r["orphaned"] for r in reports) else 0
