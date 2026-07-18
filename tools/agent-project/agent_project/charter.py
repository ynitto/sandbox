from __future__ import annotations
# charter.py — 元 agent-project.py の 8426-9289 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# プロジェクト層（charter 駆動の plan→execute→evaluate ループ）
#   設計: docs/designs/agent-project-design.md §6（プロジェクト層）
#   backlog の上に「目標→分解→消化→評価→改善」のもう一段を載せる。内側の正準ループ（run_loop）は
#   無改造で呼ぶ。done は acceptance(=verify) 全 PASS のみが根拠。知能（分解・敵対的レビュー）は
#   エージェントへ委譲し、本体は決定的なファイル操作（charter 解釈・enqueue・acceptance 実行・収束計算）
#   のみを担う。`project` を呼ばない限り従来挙動は完全不変。
# ---------------------------------------------------------------------------
REASON_PROJECT_CONVERGED = "converged"        # acceptance 全 PASS・改善ゼロ → milestone gate で人へ
REASON_PROJECT_ACCEPTED = "accepted"          # 人が milestone を承認（プロジェクト done）
REASON_PROJECT_BUDGET = "project-budget"      # 改善サイクル/内側予算の上限
REASON_PROJECT_COST = "project-cost"          # プロジェクト累計コスト上限
REASON_PROJECT_STALL = "no-progress"          # acceptance PASS 数が増えず人へ
REASON_PROJECT_BLOCKED = "blocked"            # 内側ループが人へエスカレーション
REASON_PROJECT_NO_ACCEPTANCE = "no-acceptance"  # acceptance 未定義（done 判定不能）→ 人へ（完了条件を足す）

# milestone として「人待ち」にできる status（承認できる converged と、対応が要る no-acceptance/
# blocked/stall/budget/cost）。これ以外（accepted/running）や、もう無いバージョンの milestone
# ファイルは reconcile_milestones が消す＝milestone は status の純粋な投影になり復活しない。
MILESTONE_STATUSES = frozenset({
    REASON_PROJECT_CONVERGED, REASON_PROJECT_STALL, REASON_PROJECT_BUDGET,
    REASON_PROJECT_COST, REASON_PROJECT_BLOCKED, REASON_PROJECT_NO_ACCEPTANCE,
})


@dataclass
class Charter:
    name: str = "project"
    goal: str = ""
    constraints: "list[str]" = field(default_factory=list)
    assumptions: "list[str]" = field(default_factory=list)
    deliverables: "list[str]" = field(default_factory=list)
    acceptance: "list[str]" = field(default_factory=list)   # 受入 verify（シェルコマンド）
    links: "list[str]" = field(default_factory=list)        # 横展開/参考リンク（見出し文字列）
    repos: "list[str]" = field(default_factory=list)        # 対象リポジトリ見出し（`name = url`。後方互換）
    repo_specs: "list[dict]" = field(default_factory=list)  # 構造化 repos: {name,url,desc,base,target}
    link_specs: "list[dict]" = field(default_factory=list)  # 構造化 links: {text,desc}（wiki/doc 等も可）
    # マスター憲章（`## master` セクション付き charter.md）: プロジェクト全体の普遍的な前提・制約。
    # それ自体はバックログへ分解されず、計画バージョン（charters/<name>.md）へ継承される。
    master: bool = False
    raw: str = ""


_CHARTER_NAME_RE = re.compile(r"^#\s+(?:Charter|憲章)\s*[:：]?\s*(?P<name>.+?)\s*$", re.M)
_CHARTER_SECTION_RE = re.compile(r"^##\s+(?P<key>[A-Za-z]+)\b")
# acceptance 行を自然言語として明示する接頭辞（`accept: …` / `受入: …`）。タスクの `accept:` と同じ流儀。
_ACCEPT_PREFIX_RE = re.compile(r"^(?:accept|受入|受入条件|自然文|自然言語)\s*[:：]\s*(?P<text>.+)$", re.I)


def _charter_bullets(lines: "list[str]") -> "list[str]":
    """`- ...` 行の中身を抽出（コードフェンス/バッククォートは剥がす）。空行・コメントは無視。"""
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        if s.startswith(("- ", "* ", "+ ")):
            out.append(_strip_code(s[2:].strip()))
        elif s.startswith(("-", "*", "+")) and len(s) > 1 and not s[1].isspace():
            out.append(_strip_code(s[1:].strip()))
    return [x for x in out if x]


def _bullet_text(s: str) -> str:
    """箇条書きマーカー（- * +）を剥がした本文（コード除去）。マーカー以外は空。"""
    if s.startswith(("- ", "* ", "+ ")):
        return _strip_code(s[2:].strip())
    if s[:1] in "-*+" and len(s) > 1 and not s[1].isspace():
        return _strip_code(s[1:].strip())
    return ""


def _charter_entries(lines: "list[str]") -> "list[dict]":
    """セクションを構造化エントリに分解する。最小インデントの箇条書きを「見出し」とし、
    より深くインデントした `- key: value`（`：` も可）をその見出しの属性（attrs）にする。
    旧来のフラットな箇条書き（サブ箇条なし）は、各行が属性なしの見出しになる（後方互換）。"""
    entries: "list[dict]" = []
    head_indent: "int | None" = None
    for line in lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        if s[:1] not in "-*+":
            continue
        body = _bullet_text(s)
        if not body:
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if head_indent is None or indent <= head_indent:
            head_indent = indent
            entries.append({"head": body, "attrs": {}})
        elif entries:                      # サブ箇条書き = 直近見出しの属性
            for sep in (":", "："):
                if sep in body:
                    k, v = body.split(sep, 1)
                    entries[-1]["attrs"][k.strip().lower()] = v.strip()
                    break
    return entries


# 構造化 charter の属性キー別名（日本語表記も受ける）
_REPO_KEY_ALIASES = {
    "desc": ("desc", "description", "説明", "内容", "内容物", "役割", "role"),
    "base": ("base", "base_branch", "ベース", "ベースブランチ"),
    "target": ("target", "target_branch", "ターゲット", "ターゲットブランチ"),
    "path": ("path", "dir", "folder", "subdir", "subpath",
             "パス", "ディレクトリ", "フォルダ", "サブディレクトリ"),
    "readonly": ("readonly", "read_only", "read-only", "ref", "reference",
                 "参照のみ", "参照", "読み取り専用", "読取専用"),
    "owns": ("owns", "own", "owned", "owns_paths", "paths",
             "所有", "担当", "管轄", "担当パス"),
    # 分類グロブ（repos スキーマの拡張キー。本体は使わず repos.json への書き出しで引き継ぐ）
    "docs": ("docs", "doc", "ドキュメント", "文書"),
    "tests": ("tests", "test", "テスト"),
    "code": ("code", "コード", "実装"),
}

# readonly フラグの真値表記（値なし＝キーだけ書いた場合も True 扱い）
_REPO_TRUTHY = {"", "true", "yes", "y", "1", "on", "参照のみ", "参照",
                "readonly", "read-only", "読み取り専用", "読取専用"}


def _entry_readonly(attrs: dict) -> bool:
    """repos エントリの参照のみ（readonly）フラグを判定する。`- readonly: true` /『- 参照のみ:』など。"""
    for alias in _REPO_KEY_ALIASES["readonly"]:
        if alias in attrs:
            return str(attrs[alias]).strip().lower() in _REPO_TRUTHY
    return False


def _entry_attr(attrs: dict, key: str) -> str:
    for alias in _REPO_KEY_ALIASES.get(key, (key,)):
        v = attrs.get(alias)
        if v:
            return str(v).strip()
    return ""


def _repo_spec_from_entry(e: dict) -> dict:
    """repos エントリ（見出し `name = url` ＋ 属性 desc/base/target/path/owns）を構造化する。
    target 省略時は base と同じ（同一ブランチで作業）。path はモノレポ内の作業フォルダ（任意）で、
    同一 URL を役割別に複数エントリへ分けるときの識別子になる。owns は担当パス（グロブ）の列で、
    ルーティング（タスク→書込先）の根拠になる。**owns 未指定＝参照リポジトリ**（書込先にしない）。"""
    name, url = _repo_token_parts(e["head"])
    desc = _entry_attr(e["attrs"], "desc")
    base = _entry_attr(e["attrs"], "base")
    target = _entry_attr(e["attrs"], "target") or base
    path = _entry_attr(e["attrs"], "path").strip("/")
    owns = [g for g in re.split(r"[,\s]+", _entry_attr(e["attrs"], "owns")) if g]
    # owns があれば書込先候補。owns 未指定は参照リポジトリ（readonly 明示と同義に倒す）。
    readonly = _entry_readonly(e["attrs"]) or not owns
    spec = {"name": name, "url": url, "desc": desc, "base": base,
            "target": target, "path": path, "readonly": readonly, "owns": owns}
    for k in ("docs", "tests", "code"):   # 分類グロブは repos スキーマへ損失なく引き継ぐ（本体は不使用）
        globs = [g for g in re.split(r"[,\s]+", _entry_attr(e["attrs"], k)) if g]
        if globs:
            spec[k] = globs
    return spec


def validate_charter(ch: "Charter") -> "list[str]":
    """構造化 repos の必須項目（desc・base）と、同一 URL を役割分割する際の規約を検証し、
    問題点の説明リストを返す（空＝OK）。
    『説明は原則必須・base は必須・target は省略可（既定 base）』に加え、**同じ URL を複数エントリで
    使う場合は path（作業フォルダ）か base/target（ブランチ）のいずれかで区別する**こと
    （どのフォルダ／どのブランチの役割かを曖昧にしない）。path も branch も全て一致するエントリだけを
    曖昧な重複として弾く。"""
    problems: list[str] = []
    by_url: "dict[str, list[dict]]" = {}
    for r in ch.repo_specs:
        label = r["name"] or r["url"] or "(無名 repo)"
        if not r["desc"]:
            problems.append(f"repo '{label}': 説明（desc）が必須です（内容物・関与範囲を 1 行で）")
        if not r["base"]:
            problems.append(f"repo '{label}': base ブランチが必須です（例 `- base: main`）")
        if r["url"]:
            by_url.setdefault(r["url"], []).append(r)
    for url, group in by_url.items():
        if len(group) < 2:
            continue                         # 単独エントリは path/branch 任意（後方互換）
        # 同一 URL のエントリは path（作業フォルダ）か base/target（ブランチ）で区別する。
        # どちらかが違えば別エントリとして成立し、3 つとも一致するものだけを曖昧な重複として弾く。
        seen: "dict[tuple, str]" = {}
        for r in group:
            label = r["name"] or url
            key = (r["path"], r["base"], r["target"])
            if key in seen:
                where = f"path '{r['path']}'" if r["path"] else "path 無し"
                problems.append(
                    f"repo '{label}': 同一 URL のエントリが {where}・base '{r['base']}'・"
                    f"target '{r['target']}' まで一致して '{seen[key]}' と重複しています"
                    "（path＝作業フォルダ か base/target＝ブランチ のいずれかで区別してください）")
            else:
                seen[key] = label
    return problems



def parse_charter(text: str) -> Charter:
    """charter.md を構造化する。`# Charter: <name>` と `## goal/constraints/assumptions/
    deliverables/acceptance` を読む。acceptance は受入 verify（1 行 1 コマンド）。決定的・LLM 不要。"""
    ch = Charter(raw=text)
    m = _CHARTER_NAME_RE.search(text)
    if m:
        ch.name = m.group("name").strip() or "project"
    sections: dict[str, list[str]] = {}
    cur: "str | None" = None
    for line in text.splitlines():
        sm = _CHARTER_SECTION_RE.match(line)
        if sm:
            cur = sm.group("key").lower()
            sections.setdefault(cur, [])
        elif cur is not None:
            sections[cur].append(line)
    ch.goal = "\n".join(l for l in sections.get("goal", []) if l.strip()).strip()
    ch.constraints = _charter_bullets(sections.get("constraints", []))
    ch.assumptions = _charter_bullets(sections.get("assumptions", []))
    ch.deliverables = _charter_bullets(sections.get("deliverables", []))
    ch.acceptance = _charter_bullets(sections.get("acceptance", []))
    # links: 構造化（見出し＋任意の desc）。wiki/doc/横展開先など何でも置ける。
    link_entries = _charter_entries(sections.get("links", []))
    ch.links = [e["head"] for e in link_entries]
    ch.link_specs = [{"text": e["head"], "desc": _entry_attr(e["attrs"], "desc")}
                     for e in link_entries]
    # repos: 構造化（見出し `name = url` ＋ desc/base/target）。後方互換で ch.repos は見出し列を維持。
    repo_entries = (_charter_entries(sections.get("repos", []))
                    or _charter_entries(sections.get("repositories", [])))
    ch.repos = [e["head"] for e in repo_entries]
    ch.repo_specs = [_repo_spec_from_entry(e) for e in repo_entries]
    # `## master` セクションの存在＝マスター宣言（中身は説明コメントで良い・パースしない）
    ch.master = "master" in sections
    return ch


def _repo_token_parts(token: str) -> "tuple[str, str]":
    """charter の repos 行 `name = url` または `url` を (name, url) に分解する。
    name 省略時は URL の末尾（.git 除く）を name とする。"""
    if "=" in token:
        name, url = token.split("=", 1)
        name, url = name.strip(), url.strip()
    else:
        name, url = "", token.strip()
    if not name:
        base = url.rstrip("/").split("/")[-1]
        name = base[:-4] if base.endswith(".git") else base
    return name, url


def charter_repo_map(ch: "Charter | None") -> "dict[str, str]":
    """charter の repos を {name: url} と {url: url} の両引きできる辞書にする。"""
    out: "dict[str, str]" = {}
    for token in (ch.repos if ch else []):
        name, url = _repo_token_parts(token)
        if url:
            out[name] = url
            out[url] = url
    return out


def repo_spec_map(specs: "list[dict]") -> "dict[str, dict]":
    """構造化 repos を {name: spec} と {url: spec} で両引きできる辞書にする。
    name はエントリ一意（モノレポの役割別フォルダも name で区別）。url は先勝ち（同一 URL の
    複数エントリは name で参照させる）。"""
    out: "dict[str, dict]" = {}
    for spec in specs:
        if not spec.get("url"):
            continue
        if spec.get("name"):
            out[spec["name"]] = spec
        out.setdefault(spec["url"], spec)
    return out


def charter_repo_spec_map(ch: "Charter | None") -> "dict[str, dict]":
    return repo_spec_map(ch.repo_specs if ch else [])


# ---------------------------------------------------------------------------
# repos レジストリ（schemas/repos.schema.json）— リポジトリ定義の独立スキーマ
#   <project>/repos.{yaml,yml,json} があればそれがレジストリの正。charter の ## repos は
#   互換入力（アダプタ）として残るが、内部的には同じ構造（repo_specs）へ正規化して引き回す。
#   両方あるときは repos ファイルが勝つ。repos ファイル単独では charter モード（目標駆動）は
#   発動しない（発動条件は charter.md の存在のまま）。
# ---------------------------------------------------------------------------
REPOS_FILE_NAMES = ("repos.yaml", "repos.yml", "repos.json")


def _read_structured(path: Path):
    """YAML（PyYAML 任意）/ JSON を読む（他ツールと同じフォールバック規約）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            pass
    return json.loads(text)


def _registry_entry(name: str, e: dict) -> dict:
    """repos スキーマの 1 エントリを内部の repo_spec 形へ正規化する（charter パースと同じ形）。"""
    owns = e.get("owns") or []
    if isinstance(owns, str):
        owns = [g for g in re.split(r"[,\s]+", owns) if g]
    base = str(e.get("base", "") or "")
    spec = {"name": str(name), "url": str(e.get("url", "") or ""),
            "desc": str(e.get("desc", "") or ""), "base": base,
            "target": str(e.get("target", "") or "") or base,
            "path": str(e.get("path", "") or "").strip("/"),
            # local: 手元にある同じリポジトリのクローン。worker はここから worktree を切れる
            # （ネットワーク越しのミラー取得が要らない）。agent-flow へは _workspace_token で伝搬。
            "local": str(e.get("local", "") or "").strip(),
            "readonly": bool(e.get("readonly")) or not owns,
            "owns": [str(g) for g in owns]}
    for k in ("docs", "tests", "code"):   # 分類グロブ（repos スキーマの拡張キー）も引き回す
        v = e.get(k) or []
        if isinstance(v, str):
            v = [g for g in re.split(r"[,\s]+", v) if g]
        if v:
            spec[k] = [str(g) for g in v]
    return spec


def repo_registry_path(cfg: "Config") -> "Path | None":
    base = cfg.backlog.parent
    for name in REPOS_FILE_NAMES:
        p = base / name
        if p.is_file():
            return p
    return None


def _specs_from_registry(data) -> "list[dict]":
    specs: "list[dict]" = []
    if isinstance(data, dict):
        for name, e in data.items():
            if str(name).startswith("_"):            # "_" 接頭辞キーはメタデータ予約（_meta 等）
                continue
            if isinstance(e, dict):
                specs.append(_registry_entry(str(name), e))
    elif isinstance(data, list):                     # [{name: ..., url: ...}, ...] も許容
        for e in data:
            if isinstance(e, dict) and e.get("name"):
                specs.append(_registry_entry(str(e["name"]), e))
    return specs


def _registry_generated(data) -> bool:
    """repos ファイルが「charter からの自動生成物」か（_meta.generated_from マーカー）。"""
    return (isinstance(data, dict) and isinstance(data.get("_meta"), dict)
            and bool(data["_meta"].get("generated_from")))


def load_repo_registry(cfg: "Config") -> "list[dict] | None":
    """<project>/repos.{yaml,yml,json} を読んで repo_specs 形にする。無ければ None。
    壊れたファイルは警告して None（黙って空レジストリにせず charter へフォールバック）。"""
    p = repo_registry_path(cfg)
    if p is None:
        return None
    try:
        data = _read_structured(p)
    except (OSError, ValueError) as e:
        print(f"[agent-project] repos レジストリを解釈できません: {p}: {e}", file=sys.stderr)
        return None
    return _specs_from_registry(data)


def export_repo_registry(cfg: "Config", specs: "list[dict]",
                         path: "Path | None" = None) -> None:
    """charter の ## repos を共通スキーマ（schemas/repos.schema.json）の repos.json へ書き出す。
    codd-gate 等の外部ツールへ**レジストリファイルとして渡す**ための派生物（codd-gate は charter を
    読まない）。_meta.generated_from を刻み、charter 変更のたび同期する（**正は charter のまま**。
    手で管理したくなったら _meta を消す＝以後は手書きが正で本体は上書きしない）。
    内容が同じなら書かない（ルーティング解決のたびに呼ばれるため）。"""
    path = path or (cfg.backlog.parent / "repos.json")
    entries: "dict[str, dict]" = {}
    for s in specs:
        e = {k: s[k] for k in ("url", "desc", "base", "target", "path") if s.get(k)}
        for k in ("owns", "docs", "tests", "code"):
            if s.get(k):
                e[k] = list(s[k])
        if s.get("readonly") and not s.get("owns"):
            e["readonly"] = True
        entries[s.get("name") or s.get("url") or f"repo{len(entries) + 1}"] = e
    payload = {"_meta": {"generated_from": "charter.md ## repos",
                         "note": "agent-project が自動生成（正は charter）。手で管理するなら _meta を消す"},
               **entries}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def registry_specs(cfg: "Config", ch: "Charter | None") -> "list[dict]":
    """実効レジストリ: charter があればその repo_specs（load_charter が repos ファイルで
    上書き済み）、無ければ repos ファイル単独でも読める（ルーティング/参照用）。"""
    if ch is not None:
        return ch.repo_specs
    return load_repo_registry(cfg) or []


def _apply_repo_registry(cfg: "Config", ch: "Charter", allow_export: bool = True) -> "Charter":
    """repos レジストリ（手書きが正・自動生成物は charter に追従）を charter へ適用する。
    allow_export=False は charters/ 複数運用時: 各 charter の ## repos はその charter のルーティングに
    のみ効かせ、repos.json の自動生成（単一 charter が正の前提）は行わない。"""
    rp = repo_registry_path(cfg)
    if rp is not None:
        try:
            data = _read_structured(rp)
        except (OSError, ValueError) as e:
            print(f"[agent-project] repos レジストリを解釈できません: {rp}: {e}", file=sys.stderr)
            return ch                                # 壊れた手書きは上書きせず charter のまま
        if _registry_generated(data):                # 自動生成物 → 正は charter・毎回同期
            if allow_export:
                if ch.repo_specs:
                    export_repo_registry(cfg, ch.repo_specs, rp)
                else:
                    rp.unlink(missing_ok=True)       # charter から repos が消えたら生成物も消す
            return ch
        specs = _specs_from_registry(data)
        if specs:                                    # 手書きレジストリが正・## repos は互換入力
            ch.repo_specs = specs
            ch.repos = [f"{s['name']} = {s['url']}" if s.get("name") else s["url"]
                        for s in specs if s.get("url")]
        return ch
    if allow_export and ch.repo_specs:               # レジストリ無し → charter から生成して外部ツールへ渡す
        export_repo_registry(cfg, ch.repo_specs)
    return ch


def load_charter(cfg: "Config") -> "Charter | None":
    p = cfg.charter
    if not p or not p.exists():
        return None
    ch = parse_charter(p.read_text(encoding="utf-8"))
    return _apply_repo_registry(cfg, ch)


# ---------------------------------------------------------------------------
# 複数 charter（charters/<name>.md）— 1 プロジェクトで複数バージョンの開発を並行管理する。
#   charters/ があれば各ファイルが 1 バージョン（stem が charter 名）で、全 charter を
#   ラウンドロビンで plan→execute→evaluate する。無ければ従来の charter.md（"default"）。
#   タスクには `charter: <name>` タグが付き、plan の冪等照合・drained 判定・acceptance
#   評価・milestone/state は charter 単位に閉じる（execute の run_loop は backlog 共有）。
# ---------------------------------------------------------------------------
def charters_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "charters"


# マスター宣言の軽量検知（`## master` セクション行）。charter_names は watch ループで高頻度に
# 呼ばれるため、フルパース（repos レジストリ適用・書き出し）を避けてテキストだけ見る。
_CHARTER_MASTER_RE = re.compile(r"(?m)^##\s+master\b", re.I)


def _has_master_charter(cfg: "Config") -> bool:
    """ルート charter.md がマスター憲章（`## master` 付き＝分解しない普遍の前提）か。"""
    if not (cfg.charter and cfg.charter.exists()):
        return False
    try:
        return bool(_CHARTER_MASTER_RE.search(cfg.charter.read_text(encoding="utf-8")))
    except OSError:
        return False


def charter_names(cfg: "Config") -> "list[str]":
    """駆動対象の charter 名一覧。charters/*.md（名前順）＞ 単一 charter.md（"default"）＞ 空。
    マスター憲章（`## master` 付き charter.md）は分解対象にしない: バージョンが無ければ空を返し、
    バージョン（charters/<name>.md）が置かれるとそれだけが駆動される（マスターは継承元）。"""
    d = charters_dir(cfg)
    if d.is_dir():
        names = sorted(f.stem for f in d.glob("*.md") if f.is_file())
        if names:
            return names
    if not (cfg.charter and cfg.charter.exists()):
        return []
    return [] if _has_master_charter(cfg) else ["default"]


def _merge_master_charter(cfg: "Config", ch: "Charter") -> "Charter":
    """マスター憲章（ルート charter.md・`## master` 付き）を計画バージョンへ継承する。
    バージョン側が空のフィールド（goal/deliverables/acceptance）はマスターで補い、
    制約・前提はバージョンに見出しがあればその値（空も可）、無ければマスターを使う。
    links・repos はマスター∪バージョンで合成する。raw は両方を連結し、
    マスターの編集も再計画判定（plan signature）と accepted 再開判定（full signature）に効かせる。"""
    if not _has_master_charter(cfg):
        return ch
    base = load_charter(cfg)
    if base is None or not base.master:
        return ch
    if not ch.name or ch.name == "project":
        ch.name = base.name
    ch.goal = ch.goal or base.goal
    ch.deliverables = ch.deliverables or list(base.deliverables)
    ch.acceptance = ch.acceptance or list(base.acceptance)
    # 旧バージョンは ## constraints / ## assumptions 自体を持たないためマスターへフォールバック。
    # 新しいフォームが空セクションを明示した場合は「継承値を空に上書き」という意思として扱う。
    if not re.search(r"(?m)^##\s+constraints\b", ch.raw or "", re.I):
        ch.constraints = list(base.constraints)
    if not re.search(r"(?m)^##\s+assumptions\b", ch.raw or "", re.I):
        ch.assumptions = list(base.assumptions)
    seen_links = {s.get("text") for s in ch.link_specs}
    for s in base.link_specs:
        if s.get("text") not in seen_links:
            ch.link_specs.append(dict(s))
            ch.links.append(s.get("text") or "")
    seen_repos = {(s.get("name"), s.get("url")) for s in ch.repo_specs}
    for i, s in enumerate(base.repo_specs):
        if (s.get("name"), s.get("url")) not in seen_repos:
            ch.repo_specs.append(dict(s))
            if i < len(base.repos):
                ch.repos.append(base.repos[i])
    ch.raw = base.raw + "\n\n" + ch.raw
    return ch


def _version_target_overrides(parsed: "Charter") -> "list[tuple[str, str, str, str]]":
    """バージョン charter の ## repos が明示する『base と異なる target』を抽出する。
    バージョン毎のターゲットブランチ（例 v1→release/1.x, v2→release/2.x）を、共有レジストリ
    （repos.json）を使っていても効かせるための材料。返り値は (name, url, path, target) の列。
    target が未指定、または target==base（＝作業ブランチと同じで既定）のエントリは含めない
    ＝共有レジストリの target を尊重する（後方互換。明示的にリリース先を分けた版だけ拾う）。"""
    out: "list[tuple[str, str, str, str]]" = []
    for s in parsed.repo_specs:
        t = str(s.get("target") or "").strip()
        b = str(s.get("base") or "").strip()
        if t and t != b:
            out.append((str(s.get("name") or ""), str(s.get("url") or ""),
                        str(s.get("path") or ""), t))
    return out


def _apply_version_target_overrides(
        ch: "Charter", overrides: "list[tuple[str, str, str, str]]") -> None:
    """共有レジストリ適用でバージョン charter の target が失われても、バージョンが明示した
    『base と異なる target』を実効 spec に復元する（バージョン毎のリリース先ブランチ）。
    マッチは name 一致、または (url, path) 一致（モノレポの役割別を取り違えないよう path も見る）。
    レジストリの url/path/owns/base 等（＝リポジトリ同一性・ルーティング根拠）は一切触らず、
    MR の宛先である target だけを差し替える＝ルーティングやクローン先には影響しない。"""
    if not overrides:
        return
    for spec in ch.repo_specs:
        sn = str(spec.get("name") or "")
        su = str(spec.get("url") or "")
        sp = str(spec.get("path") or "")
        for on, ou, op, t in overrides:
            if (on and on == sn) or (ou and ou == su and op == sp):
                spec["target"] = t
                break


def _load_named_charter(cfg: "Config", name: "str | None") -> "Charter | None":
    """charter 名 → Charter。charters/<name>.md があればそれ（複数運用・repos 自動生成なし。
    マスター憲章があれば継承合成する）、無ければ従来の charter.md へフォールバック（"default" や未指定）。"""
    if name:
        f = charters_dir(cfg) / f"{name}.md"
        if f.is_file():
            try:
                parsed = parse_charter(f.read_text(encoding="utf-8"))
                # 共有レジストリが ## repos を上書きする前に、バージョン毎 target を控えておく。
                overrides = _version_target_overrides(parsed)
                ch = _apply_repo_registry(cfg, parsed, allow_export=False)
            except (OSError, ValueError):
                return None
            _apply_version_target_overrides(ch, overrides)
            return _merge_master_charter(cfg, ch)
    return load_charter(cfg)


def load_charters(cfg: "Config") -> "list[tuple[str, Charter]]":
    """全 charter を (name, Charter) で返す（charters/ 無しは [("default", charter.md)]）。"""
    out: "list[tuple[str, Charter]]" = []
    for name in charter_names(cfg):
        ch = _load_named_charter(cfg, name)
        if ch is not None:
            out.append((name, ch))
    return out


def _is_multi_charter(cfg: "Config", name: "str | None") -> bool:
    """この charter 名が charters/ 複数運用のものか（milestone id の接尾辞・state のキー化に使う）。"""
    return bool(name) and (charters_dir(cfg) / f"{name}.md").is_file()


def task_charter_name(task: "Task") -> str:
    """タスクが属する charter 名（`- charter:` タグ。無ければ "default" 扱い）。"""
    return (task.get("charter") or "").strip() or "default"


def charter_for_task(cfg: "Config", task: "Task | None" = None) -> "Charter | None":
    """タスクの `charter:` タグから該当 charter を選ぶ（無ければ先頭/従来の charter.md）。
    ルーティング・文脈注入など「タスク単位で charter を引く」箇所の共通入口。"""
    name = (task.get("charter") or "").strip() if task is not None else ""
    if name:
        ch = _load_named_charter(cfg, name)
        if ch is not None:
            return ch
    chs = load_charters(cfg)
    return chs[0][1] if chs else None


def project_state_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "project.json"


def load_project_state(cfg: "Config") -> dict:
    p = project_state_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def save_project_state(cfg: "Config", state: dict) -> None:
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    project_state_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    project_state_path(cfg).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                       encoding="utf-8")


def load_charter_state(cfg: "Config", name: "str | None" = None) -> dict:
    """charter 単位の収束状態。複数運用（name あり）は project.json の {"charters": {name: …}}、
    単一運用（name 無し）は従来どおりトップレベル（後方互換）。"""
    data = load_project_state(cfg)
    if name:
        return dict((data.get("charters") or {}).get(name) or {})
    return data


def save_charter_state(cfg: "Config", state: dict, name: "str | None" = None) -> None:
    if not name:
        save_project_state(cfg, state)
        return
    data = load_project_state(cfg)
    data.setdefault("charters", {})[name] = state
    save_project_state(cfg, data)


# ---------------------------------------------------------------------------
# バックログ再分解の要求（人が charter から backlog を作り直したいときの一発の口）。
#   通常の再分解は「消化可能タスクが無い」か「charter が変わった」ときに自動で走るが、
#   タスクの取りこぼし・誤削除・plan 失敗などのエラー回復では charter が無変更のまま
#   backlog を作り直したい。project.json とは別のマーカーファイルにすることで、
#   cmd_project の通常の state 保存に上書きされず一発分だけ確実に効く（冪等・one-shot）。
#   再分解の冪等照合は「done 以外」（現行処理中のバックログ＋却下済み）と行う: 処理中タスクの
#   二重投入や却下済み（人の明示判断）の復活はさせず、done と類似のタスクだけやり直しとして
#   再作成を許す（通常 plan と違い、過去の完了実績が回復のための再分解を弾かないようにする）。
# ---------------------------------------------------------------------------
def replan_request_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / ".replan.request"


def write_replan_request(cfg: "Config", reason: str, charter: str = "") -> None:
    """次パスの再分解要求マーカーを置く（人の明示アクション。冪等＝上書き）。
    charter を渡すとその charter だけを再計画対象にする（複数 charter 運用）。"""
    p = replan_request_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason or "", "actor": getattr(cfg, "actor", "") or "",
               "ts": datetime.now().isoformat(timespec="seconds")}
    if charter:
        payload["charter"] = charter
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def consume_replan_request(cfg: "Config", charter_name: "str | None" = None) -> "dict | None":
    """再分解要求マーカーがあれば読み取り、消して payload を返す（無ければ None）。one-shot。
    charter_name を渡したとき、要求が**別の charter 宛**ならマーカーを残して None を返す
    （その charter のパスで消化される）。charter 指定の無い要求はどの charter でも消化する。"""
    p = replan_request_path(cfg)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {"reason": ""}
    target = str(payload.get("charter") or "").strip()
    if charter_name is not None and target and target != charter_name:
        return None                              # 別 charter 宛 → 残す
    try:
        p.unlink()
    except OSError:
        pass
    return payload


def _project_id(cfg: "Config", charter: "Charter") -> str:
    """milestone/state の id。プロジェクト名（ルートのディレクトリ名）を一次採用し、
    未設定なら charter 名から導出。Config を直接構築するテスト等（project_name 未設定）では
    従来どおり charter 名スラグになる（後方互換）。"""
    return getattr(cfg, "project_name", "") or _slug_id(charter.name) or "project"


def resolve_linked_projects(cfg: "Config", charter: "Charter") -> "list[tuple[str, Path]]":
    """charter の `## links` を他プロジェクト root へ解決する（横展開）。パスとして解決する:
    絶対パス、現プロジェクト root からの相対、または兄弟ディレクトリ名（root の親からの相対）。
    存在するものだけ返す（1 階層・自己/重複は無視）。"""
    out: list[tuple[str, Path]] = []
    proj_root = cfg.backlog.parent
    seen = {proj_root.resolve()}
    for link in charter.links:
        link = link.strip()
        if not link:
            continue
        p = Path(link).expanduser()
        cands = [p] if p.is_absolute() else [proj_root / link, proj_root.parent / link]
        for cand in cands:
            cand = cand.resolve()
            if cand.exists() and cand.is_dir() and cand not in seen:
                seen.add(cand)
                out.append((link, cand))
                break
    return out


def _existing_titles(cfg: "Config", charter: "str | None" = None,
                     active_only: bool = False) -> "list[str]":
    """重複投入の冪等照合に使う既存タイトル（backlog＋archive）。charter を渡すと
    その charter のタスク（タグ一致・タグ無しも含む）に照合を閉じる（複数 charter 運用で
    別バージョンの同名タスクを誤って弾かない）。
    active_only は照合を「done 以外」に絞る: 現行処理中の backlog に加え、archive の rejected
    （人の明示的な却下）だけは残す。再分解（replan）のエラー回復で、過去に done した同種タスクの
    再作成（やり直し）は許しつつ、却下済みタスクの復活は防ぐための口。"""
    def _match(t: "Task") -> bool:
        if not charter:
            return True
        tag = (t.get("charter") or "").strip()
        return tag in ("", charter)

    tasks = load_tasks(cfg.backlog)
    if active_only:
        titles = [t.title for t in tasks
                  if _match(t) and t.norm_status() != "done"]
    else:
        titles = [t.title for t in tasks if _match(t)]
    adir = cfg.archive_dir()
    if adir.exists():
        for p in adir.glob("*.md"):
            try:
                t = parse_task(p.read_text(encoding="utf-8"), p.stem)
            except (OSError, ValueError):
                continue
            if _match(t) and (not active_only or t.norm_status() == "rejected"):
                titles.append(t.title)
    return [t for t in titles if t]


def _is_duplicate(title: str, verify: str, existing: "list[str]", threshold: float) -> bool:
    """タイトルが既存と十分類似（Jaccard ≥ threshold）なら重複とみなす（plan/evaluate の冪等性）。"""
    return any(_title_overlap(title, e) >= threshold for e in existing)


def _extract_json_array(text: str) -> "list | None":
    """エージェント出力から最初の JSON 配列を取り出す（寛容パース）。"""
    depth, start = 0, -1
    for i, c in enumerate(text or ""):
        if c == "[":
            if depth == 0:
                start = i
            depth += 1
        elif c == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    v = json.loads(text[start:i + 1])
                    if isinstance(v, list):
                        return v
                except ValueError:
                    start = -1
    return None


def _coerce_repos(v) -> "list[str]":
    """エージェント出力の repos（list/str/None）を name/url の文字列リストへ正規化する。"""
    if not v:
        return []
    if isinstance(v, str):
        return [x for x in (s.strip() for s in re.split(r"[,\s]+", v)) if x]
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _coerce_titles(v) -> "list[str]":
    """after（先行タスクの title）の list/str/None を title 文字列リストへ正規化する。
    title は空白を含むため、文字列はカンマでのみ区切る（_coerce_repos の空白区切りは使えない）。"""
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def build_charter_request(charter: "Charter") -> str:
    """charter を分解要求の文章に組み立てる（plan フェーズで agent-flow/エージェントへ渡す）。"""
    parts = [f"プロジェクト目標: {charter.goal}"]
    if charter.constraints:
        parts.append("制約:\n" + "\n".join(f"- {c}" for c in charter.constraints))
    if charter.assumptions:
        parts.append("前提:\n" + "\n".join(f"- {a}" for a in charter.assumptions))
    if charter.deliverables:
        parts.append("成果物:\n" + "\n".join(f"- {d}" for d in charter.deliverables))
    if charter.acceptance:
        parts.append("受入条件(満たすべき検証):\n" + "\n".join(f"- {a}" for a in charter.acceptance))
    if charter.repo_specs:
        # 名前・フォルダ(path)・役割(desc) を提示し、プランナーが「役割に合うエントリ」を選べるようにする。
        # 同一 URL でも path/役割が違えば別エントリ＝別タスクに割り当てられる（モノレポの役割分割）。
        rlines = ["利用可能なリポジトリ（中身を読む/ push する必要があるタスクにのみ、その name で割当）:"]
        for r in charter.repo_specs:
            label = r["name"] or r["url"]
            line = f"- {label} = {r['url']}"
            tags = []
            if r.get("path"):
                tags.append(f"フォルダ {r['path']}")
            if r.get("readonly"):
                tags.append("参照のみ")
            if tags:
                line += "（" + "・".join(tags) + "）"
            if r["desc"]:
                line += f" — {r['desc']}"
            rlines.append(line)
        parts.append("\n".join(rlines))
    return "\n\n".join(parts)


def _charter_owns_note(charter: "Charter") -> str:
    """プランナーへ「どの repo がどのパスを担当（owns）するか」を伝える。書込先（workspace）選定の根拠。"""
    ws = [s for s in charter.repo_specs if s.get("owns")]
    refs = [s for s in charter.repo_specs if s.get("url") and not s.get("owns")]
    lines = []
    if ws:
        lines.append("書込先候補（owns＝担当パス。verify が操作するパスの owns を持つ repo を workspace にする）:")
        lines += [f"- {s.get('name') or s['url']}: owns {', '.join(s['owns'])}"
                  + (f" — {s['desc']}" if s.get("desc") else "") for s in ws]
    if refs:
        lines.append("参照リポジトリ（読むだけ。書込先にはしない）:")
        lines += [f"- {s.get('name') or s['url']}" + (f": {s['desc']}" if s.get("desc") else "")
                  for s in refs]
    return "\n".join(lines)


# バックログ分解の粒度指示（設定 granularity: coarse/fine/finest・既定 coarse）。
# 一般的なプロダクトバックログの書き方に合わせ、既定はユーザーストーリー相当
# （INVEST: 独立・価値・見積り可能・小さすぎない・検証可能）。agent-flow の同名設定と
# 語彙を揃えている（あちらは実行時 DAG の分解、こちらは backlog の分解に効く）。
PLAN_GRANULARITY_DIRECTIVES = {
    "coarse": "各タスクはユーザーストーリー相当の**意味のある成果のかたまり**にすること"
              "（独立に着手でき、単体で価値ある成果になり、独立に検証できる = INVEST）。"
              "1 ファイル・1 関数・単一の実装手順のレベルまでは刻まない。目安はプロジェクト"
              "全体で 3〜10 件。関連する小さな変更は 1 タスクにまとめ、verify はそのタスクの"
              "受入を代表する検証に絞ること。",
    "fine": "各タスクは単機能・単モジュール程度の変更セットにすること（目安 8〜20 件）。",
    "finest": "各タスクは機械的に検証できる最小単位（1 ファイル/1 関数/1 観点）まで"
              "原子的に分解すること。",
}


def plan_granularity_directive(level: "str | None") -> str:
    """プランナーへ渡す分解粒度の指示文。未知値は既定（coarse）に倒す。"""
    return PLAN_GRANULARITY_DIRECTIVES.get(
        (level or "coarse").lower(), PLAN_GRANULARITY_DIRECTIVES["coarse"])


# ---------------------------------------------------------------------------
