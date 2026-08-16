#!/usr/bin/env python3
"""moltbook_drafts — quiet 運転の返信下書き（outbox/drafts/）の一覧・承認・却下。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.4（K3）

reply --autonomous が reply_mode=quiet でブロックされたときに置かれた下書きを、人が
確認して承認（drafts/approved/ へ移す。次回の当番の `moltbook_batch.py
--direction reply-drafts` が privacy gate を再度通したうえで送信する）・却下
（drafts/discarded/ へ移す。送信しない）するための読み取り・確定専用 CLI。

新しい判断はしない——本文は当番プロンプトが根拠つきで書いたものをそのまま見せ、
privacy gate の判定だけをここで併記する（材料を 1 画面に揃えて 1 回で決めさせる。
計画書 C4）。gate の判定は表示用の下読みで、実際の gate は送信直前
（`moltbook_batch.py --direction reply-drafts`）にもう一度通る——ここで ALLOW と
出ても、それ自体は GitLab へは何も送らない（承認しても gate の代わりにはならない）。

CLI:
    python moltbook_drafts.py list [--drafts-dir DIR] [--json]
      承認待ち（drafts/ 直下。approved/discarded/sent は対象外）を一覧する。
    python moltbook_drafts.py approve --file NAME [--drafts-dir DIR]
    python moltbook_drafts.py discard --file NAME [--drafts-dir DIR] [--reason TEXT]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moltbook_batch import _parse_draft  # noqa: E402
from moltbook_config import get_moltbook_home  # noqa: E402
from privacy_gate import evaluate as gate_evaluate  # noqa: E402


def _drafts_dir(args) -> Path:
    if getattr(args, "drafts_dir", None):
        return Path(args.drafts_dir)
    return get_moltbook_home() / "outbox" / "drafts"


def _draft_view(path: Path) -> dict:
    meta, body = _parse_draft(path.read_text(encoding="utf-8"))
    body = body.strip()
    result = gate_evaluate(body, source_layer=str(meta.get("source_layer") or "ltm"))
    return {
        "file": path.name,
        "iid": meta.get("iid") or "",
        "author": meta.get("author") or "",
        "created": meta.get("created") or "",
        "body": body,
        "gate": {
            "allowed": result.allowed,
            "summary": result.summary(),
            "scrubbed": result.scrubbed.strip(),
        },
    }


def cmd_list(args) -> int:
    drafts = _drafts_dir(args)
    pending = sorted(p for p in drafts.glob("*.md") if p.is_file()) if drafts.is_dir() else []
    rows = [_draft_view(p) for p in pending]
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print(f"承認待ちの下書きはありません（{drafts}/*.md）")
        return 0
    for row in rows:
        gate = row["gate"]
        flag = "OK" if gate["allowed"] else "BLOCK"
        print(f"[{flag}] {row['file']}  #{row['iid']} ({row['author']}, {row['created']})")
        print(f"  {gate['summary']}")
        print(f"  {row['body'][:200]}")
    return 0


def _safe_target(drafts: Path, name: str) -> "Path | None":
    """`name` が drafts 直下の実在ファイルを指すときだけそのパスを返す。

    dashboard から渡ってくるファイル名を signal として使う口なので、`../` を含む
    値で drafts の外を指させない（path traversal 対策。一覧が返した名前をそのまま
    渡す用途しか想定しないが、境界は自前で守る）。
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    path = drafts / name
    try:
        resolved = path.resolve()
        if resolved.parent != drafts.resolve():
            return None
    except OSError:
        return None
    return path if path.is_file() else None


def _move(args, dest_name: str) -> int:
    drafts = _drafts_dir(args)
    path = _safe_target(drafts, str(args.file))
    if path is None:
        print(f"[ERROR] 下書きが見つかりません: {args.file}", file=sys.stderr)
        return 2
    dest_dir = drafts / dest_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest_dir / path.name))
    return 0


def cmd_approve(args) -> int:
    rc = _move(args, "approved")
    if rc == 0:
        print(f"承認しました: {args.file} → drafts/approved/")
    return rc


def cmd_discard(args) -> int:
    rc = _move(args, "discarded")
    if rc == 0:
        reason = f"（理由: {args.reason}）" if getattr(args, "reason", None) else ""
        print(f"却下しました: {args.file} → drafts/discarded/{reason}")
    return rc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="quiet 運転の返信下書きの一覧・承認・却下")
    p.add_argument("--drafts-dir", default=None,
                   help="下書きディレクトリ（既定: {agent_home}/.moltbook/outbox/drafts）")
    sub = p.add_subparsers(dest="cmd", required=True)

    lst = sub.add_parser("list", help="承認待ちの下書きを一覧する")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=cmd_list)

    appr = sub.add_parser("approve", help="下書きを承認する（drafts/approved/ へ移す）")
    appr.add_argument("--file", required=True, help="drafts/ 直下のファイル名")
    appr.set_defaults(func=cmd_approve)

    disc = sub.add_parser("discard", help="下書きを却下する（drafts/discarded/ へ移す。送信しない）")
    disc.add_argument("--file", required=True, help="drafts/ 直下のファイル名")
    disc.add_argument("--reason", default="", help="却下理由（任意・表示のみ）")
    disc.set_defaults(func=cmd_discard)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
