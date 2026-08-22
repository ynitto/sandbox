"""決定的スクラバ — export 系出力（report / tasks / --json）が必ず通す（仕様書 §5・不変条件 6）。

資格情報らしいトークンの伏せ字化と、絶対パスのホーム相対化。LLM は使わない。
規則は「見つけたら必ず伏せる」方向にだけ働く（伏せ過ぎは許容・漏れは許容しない）。
"""
from __future__ import annotations

import os
import re

# 資格情報らしいパターン（値の形で判る系 + key=value の系）。
_SECRET_VALUE_RES = (
    re.compile(r"\b(?:gh[pousr]|glpat|xox[baprs])-[A-Za-z0-9_-]{8,}\b"),   # GitHub/GitLab/Slack トークン
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),                              # APIキー（sk- 系）
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                    # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),  # JWT
)
_SECRET_KV_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|bearer)"
    r"([=:]\s*)(\S+)")

_HOME_RE = None


def _home_pattern() -> re.Pattern:
    global _HOME_RE
    if _HOME_RE is None:
        home = os.path.expanduser("~")
        _HOME_RE = re.compile(re.escape(home) + r"(?=[/\s'\"]|$)")
    return _HOME_RE


def scrub_text(text: str) -> str:
    if not text:
        return text
    out = text
    for rex in _SECRET_VALUE_RES:
        out = rex.sub("[REDACTED]", out)
    out = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
    out = _home_pattern().sub("~", out)
    return out


def scrub_obj(obj):
    """dict / list / str を再帰的にスクラブした複製を返す。"""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        return [scrub_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    return obj
