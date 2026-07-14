from __future__ import annotations
# policy.py — 元 agent-project.py の 616-912 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
@dataclass
class Policy:
    deny: "list[str]" = field(default_factory=list)
    pin: "list[str]" = field(default_factory=list)
    defer: "list[str]" = field(default_factory=list)
    offload: "list[str]" = field(default_factory=list)
    gate: "list[str]" = field(default_factory=list)   # verify PASS でも人の承認を要する（検収ゲート）
    protect: "list[str]" = field(default_factory=list)  # この**パス**を act が触ったら done にせず人の承認へ
    route: "list[str]" = field(default_factory=list)  # `<パターン> -> <repo名>`: タスク→書込先ワークスペースの割当ルール
    spec: "list[str]" = field(default_factory=list)   # 採点に依らず spec 前段を強制するタスクパターン（spec_track 時）


def parse_policy(text: str) -> Policy:
    pol = Policy()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = POLICY_RE.match(line)
        if m:
            getattr(pol, m.group("key")).append(m.group("val").strip())
    return pol


def load_policy(path: Path) -> Policy:
    return parse_policy(path.read_text(encoding="utf-8")) if path.exists() else Policy()


_REVIEW_VALUES = {"human", "manual", "required", "yes", "true", "1"}


def needs_human_review(task: "Task", policy: "Policy") -> bool:
    """verify PASS でも人の承認(検収)を要するか。タスクの `- review: human` か policy の
    `gate: <パターン>` 一致で gate（高リスク・不可逆・質的受け入れ等を人へ）。既定はゲート無し。"""
    if task.get("review", "").strip().lower() in _REVIEW_VALUES:
        return True
    return any(task.matches(p) for p in policy.gate)


# ---------------------------------------------------------------------------
# タスク単位の自律レベル — 実効 level = 明示 `- level:` > 自動昇格(track) > グローバル `--level`。
#   安全網（protect/gate/regression/review:human）は level に依らず締める方向で常時上乗せ。
LEVELS = ("report", "assisted", "unattended")     # 自律度の梯子（左ほど人の関与が大）


def _level_rank(level: str) -> int:
    try:
        return LEVELS.index((level or "").strip().lower())
    except ValueError:
        return LEVELS.index("unattended")          # 未知値は最も自律的（＝既定）に倒す


def _autonomy_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "autonomy"


def _autonomy_path(cfg: "Config", track: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", track)[:80] or "_"
    return _autonomy_dir(cfg) / f"{safe}.json"


def _autonomy_get(cfg: "Config", track: str, cache: "dict | None" = None) -> "dict | None":
    """track の自動昇格レコードを返す（無ければ None）。cache があれば読みを1回に抑える。"""
    if cache is not None and track in cache:
        return cache[track]
    p = _autonomy_path(cfg, track)
    rec = None
    if p.exists():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
    if cache is not None:
        cache[track] = rec
    return rec


def resolve_level(task: "Task", cfg: "Config", cache: "dict | None" = None) -> str:
    """タスクの実効自律レベル。明示 `- level:` を最優先（ピン）、次に track の自動昇格、無ければグローバル。"""
    explicit = task.get("level", "").strip().lower()
    if explicit in LEVELS:
        return explicit
    if cfg.auto_level:
        track = task.get("track", "").strip()
        if track:
            rec = _autonomy_get(cfg, track, cache)
            lvl = (rec or {}).get("level")
            if lvl in LEVELS:
                return lvl
    return cfg.level


def autonomy_record(cfg: "Config", task: "Task", clean: bool,
                    cache: "dict | None" = None) -> "tuple | None":
    """track の実績を1件記録し、必要なら level を昇格/降格する。`--auto-level` かつ `- track:` 付きのみ。
    clean=完了が手戻りなし（auto-done / approve）、False=手戻り（差し戻し/回帰/偽done/revert）。
    昇格: 連続 clean ≥ promote_after かつ rework_rate ≤ rework_max で ceiling まで1段上げ。
    降格: 手戻り1件で assisted を下限に1段下げ、累計2回で assisted にピンし自動管理を停止。"""
    if not cfg.auto_level:
        return None
    track = task.get("track", "").strip()
    if not track:
        return None
    rec = _autonomy_get(cfg, track, cache) or {
        "track": track, "level": cfg.level, "clean_streak": 0,
        "recent": [], "demotions": 0, "pinned": False}
    n = max(1, cfg.level_window)
    recent = (list(rec.get("recent", [])) + [bool(clean)])[-n:]
    rec["recent"] = recent
    cur = rec.get("level", cfg.level)
    transition = None
    if clean:
        rec["clean_streak"] = int(rec.get("clean_streak", 0)) + 1
        rework_rate = 1.0 - (sum(1 for x in recent if x) / len(recent))
        if (not rec.get("pinned") and rec["clean_streak"] >= cfg.level_promote_after
                and rework_rate <= cfg.level_rework_max
                and _level_rank(cur) < _level_rank(cfg.auto_level_max)
                and _level_rank(cur) < _level_rank("unattended")):
            new = LEVELS[_level_rank(cur) + 1]
            rec["level"], rec["clean_streak"] = new, 0
            transition = ("promote", cur, new, rework_rate)
    else:
        rec["clean_streak"] = 0
        if not rec.get("pinned"):
            rec["demotions"] = int(rec.get("demotions", 0)) + 1
            if rec["demotions"] >= 2:                      # 2回目の手戻り → assisted にピンし人へ
                rec["level"], rec["pinned"] = "assisted", True
                transition = ("pin", cur, "assisted", None)
            else:
                lowered = LEVELS[max(_level_rank("assisted"), _level_rank(cur) - 1)]
                if lowered != cur:
                    rec["level"] = lowered
                    transition = ("demote", cur, lowered, None)
    rec["updated"] = datetime.now().isoformat(timespec="seconds")
    _autonomy_dir(cfg).mkdir(parents=True, exist_ok=True)
    _autonomy_path(cfg, track).write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    if cache is not None:
        cache[track] = rec
    if transition:
        kind, old, new, rate = transition
        label = {"promote": "昇格", "demote": "降格", "pin": "人へピン(自動停止)"}[kind]
        append_decision(cfg, f"track:{track}", "auto",
                        context=f"track `{track}` の自律度を{label}",
                        action=f"autolevel-{kind}",
                        reason=f"{old}→{new}"
                               + (f" rework_rate={rate:.2f}" if rate is not None else "")
                               + f" recent={['o' if x else 'x' for x in recent]}",
                        affects=f"track:{track} → {new}")
    return transition


# ---------------------------------------------------------------------------
# パス保護ゲート（safety denylist）— act が触ったファイルが policy の `protect:` に
#   一致したら、verify=PASS でも done にせず人の承認(review)へ。無人運用の blast radius を縮める。
#   .env / secrets / auth / payments / migrations / infra など「自動で触らせない」場所を守る。
# ---------------------------------------------------------------------------
def _glob_to_regex(pat: str) -> str:
    """glob → 正規表現。`*`=スラッシュ以外の任意 / `**`=スラッシュ含む任意（`**/` は 0 階層も許容）。"""
    i, out = 0, []
    while i < len(pat):
        if pat[i] == "*":
            if pat[i:i + 2] == "**":
                out.append(".*")
                i += 2
                if i < len(pat) and pat[i] == "/":   # `**/` は途中ディレクトリ 0 個も一致させる
                    out.append("/?")
                    i += 1
                continue
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return "(?s:" + "".join(out) + r")\Z"


def path_protected(path: str, patterns: "list[str]") -> "str | None":
    """path が protect パターン群のどれかに一致すれば、その（最初の）パターンを返す。無ければ None。"""
    p = path.replace("\\", "/").lstrip("/")
    if p.startswith("./"):
        p = p[2:]
    for pat in patterns:
        pat = (pat or "").strip().replace("\\", "/")
        if pat and re.match(_glob_to_regex(pat), p):
            return pat
    return None


def _git_out(workdir: "Path", *args: str, timeout: float = 30) -> str:
    try:
        r = subprocess.run(["git", "-C", str(workdir), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _git_dirty_paths(workdir: "Path") -> "set[str]":
    """作業ツリーの未コミット変更パス（git status --porcelain）。"""
    out: set[str] = set()
    for line in _git_out(workdir, "status", "--porcelain", "-uall").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:                       # rename: old -> new は new 側を見る
            path = path.split(" -> ", 1)[1].strip().strip('"')
        if path:
            out.add(path)
    return out


def git_change_baseline(workdir: "Path") -> "tuple[str, frozenset]":
    """act 前のスナップショット（HEAD と、その時点で既に dirty なパス集合）。"""
    return (_git_out(workdir, "rev-parse", "HEAD").strip(),
            frozenset(_git_dirty_paths(workdir)))


def changed_paths_since(workdir: "Path", baseline: "tuple[str, frozenset] | None") -> "set[str]":
    """baseline 以降に act が変更したパス集合（新規 dirty ＋ baseline 以降のコミット差分）。
    git でないと空（best-effort）。remote/daemon 実行は workdir に出ないので保護対象外。"""
    if baseline is None:
        return set()
    head0, dirty0 = baseline
    changed = _git_dirty_paths(workdir) - set(dirty0)     # act で新たに dirty 化した分
    head1 = _git_out(workdir, "rev-parse", "HEAD").strip()
    if head0 and head1 and head0 != head1:                # act がコミットした分
        for line in _git_out(workdir, "diff", "--name-only", f"{head0}..{head1}").splitlines():
            if line.strip():
                changed.add(line.strip())
    return changed


def _kiro_managed_rels(cfg: "Config") -> "set[str]":
    """agent-project 自身の状態ファイル/ディレクトリの、workdir からの相対パス集合。
    backlog/needs/decisions/archive/claims/inbox/bus・journal/DELIVERY/run-log/policy は
    『成果物』ではなく運用状態なので、進捗判定（no-progress）や成果参照から除外する。"""
    wd = cfg.workdir.resolve()
    cand = [cfg.backlog, cfg.needs, cfg.decisions, cfg.archive_dir(), cfg.journal,
            Path(cfg.delivery) if cfg.delivery else None, cfg.runlog, cfg.policy,
            cfg.bus, cfg.inbox, _claims_dir(cfg), commands_dir(cfg)]
    rels: set[str] = set()
    for p in cand:
        if not p:
            continue
        try:
            rels.add(str(p.resolve().relative_to(wd)))
        except (ValueError, OSError):
            continue                                       # workdir 外 → git status に出ない
    return rels


def meaningful_changes(cfg: "Config", baseline: "tuple[str, frozenset] | None") -> "set[str]":
    """act が生んだ『成果物としての』変更（agent-project 自身の状態ファイルを除いた差分）。"""
    changed = changed_paths_since(cfg.workdir, baseline)
    managed = _kiro_managed_rels(cfg)
    return {c for c in changed
            if not any(c == r or c.startswith(r + "/") for r in managed)}


def append_policy(path: Path, key: str, value: str) -> None:
    """policy に `key: value` を足す（既に同じ指示があれば何もしない）。

    冪等にしないと、同じタスクを hold / pin するたびに同じ行が積み上がる（実際 deny が 3 重に
    積まれていた）。policy は「人の上書き指示」の集合であって履歴ではない。"""
    line = f"{key}: {value}"
    if path.exists() and any(x.strip() == line for x in
                             path.read_text(encoding="utf-8").splitlines()):
        return
    header = "" if path.exists() else "# agent-project policy（人間による上書き）\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}{line}\n")


def remove_policy(path: Path, key: str, value: str) -> int:
    """policy から `key: value` の行を消す（消した行数）。

    これが無いと hold（deny 追加）が一方通行になる: approve でタスクを ready に戻しても policy の
    deny が残り続け、次の triage が policy:deny を見て即 blocked へ引き戻す。承認したはずのタスクが
    二度と実行されない（＝人の承認が構造的に効かない）。"""
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = f"{key}: {value}"
    kept = [x for x in lines if x.strip() != target]
    n = len(lines) - len(kept)
    if n:
        path.write_text("".join(kept), encoding="utf-8")
    return n


# ---------------------------------------------------------------------------
# 決定記録（案件毎 decisions/<id>.md）
# ---------------------------------------------------------------------------
