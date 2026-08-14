from __future__ import annotations
# decisions.py — 元 agent-project.py の 913-1085 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
def decision_path(cfg: "Config", tid: str) -> Path:
    return cfg.decisions / f"{tid}.md"


def next_dr_id(path: Path) -> str:
    return f"DR-{_max_dr_num(path) + 1:04d}"


def _max_dr_num(path: Path) -> int:
    n = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = DR_HEADER_RE.match(line)
            if m:
                n = max(n, int(m.group(1)))
    return n


def dr_num(dr: "str | None") -> int:
    """`DR-0007` → 7。空・読めない綴りは 0（「記録なし」と同じ扱い）。"""
    m = re.search(r"(\d+)", str(dr or ""))
    return int(m.group(1)) if m else 0


def latest_dr_id(cfg: "Config", tid: str) -> str:
    """このタスクの決定記録の最新 DR 番号（`DR-0007`）。1 件も無ければ空文字。
    「いつの時点の記録より後か」を数えるための時計として使う（日付は日単位で足りない）。"""
    n = _max_dr_num(decision_path(cfg, tid))
    return f"DR-{n:04d}" if n else ""


def append_decision(cfg: "Config", tid: str, actor: str, context: str,
                    action: str, reason: str, affects: str,
                    learn: "tuple[str, str] | None" = None,
                    avoid: "tuple[str, str] | None" = None,
                    observation: "str | None" = None,
                    provenance: "dict | None" = None) -> str:
    """決定記録を追記。learn=(title, guidance) を渡すと『- learn:』行を残し、
    将来 find_learned_resolution が類似タスクへ自動適用できる学習材料にする。
    avoid=(title, reason) を渡すと『- avoid:』行を残し、hold/deny の予防知識として
    投入/triage 時の類似タスク検出（find_avoidance）に使えるようにする。
    Phase 3: observation / provenance を additive に残す（同一 observation ID は追記しない）。"""
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    path = decision_path(cfg, tid)
    # 観測 ID が既にあれば冪等 no-op（git 再取込・二重捕捉でも DR を増やさない）
    if observation and file_has_observation(path, str(observation)):
        return ""
    dr = next_dr_id(path)
    date = datetime.now().strftime("%Y-%m-%d")
    block = (f"## {dr}  {date}  actor: {actor}\n"
             f"- context : {context}\n- action  : {action}\n"
             f"- reason  : {reason}\n- affects : {affects}\n")
    if observation:
        block += f"- observation: {observation}\n"
    if provenance:
        block += format_provenance_line(provenance) + "\n"
    if learn:
        title, guide = learn
        block += f"- learn: {title.replace(chr(10), ' ')} :: {guide.replace(chr(10), ' ')}\n"
    if avoid:
        title, guide = avoid
        block += f"- avoid: {title.replace(chr(10), ' ')} :: {guide.replace(chr(10), ' ')}\n"
    block = redact_for_share(block, f"decisions/{tid}.md")
    with path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")
    return dr


def project_interaction_decisions(cfg: "Config", tid: str, run_id: str,
                                  bus: "Path | None" = None) -> int:
    """確定済み human 工程を追記型 DR へ一度だけ写す。タスク全体の承認とは区別する。"""
    root = (bus or cfg.bus) / "runs" / str(run_id) / "interactions"
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return 0
    try:
        recorded = decision_path(cfg, tid).read_text(encoding="utf-8")
    except OSError:
        recorded = ""
    projected = 0
    for directory in dirs:
        try:
            request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            resolution = json.loads((directory / "resolution.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict) or not isinstance(resolution, dict):
            continue
        iid = str(resolution.get("interaction_id") or request.get("interaction_id") or directory.name)
        digest = hashlib.sha256(json.dumps(
            resolution, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        marker = f"interaction:{iid} digest:{digest}"
        if marker in recorded:
            continue
        outcome = str(resolution.get("outcome") or "unknown")
        answer = json.dumps(resolution.get("answer") or {}, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"))
        append_decision(
            cfg, tid, str(resolution.get("actor") or "workflow-human"),
            context=f"run {run_id} の human 工程（mode={request.get('mode', '')}）",
            action=f"human-interaction-{outcome}",
            reason=f"{marker} answer={answer}",
            affects=f"{tid} → unchanged（工程入力のみ。機械検証は引き続き必須）",
        )
        recorded += marker
        projected += 1
    return projected


# DR ヘッダの actor がこれらなら機械の記録（人の判断ではない）。人の判断だけを
# 「もう答えが出ている」の根拠にするため、ここで明示的に区別する。
_MACHINE_ACTORS = {"auto", "system", "gitlab", "forge"}
_DR_ACTOR_RE = re.compile(r"^##\s+DR-\d+\s+\S+\s+actor:\s*(?P<actor>.+?)\s*$")
_DR_AFFECTS_RE = re.compile(r"^-\s*affects\s*:\s*\S+\s*→\s*(?P<status>[A-Za-z_-]+)", re.M)


def last_human_decision(cfg: "Config", tid: str) -> "dict | None":
    """このタスクについて**人**が下した最後の決定（DR）。無ければ None。

    返すのは `{"dr", "actor", "action", "to"}`。`to` は `- affects : <id> → <status>` が
    示す遷移先 status（読めなければ空）。

    使い道は「同じ判断を人に二度させない」（コンセプト正典 C3）の判定材料。決定記録は
    追記のみで衝突なく合流するため、**状態ファイルの同期が競合しても人の決定だけは全 PC へ
    届く**——古い status を持つ PC が「まだ判断待ち」と誤解して票を作り直すのを、この記録で
    止められる（判断待ちの復活ループ。総覧 G-2）。"""
    path = decision_path(cfg, tid)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    found: "dict | None" = None
    for block in re.split(r"(?=^## DR-)", text, flags=re.M):
        head = block.splitlines()[0] if block.strip() else ""
        m = _DR_ACTOR_RE.match(head)
        if not m or m.group("actor").strip() in _MACHINE_ACTORS:
            continue
        dr = DR_HEADER_RE.match(head)
        act = re.search(r"^-\s*action\s*:\s*(?P<action>.+?)\s*$", block, flags=re.M)
        if act and act.group("action").strip().startswith("human-interaction-"):
            continue
        to = _DR_AFFECTS_RE.search(block)
        found = {"dr": f"DR-{int(dr.group(1)):04d}" if dr else "",
                 "actor": m.group("actor").strip(),
                 "action": act.group("action").strip() if act else "",
                 "to": to.group("status") if to else ""}
    return found                      # 最後に現れた人の DR（追記順＝時系列）


# ---------------------------------------------------------------------------
# DR 学習（過去の人の判断から類似案件を自動解決して通知を減らす）
# ---------------------------------------------------------------------------
def _title_overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def split_learn_scope(guide: str) -> "tuple[str, tuple[str, str]]":
    """guide 末尾のスコープタグを (本文, (kind, name)) に分ける。タグ無しは kind=""（全体）。"""
    m = LEARN_SCOPE_RE.search(guide)
    if not m:
        return guide.strip(), ("", "")
    return guide[:m.start()].strip(), (m.group("kind"), m.group("name"))


def _learn_scope_applies(task: Task, kind: str, name: str) -> bool:
    """スコープ付き learn をこのタスクへ適用してよいか（W10）。全体（kind=""）は常に適用。"""
    if not kind:
        return True
    if kind == "charter":
        return task_charter_name(task) == name
    return name in str(task.get("workspace") or "")     # repo: workspace 指定の部分一致


def learn_suppressed(path: "Path", limit: int) -> bool:
    """learn 出典（decisions/<src>.md）単位の失効判定（W10）。

    人の無効化（`action  : learn-disable` の決定記録）か、連続不発（learn-misfire が
    learn-worked を挟まず limit 回）で、その出典の learn は適用しない。記録は append-only の
    決定記録だけで数える（新ファイルなし・ファイル内の追記順＝時系列）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    streak, disabled = 0, False
    for block in re.split(r"(?=^## DR)", text, flags=re.M):
        if "action  : learn-disable" in block:
            disabled = True
        elif "action  : learn-misfire" in block:
            streak += 1
        elif "action  : learn-worked" in block:
            streak = 0
    return disabled or (limit > 0 and streak >= limit)


# ---------------------------------------------------------------------------
# Phase 4 結合点: W10 outcome を rule 単位へ一般化（第二評価系は作らない）
# ---------------------------------------------------------------------------
RULE_LIFECYCLE_STATES = (
    "candidate", "trial", "active", "suspended", "deprecated", "rejected",
)
RULE_OUTCOMES = ("worked", "misfire", "suppressed")
RULE_EXCLUDED_STATES = frozenset({"suspended", "deprecated", "rejected"})
_RULE_LIFECYCLE_RE = re.compile(
    r"^- rule-lifecycle:\s*(?P<id>obs-[0-9a-f]{16})\s+(?P<state>\S+)\s*$")
_RULE_OUTCOME_RE = re.compile(
    r"^- rule-outcome:\s*(?P<id>obs-[0-9a-f]{16})\s+(?P<out>worked|misfire|suppressed)\s*$")
_RULE_COMMENT_RE = re.compile(
    r"<!--\s*rule:(?P<id>obs-[0-9a-f]{16})\s+state:(?P<state>\S+).*?-->")


def rule_id_for_guide(guide: str, source: str = "") -> str:
    """rule 安定 ID = Phase3 observation_id（learn-capture）。task/rules_hash は混ぜない（版で ID が変わらない）。"""
    body, _scope = split_learn_scope(str(guide or ""))
    return observation_id(kind="learn-capture", body=body.strip(), source=str(source or ""))


def applied_rules_hash_ok(cfg: "Config", task: "Task") -> bool:
    """適用版 hash の健全性（Phase3 `rules_content_hash` / `sha256:<hex>` 経路を再利用）。

    タスクに刻印が無い旧経路は True。形式が Phase3 でなければ False（成功扱いしない）。
    現行 rules.md との一致は要求しない（trial 昇格追記で hash が変わり偽陰性になるため）。
    照合材料は provenance.rules_hash に残し、drift 検知は doctor（P5）へ委ねる。"""
    applied = str(task.get("rules_hash") or "").strip()
    if not applied:
        return True
    return bool(re.match(r"^sha256:[0-9a-f]{64}$", applied))


def _iter_decision_texts(cfg: "Config") -> "list[tuple[str, str]]":
    out: "list[tuple[str, str]]" = []
    if not cfg.decisions.exists():
        return out
    for f in sorted(cfg.decisions.glob("*.md")):
        try:
            out.append((f.stem, f.read_text(encoding="utf-8")))
        except OSError:
            continue
    return out


def rule_lifecycle_state(cfg: "Config", rule_id: str) -> str:
    """append-only の `- rule-lifecycle:` 最終状態。無ければ candidate。"""
    rid = str(rule_id or "").strip()
    state = "candidate"
    if not rid:
        return state
    for _src, text in _iter_decision_texts(cfg):
        for line in text.splitlines():
            m = _RULE_LIFECYCLE_RE.match(line.strip())
            if m and m.group("id") == rid and m.group("state") in RULE_LIFECYCLE_STATES:
                state = m.group("state")
    return state


def aggregate_rule_outcomes(cfg: "Config", rule_id: str) -> dict:
    """rule 単位の worked/misfire/suppressed 集計（W10 語彙の一般化。第二系なし）。"""
    rid = str(rule_id or "").strip()
    tallies = {"worked": 0, "misfire": 0, "suppressed": 0}
    streak = 0
    if not rid:
        return {**tallies, "misfire_streak": 0}
    for _src, text in _iter_decision_texts(cfg):
        for line in text.splitlines():
            m = _RULE_OUTCOME_RE.match(line.strip())
            if not m or m.group("id") != rid:
                continue
            out = m.group("out")
            tallies[out] = tallies.get(out, 0) + 1
            if out == "misfire":
                streak += 1
            elif out == "worked":
                streak = 0
            # suppressed は連続不発ストリークを切らない（悪化の証拠として残す）
    tallies["misfire_streak"] = streak
    return tallies


def append_rule_lifecycle(cfg: "Config", src: str, rule_id: str, state: str,
                          why: str = "") -> bool:
    """rule 状態遷移を出典 DR へ追記。自動遷移は trial / suspended のみ（active は outcome 条件付き）。"""
    rid = str(rule_id or "").strip()
    st = str(state or "").strip()
    if not rid or st not in RULE_LIFECYCLE_STATES:
        return False
    if rule_lifecycle_state(cfg, rid) == st:
        return False
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    path = decision_path(cfg, src)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- rule-lifecycle: {rid} {st}\n")
    if why:
        append_journal(cfg.journal, f"rule {rid} → {st}: {why[:120]}")
    return True


def append_rule_outcome(cfg: "Config", src: str, rule_id: str, outcome: str) -> None:
    rid = str(rule_id or "").strip()
    out = str(outcome or "").strip()
    if not rid or out not in RULE_OUTCOMES:
        return
    with decision_path(cfg, src).open("a", encoding="utf-8") as f:
        f.write(f"- rule-outcome: {rid} {out}\n")


def maybe_suspend_rule(cfg: "Config", src: str, rule_id: str, limit: int) -> bool:
    """悪化時 fail-close suspension。trial/active のみ。人手 manual は触らない。"""
    st = rule_lifecycle_state(cfg, rule_id)
    if st not in ("trial", "active"):
        return False
    agg = aggregate_rule_outcomes(cfg, rule_id)
    if limit <= 0 or agg.get("misfire_streak", 0) < limit:
        return False
    return append_rule_lifecycle(
        cfg, src, rule_id, "suspended",
        why=f"misfire_streak={agg['misfire_streak']}≥{limit}（fail-close）")


def maybe_activate_rule(cfg: "Config", src: str, rule_id: str) -> bool:
    """trial → active: promote_threshold（hits）+ outcome 条件（worked≥1・非 suspended）。"""
    if rule_lifecycle_state(cfg, rule_id) != "trial":
        return False
    hits = count_learn_hits(cfg).get(src, 0)
    if hits < int(getattr(cfg, "promote_threshold", 2) or 0):
        return False
    agg = aggregate_rule_outcomes(cfg, rule_id)
    if agg.get("worked", 0) < 1:
        return False
    return append_rule_lifecycle(
        cfg, src, rule_id, "active",
        why=f"hits={hits} worked={agg['worked']}（threshold+outcome）")


def rule_excluded_from_requests(cfg: "Config", rule_id: str) -> bool:
    """suspended 等は新規要求へ入らない（完了条件）。"""
    return rule_lifecycle_state(cfg, rule_id) in RULE_EXCLUDED_STATES


def write_rule_conflict_needs(cfg: "Config", rule_id: str, sources: "list[str]",
                              guide: str) -> None:
    """競合は自動マージせず needs へ（意味的マージは範囲外・決定的な同一 guide 衝突のみ）。"""
    rid = str(rule_id or "").strip()
    if not rid:
        return
    cfg.needs.mkdir(parents=True, exist_ok=True)
    tid = f"rule-{rid[4:12]}"
    path = needs_path(cfg, tid)
    if path.exists():
        return
    srcs = [s for s in sources if s]
    why = (f"同一 guidance の rule 競合（{rid}）。出典 {', '.join(srcs) or '?'}。"
           f"自動マージしない。人が一方を採用・改定・棄却する。")
    body = (
        f"# ルール競合: {tid}\n\n"
        f"## Context and Problem Statement\n\n"
        f"- なぜ: {why}\n"
        f"- rule: {rid}\n"
        f"- guide: {str(guide or '').strip()[:200]}\n"
        f"- 状態: needs（人の裁定待ち・自動マージなし）\n\n"
        f"{DECISION_MARKER}\n\n"
        f"<!-- 人の決定の記入欄。採用する出典・改定文・棄却を書いて [x]。 -->\n"
        f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n"
    )
    path.write_text(body, encoding="utf-8")
    anchor = srcs[0] if srcs else tid
    append_decision(cfg, anchor, "auto",
                    context=f"rule 競合 {rid}",
                    action="rule-conflict",
                    reason=why[:160],
                    affects=f"needs/{tid}.md")


def _best_learn_match(task: Task, threshold: float, files: "list[Path]",
                      label, skip_id: "str | None" = None,
                      pattern: "re.Pattern" = LEARN_RE,
                      cfg: "Config | None" = None) -> "tuple[str, str] | None":
    """与えた md 群の該当行（既定 `- learn:`／pattern で `- avoid:` 等に切替）を Jaccard で
    タイトル照合し最良を返す（決定的・LLM 不要）。pattern は title/guide の名前付きグループを持つこと。
    guide 末尾のスコープタグ（W10）はここで解釈し、スコープ外の行は候補にしない。
    Phase4: suspended rule は候補にしない（新規要求へ入らない）。"""
    best, best_score = None, 0.0
    for f in sorted(files):
        if skip_id is not None and f.stem == skip_id:  # 自分の履歴は除く（自己ループ防止）
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            m = pattern.match(line)
            if not m:
                continue
            guide, (kind, name) = split_learn_scope(m.group("guide"))
            if not _learn_scope_applies(task, kind, name):
                continue
            if cfg is not None and pattern is LEARN_RE:
                rid = rule_id_for_guide(m.group("guide"), f.stem)
                if rule_excluded_from_requests(cfg, rid):
                    continue
            score = _title_overlap(task.title, m.group("title"))
            if score >= threshold and score > best_score:
                best, best_score = (label(f), guide), score
    return best


def count_gitlab_reject_recur(cfg: "Config", task: Task) -> int:
    """他タスクの決定記録から、**gitlab 却下**でありタイトルが Jaccard 類似の件数を数える（決定的）。
    同種の却下が反復しているか（＝分解/verify/policy を系として見直すべきか）の判断材料。自分の履歴は除く。"""
    if not cfg.decisions.exists():
        return 0
    n = 0
    for f in sorted(cfg.decisions.glob("*.md")):
        if f.stem == task.id:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in re.split(r"(?=^## DR)", text, flags=re.M):
            if "action  : gitlab-reject" not in block:
                continue
            # 却下された元タスクの**生タイトル**（context の （…） 内）で照合する。蒸留後の learn
            # タイトルは一般化されていて raw タイトルとの Jaccard が効きにくいため context を優先。
            m = re.search(r"（(?P<title>[^（）]+)）が gitlab で却下", block)
            if not m:
                m = re.search(r"^- learn:\s*(?P<title>.+?)\s*::", block, flags=re.M)
            cand = m.group("title") if m else ""
            if cand and _title_overlap(task.title, cand) >= cfg.learn_threshold:
                n += 1
                break                                  # 1 ファイル（=1 タスク）につき 1 回
    return n


def find_learned_resolution(cfg: "Config", task: Task) -> "tuple[str, str] | None":
    """過去の人の判断（learn）からタイトルが十分似た指示を探す。返り値 (出典, 指示文)。

    ① ローカル `decisions/` を照合 → ② ヒット無し かつ cfg.ltm なら ltm-use home を横断照合。
    どちらも決定的なファイル走査＋Jaccard で、エージェント（LLM）を一切起動しない。
    Phase4: W10 失効に加え、suspended rule は候補外（新規要求へ入らない）。"""
    local = []
    if cfg.decisions.exists():
        limit = int(getattr(cfg, "learn_misfire_limit", 3) or 0)
        files = [f for f in cfg.decisions.glob("*.md")
                 if not learn_suppressed(f, limit)]     # 失効した出典は適用しない（W10）
        local = _best_learn_match(task, cfg.learn_threshold, files,
                                  label=lambda f: f.stem, skip_id=task.id, cfg=cfg)
    if local:
        return local
    if cfg.ltm:
        mem_dir = ltm_memories_dir(cfg)
        if mem_dir and mem_dir.exists():
            return _best_learn_match(task, cfg.learn_threshold, list(mem_dir.glob("*.md")),
                                     label=lambda f: f"ltm:{f.stem}", cfg=cfg)
    return None


def record_learn_outcome(cfg: "Config", task: Task, worked: bool, why: str = "") -> None:
    """auto-resolve（learn 適用）の結末を**出典の決定記録**へ返す（W10・タスクごと 1 回）。

    done なら learn-worked、再 blocked なら learn-misfire。出典ファイル内の追記順が時系列なので、
    learn_suppressed が「worked を挟まない misfire の連続」をそのまま数えられる。ltm 出典は
    ローカルに決定記録が無いので対象外。

    Phase4 結合点: 同じ語彙を rule 単位へも一般化（`- rule-outcome:`）。適用版 hash 不一致の
    成功主張は suppressed（fail-close）。連続 misfire は rule を suspended へ。"""
    src = str(task.get("autolearned") or "").strip()
    if not src or src == "1" or src.startswith("ltm:") or task.get("learn_outcome"):
        return
    hash_ok = applied_rules_hash_ok(cfg, task)
    if worked and not hash_ok:
        rule_out = "suppressed"
        w10_worked = False  # 版不一致の成功は W10 でも成功にしない（fail-close）
    elif worked:
        rule_out = "worked"
        w10_worked = True
    else:
        rule_out = "misfire"
        w10_worked = False
    task.extra.append(("learn_outcome", rule_out if rule_out != "suppressed" else "misfire"))
    prov = None
    try:
        prov = build_provenance(cfg, task)
    except Exception:  # noqa: BLE001 — provenance は additive・失敗でも outcome 自体は残す
        prov = None
    append_decision(cfg, src, "auto",
                    context=f"{task.id}（{task.title}）への learn 適用の結果",
                    action="learn-worked" if w10_worked else "learn-misfire",
                    reason=(("成功: " if w10_worked else (
                        "版不一致: " if rule_out == "suppressed" else "不発: "))
                            + f"{task.id} {why}").strip()[:160],
                    affects=src,
                    provenance=prov)
    # rule 単位へ一般化（安定 ID は task.rule_id 優先、無ければ guide から再導出）
    rid = str(task.get("rule_id") or "").strip()
    guide = str(task.get("feedback") or "").strip()
    if not rid and guide:
        rid = rule_id_for_guide(guide, src)
    if not rid:
        for s, _title, g in collect_learnings(cfg):
            if s == src:
                rid = rule_id_for_guide(g, src)
                break
    if rid:
        append_rule_outcome(cfg, src, rid, rule_out)
        limit = int(getattr(cfg, "learn_misfire_limit", 3) or 0)
        if rule_out == "misfire":
            maybe_suspend_rule(cfg, src, rid, limit)
        elif rule_out == "worked":
            maybe_activate_rule(cfg, src, rid)


def find_avoidance(cfg: "Config", task: Task) -> "tuple[str, str] | None":
    """過去の hold/deny 判断（`- avoid:`）からタイトルが十分似た案件を探す。返り値 (出典, 理由)。

    learn（どう解けば良いか＝auto-resolve 向け）とは別軸で、『この種は自動実行させない＝人へ』の
    予防知識。投入/triage の段階で ready へ落とす前に照合し、一致すれば inbox（人の triage）へ寄せる。
    ローカル `decisions/` の決定的走査＋Jaccard のみ（エージェント不要）。"""
    if not cfg.decisions.exists():
        return None
    return _best_learn_match(task, cfg.learn_threshold, list(cfg.decisions.glob("*.md")),
                             label=lambda f: f.stem, skip_id=task.id, pattern=AVOID_RE)


def apply_intake_recall(cfg: "Config", task: Task) -> "str | None":
    """投入/triage 時の予防リコール（shift-left）。intake_recall 有効かつ task が消化対象(ready)で、
    過去の hold 判断（avoid）に類似するなら、実行前に **blocked＋needs（人の判断）へ寄せて**理由を残す。
    DR 学習が『失敗してから』人を絞るのに対し、これは『投入の時点で』先回りして止める。人は
    `approve`（実行を許可）か `hold`（恒久デニー化）で裁定できる。返り値は寄せた理由（表示用）。
    該当なし・無効・非消化なら None（タスクは素通り）。

    ※ inbox ではなく blocked にするのは、verify を持つタスクは triage が inbox→ready へ自動昇格する
    ため（人の判断を待たずに実行され得る）。hold と同じ blocked＋needs が『人の裁定待ち』の正しい状態。"""
    if not cfg.intake_recall or task.norm_status() not in CONSUMABLE:
        return None
    hit = find_avoidance(cfg, task)
    if not hit:
        return None
    src, reason = hit
    task.set("recall", f"{src} :: {reason}")   # 人が needs で見えるよう出典と理由を残す
    why = (f"予防リコール: 過去に hold した案件（{src}）に類似するため実行前に人の判断へ。"
           f"理由: {reason}（許可するなら approve、恒久デニーなら hold）")
    _block(cfg, task, why, {})                 # blocked＋needs/<id>.md（persist はここで行う）
    append_decision(cfg, task.id, "auto",
                    context=f"{task.id}（{task.title}）を投入時リコールで人の判断へ",
                    action="intake-recall", reason=f"過去の hold（{src}）に類似: {reason}",
                    affects=f"{task.id} → blocked, needs/{task.id}.md")
    return reason


# ---------------------------------------------------------------------------
# ltm-use への学習昇格（決定的・エージェント不要。home の Markdown を直接読み書き）
# ---------------------------------------------------------------------------
def resolve_ltm_home(arg: "str | None") -> Path:
    """ltm-use ストアのルート: 明示指定 → 環境変数 KIRO_LTM_HOME → ~/.claude。"""
    raw = arg or os.environ.get("KIRO_LTM_HOME") or "~/.claude"
    return Path(raw).expanduser()


def ltm_memories_dir(cfg: "Config") -> "Path | None":
    """昇格先 `<home>/memory/home/memories/agent-project`。ltm 無効なら None。"""
    if not cfg.ltm or cfg.ltm_home is None:
        return None
    return cfg.ltm_home / "memory" / "home" / "memories" / LTM_CATEGORY


# ---------------------------------------------------------------------------
