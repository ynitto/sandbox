"""memory-store — 記憶 3 層 + 共有路のメタデータ収集と決定的集計（LLM 不使用）。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.1

読むのは **メタデータだけ**（frontmatter・件数・mtime・索引・ログ）で、記憶の本文は
records にも集計にも入れない（設計 §3.1 の excerpt_ref 方式と同じ規律）。persona は
**件数と滞留日数だけ**をレコード化し、タイトルも本文もノード内の記録にすら残さない
（C1。ユーザーモデルは集計値でも外へ出さない）。

書くのは audit ストアだけ——記憶ファイルには触れない（設計 不変条件 1）。整理の実行は
各スキルのスクリプトが担い、ここは「測る者」に徹する。
"""
from __future__ import annotations

import glob
import json
import os
import re

from .store import home_relative, record_id
from .util import parse_iso, utc_day

DAY = 86400.0

LAYERS = ("ltm", "wiki", "persona", "moltbook")

# 記憶レイヤの既定しきい値（設定で上書きできる。CONFIG_DEFAULTS と対）
DEFAULT_DORMANT_DAYS = 30          # access_count=0 のまま眠っている日数（cleanup の既定と同じ）
DEFAULT_SHARE_THRESHOLD = 70       # ltm の昇格候補ライン（memory-format.md の semi-auto 閾値）
DEFAULT_RETENTION_RISK = 0.3       # 忘却リスク帯（retention_score がこれ未満）

_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_QUERY_ENTRY_RE = re.compile(r"^- \*\*(?P<query>.+?)\*\*(?P<rest>.*)$")
_PERSONA_UPDATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-update\.md$")
_WIKI_SHORT_PAGE_CHARS = 100       # wiki_lint.py の EMPTY_PAGE_THRESHOLD と同じ判定


class MemoryStoreError(RuntimeError):
    """設定された記憶ストアが読めない（collect の SourceError へ変換して exit 2）。"""


# -- skill-registry.json からの既定解決（agent-audit.yaml との二重メンテナンス回避） -----
#
# 各スキルは自分の保存先を既に skill-registry.json の skill_configs に持っている
# （wiki-use.wiki_root / persona-use.persona_home / moltbook-use.home。ltm-use は
# 保存先を持たず常に `{agent_home}/memory/home`）。agent-audit.yaml の `memory_stores:`
# へ同じパスを再度書かせると、スキル側の設定を変えたときここだけ古いまま取り残される
# （agent-audit の自己更新（update.py の find_skill_registry）が既に踏んでいる同じ探索
# 契約——KIRO_SKILL_REGISTRY を尊重し、無ければ既知のエージェントホームを順に見る）。
_KNOWN_AGENT_HOME_DIRS = (".agents", ".kiro", ".claude", ".copilot", ".codex")


def _agent_home_candidates() -> "list[str]":
    """skill-registry.json を持つエージェントホームを列挙する。

    `KIRO_SKILL_REGISTRY` が設定されていればそこだけ（複数ホームの探索はしない——
    明示指定は 1 か所を指す契約）。無指定なら `~/.claude` 等を全部調べ、複数の
    エージェント CLI を並行運用している構成（ltm_dirs が複数になる想定）に対応する。
    """
    env = os.environ.get("KIRO_SKILL_REGISTRY")
    if env:
        p = os.path.abspath(os.path.expanduser(env))
        base = p if os.path.isdir(p) else os.path.dirname(p)
        return [base] if os.path.isfile(os.path.join(base, "skill-registry.json")) else []
    homes = []
    for d in _KNOWN_AGENT_HOME_DIRS:
        base = os.path.join(os.path.expanduser("~"), d)
        if os.path.isfile(os.path.join(base, "skill-registry.json")):
            homes.append(base)
    return homes


def discover_memory_stores() -> dict:
    """`memory_stores:` の既定値を skill-registry.json から解決する。

    agent-audit.yaml で明示された値が常に優先される（この関数の戻り値は
    未設定のキーを埋める既定値としてのみ使う）。読めない・壊れた registry は
    黙って無視する——測定のために他ツールの設定ファイルを検証しに行かない。
    """
    ltm_dirs: "list[str]" = []
    wiki_root = persona_home = moltbook_home = ""
    for home in _agent_home_candidates():
        reg = _read_json(os.path.join(home, "skill-registry.json"))
        cfgs = (reg or {}).get("skill_configs")
        cfgs = cfgs if isinstance(cfgs, dict) else {}

        ltm_home = os.path.join(home, "memory", "home")   # ltm-use は保存先を持たず固定
        if os.path.isdir(ltm_home):
            ltm_dirs.append(ltm_home)

        if not wiki_root:
            wr = (cfgs.get("wiki-use") or {}).get("wiki_root")
            if wr:
                wiki_root = os.path.abspath(os.path.expanduser(str(wr)))
        if not persona_home:
            ph = (cfgs.get("persona-use") or {}).get("persona_home")
            if ph:
                persona_home = os.path.abspath(os.path.expanduser(str(ph)))
        if not moltbook_home:
            mh = (cfgs.get("moltbook-use") or {}).get("home")
            candidate = (os.path.abspath(os.path.expanduser(str(mh))) if mh
                        else os.path.join(home, ".moltbook"))
            if os.path.isdir(candidate):
                moltbook_home = candidate
    return {"ltm_dirs": ltm_dirs, "wiki_root": wiki_root,
            "persona_home": persona_home, "moltbook_home": moltbook_home}


# -- 設定の解決 ---------------------------------------------------------------

def memory_stores_config(args) -> dict:
    """`memory_stores:` を正規化する。未設定のキーは skill-registry.json からの
    自動発見で埋める（二重メンテナンス回避）。発見も無ければ空のまま返す（= 未収集）。
    """
    cfg = getattr(args, "memory_stores", None) or {}
    cfg = cfg if isinstance(cfg, dict) else {}
    discovered = discover_memory_stores()

    dirs = cfg.get("ltm_dirs") or []
    if isinstance(dirs, str):
        dirs = [dirs]
    ltm_dirs = [str(d) for d in dirs if str(d)] or discovered["ltm_dirs"]
    return {
        "ltm_dirs": ltm_dirs,
        "wiki_root": str(cfg.get("wiki_root") or discovered["wiki_root"] or ""),
        "persona_home": str(cfg.get("persona_home") or discovered["persona_home"] or ""),
        "moltbook_home": str(cfg.get("moltbook_home") or discovered["moltbook_home"] or ""),
    }


def _resolve(path: str, label: str) -> str:
    """明示設定されたストアは存在しなければ fail-close（不変条件 3）。"""
    p = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isdir(p):
        raise MemoryStoreError(f"memory_stores.{label} に設定されたパスがありません: {path}")
    return p


def _thresholds(args) -> dict:
    def num(key, dflt):
        try:
            return type(dflt)(getattr(args, key, None) if getattr(args, key, None) is not None
                              else dflt)
        except (TypeError, ValueError):
            return dflt
    return {
        "dormant_days": num("memory_dormant_days", DEFAULT_DORMANT_DAYS),
        "share_threshold": num("memory_share_threshold", DEFAULT_SHARE_THRESHOLD),
        "retention_risk": num("memory_retention_risk", DEFAULT_RETENTION_RISK),
    }


# -- frontmatter（PyYAML 非依存・決定的）--------------------------------------

def parse_frontmatter(text: str) -> dict:
    """先頭 `---` ブロックの単層キーだけを読む小さなパーサ。

    記憶・wiki ページの frontmatter は単層のスカラと短いリストだけなので、
    PyYAML を必須にしない（設定ファイル以外に依存を増やさない）。読めない行は
    黙って飛ばす——測定のために記憶ファイルを直しに行かない。
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict = {}
    key = None
    for raw in text[3:end].splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t", "-"):
            # ブロックリストの項目（`  - x` / `- x`）は直前のキーへ足す
            item = line.lstrip().lstrip("-").strip()
            if key and item:
                out.setdefault(key, [])
                if isinstance(out[key], list):
                    out[key].append(_scalar(item))
            continue
        name, sep, value = line.partition(":")
        if not sep:
            continue
        key = name.strip()
        value = value.split(" #", 1)[0].strip() if " #" in value else value.strip()
        out[key] = [] if value == "" else _scalar(value)
    return out


def _scalar(value: str):
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(part) for part in v[1:-1].split(",") if part.strip()]
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return ""
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _num(value, dflt=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return dflt


def _age_days(value, now: float) -> "float | None":
    """`YYYY-MM-DD` / ISO8601 からの経過日数。読めなければ None。"""
    epoch = parse_iso(str(value)) if value not in (None, "", []) else None
    if epoch is None:
        return None
    return max(0.0, (now - epoch) / DAY)


# -- ltm ----------------------------------------------------------------------

def scan_ltm(path: str, *, now: float, thresholds: dict) -> dict:
    """ltm-use の `memory/home` を frontmatter だけで測る（本文は読まない）。"""
    files = sorted(p for p in glob.glob(os.path.join(glob.escape(path), "**", "*.md"),
                                        recursive=True) if os.path.isfile(p))
    total = active = archived = deprecated = 0
    publish_waiting = dormant = never_accessed = risk = 0
    categories: "dict[str, int]" = {}
    bands = {"0.0-0.3": 0, "0.3-0.6": 0, "0.6-0.85": 0, "0.85-1.0": 0, "未設定": 0}
    labels: "list[str]" = []
    stalest = 0.0
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
        except OSError:
            continue
        fm = parse_frontmatter(head)
        if not fm.get("id") and not fm.get("title"):
            continue                       # 記憶ファイルではない（README 等）
        total += 1
        status = str(fm.get("status") or "active")
        active += status == "active"
        archived += status == "archived"
        deprecated += status == "deprecated"
        category = os.path.basename(os.path.dirname(fpath)) or "general"
        categories[category] = categories.get(category, 0) + 1

        retention = fm.get("retention_score")
        if retention in (None, "", []):
            bands["未設定"] += 1
        else:
            r = _num(retention, 1.0)
            band = ("0.0-0.3" if r < 0.3 else "0.3-0.6" if r < 0.6
                    else "0.6-0.85" if r < 0.85 else "0.85-1.0")
            bands[band] += 1
            if r < thresholds["retention_risk"] and \
                    str(fm.get("importance") or "normal") not in ("critical", "high"):
                risk += 1

        share = _num(fm.get("share_score"), 0.0)
        published = fm.get("moltbook_published")
        # `moltbook_published` は additive な追記フィールドで、まだ書かれていない世代が
        # 普通にある。**無い = 未公開**として数える（publish 待ちを少なく見せない）。
        if share >= thresholds["share_threshold"] and not published and status == "active":
            publish_waiting += 1

        access = _num(fm.get("access_count"), 0.0)
        age = _age_days(fm.get("updated") or fm.get("created"), now)
        if age is None:
            try:
                age = max(0.0, (now - os.path.getmtime(fpath)) / DAY)
            except OSError:
                age = 0.0
        if access <= 0:
            never_accessed += 1
            if age > thresholds["dormant_days"]:
                dormant += 1
                stalest = max(stalest, age)
        labels.append(f"{fm.get('title') or ''} {fm.get('summary') or ''}".strip())

    clusters, clustered = _similar_clusters(labels)
    index_path = os.path.join(path, ".memory-index.json")
    index = _read_json(index_path)
    index_entries = len(index.get("entries") or []) if isinstance(index, dict) else None
    return {
        "path": home_relative(path),
        "total": total,
        "active": active,
        "archived": archived,
        "deprecated": deprecated,
        "categories": dict(sorted(categories.items())),
        "retention_bands": bands,
        "forgetting_risk": risk,
        "publish_waiting": publish_waiting,
        "never_accessed": never_accessed,
        "dormant": dormant,
        "dormant_oldest_days": round(stalest, 1),
        "similar_clusters": clusters,
        "similar_clustered": clustered,
        "index_entries": index_entries,
        "index_drift": None if index_entries is None else abs(total - index_entries),
        "index_built_at": (index or {}).get("built_at") or "" if isinstance(index, dict) else "",
        "notes": ([] if index_entries is not None else
                  ["索引未構築（build_index.py --force で構築）"]),
    }


def _similar_clusters(labels: "list[str]") -> "tuple[int, int]":
    """title + summary へ audit の決定的クラスタ機構を当て、2 件以上の塊を数える。

    本文は見ない。ここで出るのは**候補の数**であって統合の指示ではない——統合の実行は
    ltm-use の consolidate（非破壊）が担う（計画 §2 の責務分離）。
    """
    from .distill import SIMILARITY_THRESHOLD, _tokens
    clusters: "list[dict]" = []
    for label in sorted(l for l in labels if l):
        toks = _tokens(label)
        if not toks:
            continue
        for c in clusters:
            denom = min(len(c["tokens"]), len(toks))
            if denom and len(c["tokens"] & toks) / denom >= SIMILARITY_THRESHOLD:
                c["size"] += 1
                c["tokens"] = c["tokens"] | toks
                break
        else:
            clusters.append({"tokens": toks, "size": 1})
    grouped = [c for c in clusters if c["size"] >= 2]
    return len(grouped), sum(c["size"] for c in grouped)


# -- wiki ---------------------------------------------------------------------

def scan_wiki(path: str, *, now: float) -> dict:
    """wiki-use の `<wiki_root>` を index・ページ frontmatter・queries.md から測る。"""
    wiki_dir = os.path.join(path, "wiki")
    counts = {"atoms": 0, "topics": 0}
    stems: "set[str]" = set()
    pages: "list[tuple[str, str]]" = []      # (category, path)
    for category in ("atoms", "topics"):
        for fpath in sorted(glob.glob(os.path.join(glob.escape(wiki_dir), category, "*.md"))):
            counts[category] += 1
            stems.add(os.path.splitext(os.path.basename(fpath))[0])
            pages.append((category, fpath))

    index_links: "set[str]" = set()
    index_path = os.path.join(path, "index.md")
    if os.path.isfile(index_path):
        index_links = set(re.findall(r"\[\[([^\]]+)\]\]", _read_text(index_path)))

    new_7d = updated_7d = short_pages = dangling = 0
    for _category, fpath in pages:
        text = _read_text(fpath)
        fm = parse_frontmatter(text)
        created_age = _age_days(fm.get("created"), now)
        updated_age = _age_days(fm.get("updated"), now)
        if created_age is None or updated_age is None:
            try:
                mtime_age = max(0.0, (now - os.path.getmtime(fpath)) / DAY)
            except OSError:
                mtime_age = None
            created_age = created_age if created_age is not None else mtime_age
            updated_age = updated_age if updated_age is not None else mtime_age
        if created_age is not None and created_age <= 7:
            new_7d += 1
        if updated_age is not None and updated_age <= 7:
            updated_7d += 1
        body = text.split("\n---", 1)[1] if text.startswith("---") and "\n---" in text else text
        if len(body.strip()) < _WIKI_SHORT_PAGE_CHARS:
            short_pages += 1
        dangling += sum(1 for link in _WIKI_LINK_RE.findall(text) if link not in stems)

    orphans = sorted(stems - index_links)
    missing = sorted(index_links - stems)
    queries_total, queries_hit = _scan_wiki_queries(os.path.join(wiki_dir, "meta", "queries.md"))
    return {
        "path": home_relative(path),
        "atoms": counts["atoms"],
        "topics": counts["topics"],
        "total": counts["atoms"] + counts["topics"],
        "new_last_7d": new_7d,
        "updated_last_7d": updated_7d,
        "index_orphans": len(orphans),
        "index_missing_files": len(missing),
        "index_drift": len(orphans) + len(missing),
        "short_pages": short_pages,
        "dangling_links": dangling,
        "lint_violations": len(orphans) + short_pages + dangling,
        "queries_total": queries_total,
        "queries_hit": queries_hit,
        "queries_miss": queries_total - queries_hit,
        "queries_hit_rate": (round(queries_hit / queries_total, 3) if queries_total else None),
        "notes": ([] if os.path.isfile(index_path) else ["index.md がありません（wiki_init 未実行）"]),
    }


def _scan_wiki_queries(path: str) -> "tuple[int, int]":
    """queries.md の記録を数える。`→ [[page]]` を伴う行だけを「引けた」とみなす。"""
    total = hit = 0
    for line in _read_text(path).splitlines():
        m = _QUERY_ENTRY_RE.match(line.strip())
        if not m:
            continue
        total += 1
        if "[[" in m.group("rest"):
            hit += 1
    return total, hit


# -- persona ------------------------------------------------------------------

def scan_persona(path: str, *, now: float) -> dict:
    """persona-use の `<persona_home>`。**件数と滞留日数だけ**（C1）。

    ファイル名（日付）も本文もタイトルも出さない。ここで測るのは「観察ログが何日
    滞留しているか」だけで、ユーザーモデルの中身は audit へ 1 文字も入らない。
    """
    managed = sum(1 for name in ("profile.md", "preferences.md", "expertise.md")
                  if os.path.isfile(os.path.join(path, name)))
    pending = 0
    oldest = 0.0
    for name in sorted(os.listdir(path)) if os.path.isdir(path) else []:
        m = _PERSONA_UPDATE_RE.match(name)
        if not m:
            continue
        pending += 1
        age = _age_days("-".join(m.groups()), now)
        if age is not None:
            oldest = max(oldest, age)
    return {
        "path": home_relative(path),
        "managed_files": managed,
        "pending_updates": pending,
        "pending_oldest_days": round(oldest, 1),
        "notes": ([] if managed == 3 else ["管理ファイルが揃っていません（init_persona.py）"]),
    }


# -- moltbook -----------------------------------------------------------------

def scan_moltbook(path: str, *, now: float) -> dict:
    """moltbook-use のローカル状態（outbox / inbox / state.json）。

    タイムラインの未回答メンションと goods は GitLab を引かないと分からない。
    ローカルからは測れないものを 0 と偽らず、`uncollected` に名指しで残す（不変条件 3）。
    """
    outbox = os.path.join(path, "outbox")
    pending = sorted(p for p in glob.glob(os.path.join(glob.escape(outbox), "*.md"))
                     if os.path.isfile(p))
    oldest = 0.0
    for p in pending:
        try:
            oldest = max(oldest, (now - os.path.getmtime(p)) / DAY)
        except OSError:
            pass
    published = len(glob.glob(os.path.join(glob.escape(outbox), "published", "*.md")))
    inbox = len(glob.glob(os.path.join(glob.escape(path), "inbox", "*.md")))
    state = _read_json(os.path.join(path, "state.json")) or {}
    return {
        "path": home_relative(path),
        "outbox_pending": len(pending),
        "outbox_oldest_days": round(oldest, 1),
        "published_local": published,
        "inbox_pending": inbox,
        "replies_today": int(_num(state.get("replies_today"), 0.0)),
        "last_auto_checked_at": str(state.get("last_auto_checked_at") or ""),
        "uncollected": ["未回答メンション", "goods"],
        "notes": ["メンション・goods は GitLab を引かないと測れません（当番が timeline で確認）"],
    }


# -- 集計（report / dashboard 用。決定的・LLM 不使用） ------------------------

def knowledge_summary(args, *, now: float, store=None) -> dict:
    """3 層 + 共有路の健全性を 1 つの dict にまとめる。

    未設定のストアは `configured: false` として**明示的に「未収集」**を返す——
    黙って部分集計を全体と偽らない（不変条件 3）。
    """
    cfg = memory_stores_config(args)
    thresholds = _thresholds(args)
    out: dict = {"generated_at": utc_day(now), "thresholds": thresholds, "layers": {}}

    ltm_dirs = cfg["ltm_dirs"]
    out["layers"]["ltm"] = {
        "configured": bool(ltm_dirs),
        "stores": [scan_ltm(_resolve(d, "ltm_dirs"), now=now, thresholds=thresholds)
                   for d in ltm_dirs],
    }
    for key, scan, label in (("wiki", scan_wiki, "wiki_root"),
                             ("persona", scan_persona, "persona_home"),
                             ("moltbook", scan_moltbook, "moltbook_home")):
        path = cfg[label]
        out["layers"][key] = {
            "configured": bool(path),
            "stores": ([scan(_resolve(path, label), now=now)] if path else []),
        }
    for layer in LAYERS:
        entry = out["layers"][layer]
        if not entry["configured"]:
            entry["status"] = "未収集（memory_stores に未設定）"
    if store is not None:
        _attach_growth(store, out, now=now)
    return out


def _attach_growth(store, summary: dict, *, now: float, days: float = 7.0) -> None:
    """snapshot レコードの履歴から週次成長を足す（履歴が無い層は None のまま）。"""
    history: "dict[tuple[str, str], list[dict]]" = {}
    for rec in store.iter_records():
        if rec.get("kind") != "memory" or rec.get("source") != "memory-store":
            continue
        history.setdefault((rec.get("layer") or "", rec.get("ref") or ""), []).append(rec)
    for layer in LAYERS:
        for entry in summary["layers"][layer]["stores"]:
            rows = sorted(history.get((layer, entry["path"]), []),
                          key=lambda r: parse_iso(r.get("ts")) or 0.0)
            baseline = None
            for row in rows:
                ts = parse_iso(row.get("ts")) or 0.0
                if ts <= now - days * DAY:
                    baseline = row
            if baseline is None:
                entry["growth_7d"] = None
                continue
            before = (baseline.get("metrics") or {}).get("total")
            entry["growth_7d"] = (None if before is None or entry.get("total") is None
                                  else int(entry["total"]) - int(before))


# -- collect（snapshot レコードの追記） ---------------------------------------

_SNAPSHOT_KEYS = {
    "ltm": ("total", "active", "archived", "publish_waiting", "forgetting_risk",
            "never_accessed", "dormant", "similar_clusters", "index_drift"),
    "wiki": ("total", "atoms", "topics", "new_last_7d", "index_drift", "lint_violations",
             "queries_total", "queries_hit"),
    # persona は件数と滞留日数だけ（本文もタイトルもファイル名も持たない。C1）
    "persona": ("managed_files", "pending_updates", "pending_oldest_days"),
    "moltbook": ("outbox_pending", "outbox_oldest_days", "published_local", "inbox_pending"),
}


def collect_memory_stores(args, store, *, now: float) -> int:
    """記憶ストアのメタデータ snapshot を audit ストアへ追記する。

    増分・冪等の作り方は cli-quota の snapshot と同じ——**内容が変わったときだけ**
    1 行増える（署名をカーソルに置く）。記憶ファイルには一切書かない。
    """
    summary = knowledge_summary(args, now=now)
    added = 0
    for layer in LAYERS:
        for entry in summary["layers"][layer]["stores"]:
            metrics = {k: entry.get(k) for k in _SNAPSHOT_KEYS[layer] if entry.get(k) is not None}
            signature = json.dumps(metrics, sort_keys=True, separators=(",", ":"))
            key = f"memory-store::{layer}::{entry['path']}"
            if store.cursor(key) == signature:
                continue
            rec = {
                "id": record_id("memory-store", f"{layer}::{entry['path']}", signature),
                "_epoch": now,
                "ts": _iso(now),
                "kind": "memory",
                "source": "memory-store",
                "tool": "memory",
                "workload": "routine",
                "layer": layer,
                "ref": entry["path"],
                "metrics": metrics,
            }
            if entry.get("notes"):
                rec["notes"] = list(entry["notes"])
            if store.append_record(rec):
                added += 1
            store.set_cursor(key, signature)
    return added


def _iso(sec: float) -> str:
    from .util import epoch_to_iso
    return epoch_to_iso(sec)


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
