from __future__ import annotations
# rules.py — 元 agent-project.py の 2007-2111 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# プロジェクトルール（rules.md）— フローを回して判明した恒常ルール（暗黙知）の明文化先。
#   learn/avoid（decisions/）が「タイトル類似のタスクにだけ recall される」のに対し、
#   rules.md は **全タスクの act / plan / verify 合成へ常時注入**される（charter と同列・有界）。
#   人が直接書くのが正で、システムは「効果が再現した learn」を決定的に昇格・追記するだけ
#   （出典コメント付き・人がいつでも編集/削除できる）。注入はプロンプト文脈を足すだけで、
#   done 条件・予算・policy には触れない（不変条件 1–3 を保つ）。
#   Phase4 結合点: 自動昇格は trial から入り、suspended は注入しない。人手行は active/manual 保持。
# ---------------------------------------------------------------------------
RULES_AUTO_SECTION = "## 自動昇格（システムが追記・人が編集/削除してよい）"


def rules_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "rules.md"


def _filter_rules_for_injection(cfg: "Config", txt: str) -> str:
    """suspended/deprecated/rejected の自動 rule を注入文から除く。人手行は常に残す。
    W10 で失効した出典（learn-disable / misfire 連続）に紐づく自動行も除く。"""
    if RULES_AUTO_SECTION not in txt:
        return txt
    head, auto = txt.split(RULES_AUTO_SECTION, 1)
    limit = int(getattr(cfg, "learn_misfire_limit", 3) or 0)
    kept: list[str] = []
    for line in auto.splitlines():
        raw = line
        s = line.strip()
        if not s.startswith("- "):
            kept.append(raw)
            continue
        m = _RULE_COMMENT_RE.search(line)
        if m:
            rid, st = m.group("id"), m.group("state")
            live = rule_lifecycle_state(cfg, rid)
            if live in RULE_EXCLUDED_STATES or st in RULE_EXCLUDED_STATES:
                continue
        else:
            # 自動節でも rule マーカー無し＝旧形式 or 人が足した行 → manual として残す
            # ただし learn:SRC があり出典が W10 失効なら除く（suppressed 一般化）
            pass
        learn_m = re.search(r"learn:(\S+)", line)
        if learn_m:
            src = learn_m.group(1)
            dp = decision_path(cfg, src)
            if dp.exists() and learn_suppressed(dp, limit):
                continue
        kept.append(raw)
    return head + RULES_AUTO_SECTION + "\n".join(kept)


def project_rules_context(cfg: "Config", limit: int = 1200) -> str:
    """rules.md を有界に読み出す（出典の HTML コメントは注入しない）。無ければ空（後方互換）。
    charter の constraints が「目標の制約」なのに対し、rules.md は「運用で判明したやり方の規則」。
    Phase4: suspended rule は新規要求へ入らない。人手（auto 節外 / rule マーカー無し）は保持。"""
    p = rules_path(cfg)
    if not p.exists():
        return ""
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    txt = _filter_rules_for_injection(cfg, txt)
    txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt[:limit]


def _rules_marker(cfg: "Config", src: str) -> bool:
    dp = decision_path(cfg, src)
    return dp.exists() and "- rules-promoted:" in dp.read_text(encoding="utf-8")


def _auto_section_learn_sources(text: str) -> "dict[str, str]":
    """自動昇格節の guide → learn 出典 stem（競合検出用）。"""
    out: dict[str, str] = {}
    if RULES_AUTO_SECTION not in text:
        return out
    auto = text.split(RULES_AUTO_SECTION, 1)[1]
    for line in auto.splitlines():
        s = line.strip()
        if not s.startswith("- "):
            continue
        body = re.sub(r"\s*<!--.*?-->\s*$", "", s[2:]).strip()
        # 競合照合は promote_rules と同じ「scope タグを外した本文」で行う
        # （キーに `:: scope=…` が残ると scope 付き learn の同一 guidance 競合を取りこぼす）
        body = split_learn_scope(body)[0].strip()
        m = re.search(r"learn:(\S+)", line)
        if body:
            out[body] = m.group(1) if m else ""
    return out


def _rewrite_rule_state_in_rules_md(text: str, rule_id: str, state: str) -> str:
    """rules.md コメントの state: を更新（表示用。正本は decisions の rule-lifecycle）。"""
    def _sub(m: "re.Match") -> str:
        if m.group("id") != rule_id:
            return m.group(0)
        return m.group(0).replace(f"state:{m.group('state')}", f"state:{state}", 1)
    return _RULE_COMMENT_RE.sub(_sub, text)


def promote_rules(cfg: "Config") -> "list[str]":
    """効果が再現した learn ルール（auto-resolve hits ≥ promote_threshold）を rules.md へ昇格する。
    ltm 昇格（プロジェクト横断・opt-in --ltm）とは独立の**プロジェクト内・常時注入層**で既定 on。
    決定的・冪等: 同一 guide は再追記せず、昇格済みは DR の `- rules-promoted:` マーカーで跳ぶ。

    Phase4 結合点: 初回は trial（自動遷移可）。active は outcome 条件付き（maybe_activate_rule）。
    同一 guide の別出典は自動マージせず needs へ。"""
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
    existing = _auto_section_learn_sources(text)
    for src, _title, guide in collect_learnings(cfg):
        if src in seen or hits.get(src, 0) < cfg.promote_threshold:
            continue
        seen.add(src)
        body, _scope = split_learn_scope(guide)
        body = body.strip()
        rid = rule_id_for_guide(guide, src)
        # 競合: 同一 guidance が別出典で既に自動節にある → needs、マージしない
        if body in existing and existing[body] and existing[body] != src:
            write_rule_conflict_needs(cfg, rid, [existing[body], src], body)
            continue
        if guide in text or body in existing or _rules_marker(cfg, src):
            # 既に trial 以上なら outcome 条件で active へ（再追記はしない）
            if _rules_marker(cfg, src):
                if maybe_activate_rule(cfg, src, rid):
                    text = _rewrite_rule_state_in_rules_md(text, rid, "active")
                    p.write_text(text, encoding="utf-8")
            continue
        if not text:
            text = ("# プロジェクトルール\n\n"
                    "<!-- フローを回して判明した恒常ルール（暗黙知）の明文化先。人が書くのが正。\n"
                    "     全タスクの act / plan / verify 合成に常時注入される（有界）。 -->\n")
        if RULES_AUTO_SECTION not in text:
            text = text.rstrip() + f"\n\n{RULES_AUTO_SECTION}\n"
        date = datetime.now().strftime("%Y-%m-%d")
        # 自動遷移は trial まで。active は worked outcome 後。
        text = (text.rstrip()
                + f"\n- {guide}  <!-- rule:{rid} state:trial learn:{src} "
                  f"hits={hits[src]} {date} -->\n")
        with decision_path(cfg, src).open("a", encoding="utf-8") as f:
            f.write("- rules-promoted: rules.md\n")
        append_rule_lifecycle(cfg, src, rid, "trial",
                              why=f"hits={hits[src]}≥{cfg.promote_threshold}",
                              actor="auto")
        # 既に worked があれば同サイクルで active へ（threshold+outcome）
        if maybe_activate_rule(cfg, src, rid):
            text = _rewrite_rule_state_in_rules_md(text, rid, "active")
        append_journal(cfg.journal, f"ルール昇格: {src} → rules.md（hits={hits[src]}・trial）")
        promoted.append(src)
        existing[body] = src
    if promoted:
        p.write_text(text, encoding="utf-8")
    elif text and p.exists() and _RULE_COMMENT_RE.search(text):
        # trial→active のコメント書き換えのみ（新規昇格なし）
        try:
            prev = p.read_text(encoding="utf-8")
        except OSError:
            prev = ""
        if text != prev:
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
