#!/usr/bin/env python3
"""kiro-marshal — Loop Engineering MVP（バックログを捌く制御層）

正準ループ（設計書 docs/designs/2026-06-16-kiro-marshal-mvp-design.md §2）:
  ① backlog/（案件毎ファイル）を読み優先順位をつけ、最優先タスクを kiro-flow に投げる
  ② 優先順位付けは原則 kiro-cli。stub 時は最古優先（FIFO）。人間は policy.md で上書きできる
  ③ kiro-flow の結果を verify ゲートで検証。done はファイル削除、NG は積み直す
  ④ backlog が尽きるか予算（サイクル数/実時間）が尽きるまで反復。--watch なら尽きても
     プロセスは生存して監視（エージェントは待機しない＝idle 中は kiro-cli/flow を起動しない）
  ⑤ ユーザーの判断は案件毎の decisions/<id>.md に保存。needs/<id>.md のフィードバック欄に
     書き込むと拾って再開する

二層構成: kiro-flow が実行（act）、kiro-marshal が優先順位付け・検証・収束・決定記録を担う。
標準ライブラリのみ。kiro-cli が無くても --planner stub / --executor stub / --dry-run で動く。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
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

VALID_STATUS = ("inbox", "ready", "doing", "done", "blocked")
CONSUMABLE = ("ready", "todo")  # 実行待ち。todo は ready の後方互換エイリアス
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")
POLICY_RE = re.compile(r"^(?P<key>deny|pin|defer|offload):\s*(?P<val>.+)$")
DR_HEADER_RE = re.compile(r"^##\s+DR-(\d+)\b")
FEEDBACK_MARKER = "## フィードバック"

# 停止理由
REASON_DRAINED = "drained"  # 消化可能タスクが尽きた（実質完了）
REASON_BUDGET = "budget"    # 予算（サイクル数/実時間）が尽きた


# ---------------------------------------------------------------------------
# タスク（案件毎ファイル backlog/<id>.md）
# ---------------------------------------------------------------------------
@dataclass
class Task:
    id: str
    title: str
    status: str = "ready"
    source: str = "human"
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


# ---------------------------------------------------------------------------
# policy.md（人間による順位付け・実行先の上書き）
# ---------------------------------------------------------------------------
@dataclass
class Policy:
    deny: "list[str]" = field(default_factory=list)
    pin: "list[str]" = field(default_factory=list)
    defer: "list[str]" = field(default_factory=list)
    offload: "list[str]" = field(default_factory=list)


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


def append_policy(path: Path, key: str, value: str) -> None:
    header = "" if path.exists() else "# kiro-marshal policy（人間による上書き）\n\n"
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
                    action: str, reason: str, affects: str) -> str:
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    path = decision_path(cfg, tid)
    dr = next_dr_id(path)
    date = datetime.now().strftime("%Y-%m-%d")
    block = (f"## {dr}  {date}  actor: {actor}\n"
             f"- context : {context}\n- action  : {action}\n"
             f"- reason  : {reason}\n- affects : {affects}\n\n")
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return dr


# ---------------------------------------------------------------------------
# 通知（案件毎 needs/<id>.md）＋ フィードバック往復
# ---------------------------------------------------------------------------
def needs_path(cfg: "Config", tid: str) -> Path:
    return cfg.needs / f"{tid}.md"


def write_needs_file(cfg: "Config", task: Task, reason: str) -> None:
    cfg.needs.mkdir(parents=True, exist_ok=True)
    body = (
        f"# 要対応: {task.id} — {task.title}\n\n"
        f"- なぜ: {reason}\n"
        f"- 状態: blocked（kiro-marshal の判断待ち）\n\n"
        f"{FEEDBACK_MARKER}\n"
        f"<!-- ここに修正方針・指示を書いて保存すると、kiro-marshal が拾ってブロックを解除し、\n"
        f"     内容を次の実行に反映します。あるいは `kiro-marshal approve {task.id}`。 -->\n"
    )
    needs_path(cfg, task.id).write_text(body, encoding="utf-8")


def clear_needs_file(cfg: "Config", tid: str) -> None:
    p = needs_path(cfg, tid)
    if p.exists():
        p.unlink()


def read_feedback(path: Path) -> str:
    """needs ファイルの『## フィードバック』以降から人の記入（HTMLコメント除く）を取り出す。"""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    i = text.find(FEEDBACK_MARKER)
    return text[i + len(FEEDBACK_MARKER):].strip() if i >= 0 else ""


def ingest_feedback(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """needs/<id>.md に人の記入があれば、対象をブロック解除し内容を次の act に渡す。"""
    ingested: list[str] = []
    if not cfg.needs.exists():
        return ingested
    by_id = {t.id: t for t in tasks}
    for nf in sorted(cfg.needs.glob("*.md")):
        fb = read_feedback(nf)
        t = by_id.get(nf.stem)
        if not fb or t is None:
            continue
        t.status = "ready"
        t.extra = [(k, v) for k, v in t.extra if k != "feedback"]
        t.extra.append(("feedback", fb.replace("\n", " ⏎ ")))
        persist_task(cfg, t)
        append_decision(cfg, t.id, cfg.actor, context=f"{t.id} に人のフィードバック",
                        action="feedback-resume", reason=fb[:200], affects=f"{t.id} → ready")
        nf.unlink()
        append_journal(cfg.journal, f"feedback 取り込み: {t.id} を再開")
        ingested.append(t.id)
    return ingested


def human_worklist(tasks: "list[Task]") -> "tuple[list[Task], list[Task]]":
    blocked = [t for t in tasks if t.norm_status() == "blocked"]
    intake = [t for t in tasks if t.norm_status() == "inbox" and not t.verify.strip()]
    return blocked, intake


def render_digest(blocked, intake, reasons: dict, budget_stop: bool) -> str:
    lines = ["# 要対応（kiro-marshal）", ""]
    if budget_stop:
        lines += ["⚠ 予算切れで未消化のまま停止しました。", ""]
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
    if not blocked and not intake:
        lines.append("（対応待ちなし）")
    return "\n".join(lines) + "\n"


def notify(cfg: "Config", tasks, reasons: dict, newly_blocked: set, budget_stop: bool) -> bool:
    """状態遷移時だけ stdout / notify-cmd へ要約を出す（案件毎の needs/<id>.md は別途書込済）。"""
    if not newly_blocked and not budget_stop:
        return False
    blocked, intake = human_worklist(tasks)
    digest = render_digest(blocked, intake, reasons, budget_stop)
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


def _extract_id_array(text: str) -> "list[str] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return [str(x) for x in arr] if isinstance(arr, list) else None


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
    listing = "\n".join(f"- {t.id}: {t.title}（source={t.source}）" for t in ready)
    prompt = ("あなたはバックログの優先順位付け役。次のタスク群を、重要度・緊急度・依存関係から"
              "優先順位の高い順に並べ替え、**タスクID の JSON 配列だけ**を出力してください"
              "（説明文なし）。\n\nタスク:\n" + listing)
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


def apply_policy_order(ordered: "list[Task]", policy: Policy) -> "list[Task]":
    def hit(t, pats):
        return any(t.matches(p) for p in pats)
    pinned = [t for t in ordered if hit(t, policy.pin)]
    deferred = [t for t in ordered if not hit(t, policy.pin) and hit(t, policy.defer)]
    middle = [t for t in ordered if t not in pinned and t not in deferred]
    return pinned + middle + deferred


def prioritize(tasks, policy, planner, model=None, ranker=None) -> "list[Task]":
    ready = consumable_tasks(tasks)
    if planner == "stub":
        base = list(ready)  # mtime 昇順 = 最古優先（FIFO）
    else:
        rank = (ranker or rank_agent)(ready, model)
        base = rank if rank is not None else list(ready)
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
    if cfg.git_bus and any(task.matches(p) for p in policy.offload):
        return "remote"
    return "local"


def _kf_base(cfg: "Config", use_git: bool) -> "list[str]":
    base = resolve_kiro_flow(cfg.kiro_flow) + ["--bus", str(cfg.bus)]
    if use_git and cfg.git_bus:
        base += ["--git", cfg.git_bus, "--git-branch", cfg.git_branch]
        if cfg.git_subdir:
            base += ["--git-subdir", cfg.git_subdir]
    return base


def build_kiro_flow_cmd(task: Task, cfg: "Config", location: str = "local") -> "list[str]":
    """kiro-flow run（都度起動）のコマンド。remote なら共有 git バスへ移譲する。"""
    return _kf_base(cfg, location == "remote") + [
        "run", build_request(task), "--planner", cfg.planner,
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


def _act_run(task: Task, cfg: "Config", location: str) -> "tuple[bool, str]":
    """daemon が無いとき: kiro-flow run で都度起動（同期実行）。"""
    cmd = build_kiro_flow_cmd(task, cfg, location)
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
    """daemon があれば submit、無ければ run で都度起動する。"""
    use_git = location == "remote" and bool(cfg.git_bus)
    if daemon_running(cfg, use_git):
        return _act_submit(task, cfg, use_git)
    return _act_run(task, cfg, location)


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
    planner: str = "flow-planner"
    executor: str = "kiro"
    model: "str | None" = None
    max_iterations: int = 3
    max_cycles: int = 20
    max_seconds: float = 0.0
    max_retries: int = 2
    pace: float = 0.0
    verify_timeout: float = 120.0
    act_timeout: float = 1800.0
    notify_cmd: "str | None" = None
    actor: str = "user"
    archive: "Path | None" = None   # done の退避先ディレクトリ（既定 archive/）
    do_archive: bool = True         # done を archive/ へ退避（False なら削除）
    watch: bool = False     # 終了条件後もプロセスを残し backlog を監視
    poll: float = 5.0       # watch のポーリング間隔（秒）
    dry_run: bool = False
    once: bool = False

    def archive_dir(self) -> Path:
        return self.archive or (self.backlog.parent / "archive")


def ensure_dirs(cfg: Config) -> None:
    for d in (cfg.backlog, cfg.needs, cfg.decisions):
        d.mkdir(parents=True, exist_ok=True)
    cfg.journal.parent.mkdir(parents=True, exist_ok=True)


def archive_task(cfg: Config, task: Task) -> None:
    """done タスクを backlog から archive/<id>.md へ退避（move）。backlog は未完だけが残る。"""
    cfg.archive_dir().mkdir(parents=True, exist_ok=True)
    task.extra.append(("archived", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    (cfg.archive_dir() / f"{task.id}.md").write_text(serialize_task(task), encoding="utf-8")
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


def run_loop(cfg: Config, act=act_via_kiro_flow, ranker=None, sleeper=time.sleep) -> dict:
    ensure_dirs(cfg)
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    reasons: dict[str, str] = {}

    ingested = ingest_feedback(cfg, tasks)           # 人のフィードバックでブロック解除
    pre_blocked = {t.id for t in tasks if t.norm_status() == "blocked"}
    for t, why in triage(tasks, policy):
        reasons[t.id] = why
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)

    append_journal(cfg.journal, f"=== kiro-marshal 開始 tasks={len(tasks)} "
                                f"ingested={len(ingested)} planner={cfg.planner} "
                                f"executor={cfg.executor} dry_run={cfg.dry_run} ===")
    start = time.time()
    cycle = 0
    archived = 0
    reason = REASON_DRAINED

    while True:
        if cycle >= cfg.max_cycles:
            reason = REASON_BUDGET
            break
        if cfg.max_seconds and (time.time() - start) >= cfg.max_seconds:
            reason = REASON_BUDGET
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
        if location == "remote":
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} を分散環境へ移譲（{cfg.git_bus}）")
        if not cfg.dry_run:
            act(task, cfg, location)

        ok, vmsg = run_verify(task.verify, cfg.workdir, cfg.verify_timeout)
        if ok:
            task.status = "done"
            if cfg.do_archive:
                archive_task(cfg, task)       # backlog → archive/ へ退避
                archived += 1
                done_disp = "DONE → archive"
            else:
                delete_task_file(cfg, task)
                done_disp = "DONE 削除"
            clear_needs_file(cfg, task.id)
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} {done_disp} — {vmsg}")
        else:
            task.retries += 1
            if not task.verify:
                _block(cfg, task, "verify 未定義", reasons)
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（verify 未定義）")
            elif task.retries > cfg.max_retries:
                _block(cfg, task, f"繰り返し NG（retries={task.retries}）: {vmsg}", reasons)
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（繰り返し NG）")
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
    newly_blocked = {t.id for t in tasks if t.norm_status() == "blocked"} - pre_blocked
    budget_stop = reason == REASON_BUDGET
    notified = notify(cfg, tasks, reasons, newly_blocked, budget_stop)
    append_journal(cfg.journal, f"=== kiro-marshal 停止 reason={reason} cycles={cycle} "
                                f"done={counts['done']} blocked={counts['blocked']} "
                                f"notified={notified} ===")
    return {"reason": reason, "cycles": cycle, "counts": counts, "tasks": tasks,
            "reasons": reasons, "newly_blocked": newly_blocked, "notified": notified,
            "ingested": ingested, "archived": archived}


def exit_code_for(result: dict) -> int:
    counts = result["counts"]
    if counts["blocked"] > 0:
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
    t.status = "ready"
    persist_task(cfg, t)
    clear_needs_file(cfg, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} を人の判断から復帰",
                         action="approve-and-fix", reason=reason, affects=f"{tid} → ready")
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
    blocked, intake = human_worklist(tasks)
    print(render_digest(blocked, intake, {}, budget_stop=False))
    if blocked:
        print(f"（各案件の詳細・フィードバック欄: {cfg.needs}/<id>.md）")
    return 1 if blocked else 0


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
    if cfg.watch:
        run_watch(cfg)
        return 0
    result = run_loop(cfg)
    counts = result["counts"]
    print("\n=== kiro-marshal 完了 ===")
    print(f"停止理由 : {result['reason']}")
    print(f"サイクル : {result['cycles']}")
    print(f"done={counts['done']} blocked={counts['blocked']} ready={counts['ready']} "
          f"inbox={counts['inbox']} archived={result.get('archived', 0)} "
          f"ingested={len(result.get('ingested', []))}")
    return exit_code_for(result)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_config(args) -> Config:
    workdir = Path(args.workdir).resolve()

    def rel(name, default):
        p = Path(getattr(args, name, None) or default)
        return p if p.is_absolute() else (workdir / p)

    return Config(
        backlog=rel("backlog", "backlog"),
        policy=rel("policy", "policy.md"),
        decisions=rel("decisions", "decisions"),
        journal=rel("journal", "journal.md"),
        needs=rel("needs", "needs"),
        workdir=workdir,
        bus=rel("bus", ".kiro-marshal-bus"),
        git_bus=args.git_bus, git_branch=args.git_branch, git_subdir=args.git_subdir,
        kiro_flow=args.kiro_flow, planner=args.planner, executor=args.executor,
        model=args.model, max_iterations=args.max_iterations,
        max_cycles=args.max_cycles, max_seconds=args.max_seconds,
        max_retries=args.max_retries, pace=args.pace, verify_timeout=args.verify_timeout,
        act_timeout=args.act_timeout, notify_cmd=args.notify_cmd, actor=args.actor,
        archive=rel("archive", "archive"), do_archive=not getattr(args, "no_archive", False),
        watch=getattr(args, "watch", False), poll=getattr(args, "poll", 5.0),
        dry_run=getattr(args, "dry_run", False), once=getattr(args, "once", False),
    )


def _add_common(sp):
    sp.add_argument("--backlog", default="backlog", help="バックログディレクトリ（案件毎 *.md）")
    sp.add_argument("--policy", default="policy.md")
    sp.add_argument("--decisions", default="decisions", help="決定記録ディレクトリ（案件毎）")
    sp.add_argument("--journal", default="journal.md")
    sp.add_argument("--needs", default="needs", help="要対応ディレクトリ（案件毎・フィードバック欄）")
    sp.add_argument("--archive", default="archive", help="done の退避先ディレクトリ")
    sp.add_argument("--workdir", default=".")
    sp.add_argument("--bus", default=".kiro-marshal-bus")
    sp.add_argument("--git-bus", default=None, help="分散移譲先の共有 git リポジトリ")
    sp.add_argument("--git-branch", default="main")
    sp.add_argument("--git-subdir", default=None)
    sp.add_argument("--kiro-flow", default=None)
    sp.add_argument("--planner", default="flow-planner", choices=["kiro", "stub", "flow-planner"])
    sp.add_argument("--executor", default="kiro", choices=["kiro", "stub"])
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-iterations", type=int, default=3)
    sp.add_argument("--max-cycles", type=int, default=20, help="予算: サイクル数")
    sp.add_argument("--max-seconds", type=float, default=0.0, help="予算: 実時間（0=無制限）")
    sp.add_argument("--max-retries", type=int, default=2)
    sp.add_argument("--pace", type=float, default=0.0, help="1サイクルの下限間隔（秒）。レーン減速")
    sp.add_argument("--verify-timeout", type=float, default=120.0)
    sp.add_argument("--act-timeout", type=float, default=1800.0)
    sp.add_argument("--notify-cmd", default=None, help="要対応ダイジェストを渡す通知コマンド")
    sp.add_argument("--actor", default=os.environ.get("USER", "user"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="kiro-marshal",
        description="backlog/ を優先順位付け・検証・収束させる制御層（Loop Engineering MVP）")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="正準ループ（優先順位付け→実行→検証→積み直し→収束）")
    _add_common(run)
    run.add_argument("--watch", action="store_true",
                     help="終了条件後もプロセスを残し backlog を監視（エージェントは待機しない）")
    run.add_argument("--poll", type=float, default=5.0, help="watch のポーリング間隔（秒）")
    run.add_argument("--no-archive", action="store_true",
                     help="done を archive/ へ退避せず削除する（既定は退避）")
    run.add_argument("--dry-run", action="store_true", help="act を飛ばし verify のみ")
    run.add_argument("--once", action="store_true", help="1 タスクだけ処理して終了")

    for name, helptext in [("triage", "優先順位付けのみ（inbox→ready 昇格・policy 適用）"),
                           ("needs", "人の判断待ち（blocked / need_intake）を表示")]:
        _add_common(sub.add_parser(name, help=helptext))

    ap = sub.add_parser("approve", help="判断待ちを修正承認して積み直し（決定記録）")
    _add_common(ap); ap.add_argument("id"); ap.add_argument("--reason", required=True)
    hd = sub.add_parser("hold", help="policy に deny 追加し保留（決定記録）")
    _add_common(hd); hd.add_argument("id"); hd.add_argument("--reason", required=True)
    rp = sub.add_parser("reprioritize", help="policy に pin/defer 追加（決定記録）")
    _add_common(rp); rp.add_argument("id")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--pin", action="store_true"); g.add_argument("--defer", action="store_true")
    rp.add_argument("--reason", required=True)

    args = p.parse_args(argv)
    cfg = build_config(args)

    if args.cmd in ("triage", "needs") and not cfg.backlog.exists():
        print(f"エラー: バックログディレクトリがありません: {cfg.backlog}", file=sys.stderr)
        return 2

    return {
        "run": lambda: cmd_run(cfg),
        "triage": lambda: cmd_triage(cfg),
        "needs": lambda: cmd_needs(cfg),
        "approve": lambda: cmd_approve(cfg, args.id, args.reason),
        "hold": lambda: cmd_hold(cfg, args.id, args.reason),
        "reprioritize": lambda: cmd_reprioritize(
            cfg, args.id, "pin" if args.pin else "defer", args.reason),
    }[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
