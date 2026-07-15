from __future__ import annotations
# model.py — 元 agent-project.py の 78-615 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。


# ---------------------------------------------------------------------------
# タスク（案件毎ファイル backlog/<id>.md）
# ---------------------------------------------------------------------------
@dataclass
class Task:
    id: str
    title: str
    status: str = "ready"
    source: str = "human"
    priority: int = 0      # 外部で付与する優先度（大きいほど高優先。none planner で使う）
    verify: str = ""
    retries: int = 0
    extra: "list[tuple[str, str]]" = field(default_factory=list)

    def norm_status(self) -> str:
        return self.status if self.status in VALID_STATUS or self.status == "todo" else "ready"

    def consumable(self) -> bool:
        return self.norm_status() in CONSUMABLE

    def matches(self, pattern: str) -> bool:
        p = pattern.strip().lower()
        return bool(p) and (p in self.id.lower() or p in self.title.lower())

    def feedback(self) -> "str | None":
        return self.get("feedback")

    def get(self, key: str, default=None):
        """追加フィールド（extra）の値。重複キーは dict と同じく最後を採る。"""
        return dict(self.extra).get(key, default)

    def set(self, key: str, value) -> None:
        """追加フィールドを 1 つに正規化して設定（既存の同名は落としてから付け直す）。"""
        self.drop(key)
        self.extra.append((key, str(value)))

    def drop(self, *keys: str) -> None:
        self.extra = [(k, v) for k, v in self.extra if k not in keys]


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """端末カラー等の ANSI エスケープを除去する。
    エージェント CLI の出力にはカラーコードが混ざるため、合成した verify を
    シェルで実行する前に正規化しないと `\\x1b[..m` が混入してコマンドが壊れる。"""
    return _ANSI_RE.sub("", text or "")


def _strip_code(val: str) -> str:
    v = strip_ansi(val).strip()
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        return v[1:-1]
    return v


def _now_ts() -> str:
    """人間可読のローカル時刻 `YYYY-MM-DD HH:MM:SS`（journal/納品書/needs などの記録に使う共通形式）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_task(text: str, tid: str) -> Task:
    """1ファイル＝1タスク。id はファイル名（tid）を正とする。"""
    t = Task(id=tid, title=tid)
    for line in text.splitlines():
        m = TASK_HEADER_RE.match(line)
        if m:
            t.title = m.group("title").strip() or tid
            continue
        fm = FIELD_RE.match(line)
        if not fm:
            continue
        key, val = fm.group("key").strip(), fm.group("val").strip()
        if key == "status":
            t.status = val or "ready"
        elif key == "source":
            t.source = val or "human"
        elif key == "priority":
            try:
                t.priority = int(val)
            except ValueError:
                t.priority = 0
        elif key == "verify":
            t.verify = _strip_code(val)
        elif key == "retries":
            try:
                t.retries = int(val)
            except ValueError:
                t.retries = 0
        else:
            t.extra.append((key, val))
    return t


def serialize_task(task: Task) -> str:
    out = [
        f"## {task.id}: {task.title}",
        f"- status: {task.norm_status()}",
        f"- source: {task.source}",
        f"- priority: {task.priority}",
        f"- verify: {f'`{task.verify}`' if task.verify else ''}",
        f"- retries: {task.retries}",
    ]
    out += [f"- {k}: {v}" for k, v in task.extra]
    return "\n".join(out) + "\n"


def load_tasks(backlog_dir: Path) -> "list[Task]":
    """backlog/ の各 *.md を1タスクとして読む。最古優先（mtime 昇順）に並べる。"""
    if not backlog_dir.exists():
        return []
    files = sorted(backlog_dir.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    return [parse_task(p.read_text(encoding="utf-8"), p.stem) for p in files]


def persist_task(cfg: "Config", task: Task) -> None:
    """タスク md をアトミックに書く（temp → os.replace）。直接 write_text すると、
    並行する state 同期・viewer・load_tasks が書きかけ（途中まで/空）のファイルを読んで
    status を既定値（inbox）に誤認したり、書き込み途中のクラッシュでタスクが壊れる。"""
    cfg.backlog.mkdir(parents=True, exist_ok=True)
    dst = cfg.backlog / f"{task.id}.md"
    tmp = cfg.backlog / f".{task.id}.md.tmp.{os.getpid()}"
    tmp.write_text(serialize_task(task), encoding="utf-8")
    os.replace(tmp, dst)


def delete_task_file(cfg: "Config", task: Task) -> None:
    p = cfg.backlog / f"{task.id}.md"
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# enqueue（汎用の取り込み口）— 外部ソース(webhook/メール/issue 抽出)は薄いアダプタで
#   ここへ流し込む。コアは stdlib のみ・ネットワーク非依存・決定的を保つ。
# ---------------------------------------------------------------------------
ENQUEUE_KNOWN_KEYS = {"id", "title", "verify", "priority", "source", "status",
                      "after", "review", "note", "accept", "verify_template", "repos",
                      "workspace", "refs", "paths", "routed_by",
                      "cohort_items", "cohort", "cohort_role"}


def _slug_id(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (text or "").strip()).strip("-")
    return s[:48]


def _unique_task_id(cfg: "Config", base: str, include_archive: bool = False) -> str:
    existing = {p.stem for p in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    if include_archive:
        # 自動採番では archive も避ける: 退避済みと同じ id を採番すると、後で archive へ移す際に
        # 過去の記録を上書きしてしまう（複数 charter で同名タスクが並ぶと同秒衝突が現実に起きる）。
        adir = cfg.archive_dir()
        if adir.exists():
            existing |= {p.stem for p in adir.glob("*.md")}
    base = base or "task"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _gen_task_id(cfg: "Config", explicit: "str | None", title: str) -> str:
    if explicit:
        # 明示 id は**冪等キー**（intake の重複判定・再投入の追跡に使う）なので改名しない
        # （backlog 内の衝突だけ回避）。archive 側の上書きは退避時にファイル名で避ける。
        return _unique_task_id(cfg, _slug_id(explicit) or "task")
    slug = _slug_id(title)
    base = (f"{slug[:24]}-{datetime.now().strftime('%H%M%S')}" if slug
            else "enq-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    return _unique_task_id(cfg, base, include_archive=True)


def task_from_spec(cfg: "Config", spec: dict) -> Task:
    """spec(dict) を検証して Task を作る。title 必須。status 未指定なら verify 有→ready / 無→inbox。"""
    title = str(spec.get("title", "") or "").strip()
    if not title:
        raise ValueError("title は必須です")
    verify = _strip_code(str(spec.get("verify", "") or "").strip())
    accept = str(spec.get("accept", "") or "").strip()
    tmpl = str(spec.get("verify_template", "") or "").strip()
    tid = _gen_task_id(cfg, spec.get("id"), title)
    # verify が無くても accept / verify_template があれば「verify を用意できる」ので ready 扱い（後で展開/合成）
    has_plan = bool(verify or accept or tmpl)
    explicit = str(spec.get("status", "") or "").strip()
    default_status = "ready" if has_plan else "inbox"
    # 実行前レビュー（plan_review・既定 on）: status を明示しない新規投入はすべて proposed で入り、
    # 人の承認（approve）で初めて実行可能（ready/inbox）になる（plan/enqueue/inbox/followup/intake 全経路）。
    if not explicit and getattr(cfg, "plan_review", False):
        default_status = "proposed"
    status = explicit or default_status
    t = Task(id=tid, title=title, status=status, verify=verify,
             source=str(spec.get("source", "") or "enqueue"))
    try:
        t.priority = int(spec.get("priority", 0) or 0)
    except (TypeError, ValueError):
        t.priority = 0
    for k in ("after", "review", "note", "accept", "verify_template", "repos",   # 既知の追加フィールド
              "workspace", "refs", "paths", "routed_by"):   # ルーティング: 書込先・参照repo・触るパス・解決経路
        v = spec.get(k)
        if v not in (None, "", []):
            t.extra.append((k, ",".join(map(str, v)) if isinstance(v, list) else str(v)))
    for k, v in spec.items():                        # 未知キーも保持（取りこぼさない）
        if k not in ENQUEUE_KNOWN_KEYS and v not in (None, "", []):
            t.extra.append((str(k), str(v)))
    if not t.verify and tmpl:                        # テンプレは決定的＝enqueue 時に即展開（エージェント不要）
        ex = expand_verify_template(tmpl)
        if ex:
            t.verify = ex
            t.extra.append(("verify_source", "template"))
    return t


def enqueue_task(cfg: "Config", spec: dict) -> Task:
    # cohort_items があれば「pilot 先行 → 人レビューで指示を固める → 残りを生成」の cohort にする
    if spec.get("cohort_items"):
        t = create_cohort(cfg, spec)
    else:
        t = task_from_spec(cfg, spec)
    cfg.backlog.mkdir(parents=True, exist_ok=True)
    persist_task(cfg, t)
    return t


# ---------------------------------------------------------------------------
# cohort（pilot-then-batch）— タスク分解で生じた「同様手順の繰り返しタスク」を、
#   まず 1 件（pilot）だけ走らせて verify→review:human で指示を固め、その定義を元に
#   残りのタスク指示を生成して実行する。act 非依存（残りは通常ループが任意の act で消化）。
#   pilot 承認の検知は既存の review/approve を再利用し、承認時に残りを materialize する。
# ---------------------------------------------------------------------------
COHORT_ITEM_TOKEN = "{item}"


def _apply_item(template: str, item: str, fallback: bool = True) -> str:
    """テンプレ中の {item} を対象で差し込む。
    fallback=True（title 用）はプレースホルダ無しなら末尾に対象を付す。
    fallback=False（verify 用）はプレースホルダ無しならコマンドをそのまま使う（全要素で共通）。"""
    if COHORT_ITEM_TOKEN in template:
        return template.replace(COHORT_ITEM_TOKEN, item)
    if not fallback:
        return template
    return f"{template}（対象: {item}）" if template else item


def _cohort_path(cfg: "Config", cid: str) -> Path:
    return cfg.cohorts_dir() / f"{cid}.json"


def _write_cohort(cfg: "Config", state: dict) -> None:
    cfg.cohorts_dir().mkdir(parents=True, exist_ok=True)
    _cohort_path(cfg, state["id"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_cohort(cfg: "Config", cid: str) -> "dict | None":
    p = _cohort_path(cfg, cid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _unique_cohort_id(cfg: "Config", base: str) -> str:
    existing = {p.stem for p in cfg.cohorts_dir().glob("*.json")} if cfg.cohorts_dir().exists() else set()
    base = base or "cohort"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def create_cohort(cfg: "Config", spec: dict) -> Task:
    """同様手順の cohort を作る: 先頭要素を pilot（review:human）として 1 件だけ作り、
    残りは cohort 状態へ保持する。pilot 承認後に materialize_cohort_rest が残りを生成する。"""
    items = [str(x).strip() for x in (spec.get("cohort_items") or []) if str(x).strip()]
    title_t = str(spec.get("title", "") or "").strip()
    verify_t = _strip_code(str(spec.get("verify", "") or "").strip())
    if not title_t:
        raise ValueError("cohort には title が必要です")
    if not items:
        raise ValueError("cohort には cohort_items が必要です")
    cid = _unique_cohort_id(cfg, _slug_id(title_t) or "cohort")
    pilot_item, rest = items[0], items[1:]
    repos = spec.get("repos")
    pilot_spec = {
        "title": _apply_item(title_t, pilot_item),
        "verify": _apply_item(verify_t, pilot_item, fallback=False) if verify_t else "",
        "accept": spec.get("accept"),
        "review": "human",                 # pilot は人の承認（feedback）で指示を固める
        "source": str(spec.get("source", "") or "cohort"),
        "repos": repos,
        "priority": spec.get("priority", 0),
    }
    pilot = task_from_spec(cfg, pilot_spec)
    pilot.set("cohort", cid)
    pilot.set("cohort_role", "pilot")
    persist_task(cfg, pilot)
    _write_cohort(cfg, {
        "id": cid,
        "pilot_id": pilot.id,
        "title_template": title_t,
        "verify_template": verify_t,
        "accept": str(spec.get("accept", "") or ""),
        "items": rest,                     # pilot 承認後に生成する残り要素
        "repos": ",".join(repos) if isinstance(repos, list) else (repos or ""),
        "source": str(spec.get("source", "") or "cohort"),
        "status": "pending",
        "feedback": "",
    })
    append_journal(cfg.journal, f"cohort {cid}: pilot {pilot.id} を作成（残り {len(rest)} 件は承認後に生成）")
    return pilot


def materialize_cohort_rest(cfg: "Config", pilot: Task, feedback: str = "") -> "list[Task]":
    """pilot で固まった定義を元に残りの cohort タスクを生成して ready にする。
    pilot の承認理由・feedback を各メンバの feedback に載せ、固めた指示を必ず反映させる。"""
    cid = pilot.get("cohort")
    if not cid:
        return []
    state = _read_cohort(cfg, cid)
    if not state or state.get("status") != "pending":
        return []
    guidance = "\n".join(x for x in [state.get("feedback", ""), feedback, pilot.feedback() or ""] if x).strip()
    repos = state.get("repos") or None
    created: "list[Task]" = []
    for item in state.get("items", []):
        mspec = {
            "title": _apply_item(state["title_template"], item),
            "verify": _apply_item(state["verify_template"], item, fallback=False) if state.get("verify_template") else "",
            "accept": state.get("accept") or None,
            "source": str(state.get("source", "") or "cohort"),
            "repos": repos,
        }
        m = task_from_spec(cfg, mspec)
        m.set("cohort", cid)
        m.set("cohort_role", "member")
        if guidance:
            m.set("feedback", guidance)     # build_request が「必ず反映」として act へ渡す
        persist_task(cfg, m)
        created.append(m)
    state["status"] = "done"
    state["feedback"] = guidance
    _write_cohort(cfg, state)
    append_journal(cfg.journal,
                   f"cohort {cid}: pilot {pilot.id} 承認 → 固めた定義から残り {len(created)} 件を生成")
    return created


def cohort_reflux(cfg: "Config", task: "Task", guidance: str) -> int:
    """gitlab で cohort のメンバ/pilot が却下されたら、その指摘を**同 cohort の未完了の兄弟メンバ**へ波及させる。
    materialize_cohort_rest（pilot 承認からの一方向）に対し、こちらは却下起点で兄弟へ横展開する（双方向化）。
    波及した件数を返す。cohort でない/指摘が空なら 0。"""
    cid = task.get("cohort")
    if not cid or not guidance.strip():
        return 0
    n = 0
    for sib in load_tasks(cfg.backlog):
        if sib.id == task.id or sib.get("cohort") != cid:
            continue
        if sib.norm_status() == "done":
            continue
        sib.drop("feedback")
        sib.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
        persist_task(cfg, sib)
        n += 1
    state = _read_cohort(cfg, cid)
    if state is not None:
        merged = "\n".join(x for x in [state.get("feedback", ""), guidance] if x).strip()
        state["feedback"] = merged[:2000]
        _write_cohort(cfg, state)
    if n:
        append_journal(cfg.journal, f"cohort {cid}: {task.id} の却下指摘を未完了メンバ {n} 件へ波及")
    return n


def already_completed(cfg: "Config", tid: str) -> bool:
    """その id は既に done して archive にあるか。

    明示 id は **冪等キー**（同じ id = 同じタスク）。done 済みの id が再投入されたら、それは
    「同じ作業をもう一度やれ」ではなく重複投入である。弾かないと完了済みの作業がまるごと
    再実行され、LLM のコストを無駄に払う（実際 done → archive 済みのタスクが inbox 経由で
    復活し、新しい run が回り始めた）。再発した別件なら新しい id で投入されるべき。"""
    if not tid:
        return False
    try:
        return (cfg.archive_dir() / f"{tid}.md").exists()
    except OSError:
        return False


def ingest_inbox(cfg: "Config") -> "list[Task]":
    """inbox/ に置かれたファイルを backlog タスクへ取り込む（.json=オブジェクト/配列 / .md=タスク形式）。
    取り込めたら元ファイルを消す。外部ソースの共通入口（watch がこの口を監視して起こす）。

    done 済み（archive にある）id の再投入は取り込まない。冪等キーとしての id の意味を守り、
    完了済みの作業が再実行されるのを防ぐ（already_completed 参照）。"""
    created: list[Task] = []
    skipped: list[str] = []
    inbox = cfg.inbox
    if not inbox or not inbox.exists():
        return created
    for f in sorted(inbox.glob("*")):
        if f.is_dir():
            continue
        try:
            if f.suffix.lower() == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
                for sp in (data if isinstance(data, list) else [data]):
                    if not isinstance(sp, dict):
                        continue
                    sid = _slug_id(str(sp.get("id") or "").strip())
                    if already_completed(cfg, sid):
                        skipped.append(sid)
                        continue
                    created.append(enqueue_task(cfg, sp))
            elif f.suffix.lower() in (".md", ".markdown", ".txt"):
                t = parse_task(f.read_text(encoding="utf-8"), f.stem)
                if already_completed(cfg, _slug_id(t.id)):
                    skipped.append(_slug_id(t.id))
                    try:
                        f.unlink()          # 消さないと毎周同じ警告を出し続ける
                    except OSError:
                        pass
                    continue
                t.id = _unique_task_id(cfg, _slug_id(t.id) or "task")
                if t.source == "human":
                    t.source = "inbox"
                if t.norm_status() == "ready" and not has_verify_plan(t):
                    t.status = "inbox"               # verify も用意材料(accept/template)も無ければ人の triage へ
                if getattr(cfg, "plan_review", False) and t.norm_status() in ("ready", "inbox"):
                    t.status = "proposed"            # 実行前レビュー: 承認まで実行しない
                cfg.backlog.mkdir(parents=True, exist_ok=True)
                persist_task(cfg, t)
                created.append(t)
            else:
                continue
        except (OSError, ValueError) as e:
            append_journal(cfg.journal, f"inbox 取り込み失敗: {f.name}: {e}")
            continue
        try:
            f.unlink()
        except OSError:
            pass
    if created:
        append_journal(cfg.journal, f"inbox 取り込み {[t.id for t in created]}")
    if skipped:
        append_journal(cfg.journal,
                       f"inbox 取り込みを見送り（既に done・archive にある）: {skipped}。"
                       f"同じ id は冪等キーなので再実行しない。やり直すなら新しい id で投入すること")
    return created


# intake の最終実行時刻（プロジェクト＝backlog パス毎。--project all の 1 プロセス多重化に対応）
_INTAKE_LAST: "dict[str, float]" = {}


def _codd_gate_debt_module():
    """`codd_gate_debt`（tools/agent-project 直下の sibling module。`codd-gate tasks --debt` 等の
    出力をレコード単位で検証・正規化する）を遅延 import する。

    `__init__.py` の exec 合成により、このフラグメント内の `__file__` は常に
    `agent_project/__init__.py` の実パスを指す（instances.py の `_self_script` と同じ前提）。
    その1階層上（`tools/agent-project/`）が sibling module の置き場なので sys.path に足す。
    見つからない・import 失敗のときは None を返し、呼び出し側は既存の緩いパースへ no-op 縮退する
    （codd_gate_status の usable=False 縮退と同じ方針。外部連携の欠落で intake 自体は壊さない）。"""
    try:
        import codd_gate_debt
        return codd_gate_debt
    except ImportError:
        pass
    sib = Path(__file__).resolve().parent.parent
    if str(sib) not in sys.path:
        sys.path.insert(0, str(sib))
    try:
        import codd_gate_debt
        return codd_gate_debt
    except ImportError:
        return None


def run_intake(cfg: "Config") -> "list[Task]":
    """取り込みコマンド（intake_cmd）を実行し、stdout の JSON（spec オブジェクト/配列＝
    `enqueue --json` と同形式）を backlog へ**冪等に**取り込む。外部の決定的ゲート/検出器
    （例: `codd-gate tasks --debt`）を watch の周期で汲み上げる汎用フック。

    - **冪等**: spec の `id` が現役 backlog（blocked/review 含む）に居れば飛ばす。定期実行しても
      同じ発見が重複投入されない（done→archive 後に同じ発見が再発したら新タスクとして積み直せる）。
    - **有限・無害**: verify_timeout で打ち切り、exit≠0・非 JSON・例外は journal に残して無視
      （ループは殺さない）。intake_interval（秒）で律速し、0 以下なら毎回。
    - **レコード単位の検証**: `codd_gate_debt`（同梱・sibling module）が使えれば
      `parse_debt_output` で1レコードずつ検証し、不備（非 object・title 欠落）は該当レコードだけ
      journal へ落として残りは取り込みを続ける（1件の不備で全体を捨てない）。使えない環境
      （sibling module 欠落）では従来どおりの緩いパース（非 dict を黙って読み飛ばす）に縮退する。
    - 常駐（長期実行）は agent-project 側が持つ。intake_cmd 自体は単発・有界であること。"""
    if not cfg.intake_cmd:
        return []
    interval = float(cfg.intake_interval or 0)
    key = str(cfg.backlog)
    now = time.time()
    if interval > 0 and now - _INTAKE_LAST.get(key, 0.0) < interval:
        return []
    _INTAKE_LAST[key] = now
    try:
        p = subprocess.run(cfg.intake_cmd, shell=True, cwd=str(cfg.workdir),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=cfg.verify_timeout)
    except (OSError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"intake 実行失敗: {e}")
        return []
    if p.returncode != 0:
        append_journal(cfg.journal, f"intake NG (exit {p.returncode}): {cfg.intake_cmd}")
        return []
    out = (p.stdout or "").strip()
    if not out:
        return []
    debt = _codd_gate_debt_module()
    if debt is not None:
        result = debt.parse_debt_output(out)
        for err in result.errors:
            append_journal(cfg.journal, f"intake レコード無効: {err}")
        specs = [item.to_spec() for item in result.items]
    else:
        try:
            data = json.loads(out)
        except ValueError:
            append_journal(cfg.journal, "intake 出力が JSON でないため無視")
            return []
        specs = [sp for sp in (data if isinstance(data, list) else [data]) if isinstance(sp, dict)]
    created: "list[Task]" = []
    existing = {f.stem for f in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    for sp in specs:
        sid = _slug_id(str(sp.get("id", "") or ""))
        if sid and sid in existing:
            continue                        # 冪等: 現役 backlog に居る発見は再投入しない
        try:
            created.append(enqueue_task(cfg, sp))
        except ValueError as e:
            append_journal(cfg.journal, f"intake spec 無効: {e}")
            continue
        if sid:
            existing.add(sid)
    if created:
        append_journal(cfg.journal, f"intake 取り込み {[t.id for t in created]}")
    return created


_FOLLOWUP_LINE_RE = re.compile(r"^@followup\s+(?P<spec>.+)$")


def parse_followups(parent: "Task", act_msg: str) -> "list[tuple[str, str]]":
    """完了タスクから派生タスク仕様 (title, verify) を集める。2 経路:
    静的: 親タスクの `- followup: <title> [:: <verify>]`／
    動的: act 出力の `@followup <title> [:: <verify>]` 行（エージェントが「ついでに見つけた」を吐く）。"""
    specs: list[tuple[str, str]] = []

    def add(raw: str):
        raw = raw.strip()
        if not raw:
            return
        title, _, verify = raw.partition("::")
        specs.append((title.strip(), _strip_code(verify.strip())))

    for k, v in parent.extra:
        if k == "followup":
            add(v)
    for line in (act_msg or "").splitlines():
        m = _FOLLOWUP_LINE_RE.match(line.strip())
        if m:
            add(m.group("spec"))
    return specs


def spawn_followups(cfg: "Config", parent: "Task", specs: "list[tuple[str, str]]",
                    tasks: "list[Task] | None", cap: int) -> "list[Task]":
    """派生タスクを backlog/<parent>-fN.md として作る（source=followup）。verify があれば ready で
    即消化対象、無ければ inbox（triage で人へ）。cap でこの run の生成数を制限し暴走を防ぐ。
    tasks を渡すと同じ run 内で自走消化できるよう追記する。"""
    spawned: list[Task] = []
    existing = {p.stem for p in cfg.backlog.glob("*.md")}
    if tasks:
        existing |= {t.id for t in tasks}
    n = 0
    for title, verify in specs:
        if len(spawned) >= cap or not title:
            break
        n += 1
        while f"{parent.id}-f{n}" in existing:
            n += 1
        nid = f"{parent.id}-f{n}"
        existing.add(nid)
        child = Task(id=nid, title=title, status=("ready" if verify else "inbox"),
                     source="followup", verify=verify, extra=[("parent", parent.id)])
        persist_task(cfg, child)
        if tasks is not None:
            tasks.append(child)
        spawned.append(child)
        append_decision(cfg, nid, "auto", context=f"{parent.id}（{parent.title}）から派生生成",
                        action="spawn-followup", reason=title[:120],
                        affects=f"{nid} → {child.status}")
    return spawned


# ---------------------------------------------------------------------------
# policy.md（人間による順位付け・実行先の上書き）
# ---------------------------------------------------------------------------
