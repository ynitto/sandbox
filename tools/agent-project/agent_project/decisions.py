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


def _best_learn_match(task: Task, threshold: float, files: "list[Path]",
                      label, skip_id: "str | None" = None,
                      pattern: "re.Pattern" = LEARN_RE) -> "tuple[str, str] | None":
    """与えた md 群の該当行（既定 `- learn:`／pattern で `- avoid:` 等に切替）を Jaccard で
    タイトル照合し最良を返す（決定的・LLM 不要）。pattern は title/guide の名前付きグループを持つこと。
    guide 末尾のスコープタグ（W10）はここで解釈し、スコープ外の行は候補にしない。"""
    best, best_score = None, 0.0
    for f in sorted(files):
        if skip_id is not None and f.stem == skip_id:  # 自分の履歴は除く（自己ループ防止）
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line)
            if not m:
                continue
            guide, (kind, name) = split_learn_scope(m.group("guide"))
            if not _learn_scope_applies(task, kind, name):
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
    どちらも決定的なファイル走査＋Jaccard で、エージェント（LLM）を一切起動しない。"""
    local = []
    if cfg.decisions.exists():
        limit = int(getattr(cfg, "learn_misfire_limit", 3) or 0)
        files = [f for f in cfg.decisions.glob("*.md")
                 if not learn_suppressed(f, limit)]     # 失効した出典は適用しない（W10）
        local = _best_learn_match(task, cfg.learn_threshold, files,
                                  label=lambda f: f.stem, skip_id=task.id)
    if local:
        return local
    if cfg.ltm:
        mem_dir = ltm_memories_dir(cfg)
        if mem_dir and mem_dir.exists():
            return _best_learn_match(task, cfg.learn_threshold, list(mem_dir.glob("*.md")),
                                     label=lambda f: f"ltm:{f.stem}")
    return None


def record_learn_outcome(cfg: "Config", task: Task, worked: bool, why: str = "") -> None:
    """auto-resolve（learn 適用）の結末を**出典の決定記録**へ返す（W10・タスクごと 1 回）。

    done なら learn-worked、再 blocked なら learn-misfire。出典ファイル内の追記順が時系列なので、
    learn_suppressed が「worked を挟まない misfire の連続」をそのまま数えられる。ltm 出典は
    ローカルに決定記録が無いので対象外。"""
    src = str(task.get("autolearned") or "").strip()
    if not src or src == "1" or src.startswith("ltm:") or task.get("learn_outcome"):
        return
    task.extra.append(("learn_outcome", "worked" if worked else "misfire"))
    append_decision(cfg, src, "auto",
                    context=f"{task.id}（{task.title}）への learn 適用の結果",
                    action="learn-worked" if worked else "learn-misfire",
                    reason=(("成功: " if worked else "不発: ") + f"{task.id} {why}").strip()[:160],
                    affects=src)


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
