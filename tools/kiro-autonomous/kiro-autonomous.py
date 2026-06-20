#!/usr/bin/env python3
"""kiro-autonomous — Loop Engineering MVP（バックログを捌く制御層）

正準ループ（設計書 docs/designs/2026-06-16-kiro-autonomous-mvp-design.md §2）:
  ① backlog/（案件毎ファイル）を読み優先順位をつけ、最優先タスクを kiro-flow に投げる
  ② 優先順位付けは原則 kiro-cli。stub 時は最古優先（FIFO）。人間は policy.md で上書きできる
  ③ kiro-flow の結果を verify ゲートで検証。done はファイル削除、NG は積み直す
  ④ backlog が尽きるか予算（サイクル数/実時間）が尽きるまで反復。--watch なら尽きても
     プロセスは生存して監視（エージェントは待機しない＝idle 中は kiro-cli/flow を起動しない）
  ⑤ ユーザーの判断は案件毎の decisions/<id>.md に保存。needs/<id>.md のフィードバック欄に
     書き込むと拾って再開する

二層構成: kiro-flow が実行（act）、kiro-autonomous が優先順位付け・検証・収束・決定記録を担う。
標準ライブラリのみ。kiro-cli が無くても --planner none / --flow-planner stub / --executor stub で動く。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # 非 POSIX では daemon 検知不可（常に run にフォールバック）
    fcntl = None

VALID_STATUS = ("inbox", "draft", "ready", "doing", "done", "blocked", "review")
CONSUMABLE = ("ready", "todo")  # 実行待ち。todo は ready の後方互換エイリアス。draft は消化対象外
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")
POLICY_RE = re.compile(r"^(?P<key>deny|pin|defer|offload|gate):\s*(?P<val>.+)$")
DR_HEADER_RE = re.compile(r"^##\s+DR-(\d+)\b")
LEARN_RE = re.compile(r"^- learn:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")
LTM_CATEGORY = "kiro-autonomous"  # ltm-use home 内のカテゴリ（昇格先サブディレクトリ）
FEEDBACK_MARKER = "## フィードバック"
CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]")        # 確定チェックボックス行（任意状態）
CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]")          # チェック済み（= 確定）

# 停止理由
REASON_DRAINED = "drained"  # 消化可能タスクが尽きた（実質完了）
REASON_BUDGET = "budget"    # 予算（サイクル数/実時間）が尽きた
REASON_COST = "cost"        # 予算（トークン/金額）が尽きた


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
        return next((v for k, v in self.extra if k == "feedback"), None)


def _strip_code(val: str) -> str:
    v = val.strip()
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        return v[1:-1]
    return v


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
    cfg.backlog.mkdir(parents=True, exist_ok=True)
    (cfg.backlog / f"{task.id}.md").write_text(serialize_task(task), encoding="utf-8")


def delete_task_file(cfg: "Config", task: Task) -> None:
    p = cfg.backlog / f"{task.id}.md"
    if p.exists():
        p.unlink()


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
@dataclass
class Policy:
    deny: "list[str]" = field(default_factory=list)
    pin: "list[str]" = field(default_factory=list)
    defer: "list[str]" = field(default_factory=list)
    offload: "list[str]" = field(default_factory=list)
    gate: "list[str]" = field(default_factory=list)   # verify PASS でも人の承認を要する（検収ゲート）


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
    if dict(task.extra).get("review", "").strip().lower() in _REVIEW_VALUES:
        return True
    return any(task.matches(p) for p in policy.gate)


def append_policy(path: Path, key: str, value: str) -> None:
    header = "" if path.exists() else "# kiro-autonomous policy（人間による上書き）\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}{key}: {value}\n")


# ---------------------------------------------------------------------------
# 決定記録（案件毎 decisions/<id>.md）
# ---------------------------------------------------------------------------
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
                    learn: "tuple[str, str] | None" = None) -> str:
    """決定記録を追記。learn=(title, guidance) を渡すと『- learn:』行を残し、
    将来 find_learned_resolution が類似タスクへ自動適用できる学習材料にする。"""
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
                      label, skip_id: "str | None" = None) -> "tuple[str, str] | None":
    """与えた md 群の『- learn:』を Jaccard でタイトル照合し最良を返す（決定的・LLM 不要）。"""
    best, best_score = None, 0.0
    for f in sorted(files):
        if skip_id is not None and f.stem == skip_id:  # 自分の履歴は除く（自己ループ防止）
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m = LEARN_RE.match(line)
            if not m:
                continue
            score = _title_overlap(task.title, m.group("title"))
            if score >= threshold and score > best_score:
                best, best_score = (label(f), m.group("guide").strip()), score
    return best


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


# ---------------------------------------------------------------------------
# ltm-use への学習昇格（決定的・エージェント不要。home の Markdown を直接読み書き）
# ---------------------------------------------------------------------------
def resolve_ltm_home(arg: "str | None") -> Path:
    """ltm-use ストアのルート: 明示指定 → 環境変数 KIRO_LTM_HOME → ~/.claude。"""
    raw = arg or os.environ.get("KIRO_LTM_HOME") or "~/.claude"
    return Path(raw).expanduser()


def ltm_memories_dir(cfg: "Config") -> "Path | None":
    """昇格先 `<home>/memory/home/memories/kiro-autonomous`。ltm 無効なら None。"""
    if not cfg.ltm or cfg.ltm_home is None:
        return None
    return cfg.ltm_home / "memory" / "home" / "memories" / LTM_CATEGORY


# ---------------------------------------------------------------------------
# 稼働インスタンスのレジストリ（外部から「いま見ているフォルダ」を発見可能にする）
#
# run（特に --watch 常駐）中、監視中のルートと OS/WSL 情報を共通 home に記録する。
# 外部の操作者（kiro-autonomous スキル等）が `instances` で発見し、同じフォルダへ
# 読み書きできる。プロセスは WSL で動き操作側は Windows/WSL という構成を想定し、
# 可能なら Windows パス（wslpath -w）も併記する。
# ---------------------------------------------------------------------------
def resolve_state_home() -> Path:
    """インスタンス・レジストリ等の置き場: 環境変数 KIRO_AUTONOMOUS_HOME → ~/.kiro-autonomous。"""
    raw = os.environ.get("KIRO_AUTONOMOUS_HOME") or "~/.kiro-autonomous"
    return Path(raw).expanduser()


def instances_dir() -> Path:
    return resolve_state_home() / "instances"


def detect_runtime() -> dict:
    """実行環境（linux / wsl / windows / darwin）と WSL ディストロ名を判定する。"""
    info: dict = {"runtime": "linux", "wsl_distro": None}
    distro = os.environ.get("WSL_DISTRO_NAME")
    is_wsl = False
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            is_wsl = "microsoft" in f.read().lower()
    except OSError:
        pass
    if distro or is_wsl:
        info["runtime"], info["wsl_distro"] = "wsl", distro
    elif sys.platform.startswith("win"):
        info["runtime"] = "windows"
    elif sys.platform == "darwin":
        info["runtime"] = "darwin"
    return info


def to_windows_path(p: "str | Path") -> "str | None":
    """WSL パス → Windows パス（`wslpath -w`）。wslpath が無ければ None。"""
    if not shutil.which("wslpath"):
        return None
    try:
        out = subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def instance_record(cfg: "Config") -> dict:
    """このプロセスの監視対象（ルートと主要パス・OS/WSL 情報）を表す発見用レコード。"""
    root = cfg.backlog.parent.resolve()
    rt = detect_runtime()
    rec = {
        "pid": os.getpid(),
        "root": str(root),
        "backlog": str(cfg.backlog.resolve()),
        "needs": str(cfg.needs.resolve()),
        "decisions": str(cfg.decisions.resolve()),
        "archive": str(cfg.archive_dir().resolve()),
        "policy": str(cfg.policy.resolve()),
        "delivery": str(Path(cfg.delivery).resolve()),
        "journal": str(cfg.journal.resolve()),
        "workdir": str(cfg.workdir.resolve()),
        "watch": cfg.watch,
        "started_at": time.time(),
        "started_iso": datetime.now().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "python": sys.executable,
        **rt,
    }
    if rt["runtime"] == "wsl":
        rec["root_windows"] = to_windows_path(root)  # \\wsl.localhost\<distro>\... 等。無ければ None
    return rec


def register_instance(cfg: "Config") -> "Path | None":
    """レジストリに自分を登録し、書いたファイルパスを返す（失敗しても run は止めない）。"""
    try:
        d = instances_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{os.getpid()}.json"
        p.write_text(json.dumps(instance_record(cfg), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True       # 別ユーザーの生存プロセス
    except OSError:
        return False
    return True


def list_instances(prune: bool = True) -> list:
    """生存中のインスタンス一覧。死んだ PID のレコードは prune で掃除する。"""
    d = instances_dir()
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _pid_alive(int(rec.get("pid", -1))):
            out.append(rec)
        elif prune:
            try:
                f.unlink()
            except OSError:
                pass
    return out


def cmd_instances(as_json: bool = False) -> int:
    """稼働中の kiro-autonomous（監視中フォルダ）を一覧。外部操作者の発見口。"""
    recs = list_instances(prune=True)
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        print("稼働中の kiro-autonomous はありません（run/--watch 起動時に登録されます）。")
        return 0
    for r in recs:
        rt = r.get("runtime", "?")
        if r.get("wsl_distro"):
            rt += f":{r['wsl_distro']}"
        flags = "watch" if r.get("watch") else "run"
        print(f"pid={r['pid']} [{rt}] {flags}  root={r['root']}")
        if r.get("root_windows"):
            print(f"    Windows: {r['root_windows']}")
    return 0



def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "memory"


def count_learn_hits(cfg: "Config") -> "dict[str, int]":
    """各 learn ルール（出典 DR id）が auto-resolve で実際に効いた回数を数える（昇格の根拠）。"""
    hits: dict[str, int] = {}
    if not cfg.decisions.exists():
        return hits
    pat = re.compile(r"learned from (?:ltm:)?(?P<src>\S+?):")
    for df in cfg.decisions.glob("*.md"):
        for line in df.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- reason"):
                m = pat.search(line)
                if m:
                    src = m.group("src")
                    hits[src] = hits.get(src, 0) + 1
    return hits


def collect_learnings(cfg: "Config") -> "list[tuple[str, str, str]]":
    """decisions/ の全 learn ルールを (出典id, title, guide) で列挙。"""
    out: list[tuple[str, str, str]] = []
    if not cfg.decisions.exists():
        return out
    for df in sorted(cfg.decisions.glob("*.md")):
        for line in df.read_text(encoding="utf-8").splitlines():
            m = LEARN_RE.match(line)
            if m:
                out.append((df.stem, m.group("title").strip(), m.group("guide").strip()))
    return out


def _promote_marker(cfg: "Config", src: str) -> bool:
    p = decision_path(cfg, src)
    return p.exists() and "- promoted:" in p.read_text(encoding="utf-8")


def write_ltm_memory(mem_dir: Path, title: str, guide: str, src: str, hits: int) -> str:
    """ltm-use 記憶フォーマット（frontmatter＋本文）で1件書き出し、記憶IDを返す。

    本文に機械可読な `- learn: <title> :: <guide>` を残し、recall 時に同じ LEARN_RE で読み戻す。"""
    mem_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(mem_dir.glob("*.md"))) + 1
    date = datetime.now().strftime("%Y-%m-%d")
    memid = f"mem-{datetime.now().strftime('%Y%m%d')}-{n:03d}"
    name = f"{_slug(title)}-{n:03d}"
    summary = guide.replace("\n", " ")[:120]
    body = (
        f"---\n"
        f"id: {memid}\n"
        f"title: \"{title}\"\n"
        f"created: \"{date}\"\n"
        f"updated: \"{date}\"\n"
        f"status: active\n"
        f"scope: home\n"
        f"tags: [{LTM_CATEGORY}, learn]\n"
        f"memory_type: procedural\n"
        f"promoted_from: \"decisions/{src}.md\"\n"
        f"access_count: {hits}\n"
        f"summary: \"{summary}\"\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"## コンテキスト\n"
        f"kiro-autonomous の判断ノウハウ。出典 decisions/{src}.md で {hits} 回再利用され昇格。\n\n"
        f"## 学び・結論\n"
        f"- learn: {title} :: {guide}\n"
    )
    (mem_dir / f"{name}.md").write_text(body, encoding="utf-8")
    return memid


def promote_learnings(cfg: "Config") -> "list[tuple[str, str]]":
    """効果が再現した learn ルール（hits ≥ promote_threshold・未昇格）を ltm-use home へ昇格。

    返り値 [(出典id, 記憶id)]。ltm 無効や home 未解決なら何もしない（グレースフル no-op）。"""
    mem_dir = ltm_memories_dir(cfg)
    if mem_dir is None:
        return []
    hits = count_learn_hits(cfg)
    seen: set[str] = set()
    promoted: list[tuple[str, str]] = []
    for src, title, guide in collect_learnings(cfg):
        if src in seen or hits.get(src, 0) < cfg.promote_threshold or _promote_marker(cfg, src):
            continue
        seen.add(src)
        memid = write_ltm_memory(mem_dir, title, guide, src, hits[src])
        with decision_path(cfg, src).open("a", encoding="utf-8") as f:
            f.write(f"- promoted: {memid}（ltm-use home へ昇格 / hits={hits[src]}）\n")
        append_journal(cfg.journal, f"学習昇格: {src} → ltm-use {memid}（hits={hits[src]}）")
        promoted.append((src, memid))
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
        if not t.verify.strip():
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
# 通知（案件毎 needs/<id>.md）＋ フィードバック往復
# ---------------------------------------------------------------------------
def needs_path(cfg: "Config", tid: str) -> Path:
    return cfg.needs / f"{tid}.md"


def write_needs_file(cfg: "Config", task: Task, reason: str, review: bool = False) -> None:
    cfg.needs.mkdir(parents=True, exist_ok=True)
    if review:    # verify=PASS の承認ゲート（検収待ち）
        state = "review（検収待ち・verify=PASS）"
        hint = (f"<!-- 承認して done 確定するなら `kiro-autonomous approve {task.id}`。\n"
                f"     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->\n")
    else:
        state = "blocked（kiro-autonomous の判断待ち）"
        hint = (f"<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。\n"
                f"     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。\n"
                f"     コマンドなら `kiro-autonomous approve {task.id}`。 -->\n")
    body = (
        f"# 要対応: {task.id} — {task.title}\n\n"
        f"- なぜ: {reason}\n"
        f"- 状態: {state}\n\n"
        f"{FEEDBACK_MARKER}\n"
        f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
        f"{hint}"
    )
    needs_path(cfg, task.id).write_text(body, encoding="utf-8")


def clear_needs_file(cfg: "Config", tid: str) -> None:
    p = needs_path(cfg, tid)
    if p.exists():
        p.unlink()


def read_feedback(path: Path) -> str:
    """『## フィードバック』以降の人の記入（HTMLコメント・チェックボックス行は除く）を取り出す。"""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    i = text.find(FEEDBACK_MARKER)
    if i < 0:
        return ""
    body = text[i + len(FEEDBACK_MARKER):]
    lines = [ln for ln in body.splitlines() if not CHECKBOX_RE.match(ln)]
    return "\n".join(lines).strip()


def feedback_submitted(path: Path) -> bool:
    """確定チェックボックスが [x] かどうか（= 人が編集を終えた明示シグナル）。"""
    return any(CHECKED_RE.match(ln) for ln in path.read_text(encoding="utf-8").splitlines())


def ingest_feedback(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """needs/<id>.md の確定（[x]）を検知したら、対象をブロック解除し内容を次の act に渡す。

    明示シグナル（チェックボックス [x]）必須。書きかけでの誤発火を防ぐため、watch 中は
    最終保存から cfg.debounce 秒が経過するまで待つ（静穏化）。"""
    ingested: list[str] = []
    if not cfg.needs.exists():
        return ingested
    by_id = {t.id: t for t in tasks}
    for nf in sorted(cfg.needs.glob("*.md")):
        if not feedback_submitted(nf):                 # [x] が無ければ確定していない
            continue
        if cfg.watch and cfg.debounce > 0 and (time.time() - nf.stat().st_mtime) < cfg.debounce:
            continue                                    # 直近に編集 → 静穏化を待つ
        t = by_id.get(nf.stem)
        if t is None:
            continue
        fb = read_feedback(nf)
        t.status = "ready"
        t.extra = [(k, v) for k, v in t.extra if k != "feedback"]
        if fb:
            t.extra.append(("feedback", fb.replace("\n", " ⏎ ")))
        persist_task(cfg, t)
        append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）に人のフィードバック",
                        action="feedback-resume", reason=fb[:200] if fb else "チェックで承認",
                        affects=f"{t.id} → ready", learn=(t.title, fb) if fb else None)
        nf.unlink()
        append_journal(cfg.journal, f"feedback 取り込み: {t.id} を再開")
        ingested.append(t.id)
    return ingested


def human_worklist(tasks: "list[Task]") -> "tuple[list[Task], list[Task], list[Task]]":
    blocked = [t for t in tasks if t.norm_status() == "blocked"]
    intake = [t for t in tasks if t.norm_status() == "inbox" and not t.verify.strip()]
    review = [t for t in tasks if t.norm_status() == "review"]   # verify=PASS の承認待ち
    return blocked, intake, review


def render_digest(blocked, intake, reasons: dict, budget_stop: bool, review=None) -> str:
    review = review or []
    lines = ["# 要対応（kiro-autonomous）", ""]
    if budget_stop:
        lines += ["⚠ 予算切れで未消化のまま停止しました。", ""]
    if review:
        lines.append("## 検収待ち（verify=PASS・承認で done 確定）")
        for t in review:
            lines.append(f"- {t.id}: {t.title}")
            lines.append(f"    成果: {dict(t.extra).get('gate_ref', '')}")
            lines.append(f"    対応: `kiro-autonomous approve {t.id}`（承認）／needs に方針を書いて差し戻し")
        lines.append("")
    if blocked:
        lines.append("## 判断待ち（blocked）")
        for t in blocked:
            why = reasons.get(t.id, "検証 NG / 判断不能")
            lines.append(f"- {t.id}: {t.title}\n    なぜ: {why}\n"
                         f"    対応: needs/{t.id}.md に方針を書く、または `approve {t.id}` / `hold {t.id}`")
    if intake:
        lines += ["", "## acceptance 未定義（need_intake）"]
        for t in intake:
            lines.append(f"- {t.id}: {t.title}\n    なぜ: verify 未定義 → verify を定義して ready 化")
    if not blocked and not intake and not review:
        lines.append("（対応待ちなし）")
    return "\n".join(lines) + "\n"


def notify(cfg: "Config", tasks, reasons: dict, newly_blocked: set, budget_stop: bool) -> bool:
    """状態遷移時だけ stdout / notify-cmd へ要約を出す（案件毎の needs/<id>.md は別途書込済）。"""
    if not newly_blocked and not budget_stop:
        return False
    blocked, intake, review = human_worklist(tasks)
    digest = render_digest(blocked, intake, reasons, budget_stop, review)
    print("\n--- 通知（要対応）---\n" + digest, flush=True)
    if cfg.notify_cmd:
        try:
            subprocess.run(cfg.notify_cmd, shell=True, input=digest, text=True,
                           cwd=str(cfg.workdir), timeout=60)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] notify-cmd 失敗: {e}", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# 優先順位付け（正準ループ ①②）
# ---------------------------------------------------------------------------
def consumable_tasks(tasks: "list[Task]") -> "list[Task]":
    return [t for t in tasks if t.consumable()]


def task_deps(task: "Task") -> "list[str]":
    """`- after: T1, T2` の依存 ID 群（カンマ/空白区切り）。無ければ空。"""
    raw = dict(task.extra).get("after", "")
    return [d for d in re.split(r"[,\s]+", raw.strip()) if d]


def unmet_deps(task: "Task", tasks: "list[Task]") -> "list[str]":
    """`after` の依存のうち、まだ未完（backlog に done 以外で残っている）ID。done は退避済みなので満たし。"""
    pending = {t.id for t in tasks if t.norm_status() != "done"}
    return [d for d in task_deps(task) if d in pending]


def ready_after_deps(tasks: "list[Task]") -> "list[Task]":
    """消化対象（ready）のうち、依存が満たされたものだけ（DAG 順序）。"""
    return [t for t in consumable_tasks(tasks) if not unmet_deps(t, tasks)]


def _extract_id_array(text: str) -> "list[str] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return [str(x) for x in arr] if isinstance(arr, list) else None


def _extract_json_obj(text: str) -> "dict | None":
    """応答から最初の JSON オブジェクト {...} を取り出す（説明文が混じっても拾う）。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def _run_kiro_cli(prompt: str, model: "str | None") -> str:
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"kiro-cli rc={proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def rank_agent(ready: "list[Task]", model: "str | None", kiro_run=_run_kiro_cli) -> "list[Task] | None":
    if not ready:
        return []
    listing = "\n".join(
        f"- {t.id}: {t.title}（priority={t.priority}, source={t.source}）" for t in ready)
    prompt = ("あなたはバックログの優先順位付け役。次のタスク群を、重要度・緊急度・依存関係に加え、"
              "**外部で付与された priority（大きいほど高優先）も加味**して優先順位の高い順に並べ替え、"
              "**タスクID の JSON 配列だけ**を出力してください（説明文なし）。\n\nタスク:\n" + listing)
    try:
        order_ids = _extract_id_array(kiro_run(prompt, model))
    except Exception:  # noqa: BLE001
        return None
    if not order_ids:
        return None
    by_id = {t.id: t for t in ready}
    ordered = [by_id[i] for i in order_ids if i in by_id]
    seen = {t.id for t in ordered}
    ordered += [t for t in ready if t.id not in seen]
    return ordered


def _tail_matching(path: "Path | None", needle: str, limit: int) -> "list[str]":
    """ファイルから needle を含む行を末尾 limit 件返す（best-effort・無ければ空）。"""
    if not path or not path.exists():
        return []
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if needle in ln]
    except OSError:
        return []
    return lines[-limit:]


def adjudication_context(cfg: "Config", task: Task,
                         journal_lines: int = 8, decision_chars: int = 1200) -> str:
    """裁定の判断材料を decisions/journal/task から決定的に集める（LLM 不要・有界）。
    『過去にどう試して何を人が判断したか』を門番へ渡し、的外れな requeue や再エスカレを減らす。"""
    parts: list[str] = []
    jl = _tail_matching(cfg.journal, task.id, journal_lines)
    if jl:
        parts.append("これまでのサイクル履歴(journal):\n" + "\n".join(jl))
    dp = decision_path(cfg, task.id)
    if dp.exists():
        try:
            txt = dp.read_text(encoding="utf-8").strip()
        except OSError:
            txt = ""
        if txt:
            if len(txt) > decision_chars:        # 直近の判断が重要なので末尾を残す
                txt = "…\n" + txt[-decision_chars:]
            parts.append("過去の決定記録(decisions):\n" + txt)
    fb = task.feedback()
    if fb:
        parts.append("適用済みの直近フィードバック: " + fb)
    note = next((v for k, v in task.extra if k == "note"), None)
    if note:
        parts.append("タスクのメモ(note): " + note)
    return "\n\n".join(parts)


def adjudicate_escalation(cfg: "Config", task: Task, reason: str,
                          kiro_run=None) -> "tuple[str, str]":
    """needs（人の判断）に落とす直前の kiro-cli 裁定ゲート。
    『ループ内で自律的に積み直して解けるか／人の判断が要るか』を判断させる。
    返り値: ("requeue", guidance) なら自律的に積み直す、("escalate", "") なら従来どおり人へ。
    判断不能・エラー・曖昧は **必ず escalate にフォールバック**（安全側＝人を飛ばさない）。"""
    run = kiro_run or _run_kiro_cli
    ctx = adjudication_context(cfg, task)        # journal/decisions/feedback の文脈を渡す
    prompt = (
        "あなたは自律バックログ・ループの『人の判断を呼ぶ前の門番』です。次のタスクが検証(verify)に"
        "失敗し、通常なら人の判断待ち(needs)へ送られます。これを **ループ内で自律的に積み直して解決を試みる"
        "価値があるか** を判断してください。\n"
        "- requeue（積み直す）: 失敗が実装の不足・取り違え等で、明確な追加指示があれば次の試行で解けそうな場合。\n"
        "- escalate（人へ）: 要件が曖昧／意思決定や承認が要る／リスクが高い／同じ失敗の繰り返しで打開策が無い場合。\n"
        "**判断は厳しめに。少しでも人の意思決定が要るなら escalate。過去に同じ案件を積み直して解けていない"
        "なら escalate。**\n\n"
        f"タスクID: {task.id}\nタイトル: {task.title}\nverify: {task.verify}\n"
        f"これまでの試行回数(retries): {task.retries}\n失敗理由: {reason}\n\n"
        + (f"--- 参考文脈（既存の試行・判断の履歴）---\n{ctx}\n\n" if ctx else "")
        + '出力は次の JSON オブジェクトだけ（説明文なし）:\n'
        '{"decision": "requeue" | "escalate", "guidance": "requeue の場合のみ、次の試行への具体的な指示"}')
    try:
        obj = _extract_json_obj(run(prompt, cfg.model))
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等は人へ
        return ("escalate", "")
    if not obj or obj.get("decision") != "requeue":
        return ("escalate", "")
    return ("requeue", str(obj.get("guidance", "")).strip())


def apply_policy_order(ordered: "list[Task]", policy: Policy) -> "list[Task]":
    def hit(t, pats):
        return any(t.matches(p) for p in pats)
    pinned = [t for t in ordered if hit(t, policy.pin)]
    deferred = [t for t in ordered if not hit(t, policy.pin) and hit(t, policy.defer)]
    middle = [t for t in ordered if t not in pinned and t not in deferred]
    return pinned + middle + deferred


def by_priority_then_age(ready: "list[Task]") -> "list[Task]":
    """優先度降順、同値は最古優先（ready は mtime 昇順で渡される＝安定ソートで age が効く）。"""
    return sorted(ready, key=lambda t: -t.priority)


def prioritize(tasks, policy, planner, model=None, ranker=None) -> "list[Task]":
    """planner=none: priority＋古さ。planner=kiro: エージェント（priority も加味）。policy が最終上書き。"""
    ready = ready_after_deps(tasks)  # mtime 昇順（最古優先）。依存(after)未達は除外
    if planner == "none":
        base = by_priority_then_age(ready)
    else:  # kiro（エージェント順位付け。失敗時は priority＋古さにフォールバック）
        rank = (ranker or rank_agent)(ready, model)
        base = rank if rank is not None else by_priority_then_age(ready)
    return apply_policy_order(base, policy)


# ---------------------------------------------------------------------------
# triage（inbox→ready 昇格・policy deny の適用）
# ---------------------------------------------------------------------------
def triage(tasks, policy) -> "list[tuple[Task, str]]":
    transitions = []
    for t in tasks:
        st = t.norm_status()
        if st == "inbox" and t.verify.strip():
            t.status = "ready"
            st = "ready"
        if st in CONSUMABLE and any(t.matches(p) for p in policy.deny):
            t.status = "blocked"
            transitions.append((t, "policy:deny（人の判断待ち）"))
    return transitions


# ---------------------------------------------------------------------------
# verify ゲート / act（kiro-flow 委譲）
# ---------------------------------------------------------------------------
def run_verify(cmd: str, workdir: Path, timeout: float) -> "tuple[bool, str]":
    if not cmd.strip():
        return (False, "verify 未定義（自己申告では done にできない → 人の判断へ）")
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(workdir), timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return (False, f"verify タイムアウト（{timeout}s）")
    tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]
    return (proc.returncode == 0, f"exit={proc.returncode} {tail.strip()}"[:500])


def resolve_kiro_flow(explicit: "str | None") -> "list[str]":
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = shutil.which("kiro-flow")
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent / "kiro-flow" / "kiro-flow.py"
    return [sys.executable, str(local)]


def build_request(task: Task) -> str:
    base = (f"{task.title}\n\n"
            f"このタスクは完了条件を満たすまで反復し、満たしたら終了すること（loop-until-done）。\n"
            f"完了条件: 次のシェルコマンドが終了コード 0 で成功すること:\n"
            f"  {task.verify or '（verify 未定義）'}\n\nタスクID: {task.id}")
    fb = task.feedback()
    if fb:
        base += f"\n\n人からのフィードバック（必ず反映すること）:\n{fb}"
    return base


def decide_pace(cfg: "Config", cycle_elapsed: float) -> float:
    pace = cfg.pace
    if cfg.max_seconds and cfg.max_cycles:
        pace = max(pace, cfg.max_seconds / cfg.max_cycles)
    return max(0.0, pace - cycle_elapsed)


def decide_location(task: Task, policy: Policy, cfg: "Config") -> str:
    """act の実行モードを local / daemon / remote に決める（kiro-flow の起動方法を統合）。

      local  : kiro-flow run（単発・自己完結・daemon 不要）
      daemon : ローカルバスの daemon に submit して結果を待つ（warm worker 再利用）
      remote : 共有 git バス（別マシンの daemon）へ submit＝真のオフロード
    `--location auto`（既定）: offload 一致かつ git-bus → remote / ローカル daemon 稼働 → daemon / それ以外 local。
    明示指定（local/daemon/remote）はそれを優先（remote は git-bus 必須、無ければ local）。"""
    loc = cfg.location
    if loc == "auto":
        if cfg.git_bus and any(task.matches(p) for p in policy.offload):
            return "remote"
        if daemon_running(cfg, use_git=False):
            return "daemon"
        return "local"
    if loc == "remote" and not cfg.git_bus:
        return "local"
    return loc


def _kf_base(cfg: "Config", use_git: bool) -> "list[str]":
    base = resolve_kiro_flow(cfg.kiro_flow) + ["--bus", str(cfg.bus)]
    if use_git and cfg.git_bus:
        base += ["--git", cfg.git_bus, "--git-branch", cfg.git_branch]
        if cfg.git_subdir:
            base += ["--git-subdir", cfg.git_subdir]
    return base


def build_kiro_flow_cmd(task: Task, cfg: "Config", use_git: bool = False) -> "list[str]":
    """kiro-flow run（都度起動）のコマンド。planner/executor を制御できる（submit では不可）。"""
    return _kf_base(cfg, use_git) + [
        "run", build_request(task), "--planner", cfg.flow_planner,
        "--executor", cfg.executor, "--max-iterations", str(cfg.max_iterations)]


def daemon_lock_path(cfg: "Config", use_git: bool) -> Path:
    """kiro-flow daemon の singleton ロックパス（kiro-flow と同一規則）。"""
    if use_git and cfg.git_bus:
        key = f"git::{cfg.git_bus}@{cfg.git_branch}/{cfg.git_subdir or ''}"
    else:
        key = "local::" + os.path.abspath(str(cfg.bus))
    h = hashlib.sha1(key.encode()).hexdigest()
    return Path(tempfile.gettempdir()) / "kiro-flow-locks" / f"daemon-{h}.lock"


def daemon_running(cfg: "Config", use_git: bool = False) -> bool:
    """対象バスの kiro-flow daemon が稼働中か（ロックが保持されているか）を判定する。"""
    if fcntl is None:
        return False
    p = daemon_lock_path(cfg, use_git)
    if not p.exists():
        return False
    try:
        f = open(p, "r+")
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        return False  # 取得できた = 誰も保持していない = daemon 無し
    except BlockingIOError:
        return True   # 保持されている = daemon 稼働中
    finally:
        f.close()


def _act_run(task: Task, cfg: "Config", use_git: bool = False) -> "tuple[bool, str]":
    """kiro-flow run で都度起動（同期実行）。daemon 不要。"""
    cmd = build_kiro_flow_cmd(task, cfg, use_git)
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=cfg.act_timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return (False, f"kiro-flow run タイムアウト（{cfg.act_timeout}s）")
    except FileNotFoundError as e:
        return (False, f"kiro-flow を起動できません: {e}")
    return (proc.returncode == 0, (proc.stdout or "")[-300:].strip())


def _act_submit(task: Task, cfg: "Config", use_git: bool) -> "tuple[bool, str]":
    """daemon があるとき: submit して、その run が終端に達するまで待つ（verify は待機後）。"""
    base = _kf_base(cfg, use_git)
    try:
        sub = subprocess.run(base + ["submit", build_request(task)], cwd=str(cfg.workdir),
                             timeout=60, capture_output=True, text=True)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return (False, f"submit 失敗: {e}")
    if sub.returncode != 0:
        return (False, f"submit rc={sub.returncode}: {sub.stderr.strip()[:200]}")
    out = (sub.stdout or "").strip().splitlines()
    run_id = out[0].strip() if out else ""
    if not run_id:
        return (False, "run-id を取得できません")
    deadline = time.time() + cfg.act_timeout
    while time.time() < deadline:
        try:
            res = subprocess.run(base + ["result", "--run-id", run_id, "--json"],
                                cwd=str(cfg.workdir), timeout=60, capture_output=True, text=True)
            data = json.loads(res.stdout)
            if data.get("done"):
                return (True, f"daemon run {run_id} done")
        except Exception:  # noqa: BLE001 — 取得失敗は次ポーリングで再試行
            pass
        time.sleep(2.0)
    return (False, f"daemon run {run_id} タイムアウト")


def act_via_kiro_flow(task: Task, cfg: "Config", location: str = "local") -> "tuple[bool, str]":
    """location（local/daemon/remote）に応じて kiro-flow へ委譲する。

      local  → run（単発）
      daemon → ローカル daemon に submit＋結果待ち（daemon が無ければ run にフォールバック）
      remote → git バスの remote daemon に submit＋結果待ち（オフロード。フォールバックしない）
    """
    if location == "remote":
        return _act_submit(task, cfg, use_git=True)
    if location == "daemon":
        if daemon_running(cfg, use_git=False):
            return _act_submit(task, cfg, use_git=False)
        return _act_run(task, cfg, use_git=False)  # daemon 不在 → run
    return _act_run(task, cfg, use_git=False)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
@dataclass
class Config:
    backlog: Path      # ディレクトリ（案件毎ファイル）
    policy: Path       # ファイル
    decisions: Path    # ディレクトリ（案件毎）
    journal: Path      # ファイル
    needs: Path        # ディレクトリ（案件毎）
    workdir: Path
    bus: Path
    git_bus: "str | None" = None
    git_branch: str = "main"
    git_subdir: "str | None" = None
    kiro_flow: "str | None" = None
    planner: str = "kiro"          # 優先順位付け戦略: kiro（エージェント）/ none（priority＋古さ）
    flow_planner: str = "flow-planner"  # kiro-flow run に渡す planner
    location: str = "auto"         # act の実行モード: auto / local / daemon / remote
    executor: str = "kiro"
    model: "str | None" = None
    max_iterations: int = 3
    max_cycles: int = 20
    max_seconds: float = 0.0
    max_tokens: int = 0            # 予算: 消費トークン上限（0=無制限）。act 出力の @cost を計上
    max_cost: float = 0.0          # 予算: 金額(USD)上限（0=無制限）
    max_retries: int = 2
    pace: float = 0.0
    verify_timeout: float = 120.0
    act_timeout: float = 1800.0
    notify_cmd: "str | None" = None
    actor: str = "user"
    archive: "Path | None" = None   # done の退避先ディレクトリ（既定 archive/）
    do_archive: bool = True         # done を archive/ へ退避（False なら削除）
    learn: bool = True              # DR 学習: 過去の人の判断から類似案件を自動解決
    learn_threshold: float = 0.5    # タイトル類似度（Jaccard）のしきい値
    auto_adjudicate: bool = True    # needs に落とす前に kiro-cli が積み直し可否を裁定（既定 on）
    adjudicate_max: int = 1         # 1タスクあたりの自律裁定の上限回数（有限停止のため）
    max_spawn: int = 20             # 1 run で生成できる派生タスク数の上限（0 で生成無効。暴走防止）
    regression_cmd: "str | None" = None  # done 確定前に走らせるグローバル回帰検査（巻き込み事故の検知）
    regression_revert: bool = False      # 回帰時に作業ツリーの未コミット変更を巻き戻す（既定 off）
    ltm: bool = False               # ltm-use 長期記憶への昇格＋横断 recall（既定 off: home へ書くため明示）
    ltm_home: "Path | None" = None  # ltm-use ストアのルート（既定 KIRO_LTM_HOME→~/.claude）
    promote_threshold: int = 2      # learn ルールがこの回数以上効いたら昇格
    rot: bool = False               # rot 検知（古い/重複/実行不能を triage で掃除）
    rot_age_days: float = 14.0      # stale とみなす経過日数
    cleanup: bool = True            # run 後に kiro-flow バスの一時状態を掃除
    delivery: "Path | None" = None  # 納品一覧（受領書）DELIVERY.md
    debounce: float = 3.0           # watch 中、最終保存からこの秒数は feedback 取込を待つ
    watch: bool = False     # 終了条件後もプロセスを残し backlog を監視
    poll: float = 5.0       # watch のポーリング間隔（秒）
    dry_run: bool = False
    once: bool = False

    def archive_dir(self) -> Path:
        return self.archive or (self.backlog.parent / "archive")

    def __post_init__(self):
        if self.delivery is None:
            self.delivery = self.backlog.parent / "DELIVERY.md"


def ensure_dirs(cfg: Config) -> None:
    for d in (cfg.backlog, cfg.needs, cfg.decisions):
        d.mkdir(parents=True, exist_ok=True)
    cfg.journal.parent.mkdir(parents=True, exist_ok=True)


def extract_delivery_ref(act_msg: str, cfg: Config) -> str:
    """成果物の参照を得る。act 出力の PR URL / commit SHA を優先、無ければ workdir の git。"""
    m = re.search(r"https?://\S+/(?:pull|merge_requests)/\d+", act_msg or "")
    if m:
        return m.group(0)
    m = re.search(r"\b[0-9a-f]{7,40}\b", act_msg or "")
    if m:
        return f"commit {m.group(0)}"
    try:
        r = subprocess.run(["git", "-C", str(cfg.workdir), "log", "-1", "--format=%h %s"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return f"git: {r.stdout.strip()}"
    except Exception:  # noqa: BLE001
        pass
    return "(参照なし)"


_COST_RE = re.compile(r"@cost\b(?P<rest>.*)")


def parse_cost(act_msg: str) -> "tuple[int, float]":
    """act 出力からコストを計上する。エージェントが `@cost tokens=1234 usd=0.05` 形式の行を吐けば
    それを合算（1タスクで複数回呼ぶこともあるので加算）。マーカが無ければ (0, 0.0)。決定的・LLM 不要。"""
    tokens, usd = 0, 0.0
    for line in (act_msg or "").splitlines():
        m = _COST_RE.search(line)
        if not m:
            continue
        rest = m.group("rest")
        tm = re.search(r"tokens?\s*[=:]\s*([\d_]+)", rest)
        um = re.search(r"(?:usd|cost)\s*[=:]\s*([\d.]+)", rest)
        if tm:
            tokens += int(tm.group(1).replace("_", ""))
        if um:
            usd += float(um.group(1))
    return tokens, usd


def append_delivery(cfg: Config, task: Task, ref: str, ts: str) -> None:
    """納品一覧（受領書）DELIVERY.md に1行追記する。"""
    path = cfg.delivery
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.exists() else (
        "# 納品一覧（受領書）\n\n| id | タイトル | 検収 | 成果参照 | 完了 |\n|---|---|---|---|---|\n")
    title = task.title.replace("|", "\\|")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}| {task.id} | {title} | PASS | {ref} | {ts} |\n")


def archive_task(cfg: Config, task: Task, vmsg: str, ref: str, ts: str) -> None:
    """done タスクを archive/<id>.md へ退避し、検収用の『納品書』を付す（backlog と1:1）。"""
    cfg.archive_dir().mkdir(parents=True, exist_ok=True)
    task.extra.append(("archived", ts))
    body = serialize_task(task) + (
        f"\n## 納品書\n"
        f"- 完了 : {ts}\n"
        f"- verify: `{task.verify}` → PASS（{vmsg}）\n"
        f"- 成果 : {ref}\n"
    )
    (cfg.archive_dir() / f"{task.id}.md").write_text(body, encoding="utf-8")
    delete_task_file(cfg, task)


# ---------------------------------------------------------------------------
# 正準ループ（run）
# ---------------------------------------------------------------------------
def summarize(tasks: "list[Task]") -> "dict[str, int]":
    c = {s: 0 for s in VALID_STATUS}
    for t in tasks:
        c[t.norm_status()] = c.get(t.norm_status(), 0) + 1
    return c


def append_journal(path: Path, line: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {ts} {line}\n")


def _block(cfg, task, reason, reasons):
    task.status = "blocked"
    reasons[task.id] = reason
    persist_task(cfg, task)
    write_needs_file(cfg, task, reason)


def _revert_workdir(cfg) -> None:
    """回帰時の best-effort 巻き戻し: 追跡ファイルを HEAD に戻し未追跡を消す。
    **コミット済み/ push 済みの変更は対象外**（未コミットの作業ツリー変更のみ）。"""
    if not (cfg.workdir / ".git").exists():
        return
    for cmd in (["git", "-C", str(cfg.workdir), "checkout", "--", "."],
                ["git", "-C", str(cfg.workdir), "clean", "-fd"]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def _escalate(cfg, task, reason, reasons, cycle):
    """ループ内で人の判断(needs)へ回す直前のフック。auto_adjudicate が有効なら、人へ送る前に
    kiro-cli へ『自律的に積み直して解けるか』を諮り、可能なら needs を作らず ready に戻して回し続ける。
    verify を持たないタスク（acceptance 未定義）は対象外＝必ず人へ。adjudicate_max で有限回に制限。"""
    if cfg.auto_adjudicate and not cfg.dry_run and task.verify:
        done_n = int(dict(task.extra).get("adjudicated", "0") or "0")
        if done_n < cfg.adjudicate_max:
            decision, guide = adjudicate_escalation(cfg, task, reason)
            if decision == "requeue":
                task.extra = [(k, v) for k, v in task.extra
                              if k not in ("feedback", "adjudicated")]
                if guide:
                    task.extra.append(("feedback", guide.replace("\n", " ⏎ ")))
                task.extra.append(("adjudicated", str(done_n + 1)))
                task.status = "ready"
                persist_task(cfg, task)
                append_decision(cfg, task.id, "auto",
                                context=f"{task.id}（{task.title}）を人の判断前に自律裁定",
                                action="auto-adjudicate",
                                reason=(f"kiro-cli: requeue — {guide[:120]}" if guide
                                        else "kiro-cli: requeue"),
                                affects=f"{task.id} → ready")
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} 自律裁定で積み直し"
                                            f"（人の判断を回避 {done_n + 1}/{cfg.adjudicate_max}）")
                return
    _block(cfg, task, reason, reasons)


def run_loop(cfg: Config, act=act_via_kiro_flow, ranker=None, sleeper=time.sleep) -> dict:
    ensure_dirs(cfg)
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    reasons: dict[str, str] = {}

    ingested = ingest_feedback(cfg, tasks)           # 人のフィードバックでブロック解除
    pre_blocked = {t.id for t in tasks if t.norm_status() in ("blocked", "review")}
    transitions = list(triage(tasks, policy))
    if cfg.rot:                                       # rot 検知（古い/重複/実行不能を掃除）
        transitions += [(t, f"rot: {why}") for t, why in detect_rot(cfg, tasks)]
    for t, why in transitions:
        if t.norm_status() != "blocked":
            t.status = "blocked"
        reasons[t.id] = why
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)

    append_journal(cfg.journal, f"=== kiro-autonomous 開始 tasks={len(tasks)} "
                                f"ingested={len(ingested)} planner={cfg.planner} "
                                f"executor={cfg.executor} dry_run={cfg.dry_run} ===")
    start = time.time()
    cycle = 0
    archived = 0
    spawned_total = 0
    tokens_used = 0
    cost_used = 0.0
    reason = REASON_DRAINED

    while True:
        if cycle >= cfg.max_cycles:
            reason = REASON_BUDGET
            break
        if cfg.max_seconds and (time.time() - start) >= cfg.max_seconds:
            reason = REASON_BUDGET
            break
        if cfg.max_tokens and tokens_used >= cfg.max_tokens:
            reason = REASON_COST
            break
        if cfg.max_cost and cost_used >= cfg.max_cost:
            reason = REASON_COST
            break

        order = prioritize(tasks, policy, cfg.planner, cfg.model, ranker)
        if not order:
            reason = REASON_DRAINED
            break
        task = order[0]

        cycle += 1
        cycle_start = time.time()
        task.status = "doing"
        persist_task(cfg, task)

        location = decide_location(task, policy, cfg)
        if location != "local":
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} を {location} で実行"
                                        + (f"（{cfg.git_bus}）" if location == "remote" else ""))
        act_msg = "(dry-run)"
        if not cfg.dry_run:
            _, act_msg = act(task, cfg, location)
        dtok, dusd = parse_cost(act_msg)             # このサイクルのコストを計上（予算ゲート用）
        tokens_used += dtok
        cost_used += dusd
        if dtok or dusd:
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} cost tokens={dtok} usd={dusd:.4f}"
                                        f"（累計 tokens={tokens_used} usd={cost_used:.4f}）")

        ok, vmsg = run_verify(task.verify, cfg.workdir, cfg.verify_timeout)
        regressed = False
        if ok and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故の検知）
            rok, rmsg = run_verify(cfg.regression_cmd, cfg.workdir, cfg.verify_timeout)
            if not rok:
                regressed = True
                if cfg.regression_revert:
                    _revert_workdir(cfg)
                _block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}",
                       reasons)
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（回帰検知）"
                               + ("・revert 済" if cfg.regression_revert else ""))
        if regressed:
            pass                          # 既に blocked 化済み。done/review にしない
        elif ok and needs_human_review(task, policy):
            # verify は通ったが承認ゲート対象 → done を確定せず人の検収待ち（review）へ
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ref = extract_delivery_ref(act_msg, cfg)
            task.status = "review"
            task.extra = [(k, v) for k, v in task.extra
                          if k not in ("gate_ref", "gate_vmsg", "gate_ts")]
            task.extra += [("gate_ref", ref), ("gate_ts", ts),
                           ("gate_vmsg", vmsg.replace("\n", " ")[:200])]
            reasons[task.id] = "検収待ち（verify=PASS。approve で done 確定）"
            persist_task(cfg, task)
            write_needs_file(cfg, task,
                             "verify=PASS だが承認ゲート対象（review/policy.gate）。"
                             "approve で done 確定、フィードバック記入で差し戻し（再実行）", review=True)
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 検収待ち（承認ゲート） — {ref}")
        elif ok:
            task.status = "done"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ref = extract_delivery_ref(act_msg, cfg)     # 成果物の参照（PR/commit/git）
            if dtok or dusd:                             # コストを納品書に残し stats で集計可能に
                task.extra.append(("cost", f"tokens={dtok} usd={dusd:.4f}"))
            append_delivery(cfg, task, ref, ts)          # 受領書一覧に追記
            if cfg.do_archive:
                archive_task(cfg, task, vmsg, ref, ts)   # backlog → archive/（納品書付き）
                archived += 1
                done_disp = "DONE → archive（納品書）"
            else:
                delete_task_file(cfg, task)
                done_disp = "DONE 削除"
            clear_needs_file(cfg, task.id)
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} {done_disp} — {ref}")
            specs = parse_followups(task, act_msg)        # done から派生タスクを生む（backlog 自走）
            if specs and spawned_total < cfg.max_spawn:
                new = spawn_followups(cfg, task, specs, tasks, cfg.max_spawn - spawned_total)
                spawned_total += len(new)
                if new:
                    append_journal(cfg.journal, f"cycle {cycle}: {task.id} から派生生成 "
                                                f"{[t.id for t in new]}")
        else:
            task.retries += 1
            if not task.verify:
                _escalate(cfg, task, "verify 未定義", reasons, cycle)
                if task.norm_status() == "blocked":
                    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（verify 未定義）")
            elif task.retries > cfg.max_retries:
                learned = find_learned_resolution(cfg, task) if cfg.learn else None
                if learned and not dict(task.extra).get("autolearned"):
                    src, guide = learned
                    task.extra = [(k, v) for k, v in task.extra
                                  if k not in ("feedback", "autolearned")]
                    task.extra += [("feedback", guide.replace("\n", " ⏎ ")), ("autolearned", "1")]
                    task.status = "ready"
                    persist_task(cfg, task)
                    append_decision(cfg, task.id, "auto",
                                    context=f"{task.id}（{task.title}）を学習で自動解決",
                                    action="auto-resolve", reason=f"learned from {src}: {guide[:120]}",
                                    affects=f"{task.id} → ready")
                    append_journal(cfg.journal, f"cycle {cycle}: {task.id} 学習で自動解決"
                                                f"（{src} に倣う・通知を抑制）")
                else:
                    _escalate(cfg, task, f"繰り返し NG（retries={task.retries}）: {vmsg}",
                              reasons, cycle)
                    if task.norm_status() == "blocked":
                        append_journal(cfg.journal,
                                       f"cycle {cycle}: {task.id} → 人の判断（繰り返し NG）")
            else:
                task.status = "ready"
                persist_task(cfg, task)
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} NG 積み直し "
                                            f"({task.retries}/{cfg.max_retries}) — {vmsg}")

        if cfg.once:
            reason = "once"
            break
        delay = decide_pace(cfg, time.time() - cycle_start)
        if delay > 0:
            sleeper(delay)

    counts = summarize(tasks)
    newly_blocked = {t.id for t in tasks
                     if t.norm_status() in ("blocked", "review")} - pre_blocked
    budget_stop = reason in (REASON_BUDGET, REASON_COST)
    notified = notify(cfg, tasks, reasons, newly_blocked, budget_stop)
    promoted = promote_learnings(cfg) if cfg.ltm else []   # 効いた学習を ltm-use へ昇格
    _cleanup_bus(cfg)             # 不要な一時ファイル（kiro-flow バスの run 状態）を掃除
    append_journal(cfg.journal, f"=== kiro-autonomous 停止 reason={reason} cycles={cycle} "
                                f"done={counts['done']} blocked={counts['blocked']} "
                                f"notified={notified} promoted={len(promoted)} ===")
    return {"reason": reason, "cycles": cycle, "counts": counts, "tasks": tasks,
            "reasons": reasons, "newly_blocked": newly_blocked, "notified": notified,
            "ingested": ingested, "archived": archived, "promoted": promoted,
            "spawned": spawned_total, "tokens": tokens_used, "cost": cost_used}


def _cleanup_bus(cfg: Config) -> None:
    """local run 後に不要となる kiro-flow バスの一時状態（runs/inbox）を削除する。
    daemon 稼働中や git バス（remote）は作業中のため触らない。"""
    if not cfg.cleanup or cfg.git_bus or daemon_running(cfg, use_git=False):
        return
    for sub in ("runs", "inbox"):
        shutil.rmtree(cfg.bus / sub, ignore_errors=True)


def exit_code_for(result: dict) -> int:
    counts = result["counts"]
    if counts["blocked"] > 0 or counts.get("review", 0) > 0:   # 人の対応待ち（判断 or 検収承認）
        return 1
    if result["reason"] == REASON_DRAINED:
        return 0
    return 2


# ---------------------------------------------------------------------------
# watch（終了条件後もプロセス常駐。エージェントは待機しない＝idle 中は起動しない）
# ---------------------------------------------------------------------------
def has_work(cfg: Config) -> bool:
    """次パスを起こすべき仕事があるか（新規/実行待ちタスク or フィードバック）。安価な FS 走査のみ。"""
    for t in load_tasks(cfg.backlog):
        if t.norm_status() in CONSUMABLE or t.norm_status() == "inbox":
            return True
    if cfg.needs.exists():
        for nf in cfg.needs.glob("*.md"):
            if read_feedback(nf):
                return True
    return False


def run_watch(cfg: Config, act=act_via_kiro_flow, ranker=None, sleeper=time.sleep,
              max_passes=None) -> dict:
    passes = 0
    last: dict = {}
    while True:
        last = run_loop(cfg, act, ranker, sleeper)
        passes += 1
        c = last["counts"]
        print(f"[watch] pass {passes}: reason={last['reason']} "
              f"done={c['done']} blocked={c['blocked']}", flush=True)
        if max_passes is not None and passes >= max_passes:
            return last
        append_journal(cfg.journal, "=== watch: 監視中（新規タスク/フィードバック待ち。"
                                    "エージェントは待機しない）===")
        while not has_work(cfg):     # idle: kiro-cli/flow は一切起動しない
            sleeper(cfg.poll)


# ---------------------------------------------------------------------------
# 人の操作コマンド（いずれも案件毎の決定記録を残す）
# ---------------------------------------------------------------------------
def cmd_approve(cfg: Config, tid: str, reason: str) -> int:
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    if t.norm_status() == "review":
        # 検収ゲートの承認 = done 確定（verify は実行済み。保持した成果参照で納品書を書く）
        ex = dict(t.extra)
        ref = ex.get("gate_ref", "")
        ts = ex.get("gate_ts") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        vmsg = ex.get("gate_vmsg", "")
        t.status = "done"
        t.extra = [(k, v) for k, v in t.extra if k not in ("gate_ref", "gate_ts", "gate_vmsg")]
        append_delivery(cfg, t, ref, ts)
        disp = "done（承認・納品書）"
        if cfg.do_archive:
            archive_task(cfg, t, vmsg or f"承認: {reason}", ref, ts)
        else:
            delete_task_file(cfg, t)
            disp = "done（承認・削除）"
        clear_needs_file(cfg, tid)
        dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を検収承認",
                             action="approve-done", reason=reason, affects=f"{tid} → done")
        print(f"{dr}: {tid} を承認し {disp} 確定しました。")
        return 0
    t.status = "ready"
    persist_task(cfg, t)
    clear_needs_file(cfg, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を人の判断から復帰",
                         action="approve-and-fix", reason=reason, affects=f"{tid} → ready",
                         learn=(t.title, reason))
    print(f"{dr}: {tid} を ready に積み直しました。")
    return 0


def cmd_hold(cfg: Config, tid: str, reason: str) -> int:
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    append_policy(cfg.policy, "deny", tid)
    _block(cfg, t, f"hold（人が保留）: {reason}", {})
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} を保留（denylist 化）",
                         action="hold(deny)", reason=reason,
                         affects=f"{tid} → blocked, policy.deny += {tid}")
    print(f"{dr}: {tid} を hold（policy.deny 追加）しました。")
    return 0


def cmd_reprioritize(cfg: Config, tid: str, kind: str, reason: str) -> int:
    append_policy(cfg.policy, kind, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} の優先度を変更",
                         action=f"reprioritize({kind})", reason=reason,
                         affects=f"policy.{kind} += {tid}")
    print(f"{dr}: {tid} を {kind}（policy.{kind} 追加）しました。")
    return 0


def cmd_needs(cfg: Config) -> int:
    tasks = load_tasks(cfg.backlog)
    blocked, intake, review = human_worklist(tasks)
    print(render_digest(blocked, intake, {}, budget_stop=False, review=review))
    if blocked or review:
        print(f"（各案件の詳細・フィードバック欄: {cfg.needs}/<id>.md）")
    return 1 if (blocked or review) else 0


def _decision_action_tally(decisions_dir: Path) -> "dict[str, int]":
    """decisions/*.md の `- action  : X` を数える（ループ計測の素）。"""
    tally: dict[str, int] = {}
    if not decisions_dir.exists():
        return tally
    pat = re.compile(r"^- action\s*:\s*(?P<a>.+)$")
    for f in decisions_dir.glob("*.md"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pat.match(line.strip())
            if m:
                a = m.group("a").strip()
                tally[a] = tally.get(a, 0) + 1
    return tally


def compute_stats(cfg: Config) -> dict:
    """archive・decisions・DELIVERY・backlog から決定的にループの KPI を集計する。"""
    tasks = load_tasks(cfg.backlog)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.norm_status()] = by_status.get(t.norm_status(), 0) + 1
    arch_dir = cfg.archive_dir()
    archived = sorted(arch_dir.glob("*.md")) if arch_dir.exists() else []
    arch_tasks = [parse_task(p.read_text(encoding="utf-8"), p.stem) for p in archived]
    deliv_rows = 0
    dp = Path(cfg.delivery) if cfg.delivery else None
    if dp and dp.exists():
        for line in dp.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("|") and not s.startswith("| id") and "---" not in s:
                deliv_rows += 1
    actions = _decision_action_tally(cfg.decisions)
    auto = actions.get("auto-resolve", 0) + actions.get("auto-adjudicate", 0)
    human = (actions.get("approve-done", 0) + actions.get("approve-and-fix", 0)
             + actions.get("hold(deny)", 0) + actions.get("feedback-resume", 0))
    routed = auto + human
    done = len(archived)
    pending_human = by_status.get("blocked", 0) + by_status.get("review", 0)
    tok_total, usd_total = 0, 0.0                         # 納品書の `- cost: tokens=.. usd=..` を集計
    for t in arch_tasks:
        dt, du = parse_cost("@cost " + dict(t.extra).get("cost", ""))
        tok_total += dt
        usd_total += du
    return {
        "backlog_pending": len(tasks),
        "by_status": by_status,
        "pending_human": pending_human,                 # blocked + review（要対応）
        "done_archived": done,
        "delivery_rows": deliv_rows,
        "decisions_total": sum(actions.values()),
        "actions": actions,
        "auto_resolved": auto,                           # auto-resolve + auto-adjudicate
        "human_actions": human,
        "automation_rate": (auto / routed) if routed else None,  # 機械で捌けた割合
        "retries_pending_sum": sum(t.retries for t in tasks),
        "retries_archived_sum": sum(t.retries for t in arch_tasks),
        "first_pass_done": sum(1 for t in arch_tasks if t.retries == 0),  # 一発 done
        "tokens_archived": tok_total,                     # archive 済みタスクの累計コスト
        "cost_archived": round(usd_total, 4),
    }


def cmd_stats(cfg: Config, as_json: bool = False) -> int:
    """ループの計測値を出す（スループット・自動化率・retry・人対応待ち）。回路調整の土台。"""
    s = compute_stats(cfg)
    if as_json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    rate = s["automation_rate"]
    rate_disp = f"{rate*100:.0f}%" if rate is not None else "—"
    fp = s["first_pass_done"]
    fp_disp = f"{fp}/{s['done_archived']}" if s["done_archived"] else "—"
    print("=== kiro-autonomous stats ===")
    print(f"完了(archive)   : {s['done_archived']}（一発 done {fp_disp}）")
    print(f"納品(DELIVERY)  : {s['delivery_rows']}")
    print(f"未消化 backlog  : {s['backlog_pending']}  {s['by_status']}")
    print(f"人の対応待ち    : {s['pending_human']}（blocked + review）")
    print(f"自動解決/人対応 : {s['auto_resolved']} / {s['human_actions']}  → 自動化率 {rate_disp}")
    print(f"retry 累計      : pending {s['retries_pending_sum']} / archived {s['retries_archived_sum']}")
    print(f"コスト(archive) : tokens {s['tokens_archived']} / usd {s['cost_archived']}")
    print(f"決定記録        : {s['decisions_total']} 件  {s['actions']}")
    return 0


def cmd_rot(cfg: Config, fix: bool) -> int:
    tasks = load_tasks(cfg.backlog)
    rot = detect_rot(cfg, tasks)
    if not rot:
        print("rot は見つかりませんでした。")
        return 0
    print(f"rot を {len(rot)} 件検出:")
    for t, reason in rot:
        print(f"  {t.id}: {t.title} — {reason}")
        if fix:
            _block(cfg, t, f"rot: {reason}", {})
    if fix:
        print("→ いずれも人の判断（blocked）へ回しました。")
    return 1


def cmd_promote(cfg: Config) -> int:
    """効いた学習（decisions/ の learn）を ltm-use 長期記憶へ昇格（エージェント不要）。"""
    cfg.ltm = True   # promote は明示操作なので ltm を有効化
    mem_dir = ltm_memories_dir(cfg)
    promoted = promote_learnings(cfg)
    print(f"昇格先: {mem_dir}")
    if not promoted:
        hits = count_learn_hits(cfg)
        print(f"昇格対象なし（threshold={cfg.promote_threshold}・既存hits={hits or '無'}）。")
        return 0
    print(f"{len(promoted)} 件を昇格:")
    for src, memid in promoted:
        print(f"  decisions/{src} → {memid}")
    return 0


def cmd_triage(cfg: Config) -> int:
    ensure_dirs(cfg)
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    for t, why in triage(tasks, policy):
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)
    for t in tasks:
        persist_task(cfg, t)
    order = prioritize(tasks, policy, cfg.planner, cfg.model)
    print("優先順位（消化対象）:")
    for i, t in enumerate(order, 1):
        print(f"  {i}. {t.id}: {t.title}")
    return 0


def cmd_run(cfg: Config) -> int:
    ensure_dirs(cfg)
    reg = register_instance(cfg)   # 外部操作者が「監視中フォルダ」を発見できるよう登録
    try:
        if cfg.watch:
            run_watch(cfg)
            return 0
        result = run_loop(cfg)
        counts = result["counts"]
        print("\n=== kiro-autonomous 完了 ===")
        print(f"停止理由 : {result['reason']}")
        print(f"サイクル : {result['cycles']}")
        print(f"done={counts['done']} blocked={counts['blocked']} ready={counts['ready']} "
              f"inbox={counts['inbox']} archived={result.get('archived', 0)} "
              f"ingested={len(result.get('ingested', []))} "
              f"promoted={len(result.get('promoted', []))}")
        return exit_code_for(result)
    finally:
        if reg is not None:
            try:
                reg.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 設定ファイル（kiro-flow と同じ流儀: YAML 任意 / JSON フォールバック）
#   優先順位 CLI > 設定ファイル > 組み込み既定。環境ごとに決まる値をファイルに、
#   その場限りの上書きだけ CLI で渡す。PyYAML 無し環境は JSON（同じキー）で。
# ---------------------------------------------------------------------------
try:
    import yaml  # type: ignore

    def _load_config_file(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
except ImportError:  # PyYAML 無し → JSON のみ
    yaml = None  # type: ignore

    def _load_config_file(path: str) -> dict:  # type: ignore[misc]
        if path.lower().endswith((".yaml", ".yml")):
            print("[kiro-autonomous] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（kiro-autonomous.json・同じキー）なら不要です。", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


DEFAULT_CONFIG_NAMES = ["kiro-autonomous.yaml", "kiro-autonomous.yml", "kiro-autonomous.json"]

# 設定ファイルで上書きできるキー（snake_case）と組み込み既定。
# CLI 引数の default は None にし、resolve_config で「設定ファイル→ここ」の順に埋める。
# 真偽フラグ（--watch / --ltm / --no-archive 等）と個別パス上書きは CLI 専用。
CONFIG_DEFAULTS = {
    "root": ".kiro-autonomous",
    "workdir": ".",
    "executor": "kiro",
    "planner": "kiro",
    "flow_planner": "flow-planner",
    "location": "auto",
    "model": None,
    "poll": 5.0,
    "debounce": 3.0,
    "pace": 0.0,
    "max_cycles": 20,
    "max_seconds": 0.0,
    "max_tokens": 0,
    "max_cost": 0.0,
    "max_retries": 2,
    "max_iterations": 3,
    "verify_timeout": 120.0,
    "act_timeout": 1800.0,
    "git_bus": None,
    "git_branch": "main",
    "git_subdir": None,
    "kiro_flow": None,
    "notify_cmd": None,
    "actor": os.environ.get("USER", "user"),
    "learn_threshold": 0.5,
    "promote_threshold": 2,
    "ltm_home": None,
    "rot_age_days": 14.0,
    "auto_adjudicate": True,    # 真偽だが --auto-adjudicate/--no-... の三値で config 上書き可（既定 on）
    "adjudicate_max": 1,
    "max_spawn": 20,            # 1 run の派生タスク生成上限（0 で無効）
    "regression_cmd": None,     # done 確定前のグローバル回帰検査コマンド（巻き込み事故の検知）
    "regression_revert": False,
    # 真偽フラグ（CLI > 設定ファイル > 既定）。CLI 未指定（None）なら設定ファイル→この既定で確定
    "watch": False, "once": False, "dry_run": False, "rot": False, "ltm": False,
    "do_archive": True, "learn": True, "cleanup": True,   # do_archive: --archive はパス用なので別名
}


def _find_config(explicit):
    """設定ファイルの探索: 1) --config 明示 2) ./.kiro/ 3) ~/.kiro/（kiro-flow と同じ .kiro）。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            print(f"[kiro-autonomous] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    for base in (os.path.join(os.getcwd(), ".kiro"),
                 os.path.join(os.path.expanduser("~"), ".kiro")):
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(base, name)
            if os.path.isfile(cand):
                return cand
    return None


def resolve_config(args):
    """CLI 未指定（None）の設定値だけを 設定ファイル→組み込み既定 で埋める（CLI > config > 既定）。"""
    path = _find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    args._config_path = path
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, dflt))
    return args


def build_config(args) -> Config:
    workdir = Path(args.workdir).resolve()
    root = Path(args.root)
    root = root if root.is_absolute() else (workdir / root)

    def under(name, sub):
        """個別指定があればそれを、無ければルート（既定 ./.kiro-autonomous）配下に集約。"""
        v = getattr(args, name, None)
        if v:
            p = Path(v)
            return p if p.is_absolute() else (workdir / p)
        return root / sub

    return Config(
        backlog=under("backlog", "backlog"),
        policy=under("policy", "policy.md"),
        decisions=under("decisions", "decisions"),
        journal=under("journal", "journal.md"),
        needs=under("needs", "needs"),
        workdir=workdir,
        bus=under("bus", "bus"),
        git_bus=args.git_bus, git_branch=args.git_branch, git_subdir=args.git_subdir,
        kiro_flow=args.kiro_flow, planner=args.planner, flow_planner=args.flow_planner,
        location=args.location, executor=args.executor,
        model=args.model, max_iterations=args.max_iterations,
        max_cycles=args.max_cycles, max_seconds=args.max_seconds,
        max_tokens=getattr(args, "max_tokens", 0) or 0,
        max_cost=getattr(args, "max_cost", 0.0) or 0.0,
        max_retries=args.max_retries, pace=args.pace, verify_timeout=args.verify_timeout,
        act_timeout=args.act_timeout, notify_cmd=args.notify_cmd, actor=args.actor,
        archive=under("archive", "archive"), do_archive=bool(getattr(args, "do_archive", True)),
        learn=bool(getattr(args, "learn", True)), learn_threshold=args.learn_threshold,
        auto_adjudicate=bool(getattr(args, "auto_adjudicate", True)),
        adjudicate_max=getattr(args, "adjudicate_max", 1),
        max_spawn=getattr(args, "max_spawn", 20),
        regression_cmd=getattr(args, "regression_cmd", None),
        regression_revert=bool(getattr(args, "regression_revert", False)),
        ltm=bool(getattr(args, "ltm", False)), ltm_home=resolve_ltm_home(getattr(args, "ltm_home", None)),
        promote_threshold=getattr(args, "promote_threshold", 2),
        rot=bool(getattr(args, "rot", False)), rot_age_days=args.rot_age_days,
        cleanup=bool(getattr(args, "cleanup", True)),
        delivery=under("delivery", "DELIVERY.md"), debounce=args.debounce,
        watch=bool(getattr(args, "watch", False)), poll=getattr(args, "poll", 5.0),
        dry_run=bool(getattr(args, "dry_run", False)), once=bool(getattr(args, "once", False)),
    )


def _add_common(sp):
    # 設定ファイルで上書き可能なキー（CONFIG_DEFAULTS）は default=None にし、resolve_config で確定する
    # （CLI > 設定ファイル > 組み込み既定）。個別パス上書きと真偽フラグは CLI 専用。
    sp.add_argument("--config", default=None,
                    help="設定ファイル（未指定なら ./.kiro → ~/.kiro の kiro-autonomous.{yaml,yml,json}）")
    sp.add_argument("--root", default=None,
                    help="作業ルート（cwd 相対、既定 ./.kiro-autonomous）。各ファイルはこの配下に集約")
    sp.add_argument("--backlog", default=None, help="バックログディレクトリ（既定 <root>/backlog）")
    sp.add_argument("--policy", default=None, help="（既定 <root>/policy.md）")
    sp.add_argument("--decisions", default=None, help="決定記録ディレクトリ（既定 <root>/decisions）")
    sp.add_argument("--journal", default=None, help="（既定 <root>/journal.md）")
    sp.add_argument("--needs", default=None, help="要対応ディレクトリ（既定 <root>/needs）")
    sp.add_argument("--archive", default=None, help="done の退避先（既定 <root>/archive）")
    sp.add_argument("--delivery", default=None, help="納品一覧（既定 <root>/DELIVERY.md）")
    sp.add_argument("--debounce", type=float, default=None,
                    help="watch 中、最終保存からこの秒数は feedback 取込を待つ（誤発火防止。既定 3）")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--bus", default=None, help="kiro-flow バス（既定 <root>/bus）")
    sp.add_argument("--git-bus", default=None, help="分散移譲先の共有 git リポジトリ")
    sp.add_argument("--git-branch", default=None)
    sp.add_argument("--git-subdir", default=None)
    sp.add_argument("--kiro-flow", default=None)
    sp.add_argument("--planner", default=None, choices=["kiro", "none"],
                    help="優先順位付け: kiro=エージェント（priority 加味）/ none=priority＋古さ（既定 kiro）")
    sp.add_argument("--flow-planner", default=None,
                    choices=["flow-planner", "kiro", "stub"], help="kiro-flow run に渡す planner（既定 flow-planner）")
    sp.add_argument("--location", default=None,
                    choices=["auto", "local", "daemon", "remote"], help="act の実行モード（既定 auto）")
    sp.add_argument("--executor", default=None, choices=["kiro", "stub"], help="（既定 kiro）")
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-iterations", type=int, default=None)
    sp.add_argument("--max-cycles", type=int, default=None, help="予算: サイクル数（既定 20）")
    sp.add_argument("--max-seconds", type=float, default=None, help="予算: 実時間（0=無制限）")
    sp.add_argument("--max-tokens", type=int, default=None,
                    help="予算: 消費トークン上限（0=無制限。act 出力の @cost を計上）")
    sp.add_argument("--max-cost", type=float, default=None,
                    help="予算: 金額(USD)上限（0=無制限。act 出力の @cost usd= を計上）")
    sp.add_argument("--max-retries", type=int, default=None)
    sp.add_argument("--pace", type=float, default=None, help="1サイクルの下限間隔（秒）。レーン減速")
    sp.add_argument("--verify-timeout", type=float, default=None)
    sp.add_argument("--act-timeout", type=float, default=None)
    sp.add_argument("--notify-cmd", default=None, help="要対応ダイジェストを渡す通知コマンド")
    sp.add_argument("--actor", default=None)
    sp.add_argument("--learn", action=argparse.BooleanOptionalAction, default=None,
                    help="DR 学習（過去の人の判断から類似案件を自動解決）。--no-learn で無効化（既定 on）")
    sp.add_argument("--learn-threshold", type=float, default=None,
                    help="DR 学習のタイトル類似度しきい値（0〜1。既定 0.5）")
    # 自律裁定: needs に落とす前に kiro-cli が積み直し可否を判断（三値: 未指定→設定ファイル/既定 on）
    sp.add_argument("--auto-adjudicate", dest="auto_adjudicate", action="store_true", default=None,
                    help="人の判断(needs)へ送る前に kiro-cli が『自律的に積み直すか人へ回すか』を裁定（既定 on）")
    sp.add_argument("--no-auto-adjudicate", dest="auto_adjudicate", action="store_false",
                    default=None, help="自律裁定を無効化して常に人へ回す（明示 off）")
    sp.add_argument("--adjudicate-max", type=int, default=None,
                    help="1タスクあたりの自律裁定の上限回数（有限停止のため。既定 1）")
    sp.add_argument("--max-spawn", type=int, default=None,
                    help="1 run で生成できる派生タスク（followup）数の上限（0 で無効。既定 20）")
    sp.add_argument("--regression-cmd", default=None,
                    help="done 確定前に走らせるグローバル回帰検査（失敗で done にせず人へ。巻き込み事故の検知）")
    sp.add_argument("--regression-revert", action=argparse.BooleanOptionalAction, default=None,
                    help="回帰検知時に作業ツリーの未コミット変更を巻き戻す（best-effort・既定 off）")
    sp.add_argument("--ltm", action=argparse.BooleanOptionalAction, default=None,
                    help="効いた学習を ltm-use 長期記憶へ昇格＋プロジェクト横断 recall（既定 off）")
    sp.add_argument("--ltm-home", default=None,
                    help="ltm-use ストアのルート（既定 KIRO_LTM_HOME → ~/.claude）")
    sp.add_argument("--promote-threshold", type=int, default=None,
                    help="learn ルールがこの回数以上効いたら昇格（既定 2）")
    sp.add_argument("--rot-age-days", type=float, default=None,
                    help="rot の stale 判定（経過日数。既定 14）")


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    p = argparse.ArgumentParser(
        prog="kiro-autonomous",
        description="backlog/ を優先順位付け・検証・収束させる制御層（Loop Engineering MVP）。"
                    "サブコマンドを省略すると常駐監視（run --watch）で起動し backlog 投入を待ち続ける")
    sub = p.add_subparsers(dest="cmd", required=False)

    run = sub.add_parser("run", help="正準ループ（優先順位付け→実行→検証→積み直し→収束）")
    _add_common(run)
    run.add_argument("--watch", action=argparse.BooleanOptionalAction, default=None,
                     help="終了条件後もプロセスを残し backlog を監視（エージェントは待機しない）")
    run.add_argument("--poll", type=float, default=None, help="watch のポーリング間隔（秒。既定 5）")
    run.add_argument("--no-archive", dest="do_archive", action="store_const", const=False,
                     default=None, help="done を archive/ へ退避せず削除（既定は退避。config: do_archive）")
    run.add_argument("--rot", action=argparse.BooleanOptionalAction, default=None,
                     help="triage で rot（古い/重複/実行不能）を検知し人の判断へ回す")
    run.add_argument("--cleanup", action=argparse.BooleanOptionalAction, default=None,
                     help="run 後に kiro-flow バスの一時状態を掃除（--no-cleanup で残す。既定 on）")
    run.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None,
                     help="act を飛ばし verify のみ")
    run.add_argument("--once", action=argparse.BooleanOptionalAction, default=None,
                     help="1 タスクだけ処理して終了")

    for name, helptext in [("triage", "優先順位付けのみ（inbox→ready 昇格・policy 適用）"),
                           ("needs", "人の判断待ち（blocked / need_intake）を表示"),
                           ("promote", "効いた学習を ltm-use 長期記憶へ昇格（エージェント不要）")]:
        _add_common(sub.add_parser(name, help=helptext))
    rot = sub.add_parser("rot", help="rot（古い/重複/実行不能）を検出して報告（--fix で blocked 化）")
    _add_common(rot); rot.add_argument("--fix", action="store_true", help="検出した rot を人の判断へ回す")

    st = sub.add_parser("stats", help="ループの計測値（スループット・自動化率・retry・人対応待ち）")
    _add_common(st); st.add_argument("--json", action="store_true", help="JSON で出力")

    ap = sub.add_parser("approve", help="判断待ちを修正承認して積み直し（決定記録）")
    _add_common(ap); ap.add_argument("id"); ap.add_argument("--reason", required=True)
    hd = sub.add_parser("hold", help="policy に deny 追加し保留（決定記録）")
    _add_common(hd); hd.add_argument("id"); hd.add_argument("--reason", required=True)
    rp = sub.add_parser("reprioritize", help="policy に pin/defer 追加（決定記録）")
    _add_common(rp); rp.add_argument("id")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--pin", action="store_true"); g.add_argument("--defer", action="store_true")
    rp.add_argument("--reason", required=True)

    inst = sub.add_parser("instances",
                          help="稼働中の kiro-autonomous（監視中フォルダ）を一覧（外部操作者の発見口）")
    inst.add_argument("--json", action="store_true", help="JSON で出力（スキル等が機械処理する用）")

    # サブコマンドを省略して呼ばれたら「常駐監視（run --watch）」を既定にする。
    # PC 起動時に立ち上げっぱなしにして backlog 投入を待つ使い方を一級にするため。
    _subcommands = {"run", "triage", "needs", "promote", "rot", "stats",
                    "approve", "hold", "reprioritize", "instances"}
    if not argv or (argv[0] not in _subcommands and argv[0] not in ("-h", "--help")):
        argv = ["run", "--watch", *argv]

    args = p.parse_args(argv)

    # instances は共通設定（backlog 等）を必要としない発見専用コマンド。
    if args.cmd == "instances":
        return cmd_instances(args.json)

    resolve_config(args)      # CLI 未指定値を 設定ファイル → 組み込み既定 で確定
    cfg = build_config(args)

    if args.cmd in ("triage", "needs", "rot") and not cfg.backlog.exists():
        print(f"エラー: バックログディレクトリがありません: {cfg.backlog}", file=sys.stderr)
        return 2

    return {
        "run": lambda: cmd_run(cfg),
        "triage": lambda: cmd_triage(cfg),
        "needs": lambda: cmd_needs(cfg),
        "stats": lambda: cmd_stats(cfg, getattr(args, "json", False)),
        "promote": lambda: cmd_promote(cfg),
        "rot": lambda: cmd_rot(cfg, getattr(args, "fix", False)),
        "approve": lambda: cmd_approve(cfg, args.id, args.reason),
        "hold": lambda: cmd_hold(cfg, args.id, args.reason),
        "reprioritize": lambda: cmd_reprioritize(
            cfg, args.id, "pin" if args.pin else "defer", args.reason),
    }[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
