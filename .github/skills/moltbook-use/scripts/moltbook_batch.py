#!/usr/bin/env python3
"""moltbook_batch — 双方向の強制バッチ（設計書 14 章）.

早期・少人数フェーズでは harvest（SNS→記憶）も publish（記憶→SNS）も滞るため、
閾値を無視して機械的に処理する。冪等性はマーカー/メタで担保し、privacy gate は
早期でも緩めない。

  harvest 方向:      moltbook:post の Issue を走査し、記憶取り込み用 Markdown を
                     staging（inbox）へ書き出す。自記憶由来・取り込み済みは skip。
  publish 方向:      outbox の候補 Markdown を privacy gate に通し、通過分のみ
                     moltbook:knowledge として公開。公開済みは published/ へ退避。
  reply-drafts 方向: quiet 運転で reply --autonomous がブロックされたときに
                     置かれた下書き（outbox/drafts/）のうち、人が承認して
                     drafts/approved/ へ移した分だけを送信する（計画書 §3.3 K2）。
                     新しい判断はしない——承認前の内容は GitLab へ一切出ない。
                     承認済みでも privacy gate は必ず通す（K3・§3.4 の防御多層）。

候補ファイル（publish outbox）の形式:
    ---
    title: ...
    source_layer: ltm        # privacy gate 用（persona は BLOCK）
    topics: [git, ci]
    ---
    本文...

例:
    python moltbook_batch.py --direction both --mode force --dry-run
    python moltbook_batch.py --direction harvest --include-open --max 50
    python moltbook_batch.py --direction publish --outbox {agent_home}/.moltbook/outbox
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitlab_api import GitLabClient, GitLabError  # noqa: E402
from privacy_gate import evaluate as gate_evaluate  # noqa: E402
from mb_state import record_reply  # noqa: E402
from moltbook_config import get_moltbook_home  # noqa: E402
import moltbook as mb  # noqa: E402


# --- front matter parsing (publish 候補) -------------------------------------

def _parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return {}, text
    head = parts[0][3:]  # strip leading ---
    body = parts[1].lstrip("\n")
    try:
        meta = yaml.safe_load(head) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), body


# --- harvest direction -------------------------------------------------------

def run_harvest(client: GitLabClient, args) -> dict:
    tally = {"harvested": 0, "skip-self": 0, "skip-dup": 0, "skip-no-answer": 0, "skip-filter": 0}
    out_dir = Path(args.inbox)
    created_after = None
    if args.since_days:
        created_after = (_dt.datetime.now() - _dt.timedelta(days=args.since_days)).isoformat()

    issues = client.list_issues(
        labels=[mb.L_POST], state="all", created_after=created_after, max_items=args.max
    )
    for issue in issues:
        labels = issue.get("labels") or []
        is_open = mb.L_OPEN in labels and mb.L_ANSWERED not in labels
        if is_open and not (args.include_open or args.mode == "force"):
            tally["skip-filter"] += 1
            continue
        if (issue.get("upvotes") or 0) < args.min_goods:
            tally["skip-filter"] += 1
            continue
        status, path = mb.harvest_issue(
            client, issue, out_dir=out_dir, layer=args.layer,
            force=(args.mode == "force"), dry_run=args.dry_run,
        )
        tally[status] = tally.get(status, 0) + 1
        if status == "harvested" and path:
            print(f"  harvest #{issue.get('iid')} → {path}")
    return tally


# --- publish direction -------------------------------------------------------

def run_publish(client: GitLabClient, args) -> dict:
    tally = {"published": 0, "blocked": 0, "empty": 0}
    outbox = Path(args.outbox)
    done_dir = outbox / "published"
    candidates = sorted(p for p in outbox.glob("*.md") if p.is_file())
    if not candidates:
        print(f"  publish 候補なし（{outbox}/*.md）")
        return tally

    for path in candidates:
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        title = str(meta.get("title") or path.stem)
        source_layer = str(meta.get("source_layer") or "")
        topics = meta.get("topics") or []
        if not body.strip():
            tally["empty"] += 1
            continue

        result = gate_evaluate(f"{title}\n{body}", source_layer=source_layer)
        if not result.allowed:
            tally["blocked"] += 1
            print(f"  blocked {path.name}: {result.summary()}", file=sys.stderr)
            continue

        scrubbed = result.scrubbed.split("\n", 1)[1] if "\n" in result.scrubbed else result.scrubbed
        scrubbed = scrubbed.rstrip()
        labels = [mb.L_POST, mb.L_KNOWLEDGE] + [mb.topic_label(str(t)) for t in topics]
        description = f"{scrubbed}\n\n{mb.origin_marker(title + scrubbed)}"
        issue = client.create_issue(title, description, labels)
        tally["published"] += 1
        if not args.dry_run:
            done_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(done_dir / path.name))
            print(f"  publish {path.name} → #{issue.get('iid')}")
        else:
            print(f"  [dry-run] publish {path.name}")
    return tally


# --- reply-drafts direction ---------------------------------------------------

def _parse_draft(text: str) -> tuple[dict, str]:
    """下書きの frontmatter（iid: / author: の単純な key: value だけ）を読む。

    publish 候補の `_parse_front_matter` と違い YAML には依存しない——下書きは
    リストを持たない単層の値だけなので、PyYAML 不在で本文ごと読めなくなる事故を
    避ける（moltbook.py の `write_reply_draft` が書く形式に合わせた専用パーサ）。
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return {}, text
    head, body = parts[0][3:], parts[1].lstrip("\n")
    meta = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, body


def run_send_drafts(client: GitLabClient, args) -> dict:
    """人が承認した返信下書き（drafts/approved/*.md）を送信する。

    quiet 運転で reply --autonomous がブロックされたときに置かれた下書きのうち、
    人が drafts/approved/ へ移した分だけを送る——ここでは新しい判断をしない。
    承認済みでも privacy gate は必ず通す（publish と同じ最後の砦。人の承認は
    「送ってよい内容か」の確認であって gate の代わりではない——確認漏れの下書きが
    そのまま漏洩に直結しない）。送信後は record_reply でガバナの帳簿（予算/
    クールダウン）へ記帳し、承認済みの送信もその日の予算消費として数える
    （無制限の抜け道にしない）。
    """
    tally = {"sent": 0, "blocked": 0, "skipped": 0}
    approved = Path(args.drafts_dir)
    sent_dir = approved.parent / "sent"
    candidates = sorted(p for p in approved.glob("*.md") if p.is_file()) if approved.is_dir() else []
    if not candidates:
        print(f"  承認済みの下書きなし（{approved}/*.md）")
        return tally

    for path in candidates:
        meta, body = _parse_draft(path.read_text(encoding="utf-8"))
        try:
            iid = int(meta.get("iid") or 0)
        except ValueError:
            iid = 0
        if not iid or not body.strip():
            tally["skipped"] += 1
            print(f"  [SKIP] {path.name}: iid または本文が読めません", file=sys.stderr)
            continue
        result = gate_evaluate(body.strip(), source_layer=str(meta.get("source_layer") or "ltm"))
        if not result.allowed:
            tally["blocked"] += 1
            print(f"  blocked {path.name}: {result.summary()}", file=sys.stderr)
            continue    # 承認済みでも gate を通らなければ approved/ に残し、人へ差し戻す
        note = client.create_note(iid, result.scrubbed.rstrip())
        tally["sent"] += 1
        if args.dry_run:
            print(f"  [dry-run] send {path.name} → #{iid}")
            continue
        record_reply(iid, str(meta.get("author") or "?"))
        sent_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(sent_dir / path.name))
        print(f"  send {path.name} → #{iid}（note {note.get('id')}）")
    return tally


# --- main --------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Moltbook 双方向 強制バッチ（harvest/publish）")
    p.add_argument("--label-conn", dest="label", default="default")
    p.add_argument("--direction", choices=["harvest", "publish", "reply-drafts", "both"],
                   default="both")
    p.add_argument("--mode", choices=["force", "quality"], default="force",
                   help="force: 早期向け（閾値ゆるめ）/ quality: 成熟向け")
    p.add_argument("--dry-run", action="store_true")
    # harvest
    p.add_argument("--inbox", default=None, help="harvest 出力先（既定: {agent_home}/.moltbook/inbox）")
    p.add_argument("--include-open", action="store_true", help="未解決 Issue も取り込む")
    p.add_argument("--min-goods", type=int, default=0, help="取込の Good 下限")
    p.add_argument("--since-days", type=int, default=0, help="N 日以内に作成された Issue のみ")
    p.add_argument("--layer", choices=["ltm", "wiki"], help="取り込み先レイヤを明示")
    p.add_argument("--max", type=int, default=100, help="走査する Issue 上限")
    # publish
    p.add_argument("--outbox", default=None, help="publish 候補ディレクトリ（既定: {agent_home}/.moltbook/outbox）")
    # reply-drafts
    p.add_argument("--drafts-dir", default=None,
                   help="承認済み返信下書きディレクトリ（既定: {agent_home}/.moltbook/outbox/drafts/approved）")
    args = p.parse_args(argv)

    home = get_moltbook_home()
    if args.inbox is None:
        args.inbox = str(home / "inbox")
    if args.outbox is None:
        args.outbox = str(home / "outbox")
    if args.drafts_dir is None:
        args.drafts_dir = str(home / "outbox" / "drafts" / "approved")

    if args.mode == "quality":
        # 成熟フェーズの既定: 未解決は対象外、Good 下限を引き上げ
        if not args.include_open:
            args.include_open = False
        if args.min_goods == 0:
            args.min_goods = 2

    try:
        client = GitLabClient.from_config(args.label, dry_run=args.dry_run)
    except GitLabError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    try:
        if args.direction in ("harvest", "both"):
            print("== harvest（SNS→記憶 staging）==")
            h = run_harvest(client, args)
            print("  集計: " + ", ".join(f"{k}={v}" for k, v in h.items() if v))
        if args.direction in ("publish", "both"):
            print("== publish（記憶→SNS, privacy gate）==")
            pub = run_publish(client, args)
            print("  集計: " + ", ".join(f"{k}={v}" for k, v in pub.items() if v))
        if args.direction == "reply-drafts":
            print("== reply-drafts（承認済み下書きの送信）==")
            d = run_send_drafts(client, args)
            print("  集計: " + ", ".join(f"{k}={v}" for k, v in d.items() if v))
    except GitLabError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
