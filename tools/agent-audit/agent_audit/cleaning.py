"""cleaning — セッションログ本文のノイズ除去（決定的・LLM 不使用。設計 §4.4）。

「何がノイズか」は CLI ごとの作法で、「その作法はバージョンで変わる」——この 2 つを
format（readers.py）と同型の分業で吸収する。ルールの実装はここに閉じた enum で 1 つ
（C7）。どのルールをどの引数で使うかは `agents/<name>.json` の `session_log.clean` が
宣言する（受入条件: CLI の作法の変更は JSON 1 ファイルで完結する）。

フェイルの向きは scrub と逆——確実にノイズと判ったものだけ消し、知らない構造は残す。
未知のルール名・閉じタグ欠落・不正な正規表現は、そのルールだけスキップして warn を
呼ぶ（呼び出し側が collect の警告へ束ねる）。消し過ぎて蒸留の証跡を壊さない。
"""
from __future__ import annotations

import re

RULE_KINDS = ("drop-line", "drop-message", "strip-tag", "strip-regex", "truncate-message")

_TAG_RE_CACHE: "dict[str, re.Pattern]" = {}


def _noop_warn(_msg: str) -> None:
    return None


def _dotted_get(obj, key: str):
    cur = obj
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def probe_version(objs: "list[dict]", version_key: "str | None", *, limit: int = 50) -> str:
    """先頭 limit 件から version_key（dotted 可）を決定的に探す。見つからなければ ''。"""
    if not version_key:
        return ""
    for obj in objs[:limit]:
        if not isinstance(obj, dict):
            continue
        v = _dotted_get(obj, version_key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return ""


def _version_tuple(v: str) -> "tuple[int, ...]":
    out = []
    for part in v.split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


def _cmp_one(actual: str, token: str) -> bool:
    m = re.match(r"(>=|<=|>|<|=)?\s*([0-9]+(?:\.[0-9]+)*)$", token.strip())
    if not m:
        return False
    op = m.group(1) or "="
    target = _version_tuple(m.group(2))
    have = _version_tuple(actual)
    n = max(len(target), len(have))
    have = have + (0,) * (n - len(have))
    target = target + (0,) * (n - len(target))
    if op == "=":
        return have == target
    if op == ">=":
        return have >= target
    if op == "<=":
        return have <= target
    if op == ">":
        return have > target
    return have < target   # "<"


def version_matches(actual: str, when: str) -> bool:
    """when は空白区切りの AND（例 '>=2.0 <3.0'）。actual が空なら常に不一致（既定へ倒す）。"""
    if not actual or not when:
        return False
    return all(_cmp_one(actual, tok) for tok in when.split() if tok)


def select_rules(clean_spec: "dict | None", version: str) -> "tuple[list, str]":
    """(rules, matched_when) を返す。versions を先勝ちで評価し、当たったエントリの
    rules が既定を丸ごと置き換える（部分マージはしない）。未検出・不一致は既定 rules。"""
    if not isinstance(clean_spec, dict):
        return [], ""
    default_rules = clean_spec.get("rules")
    default_rules = default_rules if isinstance(default_rules, list) else []
    for entry in clean_spec.get("versions") or []:
        if not isinstance(entry, dict):
            continue
        when = entry.get("when")
        rules = entry.get("rules")
        if not isinstance(when, str) or not isinstance(rules, list):
            continue
        if version_matches(version, when):
            return rules, when
    return default_rules, ""


def should_drop_line(obj: dict, rules: "list[dict]", *, warn=_noop_warn) -> bool:
    """drop-line ルールに 1 つでも一致すればそのエントリ全体を落とす。
    jsonl-dir は生ログ 1 行、kiro-sqlite は messages 配列の 1 要素が対象（§4.4）。"""
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("rule") != "drop-line":
            continue
        field = rule.get("field")
        if not isinstance(field, str) or not field:
            warn("drop-line: field 未指定のルールをスキップしました")
            continue
        val = _dotted_get(obj, field)
        if "equals" in rule and val == rule["equals"]:
            return True
        allowed = rule.get("in")
        if isinstance(allowed, list) and val in allowed:
            return True
    return False


def _tag_pattern(tag: str) -> re.Pattern:
    pat = _TAG_RE_CACHE.get(tag)
    if pat is None:
        pat = re.compile(r"<" + re.escape(tag) + r"(?:\s[^>]*)?>.*?</" + re.escape(tag) + r">",
                          re.DOTALL)
        _TAG_RE_CACHE[tag] = pat
    return pat


def clean_message_text(text: str, rules: "list[dict]", *, warn=_noop_warn) -> "str | None":
    """本文（1 メッセージ）へノイズ除去ルールを順に適用する。drop-message に一致すれば
    None（メッセージ全体を捨てる）。それ以外は変換後のテキストを返す（空白だけなら None）。"""
    if not rules:
        return text if text and text.strip() else None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        kind = rule.get("rule")
        if kind == "drop-message":
            prefixes = rule.get("prefixes") or []
            contains = rule.get("contains") or []
            if any(isinstance(p, str) and text.startswith(p) for p in prefixes):
                return None
            if any(isinstance(c, str) and c in text for c in contains):
                return None
        elif kind == "strip-tag":
            for tag in rule.get("tags") or []:
                if isinstance(tag, str) and tag:
                    text = _tag_pattern(tag).sub("", text)
        elif kind == "strip-regex":
            pattern = rule.get("pattern")
            if not isinstance(pattern, str):
                continue
            try:
                text = re.compile(pattern, re.DOTALL).sub("", text)
            except re.error:
                warn(f"strip-regex: 不正な正規表現をスキップしました: {pattern!r}")
        elif kind == "truncate-message":
            max_chars = rule.get("max_chars")
            if isinstance(max_chars, int) and max_chars > 0 and len(text) > max_chars:
                cut = len(text) - max_chars
                text = text[:max_chars] + f"\n…[truncated {cut} chars]"
        elif kind == "drop-line":
            continue        # 本文の段では扱わない（行の段でのみ意味を持つ）
        else:
            warn(f"未知の clean ルールをスキップしました: {kind!r}")
    return text if isinstance(text, str) and text.strip() else None
