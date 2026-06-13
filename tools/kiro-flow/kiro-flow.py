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
import shutil
import signal
import socket
import subprocess
import sys
import threading
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
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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
        self.root = root
        self.runs_root = os.path.join(root, "runs")
        self.inbox_dir = os.path.join(root, "inbox")
        self.inbox_claims_dir = os.path.join(root, "inbox", "claims")
        self.run_dir = os.path.join(root, "runs", run_id)
        self.tasks_dir = os.path.join(self.run_dir, "tasks")
        self.claims_dir = os.path.join(self.run_dir, "claims")
        self.results_dir = os.path.join(self.run_dir, "results")
        self.events_dir = os.path.join(self.run_dir, "events")
        self.meta_path = os.path.join(self.run_dir, "meta.json")
        self.graph_path = os.path.join(self.run_dir, "graph.json")
        self.final_path = os.path.join(self.run_dir, "final.json")

    # --- 転送フック（ローカルバスでは no-op、GitBus が上書き） ---
    def sync_pull(self) -> None:
        pass

    def sync_push(self, msg: str = "") -> None:
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

    # --- claim（名前空間付き claim ＋ 決定的タイブレーク） ---
    #
    # 各クレーマは自分専用のファイル <claim_dir>/<who>.json を書く（ファイル名が
    # 衝突しないので git で add/add コンフリクトにならない）。勝者は全 claim のうち
    # lease 内で「(ts, who) が最小」の 1 件に決定的に定まる。ローカル/ git どちらの
    # 転送でも同じロジックで唯一の勝者が決まる。タスクにも要求にも同じ仕組みを使う。
    def _claim_dir(self, node_id: str) -> str:
        return os.path.join(self.claims_dir, node_id)

    def _list_claims_in(self, claim_dir: str):
        out = {}
        if os.path.isdir(claim_dir):
            for name in os.listdir(claim_dir):
                if name.endswith(".json"):
                    info = read_json(os.path.join(claim_dir, name))
                    if info:
                        out[name[:-5]] = info
        return out

    def _winner_in(self, claim_dir: str):
        """lease 内の claim から決定的に勝者を選ぶ。無ければ None。"""
        now = time.time()
        live = [
            (info.get("ts", 0.0), who)
            for who, info in self._list_claims_in(claim_dir).items()
            if info.get("lease_until", 0) >= now
        ]
        return min(live)[1] if live else None

    def _write_claim_in(self, claim_dir: str, who: str, lease_sec: float) -> None:
        os.makedirs(claim_dir, exist_ok=True)
        write_json_atomic(os.path.join(claim_dir, f"{who}.json"), {
            "who": who,
            "ts": time.time(),
            "claimed_at": now_iso(),
            "lease_until": time.time() + lease_sec,
        })

    def _try_claim_in(self, claim_dir: str, who: str, lease_sec: float, msg: str) -> bool:
        w = self._winner_in(claim_dir)
        if w is not None and w != who:
            return False  # 既に他者が勝者（lease 内）
        self._write_claim_in(claim_dir, who, lease_sec)
        self.sync_push(msg)
        self.sync_pull()  # 他ノードの claim を取り込んでから勝敗判定
        return self._winner_in(claim_dir) == who

    # 後方互換のためのノード単位ラッパ
    def _winner(self, node_id: str):
        return self._winner_in(self._claim_dir(node_id))

    def _write_claim(self, node_id: str, who: str, lease_sec: float) -> None:
        self._write_claim_in(self._claim_dir(node_id), who, lease_sec)

    def try_claim(self, node_id: str, who: str, lease_sec: float) -> bool:
        self.sync_pull()
        if self.has_result(node_id):
            return False
        return self._try_claim_in(self._claim_dir(node_id), who, lease_sec,
                                  f"claim {node_id} by {who}")

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
        if self._winner(node_id) is not None:
            return "claimed"
        if os.path.exists(os.path.join(self.tasks_dir, f"{node_id}.json")):
            return "pending"
        return "unknown"

    def all_terminal(self) -> bool:
        ids = self.task_ids()
        return bool(ids) and all(self.node_state(i) in TERMINAL for i in ids)

    def event(self, who: str, kind: str, **extra) -> None:
        rec = {"ts": now_iso(), "who": who, "kind": kind, **extra}
        os.makedirs(self.events_dir, exist_ok=True)
        with open(os.path.join(self.events_dir, f"{who}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def recent_events(self, limit: int):
        evs = []
        if os.path.isdir(self.events_dir):
            for name in os.listdir(self.events_dir):
                with open(os.path.join(self.events_dir, name), encoding="utf-8") as f:
                    for line in f:
                        try:
                            evs.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return sorted(evs, key=lambda e: e.get("ts", ""))[-limit:]

    # --- run 管理（gc / watch 用） ---
    def list_runs(self):
        if not os.path.isdir(self.runs_root):
            return []
        return sorted(d for d in os.listdir(self.runs_root)
                      if os.path.isdir(os.path.join(self.runs_root, d)))

    def run_meta(self, run_id: str):
        return read_json(os.path.join(self.runs_root, run_id, "meta.json")) or {}

    def remove_run(self, run_id: str) -> None:
        shutil.rmtree(os.path.join(self.runs_root, run_id), ignore_errors=True)

    def run_view(self, run_id: str) -> "Bus":
        """同じ作業ツリー上の別 run を読み取るための軽量ビュー（git 再クローンしない）。"""
        return Bus(self.root, run_id)

    def active_runs(self):
        """planning/running な run の id 一覧（終端した run は除く）。"""
        out = []
        for rid in self.list_runs():
            st = self.run_meta(rid).get("status")
            if st and st not in TERMINAL:
                out.append(rid)
        return out

    def run_claimable_count(self, run_id: str) -> int:
        """その run で今すぐ claim 可能（pending かつ依存充足）なタスク数。"""
        v = self.run_view(run_id)
        graph = v.read_graph()
        if not graph:
            return 0
        return sum(1 for nid, node in graph["nodes"].items()
                   if v.node_state(nid) == "pending" and deps_satisfied(v, node))

    # --- inbox（要求キュー）と要求 claim ---
    def submit_request(self, req_id: str, request: str, submitter: str) -> None:
        write_json_atomic(os.path.join(self.inbox_dir, f"{req_id}.json"), {
            "id": req_id,
            "request": request,
            "submitter": submitter,
            "submitted_at": now_iso(),
        })

    def list_inbox(self):
        if not os.path.isdir(self.inbox_dir):
            return []
        return sorted(f[:-5] for f in os.listdir(self.inbox_dir) if f.endswith(".json"))

    def read_inbox(self, req_id: str):
        return read_json(os.path.join(self.inbox_dir, f"{req_id}.json"))

    def run_exists(self, run_id: str) -> bool:
        return os.path.exists(os.path.join(self.runs_root, run_id, "meta.json"))

    def claim_request(self, req_id: str, who: str, lease_sec: float) -> bool:
        """どのデーモンがこの要求を orchestrate するかを 1 台に決める。"""
        self.sync_pull()
        if self.run_exists(req_id):
            return False  # 既に誰かが run を作って処理開始済み
        return self._try_claim_in(os.path.join(self.inbox_claims_dir, req_id),
                                  who, lease_sec, f"claim request {req_id} by {who}")


# --------------------------------------------------------------------------
# GitBus — git 共有リポジトリをバスにする（複数 PC 分散）
# --------------------------------------------------------------------------
class GitBus(Bus):
    """共有 git リポジトリをメッセージバスにする転送実装。

    各ノードは自分専用のクローン（root）で作業し、push/pull で同期する。
    書き込みはノードごとに名前空間化されている（claims/<node>/<who>.json、
    results/<node>.json は勝者のみ、meta/graph/tasks は orchestrator のみ）ため、
    rebase はほぼ disjoint なファイルの取り込みで済みコンフリクトしない。
    push 競合は pull --rebase → 再 push のリトライで吸収する。"""

    def __init__(self, root: str, run_id: str, remote: str, branch: str = "main"):
        super().__init__(root, run_id)
        self.remote = remote
        self.branch = branch
        self._ensure_clone()

    def _git(self, args, check=True):
        p = subprocess.run(["git", "-C", self.root] + args, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {p.stderr.strip()[:300]}")
        return p

    def _ensure_clone(self) -> None:
        if not os.path.isdir(os.path.join(self.root, ".git")):
            os.makedirs(os.path.dirname(self.root) or ".", exist_ok=True)
            r = subprocess.run(["git", "clone", self.remote, self.root],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"git clone 失敗: {r.stderr.strip()[:300]}")
        # コミット用 ID（未設定環境向けのフォールバック）
        if not self._git(["config", "user.email"], check=False).stdout.strip():
            self._git(["config", "user.email", "kiro-flow@local"])
            self._git(["config", "user.name", "kiro-flow"])
        # 対象ブランチへ。無ければ作成（空リポジトリ初回も含む）
        if self._git(["checkout", self.branch], check=False).returncode != 0:
            self._git(["checkout", "-B", self.branch])

    def _retry(self, args, attempts=4):
        delay = 2
        for i in range(attempts):
            if self._git(args, check=False).returncode == 0:
                return True
            if i < attempts - 1:
                time.sleep(delay)
                delay *= 2
        return False

    def sync_pull(self) -> None:
        # リモートに当該ブランチが無い初回などは黙って無視
        self._git(["pull", "--rebase", "origin", self.branch], check=False)

    def sync_push(self, msg: str = "kiro-flow update") -> None:
        self._git(["add", "-A"])
        if self._git(["commit", "-m", msg], check=False).returncode != 0:
            # コミット対象が無ければ push 試行のみ（初回の追従用）
            pass
        for i in range(5):
            if self._git(["push", "-u", "origin", self.branch], check=False).returncode == 0:
                return
            # 競合 → 取り込んで再試行（disjoint なので基本コンフリクトしない）
            self._git(["pull", "--rebase", "origin", self.branch], check=False)
            time.sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"git push が {self.branch} へ反映できませんでした")

    def remove_run(self, run_id: str) -> None:
        self._git(["rm", "-r", "-q", "--ignore-unmatch", f"runs/{run_id}"], check=False)
        super().remove_run(run_id)  # 未追跡の残骸も掃除（commit/push は呼び出し側）


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def make_bus(args, node_id: str) -> Bus:
    """--git があれば GitBus（ノードごとに専用クローン）、無ければローカル Bus。"""
    run_id = args.run_id or "_"  # gc 等 run 横断コマンドでは run_id 不要
    if getattr(args, "git", None):
        clone_dir = os.path.join(os.path.abspath(args.bus), _safe(node_id))
        return GitBus(clone_dir, run_id, remote=args.git, branch=args.git_branch)
    return Bus(os.path.abspath(args.bus), run_id)


# --------------------------------------------------------------------------
# Heartbeat — 長時間タスク実行中に claim の lease を更新し続ける
# --------------------------------------------------------------------------
class Heartbeat(threading.Thread):
    """実行中のワーカーが claim を握り続けるための心拍。

    lease の 1/3 間隔で claims/<node>/<who>.json の lease_until を延長し push する。
    これがないと、実行が lease を超えた瞬間に他ノードへ再 claim され二重実行になりうる。"""

    def __init__(self, bus: Bus, node_id: str, who: str, lease: float):
        super().__init__(daemon=True)
        self.bus, self.node_id, self.who, self.lease = bus, node_id, who, lease
        self._stopped = threading.Event()

    def run(self) -> None:
        interval = max(2.0, self.lease / 3.0)
        while not self._stopped.wait(interval):
            try:
                self.bus._write_claim(self.node_id, self.who, self.lease)
                self.bus.sync_push(f"heartbeat {self.node_id} by {self.who}")
            except Exception:  # noqa: BLE001 — 心拍失敗は実行を止めない
                pass

    def stop(self) -> None:
        self._stopped.set()
        self.join(timeout=5)


# --------------------------------------------------------------------------
# Planner — 動的分解（kiro-cli or stub）
# --------------------------------------------------------------------------
def plan_stub(request: str):
    """kiro-cli 無しでプロトコルを検証するための簡易分解。

    区切り記号で依存関係も表現できる:
      ';' / 改行 … 独立（並列）タスクの境界
      '->'        … 逐次依存チェーン（各タスクが直前のタスクに依存）
    例: "setup -> build -> test; write docs"
        → t1=setup, t2=build(deps t1), t3=test(deps t2), t4=write docs(deps なし)"""
    segments = [s.strip() for s in request.replace("\n", ";").split(";") if s.strip()]
    if not segments:
        segments = [request.strip() or "no-op"]
    tasks = []
    idx = 0
    for seg in segments:
        chain = [c.strip() for c in seg.split("->") if c.strip()]
        prev = None
        for goal in chain:
            idx += 1
            tid = f"t{idx}"
            tasks.append({"id": tid, "goal": goal, "deps": [prev] if prev else []})
            prev = tid
    return tasks


def plan_kiro(request: str, model: str | None):
    prompt = (
        "あなたは分散ワークフローの計画役です。次の要求を、小さなサブタスクに分解してください。"
        "互いに独立なタスクは並列実行できるよう deps を空にし、順序が必要なタスクは deps に"
        "先行タスクの id を入れてください（依存を正しく抽出することが重要）。"
        "出力は JSON 配列のみ。各要素は "
        '{"id": "t1", "goal": "...", "deps": []} の形式。\n\n'
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
    # 再計画ループ検証用: ゴールに "FAIL" を含むと失敗する（再計画で retry される）
    if "FAIL" in goal:
        raise RuntimeError(f"[stub] 意図的失敗: {goal}")
    ctx = f" / 依存結果あり({len(dep_ctx)}字)" if dep_ctx else ""
    return f"[stub] 完了: {goal}{ctx}"


# --------------------------------------------------------------------------
# Evaluator — 結果を評価して done / replan を決める（evaluator-optimizer）
# --------------------------------------------------------------------------
def evaluate_stub(request: str, goals: dict, results: dict, iteration: int):
    """失敗ノードがあれば retry タスクを 1 回だけ追加して replan。無ければ done。"""
    new_tasks = []
    for nid, r in results.items():
        if r.get("status") == "failed":
            rid = f"{nid}r"
            if rid in goals:  # 既に retry 済み → 無限ループ防止
                continue
            fixed = goals.get(nid, "").replace("FAIL", "ok")
            new_tasks.append({"id": rid, "goal": f"[retry] {fixed}", "deps": []})
    if new_tasks:
        return "replan", new_tasks, f"{len(new_tasks)} 件の失敗を retry"
    return "done", [], "全タスク成功"


def evaluate_kiro(request: str, goals: dict, results: dict, iteration: int):
    summary = "\n".join(
        f"- {nid} [{r.get('status')}]: {str(r.get('output',''))[:200]}"
        for nid, r in results.items()
    )
    prompt = (
        "あなたは分散ワークフローの評価役です。元の要求に対して、現在の結果が十分か判定し、"
        "不足があれば追加すべきタスクを出してください。出力は JSON オブジェクトのみ:\n"
        '{"decision": "done"|"replan", "reason": "...", '
        '"new_tasks": [{"id": "...", "goal": "...", "deps": []}]}\n'
        "done のときは new_tasks を空配列にしてください。既存の id とは重複しない id を使うこと。\n\n"
        f"元の要求: {request}\n\n現在の結果:\n{summary}"
    )
    try:
        data = extract_json(run_kiro(prompt, None))
    except Exception:  # noqa: BLE001 — 評価に失敗したら done 扱い（暴走防止）
        return "done", [], "評価出力を解釈できず done 扱い"
    decision = data.get("decision", "done")
    new_tasks = []
    for t in data.get("new_tasks", []) or []:
        if isinstance(t, dict) and t.get("id") and t.get("id") not in goals:
            new_tasks.append({
                "id": str(t["id"]),
                "goal": str(t.get("goal", "")),
                "deps": list(t.get("deps", [])),
            })
    if decision == "replan" and new_tasks:
        return "replan", new_tasks, str(data.get("reason", ""))
    return "done", [], str(data.get("reason", "done"))


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
def _plan(args):
    return plan_kiro(args.request, args.model) if args.planner == "kiro" else plan_stub(args.request)


def _evaluate(args, request, goals, results, iteration):
    if args.executor == "kiro":
        return evaluate_kiro(request, goals, results, iteration)
    return evaluate_stub(request, goals, results, iteration)


def cmd_orchestrate(args) -> int:
    who = args.node_id
    bus = make_bus(args, who)
    bus.sync_pull()
    bus.ensure_run(args.request)
    graph = bus.read_graph()

    # 既存グラフがあれば計画をやり直さず再開（resume）
    if graph and graph.get("nodes"):
        iteration = graph.get("iteration", 0)
        log(who, f"run={args.run_id} 再開（既存 {len(graph['nodes'])} ノード, iteration={iteration}）")
        if not bus.all_terminal():
            bus.set_status("running")
            bus.sync_push(f"resume run {args.run_id}")
    else:
        tasks = _plan(args)
        graph = {"nodes": {t["id"]: {"goal": t["goal"], "deps": t["deps"]} for t in tasks},
                 "iteration": 0}
        bus.write_graph(graph)
        for t in tasks:
            bus.write_task(t)
        bus.set_status("running")
        bus.event(who, "planned", tasks=[t["id"] for t in tasks])
        bus.sync_push(f"plan run {args.run_id}: {[t['id'] for t in tasks]}")
        log(who, f"計画 (planner={args.planner}) → タスク投入: {[t['id'] for t in tasks]}")
        iteration = 0

    # evaluator-optimizer ループ: 完了 → 評価 → (replan ならタスク追加して継続)
    while True:
        while not bus.all_terminal():
            bus.sync_pull()
            time.sleep(args.poll)
        bus.sync_pull()

        graph = bus.read_graph()
        goals = {nid: n.get("goal", "") for nid, n in graph["nodes"].items()}
        results = {nid: (bus.read_result(nid) or {}) for nid in graph["nodes"]}

        if iteration >= args.max_iterations:
            decision, new_tasks, reason = "done", [], f"max-iterations({args.max_iterations}) 到達"
        else:
            decision, new_tasks, reason = _evaluate(args, args.request, goals, results, iteration)
        log(who, f"評価 #{iteration}: {decision} — {reason}")

        if decision == "replan" and new_tasks:
            iteration += 1
            for t in new_tasks:
                graph["nodes"][t["id"]] = {"goal": t["goal"], "deps": t["deps"]}
                bus.write_task(t)
            graph["iteration"] = iteration
            bus.write_graph(graph)
            bus.set_status("running")
            bus.event(who, "replan", iteration=iteration, added=[t["id"] for t in new_tasks])
            bus.sync_push(f"replan #{iteration} run {args.run_id}: +{[t['id'] for t in new_tasks]}")
            log(who, f"再計画 #{iteration}: 追加タスク {[t['id'] for t in new_tasks]}")
            continue
        break

    # 統合
    results = {nid: (bus.read_result(nid) or {}) for nid in bus.task_ids()}
    summary = "\n".join(
        f"- {nid} [{r.get('status')}]: {str(r.get('output',''))[:200]}"
        for nid, r in results.items()
    )
    write_json_atomic(bus.final_path, {
        "request": args.request,
        "finished_at": now_iso(),
        "iterations": iteration,
        "summary": summary,
        "results": results,
    })
    bus.set_status("done")
    bus.sync_push(f"finalize run {args.run_id}")
    log(who, f"完了（iteration={iteration}）。final.json を書き出しました。")
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
    bus = make_bus(args, who)
    idle_exit = getattr(args, "idle_exit", False)
    log(who, f"ワーカー起動 (executor={args.executor}, keep_alive={args.keep_alive}, "
             f"idle_exit={idle_exit})")
    time.sleep(random.uniform(0, args.poll))  # 負荷分散: 起動位相をずらす

    idle_polls = 0
    while True:
        bus.sync_pull()
        status = bus.get_status()

        candidate = pick_claimable(bus)
        if candidate is None:
            if status in TERMINAL and not args.keep_alive:
                log(who, f"run が {status}。終了します。")
                return 0
            # デーモン起動の短命ワーカー: 仕事が無くなったら少し待って終了（オンデマンド）
            if idle_exit and status not in (None,) and not args.keep_alive:
                idle_polls += 1
                if idle_polls >= 2:
                    log(who, "claim 可能タスクが無いため終了します（idle-exit）。")
                    return 0
            time.sleep(args.poll)
            continue

        idle_polls = 0
        nid, node = candidate
        if not bus.try_claim(nid, who, args.lease):
            continue  # 競り負け
        log(who, f"claim 成功: {nid} — {node['goal'][:60]}")
        bus.event(who, "claimed", node=nid)

        dep_ctx = "\n".join(
            f"[{d}] {(bus.read_result(d) or {}).get('output','')}"
            for d in node.get("deps", [])
        )
        # 実行中は心拍で lease を延長し続け、長時間タスクでも再 claim されないようにする
        hb = Heartbeat(bus, nid, who, args.lease)
        hb.start()
        try:
            if args.executor == "kiro":
                output = execute_kiro(node["goal"], dep_ctx, args.model)
            else:
                output = execute_stub(node["goal"], dep_ctx, args.model)
            rstatus = "done"
        except Exception as e:  # noqa: BLE001 — 結果として記録する
            output = f"実行エラー: {e}"
            rstatus = "failed"
        finally:
            hb.stop()

        bus.write_result(nid, who, rstatus, output)
        bus.event(who, "result", node=nid, status=rstatus)
        bus.sync_push(f"result {nid} [{rstatus}] by {who}")
        log(who, f"完了: {nid} [{rstatus}]")
        time.sleep(random.uniform(0, 0.3))  # 負荷分散: 他ノードに claim の機会を渡す


# --------------------------------------------------------------------------
# run — 単発実行。既存 run-id なら再開、無ければ新規（状態で自動判断）
# --------------------------------------------------------------------------
def cmd_run(args) -> int:
    probe = make_bus(args, "run")
    probe.sync_pull()
    resuming = bool(args.run_id) and probe.run_exists(args.run_id)
    if resuming:
        meta = probe.run_meta(args.run_id)
        args.request = meta.get("request", "")
        print(f">>> 既存 run {args.run_id} を再開します（status={meta.get('status')}）", flush=True)
    else:
        if not args.request:
            print("エラー: 新規実行には <要求> が必要です（再開なら既存の --run-id を指定）",
                  file=sys.stderr)
            return 2
        args.run_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    run_id = args.run_id

    bus_root = os.path.abspath(args.bus)
    me = self_path()
    # グローバル引数（バス・転送）を子プロセスへ引き継ぐ
    base = [sys.executable, me, "--bus", bus_root, "--run-id", run_id, "--lease", str(args.lease)]
    if args.git:
        base += ["--git", args.git, "--git-branch", args.git_branch]
    mode = f"git:{args.git}@{args.git_branch}" if args.git else f"local:{bus_root}"

    procs = []
    orch = subprocess.Popen(base + [
        "orchestrate", "--request", args.request,
        "--planner", args.planner, "--executor", args.executor,
        "--max-iterations", str(args.max_iterations),
        "--model_opt", args.model or "",
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

    print(f"\n>>> kiro-flow run: run_id={run_id} bus={mode} ({'resume' if resuming else 'new'})")
    print(f">>> orchestrator x1 + worker x{args.workers} を起動しました。Ctrl-C で全停止。\n", flush=True)

    bus = make_bus(args, "run")

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
            bus.sync_pull()
            if bus.get_status() in TERMINAL:
                print(f"\n>>> run {bus.get_status()}。ワーカーを停止します。", flush=True)
                break
            if orch.poll() is not None and bus.get_status() not in TERMINAL:
                print("\n>>> orchestrator が終了しました。停止します。", flush=True)
                break
            time.sleep(max(args.poll, 1))
    finally:
        shutdown()

    bus.sync_pull()
    final = read_json(bus.final_path)
    if final:
        print("\n=== 最終結果 ===")
        print(final.get("summary", ""))
    return 0


# --------------------------------------------------------------------------
# submit — 要求を inbox に投入（デーモンが拾って orchestrator を起動する）
# --------------------------------------------------------------------------
def cmd_submit(args) -> int:
    req_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    bus = make_bus(args, "submitter")
    bus.sync_pull()
    bus.submit_request(req_id, args.request, f"{socket.gethostname()}-{os.getpid()}")
    bus.sync_push(f"submit request {req_id}")
    print(req_id)  # run-id を標準出力（スクリプトから拾える）
    print(f">>> 要求を投入しました: {req_id}（デーモンが拾います）", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# daemon — 常駐し、要求に応じて orchestrator/worker をオンデマンド起動
# --------------------------------------------------------------------------
def cmd_daemon(args) -> int:
    daemon_id = args.node_id or f"{socket.gethostname()}-{os.getpid()}"
    bus = make_bus(args, f"daemon-{_safe(daemon_id)}")
    me = self_path()
    base = [sys.executable, me, "--bus", os.path.abspath(args.bus), "--lease", str(args.lease)]
    if args.git:
        base += ["--git", args.git, "--git-branch", args.git_branch]
    mode = f"git:{args.git}@{args.git_branch}" if args.git else f"local:{os.path.abspath(args.bus)}"

    orchestrators = {}   # run_id -> Popen
    workers = []         # list of (run_id, Popen)
    wcounter = 0
    stop = {"v": False}

    def shutdown(*_):
        stop["v"] = True
        for _, p in list(orchestrators.items()) + workers:
            if p.poll() is None:
                p.terminate()
    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(130)))
    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(143)))

    log(daemon_id, f"daemon 起動 bus={mode} max_workers={args.max_workers} poll={args.poll}")

    while not stop["v"]:
        bus.sync_pull()
        # 死んだ子を刈り取る
        for rid in [r for r, p in orchestrators.items() if p.poll() is not None]:
            log(daemon_id, f"orchestrator 終了: {rid}")
            del orchestrators[rid]
        workers = [(r, p) for r, p in workers if p.poll() is None]

        # 1) 新しい要求を受理 → orchestrator をオンデマンド起動（分散時は 1 台だけ担当）
        for req_id in bus.list_inbox():
            if bus.run_exists(req_id) or req_id in orchestrators:
                continue
            req = bus.read_inbox(req_id)
            if not req:
                continue
            if bus.claim_request(req_id, daemon_id, args.lease):
                p = subprocess.Popen(base + [
                    "--run-id", req_id, "orchestrate", "--request", req["request"],
                    "--planner", args.planner, "--executor", args.executor,
                    "--max-iterations", str(args.max_iterations),
                    "--model_opt", args.model or "", "--poll", str(args.poll),
                    "--node-id", f"orchestrator-{req_id}",
                ])
                orchestrators[req_id] = p
                log(daemon_id, f"要求 {req_id} を受理 → orchestrator 起動: {req['request'][:50]}")

        # 2) claim 可能タスク量に応じてワーカーをオンデマンド起動
        claim_by_run = {r: bus.run_claimable_count(r) for r in bus.active_runs()}
        alive_by_run = {}
        for r, _ in workers:
            alive_by_run[r] = alive_by_run.get(r, 0) + 1
        for rid in sorted(claim_by_run, key=lambda x: -claim_by_run[x]):
            want = claim_by_run[rid]
            have = alive_by_run.get(rid, 0)
            while have < want and len(workers) < args.max_workers:
                wcounter += 1
                wid = f"{daemon_id}-w{wcounter}"
                p = subprocess.Popen(base + [
                    "--run-id", rid, "work", "--node-id", wid,
                    "--executor", args.executor, "--model_opt", args.model or "",
                    "--poll", str(args.poll), "--idle-exit",
                ])
                workers.append((rid, p))
                have += 1
                log(daemon_id, f"ワーカー起動: {wid} → run {rid}（claim可能={want}）")

        time.sleep(args.poll)
    return 0


# --------------------------------------------------------------------------
# gc — 古い run を掃除
# --------------------------------------------------------------------------
def _age_hours(meta) -> float:
    ts = meta.get("updated_at") or meta.get("created_at")
    if not ts:
        return float("inf")  # タイムスタンプ無し＝十分古いとみなす
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def cmd_gc(args) -> int:
    bus = make_bus(args, "gc")
    bus.sync_pull()
    runs = bus.list_runs()
    metas = [(rid, bus.run_meta(rid)) for rid in runs]
    # 新しい順に並べ、先頭 keep 件は無条件で保護
    metas.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)

    to_delete = []
    for i, (rid, meta) in enumerate(metas):
        if i < args.keep:
            continue
        if _age_hours(meta) < args.older_than * 24.0:
            continue
        if args.status and meta.get("status") != args.status:
            continue
        to_delete.append((rid, meta))

    for rid, meta in to_delete:
        tag = "[dry-run] " if args.dry_run else ""
        print(f"{tag}削除: {rid} (status={meta.get('status')}, age={_age_hours(meta):.1f}h)")
        if not args.dry_run:
            bus.remove_run(rid)
    if to_delete and not args.dry_run:
        bus.sync_push(f"gc: removed {len(to_delete)} run(s)")
    print(f"削除 {len(to_delete)} / 全 {len(runs)} runs"
          f"{'（dry-run）' if args.dry_run else ''}")
    return 0


# --------------------------------------------------------------------------
# status — 状態表示。既定は 1 回表示、--follow でライブ監視（tmux ペイン向け）
# --------------------------------------------------------------------------
def _render_status(bus, run_id, events):
    graph = bus.read_graph()
    status = bus.get_status()
    lines = [f"run: {run_id}   status: {status}   {now_iso()}"]
    if graph and graph.get("nodes"):
        counts = {}
        for nid in graph["nodes"]:
            st = bus.node_state(nid)
            counts[st] = counts.get(st, 0) + 1
        lines.append("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                     + f"   iteration={graph.get('iteration', 0)}")
        for nid, node in graph["nodes"].items():
            deps = ",".join(node.get("deps", [])) or "-"
            lines.append(f"  {nid:<6} {bus.node_state(nid):<8} "
                         f"deps[{deps}] {node.get('goal','')[:48]}")
    else:
        lines.append("  (グラフ未生成)")
    if events:
        evs = bus.recent_events(events)
        if evs:
            lines.append("  --- recent events ---")
            for e in evs:
                lines.append(f"  {e.get('ts','')} {e.get('who',''):<12} "
                             f"{e.get('kind','')} {e.get('node','') or e.get('tasks') or ''}")
    return status, "\n".join(lines)


def cmd_status(args) -> int:
    bus = make_bus(args, "status-viewer")
    try:
        while True:
            bus.sync_pull()
            status, text = _render_status(bus, args.run_id, args.events)
            if args.follow:
                sys.stdout.write("\033[2J\033[H")  # 画面クリア
            print(text, flush=True)
            if not args.follow or (args.until_done and status in TERMINAL):
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


# --------------------------------------------------------------------------
def self_path() -> str:
    return os.path.abspath(__file__)


def main() -> int:
    p = argparse.ArgumentParser(description="kiro-flow — git 共有型・分散 Dynamic Workflow")
    p.add_argument("--bus", default="./.kiro-flow",
                   help="ローカルバスのルート / git モードでは各ノードのクローン親ディレクトリ")
    p.add_argument("--run-id", default=None, help="run 識別子")
    p.add_argument("--git", default=None,
                   help="共有 git リポジトリ URL/パス。指定で複数 PC 分散モードになる")
    p.add_argument("--git-branch", default="main", help="バスに使う git ブランチ")
    p.add_argument("--lease", type=float, default=1800.0,
                   help="claim のリース秒数（超過すると他ノードが再 claim 可能）")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="単発実行。既存 --run-id なら再開、無ければ新規（状態で自動判断）")
    run.add_argument("request", nargs="?", default=None,
                     help="ワークフローへの要求（再開時は省略可）")
    run.add_argument("--workers", type=int, default=2)
    run.add_argument("--planner", choices=["kiro", "stub"], default="kiro")
    run.add_argument("--executor", choices=["kiro", "stub"], default="kiro")
    run.add_argument("--max-iterations", type=int, default=3,
                     help="再計画（evaluator-optimizer）の最大反復回数")
    run.add_argument("--model", default=None)
    run.add_argument("--poll", type=float, default=2.0)
    run.set_defaults(func=cmd_run)

    orch = sub.add_parser("orchestrate", help="計画役")
    orch.add_argument("--request", required=True)
    orch.add_argument("--planner", choices=["kiro", "stub"], default="kiro")
    orch.add_argument("--executor", choices=["kiro", "stub"], default="kiro",
                      help="評価役（evaluator）に使うバックエンド")
    orch.add_argument("--max-iterations", type=int, default=3)
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
    work.add_argument("--idle-exit", action="store_true",
                      help="claim 可能タスクが無くなったら終了（デーモンのオンデマンド起動用）")
    work.set_defaults(func=cmd_work)

    dm = sub.add_parser("daemon", help="常駐し、要求に応じ orchestrator/worker をオンデマンド起動")
    dm.add_argument("--node-id", default=None, help="デーモン識別子（既定: host-pid）")
    dm.add_argument("--max-workers", type=int, default=4, help="このデーモンが同時に走らせる worker 上限")
    dm.add_argument("--planner", choices=["kiro", "stub"], default="kiro")
    dm.add_argument("--executor", choices=["kiro", "stub"], default="kiro")
    dm.add_argument("--max-iterations", type=int, default=3)
    dm.add_argument("--model", default=None)
    dm.add_argument("--poll", type=float, default=2.0)
    dm.set_defaults(func=cmd_daemon)

    sb = sub.add_parser("submit", help="要求を inbox に投入（デーモンが拾う）")
    sb.add_argument("request", help="ワークフローへの要求")
    sb.set_defaults(func=cmd_submit)

    st = sub.add_parser("status", help="run の状態表示（既定 1 回 / --follow でライブ監視）")
    st.add_argument("--follow", "-f", action="store_true", help="ライブ監視（tmux ペイン向け）")
    st.add_argument("--interval", type=float, default=1.0, help="更新間隔（秒, --follow 時）")
    st.add_argument("--events", type=int, default=8, help="表示する直近イベント数")
    st.add_argument("--until-done", action="store_true", help="run 完了で自動終了（--follow 時）")
    st.set_defaults(func=cmd_status)

    gc = sub.add_parser("gc", help="古い run を掃除")
    gc.add_argument("--older-than", type=float, default=7.0, help="この日数より古い run が対象")
    gc.add_argument("--keep", type=int, default=5, help="新しい順にこの件数は無条件で保護")
    gc.add_argument("--status", default=None, help="この status の run のみ対象（例: done）")
    gc.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示")
    gc.set_defaults(func=cmd_gc)

    args = p.parse_args()
    # --model は up でだけ name が衝突しないよう調整済み
    if getattr(args, "model", None) == "":
        args.model = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
