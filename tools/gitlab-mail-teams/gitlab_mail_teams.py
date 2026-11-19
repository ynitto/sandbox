#!/usr/bin/env python3
"""gitlab-mail-teams — GitLab の通知メール（.eml）を Teams の Adaptive Card に変換する。

GitLab が送る通知メールを読み、何の通知か（kind）と要点を取り出した「イベント」の JSON にし、
そのイベントから Microsoft Teams に投稿できる Adaptive Card の JSON を作る。
GitLab にも Teams にも接続しない。メールを読んで JSON を出すだけの変換器。

使い方:

    python3 gitlab_mail_teams.py mail.eml                 # イベントの JSON
    python3 gitlab_mail_teams.py mail.eml --format card   # Adaptive Card の JSON
    cat mail.eml | python3 gitlab_mail_teams.py -         # 標準入力から読む

kind:

    issue_created        Issue が作られた
    mr_review_requested  マージリクエストのレビューを頼まれた（メンションを含む）
    pipeline_failed      パイプラインが失敗した
    unknown              上のどれでもない（成功したパイプライン、コメントなど）。取れた項目は埋める

終了コード: 0 = 成功 / 2 = メールとして読めない（空、ヘッダーが無い、ファイルが無い）。
"""

from __future__ import annotations

import argparse
import email
import email.policy
import html
import json
import re
import sys
from email.utils import parseaddr, parsedate_to_datetime

KINDS = ("issue_created", "mr_review_requested", "pipeline_failed", "unknown")

EVENT_FIELDS = (
    "kind", "project", "title", "number", "actor", "url", "status", "branch",
    "summary", "received_at", "subject", "sender",
)

CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
CARD_VERSION = "1.4"
SUMMARY_MAX = 280

# カードの見出し（kind ごと）と、失敗だけに付ける色。
_KIND_LABELS = {
    "issue_created": "Issue が作成されました",
    "mr_review_requested": "レビューの依頼",
    "pipeline_failed": "パイプラインが失敗しました",
    "unknown": "GitLab からの通知",
}
_KIND_COLORS = {"pipeline_failed": "Attention"}

_URL = re.compile(r"https?://[^\s<>()\"']+")
_ISSUE_URL = re.compile(r"https?://[^\s<>()\"']+/-/issues/(\d+)")
_MR_URL = re.compile(r"https?://[^\s<>()\"']+/-/merge_requests/(\d+)")
_PIPELINE_URL = re.compile(r"https?://[^\s<>()\"']+/-/pipelines/(\d+)")
_SUBJECT_REF = re.compile(r"\s*\(([#!])(\d+)\)\s*$")
_SUBJECT_PIPELINE = re.compile(
    r"^(?P<status>Failed|Successful|Fixed)\s+pipeline\s+for\s+(?P<ref>\S+)", re.I)
_REPLY_PREFIX = re.compile(r"^(?:(?:re|fwd?|fw)\s*:\s*)+", re.I)
_USER_SUFFIX = re.compile(r"\s*\(@[^)]+\)\s*$")
_BRANCHES = re.compile(r"^Branches?:\s*(\S+)(?:\s+to\s+(\S+))?", re.I | re.M)
# 本文の先頭にある「誰が何をしたか」の 1 行と、項目名: 値 の行は要約に入れない。
_LEAD_LINE = re.compile(
    r"^.{0,120}?\b(created an? (issue|merge request)|requested (a new )?review|"
    r"was requested to review|mentioned you|commented( on)?|pushed new commits)\b.*$", re.I)
_META_LINE = re.compile(
    r"^(Assignees?|Reviewers?|Authors?|Branches?|Labels?|Milestone|Due date|"
    r"Approvers?|Project|Merge request|Issue)\s*:", re.I)
# <a href="…"> / <a href='…'> / <a href=…> のどれでも、リンク先と中身を取る。
_ANCHOR = re.compile(
    r"""(?is)<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))[^>]*>(.*?)</a>""")
# 先頭行を見分けるときに外す URL（「名前 ( URL ) created an issue」の長い URL で見落とさないため）。
_INLINE_URL = re.compile(r"\(\s*https?://[^)]*\)|https?://\S+")
# Issue が作られたと言える積極的な証拠（本文の定型句 / 通知理由）。
_ISSUE_CREATED_PHRASE = re.compile(
    r"\b(?:created|opened) an? (?:new )?issue\b|\bnew issue was created\b|"
    r"\bissue was (?:created|opened)\b", re.I)
_ISSUE_CREATED_REASONS = frozenset({"created", "issue_created", "new_issue", "opened"})
_PIPELINE_STATUS_WORDS = {"failed": "failed", "successful": "success", "fixed": "success"}


class ParseError(Exception):
    """入力がメールとして読めない。"""


# --------------------------------------------------------------------------- メールを読む

def _header(msg, name: str) -> "str | None":
    value = msg.get(name)
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _part_text(part) -> str:
    try:
        content = part.get_content()
    except (LookupError, ValueError, UnicodeError):
        content = part.get_payload(decode=True) or b""
    if isinstance(content, bytes):
        content = _decode_bytes(content, part.get_content_charset())
    return content if isinstance(content, str) else ""


def _decode_bytes(data: bytes, charset: "str | None") -> str:
    """宣言された文字コードで読む。知らない名前・読めない並びのときだけ UTF-8 に落とす。"""
    if charset:
        try:
            return data.decode(charset)
        except (LookupError, ValueError):  # UnicodeDecodeError は ValueError に含まれる
            pass
    return data.decode("utf-8", errors="replace")


def _anchor_to_text(m: "re.Match[str]") -> str:
    href = m.group(1) or m.group(2) or m.group(3) or ""
    return f"{m.group(4)} ( {href} )" if href else m.group(4)


def _html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", markup)
    # リンク先は本文から URL を拾えるよう残す。
    text = _ANCHOR.sub(_anchor_to_text, text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return html.unescape(text)


def _body_text(msg) -> str:
    """text/plain を優先して本文を返す。無ければ text/html からタグを落とす。"""
    plain: "list[str]" = []
    rich: "list[str]" = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            rich.append(_html_to_text(_part_text(part)))
    text = "\n".join(plain) if any(p.strip() for p in plain) else "\n".join(rich)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_signature(body: str) -> str:
    """「-- 」以降（View it on GitLab / 配信理由）を落とす。"""
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() == "--" or line.startswith("View it on GitLab"):
            return "\n".join(lines[:i])
        if line.startswith("You're receiving this email because"):
            return "\n".join(lines[:i])
    return body


def _clip(text: str, limit: int = SUMMARY_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _to_int(value: "str | None") -> "int | None":
    if value and value.strip().isdigit():
        return int(value.strip())
    return None


# --------------------------------------------------------------------------- 項目を取り出す

def _sender_name(msg) -> str:
    """From の表示名（"山田 太郎 (@t-yamada)" のような、アドレスを除いた部分）。"""
    header = msg.get("From")
    if header is None:
        return ""
    try:
        addresses = getattr(header, "addresses", None)
        if addresses:
            return addresses[0].display_name or ""
    except Exception:  # 崩れた From は下の素朴な読み方に任せる
        pass
    return parseaddr(str(header))[0]


def _received_at(msg) -> "str | None":
    raw = msg.get("Date")
    if not raw:
        return None
    parsed = getattr(raw, "datetime", None)
    if parsed is not None:
        return parsed.isoformat()
    try:
        return parsedate_to_datetime(str(raw)).isoformat()
    except (TypeError, ValueError):
        return None


def _split_subject(subject: str) -> "list[str]":
    return [seg.strip() for seg in _REPLY_PREFIX.sub("", subject).split(" | ")]


def _summary(body: str) -> "str | None":
    lines = [ln.strip() for ln in _strip_signature(body).split("\n")]
    lead_dropped = False
    kept: "list[str]" = []
    for line in lines:
        if not line:
            continue
        if not kept and not lead_dropped and _LEAD_LINE.match(
                re.sub(r"\s{2,}", " ", _INLINE_URL.sub("", line))):
            lead_dropped = True
            continue
        if not kept and (_META_LINE.match(line) or _URL.fullmatch(line)):
            continue
        kept.append(line)
    text = " ".join(kept).strip()
    return _clip(text) if text else None


def _field_line(body: str, label: str) -> "str | None":
    """「Label: 値 ( URL )」の形の行から値だけを返す。"""
    m = re.search(rf"^{re.escape(label)}:\s*(.+)$", body, re.I | re.M)
    if not m:
        return None
    value = re.sub(r"\s*\(\s*https?://[^)]*\)\s*", " ", m.group(1)).strip()
    return value or None


def _pipeline_jobs(body: str) -> "list[str]":
    """失敗したジョブを「ステージ/ジョブ名」で返す。"""
    jobs: "list[str]" = []
    stage = None
    for line in body.split("\n"):
        line = line.strip()
        m = re.match(r"^Stage:\s*(.+)$", line, re.I)
        if m:
            stage = m.group(1).strip()
            continue
        m = re.match(r"^Name:\s*(.+)$", line, re.I)
        if m:
            name = m.group(1).strip()
            jobs.append(f"{stage}/{name}" if stage else name)
            stage = None
    return jobs


def _pipeline_summary(body: str) -> "str | None":
    parts: "list[str]" = []
    commit = _field_line(body, "Commit Message")
    if commit:
        parts.append(commit)
    jobs = _pipeline_jobs(body)
    if jobs:
        parts.append("失敗したジョブ: " + ", ".join(jobs))
    return _clip(" / ".join(parts)) if parts else None


def _pipeline_actor(body: str) -> "str | None":
    m = re.search(r"triggered by\s+(.+?)\s*(?:\(\s*https?://|$)", body, re.I | re.M)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return _field_line(body, "Commit Author")


def _first_url(body: str, pattern: "re.Pattern[str]") -> "str | None":
    m = pattern.search(body)
    return m.group(0).rstrip(".,;") if m else None


def parse_message(msg) -> dict:
    """email.message.EmailMessage からイベント（dict）を作る。"""
    subject = _header(msg, "Subject")
    sender = _header(msg, "From")
    body = _body_text(msg)
    segments = _split_subject(subject or "")

    actor = _USER_SUFFIX.sub("", _sender_name(msg)).strip() or None
    if actor and actor.lower() == "gitlab":
        actor = None  # システム発の通知（パイプラインなど）は差出人が人ではない

    project = _header(msg, "X-GitLab-Project-Path") or _header(msg, "X-GitLab-Project")
    if not project and len(segments) > 1:
        project = segments[0]

    event = {
        "kind": "unknown",
        "project": project,
        "title": None,
        "number": None,
        "actor": actor,
        "url": None,
        "status": None,
        "branch": None,
        "summary": None,
        "received_at": _received_at(msg),
        "subject": subject,
        "sender": sender,
    }

    reason = (_header(msg, "X-GitLab-NotificationReason") or "").lower()
    is_reply = bool(msg.get("In-Reply-To")) or bool(_REPLY_PREFIX.match(subject or ""))
    ref = _SUBJECT_REF.search(segments[-1]) if segments else None
    pipeline_subject = next(
        (m for m in (_SUBJECT_PIPELINE.match(seg) for seg in segments) if m), None)

    pipeline_id = _to_int(_header(msg, "X-GitLab-Pipeline-Id"))
    mr_iid = _to_int(_header(msg, "X-GitLab-MergeRequest-IID"))
    issue_iid = _to_int(_header(msg, "X-GitLab-Issue-IID"))
    if mr_iid is None and issue_iid is None and pipeline_id is None and ref:
        if ref.group(1) == "!":
            mr_iid = int(ref.group(2))
        else:
            issue_iid = int(ref.group(2))

    if pipeline_id is not None or pipeline_subject:
        status = (_header(msg, "X-GitLab-Pipeline-Status") or "").lower() or None
        if not status and pipeline_subject:
            status = _PIPELINE_STATUS_WORDS.get(pipeline_subject.group("status").lower())
        url = _first_url(body, _PIPELINE_URL)
        if pipeline_id is None and url:
            pipeline_id = int(_PIPELINE_URL.search(url).group(1))
        event.update(
            kind="pipeline_failed" if status == "failed" else "unknown",
            title=pipeline_subject.group(0) if pipeline_subject else _strip_ref(segments),
            number=pipeline_id,
            actor=_pipeline_actor(body) or actor,
            url=url or _first_url(body, _URL),
            status=status,
            branch=_header(msg, "X-GitLab-Pipeline-Ref")
            or (pipeline_subject.group("ref") if pipeline_subject else None)
            or _field_line(body, "Branch"),
            summary=_pipeline_summary(body) or _summary(body),
        )
        return event

    if mr_iid is not None:
        branches = _BRANCHES.search(body)
        requested = (
            reason in ("review_requested", "mentioned", "directly_addressed")
            or re.search(r"requested (a new )?review|was requested to review|"
                         r"requested you to review", body, re.I) is not None
        )
        event.update(
            kind="mr_review_requested" if requested else "unknown",
            title=_strip_ref(segments),
            number=mr_iid,
            url=_first_url(body, _MR_URL) or _first_url(body, _URL),
            status="review_requested" if requested else None,
            branch=branches.group(1) if branches else None,
            summary=_summary(body),
        )
        return event

    if issue_iid is not None:
        # 「返信でない」だけでは作成と言えない（担当者・ラベルの変更、クローズも同じ形で届く）。
        # 作成の定型句か、作成を示す通知理由があるときだけ issue_created にする。
        created = (
            _ISSUE_CREATED_PHRASE.search(_strip_signature(body)) is not None
            or (reason in _ISSUE_CREATED_REASONS and not is_reply)
        )
        event.update(
            kind="issue_created" if created else "unknown",
            title=_strip_ref(segments),
            number=issue_iid,
            url=_first_url(body, _ISSUE_URL) or _first_url(body, _URL),
            status="opened" if created else None,
            summary=_summary(body),
        )
        return event

    # GitLab の通知として分類できないメール。件名と本文の頭だけは渡す。
    event.update(
        title=_strip_ref(segments) or subject,
        url=_first_url(body, _URL),
        summary=_summary(body),
    )
    return event


def _strip_ref(segments: "list[str]") -> "str | None":
    """件名からプロジェクト名と末尾の (#12) / (!34) を除いた題名を返す。"""
    if not segments or not any(segments):
        return None
    rest = segments[1:] if len(segments) > 1 else segments
    title = _SUBJECT_REF.sub("", " | ".join(rest)).strip()
    return title or None


def parse_eml(data: "bytes | str") -> dict:
    """.eml の中身（bytes か str）からイベントを作る。読めなければ ParseError。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not data.strip():
        raise ParseError("入力が空です")
    try:
        msg = email.message_from_bytes(data, policy=email.policy.default)
    except Exception as exc:  # 壊れた入力で email が投げる例外は種類が定まらない
        raise ParseError(f"メールとして読めません: {exc}") from exc
    if not msg.keys() or not any(msg.get(h) for h in ("From", "Subject", "Date", "Message-ID")):
        raise ParseError("メールのヘッダー（From / Subject / Date）が見つかりません")
    try:
        return parse_message(msg)
    except Exception as exc:
        raise ParseError(f"メールとして読めません: {exc}") from exc


# --------------------------------------------------------------------------- カードにする

def build_card(event: dict) -> dict:
    """イベントから Teams 向けの Adaptive Card（dict）を作る。"""
    kind = event.get("kind") if event.get("kind") in KINDS else "unknown"
    header = {
        "type": "TextBlock",
        "text": _KIND_LABELS[kind],
        "weight": "Bolder",
        "size": "Small",
        "wrap": True,
    }
    if kind in _KIND_COLORS:
        header["color"] = _KIND_COLORS[kind]
    else:
        header["isSubtle"] = True

    url = event.get("url") or ""
    number = event.get("number")
    is_pipeline = kind == "pipeline_failed" or "/-/pipelines/" in url
    is_mr = kind == "mr_review_requested" or "/-/merge_requests/" in url

    title = event.get("title") or event.get("subject") or "(件名なし)"
    if number is not None and not is_pipeline:
        title = f"{title} ({'!' if is_mr else '#'}{number})"
    body = [header, {
        "type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True,
        "markdown": False,  # メール由来の文字（* や [ ]( ) など）を Markdown として解釈させない
    }]

    facts = [
        ("プロジェクト", event.get("project")),
        ("パイプライン", f"#{number}" if is_pipeline and number is not None else None),
        ("ブランチ", event.get("branch")),
        ("状態", event.get("status")),
        ("操作した人", event.get("actor")),
        ("受信", event.get("received_at")),
    ]
    fact_items = [{"title": k, "value": str(v), "markdown": False}
                  for k, v in facts if v not in (None, "")]
    if fact_items:
        body.append({"type": "FactSet", "facts": fact_items})
    if event.get("summary"):
        body.append({"type": "TextBlock", "text": event["summary"], "wrap": True,
                     "spacing": "Medium", "markdown": False})

    card = {
        "type": "AdaptiveCard",
        "$schema": CARD_SCHEMA,
        "version": CARD_VERSION,
        "body": body,
    }
    if event.get("url"):
        card["actions"] = [{"type": "Action.OpenUrl", "title": "GitLab で開く", "url": event["url"]}]
    return card


# --------------------------------------------------------------------------- CLI

def _read_input(path: "str | None") -> bytes:
    if path in (None, "-"):
        return sys.stdin.buffer.read()
    with open(path, "rb") as fh:
        return fh.read()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gitlab_mail_teams.py",
        description="GitLab の通知メール（.eml）をイベントの JSON か Teams の Adaptive Card に変換する。",
    )
    parser.add_argument("eml", nargs="?", default="-",
                        help=".eml のパス（省略するか - で標準入力）")
    parser.add_argument("--format", choices=("json", "card"), default="json",
                        help="json = イベント（既定） / card = Adaptive Card")
    args = parser.parse_args(argv)

    try:
        event = parse_eml(_read_input(args.eml))
    except OSError as exc:
        print(f"error: 読めません: {exc}", file=sys.stderr)
        return 2
    except ParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = build_card(event) if args.format == "card" else event
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    # 端末の文字コードに左右されないよう、常に UTF-8 で出す（Windows の cp932 対策）。
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        sys.stdout.flush()
        stream.write(text.encode("utf-8"))
        stream.flush()
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
