from __future__ import annotations
# rules.py — 元 kiro-project.py の 2007-2111 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# プロジェクトルール（rules.md）— フローを回して判明した恒常ルール（暗黙知）の明文化先。
#   learn/avoid（decisions/）が「タイトル類似のタスクにだけ recall される」のに対し、
#   rules.md は **全タスクの act / plan / verify 合成へ常時注入**される（charter と同列・有界）。
#   人が直接書くのが正で、システムは「効果が再現した learn」を決定的に昇格・追記するだけ
#   （出典コメント付き・人がいつでも編集/削除できる）。注入はプロンプト文脈を足すだけで、
#   done 条件・予算・policy には触れない（不変条件 1–3 を保つ）。
# ---------------------------------------------------------------------------
RULES_AUTO_SECTION = "## 自動昇格（システムが追記・人が編集/削除してよい）"


def rules_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "rules.md"


def project_rules_context(cfg: "Config", limit: int = 1200) -> str:
    """rules.md を有界に読み出す（出典の HTML コメントは注入しない）。無ければ空（後方互換）。
    charter の constraints が「目標の制約」なのに対し、rules.md は「運用で判明したやり方の規則」。"""
    p = rules_path(cfg)
    if not p.exists():
        return ""
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt[:limit]


def _rules_marker(cfg: "Config", src: str) -> bool:
    dp = decision_path(cfg, src)
    return dp.exists() and "- rules-promoted:" in dp.read_text(encoding="utf-8")


def promote_rules(cfg: "Config") -> "list[str]":
    """効果が再現した learn ルール（auto-resolve hits ≥ promote_threshold）を rules.md へ昇格する。
    ltm 昇格（プロジェクト横断・opt-in --ltm）とは独立の**プロジェクト内・常時注入層**で既定 on。
    決定的・冪等: 同一 guide は再追記せず、昇格済みは DR の `- rules-promoted:` マーカーで跳ぶ。"""
    if not getattr(cfg, "rules_capture", True):
        return []
    hits = count_learn_hits(cfg)
    if not hits:
        return []
    p = rules_path(cfg)
    text = ""
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return []
    promoted: "list[str]" = []
    seen: "set[str]" = set()
    for src, _title, guide in collect_learnings(cfg):
        if src in seen or hits.get(src, 0) < cfg.promote_threshold:
            continue
        seen.add(src)
        if guide in text or _rules_marker(cfg, src):
            continue
        if not text:
            text = ("# プロジェクトルール\n\n"
                    "<!-- フローを回して判明した恒常ルール（暗黙知）の明文化先。人が書くのが正。\n"
                    "     全タスクの act / plan / verify 合成に常時注入される（有界）。 -->\n")
        if RULES_AUTO_SECTION not in text:
            text = text.rstrip() + f"\n\n{RULES_AUTO_SECTION}\n"
        date = datetime.now().strftime("%Y-%m-%d")
        text = text.rstrip() + f"\n- {guide}  <!-- learn:{src} hits={hits[src]} {date} -->\n"
        with decision_path(cfg, src).open("a", encoding="utf-8") as f:
            f.write("- rules-promoted: rules.md\n")
        append_journal(cfg.journal, f"ルール昇格: {src} → rules.md（hits={hits[src]}）")
        promoted.append(src)
    if promoted:
        p.write_text(text, encoding="utf-8")
    return promoted


def normalize_title(t: Task) -> str:
    return re.sub(r"\s+", " ", t.title.strip().lower())


def file_age_days(cfg: "Config", tid: str) -> float:
    p = cfg.backlog / f"{tid}.md"
    return (time.time() - p.stat().st_mtime) / 86400.0 if p.exists() else 0.0


def detect_rot(cfg: "Config", tasks: "list[Task]") -> "list[tuple[Task, str]]":
    """腐ったタスクを検出: unverifiable（verify無）/ duplicate（同題）/ stale（古い）。"""
    out: list[tuple[Task, str]] = []
    seen: dict[str, str] = {}
    for t in tasks:
        if not t.consumable():
            continue
        if not has_verify_plan(t):                # accept / verify_template があれば verify を用意できる
            out.append((t, "unverifiable（verify 未定義）"))
            continue
        nt = normalize_title(t)
        if nt in seen:
            out.append((t, f"duplicate（{seen[nt]} と重複）"))
            continue
        seen[nt] = t.id
        if cfg.rot_age_days and file_age_days(cfg, t.id) > cfg.rot_age_days:
            out.append((t, f"stale（{cfg.rot_age_days:.0f}日以上未処理）"))
    return out


# ---------------------------------------------------------------------------
