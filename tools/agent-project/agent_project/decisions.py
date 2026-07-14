from __future__ import annotations
# decisions.py — 元 agent-project.py の 913-1085 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
def decision_path(cfg: "Config", tid: str) -> Path:
    return cfg.decisions / f"{tid}.md"


def next_dr_id(path: Path) -> str:
    n = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = DR_HEADER_RE.match(line)
            if m:
                n = max(n, int(m.group(1)))
    return f"DR-{n + 1:04d}"


def append_decision(cfg: "Config", tid: str, actor: str, context: str,
                    action: str, reason: str, affects: str,
                    learn: "tuple[str, str] | None" = None,
                    avoid: "tuple[str, str] | None" = None) -> str:
    """決定記録を追記。learn=(title, guidance) を渡すと『- learn:』行を残し、
    将来 find_learned_resolution が類似タスクへ自動適用できる学習材料にする。
    avoid=(title, reason) を渡すと『- avoid:』行を残し、hold/deny の予防知識として
    投入/triage 時の類似タスク検出（find_avoidance）に使えるようにする。"""
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    path = decision_path(cfg, tid)
    dr = next_dr_id(path)
    date = datetime.now().strftime("%Y-%m-%d")
    block = (f"## {dr}  {date}  actor: {actor}\n"
             f"- context : {context}\n- action  : {action}\n"
             f"- reason  : {reason}\n- affects : {affects}\n")
    if learn:
        title, guide = learn
        block += f"- learn: {title.replace(chr(10), ' ')} :: {guide.replace(chr(10), ' ')}\n"
    if avoid:
        title, guide = avoid
        block += f"- avoid: {title.replace(chr(10), ' ')} :: {guide.replace(chr(10), ' ')}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")
    return dr


# ---------------------------------------------------------------------------
# DR 学習（過去の人の判断から類似案件を自動解決して通知を減らす）
# ---------------------------------------------------------------------------
def _title_overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _best_learn_match(task: Task, threshold: float, files: "list[Path]",
                      label, skip_id: "str | None" = None,
                      pattern: "re.Pattern" = LEARN_RE) -> "tuple[str, str] | None":
    """与えた md 群の該当行（既定 `- learn:`／pattern で `- avoid:` 等に切替）を Jaccard で
    タイトル照合し最良を返す（決定的・LLM 不要）。pattern は title/guide の名前付きグループを持つこと。"""
    best, best_score = None, 0.0
    for f in sorted(files):
        if skip_id is not None and f.stem == skip_id:  # 自分の履歴は除く（自己ループ防止）
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line)
            if not m:
                continue
            score = _title_overlap(task.title, m.group("title"))
            if score >= threshold and score > best_score:
                best, best_score = (label(f), m.group("guide").strip()), score
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
    どちらも決定的なファイル走査＋Jaccard で、エージェント（LLM）を一切起動しない。"""
    local = []
    if cfg.decisions.exists():
        local = _best_learn_match(task, cfg.learn_threshold,
                                  list(cfg.decisions.glob("*.md")),
                                  label=lambda f: f.stem, skip_id=task.id)
    if local:
        return local
    if cfg.ltm:
        mem_dir = ltm_memories_dir(cfg)
        if mem_dir and mem_dir.exists():
            return _best_learn_match(task, cfg.learn_threshold, list(mem_dir.glob("*.md")),
                                     label=lambda f: f"ltm:{f.stem}")
    return None


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
