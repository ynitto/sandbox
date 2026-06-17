#!/usr/bin/env python3
"""kiro-marshal — Loop Engineering MVP（バックログを捌く制御層）

正準ループ（設計書 docs/designs/2026-06-16-kiro-marshal-mvp-design.md §2）:
  ① backlog.md を読み優先順位をつけ、最優先タスクを kiro-flow に投げる
  ② 優先順位付けは原則 kiro-cli。stub 時は最古優先（FIFO）。人間は policy.md で上書きできる
  ③ kiro-flow の結果を verify ゲートで検証。NG なら backlog に積み直す
  ④ backlog が尽きるか予算（サイクル数/実時間）が尽きるまで反復
  ⑤ ユーザーの判断は DECISIONS.md（決定記録）に保存

二層構成: kiro-flow が実行（act）、kiro-marshal が優先順位付け・検証・収束・決定記録を担う。
標準ライブラリのみ。kiro-cli が無くても --planner stub / --executor stub / --dry-run で動く。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VALID_STATUS = ("inbox", "ready", "doing", "done", "blocked")
CONSUMABLE = ("ready", "todo")  # 実行待ち。todo は ready の後方互換エイリアス
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")
POLICY_RE = re.compile(r"^(?P<key>deny|pin|defer|offload):\s*(?P<val>.+)$")
DR_HEADER_RE = re.compile(r"^##\s+DR-(\d+)\b")

# 停止理由
REASON_DRAINED = "drained"  # 消化可能タスクが尽きた（実質完了）
REASON_BUDGET = "budget"    # 予算（サイクル数/実時間）が尽きた


# ---------------------------------------------------------------------------
# backlog.md のパース / シリアライズ
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


def _strip_code(val: str) -> str:
    v = val.strip()
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        return v[1:-1]
    return v


def parse_backlog(text: str) -> "tuple[str, list[Task]]":
    lines = text.splitlines()
    tasks: list[Task] = []
    preamble: list[str] = []
    cur: Task | None = None
    seen = False
    for line in lines:
        m = TASK_HEADER_RE.match(line)
        if m:
            seen = True
            cur = Task(id=m.group("id").strip(), title=m.group("title").strip())
            tasks.append(cur)
            continue
        if not seen:
            preamble.append(line)
            continue
        fm = FIELD_RE.match(line)
        if fm and cur is not None:
            key, val = fm.group("key").strip(), fm.group("val").strip()
            if key == "status":
                cur.status = val or "ready"
            elif key == "source":
                cur.source = val or "human"
            elif key == "verify":
                cur.verify = _strip_code(val)
            elif key == "retries":
                try:
                    cur.retries = int(val)
                except ValueError:
                    cur.retries = 0
            else:
                cur.extra.append((key, val))
    return ("\n".join(preamble).rstrip("\n"), tasks)


def serialize_backlog(preamble: str, tasks: "list[Task]") -> str:
    out: list[str] = []
    if preamble.strip():
        out.append(preamble.rstrip("\n"))
        out.append("")
    for t in tasks:
        out.append(f"## {t.id}: {t.title}")
        out.append(f"- status: {t.norm_status()}")
        out.append(f"- source: {t.source}")
        out.append(f"- verify: {f'`{t.verify}`' if t.verify else ''}")
        out.append(f"- retries: {t.retries}")
        for k, v in t.extra:
            out.append(f"- {k}: {v}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def load_backlog(path: Path) -> "tuple[str, list[Task]]":
    return parse_backlog(path.read_text(encoding="utf-8"))


def save_backlog(path: Path, preamble: str, tasks: "list[Task]") -> None:
    path.write_text(serialize_backlog(preamble, tasks), encoding="utf-8")


# ---------------------------------------------------------------------------
# policy.md（人間による順位付けの上書き）
# ---------------------------------------------------------------------------
@dataclass
class Policy:
    deny: "list[str]" = field(default_factory=list)
    pin: "list[str]" = field(default_factory=list)
    defer: "list[str]" = field(default_factory=list)
    offload: "list[str]" = field(default_factory=list)  # 分散環境へ移譲する対象


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
    if not path.exists():
        return Policy()
    return parse_policy(path.read_text(encoding="utf-8"))


def append_policy(path: Path, key: str, value: str) -> None:
    """policy.md に1ルール追記（無ければヘッダ付きで作成）。"""
    header = "" if path.exists() else "# kiro-marshal policy（人間による順位付けの上書き）\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}{key}: {value}\n")


# ---------------------------------------------------------------------------
# 決定記録（DECISIONS.md）
# ---------------------------------------------------------------------------
def next_dr_id(path: Path) -> str:
    n = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = DR_HEADER_RE.match(line)
            if m:
                n = max(n, int(m.group(1)))
    return f"DR-{n + 1:04d}"


def append_decision(path: Path, actor: str, context: str, action: str,
                    reason: str, affects: str) -> str:
    dr = next_dr_id(path)
    date = datetime.now().strftime("%Y-%m-%d")
    block = (
        f"## {dr}  {date}  actor: {actor}\n"
        f"- context : {context}\n"
        f"- action  : {action}\n"
        f"- reason  : {reason}\n"
        f"- affects : {affects}\n\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return dr


# ---------------------------------------------------------------------------
# 優先順位付け（正準ループ ①②）
# ---------------------------------------------------------------------------
def consumable_tasks(tasks: "list[Task]") -> "list[Task]":
    return [t for t in tasks if t.consumable()]


def _extract_id_array(text: str) -> "list[str] | None":
    """kiro-cli 出力から JSON 配列（id の優先順）を寛容に抽出する。"""
    start = text.find("[")
    end = text.rfind("]")
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
    """kiro-cli に優先順位を決めさせる。失敗時は None（呼び出し側で最古優先にフォールバック）。"""
    if not ready:
        return []
    listing = "\n".join(f"- {t.id}: {t.title}（source={t.source}）" for t in ready)
    prompt = (
        "あなたはバックログの優先順位付け役。次のタスク群を、重要度・緊急度・依存関係から"
        "優先順位の高い順に並べ替え、**タスクID の JSON 配列だけ**を出力してください"
        "（説明文なし）。\n\nタスク:\n" + listing
    )
    try:
        order_ids = _extract_id_array(kiro_run(prompt, model))
    except Exception:  # noqa: BLE001
        return None
    if not order_ids:
        return None
    by_id = {t.id: t for t in ready}
    ordered = [by_id[i] for i in order_ids if i in by_id]
    # 欠落（エージェントが落としたもの）は元順（最古優先）で末尾に補完
    seen = {t.id for t in ordered}
    ordered += [t for t in ready if t.id not in seen]
    return ordered


def apply_policy_order(ordered: "list[Task]", policy: Policy) -> "list[Task]":
    """pin を先頭・defer を末尾へ。相対順は維持。deny は triage で blocked 化済み。"""
    def hit(t, pats):
        return any(t.matches(p) for p in pats)
    pinned = [t for t in ordered if hit(t, policy.pin)]
    deferred = [t for t in ordered if not hit(t, policy.pin) and hit(t, policy.defer)]
    middle = [t for t in ordered if t not in pinned and t not in deferred]
    return pinned + middle + deferred


def prioritize(tasks: "list[Task]", policy: Policy, planner: str,
               model: "str | None" = None, ranker=None) -> "list[Task]":
    """消化可能タスクを最終順位で返す。先頭が次に実行すべきタスク。"""
    ready = consumable_tasks(tasks)
    if planner == "stub":
        base = list(ready)  # ファイル順 = 最古優先（FIFO）
    else:
        rank = (ranker or rank_agent)(ready, model)
        base = rank if rank is not None else list(ready)
    return apply_policy_order(base, policy)


# ---------------------------------------------------------------------------
# triage（inbox→ready 昇格・policy deny の適用）
# ---------------------------------------------------------------------------
def triage(tasks: "list[Task]", policy: Policy) -> "list[tuple[Task, str]]":
    """状態を整える。新たに人の判断待ち（blocked）へ落ちたタスクと理由を返す。"""
    transitions: list[tuple[Task, str]] = []
    for t in tasks:
        st = t.norm_status()
        if st == "inbox" and t.verify.strip():
            t.status = "ready"  # verify があるなら消化可能へ昇格
            st = "ready"
        if st in CONSUMABLE and any(t.matches(p) for p in policy.deny):
            t.status = "blocked"
            transitions.append((t, "policy:deny（人の判断待ち）"))
    return transitions


# ---------------------------------------------------------------------------
# verify ゲート（done 確定の唯一の根拠）
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


# ---------------------------------------------------------------------------
# act（kiro-flow に実行を委譲）
# ---------------------------------------------------------------------------
def resolve_kiro_flow(explicit: "str | None") -> "list[str]":
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = shutil.which("kiro-flow")
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent / "kiro-flow" / "kiro-flow.py"
    return [sys.executable, str(local)]


def build_request(task: Task) -> str:
    return (
        f"{task.title}\n\n"
        f"このタスクは完了条件を満たすまで反復し、満たしたら終了すること（loop-until-done）。\n"
        f"完了条件: 次のシェルコマンドが終了コード 0 で成功すること:\n"
        f"  {task.verify or '（verify 未定義）'}\n\nタスクID: {task.id}"
    )


def decide_pace(cfg: "Config", cycle_elapsed: float) -> float:
    """次サイクルまでの待機秒数（レーン減速）。

    拡張次元（pace）。`--pace` を1サイクルの下限間隔とし、実時間予算（max_seconds）が
    設定されていれば max_seconds/max_cycles のペースに均してバーストを防ぐ。
    既に間隔ぶん経過していれば待たない。"""
    pace = cfg.pace
    if cfg.max_seconds and cfg.max_cycles:
        target = cfg.max_seconds / cfg.max_cycles  # 1サイクルあたりの目標間隔
        pace = max(pace, target)
    return max(0.0, pace - cycle_elapsed)


def decide_location(task: Task, policy: Policy, cfg: "Config") -> str:
    """act の実行先を決める拡張次元。git バス設定があり offload 規則に当たれば remote。"""
    if cfg.git_bus and any(task.matches(p) for p in policy.offload):
        return "remote"
    return "local"


def build_kiro_flow_cmd(task: Task, cfg: "Config", location: str = "local") -> "list[str]":
    """kiro-flow 起動コマンドを組み立てる。remote なら共有 git バスへ移譲する。"""
    base = resolve_kiro_flow(cfg.kiro_flow) + ["--bus", str(cfg.bus)]
    if location == "remote" and cfg.git_bus:
        base += ["--git", cfg.git_bus, "--git-branch", cfg.git_branch]
        if cfg.git_subdir:
            base += ["--git-subdir", cfg.git_subdir]
    return base + [
        "run", build_request(task),
        "--planner", cfg.planner, "--executor", cfg.executor,
        "--max-iterations", str(cfg.max_iterations),
    ]


def act_via_kiro_flow(task: Task, cfg: "Config", location: str = "local") -> "tuple[bool, str]":
    cmd = build_kiro_flow_cmd(task, cfg, location)
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=cfg.act_timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return (False, f"kiro-flow タイムアウト（{cfg.act_timeout}s）")
    except FileNotFoundError as e:
        return (False, f"kiro-flow を起動できません: {e}")
    return (proc.returncode == 0, (proc.stdout or "")[-300:].strip())


# ---------------------------------------------------------------------------
# 通知（人の判断を要する時だけ push）
# ---------------------------------------------------------------------------
def human_worklist(tasks: "list[Task]") -> "tuple[list[Task], list[Task]]":
    blocked = [t for t in tasks if t.norm_status() == "blocked"]
    intake = [t for t in tasks if t.norm_status() == "inbox" and not t.verify.strip()]
    return blocked, intake


def render_digest(blocked, intake, reasons: dict, budget_stop: bool) -> str:
    lines = ["# 要対応（kiro-marshal）", ""]
    if budget_stop:
        lines.append("⚠ 予算切れで未消化のまま停止しました。")
        lines.append("")
    if blocked:
        lines.append("## 判断待ち（blocked）")
        for t in blocked:
            why = reasons.get(t.id, "検証 NG / 判断不能")
            lines.append(f"- {t.id}: {t.title}\n    なぜ: {why}\n    推奨: 修正して `approve {t.id}`、保留なら `hold {t.id}`")
    if intake:
        lines.append("")
        lines.append("## acceptance 未定義（need_intake）")
        for t in intake:
            lines.append(f"- {t.id}: {t.title}\n    なぜ: verify 未定義\n    推奨: verify を定義して ready 化")
    if not blocked and not intake:
        lines.append("（対応待ちなし）")
    return "\n".join(lines) + "\n"


def notify(cfg: "Config", tasks, reasons: dict, newly_blocked: set, budget_stop: bool) -> bool:
    """状態遷移時だけ通知する（dedup）。送ったら True。"""
    if not newly_blocked and not budget_stop:
        return False
    blocked, intake = human_worklist(tasks)
    digest = render_digest(blocked, intake, reasons, budget_stop)
    cfg.needs.write_text(digest, encoding="utf-8")
    print("\n--- 通知（要対応）---\n" + digest, flush=True)
    if cfg.notify_cmd:
        try:
            subprocess.run(cfg.notify_cmd, shell=True, input=digest, text=True,
                           cwd=str(cfg.workdir), timeout=60)
        except Exception as e:  # noqa: BLE001 — 通知失敗で本体は止めない
            print(f"[warn] notify-cmd 失敗: {e}", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
@dataclass
class Config:
    backlog: Path
    policy: Path
    decisions: Path
    journal: Path
    needs: Path
    workdir: Path
    bus: Path
    git_bus: "str | None" = None   # 分散移譲先（kiro-flow --git）。未設定なら常に local
    git_branch: str = "main"
    git_subdir: "str | None" = None
    kiro_flow: "str | None" = None
    planner: str = "flow-planner"
    executor: str = "kiro"
    model: "str | None" = None
    max_iterations: int = 3
    max_cycles: int = 20         # 予算: サイクル数
    max_seconds: float = 0.0     # 予算: 実時間（0=無制限）
    max_retries: int = 2         # これを超える NG で人の判断へ
    pace: float = 0.0            # 1サイクルあたりの下限間隔（秒）。予算でレーンを減速
    verify_timeout: float = 120.0
    act_timeout: float = 1800.0
    notify_cmd: "str | None" = None
    actor: str = "user"
    archive: "Path | None" = None   # done の退避先（既定 backlog と同じ場所の ARCHIVE.md）
    do_archive: bool = True         # done を backlog から ARCHIVE.md へ退避するか
    dry_run: bool = False
    once: bool = False


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
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {ts} {line}\n")


def append_archive(path: Path, tasks: "list[Task]") -> None:
    """完了タスクを ARCHIVE.md へ append（長期運用で backlog を小さく保つ）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = "" if path.exists() else "# kiro-marshal archive（完了タスク）\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(header)
        for t in tasks:
            f.write(f"## {t.id}: {t.title}\n")
            f.write(f"- archived: {ts}\n")
            f.write(f"- source: {t.source}\n")
            f.write(f"- verify: {f'`{t.verify}`' if t.verify else ''}\n\n")


def run_loop(cfg: Config, act=act_via_kiro_flow, ranker=None, sleeper=time.sleep) -> dict:
    cfg.journal.parent.mkdir(parents=True, exist_ok=True)
    preamble, tasks = load_backlog(cfg.backlog)
    policy = load_policy(cfg.policy)
    reasons: dict[str, str] = {}

    pre_blocked = {t.id for t in tasks if t.norm_status() == "blocked"}
    for t, why in triage(tasks, policy):
        reasons[t.id] = why
    save_backlog(cfg.backlog, preamble, tasks)

    append_journal(cfg.journal, f"=== kiro-marshal 開始 tasks={len(tasks)} "
                                f"planner={cfg.planner} executor={cfg.executor} "
                                f"dry_run={cfg.dry_run} ===")
    start = time.time()
    cycle = 0
    reason = REASON_DRAINED

    while True:
        # 予算（サイクル数 / 実時間）
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
        save_backlog(cfg.backlog, preamble, tasks)

        location = decide_location(task, policy, cfg)
        if location == "remote":
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} を分散環境へ移譲（{cfg.git_bus}）")
        act_msg = "(dry-run: act skip)"
        if not cfg.dry_run:
            _, act_msg = act(task, cfg, location)

        ok, vmsg = run_verify(task.verify, cfg.workdir, cfg.verify_timeout)
        if ok:
            task.status = "done"
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} DONE — {vmsg}")
        else:
            task.retries += 1
            if not task.verify:
                task.status = "blocked"
                reasons[task.id] = "verify 未定義"
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（verify 未定義）")
            elif task.retries > cfg.max_retries:
                task.status = "blocked"
                reasons[task.id] = f"繰り返し NG（retries={task.retries}）: {vmsg}"
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（繰り返し NG）")
            else:
                task.status = "ready"  # backlog に積み直す
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} NG 積み直し "
                                            f"({task.retries}/{cfg.max_retries}) — {vmsg}")
        save_backlog(cfg.backlog, preamble, tasks)

        if cfg.once:
            reason = "once"
            break

        delay = decide_pace(cfg, time.time() - cycle_start)  # レーン減速（予算で均す）
        if delay > 0:
            sleeper(delay)

    counts = summarize(tasks)  # done を含む（この run の成果。アーカイブ前に確定）
    newly_blocked = {t.id for t in tasks if t.norm_status() == "blocked"} - pre_blocked
    budget_stop = reason == REASON_BUDGET
    notified = notify(cfg, tasks, reasons, newly_blocked, budget_stop)

    # done の自動アーカイブ: backlog から退避して live を小さく保つ
    archived = 0
    if cfg.do_archive:
        done_tasks = [t for t in tasks if t.norm_status() == "done"]
        if done_tasks:
            archive_path = cfg.archive or (cfg.backlog.parent / "ARCHIVE.md")
            append_archive(archive_path, done_tasks)
            remaining = [t for t in tasks if t.norm_status() != "done"]
            save_backlog(cfg.backlog, preamble, remaining)
            archived = len(done_tasks)

    append_journal(cfg.journal, f"=== kiro-marshal 停止 reason={reason} cycles={cycle} "
                                f"done={counts['done']} blocked={counts['blocked']} "
                                f"archived={archived} notified={notified} ===")
    return {"reason": reason, "cycles": cycle, "counts": counts, "tasks": tasks,
            "reasons": reasons, "newly_blocked": newly_blocked, "notified": notified,
            "archived": archived}


def exit_code_for(result: dict) -> int:
    """0=drained で判断待ち無し / 1=判断待ちあり / 2=予算停止。"""
    counts = result["counts"]
    if counts["blocked"] > 0:
        return 1
    if result["reason"] == REASON_DRAINED:
        return 0
    return 2


# ---------------------------------------------------------------------------
# 人の操作コマンド（いずれも決定記録を残す）
# ---------------------------------------------------------------------------
def find_task(tasks, tid: str) -> "Task | None":
    return next((t for t in tasks if t.id == tid), None)


def cmd_approve(cfg: Config, tid: str, reason: str) -> int:
    preamble, tasks = load_backlog(cfg.backlog)
    t = find_task(tasks, tid)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    t.status = "ready"  # 修正承認して積み直し
    save_backlog(cfg.backlog, preamble, tasks)
    dr = append_decision(cfg.decisions, cfg.actor,
                         context=f"{tid} を人の判断から復帰", action="approve-and-fix",
                         reason=reason, affects=f"{tid} → ready")
    print(f"{dr}: {tid} を ready に積み直しました。")
    return 0


def cmd_hold(cfg: Config, tid: str, reason: str) -> int:
    preamble, tasks = load_backlog(cfg.backlog)
    t = find_task(tasks, tid)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    append_policy(cfg.policy, "deny", tid)
    t.status = "blocked"
    save_backlog(cfg.backlog, preamble, tasks)
    dr = append_decision(cfg.decisions, cfg.actor,
                         context=f"{tid} を保留（denylist 化）", action="hold(deny)",
                         reason=reason, affects=f"{tid} → blocked, policy.deny += {tid}")
    print(f"{dr}: {tid} を hold（policy.deny 追加）しました。")
    return 0


def cmd_reprioritize(cfg: Config, tid: str, kind: str, reason: str) -> int:
    if kind not in ("pin", "defer"):
        print("エラー: --pin か --defer を指定してください", file=sys.stderr)
        return 2
    append_policy(cfg.policy, kind, tid)
    dr = append_decision(cfg.decisions, cfg.actor,
                         context=f"{tid} の優先度を変更", action=f"reprioritize({kind})",
                         reason=reason, affects=f"policy.{kind} += {tid}")
    print(f"{dr}: {tid} を {kind}（policy.{kind} 追加）しました。")
    return 0


def cmd_needs(cfg: Config) -> int:
    _, tasks = load_backlog(cfg.backlog)
    blocked, intake = human_worklist(tasks)
    print(render_digest(blocked, intake, {}, budget_stop=False))
    return 1 if blocked else 0


def cmd_triage(cfg: Config) -> int:
    preamble, tasks = load_backlog(cfg.backlog)
    policy = load_policy(cfg.policy)
    triage(tasks, policy)
    save_backlog(cfg.backlog, preamble, tasks)
    order = prioritize(tasks, policy, cfg.planner, cfg.model)
    print("優先順位（消化対象）:")
    for i, t in enumerate(order, 1):
        print(f"  {i}. {t.id}: {t.title}")
    return 0


def cmd_run(cfg: Config) -> int:
    result = run_loop(cfg)
    counts = result["counts"]
    print("\n=== kiro-marshal 完了 ===")
    print(f"停止理由 : {result['reason']}")
    print(f"サイクル : {result['cycles']}")
    print(f"done={counts['done']} blocked={counts['blocked']} ready={counts['ready']} "
          f"inbox={counts['inbox']} archived={result.get('archived', 0)}")
    return exit_code_for(result)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_config(args) -> Config:
    workdir = Path(args.workdir).resolve()

    def rel(p, default):
        p = Path(getattr(args, p, None) or default)
        return p if p.is_absolute() else (workdir / p)

    return Config(
        backlog=rel("backlog", "backlog.md"),
        policy=rel("policy", "policy.md"),
        decisions=rel("decisions", "DECISIONS.md"),
        journal=rel("journal", "journal.md"),
        needs=rel("needs", "NEEDS_YOU.md"),
        workdir=workdir,
        bus=rel("bus", ".kiro-marshal-bus"),
        git_bus=args.git_bus, git_branch=args.git_branch, git_subdir=args.git_subdir,
        kiro_flow=args.kiro_flow, planner=args.planner, executor=args.executor,
        model=args.model, max_iterations=args.max_iterations,
        max_cycles=args.max_cycles, max_seconds=args.max_seconds,
        max_retries=args.max_retries, pace=args.pace, verify_timeout=args.verify_timeout,
        act_timeout=args.act_timeout, notify_cmd=args.notify_cmd,
        actor=args.actor, archive=rel("archive", "ARCHIVE.md"),
        do_archive=not getattr(args, "no_archive", False),
        dry_run=getattr(args, "dry_run", False),
        once=getattr(args, "once", False),
    )


def _add_common(sp):
    sp.add_argument("--backlog", default="backlog.md")
    sp.add_argument("--policy", default="policy.md")
    sp.add_argument("--decisions", default="DECISIONS.md")
    sp.add_argument("--journal", default="journal.md")
    sp.add_argument("--needs", default="NEEDS_YOU.md")
    sp.add_argument("--archive", default="ARCHIVE.md")
    sp.add_argument("--workdir", default=".")
    sp.add_argument("--bus", default=".kiro-marshal-bus")
    sp.add_argument("--git-bus", default=None,
                    help="分散移譲先の共有 git リポジトリ（policy の offload 対象を remote 実行）")
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
    sp.add_argument("--pace", type=float, default=0.0,
                    help="1サイクルの下限間隔（秒）。予算でレーンを減速（バースト防止）")
    sp.add_argument("--verify-timeout", type=float, default=120.0)
    sp.add_argument("--act-timeout", type=float, default=1800.0)
    sp.add_argument("--notify-cmd", default=None, help="要対応ダイジェストを渡す通知コマンド")
    sp.add_argument("--actor", default=os.environ.get("USER", "user"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="kiro-marshal",
        description="backlog.md を優先順位付け・検証・収束させる制御層（Loop Engineering MVP）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="正準ループ（優先順位付け→実行→検証→積み直し→収束・通知）")
    _add_common(run)
    run.add_argument("--no-archive", action="store_true",
                     help="done を backlog に残す（既定は ARCHIVE.md へ退避）")
    run.add_argument("--dry-run", action="store_true", help="act を飛ばし verify のみ")
    run.add_argument("--once", action="store_true", help="1 タスクだけ処理して終了")

    tr = sub.add_parser("triage", help="優先順位付けのみ（inbox→ready 昇格・policy 適用）")
    _add_common(tr)

    nd = sub.add_parser("needs", help="人の判断待ち（blocked / need_intake）を表示")
    _add_common(nd)

    ap = sub.add_parser("approve", help="判断待ちを修正承認して積み直し（決定記録）")
    _add_common(ap)
    ap.add_argument("id")
    ap.add_argument("--reason", required=True)

    hd = sub.add_parser("hold", help="policy に deny 追加し保留（決定記録）")
    _add_common(hd)
    hd.add_argument("id")
    hd.add_argument("--reason", required=True)

    rp = sub.add_parser("reprioritize", help="policy に pin/defer 追加（決定記録）")
    _add_common(rp)
    rp.add_argument("id")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--pin", action="store_true")
    g.add_argument("--defer", action="store_true")
    rp.add_argument("--reason", required=True)

    args = p.parse_args(argv)
    cfg = build_config(args)

    if args.cmd in ("run", "triage", "needs") and not cfg.backlog.exists():
        print(f"エラー: バックログが見つかりません: {cfg.backlog}", file=sys.stderr)
        return 2

    if args.cmd == "run":
        return cmd_run(cfg)
    if args.cmd == "triage":
        return cmd_triage(cfg)
    if args.cmd == "needs":
        return cmd_needs(cfg)
    if args.cmd == "approve":
        return cmd_approve(cfg, args.id, args.reason)
    if args.cmd == "hold":
        return cmd_hold(cfg, args.id, args.reason)
    if args.cmd == "reprioritize":
        return cmd_reprioritize(cfg, args.id, "pin" if args.pin else "defer", args.reason)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
