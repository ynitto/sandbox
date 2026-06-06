#!/usr/bin/env python3
"""persona privacy gate — Moltbook の公開前フィルタ（設計書 11 章）.

外向き公開（publish / ask）は不可逆。本 gate を単一のチョークポイントとして、
個人情報・秘匿情報の漏えいを止める。原則 **default-deny**（迷ったら出さない）。

2段フィルタ:
  [1] 来歴（provenance）: source_layer == persona は無条件 BLOCK
  [2] 内容スクラブ（content）:
        - シークレット（トークン/鍵） → BLOCK
        - ユーザー参照文（嗜好・専門など persona 漏れ） → BLOCK
        - PII（メール/電話）・社内識別子（パス/内部IP/内部ホスト） → redact
        - redact 後に内容が崩れる/空 → BLOCK

CLI:
    python privacy_gate.py check --source-layer ltm --infile cand.md
      exit 0: ALLOW（スクラブ済み本文を stdout）
      exit 2: BLOCK（理由を stderr）

Python API:
    from privacy_gate import evaluate
    result = evaluate(text, source_layer="ltm")
    if result.allowed: publish(result.scrubbed)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
from dataclasses import dataclass, field

PUBLISHABLE_LAYERS = {"ltm", "ltm-home", "ltm-shared", "wiki", "idd", "gitlab-idd"}
BLOCKED_LAYERS = {"persona", "persona-use"}

# --- secret patterns (見つかったら BLOCK：redact では公開しない) --------------
_SECRET_PATTERNS = [
    re.compile(r"glpat-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)PRIVATE-TOKEN\s*[:=]\s*\S+"),
]

# --- user-referential statements (persona 漏れ → BLOCK) ----------------------
_USER_REF_PATTERNS = [
    re.compile(r"ユーザー(?:は|が|の|さんは)[^。\n]{0,30}(?:好(?:む|き)|嗜好|苦手|専門|得意|スタイル|希望|要望|傾向)"),
    re.compile(r"(?:ユーザー|あなた)は[^。\n]{0,30}を(?:好む|好み|希望)"),
    re.compile(r"個人の(?:好み|嗜好|スタイル)"),
    re.compile(r"(?i)\buser\b[^.\n]{0,30}\b(?:prefers?|likes?|wants?|favou?rite|is an expert|speciali[sz]es)\b"),
]

# --- PII / internal identifiers (redact) -------------------------------------
_REDACTIONS = [
    ("email", re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")),
    ("phone", re.compile(r"\b(?:0\d{1,4}-\d{1,4}-\d{3,4}|\+\d{1,3}[\d \-]{7,})\b")),
    ("unix-home", re.compile(r"/home/[^/\s]+")),
    ("win-home", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+")),
    ("private-ip", re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
    ("internal-host", re.compile(r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:internal|local|intranet|corp)\b")),
]

# redact 後に本文がこの割合未満になったら「中核が削られた」とみなし BLOCK
_MIN_RETAIN_RATIO = 0.4


@dataclass
class GateResult:
    allowed: bool
    scrubbed: str = ""
    reasons: list = field(default_factory=list)
    redactions: list = field(default_factory=list)
    source_layer: str = ""

    def summary(self) -> str:
        head = "ALLOW" if self.allowed else "BLOCK"
        parts = [head]
        if self.reasons:
            parts.append("理由: " + "; ".join(self.reasons))
        if self.redactions:
            kinds = ", ".join(sorted(set(self.redactions)))
            parts.append(f"redacted: {kinds}")
        return " / ".join(parts)


def _nonspace_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def evaluate(text: str, *, source_layer: str = "", strict: bool = True) -> GateResult:
    """Evaluate *text* for outward publication.

    Returns a GateResult. With ``strict`` (default), ambiguous/empty results
    are denied.
    """
    layer = (source_layer or "").strip().lower()

    # [1] provenance
    if layer in BLOCKED_LAYERS:
        return GateResult(False, reasons=[f"来歴 {layer}: persona 層は公開しない"], source_layer=layer)
    if strict and layer and layer not in PUBLISHABLE_LAYERS:
        return GateResult(False, reasons=[f"未知の source_layer '{layer}'（default-deny）"], source_layer=layer)
    if strict and not layer:
        return GateResult(False, reasons=["source_layer 未指定（default-deny）"], source_layer=layer)

    # [2a] secrets → BLOCK
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return GateResult(False, reasons=["シークレット（トークン/鍵）を検出"], source_layer=layer)

    # [2b] user-referential → BLOCK
    for pat in _USER_REF_PATTERNS:
        if pat.search(text):
            return GateResult(False, reasons=["ユーザー参照文（persona 漏れ）を検出"], source_layer=layer)

    # [2c] PII / internal → redact
    scrubbed = text
    redactions: list = []
    for kind, pat in _REDACTIONS:
        if pat.search(scrubbed):
            scrubbed = pat.sub(f"[REDACTED:{kind}]", scrubbed)
            redactions.append(kind)

    # [2d] self-containment / default-deny
    if _nonspace_len(scrubbed) == 0:
        return GateResult(False, reasons=["スクラブ後に本文が空"], redactions=redactions, source_layer=layer)
    if redactions and _nonspace_len(scrubbed.replace("[REDACTED:", "")) < _nonspace_len(text) * _MIN_RETAIN_RATIO:
        return GateResult(False, reasons=["redact で本文の中核が失われた"], redactions=redactions, source_layer=layer)

    return GateResult(True, scrubbed=scrubbed, redactions=redactions, source_layer=layer)


def _audit(result: GateResult, source_id: str) -> None:
    path = os.environ.get("MOLTBOOK_AUDIT_LOG")
    if not path:
        return
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    line = f"{ts}\t{result.source_layer}\t{source_id}\t{result.summary()}\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Moltbook 公開前 privacy gate")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="本文を評価する")
    c.add_argument("--source-layer", required=True,
                   help="来歴レイヤ: ltm / wiki / idd / persona など")
    c.add_argument("--source-id", default="-", help="監査用の識別子（任意）")
    c.add_argument("--infile", help="入力ファイル（省略時は標準入力）")
    c.add_argument("--no-strict", action="store_true", help="default-deny を緩める（非推奨）")
    args = p.parse_args(argv)

    text = (open(args.infile, encoding="utf-8").read() if args.infile else sys.stdin.read())
    result = evaluate(text, source_layer=args.source_layer, strict=not args.no_strict)
    _audit(result, args.source_id)

    if result.allowed:
        sys.stdout.write(result.scrubbed)
        if result.redactions:
            print(f"\n[gate] {result.summary()}", file=sys.stderr)
        return 0
    print(f"[gate] {result.summary()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
