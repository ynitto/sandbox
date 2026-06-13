#!/usr/bin/env python3
"""kiro-flow — git 共有型・分散 Dynamic Workflow (M1: ローカルバス版)

Claude 風の "動的分解 → ワーカー委譲 → 結果統合" を kiro-cli で実現する基盤。
M1 ではメッセージバスをローカルディレクトリにして、claim プロトコルと
最小ワーカーループの正しさを検証する。バスを git に差し替えれば複数 PC へ
そのまま分散できる（同じ Bus インターフェース）。

通信は「ファイルのみ」。タスクの状態はファイルの存在から導出するため、
ノード間で同じファイルを書き換えることがなく、衝突しない。

  pending : tasks/<id>.json があり、claims/<id>.lock も results/<id>.json も無い
  claimed : claims/<id>.lock がある（result はまだ無い）
  done    : results/<id>.json があり status == "done"
  failed  : results/<id>.json があり status == "failed"

claim は claims/<id>.lock を O_CREAT|O_EXCL で作る＝ファイルシステム原子操作。
最初に作れたワーカーだけが勝者。git バスでは push 拒否を同じ用途に使う。

サブコマンド:
  up          一発で orchestrator + worker(複数) を起動して待機
  orchestrate 計画役: 分解 → タスク投入 → 完了待ち → 統合
  work        ワーカー役: claim → 実行 → result を回す
  status      run の状態表示
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

TERMINAL = {"done", "failed"}


# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(node: str, msg: str) -> None:
    print(f"[{now_iso()}] [{node}] {msg}", flush=True)


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json_atomic(path: str, data) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def extract_json(text: str):
    """LLM 出力から JSON を寛容に取り出す（hermes-kiro-acp の作法）。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opn, cls in (("[", "]"), ("{", "}")):
        i, j = text.find(opn), text.rfind(cls)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("planner 出力から JSON を抽出できませんでした")


# --------------------------------------------------------------------------
# Bus — メッセージバス抽象（M1: ローカルディレクトリ実装）
# --------------------------------------------------------------------------
class Bus:
    def __init__(self, root: str, run_id: str):
        self.run_dir = os.path.join(root, "runs", run_id)
        self.tasks_dir = os.path.join(self.run_dir, "tasks")
        self.claims_dir = os.path.join(self.run_dir, "claims")
        self.results_dir = os.path.join(self.run_dir, "results")
        self.events_dir = os.path.join(self.run_dir, "events")
        self.meta_path = os.path.join(self.run_dir, "meta.json")
        self.graph_path = os.path.join(self.run_dir, "graph.json")
        self.final_path = os.path.join(self.run_dir, "final.json")

    # --- git バス化のための同期フック（ローカルでは no-op） ---
    def sync_pull(self) -> None:
        pass

    def sync_push(self) -> None:
        pass

    # --- セットアップ ---
    def ensure_dirs(self) -> None:
        for d in (self.tasks_dir, self.claims_dir, self.results_dir, self.events_dir):
            os.makedirs(d, exist_ok=True)

    def ensure_run(self, request: str) -> None:
        self.ensure_dirs()
        if read_json(self.meta_path) is None:
            write_json_atomic(self.meta_path, {
                "request": request,
                "status": "planning",
                "created_at": now_iso(),
            })

    # --- メタ / グラフ ---
    def set_status(self, status: str) -> None:
        meta = read_json(self.meta_path) or {}
        meta["status"] = status
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)

    def get_status(self):
        meta = read_json(self.meta_path)
        return meta.get("status") if meta else None

    def write_graph(self, graph) -> None:
        write_json_atomic(self.graph_path, graph)

    def read_graph(self):
        return read_json(self.graph_path)

    # --- タスク ---
    def write_task(self, task) -> None:
        write_json_atomic(os.path.join(self.tasks_dir, f"{task['id']}.json"), task)

    def task_ids(self):
        g = self.read_graph()
        return list(g["nodes"].keys()) if g else []

    # --- claim（原子操作） ---
    def lock_path(self, node_id: str) -> str:
        return os.path.join(self.claims_dir, f"{node_id}.lock")

    def try_claim(self, node_id: str, who: str, lease_sec: int) -> bool:
        path = self.lock_path(node_id)
        payload = json.dumps({
            "who": who,
            "claimed_at": now_iso(),
            "lease_until": time.time() + lease_sec,
        }).encode()
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        return True

    def reclaim_expired(self) -> int:
        """lease 切れの孤児ロックを解放する（orchestrator が単独で呼ぶ想定）。"""
        freed = 0
        now = time.time()
        for name in os.listdir(self.claims_dir):
            if not name.endswith(".lock"):
                continue
            node_id = name[:-len(".lock")]
            if self.has_result(node_id):
                continue
            info = read_json(os.path.join(self.claims_dir, name)) or {}
            if info.get("lease_until", 0) < now:
                try:
                    os.remove(os.path.join(self.claims_dir, name))
                    freed += 1
                except FileNotFoundError:
                    pass
        return freed

    # --- 結果 ---
    def result_path(self, node_id: str) -> str:
        return os.path.join(self.results_dir, f"{node_id}.json")

    def has_result(self, node_id: str) -> bool:
        return os.path.exists(self.result_path(node_id))

    def read_result(self, node_id: str):
        return read_json(self.result_path(node_id))

    def write_result(self, node_id: str, who: str, status: str, output: str) -> None:
        write_json_atomic(self.result_path(node_id), {
            "id": node_id,
            "who": who,
            "status": status,
            "output": output,
            "finished_at": now_iso(),
        })

    # --- 状態導出 ---
    def node_state(self, node_id: str) -> str:
        res = self.read_result(node_id)
        if res:
            return res.get("status", "done")
        if os.path.exists(self.lock_path(node_id)):
            return "claimed"
        if os.path.exists(os.path.join(self.tasks_dir, f"{node_id}.json")):
            return "pending"
        return "unknown"

    def all_terminal(self) -> bool:
        ids = self.task_ids()
        return bool(ids) and all(self.node_state(i) in TERMINAL for i in ids)

    def event(self, who: str, kind: str, **extra) -> None:
        rec = {"ts": now_iso(), "who": who, "kind": kind, **extra}
        with open(os.path.join(self.events_dir, f"{who}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Planner — 動的分解（kiro-cli or stub）
# --------------------------------------------------------------------------
def plan_stub(request: str):
    """kiro-cli 無しでプロトコルを検証するための簡易分解。

    request を ';' か改行で割って独立タスクにする（依存なし＝並列 claim を試せる）。
    1 つしか無ければそのまま 1 タスク。"""
    parts = [p.strip() for p in request.replace("\n", ";").split(";") if p.strip()]
    if not parts:
        parts = [request.strip() or "no-op"]
    return [{"id": f"t{i+1}", "goal": g, "deps": []} for i, g in enumerate(parts)]


def plan_kiro(request: str, model: str | None):
    prompt = (
        "あなたは分散ワークフローの計画役です。次の要求を、互いに独立して実行できる"
        "小さなサブタスクに分解してください。出力は JSON 配列のみ。各要素は "
        '{"id": "t1", "goal": "...", "deps": []} の形式。deps は先行タスクの id 配列。\n\n'
        f"要求: {request}"
    )
    out = run_kiro(prompt, model)
    raw = extract_json(out)
    tasks = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            tasks.append({"id": f"t{i+1}", "goal": item, "deps": []})
        else:
            tasks.append({
                "id": str(item.get("id") or f"t{i+1}"),
                "goal": str(item.get("goal", "")),
                "deps": list(item.get("deps", [])),
            })
    return tasks or plan_stub(request)


# --------------------------------------------------------------------------
# Executor — タスク実行（kiro-cli or stub）
# --------------------------------------------------------------------------
def run_kiro(prompt: str, model: str | None) -> str:
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"kiro-cli 失敗 (rc={proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout.strip()


def execute_stub(goal: str, dep_ctx: str, model: str | None) -> str:
    time.sleep(random.uniform(0.2, 0.6))  # 実行のばらつきを模す
    ctx = f" / 依存結果あり({len(dep_ctx)}字)" if dep_ctx else ""
    return f"[stub] 完了: {goal}{ctx}"


def execute_kiro(goal: str, dep_ctx: str, model: str | None) -> str:
    prompt = (
        "あなたは分散ワークフローのワーカーです。次のタスクだけを完了し、成果物を出力してください。\n"
        f"タスク: {goal}\n"
    )
    if dep_ctx:
        prompt += f"\n依存タスクの成果（参考）:\n{dep_ctx}\n"
    prompt += "\n成果物を簡潔に直接出力してください。"
    return run_kiro(prompt, model)


# --------------------------------------------------------------------------
# orchestrate
# --------------------------------------------------------------------------
def cmd_orchestrate(args) -> int:
    who = args.node_id
    bus = Bus(args.bus, args.run_id)
    bus.ensure_run(args.request)
    log(who, f"run={args.run_id} 計画開始 (planner={args.planner})")

    if args.planner == "kiro":
        tasks = plan_kiro(args.request, args.model)
    else:
        tasks = plan_stub(args.request)

    graph = {"nodes": {t["id"]: {"goal": t["goal"], "deps": t["deps"]} for t in tasks}}
    bus.write_graph(graph)
    for t in tasks:
        bus.write_task(t)
    bus.sync_push()
    bus.set_status("running")
    log(who, f"タスク投入: {[t['id'] for t in tasks]}")
    bus.event(who, "planned", tasks=[t["id"] for t in tasks])

    # 完了待ち
    while not bus.all_terminal():
        bus.sync_pull()
        freed = bus.reclaim_expired()
        if freed:
            log(who, f"孤児ロックを {freed} 件解放")
        time.sleep(args.poll)

    # 統合
    results = {nid: (bus.read_result(nid) or {}) for nid in bus.task_ids()}
    summary = "\n".join(
        f"- {nid} [{r.get('status')}]: {str(r.get('output',''))[:200]}"
        for nid, r in results.items()
    )
    write_json_atomic(bus.final_path, {
        "request": args.request,
        "finished_at": now_iso(),
        "summary": summary,
        "results": results,
    })
    bus.set_status("done")
    bus.sync_push()
    log(who, "全タスク完了。final.json を書き出しました。")
    log(who, "結果サマリ:\n" + summary)
    return 0


# --------------------------------------------------------------------------
# work
# --------------------------------------------------------------------------
def deps_satisfied(bus: Bus, node) -> bool:
    return all(
        (bus.read_result(d) or {}).get("status") == "done"
        for d in node.get("deps", [])
    )


def pick_claimable(bus: Bus):
    graph = bus.read_graph()
    if not graph:
        return None
    items = list(graph["nodes"].items())
    random.shuffle(items)  # ワーカー間の衝突を減らす
    for nid, node in items:
        if bus.node_state(nid) == "pending" and deps_satisfied(bus, node):
            return nid, node
    return None


def cmd_work(args) -> int:
    who = args.node_id
    bus = Bus(args.bus, args.run_id)
    log(who, f"ワーカー起動 (executor={args.executor}, keep_alive={args.keep_alive})")

    while True:
        bus.sync_pull()
        status = bus.get_status()

        candidate = pick_claimable(bus)
        if candidate is None:
            if status in TERMINAL and not args.keep_alive:
                log(who, f"run が {status}。終了します。")
                return 0
            time.sleep(args.poll)
            continue

        nid, node = candidate
        lease = max(args.poll * 10, 60)
        if not bus.try_claim(nid, who, lease):
            continue  # 競り負け
        log(who, f"claim 成功: {nid} — {node['goal'][:60]}")
        bus.event(who, "claimed", node=nid)

        dep_ctx = "\n".join(
            f"[{d}] {(bus.read_result(d) or {}).get('output','')}"
            for d in node.get("deps", [])
        )
        try:
            if args.executor == "kiro":
                output = execute_kiro(node["goal"], dep_ctx, args.model)
            else:
                output = execute_stub(node["goal"], dep_ctx, args.model)
            rstatus = "done"
        except Exception as e:  # noqa: BLE001 — 結果として記録する
            output = f"実行エラー: {e}"
            rstatus = "failed"

        bus.write_result(nid, who, rstatus, output)
        bus.sync_push()
        log(who, f"完了: {nid} [{rstatus}]")
        bus.event(who, "result", node=nid, status=rstatus)


# --------------------------------------------------------------------------
# up — 一発起動
# --------------------------------------------------------------------------
def cmd_up(args) -> int:
    run_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    bus_root = os.path.abspath(args.bus)
    me = self_path()
    base = [sys.executable, me, "--bus", bus_root, "--run-id", run_id]

    procs = []
    orch = subprocess.Popen(base + [
        "orchestrate", "--request", args.request,
        "--planner", args.planner, "--model_opt", args.model or "",
        "--poll", str(args.poll), "--node-id", "orchestrator",
    ])
    procs.append(("orchestrator", orch))

    for i in range(args.workers):
        wid = f"worker-{i+1}"
        w = subprocess.Popen(base + [
            "work", "--node-id", wid, "--executor", args.executor,
            "--model_opt", args.model or "", "--poll", str(args.poll),
        ])
        procs.append((wid, w))

    print(f"\n>>> kiro-flow up: run_id={run_id} bus={bus_root}")
    print(f">>> orchestrator x1 + worker x{args.workers} を起動しました。Ctrl-C で全停止。\n", flush=True)

    bus = Bus(bus_root, run_id)

    def shutdown(*_):
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(130)))

    # run が終端に達するか orchestrator が落ちるまで待機
    try:
        while True:
            if bus.get_status() in TERMINAL:
                print(f"\n>>> run {bus.get_status()}。ワーカーを停止します。", flush=True)
                break
            if orch.poll() is not None and bus.get_status() not in TERMINAL:
                print("\n>>> orchestrator が終了しました。停止します。", flush=True)
                break
            time.sleep(max(args.poll, 1))
    finally:
        shutdown()

    final = read_json(bus.final_path)
    if final:
        print("\n=== 最終結果 ===")
        print(final.get("summary", ""))
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def cmd_status(args) -> int:
    bus = Bus(args.bus, args.run_id)
    print(f"run: {args.run_id}  status: {bus.get_status()}")
    for nid in bus.task_ids():
        print(f"  {nid:<8} {bus.node_state(nid)}")
    return 0


# --------------------------------------------------------------------------
def self_path() -> str:
    return os.path.abspath(__file__)


def main() -> int:
    p = argparse.ArgumentParser(description="kiro-flow — git 共有型・分散 Dynamic Workflow (M1)")
    p.add_argument("--bus", default="./.kiro-flow", help="メッセージバスのルートディレクトリ")
    p.add_argument("--run-id", default=None, help="run 識別子")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="orchestrator + worker(複数) を一発起動して待機")
    up.add_argument("request", help="ワークフローへの要求")
    up.add_argument("--workers", type=int, default=2)
    up.add_argument("--planner", choices=["kiro", "stub"], default="kiro")
    up.add_argument("--executor", choices=["kiro", "stub"], default="kiro")
    up.add_argument("--model", default=None)
    up.add_argument("--poll", type=float, default=2.0)
    up.set_defaults(func=cmd_up)

    orch = sub.add_parser("orchestrate", help="計画役")
    orch.add_argument("--request", required=True)
    orch.add_argument("--planner", choices=["kiro", "stub"], default="kiro")
    orch.add_argument("--node-id", default="orchestrator")
    orch.add_argument("--model_opt", dest="model", default=None)
    orch.add_argument("--poll", type=float, default=2.0)
    orch.set_defaults(func=cmd_orchestrate)

    work = sub.add_parser("work", help="ワーカー役")
    work.add_argument("--node-id", default=f"{socket.gethostname()}-{os.getpid()}")
    work.add_argument("--executor", choices=["kiro", "stub"], default="kiro")
    work.add_argument("--model_opt", dest="model", default=None)
    work.add_argument("--poll", type=float, default=2.0)
    work.add_argument("--keep-alive", action="store_true", help="run 完了後も待機し続ける")
    work.set_defaults(func=cmd_work)

    st = sub.add_parser("status", help="run の状態表示")
    st.set_defaults(func=cmd_status)

    args = p.parse_args()
    # --model は up でだけ name が衝突しないよう調整済み
    if getattr(args, "model", None) == "":
        args.model = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
