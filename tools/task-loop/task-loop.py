#!/usr/bin/env python3
"""task-loop — Loop Engineering MVP（自律タスク消化ループ）

`queue.md` に並んだタスクを 1 件ずつ拾い、kiro-flow に実行させ、**タスク自身が
持つ verify コマンドをローカルで実行して PASS したものだけを done に確定**する
外側ループ。人間がプロンプトを毎回投げ込まなくても、キューが枯れるか停止条件に
達するまで自律的に回り続ける。

二層構成:
  - kiro-flow   … 実行（分解 → act → 内側 verify ループ）を担う「頭脳」
  - task-loop   … queue.md の状態管理／外側の停止条件／真の verify ゲートを担う

設計上の肝（Loop Engineering の事故を物理的に潰す）:
  1. done は **自己申告では確定しない**。verify コマンドの終了コード 0 だけが根拠。
  2. verify を持たないタスクは done にできない（即 blocked）。
  3. ループは必ず有限回で止まる（枯渇 / max-cycles / 進捗停滞 / blocked 比率 / 時間予算）。

標準ライブラリのみで動作（pip 依存なし）。kiro-cli が無くても
`--executor stub`（kiro-flow の stub）で挙動を確認できる。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VALID_STATUS = ("todo", "doing", "done", "blocked")
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")

# 停止理由（journal とサマリで使う）
REASON_DRAINED = "drained"          # todo が尽きた（実質完了）
REASON_MAX_CYCLES = "max_cycles"    # サイクル上限
REASON_NO_PROGRESS = "no_progress"  # done が N サイクル増えていない
REASON_BLOCKED_RATIO = "blocked_ratio"
REASON_BUDGET = "budget"            # 実時間予算超過


# ---------------------------------------------------------------------------
# queue.md のパース / シリアライズ
# ---------------------------------------------------------------------------
@dataclass
class Task:
    id: str
    title: str
    status: str = "todo"
    verify: str = ""
    retries: int = 0
    # 既知フィールド以外（note 等）を順序を保って保持し、書き戻し時に復元する
    extra: "list[tuple[str, str]]" = field(default_factory=list)

    def normalized_status(self) -> str:
        return self.status if self.status in VALID_STATUS else "todo"


def _strip_code(val: str) -> str:
    """`...` で囲まれた値からバッククォートを外す（verify をそのまま実行できるように）。"""
    v = val.strip()
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        return v[1:-1]
    return v


def parse_queue(text: str) -> "tuple[str, list[Task]]":
    """queue.md をプレアンブル（最初のタスク見出しより前）とタスク列に分解する。"""
    lines = text.splitlines()
    tasks: list[Task] = []
    preamble: list[str] = []
    cur: Task | None = None
    seen_task = False

    for line in lines:
        m = TASK_HEADER_RE.match(line)
        if m:
            seen_task = True
            cur = Task(id=m.group("id").strip(), title=m.group("title").strip())
            tasks.append(cur)
            continue
        if not seen_task:
            preamble.append(line)
            continue
        fm = FIELD_RE.match(line)
        if fm and cur is not None:
            key, val = fm.group("key").strip(), fm.group("val").strip()
            if key == "status":
                cur.status = val or "todo"
            elif key == "verify":
                cur.verify = _strip_code(val)
            elif key == "retries":
                try:
                    cur.retries = int(val)
                except ValueError:
                    cur.retries = 0
            else:
                cur.extra.append((key, val))
        # フィールド以外の行（空行・自由記述）は捨てて正準形に寄せる
    return ("\n".join(preamble).rstrip("\n"), tasks)


def serialize_queue(preamble: str, tasks: "list[Task]") -> str:
    out: list[str] = []
    if preamble.strip():
        out.append(preamble.rstrip("\n"))
        out.append("")
    for t in tasks:
        out.append(f"## {t.id}: {t.title}")
        out.append(f"- status: {t.normalized_status()}")
        verify_disp = f"`{t.verify}`" if t.verify else ""
        out.append(f"- verify: {verify_disp}")
        out.append(f"- retries: {t.retries}")
        for k, v in t.extra:
            out.append(f"- {k}: {v}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def load_queue(path: Path) -> "tuple[str, list[Task]]":
    return parse_queue(path.read_text(encoding="utf-8"))


def save_queue(path: Path, preamble: str, tasks: "list[Task]") -> None:
    path.write_text(serialize_queue(preamble, tasks), encoding="utf-8")


# ---------------------------------------------------------------------------
# verify ゲート（done を確定させる唯一の根拠）
# ---------------------------------------------------------------------------
def run_verify(cmd: str, workdir: Path, timeout: float) -> "tuple[bool, str]":
    """verify コマンドをシェル実行し、終了コード 0 を PASS とみなす。"""
    if not cmd.strip():
        return (False, "verify 未定義（自己申告では done にできない → blocked）")
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(workdir), timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return (False, f"verify タイムアウト（{timeout}s）")
    tail = (proc.stdout or "")[-500:] + (proc.stderr or "")[-500:]
    return (proc.returncode == 0, f"exit={proc.returncode} {tail.strip()}"[:600])


# ---------------------------------------------------------------------------
# act（kiro-flow に実行を委譲）
# ---------------------------------------------------------------------------
def resolve_kiro_flow(explicit: "str | None") -> "list[str]":
    """kiro-flow の起動コマンドを解決する。--kiro-flow > PATH > リポジトリ同梱。"""
    if explicit:
        if explicit.endswith(".py"):
            return [sys.executable, explicit]
        return [explicit]
    found = shutil.which("kiro-flow")
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent / "kiro-flow" / "kiro-flow.py"
    return [sys.executable, str(local)]


def build_request(task: Task) -> str:
    """kiro-flow へ渡す要求文。完了条件（verify）を明示し、loop パターンを促す。"""
    return (
        f"{task.title}\n\n"
        f"このタスクは完了条件を満たすまで反復し、満たしたら終了すること（loop-until-done）。\n"
        f"完了条件: 次のシェルコマンドが終了コード 0 で成功すること:\n"
        f"  {task.verify or '（verify 未定義）'}\n\n"
        f"タスクID: {task.id}"
    )


def act_via_kiro_flow(task: Task, cfg: "Config") -> "tuple[bool, str]":
    """kiro-flow run を同期実行する。非ゼロ終了は act 失敗として扱う。"""
    base = resolve_kiro_flow(cfg.kiro_flow)
    cmd = base + [
        "--bus", str(cfg.bus),
        "run", build_request(task),
        "--planner", cfg.planner,
        "--executor", cfg.executor,
        "--max-iterations", str(cfg.max_iterations),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(cfg.workdir), timeout=cfg.act_timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return (False, f"kiro-flow タイムアウト（{cfg.act_timeout}s）")
    except FileNotFoundError as e:
        return (False, f"kiro-flow を起動できません: {e}")
    return (proc.returncode == 0, (proc.stdout or "")[-300:].strip())


# ---------------------------------------------------------------------------
# ループ本体
# ---------------------------------------------------------------------------
@dataclass
class Config:
    queue: Path
    journal: Path
    workdir: Path
    bus: Path
    kiro_flow: "str | None" = None
    planner: str = "flow-planner"
    executor: str = "kiro"
    max_iterations: int = 3
    max_cycles: int = 20
    max_retries: int = 2
    no_progress: int = 3
    blocked_ratio: float = 0.5
    max_seconds: float = 0.0       # 0 = 無制限
    verify_timeout: float = 120.0
    act_timeout: float = 1800.0
    dry_run: bool = False          # act を飛ばし verify だけで状態を整合させる
    once: bool = False


def progress_count(tasks: "list[Task]") -> int:
    return sum(1 for t in tasks if t.normalized_status() == "done")


def state_hash(tasks: "list[Task]") -> str:
    sig = ";".join(f"{t.id}:{t.normalized_status()}:{t.retries}"
                   for t in sorted(tasks, key=lambda x: x.id))
    return hashlib.sha1(sig.encode()).hexdigest()[:12]


def pick_next(tasks: "list[Task]") -> "Task | None":
    for t in tasks:
        if t.normalized_status() == "todo":
            return t
    return None


def append_journal(path: Path, line: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {ts} {line}\n")


def summarize(tasks: "list[Task]") -> "dict[str, int]":
    c = {s: 0 for s in VALID_STATUS}
    for t in tasks:
        c[t.normalized_status()] = c.get(t.normalized_status(), 0) + 1
    return c


def check_guards(tasks, cycle, cfg, no_progress_streak, start_ts) -> "str | None":
    """ループ継続前に評価する停止条件。停止理由を返す（継続なら None）。"""
    if cycle >= cfg.max_cycles:
        return REASON_MAX_CYCLES
    if cfg.no_progress and no_progress_streak >= cfg.no_progress:
        return REASON_NO_PROGRESS
    total = len(tasks) or 1
    if summarize(tasks)["blocked"] / total >= cfg.blocked_ratio:
        return REASON_BLOCKED_RATIO
    if cfg.max_seconds and (time.time() - start_ts) >= cfg.max_seconds:
        return REASON_BUDGET
    return None


def run_loop(cfg: Config, act=act_via_kiro_flow) -> dict:
    """外側ループを回す。act は差し替え可能（テストで注入）。"""
    cfg.journal.parent.mkdir(parents=True, exist_ok=True)
    preamble, tasks = load_queue(cfg.queue)
    start_ts = time.time()
    cycle = 0
    last_done = progress_count(tasks)
    no_progress_streak = 0
    append_journal(cfg.journal, f"=== task-loop 開始 tasks={len(tasks)} "
                                f"executor={cfg.executor} dry_run={cfg.dry_run} ===")

    while True:
        reason = check_guards(tasks, cycle, cfg, no_progress_streak, start_ts)
        if reason:
            break
        task = pick_next(tasks)
        if task is None:
            reason = REASON_DRAINED
            break

        cycle += 1
        task.status = "doing"
        save_queue(cfg.queue, preamble, tasks)

        # --- act（dry-run では verify のみで状態を整合させる）---
        if cfg.dry_run:
            act_ok, act_msg = True, "(dry-run: act skip)"
        else:
            act_ok, act_msg = act(task, cfg)

        # --- verify ゲート（done 確定の唯一の根拠）---
        ok, vmsg = run_verify(task.verify, cfg.workdir, cfg.verify_timeout)

        if ok:
            task.status = "done"
            append_journal(cfg.journal,
                           f"cycle {cycle}: {task.id} DONE — {vmsg}")
        else:
            task.retries += 1
            if not task.verify:
                # verify が無いタスクは構造的に done 不能 → 即 blocked
                task.status = "blocked"
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} BLOCKED（verify 未定義）")
            elif task.retries > cfg.max_retries:
                task.status = "blocked"
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} BLOCKED "
                               f"(retries={task.retries}) — act:{act_msg} verify:{vmsg}")
            else:
                task.status = "todo"  # 再キュー
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} FAIL retry "
                               f"({task.retries}/{cfg.max_retries}) — verify:{vmsg}")
        save_queue(cfg.queue, preamble, tasks)

        # --- 進捗停滞の検知（done 件数が増えたか）---
        done_now = progress_count(tasks)
        if done_now > last_done:
            last_done = done_now
            no_progress_streak = 0
        else:
            no_progress_streak += 1

        if cfg.once:
            reason = "once"
            break

    counts = summarize(tasks)
    append_journal(cfg.journal,
                   f"=== task-loop 停止 reason={reason} cycles={cycle} "
                   f"done={counts['done']} blocked={counts['blocked']} "
                   f"todo={counts['todo']} ===")
    return {
        "reason": reason,
        "cycles": cycle,
        "counts": counts,
        "tasks": tasks,
        "state_hash": state_hash(tasks),
    }


def exit_code_for(result: dict) -> int:
    """CI 連携用の終了コード。0=完走で blocked 無し / 1=blocked あり / 2=ガード停止。"""
    counts = result["counts"]
    if result["reason"] == REASON_DRAINED and counts["blocked"] == 0:
        return 0
    if counts["blocked"] > 0:
        return 1
    return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_config(args) -> Config:
    workdir = Path(args.workdir).resolve()
    queue = Path(args.queue)
    queue = queue if queue.is_absolute() else (workdir / queue)
    journal = Path(args.journal)
    journal = journal if journal.is_absolute() else (workdir / journal)
    bus = Path(args.bus)
    bus = bus if bus.is_absolute() else (workdir / bus)
    return Config(
        queue=queue, journal=journal, workdir=workdir, bus=bus,
        kiro_flow=args.kiro_flow, planner=args.planner, executor=args.executor,
        max_iterations=args.max_iterations, max_cycles=args.max_cycles,
        max_retries=args.max_retries, no_progress=args.no_progress,
        blocked_ratio=args.blocked_ratio, max_seconds=args.max_seconds,
        verify_timeout=args.verify_timeout, act_timeout=args.act_timeout,
        dry_run=args.dry_run, once=args.once,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="task-loop",
        description="queue.md を verify ゲート付きで自律消化するループ（Loop Engineering MVP）",
    )
    p.add_argument("--queue", default="queue.md", help="キューファイル（既定 queue.md）")
    p.add_argument("--journal", default="journal.md", help="申し送りログ（既定 journal.md）")
    p.add_argument("--workdir", default=".", help="作業ディレクトリ（verify/act の cwd）")
    p.add_argument("--bus", default=".task-loop-bus", help="kiro-flow のバス")
    p.add_argument("--kiro-flow", default=None, help="kiro-flow 実行体（既定: PATH→同梱）")
    p.add_argument("--planner", default="flow-planner",
                   choices=["kiro", "stub", "flow-planner"])
    p.add_argument("--executor", default="kiro", choices=["kiro", "stub"])
    p.add_argument("--max-iterations", type=int, default=3, help="kiro-flow 内側の再計画上限")
    # 停止条件
    p.add_argument("--max-cycles", type=int, default=20, help="外側ループの最大サイクル数")
    p.add_argument("--max-retries", type=int, default=2, help="タスクを blocked にするまでの再試行回数")
    p.add_argument("--no-progress", type=int, default=3, help="done が増えないまま許容するサイクル数")
    p.add_argument("--blocked-ratio", type=float, default=0.5, help="blocked 比率がこれ以上で停止")
    p.add_argument("--max-seconds", type=float, default=0.0, help="実時間予算（0=無制限）")
    p.add_argument("--verify-timeout", type=float, default=120.0)
    p.add_argument("--act-timeout", type=float, default=1800.0)
    p.add_argument("--dry-run", action="store_true",
                   help="act を飛ばし verify だけで状態を整合（既存成果の点検に）")
    p.add_argument("--once", action="store_true", help="1 タスクだけ処理して終了")
    args = p.parse_args(argv)

    cfg = build_config(args)
    if not cfg.queue.exists():
        print(f"エラー: キューが見つかりません: {cfg.queue}", file=sys.stderr)
        return 2

    result = run_loop(cfg)
    counts = result["counts"]
    print("\n=== task-loop 完了 ===")
    print(f"停止理由 : {result['reason']}")
    print(f"サイクル : {result['cycles']}")
    print(f"done={counts['done']} blocked={counts['blocked']} "
          f"todo={counts['todo']} doing={counts['doing']}")
    if counts["blocked"] or counts["todo"]:
        print("\n人間の判断が必要なタスク:")
        for t in result["tasks"]:
            if t.normalized_status() in ("blocked", "todo"):
                print(f"  [{t.normalized_status()}] {t.id}: {t.title}")
    print(f"\n申し送り: {cfg.journal}")
    return exit_code_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
