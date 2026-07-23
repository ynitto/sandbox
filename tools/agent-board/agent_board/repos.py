"""repos — リポジトリレジストリ（repos.schema.json）の正規化と identity 照合。

ノードは自分が担当するリポジトリを repos.schema.json 形で宣言し、板は公示の
workspace.url / requires.repos をノード宣言と (url, path, base) identity で突き合わせて
入札資格を判定する（設計 §5.1）。正規化規則は agent-project の charter._registry_entry と
同形（『同じ仕様・別実装』— ツール間はデータ契約のみで結合）。
"""
from __future__ import annotations

import re


def _globs(v) -> "list[str]":
    if isinstance(v, str):
        return [g for g in re.split(r"[,\s]+", v) if g]
    return [str(g) for g in (v or [])]


def normalize_entry(name: str, e: dict) -> dict:
    """repos スキーマの 1 エントリを内部形へ正規化する（agent-project と同形）。"""
    e = e or {}
    owns = _globs(e.get("owns"))
    base = str(e.get("base", "") or "")
    return {
        "name": str(name),
        "url": str(e.get("url", "") or ""),
        "base": base,
        "target": str(e.get("target", "") or "") or base,
        "path": str(e.get("path", "") or "").strip("/"),
        "local": str(e.get("local", "") or "").strip(),
        "readonly": bool(e.get("readonly")) or not owns,
        "owns": owns,
    }


def normalize_registry(registry) -> "list[dict]":
    """レジストリ（{name: entry} マッピング、または [{name,...}] のリスト）を正規化する。
    トップレベルの `_` 接頭辞キー（_meta 等）はメタデータ予約でスキップする。"""
    specs = []
    if isinstance(registry, dict):
        for name, entry in registry.items():
            if str(name).startswith("_"):
                continue
            if isinstance(entry, dict):
                specs.append(normalize_entry(name, entry))
    elif isinstance(registry, list):
        for entry in registry:
            if isinstance(entry, dict) and entry.get("name"):
                specs.append(normalize_entry(entry["name"], entry))
    return specs


def _norm_url(url: str) -> str:
    """比較用に URL を軽く正規化する（末尾スラッシュ・.git・大小の揺れを吸収）。"""
    u = str(url or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def _identity(spec: dict) -> tuple:
    return (_norm_url(spec.get("url", "")), spec.get("path", ""), spec.get("base", ""))


def covers(specs: "list[dict]", want: dict, *, writable: bool = True) -> bool:
    """ノードの specs が want（{url, path?, base?}）を担当しているか。
    url が一致し、path/base が指定されていればそれも一致（identity は (url, path, base)）。
    writable=True なら readonly（参照）エントリは担当とみなさない。"""
    wu = _norm_url(want.get("url", ""))
    if not wu:
        return False
    wpath = str(want.get("path", "") or "").strip("/")
    wbase = str(want.get("base", "") or "")
    for s in specs:
        if writable and s.get("readonly"):
            continue
        if _norm_url(s.get("url", "")) != wu:
            continue
        if wpath and s.get("path", "") != wpath:
            continue
        if wbase and s.get("base", "") and s.get("base", "") != wbase:
            continue
        return True
    return False


def covers_ref(specs: "list[dict]", ref: str) -> bool:
    """requires.repos の 1 要素（url または repo 名）をノードが担当しているか。"""
    ref = str(ref or "").strip()
    if not ref:
        return False
    for s in specs:
        if s.get("readonly"):
            continue
        if s.get("name") == ref:
            return True
        if _norm_url(s.get("url", "")) == _norm_url(ref):
            return True
    return False
