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
import contextlib
import hashlib
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

try:
    import fcntl  # POSIX のみ（macOS/Linux/WSL）。Windows では None。
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

TERMINAL = {"done", "failed"}


def _claim_lock_path(claim_dir: str) -> str:
    """claim 用の排他ロックファイルのパス（バス外の一時領域に置く）。
    同一マシンの同一 claim_dir には同一パスが対応し、プロセス/スレッド間で排他になる。"""
    h = hashlib.sha1(os.path.abspath(claim_dir).encode()).hexdigest()
    d = os.path.join(tempfile.gettempdir(), "kiro-flow-locks")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.lock")


@contextlib.contextmanager
def _file_lock(path: str):
    """fcntl があれば排他ロック。無ければ no-op（ベストエフォート）。"""
    if fcntl is None:
        yield
        return
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


# --------------------------------------------------------------------------
# 設定ファイル（kiro-loop と同じ流儀: YAML 任意 / JSON フォールバック）
# --------------------------------------------------------------------------
try:
    import yaml  # type: ignore

    def _load_config_file(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
except ImportError:  # PyYAML 無し → JSON のみ
    yaml = None  # type: ignore

    def _load_config_file(path: str) -> dict:  # type: ignore[misc]
        if path.lower().endswith((".yaml", ".yml")):
            print("[kiro-flow] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定なら不要です。", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


DEFAULT_CONFIG_NAMES = ["kiro-flow.yaml", "kiro-flow.yml", "kiro-flow.json"]

# 環境ごとに変わる値の組み込み既定。設定ファイルのキーもこの名前（snake_case）。
CONFIG_DEFAULTS = {
    "bus": "./.kiro-flow",
    "git": None,
    "git_branch": "main",
    "git_subdir": "",
    "lease": 1800.0,
    "poll": 2.0,
    "model": None,
    "planner": "flow-planner",
    "executor": "kiro",
    "max_workers": 4,
    "max_iterations": 3,
    "max_fanout": 50,
    "review": "auto",  # auto: 集約パターンで自動有効 / True/False: 明示上書き
    "workers": 2,
}

# 集約点（reduce/synthesize）を持ち、独立レビューが結果の信頼性を高めるパターン。
# 公式 dynamic workflows の「集約前に互いの成果をレビューする品質パターン」に倣い、
# これらでは検証 gate を既定で自動挿入する。generate-and-filter/tournament/
# adversarial-verification は元々 filter/judge/verify を内包するため対象外。
AGGREGATING_PATTERNS = {"map-reduce", "fan-out-and-synthesize"}


def _review_decision(review_setting, patterns) -> bool:
    """review の三値解決。True/False は明示指定として尊重。'auto'（既定）や None は
    集約パターンを含むときのみ自動で有効化する。"""
    if isinstance(review_setting, bool):
        return review_setting
    return bool(set(patterns or []) & AGGREGATING_PATTERNS)


def _find_config(explicit):
    """設定ファイルの探索（フォールバック順）:
       1. --config で明示指定
       2. カレントディレクトリの .kiro/kiro-flow.{yaml,yml,json}
       3. ~/.kiro/kiro-flow.{yaml,yml,json}"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            print(f"[kiro-flow] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
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
    """優先順位 CLI > 設定ファイル > 組み込み既定 で各値を確定する。
    CLI 未指定（None）の設定値だけを設定ファイル→既定で埋める。"""
    path = _find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    args._config_path = path
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, dflt))
    return args


# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ts_lock = threading.Lock()
_last_ts = 0.0


def _unique_ts() -> float:
    """プロセス内で厳密に増加する claim 用タイムスタンプ。
    同値 ts による「決定的タイブレークの勝者」と「先着読みの勝者」の食い違い
    （同プロセスの並行 claim で二重勝者になりうる）を防ぐ。"""
    global _last_ts
    with _ts_lock:
        t = time.time()
        if t <= _last_ts:
            t = _last_ts + 1e-6
        _last_ts = t
        return t


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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """端末カラー等の ANSI エスケープを除去する。
    kiro-cli の出力にはカラーコードが混ざるため、保存・解析前に正規化する。"""
    return _ANSI_RE.sub("", text or "")


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
            "ts": _unique_ts(),
            "claimed_at": now_iso(),
            "lease_until": time.time() + lease_sec,
        })

    def _try_claim_in(self, claim_dir: str, who: str, lease_sec: float, msg: str) -> bool:
        # 同一マシン上の並行 claim を排他ロックで直列化する（ロックはバス外＝
        # git に乗せない一時ファイル）。これで「先着読みの勝者」と「決定的
        # タイブレークの勝者」の食い違いによる二重勝者を防ぐ。
        # git 分散（別マシン）はクローンごとに別ロックなので直列化されないが、
        # その整合は sync_pull 後の決定的タイブレーク＋lease が担う。
        os.makedirs(claim_dir, exist_ok=True)
        with _file_lock(_claim_lock_path(claim_dir)):
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

    def write_result(self, node_id: str, who: str, status: str, output: str,
                     data=None) -> None:
        rec = {
            "id": node_id,
            "who": who,
            "status": status,
            "output": output,
            "finished_at": now_iso(),
        }
        if data is not None:  # 構造化成果（任意）。エージェント間を JSON で流す
            rec["data"] = data
        write_json_atomic(self.result_path(node_id), rec)

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
        # 対応する inbox 要求と claim も消す（req_id == run_id）。残すとデーモンの
        # 重複排除（run_exists ベース）が外れ、gc 後にリース失効済みの要求を拾い直して
        # 完了済みの run を再実行してしまう。
        try:
            os.remove(os.path.join(self.inbox_dir, f"{run_id}.json"))
        except OSError:
            pass
        shutil.rmtree(os.path.join(self.inbox_claims_dir, run_id), ignore_errors=True)

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

    def __init__(self, clone_dir: str, run_id: str, remote: str, branch: str = "main",
                 subdir: str = ""):
        # git の作業ツリーは clone_dir。バスのルートはその中の subdir（指定時）。
        self.workdir = clone_dir
        self.subdir = (subdir or "").strip("/")
        bus_root = os.path.join(clone_dir, self.subdir) if self.subdir else clone_dir
        super().__init__(bus_root, run_id)
        self.remote = remote
        self.branch = branch
        self._ensure_clone()

    # sparse checkout で作業ツリーに展開するパス（cone モード）
    def _sparse_paths(self):
        return [self.subdir] if self.subdir else ["runs", "inbox"]

    def _git(self, args, check=True):
        p = subprocess.run(["git", "-C", self.workdir] + args, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {p.stderr.strip()[:300]}")
        return p

    def _ensure_clone(self) -> None:
        first = not os.path.isdir(os.path.join(self.workdir, ".git"))
        if first:
            os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
            # sparse checkout: --no-checkout で取得し、必要なパスだけ展開する
            r = subprocess.run(
                ["git", "clone", "--no-checkout", "--filter=blob:none", self.remote, self.workdir],
                capture_output=True, text=True)
            if r.returncode != 0:
                # blob filter 非対応サーバ向けフォールバック
                r = subprocess.run(["git", "clone", "--no-checkout", self.remote, self.workdir],
                                   capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"git clone 失敗: {r.stderr.strip()[:300]}")
        # コミット用 ID（未設定環境向けのフォールバック）
        if not self._git(["config", "user.email"], check=False).stdout.strip():
            self._git(["config", "user.email", "kiro-flow@local"])
            self._git(["config", "user.name", "kiro-flow"])
        # sparse checkout（cone モード）を設定 — バスのサブツリーだけ作業ツリーに置く
        self._git(["sparse-checkout", "init", "--cone"], check=False)
        self._git(["sparse-checkout", "set"] + self._sparse_paths(), check=False)
        # 対象ブランチへ。無ければ作成（空リポジトリ初回も含む）
        if self._git(["checkout", self.branch], check=False).returncode != 0:
            self._git(["checkout", "-B", self.branch])

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
        # バスサブディレクトリを考慮したリポジトリ相対パスで git rm
        rel = os.path.join(self.subdir, "runs", run_id) if self.subdir else f"runs/{run_id}"
        self._git(["rm", "-r", "-q", "--ignore-unmatch", rel], check=False)
        super().remove_run(run_id)  # 未追跡の残骸も掃除（commit/push は呼び出し側）


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def make_bus(args, node_id: str) -> Bus:
    """--git があれば GitBus（ノードごとに専用クローン）、無ければローカル Bus。"""
    run_id = args.run_id or "_"  # gc 等 run 横断コマンドでは run_id 不要
    if getattr(args, "git", None):
        clone_dir = os.path.join(os.path.abspath(args.bus), _safe(node_id))
        return GitBus(clone_dir, run_id, remote=args.git, branch=args.git_branch,
                      subdir=getattr(args, "git_subdir", "") or "")
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
# ワークフローパターンのカタログ（7 パターン）
# --------------------------------------------------------------------------
# 最初の 6 つは Claude Dynamic Workflows の 6 パターン、map-reduce は kiro-flow が
# 追加した 7 つ目の正規パターン（split→実行時に map×N を動的展開→reduce）。
# orchestrator はこのカタログを知っていて、要求に応じてパターンの組み合わせと
# 並列数（fan-out 幅）を決め、タスクグラフを形作る。各ノードには kind を付け、
# kind に応じて worker の実行プロンプトと評価役の継続判断が変わる。
PATTERNS = {
    "classify-and-act": "1 つの分類エージェントが種別を判定し、結果に応じて適切な専門タスクへ振り分ける（ルーティング）。",
    "fan-out-and-synthesize": "大きな仕事を独立な小片に分割し並列実行、最後に統合ノードでまとめる。",
    "adversarial-verification": "生成ノードの成果を別の検証ノードが批判的にチェックし、問題があれば作り直す。",
    "generate-and-filter": "候補を多数（並列）生成し、フィルタノードが基準を満たすものだけ残す。",
    "tournament": "複数案を並列生成し、判定ノードが比較して最良案を選ぶ。",
    "loop-until-done": "完了条件（テスト通過・指摘なし・品質達成）を満たすまで生成と検証を反復する。",
    "map-reduce": "split ノードが入力をリスト化し、実行時に要素数ぶんの map を動的に展開して "
                  "reduce で集約する（データ駆動の fan-out。件数を事前に固定しない）。",
}
# ノード種別: work=通常実行 / generate=候補生成 / classify=分類 / synthesize=統合 /
#            verify=検証 / filter=絞り込み / judge=最良選択 / reduce=構造化データの集約 /
#            split=リスト化（データ駆動 fan-out の起点）/ map=要素ごとの処理
PATTERN_LIST = list(PATTERNS)

# 有効なノード kind。planner（kiro）が未知 kind を出したら work に丸める。
VALID_KINDS = {"work", "generate", "classify", "synthesize", "verify",
               "filter", "judge", "reduce", "split", "map"}

# 構造化データ（data）を成果として意図する kind。これら以外（work/generate/
# classify/synthesize）の自由記述出力では、散文中に紛れた JSON 風断片を data に
# 昇格させない（例: 本文の "issues": [] を空リスト data と誤抽出して下流を汚す事故を防ぐ）。
STRUCTURED_KINDS = {"split", "map", "reduce", "filter", "judge", "verify"}


def _coerce_tasks(raw, existing=()):
    """planner/評価役（kiro）の生出力をタスク dict に正規化する。
    id 重複除去・既存 id 回避・不正 kind の work 丸め・deps の文字列化を行う。"""
    seen = set(existing)
    out = []
    for i, t in enumerate(raw or []):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"t{i+1}")
        if tid in seen:
            continue
        seen.add(tid)
        kind = str(t.get("kind", "work"))
        if kind not in VALID_KINDS:
            kind = "work"
        out.append({
            "id": tid,
            "goal": str(t.get("goal", "")),
            "deps": [str(d) for d in (t.get("deps") or [])],
            "kind": kind,
        })
    return out


def plan_stub(request: str):
    """kiro-cli 無しの簡易分解。

    区切り記号で依存も表現:
      ';' / 改行 … 独立（並列）タスクの境界
      '->'        … 逐次依存チェーン（各タスクが直前のタスクに依存）

    区切り記号が無い単一文字列ならタスク数をランダム（2−5件）で決める。"""
    segments = [s.strip() for s in request.replace("\n", ";").split(";") if s.strip()]
    if not segments:
        segments = [request.strip() or "no-op"]
    # 単一セグメントかつ依存記号（'->')も無い場合はタスク数をランダム展開
    if len(segments) == 1 and "->" not in segments[0]:
        n = random.randint(2, 5)
        base = segments[0][:48]
        segments = [f"{base}（サブタスク{j + 1}）" for j in range(n)]
    tasks = []
    idx = 0
    for seg in segments:
        chain = [c.strip() for c in seg.split("->") if c.strip()]
        prev = None
        for goal in chain:
            idx += 1
            tid = f"t{idx}"
            tasks.append({"id": tid, "goal": goal, "deps": [prev] if prev else [], "kind": "work"})
            prev = tid
    return tasks


def _detect_pattern(request: str) -> str:
    t = request.lower()
    table = [
        ("classify-and-act", ["classif", "route", "routing", "ルーティング", "分類", "振り分け", "triage", "トリアージ"]),
        ("map-reduce", ["それぞれ", "各", "per item", "per-item", "分割して", "一覧", "列挙", "map-reduce", "map reduce", "件ごと", "ごとに"]),
        ("tournament", ["tournament", "トーナメント", "対戦", "ベスト", "best of", "最良", "勝ち抜き"]),
        ("generate-and-filter", ["filter", "フィルタ", "候補", "絞り込", "candidate", "ふるい"]),
        ("adversarial-verification", ["verify", "検証", "レビュー", "review", "adversar", "批判", "critique", "監査"]),
        ("loop-until-done", ["loop", "until", "繰り返", "反復", "直るまで", "tests pass", "通るまで", "完了まで"]),
    ]
    for name, kws in table:
        if any(k in t for k in kws):
            return name
    return "fan-out-and-synthesize"


def _parallelism(request: str, default: int) -> int:
    m = re.search(r"[x×]\s*(\d+)", request) or re.search(r"並列\s*(\d+)", request)
    if m:
        return max(1, min(8, int(m.group(1))))
    return max(2, min(6, default))


def _strategy_to_graph(pattern: str, request: str, par: int, review: bool = False):
    """選んだパターンを初期タスクグラフ（kind 付き）へ落とし込む。"""
    short = request.strip()[:48]
    if pattern == "classify-and-act":
        # 分類ノードのみ。専門タスクは分類結果を見て継続段階で追加（ルーティング）
        return [{"id": "classify", "goal": f"分類: {short}", "deps": [], "kind": "classify"}]
    if pattern == "map-reduce":
        # split ノードのみ。map（要素ごと）と reduce は実行時に動的展開（データ駆動 fan-out）
        return [{"id": "split1", "goal": f"分解: {short}", "deps": [], "kind": "split"}]
    if pattern == "generate-and-filter":
        gens = [{"id": f"g{i+1}", "goal": f"候補{i+1}: {short}", "deps": [], "kind": "generate"}
                for i in range(par)]
        return gens + [{"id": "filter", "goal": "候補を基準でフィルタ",
                        "deps": [g["id"] for g in gens], "kind": "filter"}]
    if pattern == "tournament":
        gens = [{"id": f"c{i+1}", "goal": f"案{i+1}: {short}", "deps": [], "kind": "generate"}
                for i in range(par)]
        return gens + [{"id": "judge", "goal": "比較して最良案を選ぶ",
                        "deps": [g["id"] for g in gens], "kind": "judge"}]
    if pattern == "adversarial-verification":
        return [{"id": "gen1", "goal": short, "deps": [], "kind": "generate"},
                {"id": "verify1", "goal": "成果を批判的に検証", "deps": ["gen1"], "kind": "verify"}]
    if pattern == "loop-until-done":
        return [{"id": "work1", "goal": short, "deps": [], "kind": "work"},
                {"id": "check1", "goal": "完了条件を確認", "deps": ["work1"], "kind": "verify"}]
    # fan-out-and-synthesize（既定）: 並列ノード + （任意で gate）+ 統合ノード
    gens = plan_stub(request)
    if len(gens) < 2:  # 単一要求なら par 個に展開
        gens = [{"id": f"t{i+1}", "goal": f"{short}（観点{i+1}）", "deps": [], "kind": "work"}
                for i in range(par)]
    gen_ids = [g["id"] for g in gens]
    if review:
        # 統合前の事前チェック / 敵対的レビュー（adversarial-verification との複合）。
        # 統合ノードは成果（gens）＋ gate に依存し、gate 通過後に gens を統合する。
        gate = {"id": "gate", "goal": "統合前レビュー（成果を検証）",
                "deps": gen_ids, "kind": "verify"}
        synth = {"id": "synth", "goal": f"統合: {short}",
                 "deps": gen_ids + ["gate"], "kind": "synthesize"}
        return gens + [gate, synth]
    return gens + [{"id": "synth", "goal": f"統合: {short}",
                    "deps": gen_ids, "kind": "synthesize"}]


def plan_strategy_stub(request: str, review="auto"):
    """要求からパターンと並列数を選び、初期グラフを作る（kiro 無し版）。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。"""
    pattern = _detect_pattern(request)
    base = plan_stub(request)
    par = _parallelism(request, len([t for t in base if not t["deps"]]))
    review = _review_decision(review, [pattern])
    tasks = _strategy_to_graph(pattern, request, par, review)
    patterns = [pattern] + (["adversarial-verification"] if review and pattern != "adversarial-verification" else [])
    strategy = {"patterns": patterns, "parallelism": par, "review": review,
                "reason": f"stub heuristic → {pattern}" + ("（統合前レビュー有）" if review else "")}
    return strategy, tasks


def plan_strategy_kiro(request: str, model: str | None, review="auto"):
    """kiro-cli にパターン選択・並列数・初期グラフを決めさせる。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。"""
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    compose = ("必要なら複数パターンを多段に複合してよい（例: classify-and-act の各分岐を "
               "fan-out-and-synthesize にする / generate-and-filter の通過案で tournament を行う）。")
    # 明示 OFF でなければレビューの意図を planner に伝える（最終的な有効/無効は
    # 返ってきた patterns を見て _review_decision で確定する）。
    review_note = ("統合（synthesize/reduce）を伴うパターンでは、集約の前に verify ノードを 1 つ挟み、"
                   "事前チェック・敵対的レビューを行ってください。" if review is not False else "")
    prompt = (
        "あなたは分散 Dynamic Workflow の計画役です。以下のワークフローパターンを知っています:\n"
        f"{catalog}\n\n"
        f"要求に最も適したパターンと並列数を選び、{compose}{review_note}"
        "それを反映した初期タスクグラフを作ってください。各タスクには kind を付けます: "
        "work/generate/classify/synthesize/verify/filter/judge/reduce/split"
        "（reduce=構造化データの集約 / split=リスト化してデータ駆動 fan-out の起点）。"
        "重要: map-reduce では split ノードを1つだけ置き、要素ごとの map と reduce は"
        " split 完了後に実行時へ動的展開されるので、グラフに静的に書かないこと"
        "（split→work→reduce のような固定チェーンにすると並列展開されない）。"
        "並列にできるタスクは deps を空に、順序や統合が要るものは deps に先行 id を入れます。"
        "依存は既存タスク id のみ、循環は作らないこと。\n"
        "出力は JSON オブジェクトのみ:\n"
        '{"patterns": ["..."], "parallelism": N, "reason": "...", '
        '"tasks": [{"id": "t1", "goal": "...", "deps": [], "kind": "work"}]}\n\n'
        f"要求: {request}"
    )
    try:
        data = extract_json(run_kiro(prompt, model))
        # planner がオブジェクトでなくベア配列を返すことがある → tasks とみなす
        if isinstance(data, list):
            data = {"tasks": data}
        tasks = _coerce_tasks(data.get("tasks"))
        if not tasks:
            raise ValueError("tasks 空")
        patterns = [p for p in (data.get("patterns") or []) if p in PATTERNS] or ["fan-out-and-synthesize"]
        strategy = {
            "patterns": patterns,
            "parallelism": int(data.get("parallelism", 2) or 2),
            "review": _review_decision(review, patterns),
            "reason": str(data.get("reason", "")),
        }
        return strategy, tasks
    except Exception:  # noqa: BLE001 — 解釈できなければ stub の戦略に倒す
        return plan_strategy_stub(request, review)


def _find_flow_planner_script():
    """flow-planner スキルの plan.py を探す。
    検索順: .github/skills/flow-planner/ → git root/.github/skills/ → ~/.kiro/skills/ → {skill_home}/"""
    candidates = []
    # ワークスペース内
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, ".github", "skills", "flow-planner", "scripts", "plan.py"))
    # リポジトリルート（git rev-parse で探す）
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        ).stdout.strip()
        if root:
            candidates.append(os.path.join(root, ".github", "skills", "flow-planner", "scripts", "plan.py"))
    except Exception:  # noqa: BLE001
        pass
    # ~/.kiro/skills 直下を直接確認
    kiro_skills = os.path.expanduser("~/.kiro/skills")
    candidates.append(os.path.join(kiro_skills, "flow-planner", "scripts", "plan.py"))
    # skill-registry.json から skill_home を読む
    for agent_dir in [os.path.expanduser("~/.kiro"), os.path.expanduser("~/.copilot"),
                      os.path.expanduser("~/.claude"), os.path.expanduser("~/.codex")]:
        reg = os.path.join(agent_dir, "skill-registry.json")
        if os.path.isfile(reg):
            try:
                with open(reg, encoding="utf-8") as f:
                    data = json.load(f)
                home = data.get("skill_home", "")
                if home:
                    candidates.append(os.path.join(home, "flow-planner", "scripts", "plan.py"))
            except Exception:  # noqa: BLE001
                pass
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def plan_strategy_flow_planner(request: str, model: str | None, review="auto"):
    """flow-planner スキルの3段パイプラインを呼び出す。
    スキルが見つからない / 失敗した場合は plan_strategy_kiro にフォールバック。"""
    script = _find_flow_planner_script()
    if not script:
        # flow-planner スキル未インストール → kiro planner にフォールバック
        return plan_strategy_kiro(request, model, review)
    cmd = [sys.executable, script, request]
    if model:
        cmd += ["--model", model]
    if isinstance(review, bool):
        cmd += ["--review", "true" if review else "false"]
    else:
        cmd += ["--review", str(review)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:500])
        data = json.loads(proc.stdout)
        strategy = data.get("strategy", {})
        tasks = _coerce_tasks(data.get("tasks", []))
        if not tasks:
            raise ValueError("flow-planner returned empty tasks")
        # strategy を正規化
        patterns = [p for p in (strategy.get("patterns") or []) if p in PATTERNS] or ["fan-out-and-synthesize"]
        final_strategy = {
            "patterns": patterns,
            "parallelism": int(strategy.get("parallelism", 2) or 2),
            "review": _review_decision(review, patterns) if not isinstance(strategy.get("review"), bool)
                      else strategy["review"],
            "reason": f"[flow-planner] {strategy.get('reason', '')}",
        }
        return final_strategy, tasks
    except Exception:  # noqa: BLE001 — flow-planner 失敗時は kiro にフォールバック
        return plan_strategy_kiro(request, model, review)


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
    return strip_ansi(proc.stdout).strip()


# dep_results は {dep_id: result_dict}（result_dict は output テキストと任意の data を持つ）。
# 実行結果は (text, data) を返す。data は構造化成果（JSON 可、無ければ None）。
def _dep_text(r: dict) -> str:
    return str((r or {}).get("output", ""))


def _dep_data(r: dict):
    return (r or {}).get("data")


def _stub_sleep() -> None:
    """stub の擬似実行時間。既定 1〜5 秒。環境変数 KIRO_FLOW_STUB_SLEEP_MAX で調整
    （テストや動作確認では 0 にして高速化できる）。"""
    try:
        mx = float(os.environ.get("KIRO_FLOW_STUB_SLEEP_MAX", "5"))
    except ValueError:
        mx = 5.0
    if mx > 0:
        time.sleep(random.uniform(min(1.0, mx), mx))


def execute_stub(kind: str, goal: str, dep_results: dict, model: str | None):
    _stub_sleep()  # 実行時間を模す（KIRO_FLOW_STUB_SLEEP_MAX で調整可）
    # 失敗注入: "FAIL" を含むと失敗（retry される）/ "FLAKY" は一旦 issue を残す（verify loop 用）
    if "FAIL" in goal:
        raise RuntimeError(f"[stub] 意図的失敗: {goal}")
    # gate（verify の判定 {"ok":...}）は集約対象から除く
    def _is_gate(r):
        dv = _dep_data(r)
        return isinstance(dv, dict) and "ok" in dv
    agg = {d: r for d, r in dep_results.items() if not _is_gate(r)}
    texts = {d: _dep_text(r) for d, r in dep_results.items()}
    if kind == "split":
        # 入力をリストへ分解（データ駆動 fan-out の起点）。要素数は goal 中の数字 or 既定 3
        m = re.search(r"\d+", goal)
        k = max(1, min(int(m.group()) if m else 3, 8))
        items = [f"{goal[:30]} #{i + 1}" for i in range(k)]
        return f"[split] {k} 件に分解", items
    if kind == "classify":
        label = next((lbl for lbl in ("frontend", "backend", "security", "performance")
                      if lbl in goal.lower()), "general")
        return f"class={label}", {"label": label}
    if kind == "synthesize":
        return (f"[synth] {len(agg)} 件を統合: " + " | ".join(agg)[:80],
                {"merged": list(agg)})
    if kind == "filter":
        kept = [d for d, t in texts.items() if "FAIL" not in t and "issue" not in t]
        return f"[filter] 採用={','.join(kept)}", {"kept": kept}
    if kind == "judge":
        win = next(iter(dep_results), "")
        return f"[judge] winner={win}", {"winner": win}
    if kind == "verify":
        ok = all("issue" not in t and "fail" not in t.lower() for t in texts.values())
        return ("verify=pass" if ok else "verify=fail"), {"ok": ok}
    if kind == "reduce":
        # 依存の構造化 data を畳み込む（gate は除外。list は連結、その他は要素として収集）
        items = []
        for d, r in agg.items():
            dv = _dep_data(r)
            if isinstance(dv, list):
                items.extend(dv)
            elif dv is not None:
                items.append(dv)
            else:
                items.append(_dep_text(r))
        return f"[reduce] {len(items)} 件を集約", {"items": items, "count": len(items)}
    # work / generate
    if "FLAKY" in goal:
        return f"[stub] 未完(issue): {goal}", None
    return f"[stub] 完了: {goal}", None


def execute_kiro(kind: str, goal: str, dep_results: dict, model: str | None):
    role = {
        "classify": "分類役。入力を適切なカテゴリへ分類し『class=<ラベル>』形式で出力。",
        "synthesize": "統合役。依存タスクの成果を統合して 1 つの成果物にまとめる。",
        "filter": "選別役。依存の候補から基準を満たすものだけを残し、採用理由を述べる。",
        "judge": "審判役。依存の複数案を比較し最良案を選び理由を述べる。",
        "reduce": "集約役。依存タスクの構造化データを畳み込み、集約結果を JSON で出力。"
                  " 要素数を表す count を含める場合は、必ず集約後リストの実際の要素数と一致させること。",
        "split": "分解役。入力を独立に処理できる小片のリストへ分解し、"
                 "各要素を文字列とする JSON 配列のみを出力（例: [\"1-100\", \"101-200\"]）。"
                 " 説明文は付けず配列だけを返すこと。",
        "map": "map役。ゴールに示された本来のタスクを、与えられた1要素だけに適用して結果を返す。"
               " 勝手に別の処理（合計・件数など）に変えないこと。"
               " リスト状の成果は JSON 配列で出力し、後段の集約に渡せるようにする。",
        "verify": "検証役。依存の成果を鵜呑みにせず独立に検算する。"
                  "可能なら結果を自分で再導出して突き合わせ、最低限"
                  "(1)件数・合計の整合 (2)抜け漏れ・重複 (3)各要素の妥当性の抜き取り検査"
                  " を行う。問題が無ければ『verify=pass』、あれば『verify=fail』と"
                  "具体的な該当箇所を出力し、末尾に JSON"
                  ' {"ok": true|false, "issues": ["..."]} を必ず添える。',
    }.get(kind, "ワーカー。次のタスクだけを完了し成果物を出力。")
    # 集約・選別系では gate（verify の判定）を入力から除く（成果物に紛れ込ませない）
    deps = dep_results
    if kind in ("reduce", "synthesize", "filter", "judge"):
        deps = {d: r for d, r in dep_results.items() if not _is_gate_result(r)}
    prompt = f"あなたは分散 Dynamic Workflow の{role}\nタスク({kind}): {goal}\n"
    if deps:
        lines = []
        for d, r in deps.items():
            line = f"[{d}] {_dep_text(r)}"
            dv = _dep_data(r)
            if dv is not None:
                line += f"\n  data: {json.dumps(dv, ensure_ascii=False)[:400]}"
            lines.append(line)
        prompt += "\n依存タスクの成果:\n" + "\n".join(lines) + "\n"
    prompt += "\n成果物を簡潔に直接出力してください。"
    text = run_kiro(prompt, model)
    # 構造化データを意図する kind のみ JSON を抽出（自由記述の本文から JSON 風断片を
    # data に誤昇格させない）。
    data = None
    if kind in STRUCTURED_KINDS:
        try:
            data = extract_json(text)
        except Exception:  # noqa: BLE001 — 構造化できなければテキストのみ
            data = None
    if kind == "reduce":
        data = _reconcile_count(data)
    elif kind == "verify":
        data = _normalize_verify(text, data)
    return text, data


def _is_gate_result(r: dict) -> bool:
    """verify gate の結果か（data が {"ok": ...} を持つ）。集約対象から除くのに使う。"""
    dv = _dep_data(r)
    return isinstance(dv, dict) and "ok" in dv


def _collect_dep_results(bus, node: dict, kind: str) -> dict:
    """ノードの依存成果を集める。集約系（reduce/synthesize/filter/judge）では、
    planner が work→gate→synth と直列にして集約役の依存が gate だけになっても入力が
    空にならないよう、gate が検証した上流の成果も透過して渡す（gate 判定自体は
    execute 側で集約対象から除外される）。"""
    dep_results = {d: (bus.read_result(d) or {}) for d in node.get("deps", [])}
    if kind in ("reduce", "synthesize", "filter", "judge"):
        gnodes = (bus.read_graph() or {}).get("nodes", {})
        for d in list(dep_results):
            if _is_gate_result(dep_results[d]):
                for up in gnodes.get(d, {}).get("deps", []):
                    dep_results.setdefault(up, bus.read_result(up) or {})
    return dep_results


def _normalize_verify(text: str, data):
    """verify 成果を {"ok": bool, ...} 形へ正規化する。
    LLM が JSON を欠いても、本文の verify=pass/fail から ok を導いて gate を機能させる。"""
    if isinstance(data, dict) and "ok" in data:
        return data
    low = text.lower()
    ok = ("verify=pass" in low) or ("verify=fail" not in low and "fail" not in low)
    out = {"ok": ok}
    if isinstance(data, dict):
        out.update(data)
        out["ok"] = ok
    return out


def _reconcile_count(data):
    """reduce 成果の count を実リスト長へ補正する。
    dict に count(int) と単一のリスト値があれば、count = len(list) に揃える
    （LLM 自己申告の件数とリスト実体の不整合を機械的に解消）。"""
    if not isinstance(data, dict) or "count" not in data:
        return data
    lists = [v for v in data.values() if isinstance(v, list)]
    if len(lists) == 1 and isinstance(data.get("count"), int):
        data["count"] = len(lists[0])
    return data


# --------------------------------------------------------------------------
# Continuation — パターンに応じて done / replan（タスク追加）を決める
# --------------------------------------------------------------------------
def _expand_splits(nodes: dict, results: dict, max_fanout: int,
                   review: bool = False, request: str = ""):
    """データ駆動の動的 fan-out: 完了した split ノードの data(リスト)を見て、
    実行時に要素ごとの map タスクと、それらを集約する reduce タスクを生成する。
    （reduce は展開時に作るので、split 完了直後に reduce が先走り実行されない）
    review 時は map と reduce の間に検証 gate を挟む。
    map・reduce ゴールには元の要求（intent）を埋め込み、各要素への適用と最終整形
    （並べ替え・重複排除など要求由来の集約条件）が失われないようにする。"""
    new = []
    have = set(nodes)
    for nid, node in nodes.items():
        if node.get("kind") != "split":
            continue
        r = results.get(nid, {})
        if r.get("status") != "done":
            continue
        if f"{nid}-reduce" in have:  # 既に展開済み
            continue
        items = r.get("data")
        if not isinstance(items, list) or not items:
            continue
        items = items[:max(1, max_fanout)]  # 暴走防止のクランプ
        intent = (request or node.get("goal", "")).strip()
        map_ids = []
        for i, item in enumerate(items):
            mid = f"{nid}-m{i+1}"
            map_ids.append(mid)
            # 要素だけでなく「何をするか」を渡さないと map が意図を失う
            goal = f"{intent}（対象要素: {item}）" if intent else f"{nid} 要素{i+1}: {item}"
            new.append({"id": mid, "goal": goal, "deps": [], "kind": "map"})
        reduce_deps = map_ids
        if review:  # 集約前の事前チェック / 敵対的レビュー。reduce は map＋gate に依存
            gid = f"{nid}-gate"
            new.append({"id": gid, "goal": f"{nid} の map 結果を集約前に検証",
                        "deps": map_ids, "kind": "verify"})
            reduce_deps = map_ids + [gid]
        # reduce も intent を保持（「アルファベット順」「重複排除」等の集約条件を失わない）
        reduce_goal = (f"{intent}（各 map の結果を要求どおりに集約・整形して最終成果にまとめる）"
                       if intent else f"{nid} の結果を集約")
        new.append({"id": f"{nid}-reduce", "goal": reduce_goal,
                    "deps": reduce_deps, "kind": "reduce"})
    return new


def continue_stub(request: str, nodes: dict, results: dict, iteration: int,
                  max_fanout: int = 50, review: bool = False):
    """パターン継続（kiro 無し版）:
       - データ駆動 fan-out: split 完了 → 要素ごとの map + reduce を生成
       - classify-and-act: 分類完了 → 振り分け先の専門タスクを追加
       - adversarial / loop-until-done: verify が fail → 作り直し + 再検証
       - 失敗タスク: retry を 1 回追加"""
    new = _expand_splits(nodes, results, max_fanout, review, request)
    have = set(nodes)

    def fresh(tid):
        return tid not in have and tid not in [t["id"] for t in new]

    for nid, node in nodes.items():
        r = results.get(nid, {})
        if r.get("status") != "done" and r.get("status") != "failed":
            continue
        kind = node.get("kind", "work")
        # 1) classify → 専門タスクへルーティング（追加のみ）
        if kind == "classify" and r.get("status") == "done":
            actid = f"{nid}-act"
            if fresh(actid):
                label = str(r.get("output", "")).split("=")[-1].strip() or "general"
                new.append({"id": actid, "goal": f"{label} 専門処理: {request[:30]}",
                            "deps": [nid], "kind": "work"})
        # 2) verify が fail → 依存を作り直して再検証（loop-until-done / adversarial）
        #    replaces で依存元（gen/verify）を置き換え、後続の依存を付け替える
        if kind == "verify" and "fail" in str(r.get("output", "")):
            for dep in node.get("deps", []):
                rid = f"{dep}-r{iteration+1}"
                if fresh(rid):
                    goal = nodes.get(dep, {}).get("goal", "").replace("FLAKY", "ok")
                    new.append({"id": rid, "goal": f"[retry] {goal}", "deps": [],
                                "kind": nodes.get(dep, {}).get("kind", "work"), "replaces": dep})
            vid = f"{nid}-r{iteration+1}"
            if fresh(vid):
                new.append({"id": vid, "goal": "再検証",
                            "deps": [f"{dep}-r{iteration+1}" for dep in node.get("deps", [])],
                            "kind": "verify", "replaces": nid})
        # 3) 失敗タスクの retry（失敗ノードを置き換え、依存元を付け替える）
        if r.get("status") == "failed":
            rid = f"{nid}r"
            if fresh(rid):
                goal = node.get("goal", "").replace("FAIL", "ok")
                new.append({"id": rid, "goal": f"[retry] {goal}", "deps": [],
                            "kind": node.get("kind", "work"), "replaces": nid})
    if new:
        return "replan", new, f"{len(new)} 件追加"
    return "done", [], "全パターン完了"


def continue_kiro(request: str, nodes: dict, results: dict, iteration: int,
                  max_fanout: int = 50, review: bool = False):
    # データ駆動 fan-out は機械的に展開（LLM 判断不要）。先に処理する。
    fanout_tasks = _expand_splits(nodes, results, max_fanout, review, request)
    if fanout_tasks:
        return "replan", fanout_tasks, f"data-driven fan-out: +{len(fanout_tasks)}"
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    summary = "\n".join(
        f"- {nid} ({nodes.get(nid, {}).get('kind','work')}) "
        f"[{r.get('status')}]: {str(r.get('output',''))[:160]}"
        for nid, r in results.items()
    )
    prompt = (
        "あなたは分散 Dynamic Workflow の評価役です。6 パターンを踏まえ、現在の結果が要求を満たすか判定し、"
        "必要なら次のタスクを追加してください（例: 分類結果に応じた専門タスク、検証 fail の作り直し、"
        "統合や追加候補の生成）。\n"
        f"パターン:\n{catalog}\n\n"
        "出力は JSON のみ: "
        '{"decision":"done"|"replan","reason":"...",'
        '"new_tasks":[{"id":"...","goal":"...","deps":[],"kind":"work"}]}\n'
        "既存 id と重複しない id を使うこと。done のとき new_tasks は空配列。\n\n"
        f"元の要求: {request}\n\n現在の結果:\n{summary}"
    )
    try:
        data = extract_json(run_kiro(prompt, None))
    except Exception:  # noqa: BLE001
        return "done", [], "評価出力を解釈できず done 扱い"
    # planner がオブジェクトでなくベア配列を返すことがある → new_tasks とみなす
    if isinstance(data, list):
        data = {"decision": "replan", "new_tasks": data}
    if not isinstance(data, dict):
        return "done", [], "評価出力が想定形でなく done 扱い"
    new = _coerce_tasks(data.get("new_tasks"), existing=nodes)  # 既存 id と衝突しないよう正規化
    if data.get("decision") == "replan" and new:
        return "replan", new, str(data.get("reason", ""))
    return "done", [], str(data.get("reason", "done"))


# --------------------------------------------------------------------------
# orchestrate
# --------------------------------------------------------------------------
def _plan_strategy(args):
    review = getattr(args, "review", "auto")  # 'auto'/True/False の三値
    if args.planner == "flow-planner":
        return plan_strategy_flow_planner(args.request, args.model, review)
    if args.planner == "kiro":
        return plan_strategy_kiro(args.request, args.model, review)
    return plan_strategy_stub(args.request, review)


def _continue(args, request, nodes, results, iteration, strategy=None):
    mf = int(getattr(args, "max_fanout", 50) or 50)
    # 計画時に確定した review 判断を再利用（resume・継続でも一貫させる）。
    # CLI で明示指定（True/False）があればそれを優先。
    cli = getattr(args, "review", "auto")
    if isinstance(cli, bool):
        review = cli
    elif strategy and "review" in strategy:
        review = bool(strategy["review"])
    else:
        review = _review_decision(cli, (strategy or {}).get("patterns", []))
    if args.executor == "kiro":
        return continue_kiro(request, nodes, results, iteration, mf, review)
    return continue_stub(request, nodes, results, iteration, mf, review)


def _node_entry(t):
    return {"goal": t["goal"], "deps": t["deps"], "kind": t.get("kind", "work")}


def _collapse_split_successors(nodes: dict) -> dict:
    """split は実行時 fan-out で map→reduce を生成するのが正典。planner が split の
    後段に静的な work/reduce を付けると fan-out と二重化し、意図を失った map と
    重複 reduce が並走する。fan-out 前（<split>-reduce 未生成）に限り、split に
    （推移的に）依存する静的後段ノードを除去する。"""
    splits = {i for i, n in nodes.items()
              if n.get("kind") == "split" and f"{i}-reduce" not in nodes}
    if not splits:
        return nodes
    tainted, changed = set(splits), True
    while changed:
        changed = False
        for i, n in nodes.items():
            if i in tainted:
                continue
            if any(d in tainted for d in n.get("deps", [])):
                tainted.add(i)
                changed = True
    for i in tainted - splits:  # split 自体は残し、後段だけ落とす
        nodes.pop(i, None)
    return nodes


def _sanitize_graph(nodes: dict) -> dict:
    """グラフ健全性検査: 未知の依存 ID を除去し、循環依存を断ち切る。
    planner（kiro）の誤出力や継続での追加に対する防御。"""
    _collapse_split_successors(nodes)
    ids = set(nodes)
    for n in nodes.values():
        n["deps"] = [d for d in n.get("deps", []) if d in ids and d != n.get("id")]
    # Kahn 法で到達可能順を求め、到達できないノード（循環）の残依存を落とす
    from collections import deque
    pending = {i: set(nodes[i]["deps"]) for i in ids}
    ready = deque(i for i in ids if not pending[i])
    done = set()
    while ready:
        x = ready.popleft()
        done.add(x)
        for i in ids:
            if x in pending[i]:
                pending[i].discard(x)
                if not pending[i] and i not in done and i not in ready:
                    ready.append(i)
    for i in ids:
        if i not in done:  # 循環に含まれる → 未解決の依存を断ち切る
            nodes[i]["deps"] = [d for d in nodes[i]["deps"] if d in done]
    return nodes


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
        # 要求から 6 パターンの組み合わせと並列数を選び、初期グラフを形作る
        strategy, tasks = _plan_strategy(args)
        graph = {"strategy": strategy,
                 "nodes": {t["id"]: _node_entry(t) for t in tasks},
                 "iteration": 0}
        _sanitize_graph(graph["nodes"])  # 未知依存・循環を弾く
        bus.write_graph(graph)
        for t in tasks:
            bus.write_task(t)
        bus.set_status("running")
        bus.event(who, "planned", patterns=strategy["patterns"],
                  parallelism=strategy["parallelism"], tasks=[t["id"] for t in tasks])
        bus.sync_push(f"plan run {args.run_id}: {strategy['patterns']} x{strategy['parallelism']}")
        log(who, f"戦略: patterns={strategy['patterns']} parallelism={strategy['parallelism']} "
                 f"（{strategy.get('reason','')}）")
        log(who, f"初期タスク: {[(t['id'], t.get('kind','work')) for t in tasks]}")
        iteration = 0

    # evaluator-optimizer ループ: 静止（claim 可能・実行中タスクが無い）→ パターン継続判断
    while True:
        graph = bus.read_graph()
        while not _quiesced(bus, graph["nodes"]):
            bus.sync_pull()
            time.sleep(args.poll)
            graph = bus.read_graph()
        bus.sync_pull()
        graph = bus.read_graph()
        nodes = graph["nodes"]
        results = {nid: (bus.read_result(nid) or {}) for nid in nodes}

        if iteration >= args.max_iterations:
            decision, new_tasks, reason = "done", [], f"max-iterations({args.max_iterations}) 到達"
        else:
            decision, new_tasks, reason = _continue(
                args, args.request, nodes, results, iteration, graph.get("strategy"))
        log(who, f"評価 #{iteration}: {decision} — {reason}")

        if decision == "replan" and new_tasks:
            iteration += 1
            for t in new_tasks:
                graph["nodes"][t["id"]] = _node_entry(t)
                bus.write_task({k: v for k, v in t.items() if k != "replaces"})
                # replaces 指定: 旧ノードを外し、旧ノードに依存する後続を新ノードへ付け替える
                old = t.get("replaces")
                if old and old in graph["nodes"]:
                    for n in graph["nodes"].values():
                        n["deps"] = [t["id"] if d == old else d for d in n.get("deps", [])]
                    del graph["nodes"][old]
            _sanitize_graph(graph["nodes"])  # 追加で混入した未知依存・循環を弾く
            graph["iteration"] = iteration
            bus.write_graph(graph)
            bus.set_status("running")
            bus.event(who, "replan", iteration=iteration, added=[t["id"] for t in new_tasks])
            bus.sync_push(f"replan #{iteration} run {args.run_id}: +{[t['id'] for t in new_tasks]}")
            log(who, f"再計画 #{iteration}: 追加タスク {[(t['id'], t.get('kind','work')) for t in new_tasks]}")
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
        "strategy": (bus.read_graph() or {}).get("strategy", {}),
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


def _quiesced(bus: Bus, nodes: dict) -> bool:
    """run が静止したか: 実行中(claimed)も、今すぐ claim 可能な pending も無い状態。
    依存が失敗してブロックされた pending は静止扱い（継続判断で付け替えられる）。"""
    for nid, node in nodes.items():
        st = bus.node_state(nid)
        if st == "claimed":
            return False
        if st == "pending" and deps_satisfied(bus, node):
            return False
    return True


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
        kind = node.get("kind", "work")
        if not bus.try_claim(nid, who, args.lease):
            continue  # 競り負け
        log(who, f"claim 成功: {nid} [{kind}] — {node['goal'][:55]}")
        bus.event(who, "claimed", node=nid)

        # 依存の成果は構造化データ込みの完全な result dict で渡す
        dep_results = _collect_dep_results(bus, node, kind)
        # 実行中は心拍で lease を延長し続け、長時間タスクでも再 claim されないようにする
        hb = Heartbeat(bus, nid, who, args.lease)
        hb.start()
        rdata = None
        try:
            if args.executor == "kiro":
                output, rdata = execute_kiro(kind, node["goal"], dep_results, args.model)
            else:
                output, rdata = execute_stub(kind, node["goal"], dep_results, args.model)
            rstatus = "done"
        except Exception as e:  # noqa: BLE001 — 結果として記録する
            output = f"実行エラー: {e}"
            rstatus = "failed"
        finally:
            hb.stop()

        bus.write_result(nid, who, rstatus, output, rdata)
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
        base += ["--git", args.git, "--git-branch", args.git_branch,
                 "--git-subdir", args.git_subdir or ""]
    mode = f"git:{args.git}@{args.git_branch}" if args.git else f"local:{bus_root}"

    procs = []
    orch = subprocess.Popen(base + [
        "orchestrate", "--request", args.request,
        "--planner", args.planner, "--executor", args.executor,
        "--max-iterations", str(args.max_iterations),
        "--max-fanout", str(args.max_fanout),
        *(["--review"] if args.review is True
          else ["--no-review"] if args.review is False else []),
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
def _daemon_lock_path(args) -> str:
    """バス単位のデーモン singleton 用ロックパス（バス外の一時領域）。"""
    if getattr(args, "git", None):
        key = f"git::{args.git}@{args.git_branch}/{args.git_subdir or ''}"
    else:
        key = "local::" + os.path.abspath(args.bus)
    h = hashlib.sha1(key.encode()).hexdigest()
    d = os.path.join(tempfile.gettempdir(), "kiro-flow-locks")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"daemon-{h}.lock")


def cmd_daemon(args) -> int:
    # 冪等化: 同一バスのデーモンが既に稼働していれば何もしない（多重起動しない）
    lock_path = _daemon_lock_path(args)
    lock_file = open(lock_path, "w")
    if fcntl is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            mode0 = f"git:{args.git}@{args.git_branch}" if args.git else f"local:{os.path.abspath(args.bus)}"
            print(f">>> kiro-flow daemon は既に稼働中です（{mode0}）。起動をスキップします。", flush=True)
            lock_file.close()
            return 0
        lock_file.write(str(os.getpid()))
        lock_file.flush()

    daemon_id = args.node_id or f"{socket.gethostname()}-{os.getpid()}"
    bus = make_bus(args, f"daemon-{_safe(daemon_id)}")
    me = self_path()
    base = [sys.executable, me, "--bus", os.path.abspath(args.bus), "--lease", str(args.lease)]
    if args.git:
        base += ["--git", args.git, "--git-branch", args.git_branch,
                 "--git-subdir", args.git_subdir or ""]
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
        "--max-fanout", str(args.max_fanout),
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
    if len(to_delete) == 0 and len(runs) > 0:
        oldest_h = max(_age_hours(m) for _, m in metas) if metas else 0
        print(f"ヒント: --keep {args.keep} で全件保護中、最古 run は {oldest_h:.1f}h前。"
              f" --keep 0 --older-than 0 で全件を対象にできます。")
    return 0


# --------------------------------------------------------------------------
# status — 状態表示。既定は 1 回表示、--follow でライブ監視（tmux ペイン向け）
# --------------------------------------------------------------------------
_STATE_GLYPH = {"done": "✓", "failed": "✗", "claimed": "▶", "pending": "○", "unknown": "·"}


def _progress_bar(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[" + "·" * width + "] 0/0"
    filled = int(width * done / total)
    pct = int(100 * done / total)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {done}/{total} ({pct}%)"


def _node_depth(nid, nodes, memo):
    if nid in memo:
        return memo[nid]
    memo[nid] = 0  # 循環ガード（_sanitize_graph 済みだが念のため）
    deps = [d for d in nodes.get(nid, {}).get("deps", []) if d in nodes]
    d = 0 if not deps else 1 + max(_node_depth(x, nodes, memo) for x in deps)
    memo[nid] = d
    return d


def _elapsed(meta) -> str:
    a = meta.get("created_at")
    b = meta.get("updated_at") or now_iso()
    try:
        ta = datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
        tb = datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
        s = int((tb - ta).total_seconds())
        return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"
    except (TypeError, ValueError):
        return "-"


def _render_status(bus, run_id, events):
    """公式 Dynamic Workflows 風のダッシュボード表示。
    進捗バー / エージェント（タスク）状態ツリー / 直近アクティビティ / 最終サマリ。"""
    graph = bus.read_graph()
    status = bus.get_status()
    meta = bus.run_meta(run_id) if hasattr(bus, "run_meta") else (read_json(bus.meta_path) or {})
    nodes = (graph or {}).get("nodes", {})

    states = {nid: bus.node_state(nid) for nid in nodes}
    counts = {}
    for st in states.values():
        counts[st] = counts.get(st, 0) + 1
    total = len(nodes)
    done = counts.get("done", 0) + counts.get("failed", 0)

    L = []
    L.append(f"╭─ kiro-flow ── run {run_id} ── [{(status or '?').upper()}]  ⏱ {_elapsed(meta)}")
    if meta.get("request"):
        L.append(f"│  request : {meta['request'][:78]}")
    if graph and graph.get("strategy"):
        s = graph["strategy"]
        pats = " + ".join(s.get("patterns", []) or [])
        L.append(f"│  strategy: {pats}   ‖parallel={s.get('parallelism','?')}"
                 f"   iter={graph.get('iteration', 0)}")
    if total:
        L.append(f"│  progress: {_progress_bar(done, total)}")
        order = ("done", "claimed", "pending", "failed", "unknown")
        agentline = "  ".join(f"{_STATE_GLYPH[k]}{k}={counts[k]}" for k in order if counts.get(k))
        L.append(f"│  agents  : {total}   {agentline}")
        L.append("├─ tasks")
        memo = {}
        ordered = sorted(nodes, key=lambda n: (_node_depth(n, nodes, memo), n))
        for nid in ordered:
            node = nodes[nid]
            g = _STATE_GLYPH.get(states[nid], "·")
            indent = "  " * _node_depth(nid, nodes, memo)
            res = bus.read_result(nid) or {}
            who = res.get("who", "")
            dep = (" ← " + ",".join(node.get("deps", []))) if node.get("deps") else ""
            who_s = f"  @{who}" if who else ""
            L.append(f"│  {g} {indent}{nid} [{node.get('kind','work')}]{dep}{who_s}")
    else:
        L.append("│  (グラフ未生成 — 計画中)")

    if events:
        evs = bus.recent_events(events)
        if evs:
            L.append("├─ activity")
            for e in evs:
                ts = (e.get("ts", "") or "")[11:19]  # HH:MM:SS
                detail = e.get("node", "") or (",".join(e.get("tasks", [])) if e.get("tasks") else "")
                L.append(f"│  {ts}  {e.get('who',''):<14} {e.get('kind',''):<8} {detail}")

    if status in TERMINAL:
        final = read_json(bus.final_path)
        if final:
            L.append("├─ result")
            for line in final.get("summary", "").splitlines()[:20]:
                L.append(f"│  {line}")
    L.append("╰─")
    return status, "\n".join(L)


def _resolve_run_id(args) -> str | None:
    """--run-id 未指定時に最新 run を自動選択（done/failed 含む）。
    見つからなければ None を返す。"""
    probe = make_bus(args, "status-viewer")
    probe.sync_pull()
    runs = probe.list_runs()
    if not runs:
        return None
    metas = [(rid, probe.run_meta(rid)) for rid in runs]
    metas.sort(key=lambda x: x[1].get("created_at", x[0]), reverse=True)
    return metas[0][0]


def cmd_status(args) -> int:
    # --list: run 一覧を表示して終了
    if getattr(args, "list", False):
        probe = make_bus(args, "status-viewer")
        probe.sync_pull()
        runs = probe.list_runs()
        if not runs:
            print("run がありません。")
            return 0
        metas = [(rid, probe.run_meta(rid)) for rid in runs]
        metas.sort(key=lambda x: x[1].get("created_at", x[0]), reverse=True)
        for rid, meta in metas:
            req = meta.get("request", "")[:50]
            print(f"  {rid}  status={meta.get('status','?'):<8}  "
                  f"created={meta.get('created_at','?')}  req={req}")
        return 0

    # run_id が未指定の場合、最新の run を自動選択（終了済み含む）
    if not args.run_id:
        resolved = _resolve_run_id(args)
        if not resolved:
            print("エラー: run が見つかりません。まず kiro-flow run を実行してください。",
                  file=sys.stderr)
            return 1
        args.run_id = resolved
        print(f"(run_id 未指定 — 最新の run を表示: {args.run_id})", file=sys.stderr)

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
    # 設定値の優先順位: CLI > 設定ファイル(kiro-flow.yaml) > 組み込み既定。
    # 設定ファイル対象のオプションは既定 None にし、parse 後 resolve_config で確定する。
    p.add_argument("--config", default=None,
                   help="設定ファイルのパス（未指定なら CWD → ~/.kiro の kiro-flow.{yaml,yml,json}）")
    p.add_argument("--bus", default=None,
                   help="ローカルバスのルート / git モードでは各ノードのクローン親ディレクトリ")
    p.add_argument("--run-id", default=None, help="run 識別子")
    p.add_argument("--git", default=None,
                   help="共有 git リポジトリ URL/パス。指定で複数 PC 分散モードになる")
    p.add_argument("--git-branch", default=None, help="バスに使う git ブランチ（既定 main）")
    p.add_argument("--git-subdir", default=None,
                   help="リポジトリ内のバスにするサブディレクトリ（既定: リポジトリ直下）")
    p.add_argument("--lease", type=float, default=None,
                   help="claim のリース秒数（超過すると他ノードが再 claim 可能。既定 1800）")
    # サブコマンド未指定なら daemon として扱う（required=False）
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="単発実行。既存 --run-id なら再開、無ければ新規（状態で自動判断）")
    run.add_argument("request", nargs="?", default=None,
                     help="ワークフローへの要求（再開時は省略可）")
    run.add_argument("--workers", type=int, default=None)
    run.add_argument("--planner", choices=["kiro", "stub", "flow-planner"], default=None)
    run.add_argument("--executor", choices=["kiro", "stub"], default=None)
    run.add_argument("--max-iterations", type=int, default=None,
                     help="再計画（evaluator-optimizer）の最大反復回数")
    run.add_argument("--max-fanout", type=int, default=None,
                     help="データ駆動 fan-out の最大展開数（既定 50）")
    run.add_argument("--review", dest="review", action="store_const", const=True, default=None,
                     help="統合（synthesize/reduce）の前に検証 gate を必ず挟む（既定: 集約パターンで自動）")
    run.add_argument("--no-review", dest="review", action="store_const", const=False,
                     help="自動の検証 gate を無効化する")
    run.add_argument("--model", default=None)
    run.add_argument("--poll", type=float, default=None)
    run.set_defaults(func=cmd_run)

    orch = sub.add_parser("orchestrate", help="計画役")
    orch.add_argument("--request", required=True)
    orch.add_argument("--planner", choices=["kiro", "stub", "flow-planner"], default=None)
    orch.add_argument("--executor", choices=["kiro", "stub"], default=None,
                      help="評価役（evaluator）に使うバックエンド")
    orch.add_argument("--max-iterations", type=int, default=None)
    orch.add_argument("--max-fanout", type=int, default=None)
    orch.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    orch.add_argument("--no-review", dest="review", action="store_const", const=False)
    orch.add_argument("--node-id", default="orchestrator")
    orch.add_argument("--model_opt", dest="model", default=None)
    orch.add_argument("--poll", type=float, default=None)
    orch.set_defaults(func=cmd_orchestrate)

    work = sub.add_parser("work", help="ワーカー役")
    work.add_argument("--node-id", default=f"{socket.gethostname()}-{os.getpid()}")
    work.add_argument("--executor", choices=["kiro", "stub"], default=None)
    work.add_argument("--model_opt", dest="model", default=None)
    work.add_argument("--poll", type=float, default=None)
    work.add_argument("--keep-alive", action="store_true", help="run 完了後も待機し続ける")
    work.add_argument("--idle-exit", action="store_true",
                      help="claim 可能タスクが無くなったら終了（デーモンのオンデマンド起動用）")
    work.set_defaults(func=cmd_work)

    dm = sub.add_parser("daemon", help="常駐し、要求に応じ orchestrator/worker をオンデマンド起動")
    dm.add_argument("--node-id", default=None, help="デーモン識別子（既定: host-pid）")
    dm.add_argument("--max-workers", type=int, default=None,
                    help="このデーモンが同時に走らせる worker 上限（既定 4）")
    dm.add_argument("--planner", choices=["kiro", "stub", "flow-planner"], default=None)
    dm.add_argument("--executor", choices=["kiro", "stub"], default=None)
    dm.add_argument("--max-iterations", type=int, default=None)
    dm.add_argument("--max-fanout", type=int, default=None)
    dm.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    dm.add_argument("--no-review", dest="review", action="store_const", const=False)
    dm.add_argument("--model", default=None)
    dm.add_argument("--poll", type=float, default=None)
    dm.set_defaults(func=cmd_daemon)

    sb = sub.add_parser("submit", help="要求を inbox に投入（デーモンが拾う）")
    sb.add_argument("request", help="ワークフローへの要求")
    sb.set_defaults(func=cmd_submit)

    st = sub.add_parser("status", help="run の状態表示（既定 1 回 / --follow でライブ監視）")
    st.add_argument("--follow", "-f", action="store_true", help="ライブ監視（tmux ペイン向け）")
    st.add_argument("--interval", type=float, default=1.0, help="更新間隔（秒, --follow 時）")
    st.add_argument("--events", type=int, default=8, help="表示する直近イベント数")
    st.add_argument("--until-done", action="store_true", help="run 完了で自動終了（--follow 時）")
    st.add_argument("--list", "-l", action="store_true", help="run 一覧を表示して終了")
    st.set_defaults(func=cmd_status)

    gc = sub.add_parser("gc", help="古い run を掃除（対応する inbox 要求・claim も削除）")
    gc.add_argument("--older-than", type=float, default=7.0, help="この日数より古い run が対象")
    gc.add_argument("--keep", type=int, default=3, help="新しい順にこの件数は無条件で保護")
    gc.add_argument("--status", default=None, help="この status の run のみ対象（例: done）")
    gc.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示")
    gc.set_defaults(func=cmd_gc)

    args = p.parse_args()
    # CLI 未指定の設定値を設定ファイル→組み込み既定で確定（CLI > config > 既定）
    resolve_config(args)
    # 子プロセスから渡る空文字の --model_opt は「モデル指定なし」を意味する
    if getattr(args, "model", None) == "":
        args.model = None
    # サブコマンド未指定 → daemon として処理
    if getattr(args, "func", None) is None:
        args.node_id = getattr(args, "node_id", None)
        return cmd_daemon(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
