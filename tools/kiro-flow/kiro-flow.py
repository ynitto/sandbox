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
import atexit
import contextlib
import hashlib
import inspect
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

# このツールがスキルリポジトリ内に置かれているサブディレクトリ（自動アップデートの参照先）。
# 自動アップデートは update_repo のこのパス以下だけを temp 領域へ sparse-checkout して
# install.sh を実行する（doctor と同じ流儀で、操作は決定的・無関係ファイルは取得しない）。
TOOL_SUBDIR = "tools/kiro-flow"
# スキルリポジトリ（git URL/パス）の既定。空なら install.py が生成する skill-registry.json から
# 自動解決する（repositories.origin.url → install_dir）。設定ファイルの update_repo で明示も可。
DEFAULT_UPDATE_REPO = ""
# skill-registry.json を探すエージェントホーム（install.py の AGENT_DIRS に対応）。
_AGENT_HOME_DIRS = (".kiro", ".claude", ".copilot", ".codex")

# 環境ごとに変わる値の組み込み既定。設定ファイルのキーもこの名前（snake_case）。
CONFIG_DEFAULTS = {
    "bus": "./.kiro-flow",
    "git": None,
    "git_branch": "main",
    "git_subdir": "",
    "lock_dir": None,   # daemon singleton ロックの置き場（外部 daemon の発見性を担保。既定 tempdir 配下）
    "repos": [],        # 成果物リポジトリ URL。worker が temp 領域へ clone してから作業し、作業後に消す
    "lease": 1800.0,
    "poll": 2.0,
    "model": None,
    "planner": "flow-planner",
    "executor": "kiro",
    "granularity": "finest",   # 分解の細かさ: coarse(現状)/fine(1段細)/finest(2段細・既定)
    "exemplar_first": False,   # map-reduce で「1件先行→検証ゲート→残り展開」の見本先行分解にする
    "max_workers": 4,
    "max_iterations": 3,
    "max_fanout": 50,
    # judge/評価役のサーキットブレーカー: 同一系統（verify/失敗）の作り直しをこの回数で打ち切る。
    # 達成不可能な完了条件で無限に再タスクを生み続けるのを防ぐ（max_iterations と二重ガード）。
    "max_retries": 3,
    # kiro-cli へ argv で渡すプロンプトの最大バイト数。超過分は一時ファイルへ退避し参照渡しに
    # 切り替える（依存成果物が大きいときに OS の ARG_MAX に達して起動失敗するのを防ぐ）。
    "argv_limit": 100000,
    # kiro-cli 1 呼び出しのタイムアウト秒（既定 600、0/負で無効化）。None なら環境変数
    # KIRO_FLOW_KIRO_TIMEOUT → 600 にフォールバック。ハングした kiro-cli を止める唯一の手段。
    "kiro_timeout": None,
    # stub executor の擬似実行スリープ上限秒（既定 1〜5 秒）。None なら環境変数
    # KIRO_FLOW_STUB_SLEEP_MAX → 5 にフォールバック。テスト/動作確認では 0 で高速化できる。
    "stub_sleep_max": None,
    "review": "auto",  # auto: 集約パターンで自動有効 / True/False: 明示上書き
    "workers": 2,
    # 一時ファイルの自動クリーンアップ（daemon ループ内で定期実行）
    "cleanup_interval": 3600.0,  # 掃除の実行間隔（秒）。0 以下で無効化
    "cleanup_age": 24.0,         # 孤立クローンを掃除するまでのアイドル時間（時間）
    # 作業後に sparse-checkout クローンを削除するか（True で削除 / False で残して再利用）
    "cleanup_clone": True,
    "cleanup_per_node": False,   # 各ノード完了後に成果物リポジトリの clone を即削除（長命 worker のディスク抑制）
    # --- 自動アップデート（既定 on）。スキルリポジトリ main の更新を daemon のアイドル時に取り込む ---
    # 更新元は skill-registry.json から自動解決（repositories.origin.url → install_dir）。
    # アイドル時に git ls-remote で main の先頭コミットを確認し、適用済みと違えば temp 領域へ
    # sparse-checkout（tools/kiro-flow/ だけ）→ install.sh 実行 → graceful 再起動する。
    # 起動直後の最初のアイドルでも 1 回実施する（停止中に入った更新を取りこぼさない）。
    "update_enabled": True,              # 自動アップデートの ON/OFF（false で完全無効・既定 on）
    "update_check_interval": 21600.0,    # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
    "update_repo": DEFAULT_UPDATE_REPO,  # スキルリポジトリ（git URL/パス）。空なら skill-registry.json から自動解決
    "update_branch": "main",             # 追従するブランチ
    "update_subdir": TOOL_SUBDIR,        # リポジトリ内のこのツールのサブディレクトリ
    "update_installer": "install.sh",    # サブディレクトリ内で実行するインストーラ
    # executor プラグインの追加検索ディレクトリ（既定の検索先に加えて優先探索する）。
    "executor_dir": None,
    # gitlab executor プラグイン（opt-in のワーカーバス）の設定。executor: gitlab を選んだ
    # ときだけ使われ、この dict が JSON 化され環境変数経由でプラグインに渡される。
    # タスクを GitLab イシュー化し、リモートのワーカーが拾って実行する。status:approved
    # ラベルが付く（レビュー承認）まで起票したイシューをポーリングし完了とみなす。
    # イシュー API は GitLab REST を stdlib で直叩き（gl.py 不要・フォールバックもしない）。
    # 起票先 URL は repo_url が権威（git origin へ流れない）。トークンはここには置かず、
    # gl.py と同じ場所（connections.yaml / 環境変数 GITLAB_TOKEN・GL_TOKEN / シェル rc）から解決する。
    "gitlab": {
        "conn_label": "default",            # connections.yaml の接続ラベル（トークン解決に使用）
        "repo_url": "",                     # 起票先プロジェクト URL（権威）。必ずこの URL を使う
        "labels": "status:open,assignee:any",  # 起票するイシューに付ける初期ラベル
        "priority": "priority:normal",      # 付与する優先度ラベル（空文字で付けない）
        "poll_interval": 30.0,              # イシューのポーリング間隔（秒）
        "timeout": 86400.0,                 # approved 待ちのタイムアウト（秒）。0/負で無限待ち
        "approved_label": "status:approved",  # この状態に達したら完了とみなす（= 受け入れ承認）
        "done_label": "status:done",        # approved 以外に完了とみなすラベル
    },
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
        self.run_id = run_id
        self.runs_root = os.path.join(root, "runs")
        self.inbox_dir = os.path.join(root, "inbox")
        self.inbox_claims_dir = os.path.join(root, "inbox", "claims")
        self.run_dir = os.path.join(root, "runs", run_id)
        self.tasks_dir = os.path.join(self.run_dir, "tasks")
        self.claims_dir = os.path.join(self.run_dir, "claims")
        self.results_dir = os.path.join(self.run_dir, "results")
        self.artifacts_dir = os.path.join(self.run_dir, "artifacts")
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

    def ensure_run(self, request: str, repos: "list[str] | None" = None) -> None:
        self.ensure_dirs()
        if read_json(self.meta_path) is None:
            write_json_atomic(self.meta_path, {
                "request": request,
                "repos": list(repos or []),   # 成果物リポジトリ（worker が clone してから作業）
                "status": "planning",
                "created_at": now_iso(),
            })

    def run_repos(self) -> "list[str]":
        """この run の成果物リポジトリ URL 一覧（meta に記録、worker が clone する）。"""
        meta = read_json(self.meta_path) or {}
        r = meta.get("repos")
        return list(r) if isinstance(r, list) else []

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

    # --- 中間成果物（ファイル）プロトコル ---
    #
    # output/data（JSON）に乗らない大きな成果物（生成ファイル等）は、ノードごとの
    # 決定的なディレクトリ artifacts/<node-id>/ に置く。パスが node-id から一意に
    # 決まるので、後続タスクは依存ノードの同じパスを読んで成果物を発見できる。
    # （バスのファイルとして push/pull で同期されるため分散でも同じパスで参照可能。）
    def node_artifact_dir(self, node_id: str) -> str:
        return os.path.join(self.artifacts_dir, node_id)

    def ensure_artifact_dir(self, node_id: str) -> str:
        d = self.node_artifact_dir(node_id)
        os.makedirs(d, exist_ok=True)
        return d

    def list_artifacts(self, node_id: str) -> "list[str]":
        """ノードの成果物ディレクトリ内のファイル絶対パス一覧（無ければ空）。"""
        d = self.node_artifact_dir(node_id)
        if not os.path.isdir(d):
            return []
        out = []
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                out.append(os.path.join(dirpath, fn))
        return sorted(out)

    # --- 結果 ---
    def result_path(self, node_id: str) -> str:
        return os.path.join(self.results_dir, f"{node_id}.json")

    def has_result(self, node_id: str) -> bool:
        return os.path.exists(self.result_path(node_id))

    def read_result(self, node_id: str):
        return read_json(self.result_path(node_id))

    def write_result(self, node_id: str, who: str, status: str, output: str,
                     data=None, artifacts=None) -> None:
        rec = {
            "id": node_id,
            "who": who,
            "status": status,
            "output": output,
            "finished_at": now_iso(),
        }
        if data is not None:  # 構造化成果（任意）。エージェント間を JSON で流す
            rec["data"] = data
        if artifacts:  # 生成した中間成果物（run_dir 相対パス）。後続が参照できる
            rec["artifacts"] = list(artifacts)
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

    def mark_run_failed(self, run_id: str, reason: str = "") -> bool:
        """run_id がまだ終端でなければ status を failed に確定する。
        orchestrator が done を書く前に異常終了した（クラッシュ・kill 等）ケースを終端化し、
        result/status を待つ消費者（kiro-autonomous の submit 待ちなど）が永久待機に陥らないようにする。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "failed"
        meta["updated_at"] = now_iso()
        if reason:
            meta["failure_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def touch_run(self, run_id: str, lease_sec: float) -> None:
        """自分が orchestrator を回している run の生存リース（heartbeat）を更新する。
        これにより別デーモン／再起動後の自分が「この run は生きている（owner が駆動中）」と判定でき、
        孤児回収で誤って failed にしない。終端済み／不在の run には何もしない。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return
        meta["orch_lease_until"] = time.time() + lease_sec
        meta["heartbeat_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)

    def run_is_orphaned(self, run_id: str, grace_sec: float) -> bool:
        """run が非終端なのに生存リースが切れている（owning daemon/orchestrator が消失した）か。
        owner が一度でも heartbeat していれば orch_lease_until で判定する。リース未記録の古い run
        （owner が heartbeat する前に死んだ／本変更前から残る run）は age を grace と比較して判定する。"""
        meta = read_json(self.run_view(run_id).meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        lease = meta.get("orch_lease_until")
        if isinstance(lease, (int, float)):
            return lease < time.time()
        return _age_hours(meta) * 3600.0 > grace_sec

    # --- inbox（要求キュー）と要求 claim ---
    def submit_request(self, req_id: str, request: str, submitter: str,
                       repos: "list[str] | None" = None) -> None:
        write_json_atomic(os.path.join(self.inbox_dir, f"{req_id}.json"), {
            "id": req_id,
            "request": request,
            "submitter": submitter,
            "repos": list(repos or []),   # 成果物リポジトリを daemon の orchestrate へ伝搬する
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

    # 自前管理のバスクローンに付ける目印（git config）。ユーザーのフルチェックアウトを
    # 誤って sparse-checkout で間引かないため、再利用は「この目印を持つ／既に sparse 済みの
    # 自前バスクローン」に限定する。
    MANAGED_FLAG = "kiro-flow.busclone"

    def _git_env(self) -> dict:
        """`git -C workdir` が workdir の親ディレクトリへ遡ってリポジトリを探さないようにする環境。
        GIT_CEILING_DIRECTORIES に workdir の親を指定し、workdir 直下に .git が無い場合でも
        親リポジトリを掴んで sparse-checkout 等を波及させる事故を物理的に防ぐ（多重防御）。"""
        env = dict(os.environ)
        parent = os.path.dirname(os.path.realpath(self.workdir)) or "/"
        ceil = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = parent + (os.pathsep + ceil if ceil else "")
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        return env

    def _git(self, args, check=True):
        p = subprocess.run(["git", "-C", self.workdir] + args, capture_output=True, text=True,
                           env=self._git_env())
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {p.stderr.strip()[:300]}")
        return p

    def _is_own_repo_root(self) -> bool:
        """workdir が「自分自身を root とする git 作業ツリー」か（親リポジトリを掴んでいない）。
        _git_env の ceiling により、workdir 直下に .git が無ければ rev-parse は失敗するので親を拾わない。"""
        top = self._git(["rev-parse", "--show-toplevel"], check=False).stdout.strip()
        return bool(top) and os.path.realpath(top) == os.path.realpath(self.workdir)

    def _origin_matches(self) -> bool:
        origin = self._git(["remote", "get-url", "origin"], check=False).stdout.strip()
        return origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))

    def _is_managed_bus_clone(self) -> bool:
        """workdir が「kiro-flow が管理する self.remote の sparse バスクローン」か。
        これを満たすときのみ sparse-checkout/checkout を適用してよい。ユーザーのフルチェックアウト
        （目印も sparse 設定も無い）を間引いて作業ファイルを隠す事故を防ぐためのガード。"""
        if not self._is_own_repo_root() or not self._origin_matches():
            return False
        # 1) 自前で付けた目印があれば管理クローン
        if self._git(["config", "--get", self.MANAGED_FLAG], check=False).stdout.strip() == "1":
            return True
        # 2) 目印が無くても、既に sparse-checkout 済みなら過去の自前バスクローンとみなし採用（後方互換）。
        #    ユーザーのフルチェックアウトは sparseCheckout 未設定なので false になり、間引かれない。
        sparse = self._git(["config", "--get", "core.sparseCheckout"], check=False).stdout.strip()
        return sparse.lower() == "true"

    def _ensure_clone(self) -> None:
        # workdir が自前管理の sparse バスクローンなら再利用。そうでなければ新規 clone する。
        # （ユーザーのフルチェックアウトや親/別リポジトリへ sparse-checkout を効かせて作業ツリーを
        #   壊さないため、「自前のバスクローンである」ことを確認してからでないと sparse-checkout に進まない。）
        if not self._is_managed_bus_clone():
            if os.path.isdir(self.workdir) and os.listdir(self.workdir):
                # 既存の非空ディレクトリ（ユーザーの作業チェックアウト・親/別リポジトリ等）は上書きせず中断。
                # ここで sparse-checkout すると subdir 以外の追跡ファイルを作業ツリーから隠してしまう。
                raise RuntimeError(
                    f"クローン先 {self.workdir} が空でない既存ディレクトリ（kiro-flow 管理外のクローン/作業"
                    f"ツリー）です。sparse-checkout で作業ファイルを隠す事故を防ぐため中断します"
                    f"（専用の空ディレクトリを --bus に指定してください）。")
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
            if not self._is_own_repo_root():
                # clone 後も workdir 自身がリポジトリのルートでなければ、以降の sparse-checkout が
                # 親リポジトリへ波及しうる。安全側に倒して中断する。
                raise RuntimeError(
                    f"git clone 後も {self.workdir} がクローンのルートになっていません。"
                    "親リポジトリへの sparse-checkout を防ぐため中断します。")
            self._git(["config", self.MANAGED_FLAG, "1"])   # 自前管理クローンの目印
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

    def cleanup_clone(self) -> None:
        """作業後にこのノード専用の sparse-checkout クローンを丸ごと削除する。
        共有リポジトリ本体ではなく、ローカルの作業ツリー（.git を含むクローン）だけを
        対象にする。push 済みのデータはリモートにあるため、消しても情報は失われない。"""
        wd = os.path.abspath(self.workdir)
        if os.path.isdir(os.path.join(wd, ".git")):
            shutil.rmtree(wd, ignore_errors=True)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


# 作業後に削除する候補の GitBus クローン（make_bus で登録し main の finally で掃除）
_active_clones: list = []


def make_bus(args, node_id: str) -> Bus:
    """--git があれば GitBus（ノードごとに専用クローン）、無ければローカル Bus。"""
    run_id = args.run_id or "_"  # gc 等 run 横断コマンドでは run_id 不要
    if getattr(args, "git", None):
        clone_dir = os.path.join(os.path.abspath(args.bus), _safe(node_id))
        bus = GitBus(clone_dir, run_id, remote=args.git, branch=args.git_branch,
                     subdir=getattr(args, "git_subdir", "") or "")
        _active_clones.append(bus)  # 作業後に cleanup_clone で消す
        return bus
    return Bus(os.path.abspath(args.bus), run_id)


def cleanup_active_clones() -> None:
    """このプロセスが作った sparse-checkout クローンを作業後にまとめて削除する。"""
    while _active_clones:
        bus = _active_clones.pop()
        try:
            bus.cleanup_clone()
        except Exception:  # noqa: BLE001 — 掃除失敗で終了処理を止めない
            pass


# --------------------------------------------------------------------------
# 成果物リポジトリ — worker が temp 領域へ clone してから作業し、作業後に必ず消す。
#   push が必要なもの・中身を読む必要があるもの（複数可）を、orchestrator の
#   作業ツリーを汚さずに分離して扱うための仕組み。clone はプロセス内で再利用する。
# --------------------------------------------------------------------------
_work_repos_cache: "dict[str, str]" = {}   # url -> clone パス（""=clone 失敗）
_work_repos_root: "str | None" = None


def _repo_name(url: str) -> str:
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return _safe(base) or "repo"


def parse_repo_token(token: str) -> dict:
    """`--repo` トークンを構造化 spec に正規化する。素の URL でも、kiro-autonomous が付ける
    JSON（{url,name,path,base,target,role,readonly,desc}）でも受ける（後方互換）。
    同一 URL でも path/役割が違えば別 spec（モノレポの役割別フォルダ）。

    `role`（成果物リポジトリのロール・executor 横断の正準スキーマ）:
      - `write` … 成果物をコミットする単一の対象（push 先）。
      - `read`  … 参照のみ（clone するが push しない）。複数可。
      - `auto`/未指定 … planner が判別しなかった。executor が決める（classify_repos）。
    後方互換: `readonly: true` は `role: read` と同義（role 未指定時に適用）。"""
    spec = {"url": "", "name": "", "path": "", "base": "",
            "target": "", "role": "", "readonly": False, "desc": ""}
    token = (token or "").strip()
    if token.startswith("{"):
        try:
            d = json.loads(token)
        except (ValueError, TypeError):
            d = None
        if isinstance(d, dict) and d.get("url"):
            for k in ("url", "name", "path", "base", "target", "role", "desc"):
                if d.get(k):
                    spec[k] = str(d[k]).strip()
            spec["readonly"] = bool(d.get("readonly"))
            return spec
    spec["url"] = token                           # 素の URL（メタ無し）
    return spec


def repo_role(spec: dict) -> str:
    """spec の実効ロールを返す（write/read/auto）。executor 横断で同じ判定を使う。
    明示 `role` を優先し、無ければ `readonly`→read、それも無ければ auto（未決定）。"""
    r = str(spec.get("role") or "").strip().lower()
    if r in ("write", "read"):
        return r
    if spec.get("readonly"):
        return "read"
    return "auto"


def classify_repos(specs: "list[dict]") -> dict:
    """成果物リポジトリのロールを解決する正準リゾルバ（どの executor もこの結果に従う）。

    規約: **成果物をコミットするのは単一（write）**、**参照は複数可（read）**、
    planner が判別しなかった `auto` は **executor がここで決める**。

    解決則:
      - 明示 write が 1 つ以上 → 先頭を write、残りの write と全 auto は read に降格
        （単一 write 規則。`extra_writes` に降格元を残す）。
      - 明示 write が無い:
          - auto がちょうど 1 つで read が無い → その 1 つを write（単一 repo は自明に成果物先）。
          - それ以外（auto 複数 / read 併存）→ write 未定（`undecided` に auto を残す）。
            LLM executor は指示で 1 つだけ選び、決定的処理（capture）は変更のある候補を採用する。
    返り値: {"write": spec|None, "reads": [spec], "undecided": [spec], "extra_writes": [spec]}。"""
    writes = [s for s in specs if repo_role(s) == "write"]
    reads = [s for s in specs if repo_role(s) == "read"]
    autos = [s for s in specs if repo_role(s) == "auto"]
    if writes:
        return {"write": writes[0], "reads": reads + writes[1:] + autos,
                "undecided": [], "extra_writes": writes[1:]}
    if len(autos) == 1 and not reads:
        return {"write": autos[0], "reads": [], "undecided": [], "extra_writes": []}
    return {"write": None, "reads": reads, "undecided": autos, "extra_writes": []}


def repo_token_id(token: str) -> str:
    """`--repo` トークンの参照識別子（name 優先、無ければ URL 末尾名）。ノードへの repo 割当はこの id で行う。"""
    spec = parse_repo_token(token)
    return spec.get("name") or _repo_name(spec["url"]) or spec["url"]


def resolve_node_repos(node: dict, run_tokens: "list[str]") -> "list[str]":
    """このノードが clone すべき repo トークンを決める（必要なものだけを必要な時に clone するための判断）。
    - run に repo が無ければ空。
    - ノードに `repos` キーが無い（未注釈・後方互換）→ run の全 repo。
    - `repos` がある → その id（name/url）に一致する run トークンだけ（空配列＝何も clone しない）。"""
    if not run_tokens:
        return []
    if "repos" not in node:
        return list(run_tokens)               # 未注釈は全 repo（安全側・後方互換）
    want = node.get("repos") or []
    if not want:
        return []                             # 明示的に「不要」＝ clone しない
    by_id: "dict[str, str]" = {}
    for tok in run_tokens:
        by_id.setdefault(repo_token_id(tok), tok)
        url = parse_repo_token(tok)["url"]
        if url:
            by_id.setdefault(url, tok)
    out: "list[str]" = []
    seen: "set[str]" = set()
    for w in want:
        tok = by_id.get(str(w))
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _clone_repo(url: str, base: str, dest: str) -> str:
    """url を dest へ clone する。base 指定があればそのブランチを checkout（無ければ既定にフォールバック）。
    成功で dest、失敗で "" を返す。"""
    attempts = []
    if base:
        attempts.append(["git", "clone", "-b", base, url, dest])
    attempts.append(["git", "clone", url, dest])
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                return dest
        except (OSError, subprocess.SubprocessError):
            pass
        if os.path.exists(dest):                  # 失敗の残骸を消してからフォールバック
            shutil.rmtree(dest, ignore_errors=True)
    return ""


def ensure_work_repos(repos: "list[str]", node_id: str) -> "list[dict]":
    """成果物リポジトリを worker 専用の temp 領域へ clone する（URL 単位でプロセス内キャッシュ）。
    各 spec に clone 先パスを足した dict の列を返す（clone="" は clone 失敗）。base 指定があれば
    そのブランチを checkout する。作業後に cleanup_work_repos で必ず消す。"""
    global _work_repos_root
    out: "list[dict]" = []
    if not repos:
        return out
    if _work_repos_root is None:
        # 所有プロセスの pid を名前に埋める → SIGKILL 等で finally が走らず残った孤立 clone を
        # janitor（sweep_work_repo_dirs）が「pid 死亡」を根拠に安全に回収できる（稼働中は消さない）。
        _work_repos_root = tempfile.mkdtemp(
            prefix=f"kiro-flow-repos-{os.getpid()}-{_safe(node_id)}-")
    for token in repos:
        spec = parse_repo_token(token)
        url = spec["url"]
        if url in _work_repos_cache:              # 同一 URL は一度だけ clone（役割別 spec は指示で出し分け）
            out.append({**spec, "clone": _work_repos_cache[url]})
            continue
        stem = _safe(spec["name"]) or _repo_name(url)
        dest = os.path.join(_work_repos_root, stem)
        n = 2
        while os.path.exists(dest):               # 同名 repo の衝突回避
            dest = os.path.join(_work_repos_root, f"{stem}-{n}")
            n += 1
        path = _clone_repo(url, spec["base"], dest)
        _work_repos_cache[url] = path
        out.append({**spec, "clone": path})
    return out


def cleanup_work_repos() -> None:
    """worker が clone した成果物リポジトリを丸ごと削除する（作業後クリーンは必須）。"""
    global _work_repos_root
    if _work_repos_root and os.path.isdir(_work_repos_root):
        shutil.rmtree(_work_repos_root, ignore_errors=True)
    _work_repos_root = None
    _work_repos_cache.clear()


def _repo_line(c: dict) -> "list[str]":
    """1 リポジトリの clone 情報（ラベル・パス・ブランチ・フォルダ・説明）を行に整形する。"""
    label = (f"{c['name']} = " if c.get("name") else "") + c.get("url", "")
    lines = [f"  - {label} → {c.get('clone')}"]
    if c.get("base"):
        br = f"      作業ブランチ: {c['base']} から分岐"
        if c.get("target") and c["target"] != c["base"]:
            br += f"・push 先（MR/PR ターゲット）= {c['target']}"
        lines.append(br)
    if c.get("path"):
        lines.append(f"      フォルダ: {c['path']} 配下のみ変更すること")
    if c.get("desc"):
        lines.append(f"      内容: {c['desc']}")
    return lines


def repo_instruction(clones: "list[dict]") -> str:
    """clone 済みの成果物リポジトリをエージェントに伝える決定的な指示ブロック。

    ロール（classify_repos）に従い、**成果物をコミットする単一の対象（write）**と
    **参照のみ（read・複数可）**を明確に出し分ける。planner が判別しなかった（auto）場合は、
    『どれにコミットするかを 1 つだけ選べ』と executor（エージェント）に委ねる。
    この指示は call_executor 経由で executor へ goal とは別引数（repo_instruction）として渡る
    （gitlab executor はイシュー本文の独立した節に載せる。goal のタイトル/目的は汚さない）。"""
    have = [c for c in clones if c.get("clone")]
    if not have:
        failed = [c.get("url", "") for c in clones if not c.get("clone")]
        return ("【成果物リポジトリ】clone 失敗（手動取得が必要・push 不可の可能性）: "
                + ", ".join(failed)) if failed else ""
    res = classify_repos(have)
    lines = ["【成果物リポジトリ】このタスク用に clone 済みです。読み書きは必ず各パス内で行い、"
             "他の場所（orchestrator の作業ツリーなど）は編集しないこと。"]
    if res["write"]:
        lines.append("■ 成果物をコミットする対象（ここだけに commit・push する。単一）:")
        lines += _repo_line(res["write"])
    if res["reads"]:
        lines.append("■ 参照のみ（読むだけ。変更・commit・push しないこと）:")
        for c in res["reads"]:
            lines += _repo_line(c)
    if res["undecided"]:
        lines.append("■ 成果物のコミット先が未確定（planner が判別しませんでした）。"
                     "次のうち**最も関連の深い 1 つだけ**を選んでコミットし、残りは参照のみとすること:")
        for c in res["undecided"]:
            lines += _repo_line(c)
    if res["write"] or res["undecided"]:
        lines.append("コミットした対象は commit すること。ローカル実行では kiro-flow が成果ブランチへ "
                     "push してリンクを記録する（リモート委譲の場合は push して MR/PR を用意すること）。"
                     "なお、調査など成果物がコミットを伴わないタスクは、無理にコミットせず成果を本文に"
                     "まとめること（その場合はバス上の成果が納品物になる）。")
    else:
        lines.append("いずれも参照のみです。成果は本文にまとめること（バス上の成果が納品物になる）。")
    failed = [c.get("url", "") for c in clones if not c.get("clone")]
    if failed:
        lines.append("  ※ clone 失敗（必要なら手動で取得・push 不可の可能性）: " + ", ".join(failed))
    return "\n".join(lines)


# 決定的 commit の作者（環境の git config に依存せず再現可能にする）。
_DELIVERY_GIT_AUTHOR = ["-c", "user.name=kiro-flow", "-c", "user.email=kiro-flow@localhost"]


def _git_io(path: str, *args: str) -> "tuple[int, str, str]":
    """clone 内で git を実行し (returncode, stdout, stderr) を返す。"""
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _delivery_branch(spec: dict, run_id: str, node_id: str) -> str:
    """この repo への push 先（成果ブランチ）を決定的に決める。
    `target`（MR/PR ターゲット指定）があればそれ、無ければ `kiro-flow/<run>/<node>`。"""
    if spec.get("target"):
        return str(spec["target"])
    rid = re.sub(r"[^A-Za-z0-9._-]", "-", str(run_id))[:40]
    nid = re.sub(r"[^A-Za-z0-9._-]", "-", str(node_id))
    return f"kiro-flow/{rid}/{nid}"


def _capture_one(c: dict, run_id: str, node_id: str) -> "dict | None":
    """1 リポジトリの変更を決定的に捕捉する。変更があれば成果ブランチへ commit/push して
    delivery dict を返す。変更が無ければ None（＝コミットを伴わない＝納品物はバス上）。"""
    path = c.get("clone")
    if not path or not os.path.isdir(path):
        return None
    base = str(c.get("base") or "")
    branch = _delivery_branch(c, run_id, node_id)
    # 1. 未コミット変更を取り込む（編集だけして commit しなかった場合の保険）
    _, porcelain, _ = _git_io(path, "status", "--porcelain")
    committed_here = False
    if porcelain:
        _git_io(path, "add", "-A")
        rc, _, _ = _git_io(path, *_DELIVERY_GIT_AUTHOR, "commit", "-m", f"[kiro-flow] {node_id}")
        committed_here = rc == 0
    # 2. base から HEAD が進んでいるか（＝コミットが存在するか）を判定
    rc, head, _ = _git_io(path, "rev-parse", "HEAD")
    if rc != 0:
        return None
    ahead = committed_here
    ref = f"origin/{base}" if base else "origin/HEAD"
    rc_b, base_sha, _ = _git_io(path, "rev-parse", ref)
    if rc_b == 0:
        ahead = ahead or (head != base_sha)
    if not ahead:
        return None  # 変更なし（調査系など）→ 納品物はバス上の成果に委ねる
    # 3. 決定的な成果ブランチへ push（temp clone 削除前の永続化）
    rc_p, _, perr = _git_io(path, "push", "origin", f"HEAD:refs/heads/{branch}")
    d: dict = {"url": c.get("url", ""), "base": base, "target": c.get("target", ""),
               "branch": branch, "head_sha": head, "pushed": rc_p == 0, "changed": True}
    if c.get("name"):
        d["name"] = c["name"]
    if rc_p != 0:
        d["push_error"] = perr[:200]
        log(node_id, f"成果ブランチ push 失敗（{c.get('url','')} → {branch}）: {perr[:120]}")
    else:
        log(node_id, f"成果を push: {c.get('url','')} → {branch} @ {head[:8]}")
    return d


def capture_deliveries(clones: "list[dict]", run_id: str, node_id: str) -> "list[dict]":
    """エージェント実行後に、成果物リポジトリの状態を決定的に捕捉する。

    ロール（classify_repos）に従い、**成果物のコミットは単一の write 対象だけ**を push する。
    参照のみ（read）は決して push しない。planner 未確定（auto・undecided）のときは、
    エージェントが実際にコミットした候補（＝変更のある repo）を採用する（executor が決めた結果を尊重）。
    **成果物がコミットを伴うとは限らない**（調査系）ので、変更が無ければ何も返さない
    （その場合の納品物はバス上の `result.output/data`）。
    """
    have = [c for c in (clones or []) if c.get("clone") and os.path.isdir(c.get("clone"))]
    res = classify_repos(have)
    if res["write"]:
        candidates = [res["write"]]              # 単一 write のみ push（read は対象外）
    else:
        candidates = res["undecided"]            # 未確定 → 変更のある候補を採用
    deliveries: "list[dict]" = []
    for c in candidates:
        d = _capture_one(c, run_id, node_id)
        if d:
            deliveries.append(d)
    if len(deliveries) > 1:
        # 単一 write 規則: 未確定で複数 repo に変更が出た（成果物先が割れた）→ 記録して可視化する
        log(node_id, f"警告: 成果物コミット先が複数に分かれました（{len(deliveries)} 件）。"
                     "planner で write/read を明示するか、1 リポジトリに集約してください。")
    return deliveries


def artifact_instruction(self_dir: "str | None", dep_arts: "dict[str, str] | None") -> str:
    """中間成果物（ファイル）の受け渡しプロトコルをエージェントへ伝える指示ブロック。

    output/data に乗らない大きな成果物は決定的なディレクトリでファイル参照する。
    - 自ノードの出力先（self_dir）に書き出すと後続タスクが同じパスで発見できる。
    - 依存タスクの成果物（dep_arts）は、その内容を本文に貼らずパスを示し、
      エージェントにファイルとして読ませる（コマンドライン長制限を避ける狙いも兼ねる）。"""
    if not self_dir and not dep_arts:
        return ""
    lines = ["【中間成果物プロトコル】タスク間の大きな成果物はファイルで受け渡します。"]
    if self_dir:
        lines.append("  - 出力先: 生成ファイル・大きな中間成果物は必ず次のディレクトリに書き出すこと"
                     f"（後続タスクがこのパスで参照します）: {self_dir}")
    have = {d: p for d, p in (dep_arts or {}).items()
            if p and os.path.isdir(p) and os.listdir(p)}
    if have:
        lines.append("  - 依存タスクの成果物（本文には貼りません。次のパス内のファイルを読んで利用すること）:")
        for d, p in have.items():
            files = sorted(os.listdir(p))
            more = " …" if len(files) > 10 else ""
            lines.append(f"    [{d}] {p} （{', '.join(files[:10])}{more}）")
    return "\n".join(lines)


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
        node = {
            "id": tid,
            "goal": str(t.get("goal", "")),
            "deps": [str(d) for d in (t.get("deps") or [])],
            "kind": kind,
        }
        if "repos" in t:                       # ノード単位の clone 対象（空配列＝不要）。プランナーの判断を保持
            node["repos"] = [str(r) for r in (t.get("repos") or [])]
        out.append(node)
    return out


def _first_line(text: str, limit: int = 48) -> str:
    """要求の先頭の非空行を limit 文字までで返す（イシューのタイトル等に使う簡潔な見出し）。
    構造化された複数行の要求でも、見出しを 1 行に保ち本来の目的が読めるようにする。"""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:limit]
    return text.strip()[:limit]


def plan_stub(request: str):
    """kiro-cli 無しの簡易分解。

    区切り記号で依存も表現:
      ';' / 改行 … 独立（並列）タスクの境界。**ただし改行は空行を含まないフラットな簡易
                   リストのときだけ区切りとみなす**（後述）。
      '->'        … 逐次依存チェーン（各タスクが直前のタスクに依存）

    区切り記号が無い単一文字列ならタスク数をランダム（2−5件）で決める。

    改行の扱い: 空行（段落 = "\\n\\n"）を含む**構造化された要求**（build_request が組み立てる
    charter 文脈・完了条件つきの要求など）は 1 件の要求として扱い、行ごとに細切れのタスクへ
    分割しない。さもないと対象リポジトリ一覧などの 1 行 1 行が別タスク（=別イシュー）になり、
    gitlab executor のタイトル/本文が文脈行で埋まってしまう。空行の無いフラットなリスト
    （例 "task1\\ntask2\\ntask3"）は従来どおり改行を区切りとして扱う。"""
    src = request if "\n\n" in request else request.replace("\n", ";")
    segments = [s.strip() for s in src.split(";") if s.strip()]
    if not segments:
        segments = [request.strip() or "no-op"]
    # 単一セグメントかつ依存記号（'->')も無い場合はタスク数をランダム展開
    if len(segments) == 1 and "->" not in segments[0]:
        n = random.randint(2, 5)
        base = _first_line(segments[0])   # 構造化要求でも見出しを 1 行に保つ（文脈行で埋めない）
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


# --------------------------------------------------------------------------
# 分解の粒度（granularity）— 設定ファイルで調整。coarse=現状 / fine=1段細かい /
#   finest=2段細かい（既定）。factor は並列ノード数の倍率＋プロンプトの分解指示に効く。
# --------------------------------------------------------------------------
GRANULARITY_FACTORS = {"coarse": 1, "fine": 2, "finest": 3}


def granularity_factor(level: "str | None") -> int:
    """粒度レベルを倍率（1/2/3）に。未知値は既定（finest=3）。"""
    return GRANULARITY_FACTORS.get((level or "finest").lower(), 3)


def scale_parallelism(par: int, level: "str | None") -> int:
    """並列ノード数を粒度倍率でスケールする（細かいほど多く・上限 16）。"""
    return max(1, min(16, int(par) * granularity_factor(level)))


def _explicit_parallelism(request: str) -> bool:
    """要求に並列数が明示（"x3"/"並列3"）されているか。明示なら粒度倍率を効かせない。"""
    return bool(re.search(r"[x×]\s*\d+", request) or re.search(r"並列\s*\d+", request))


def maybe_scale_parallelism(request: str, par: int, level: "str | None") -> int:
    """要求に明示が無いときだけ並列数を粒度倍率でスケールする（明示指定は尊重）。"""
    return par if _explicit_parallelism(request) else scale_parallelism(par, level)


def granularity_directive(level: "str | None") -> str:
    """プランナーへ渡す分解の細かさ指示。coarse は空（現状どおり）。"""
    f = granularity_factor(level)
    if f <= 1:
        return ""
    unit = "1ファイル/1関数/1観点" if f >= 3 else "意味のある最小単位"
    return (f"分解の粒度: 通常より細かく、各タスクを{unit}まで原子的に分解すること。"
            f"目安は通常の約{f}倍の数の小さなタスク（ただし無意味な細分化・重複は避け、"
            "各タスクは独立に検証可能に保つこと）。")


def _strategy_to_graph(pattern: str, request: str, par: int, review: bool = False):
    """選んだパターンを初期タスクグラフ（kind 付き）へ落とし込む。"""
    short = _first_line(request)   # 見出しは先頭の非空行（構造化要求でも目的が 1 行で読める）
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


def plan_strategy_stub(request: str, review="auto", granularity="finest"):
    """要求からパターンと並列数を選び、初期グラフを作る（kiro 無し版）。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。
    granularity で並列ノード数（=分解の細かさ）をスケールする。"""
    pattern = _detect_pattern(request)
    base = plan_stub(request)
    par = maybe_scale_parallelism(request, _parallelism(request, len([t for t in base if not t["deps"]])),
                                  granularity)
    review = _review_decision(review, [pattern])
    tasks = _strategy_to_graph(pattern, request, par, review)
    patterns = [pattern] + (["adversarial-verification"] if review and pattern != "adversarial-verification" else [])
    strategy = {"patterns": patterns, "parallelism": par, "review": review,
                "reason": f"stub heuristic → {pattern}（粒度 {granularity}）"
                          + ("（統合前レビュー有）" if review else "")}
    return strategy, tasks


def _repos_planner_note(repos: "list[str] | None") -> str:
    """プランナーへ「利用可能な repo 一覧」と「各タスクに必要な repo だけ割り当てよ」を伝える指示。
    repos が無ければ空文字（従来どおり repos 非対応）。"""
    if not repos:
        return ""
    lines = []
    for tok in repos:
        spec = parse_repo_token(tok)
        rid = spec.get("name") or _repo_name(spec["url"])
        tags = []
        if spec.get("path"):
            tags.append(f"フォルダ {spec['path']}")
        if spec.get("readonly"):
            tags.append("参照のみ")
        label = f"- {rid}" + ("（" + "・".join(tags) + "）" if tags else "")
        if spec.get("desc"):
            label += f": {spec['desc']}"
        lines.append(label)
    return ("\n利用可能なリポジトリ（中身を読む/ push する必要があるタスクにのみ割当）:\n"
            + "\n".join(lines)
            + "\n各タスクには、そのタスクが実際に必要とするリポジトリ id だけを \"repos\": [\"id\", ...] で"
            " 付けてください（不要なタスクは空配列 [] にする＝何も clone しない）。判断できない場合は省略可。\n")


def plan_strategy_kiro(request: str, model: str | None, review="auto", granularity="finest",
                       repos: "list[str] | None" = None):
    """kiro-cli にパターン選択・並列数・初期グラフを決めさせる。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。
    granularity で分解の細かさを指示し、返ってきた並列数も粒度倍率でスケールする。
    repos があれば各タスクへ必要な repo だけを割り当てさせる（必要なものだけ clone）。"""
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    compose = ("必要なら複数パターンを多段に複合してよい（例: classify-and-act の各分岐を "
               "fan-out-and-synthesize にする / generate-and-filter の通過案で tournament を行う）。")
    # 明示 OFF でなければレビューの意図を planner に伝える（最終的な有効/無効は
    # 返ってきた patterns を見て _review_decision で確定する）。
    review_note = ("統合（synthesize/reduce）を伴うパターンでは、集約の前に verify ノードを 1 つ挟み、"
                   "事前チェック・敵対的レビューを行ってください。" if review is not False else "")
    gran_note = granularity_directive(granularity)
    prompt = (
        "あなたは分散 Dynamic Workflow の計画役です。以下のワークフローパターンを知っています:\n"
        f"{catalog}\n\n"
        "patterns に書けるのは上記 7 つのパターン名だけです。派生語・同義語は使わず、"
        "近いものは必ず上記の正規名へ読み替えてください（例: 'panel of verifiers'→adversarial-verification）。\n"
        + (gran_note + "\n" if gran_note else "")
        + f"要求に最も適したパターンと並列数を選び、{compose}{review_note}"
        "それを反映した初期タスクグラフを作ってください。各タスクには kind を付けます"
        "（kind はノード種別であってパターン名ではありません。patterns には書かないこと）: "
        "work/generate/classify/synthesize/verify/filter/judge/reduce/split"
        "（reduce=構造化データの集約 / split=リスト化してデータ駆動 fan-out の起点）。"
        "重要: map-reduce では split ノードを1つだけ置き、要素ごとの map と reduce は"
        " split 完了後に実行時へ動的展開されるので、グラフに静的に書かないこと"
        "（split→work→reduce のような固定チェーンにすると並列展開されない）。"
        "並列にできるタスクは deps を空に、順序や統合が要るものは deps に先行 id を入れます。"
        "依存は既存タスク id のみ、循環は作らないこと。\n"
        + _repos_planner_note(repos)
        + "出力は JSON オブジェクトのみ:\n"
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
            "parallelism": maybe_scale_parallelism(request, int(data.get("parallelism", 2) or 2), granularity),
            "review": _review_decision(review, patterns),
            "reason": str(data.get("reason", "")),
        }
        return strategy, tasks
    except Exception:  # noqa: BLE001 — 解釈できなければ stub の戦略に倒す
        return plan_strategy_stub(request, review, granularity)


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


def plan_strategy_flow_planner(request: str, model: str | None, review="auto", granularity="finest"):
    """flow-planner スキルの3段パイプラインを呼び出す。
    スキルが見つからない / 失敗した場合は plan_strategy_kiro にフォールバック。
    granularity はスキルへ `--granularity` で渡し、返ってきた並列数も粒度倍率でスケールする。"""
    script = _find_flow_planner_script()
    if not script:
        # flow-planner スキル未インストール → kiro planner にフォールバック
        return plan_strategy_kiro(request, model, review, granularity)
    cmd = [sys.executable, script, request, "--granularity", str(granularity)]
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
            "parallelism": maybe_scale_parallelism(request, int(strategy.get("parallelism", 2) or 2), granularity),
            "review": _review_decision(review, patterns) if not isinstance(strategy.get("review"), bool)
                      else strategy["review"],
            "reason": f"[flow-planner] {strategy.get('reason', '')}（粒度 {granularity}）",
        }
        return final_strategy, tasks
    except Exception:  # noqa: BLE001 — flow-planner 失敗時は kiro にフォールバック
        return plan_strategy_kiro(request, model, review, granularity)


# --------------------------------------------------------------------------
# Executor — タスク実行（kiro-cli or stub）
# --------------------------------------------------------------------------
def _kiro_timeout() -> float | None:
    """kiro-cli 1 呼び出しのタイムアウト秒。設定ファイル `kiro_timeout` で調整、0/負で無効化。
    設定が無ければ環境変数 KIRO_FLOW_KIRO_TIMEOUT → 既定 600 にフォールバックする。
    心拍が lease を延長し続けるため、ハングした kiro-cli はこのタイムアウトでしか
    止められない（無いと worker が無限ブロックし run 全体が停止する）。"""
    to = _KIRO_TIMEOUT
    if to is None:
        try:
            to = float(os.environ.get("KIRO_FLOW_KIRO_TIMEOUT", "600"))
        except ValueError:
            to = 600.0
    return to if to > 0 else None


# 設定ファイル/CLI で解決した閾値を、args を持たない free 関数（run_kiro 等）が参照できる
# よう、main の resolve 後に _configure_thresholds がここへ反映する（既定は CONFIG_DEFAULTS）。
_ARGV_LIMIT = CONFIG_DEFAULTS["argv_limit"]
# executor プラグインの追加検索ディレクトリ（設定 executor_dir）。
_EXECUTOR_DIR: "str | None" = None
# kiro-cli タイムアウト秒 / stub スリープ上限秒（設定 kiro_timeout / stub_sleep_max）。
# None のままなら _kiro_timeout / _stub_sleep が環境変数→組み込み既定にフォールバックする。
_KIRO_TIMEOUT: "float | None" = None
_STUB_SLEEP_MAX: "float | None" = None


def _configure_thresholds(args) -> None:
    """設定ファイル/CLI（resolve_config 済み）の閾値をモジュール変数へ確定させる。
    run_kiro / executor 解決は args を受け取らないため、プロセス起動時に一度だけ値を固定する。"""
    global _ARGV_LIMIT, _EXECUTOR_DIR, _KIRO_TIMEOUT, _STUB_SLEEP_MAX
    v = getattr(args, "argv_limit", None)
    if v:
        try:
            _ARGV_LIMIT = int(v)
        except (TypeError, ValueError):
            pass
    d = getattr(args, "executor_dir", None)
    if d:
        _EXECUTOR_DIR = str(d)
    kt = getattr(args, "kiro_timeout", None)
    if kt is not None:
        try:
            _KIRO_TIMEOUT = float(kt)
        except (TypeError, ValueError):
            pass
    ss = getattr(args, "stub_sleep_max", None)
    if ss is not None:
        try:
            _STUB_SLEEP_MAX = float(ss)
        except (TypeError, ValueError):
            pass


def _kiro_argv_limit() -> int:
    """kiro-cli へ argv（コマンドライン）で渡すプロンプトの最大バイト数。
    これを超えるプロンプトは一時ファイルへ退避し参照渡しに切り替える。依存タスクの
    成果物が大きいとプロンプトが肥大し、OS の ARG_MAX（コマンドライン長制限）に達して
    プロセス起動自体が失敗するため。設定 argv_limit / CLI --argv-limit で調整（既定 100000）。"""
    return _ARGV_LIMIT if _ARGV_LIMIT > 0 else CONFIG_DEFAULTS["argv_limit"]


def run_kiro(prompt: str, model: str | None) -> str:
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    # プロンプトが大きすぎて argv 長制限に達する恐れがあれば、一時ファイルへ退避して
    # 「そのファイルを読んで実行」する短い指示に置き換える（成果物の受け渡しを参照渡しに）。
    spill = None
    if len(prompt.encode("utf-8")) > _kiro_argv_limit():
        fd, spill = tempfile.mkstemp(prefix="kiro-flow-prompt-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        cmd.append("以下のファイルにこのタスクの全文（依存タスクの成果物を含む）があります。"
                   f"必ずファイルの内容を読み込み、その指示に従ってタスクを実行してください: {spill}")
    else:
        cmd.append(prompt)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_kiro_timeout())
    except subprocess.TimeoutExpired:
        # 失敗として上位へ。タスクは failed 記録 → 再計画で retry に回り、run は前進する
        raise RuntimeError(f"kiro-cli タイムアウト（{_kiro_timeout():.0f}s 超過）")
    finally:
        if spill:
            with contextlib.suppress(OSError):
                os.remove(spill)
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
    """stub の擬似実行時間。既定 1〜5 秒。設定ファイル `stub_sleep_max` で調整
    （テストや動作確認では 0 にして高速化できる）。設定が無ければ環境変数
    KIRO_FLOW_STUB_SLEEP_MAX → 既定 5 にフォールバックする。"""
    mx = _STUB_SLEEP_MAX
    if mx is None:
        try:
            mx = float(os.environ.get("KIRO_FLOW_STUB_SLEEP_MAX", "5"))
        except ValueError:
            mx = 5.0
    if mx > 0:
        time.sleep(random.uniform(min(1.0, mx), mx))


def execute_stub(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = ""):
    # repo_instruction（成果物リポジトリの clone 指示）は stub の判定に使わない（goal は本来の goal）。
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


def execute_kiro(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = ""):
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
    if repo_instruction:  # 成果物リポジトリの clone 指示（ローカル実行のエージェントへ伝える）
        prompt += repo_instruction + "\n"
    art_note = artifact_instruction(art_dir, dep_arts)
    if art_note:  # 中間成果物のファイル参照プロトコル（出力先・依存成果物のパス）
        prompt += art_note + "\n"
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


# --------------------------------------------------------------------------
# executor プラグイン — kiro/stub は組み込み、それ以外はプラグインを動的ロードする
#
#   kiro-loop の event_hook と同じ流儀で、executor をプラグイン化する。`--executor`
#   （設定 executor）には次のいずれかを指定できる:
#     - "kiro" / "stub"  : 組み込み executor
#     - プラグイン名（例 "gitlab"）: 検索ディレクトリの executors/<name>.py を解決
#     - .py への明示パス : そのファイルをプラグインとしてロード
#   プラグインは `execute(kind, goal, dep_results, model, art_dir, dep_arts)` を公開し、
#   (text, data) を返す。任意で末尾に `repo_instruction`（成果物リポジトリの clone 指示・
#   キーワード可）を受け取れる。受け取れる executor には goal とは別引数で渡すので、
#   本来の goal を汚さずに使える（gitlab はイシューのタイトル/目的に本来の goal を出せる）。
#   受け取れない旧プラグインには、従来どおり clone 指示を goal 先頭へ結合して渡す（後方互換）。
#   プラグイン固有の設定は、同名のトップレベル設定ブロック（例 gitlab:）を JSON 化して
#   環境変数 KIRO_FLOW_EXECUTOR_CONFIG で渡す。
# --------------------------------------------------------------------------
# 組み込み executor の名前 → 実体は呼び出し時に globals() から解決する
# （テストの monkeypatch やホットリロードが効くよう、import 時の参照を握らない）。
BUILTIN_EXECUTORS = {"kiro": "execute_kiro", "stub": "execute_stub"}


def _executor_accepts_repo_instruction(execute) -> bool:
    """executor が `repo_instruction` を別引数で受け取れるか（名前付き引数 or **kwargs）。"""
    try:
        sig = inspect.signature(execute)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.name == "repo_instruction" or p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def call_executor(execute, kind: str, goal: str, dep_results: dict, model: "str | None",
                  art_dir, dep_arts, repo_instruction: str = ""):
    """executor を呼ぶ単一の入口。`repo_instruction`（成果物リポジトリの clone 指示）を
    受け取れる executor には**別引数**で渡して goal を汚さない（イシューのタイトル/目的が
    clone 指示で埋まらないようにする）。受け取れない旧 executor には従来どおり goal 先頭へ
    結合して渡す（後方互換）。"""
    if repo_instruction and _executor_accepts_repo_instruction(execute):
        return execute(kind, goal, dep_results, model, art_dir, dep_arts,
                       repo_instruction=repo_instruction)
    g = (repo_instruction + "\n\n" + goal) if repo_instruction else goal
    return execute(kind, g, dep_results, model, art_dir, dep_arts)

# executor プラグインモジュールの mtime キャッシュ: {path: (mtime, module)}
_executor_module_cache: "dict[str, tuple[float, object]]" = {}


def _executor_search_dirs() -> "list[str]":
    """executor プラグイン（<name>.py）を探すディレクトリ群（優先順）。"""
    dirs = []
    # 1. スクリプトと同階層の executors/（リポジトリ実行時の同梱プラグイン／インストーラが
    #    本体 bin と同じフォルダに配置した同梱プラグインを発見＝kiro-loop と同じ「本体隣」流儀）
    dirs.append(os.path.join(os.path.dirname(self_path()), "executors"))
    # 2. git リポジトリの tools/kiro-flow/executors（cwd がサブディレクトリでも届く）
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
        if root:
            dirs.append(os.path.join(root, "tools", "kiro-flow", "executors"))
    except Exception:  # noqa: BLE001
        pass
    # 3. ~/.kiro/kiro-flow/executors（旧インストーラの配置先・後方互換）
    dirs.append(os.path.expanduser("~/.kiro/kiro-flow/executors"))
    # 4. 設定 executor_dir（任意の追加ディレクトリ）
    extra = _EXECUTOR_DIR
    if extra:
        dirs.insert(0, os.path.expanduser(extra))
    # 重複を保ちつつ除去
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _resolve_executor_plugin(spec: str) -> "str | None":
    """executor 名 or パスからプラグイン .py の絶対パスを解決する。無ければ None。"""
    # 明示パス（.py）
    p = os.path.expanduser(spec)
    if p.endswith(".py") and os.path.isfile(p):
        return os.path.abspath(p)
    # 検索ディレクトリの <name>.py
    if not os.sep in spec and not spec.endswith(".py"):
        for d in _executor_search_dirs():
            cand = os.path.join(d, f"{spec}.py")
            if os.path.isfile(cand):
                return cand
    return None


def _load_executor_module(path: str):
    """executor プラグインを importlib でロードする（mtime キャッシュ付き）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise RuntimeError(f"executor プラグインが見つかりません: {path}")
    cached = _executor_module_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    import importlib.util
    spec = importlib.util.spec_from_file_location("kiro_flow_executor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"executor プラグインの spec 生成に失敗: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _executor_module_cache[path] = (mtime, module)
    return module


def resolve_executor_config_json(args) -> "str | None":
    """executor プラグインの設定ブロック（executor 名と同名のトップレベル設定。例 `executor: gitlab`
    なら `args.gitlab`）を親（daemon/orchestrator）で解決し、JSON 文字列にして返す。組み込み executor
    （kiro/stub）や、設定ブロックが無い/空のときは None。
    worker 起動時に環境変数 `KIRO_FLOW_EXECUTOR_CONFIG` として明示的に渡し、worker が `--config` を
    再解決できない/別の設定を拾う場合でも、親が解決した設定（例 gitlab の repo_url/conn_label）を
    確実に届けるために使う。"""
    spec = getattr(args, "executor", None) or "kiro"
    if spec in BUILTIN_EXECUTORS:
        return None
    cfg = getattr(args, spec, None)
    if isinstance(cfg, dict) and cfg:
        return json.dumps(cfg, ensure_ascii=False)
    return None


def make_executor(args):
    """args.executor を解決し、execute(kind, goal, dep_results, model, art_dir, dep_arts)
    形の呼び出し可能オブジェクトを返す。プラグインのときは設定ブロックを環境変数で渡す。"""
    spec = getattr(args, "executor", None) or "kiro"
    if spec in BUILTIN_EXECUTORS:
        return globals()[BUILTIN_EXECUTORS[spec]]
    path = _resolve_executor_plugin(spec)
    if not path:
        dirs = "、".join(_executor_search_dirs())
        raise SystemExit(
            f"[kiro-flow] executor '{spec}' を解決できません。組み込み（kiro/stub）か、"
            f"プラグイン .py（検索: {dirs}）か、明示パスを指定してください。")
    module = _load_executor_module(path)
    fn = getattr(module, "execute", None)
    if not callable(fn):
        raise SystemExit(f"[kiro-flow] executor プラグインに execute() がありません: {path}")
    # プラグイン固有設定: 同名のトップレベル設定ブロック（例 gitlab:）を JSON で環境変数に渡す。
    # 親が解決済みで既に渡されている（worker が再解決できない）場合は、その値を尊重して上書きしない。
    cfgjson = resolve_executor_config_json(args)
    if cfgjson is not None:
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = cfgjson
    log("executor", f"プラグイン '{spec}' をロードしました: {path}")
    return fn


def make_finalizer(args):
    """args.executor の `finalize(delivery, action)` を解決する（任意のプラグイン契約）。

    finalize は verify 通過後に呼ばれる決定的な納品アクション（例: gitlab の MR マージ＋
    イシュークローズ）。組み込み（kiro/stub）はローカルで commit/push 済み or 実変更なしの
    ため追加の finalize は不要＝None（no-op）。finalize を持たないプラグインも None。"""
    spec = getattr(args, "executor", None) or "kiro"
    if spec in BUILTIN_EXECUTORS:
        return None
    path = _resolve_executor_plugin(spec)
    if not path:
        return None
    module = _load_executor_module(path)
    fn = getattr(module, "finalize", None)
    if not callable(fn):
        return None
    # finalize もプラグイン設定（例 gitlab: の repo_url/conn_label/トークン解決元）を要する
    cfgjson = resolve_executor_config_json(args)
    if cfgjson is not None:
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = cfgjson
    return fn


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
                   review: bool = False, request: str = "", exemplar_first: bool = False):
    """データ駆動の動的 fan-out: 完了した split ノードの data(リスト)を見て、
    実行時に要素ごとの map タスクと、それらを集約する reduce タスクを生成する。
    （reduce は展開時に作るので、split 完了直後に reduce が先走り実行されない）
    review 時は map と reduce の間に検証 gate を挟む。
    map・reduce ゴールには元の要求（intent）を埋め込み、各要素への適用と最終整形
    （並べ替え・重複排除など要求由来の集約条件）が失われないようにする。

    exemplar_first=True のときは「見本先行」分解にする: まず先頭1件(pilot map)と
    その検証ゲートだけを出し、ゲート通過後に残りの map（pilot を範に取る = pilot に依存）
    と reduce を展開する。同様手順の繰り返しで、1件で手順を固めてから残りを流す。"""
    new = []
    have = set(nodes)
    for nid, node in nodes.items():
        if node.get("kind") != "split":
            continue
        r = results.get(nid, {})
        if r.get("status") != "done":
            continue
        if f"{nid}-reduce" in have:  # 既に完全展開済み
            continue
        items = r.get("data")
        if not isinstance(items, list) or not items:
            continue
        items = items[:max(1, max_fanout)]  # 暴走防止のクランプ
        intent = (request or node.get("goal", "")).strip()

        def _mgoal(i, item):
            return f"{intent}（対象要素: {item}）" if intent else f"{nid} 要素{i+1}: {item}"

        reduce_goal = (f"{intent}（各 map の結果を要求どおりに集約・整形して最終成果にまとめる）"
                       if intent else f"{nid} の結果を集約")
        pilot_gate = f"{nid}-pilot"
        m1 = f"{nid}-m1"

        if exemplar_first:
            if m1 not in have:
                # Stage 1: pilot map 1件＋その検証ゲートだけを出す（残りはまだ展開しない）
                new.append({"id": m1, "goal": _mgoal(0, items[0]), "deps": [], "kind": "map"})
                new.append({"id": pilot_gate,
                            "goal": f"先行1件(map)を検証し、残りに使う手順・基準を固める: {intent}"[:200],
                            "deps": [m1], "kind": "verify"})
                continue
            if results.get(pilot_gate, {}).get("status") != "done":
                continue  # pilot ゲート通過まで残りは展開しない
            # Stage 2: 残り map（pilot を範に取り、ゲート通過後に走る）＋ reduce
            map_ids = [m1]
            for i, item in enumerate(items[1:], start=1):
                mid = f"{nid}-m{i+1}"
                map_ids.append(mid)
                new.append({"id": mid, "goal": _mgoal(i, item),
                            "deps": [m1, pilot_gate], "kind": "map"})
        else:
            map_ids = []
            for i, item in enumerate(items):
                mid = f"{nid}-m{i+1}"
                map_ids.append(mid)
                # 要素だけでなく「何をするか」を渡さないと map が意図を失う
                new.append({"id": mid, "goal": _mgoal(i, item), "deps": [], "kind": "map"})

        reduce_deps = map_ids
        if review:  # 集約前の事前チェック / 敵対的レビュー。reduce は map＋gate に依存
            gid = f"{nid}-gate"
            new.append({"id": gid, "goal": f"{nid} の map 結果を集約前に検証",
                        "deps": map_ids, "kind": "verify"})
            reduce_deps = map_ids + [gid]
        new.append({"id": f"{nid}-reduce", "goal": reduce_goal,
                    "deps": reduce_deps, "kind": "reduce"})
    return new


def continue_stub(request: str, nodes: dict, results: dict, iteration: int,
                  max_fanout: int = 50, review: bool = False, exemplar_first: bool = False,
                  max_retries: int = 3):
    """パターン継続（kiro 無し版）:
       - データ駆動 fan-out: split 完了 → 要素ごとの map + reduce を生成
       - classify-and-act: 分類完了 → 振り分け先の専門タスクを追加
       - adversarial / loop-until-done: verify が fail → 作り直し + 再検証
       - 失敗タスク: retry を 1 回追加

    サーキットブレーカー: 同一系統の作り直し回数（retries）が max_retries に達したら、
    その系統の verify-fail / 失敗ノードに対する再タスクをこれ以上生成しない。達成不可能な
    完了条件で無限に再タスクを積み続けるのを防ぐ（node["retries"] で系統ごとに計上）。"""
    new = _expand_splits(nodes, results, max_fanout, review, request, exemplar_first)
    have = set(nodes)
    tripped = []  # サーキットブレーカーが作動した系統（理由表示用）

    def fresh(tid):
        return tid not in have and tid not in [t["id"] for t in new]

    for nid, node in nodes.items():
        r = results.get(nid, {})
        if r.get("status") != "done" and r.get("status") != "failed":
            continue
        kind = node.get("kind", "work")
        tries = int(node.get("retries", 0))  # この系統で既に作り直した回数
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
            if tries >= max_retries:
                tripped.append(nid)  # サーキット開放: これ以上作り直さない（達成不可能とみなす）
            else:
                for dep in node.get("deps", []):
                    rid = f"{dep}-r{iteration+1}"
                    if fresh(rid):
                        goal = nodes.get(dep, {}).get("goal", "").replace("FLAKY", "ok")
                        new.append({"id": rid, "goal": f"[retry] {goal}", "deps": [],
                                    "kind": nodes.get(dep, {}).get("kind", "work"),
                                    "replaces": dep, "retries": tries + 1})
                vid = f"{nid}-r{iteration+1}"
                if fresh(vid):
                    new.append({"id": vid, "goal": "再検証",
                                "deps": [f"{dep}-r{iteration+1}" for dep in node.get("deps", [])],
                                "kind": "verify", "replaces": nid, "retries": tries + 1})
        # 3) 失敗タスクの retry（失敗ノードを置き換え、依存元を付け替える）
        if r.get("status") == "failed":
            if tries >= max_retries:
                tripped.append(nid)  # サーキット開放: 反復失敗するタスクは諦める
            else:
                rid = f"{nid}r"
                if fresh(rid):
                    goal = node.get("goal", "").replace("FAIL", "ok")
                    new.append({"id": rid, "goal": f"[retry] {goal}", "deps": [],
                                "kind": node.get("kind", "work"),
                                "replaces": nid, "retries": tries + 1})
    if new:
        return "replan", new, f"{len(new)} 件追加"
    if tripped:
        return "done", [], (f"サーキットブレーカー作動: {','.join(tripped)} は "
                            f"{max_retries} 回の作り直しでも未達のため打ち切り")
    return "done", [], "全パターン完了"


_RETRY_SUFFIX_RE = re.compile(r"-r\d+")


def _retry_depth(nid: str, node: dict) -> int:
    """ノードの作り直し回数（系統の深さ）。明示の retries カウンタを優先し、無ければ
    id の -rN 連鎖（例: gen1-r1-r2 → 2）から推定する。サーキットブレーカー判定に使う。"""
    if node and node.get("retries"):
        return int(node["retries"])
    return len(_RETRY_SUFFIX_RE.findall(nid or ""))


def _circuit_tripped(nodes: dict, results: dict, max_retries: int) -> list:
    """達成不可能な完了条件で打ち切るべき系統の id 一覧を返す。
    verify が fail し続ける／失敗を繰り返すノードのうち、作り直しが max_retries に
    達したものを「これ以上再タスクを積まない」対象として検出する。"""
    out = []
    for nid, node in nodes.items():
        r = results.get(nid, {})
        st = r.get("status")
        is_verify_fail = node.get("kind") == "verify" and "fail" in str(r.get("output", ""))
        if (st == "failed" or is_verify_fail) and _retry_depth(nid, node) >= max_retries:
            out.append(nid)
    return out


def continue_kiro(request: str, nodes: dict, results: dict, iteration: int,
                  max_fanout: int = 50, review: bool = False, exemplar_first: bool = False,
                  max_retries: int = 3):
    # データ駆動 fan-out は機械的に展開（LLM 判断不要）。先に処理する。
    fanout_tasks = _expand_splits(nodes, results, max_fanout, review, request, exemplar_first)
    if fanout_tasks:
        return "replan", fanout_tasks, f"data-driven fan-out: +{len(fanout_tasks)}"
    # サーキットブレーカー: 作り直しが上限に達した系統は達成不可能とみなし打ち切る
    # （評価役 LLM が無限に再タスクを積み続けるのを防ぐ）。
    tripped = _circuit_tripped(nodes, results, max_retries)
    if tripped:
        return "done", [], (f"サーキットブレーカー作動: {','.join(tripped)} は "
                            f"{max_retries} 回の作り直しでも未達のため打ち切り")
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    summary = "\n".join(
        f"- {nid} ({nodes.get(nid, {}).get('kind','work')}) "
        f"[{r.get('status')}]: {str(r.get('output',''))[:160]}"
        for nid, r in results.items()
    )
    prompt = (
        "あなたは分散 Dynamic Workflow の評価役です。7 パターンを踏まえ、現在の結果が要求を満たすか判定し、"
        "必要なら次のタスクを追加してください（例: 分類結果に応じた専門タスク、検証 fail の作り直し、"
        "統合や追加候補の生成）。\n"
        f"ただし同じ完了条件のために作り直しを繰り返しても改善しない場合（達成不可能な条件など）は、"
        f"同一タスクの作り直しは最大 {max_retries} 回までとし、それを超えるなら無理に再タスクを足さず "
        '"done" を返してください。\n'
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
    gran = getattr(args, "granularity", "finest")
    if args.planner == "flow-planner":
        return plan_strategy_flow_planner(args.request, args.model, review, gran)
    if args.planner == "kiro":
        return plan_strategy_kiro(args.request, args.model, review, gran,
                                  getattr(args, "repos", None))
    return plan_strategy_stub(args.request, review, gran)


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
    ef = bool(getattr(args, "exemplar_first", False))
    mr = int(getattr(args, "max_retries", 3) or 3)
    # 再計画（evaluator-optimizer）はオーケストレータ側でローカルに判断する。stub のときだけ
    # stub 継続、それ以外（kiro やプラグイン executor）はローカル kiro で判断する
    # （プラグインはワーカータスクの実行のみを委譲し、メタ評価はローカルに残す）。
    if args.executor == "stub":
        return continue_stub(request, nodes, results, iteration, mf, review, ef, mr)
    return continue_kiro(request, nodes, results, iteration, mf, review, ef, mr)


def _node_entry(t):
    e = {"goal": t["goal"], "deps": t["deps"], "kind": t.get("kind", "work")}
    if "repos" in t:  # ノード単位の clone 対象（必要なものだけ）。空配列＝clone 不要
        e["repos"] = list(t.get("repos") or [])
    if t.get("retries"):  # サーキットブレーカー用の作り直し回数（>0 のときだけ保持）
        e["retries"] = int(t["retries"])
    return e


def _assign_node_repos(tasks, args) -> None:
    """計画時に各ノードへ clone 対象 repo を割り当てる（『必要な時に必要なものを判断して clone』の計画層）。
    - stub プランナー（知能なし）は全 repo を割り当てる（安全側・現状維持）。
    - LLM プランナー（kiro/flow-planner）が既に subset を付けたノードはそれを尊重する。
    - LLM で未注釈のノードは worker 側で全 repo にフォールバックする（取りこぼし防止）。"""
    tokens = getattr(args, "repos", None) or []
    if not tokens:
        return
    intelligent = getattr(args, "planner", "stub") in ("kiro", "flow-planner")
    if intelligent:
        return                                 # プランナー出力の repos をそのまま使う（未注釈は worker で全 repo）
    ids = [repo_token_id(t) for t in tokens]
    for t in tasks:
        if not t.get("repos"):
            t["repos"] = list(ids)             # stub: 全 repo を割り当てる


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


def _finalize_run(bus, args, iteration: int) -> None:
    """全ノードの結果を集約して final.json を書き出し、run を done にして push・ログ出力する。"""
    results = {nid: (bus.read_result(nid) or {}) for nid in bus.task_ids()}
    summary = "\n".join(
        f"- {nid} [{r.get('status')}]: {str(r.get('output',''))[:200]}"
        for nid, r in results.items())
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
    log(args.node_id, f"完了（iteration={iteration}）。final.json を書き出しました。")
    log(args.node_id, "結果サマリ:\n" + summary)


def cmd_orchestrate(args) -> int:
    who = args.node_id
    bus = make_bus(args, who)
    bus.sync_pull()
    bus.ensure_run(args.request, getattr(args, "repos", None))
    graph = bus.read_graph()

    # 既存グラフがあれば計画をやり直さず再開（resume）
    if graph and graph.get("nodes"):
        iteration = graph.get("iteration", 0)
        log(who, f"run={args.run_id} 再開（既存 {len(graph['nodes'])} ノード, iteration={iteration}）")
        if not bus.all_terminal():
            bus.set_status("running")
            bus.sync_push(f"resume run {args.run_id}")
    else:
        # 要求から 7 パターンの組み合わせと並列数を選び、初期グラフを形作る
        strategy, tasks = _plan_strategy(args)
        _assign_node_repos(tasks, args)   # 各ノードへ clone 対象 repo を割当（stub=全 / LLM=判断済みを尊重）
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
            _assign_node_repos(new_tasks, args)   # 追加ノードにも clone 対象 repo を割当
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

    _finalize_run(bus, args, iteration)   # 全ノード結果を集約 → final.json 書き出し → done/push
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
    # executor を一度だけ解決する（組み込み kiro/stub or プラグイン）。
    execute = make_executor(args)
    # 親（run/daemon）からの SIGTERM でも成果物リポジトリの clone を消してから抜ける
    signal.signal(signal.SIGTERM, lambda *_: (cleanup_work_repos(), sys.exit(143)))
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
        # 中間成果物プロトコル: 自ノードの出力先を用意し、依存ノードの成果物パスを集める。
        # これにより大きな成果物は output/data に貼らずファイル参照で受け渡せる。
        art_dir = bus.ensure_artifact_dir(nid)
        dep_arts = {d: bus.node_artifact_dir(d) for d in node.get("deps", [])}
        # 成果物リポジトリを temp 領域へ clone し、エージェントへパスを渡す。
        # このノードに必要な repo だけを clone する（計画時にノードへ割り当てた subset。未注釈は全 repo）。
        goal = node["goal"]
        clones = ensure_work_repos(resolve_node_repos(node, bus.run_repos()), who)
        # clone 指示は goal に結合せず別引数で渡す（goal を汚さない）。対応 executor は
        # 本来の goal をそのまま使い（gitlab はタイトル/目的に出す）、clone 指示は別枠で扱う。
        instruction = repo_instruction(clones) if clones else ""
        # 実行中は心拍で lease を延長し続け、長時間タスクでも再 claim されないようにする
        hb = Heartbeat(bus, nid, who, args.lease)
        hb.start()
        rdata = None
        try:
            output, rdata = call_executor(execute, kind, goal, dep_results, args.model,
                                          art_dir, dep_arts, instruction)
            rstatus = "done"
        except Exception as e:  # noqa: BLE001 — 結果として記録する
            output = f"実行エラー: {e}"
            rstatus = "failed"
        finally:
            hb.stop()

        # 成果物リポジトリへの実体（コミット）を決定的に捕捉する。kiro executor のみ
        # （stub は実変更なし、gitlab 等は別マシンで作業＝ローカル clone に変更が出ない）。
        # 変更があった repo だけを成果ブランチへ push し、リンクを result.data.delivery に残す。
        # 成果物がコミットを伴わない調査系タスクは捕捉対象外＝納品物はバス上の output/data。
        if rstatus == "done" and clones and getattr(args, "executor", "kiro") == "kiro":
            try:
                dels = capture_deliveries(clones, bus.run_id, nid)
            except Exception as e:  # noqa: BLE001 — 捕捉失敗で run 全体を止めない
                dels = []
                log(who, f"delivery 捕捉に失敗: {e}")
            if dels:
                if isinstance(rdata, dict):
                    rdata = {**rdata, "delivery": dels}
                elif rdata is None:
                    rdata = {"delivery": dels}
                else:
                    rdata = {"value": rdata, "delivery": dels}

        # 生成された中間成果物を run_dir 相対パスで記録（後続・status から発見できる）
        artifacts = [os.path.relpath(p, bus.run_dir) for p in bus.list_artifacts(nid)]
        bus.write_result(nid, who, rstatus, output, rdata, artifacts=artifacts)
        bus.event(who, "result", node=nid, status=rstatus)
        bus.sync_push(f"result {nid} [{rstatus}] by {who}")
        log(who, f"完了: {nid} [{rstatus}]")
        if getattr(args, "cleanup_per_node", False) and clones:
            cleanup_work_repos()  # ノード完了/失敗ごとに clone を即削除（長命 worker のディスク抑制）
        time.sleep(random.uniform(0, 0.3))  # 負荷分散: 他ノードに claim の機会を渡す


# --------------------------------------------------------------------------
# run — 単発実行。既存 run-id なら再開、無ければ新規（状態で自動判断）
# --------------------------------------------------------------------------
def _mode_string(args, bus: str) -> str:
    """ログ用のモード表記。git バスなら `git:<repo>@<branch>`、ローカルなら `local:<bus>`。"""
    return f"git:{args.git}@{args.git_branch}" if args.git else f"local:{bus}"


def _child_base(args, bus_abs: str) -> list:
    """子プロセス（orchestrator/worker）へ引き継ぐ共通先頭 argv（バス・lease・設定・git・keep-clone）。
    グローバル引数のみ。run_id / repos / granularity 等はサブコマンド毎に呼び出し側で付け足す。"""
    base = [sys.executable, self_path(), "--bus", bus_abs, "--lease", str(args.lease)]
    cfg_path = getattr(args, "_config_path", None)
    if cfg_path:
        # 設定（executor プラグインの gitlab: ブロック等）を子へ伝搬。子は cwd が異なりうるので絶対パスで渡す。
        base += ["--config", os.path.abspath(cfg_path)]
    if args.git:
        base += ["--git", args.git, "--git-branch", args.git_branch, "--git-subdir", args.git_subdir or ""]
    if not getattr(args, "cleanup_clone", True):
        base += ["--keep-clone"]  # 親の指定を子（orchestrator/worker）へ引き継ぐ
    if getattr(args, "cleanup_per_node", False):
        base += ["--cleanup-per-node"]  # ノード単位の即時削除も子へ引き継ぐ
    return base


def _acquire_daemon_lock(args):
    """daemon singleton ロックを取得して pid を記録し、lock_file を返す。既に保持中なら None。
    pid は flock の有無に関わらず記録する（flock 非対応環境でも pid 生存で発見できるように）。"""
    lock_path = _daemon_lock_path(args)
    # 既存ホルダの pid を消さないよう truncate せず開く（flock 取得後にだけ書く）
    lock_file = os.fdopen(os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644), "r+")
    if fcntl is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _release_daemon_lock(lock_file) -> None:
    """daemon singleton ロックを解放して fd を閉じる（自己更新の再起動前に呼ぶ）。
    flock は fd に紐づくため、execv で再起動する前に解放しないと再取得で多重起動扱いになる。"""
    if lock_file is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        lock_file.close()
    except OSError:
        pass


def _run_lease_window(args) -> float:
    """run 生存リース（heartbeat）の猶予秒。健康な daemon は poll 毎に更新するので、
    poll の十数倍を確保すれば一過性の遅延（GC/ネットワーク）で誤回収しない。一方 act_timeout
    （消費者側の上限・既定 1800s）より十分短くして、owner 消失後すばやく孤児回収できるようにする。"""
    return max(float(getattr(args, "poll", 2.0) or 2.0) * 10.0, 120.0)


def _recover_orphan_runs(bus: Bus, daemon_id: str, owned: set, lease_window: float) -> "list[str]":
    """inbox 由来で自分が回しておらず生存リースの切れた（owning daemon が消失した）run を failed に
    確定し、回収した run_id を返す。これが無いと、再起動した新プロセスは前プロセスが残した
    status:running を見て受理をスキップするだけで、remote submit を待つ消費者が act_timeout まで
    永久待機してしまう。`owned` は自分が今 orchestrator を回している run（誤回収しない）。"""
    recovered: "list[str]" = []
    for req_id in bus.list_inbox():
        if req_id in owned or not bus.run_exists(req_id):
            continue
        if bus.run_is_orphaned(req_id, lease_window) and \
                bus.mark_run_failed(req_id, "orphaned: owning daemon が消失（生存リース切れ）"):
            bus.run_view(req_id).event(daemon_id, "run-orphaned", run=req_id)
            bus.sync_push(f"run {req_id} failed: orphaned（生存リース切れ）")
            recovered.append(req_id)
    return recovered


def _spawn_orchestrator(base: list, args, req_id: str, req: dict):
    """要求 req を担当する orchestrator を base argv から起動する（daemon のオンデマンド起動）。"""
    repo_args = []
    for r in (req.get("repos") or []):   # 要求に紐づく成果物リポジトリを run meta へ載せる
        repo_args += ["--repo", r]
    return subprocess.Popen(base + repo_args + [
        "--granularity", str(getattr(args, "granularity", "finest") or "finest"),
        *(["--exemplar-first"] if getattr(args, "exemplar_first", False) else []),
        "--run-id", req_id, "orchestrate", "--request", req["request"],
        "--planner", args.planner, "--executor", args.executor,
        "--max-iterations", str(args.max_iterations),
        "--max-fanout", str(args.max_fanout),
        "--max-retries", str(args.max_retries),
        "--model_opt", args.model or "", "--poll", str(args.poll),
        "--node-id", f"orchestrator-{req_id}",
    ])


def _spawn_worker(base: list, args, rid: str, wid: str):
    """run rid のワーカーを1つ base argv から起動する（idle-exit のオンデマンド worker）。
    親（daemon）で解決した executor プラグイン設定（例 gitlab: の repo_url/conn_label）を
    `KIRO_FLOW_EXECUTOR_CONFIG` として worker の環境に明示的に渡す。worker が `--config` を
    再解決できない/別の設定を拾う場合でも、親の設定が確実に届くようにする。"""
    env = os.environ.copy()
    cfgjson = resolve_executor_config_json(args)
    if cfgjson is not None:
        env["KIRO_FLOW_EXECUTOR_CONFIG"] = cfgjson
    return subprocess.Popen(base + [
        "--run-id", rid, "work", "--node-id", wid,
        "--executor", args.executor, "--model_opt", args.model or "",
        "--poll", str(args.poll), "--idle-exit",
    ], env=env)


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
    # グローバル引数（バス・転送・run_id・成果物リポジトリ・分解粒度）を子プロセスへ引き継ぐ
    base = _child_base(args, bus_root) + ["--run-id", run_id]
    for r in (getattr(args, "repos", None) or []):
        base += ["--repo", r]     # 成果物リポジトリを orchestrator/worker へ伝搬
    base += ["--granularity", str(getattr(args, "granularity", "finest") or "finest")]  # 分解粒度
    if getattr(args, "exemplar_first", False):
        base += ["--exemplar-first"]   # 見本先行分解を orchestrator へ伝搬
    mode = _mode_string(args, bus_root)

    procs = []
    orch = subprocess.Popen(base + [
        "orchestrate", "--request", args.request,
        "--planner", args.planner, "--executor", args.executor,
        "--max-iterations", str(args.max_iterations),
        "--max-fanout", str(args.max_fanout),
        "--max-retries", str(args.max_retries),
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
    bus.submit_request(req_id, args.request, f"{socket.gethostname()}-{os.getpid()}",
                       repos=getattr(args, "repos", None))
    bus.sync_push(f"submit request {req_id}")
    print(req_id)  # run-id を標準出力（スクリプトから拾える）
    print(f">>> 要求を投入しました: {req_id}（デーモンが拾います）", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# daemon — 常駐し、要求に応じて orchestrator/worker をオンデマンド起動
# --------------------------------------------------------------------------
def daemon_lock_dir(lock_dir: "str | None" = None) -> str:
    """daemon ロックを置く共有ディレクトリ。
    起動側とプローブ側（kiro-autonomous 等）で必ず一致させる必要があるため、
    設定ファイルの `lock_dir`（CLI `--lock-dir`）で明示でき、既定は tempdir 配下。
    TMPDIR 差で別ディレクトリを見て「外部 daemon を発見できない」事故を防ぐ。"""
    d = lock_dir or os.path.join(tempfile.gettempdir(), "kiro-flow-locks")
    os.makedirs(d, exist_ok=True)
    return d


def daemon_lock_key(args) -> str:
    """バスを正規化した singleton キー。symlink/相対パス/別 cwd で起動された
    外部 daemon でも同じ論理バスなら同一キーになるよう realpath で canonical 化する。"""
    if getattr(args, "git", None):
        return f"git::{args.git}@{args.git_branch}/{args.git_subdir or ''}"
    return "local::" + os.path.realpath(args.bus)


def _daemon_lock_path(args) -> str:
    """バス単位のデーモン singleton 用ロックパス（バス外の一時領域）。"""
    h = hashlib.sha1(daemon_lock_key(args).encode()).hexdigest()
    return os.path.join(daemon_lock_dir(getattr(args, "lock_dir", None)), f"daemon-{h}.lock")


def cmd_daemon(args) -> int:
    # 冪等化: 同一バスのデーモンが既に稼働していれば何もしない（多重起動しない）
    lock_file = _acquire_daemon_lock(args)
    if lock_file is None:
        print(f">>> kiro-flow daemon は既に稼働中です（{_mode_string(args, os.path.realpath(args.bus))}）。"
              "起動をスキップします。", flush=True)
        return 0

    daemon_id = args.node_id or f"{socket.gethostname()}-{os.getpid()}"
    bus = make_bus(args, f"daemon-{_safe(daemon_id)}")
    base = _child_base(args, os.path.abspath(args.bus))
    mode = _mode_string(args, os.path.abspath(args.bus))

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
    cleanup_interval = float(args.cleanup_interval)
    # 起動直後に 1 回掃除しないよう、最初の判定は interval 後になるよう初期化
    last_cleanup = time.time()
    # 自己更新（既定 on）: 起動直後の最初のアイドルでも実施するため last=0 で初期化し、cwd を保持
    start_cwd = os.getcwd()
    update_state = {"last": 0.0}
    # 自分が回している run の生存リース（heartbeat）。ローカル meta は毎 poll 更新（安価）、
    # git バスへの push は lease_window/3 毎に間引く（毎 poll の push を避ける）。
    lease_window = _run_lease_window(args)
    next_heartbeat_push = 0.0

    while not stop["v"]:
        bus.sync_pull()
        # 一時ファイルの自動クリーンアップ（ロック / 中間 .tmp / 孤立クローン）を定期実行
        if cleanup_interval > 0 and time.time() - last_cleanup >= cleanup_interval:
            last_cleanup = time.time()
            try:
                c = run_cleanup(args, bus)
                if any(c.values()):
                    log(daemon_id, f"cleanup: locks={c['locks']} tmp={c['tmp']} "
                                   f"clones={c['clones']} work_repos={c['work_repos']}")
            except Exception as e:  # noqa: BLE001 — 掃除失敗は daemon を止めない
                log(daemon_id, f"cleanup でエラー（無視して継続）: {e}")
        # 死んだ子を刈り取る。orchestrator が done を書く前に異常終了（クラッシュ / kill /
        # 起動失敗）した場合は run が終端に達さないまま放置され、result/status を待つ消費者
        # （kiro-autonomous の charter 駆動 watch など）が永久待機に陥る。終端でなければ
        # failed に確定し、失敗終了を検知可能にする。
        for rid in [r for r, p in orchestrators.items() if p.poll() is not None]:
            rc = orchestrators[rid].poll()
            del orchestrators[rid]
            if bus.mark_run_failed(rid, f"orchestrator が終端化前に終了しました（rc={rc}）"):
                bus.run_view(rid).event(daemon_id, "run-failed", run=rid, rc=rc)
                bus.sync_push(f"run {rid} failed: orchestrator 異常終了（rc={rc}）")
                log(daemon_id, f"orchestrator 異常終了: {rid}（rc={rc}）→ run を failed に確定")
            else:
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）")
        workers = [(r, p) for r, p in workers if p.poll() is None]

        # 自分が回している run の生存リースを更新（再起動後の自分・別デーモンへ「駆動中」を示す）。
        # ローカル meta は毎 poll 更新し、git バスへの伝搬は間引いて push する。
        for rid in orchestrators:
            bus.touch_run(rid, lease_window)
        if orchestrators and time.time() >= next_heartbeat_push:
            bus.sync_push("heartbeat: 駆動中の run の生存リースを更新")
            next_heartbeat_push = time.time() + lease_window / 3.0

        # 孤児 run の回収: owning daemon が消失した非終端 run を failed に確定する（再起動した
        # 新プロセスが status:running を放置せず、消費者が act_timeout まで待たずに復旧できるように）。
        for rid in _recover_orphan_runs(bus, daemon_id, set(orchestrators), lease_window):
            log(daemon_id, f"孤児 run を回収: {rid} → failed（owning daemon 消失）")

        # 1) 新しい要求を受理 → orchestrator をオンデマンド起動（分散時は 1 台だけ担当）
        for req_id in bus.list_inbox():
            if bus.run_exists(req_id) or req_id in orchestrators:
                continue
            req = bus.read_inbox(req_id)
            if not req:
                continue
            if bus.claim_request(req_id, daemon_id, args.lease):
                orchestrators[req_id] = _spawn_orchestrator(base, args, req_id, req)
                bus.touch_run(req_id, lease_window)   # 受理直後に生存リースを張る（孤児誤判定を防ぐ）
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
                workers.append((rid, _spawn_worker(base, args, rid, wid)))
                have += 1
                log(daemon_id, f"ワーカー起動: {wid} → run {rid}（claim可能={want}）")

        # 3) アイドル（要求も子も無い）なら自己更新を確認。更新を取り込めたら graceful 再起動。
        idle = not orchestrators and not workers and not bus.list_inbox()
        if maybe_self_update(args, idle, update_state):
            log(daemon_id, "自己更新を適用しました。子を停止し graceful 再起動します。")
            shutdown()                       # 残っている子があれば terminate（idle なので基本居ない）
            _release_daemon_lock(lock_file)  # flock を解放してから再取得できるようにする
            restart_self(start_cwd)          # 動いていた cwd のまま新しい本体へ（戻らない）

        time.sleep(args.poll)
    return 0


# --------------------------------------------------------------------------
# cleanup — 一時ファイルの自動掃除（ロック / 中間 .tmp / 孤立クローン）
# --------------------------------------------------------------------------
# バス内の run（gc が掃除する）とは別に、kiro-flow は「バス外の一時ファイル」を
# 残す。これらは削除処理が無く溜まり続けるため、daemon ループから定期掃除する。
#   A) $TMPDIR/kiro-flow-locks/*.lock        … claim/daemon の排他ロック
#   B) <path>.tmp.<pid>                       … write_json_atomic の中間ファイル（crash 残骸）
#   C) {bus}/<node>/                          … git モードのノード別クローン（run 終了後に孤立）
_TMP_SUFFIX_RE = re.compile(r"\.tmp\.(\d+)$")


def _locks_root() -> str:
    return os.path.join(tempfile.gettempdir(), "kiro-flow-locks")


def _pid_alive(pid: int) -> bool:
    """pid のプロセスが存命か（POSIX）。判定不能なら安全側で True を返す。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # 別ユーザのプロセス＝存在はする
        return True
    except OSError:
        return True
    return True


def sweep_lock_files(min_age_sec: float = 3600.0) -> int:
    """$TMPDIR/kiro-flow-locks/ の使われていない .lock を削除し、削除数を返す。
    保持中のロックを消すと排他が壊れるため、(1) 十分古い（min_age_sec 以上アイドル）
    かつ (2) flock を非ブロッキングで取得できた（＝誰も保持していない）ものに限る。"""
    d = _locks_root()
    if not os.path.isdir(d):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(d):
        if not name.endswith(".lock"):
            continue
        path = os.path.join(d, name)
        try:
            if now - os.path.getmtime(path) < min_age_sec:
                continue  # 最近使われた → 残す
            f = open(path, "a")  # "a": 既存内容を切り詰めない（保持中でも無害）
        except OSError:
            continue
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue  # 保持中 → 残す（finally で close）
            os.remove(path)
            removed += 1
        except OSError:
            pass
        finally:
            f.close()
    return removed


def sweep_tmp_files(root: str, min_age_sec: float = 300.0) -> int:
    """write_json_atomic が残した <path>.tmp.<pid> の残骸を掃除し、削除数を返す。
    正常時は即 os.replace されるので、残存＝書き込み中かクラッシュ由来。書き込み元 pid が
    死んでいる、または min_age_sec 以上古いものを消す（.git 配下は触らない）。"""
    if not os.path.isdir(root):
        return 0
    removed = 0
    now = time.time()
    for dirpath, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")  # git 内部には踏み込まない
        for fn in files:
            m = _TMP_SUFFIX_RE.search(fn)
            if not m:
                continue
            path = os.path.join(dirpath, fn)
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if _pid_alive(int(m.group(1))) and age < min_age_sec:
                continue  # 生存プロセスが書き込み中かも → 残す
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


_WORK_REPO_DIR_RE = re.compile(r"^kiro-flow-repos-(\d+)-")


def sweep_work_repo_dirs(min_age_sec: float = 3600.0) -> int:
    """SIGKILL/OOM/電源断で finally が走らず残った成果物リポジトリの孤立 clone を回収し、削除数を返す。
    名前に埋めた pid（`kiro-flow-repos-<pid>-…`）で所有プロセスの生死を判定し、**死んでいるものだけ**消す
    （稼働中・`--keep-alive` 長命 worker の clone は残す）。pid 再利用の誤判定を避けるため min_age も併用。"""
    root = tempfile.gettempdir()
    if not os.path.isdir(root):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(root):
        m = _WORK_REPO_DIR_RE.match(name)
        if not m:
            continue
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        try:
            age = now - os.path.getmtime(sub)
        except OSError:
            continue
        if _pid_alive(int(m.group(1))):
            continue  # 所有プロセス生存（--keep-alive 長命 worker 含む）→ 経過時間に関わらず残す
        if age < min_age_sec:
            continue  # 死亡判定でも作成直後は残す（pid 再利用の誤判定・終了直前 race の保険）
        shutil.rmtree(sub, ignore_errors=True)
        removed += 1
    return removed


def sweep_clone_dirs(bus_parent: str, keep_basename: str, min_age_sec: float) -> int:
    """git モードでノードごとに作られた孤立クローン（{bus}/<node>/）を削除し、削除数を返す。
    最近 git 操作のあったクローン（mtime が新しい＝稼働中）と、稼働デーモン自身の
    クローン（keep_basename）は残す。クローン以外（runs/inbox 等）は .git の有無で除外。"""
    if not os.path.isdir(bus_parent):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(bus_parent):
        if name == keep_basename:
            continue
        sub = os.path.join(bus_parent, name)
        gitdir = os.path.join(sub, ".git")
        if not os.path.exists(gitdir):
            continue  # クローンでない → 触らない
        try:
            ref = max(os.path.getmtime(sub), os.path.getmtime(gitdir))
        except OSError:
            continue
        if now - ref < min_age_sec:
            continue  # 最近使われた → 残す
        shutil.rmtree(sub, ignore_errors=True)
        removed += 1
    return removed


def run_cleanup(args, bus: Bus) -> dict:
    """A/B/C の一時ファイルをまとめて掃除し、{種別: 削除数} を返す。
    ロックは lease の 2 倍（最低 1h）アイドルなら確実に未使用。クローンは cleanup_age 時間。"""
    bus_parent = os.path.abspath(args.bus)
    lock_age = max(float(args.lease) * 2.0, 3600.0)
    n_lock = sweep_lock_files(lock_age)
    n_tmp = sweep_tmp_files(bus_parent)
    n_clone = 0
    if getattr(args, "git", None):  # 孤立クローンは git モードのみ存在する
        keep = os.path.basename(bus.workdir) if isinstance(bus, GitBus) else ""
        n_clone = sweep_clone_dirs(bus_parent, keep, float(args.cleanup_age) * 3600.0)
    # 成果物リポジトリの孤立 temp clone（pid 死亡）を回収（SIGKILL リーク対策・local/git 共通）
    n_work = sweep_work_repo_dirs(float(args.cleanup_age) * 3600.0)
    return {"locks": n_lock, "tmp": n_tmp, "clones": n_clone, "work_repos": n_work}


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


# 集約・最終ノード（sink）として優先する kind。これらがあれば最終成果とみなす。
_AGG_KINDS = ("synthesize", "reduce", "judge", "filter")


def _final_result_nodes(nodes: dict, results: dict) -> list:
    """ワークフローの最終成果に当たるノード id を返す。

    sink（他ノードの deps に現れない末端）かつ done のものを集め、集約 kind
    （synthesize/reduce/judge/filter）があればそれを優先する。末端が無い／done で
    ないときは done ノード全体へフォールバックする（最終結果を必ず何か返すため）。"""
    if not nodes:
        return []
    done = [nid for nid in nodes if (results.get(nid) or {}).get("status") == "done"]
    if not done:
        return []
    depended = {d for n in nodes.values() for d in n.get("deps", [])}
    sinks = [nid for nid in done if nid not in depended]
    pool = sinks or done
    agg = [nid for nid in pool if nodes[nid].get("kind") in _AGG_KINDS]
    return agg or pool


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
        node_results = {nid: bus.read_result(nid) or {} for nid in nodes}
        sink_ids = _final_result_nodes(nodes, node_results)
        if sink_ids:
            L.append("├─ result")
            for nid in sink_ids:
                out = str(node_results[nid].get("output", "")).strip()
                lines = out.splitlines() or ["(出力なし)"]
                L.append(f"│  ◆ {nid} [{nodes[nid].get('kind', 'work')}]")
                for line in lines[:10]:
                    L.append(f"│    {line[:96]}")
                if len(lines) > 10:
                    L.append(f"│    … (全 {len(lines)} 行 — 全文は `kiro-flow result` で)")
        else:
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


def cmd_result(args) -> int:
    """完了した run の最終結果を探し出して提示する。

    status が進捗ダッシュボードなのに対し、result は成果そのものを返す。
    最終成果＝集約／末端（sink）ノードの全文出力（`_final_result_nodes` で特定）。
    run_id 未指定なら最新 run を自動選択（status と同じ挙動）。未完了なら
    その旨を知らせ、確定済みの成果があれば参考表示する。"""
    if not args.run_id:
        resolved = _resolve_run_id(args)
        if not resolved:
            print("エラー: run が見つかりません。まず kiro-flow run を実行してください。",
                  file=sys.stderr)
            return 1
        args.run_id = resolved
        print(f"(run_id 未指定 — 最新の run: {args.run_id})", file=sys.stderr)

    bus = make_bus(args, "result-viewer")
    bus.sync_pull()
    status = bus.get_status()
    graph = bus.read_graph() or {}
    nodes = graph.get("nodes", {})
    results = {nid: (bus.read_result(nid) or {}) for nid in nodes}
    final_meta = read_json(bus.final_path) or {}
    request = final_meta.get("request") or bus.run_meta(args.run_id).get("request", "")
    sink_ids = _final_result_nodes(nodes, results)

    if getattr(args, "json", False):
        print(json.dumps({
            "run_id": args.run_id,
            "status": status,
            "done": status in TERMINAL,
            "request": request,
            "strategy": graph.get("strategy") or final_meta.get("strategy", {}),
            "finished_at": final_meta.get("finished_at"),
            "final_nodes": [
                {"id": nid, "kind": nodes.get(nid, {}).get("kind", "work"),
                 "output": str(results.get(nid, {}).get("output", "")),
                 "data": results.get(nid, {}).get("data"),
                 "artifacts": results.get(nid, {}).get("artifacts", [])}
                for nid in sink_ids
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if status not in TERMINAL:
        done_n = sum(1 for r in results.values() if r.get("status") in TERMINAL)
        print(f"run {args.run_id} はまだ完了していません（status={status}, "
              f"{done_n}/{len(nodes)} 完了）。"
              f"進捗は `kiro-flow status --run-id {args.run_id} --follow` で確認してください。",
              file=sys.stderr)
        if not sink_ids:
            return 0
        print("（現時点で確定している成果のみ表示します）")

    if not sink_ids:
        print("（最終結果がまだありません）")
        return 0

    print(f"== run {args.run_id} 最終結果 ==")
    if request:
        print(f"request : {request}")
    if final_meta.get("finished_at"):
        print(f"finished: {final_meta['finished_at']}")
    for nid in sink_ids:
        r = results.get(nid, {})
        kind = nodes.get(nid, {}).get("kind", "work")
        print(f"\n── {nid} [{kind}] ──")
        out = str(r.get("output", "")).strip()
        print(out or "(出力なし)")
        if r.get("data") is not None:
            print(f"[data] {json.dumps(r['data'], ensure_ascii=False)}")
        if r.get("artifacts"):
            print(f"[artifacts] {', '.join(r['artifacts'])}")
    return 0


# --------------------------------------------------------------------------
# finalize — verify 通過後の決定的な納品アクションを executor へ委譲する。
#   呼び出し元（kiro-autonomous）が done を確定（verify exit 0 ＋ 全ゲート通過）したあとに
#   呼ぶ。executor の finalize(delivery, action) を各ノードの result.data に対して実行する。
#   gitlab executor は MR をマージしイシューを明示的にクローズする（MR マージ≠イシュー
#   クローズなので別個に行う）。finalize を持たない executor（kiro 等・ローカルで push 済み）は
#   no-op。done を緩めない（マージは done の*帰結*）ので不変条件を破らない。
# --------------------------------------------------------------------------
def _node_deliveries(data) -> "list[dict]":
    """ノードの result.data から finalize 対象の delivery dict 群を取り出す。
    kiro: data.delivery=[{repo...}]（複数 repo）。gitlab: data 自体が {issue_iid,...}。"""
    if not isinstance(data, dict):
        return []
    d = data.get("delivery")
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    return [data]


def cmd_finalize(args) -> int:
    """run の各ノード result.data に対し executor の finalize を実行する。

    --node-id 指定で 1 ノードのみ。--action（既定 merge）を executor へ渡す。
    --delivery-json を渡すと **バスを読まず**その delivery（dict か dict の配列）を直接
    finalize する（人の承認が後日＝run/バスが gc 済みでも、保持しておいた delivery で
    確実に MR マージ＋イシュークローズできる）。finalize を持たない executor は no-op。
    1 件でも失敗すれば exit 1。"""
    fin = make_finalizer(args)
    action = getattr(args, "action", None) or "merge"
    # --delivery-json: バス非依存の直接 finalize（run/バスのライフサイクルから切り離す）
    raw = getattr(args, "delivery_json", None)
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"エラー: --delivery-json を解釈できません: {e}", file=sys.stderr)
            return 2
        deliveries = payload if isinstance(payload, list) else [payload]
        results: "list[dict]" = []
        for d in deliveries:
            if not isinstance(d, dict) or fin is None:
                continue
            try:
                out = fin(d, action)
                results.append({"ok": True, "result": out})
                log("finalize", json.dumps(out, ensure_ascii=False)[:200])
            except Exception as e:  # noqa: BLE001
                results.append({"ok": False, "error": str(e)})
                log("finalize", f"finalize 失敗: {e}")
        print(json.dumps({"action": action, "finalized": results},
                         ensure_ascii=False, indent=2))
        return 0 if all(r.get("ok", True) for r in results) else 1
    if not args.run_id:
        resolved = _resolve_run_id(args)
        if not resolved:
            print("エラー: run が見つかりません。", file=sys.stderr)
            return 1
        args.run_id = resolved
    bus = make_bus(args, "finalize")
    bus.sync_pull()
    graph = bus.read_graph() or {}
    nodes = list(graph.get("nodes", {}))
    node_ids = [args.node_id] if getattr(args, "node_id", None) else nodes
    results = []
    for nid in node_ids:
        r = bus.read_result(nid) or {}
        for d in _node_deliveries(r.get("data")):
            if fin is None:
                continue   # finalize 不要な executor（kiro 等）
            if not d.get("issue_iid") and not d.get("branch"):
                continue   # 委譲も成果ブランチも無い（調査系等）→ 対象外
            try:
                out = fin(d, action)
                results.append({"node": nid, "ok": True, "result": out})
                log("finalize", f"{nid}: {json.dumps(out, ensure_ascii=False)[:200]}")
            except Exception as e:  # noqa: BLE001 — 失敗は記録し他ノードは続行
                results.append({"node": nid, "ok": False, "error": str(e)})
                log("finalize", f"{nid}: finalize 失敗: {e}")
    bus.sync_push("finalize")
    print(json.dumps({"run_id": args.run_id, "action": action, "finalized": results},
                     ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok", True) for r in results) else 1


# --------------------------------------------------------------------------
# doctor（稼働診断）— bus 上の run（meta/events/results）と環境から稼働状況を
#   kiro-cli に診断させ、原因を env（ユーザー環境固有）/ config（設定）/
#   program（プログラム上の不具合）へ分類する。env/config は --fix で修正、program は
#   gitlab-idd スキルでイシュー起票（無ければ出力のみ）。収集・修正・起票の駆動は決定的、
#   診断と分類は kiro-cli へ委譲する。`kiro-flow doctor --json` は単独でも、
#   kiro-autonomous の doctor からの連携呼び出しでも使える（同一スキーマの findings を返す）。
# --------------------------------------------------------------------------
_DOCTOR_CATEGORIES = ("env", "config", "program")
_DOCTOR_SEVERITIES = ("critical", "warn", "info")
_DOCTOR_STUCK_HOURS = 2.0     # 非終端のまま放置された run を「滞留」とみなす目安（時間）
_DOCTOR_RECENT_RUNS = 10      # 診断で走査する直近 run 数


def _doctor_norm(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower()).strip()


def doctor_env_findings(args, which=shutil.which) -> "list[dict]":
    """環境/設定の決定的チェック（LLM 不要）。fix_action を持つものは --fix で修正できる。"""
    findings: list[dict] = []
    needs_cli = (getattr(args, "executor", "kiro") == "kiro"
                 or getattr(args, "planner", "") == "kiro")
    if needs_cli and not which("kiro-cli"):
        findings.append({
            "category": "env", "severity": "critical",
            "title": "kiro-cli が PATH に見つからない",
            "evidence": (f"executor={getattr(args, 'executor', '?')} "
                         f"planner={getattr(args, 'planner', '?')} は kiro-cli を要求する"),
            "fix": "kiro-cli をインストールして PATH を通す（暫定回避は --executor stub / --planner stub）"})
    if getattr(args, "git", None) and not which("git"):
        findings.append({
            "category": "env", "severity": "critical",
            "title": "git バスモードなのに git が見つからない",
            "evidence": f"git={args.git} の分散バスは git クローン/同期に git を使う",
            "fix": "git をインストールして PATH を通す（単一ノードなら --git を外す）"})
    bus_root = os.path.abspath(args.bus)
    parent = os.path.dirname(bus_root) or "."
    if not os.path.isdir(bus_root):
        findings.append({
            "category": "config", "severity": "info", "title": "バスのルートが未作成",
            "evidence": f"bus={bus_root}",
            "fix": "バスのルートを作成する（run 実行時にも自動作成される）",
            "fix_action": "ensure-bus"})
    elif not os.access(bus_root, os.W_OK):
        findings.append({
            "category": "env", "severity": "critical", "title": "バスのルートに書き込めない",
            "evidence": f"bus={bus_root} が書き込み不可",
            "fix": "バスのディレクトリの権限を修正するか、書き込める --bus を指定する"})
    if os.path.isdir(bus_root) and not os.access(parent, os.W_OK):
        findings.append({
            "category": "env", "severity": "warn", "title": "バスの親ディレクトリに書き込めない",
            "evidence": f"parent={parent}（一時ファイルの atomic 書き込みに影響）",
            "fix": "親ディレクトリの権限を確認する"})
    if int(getattr(args, "max_iterations", 3) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "critical", "title": "max_iterations が無限（≤0）",
            "evidence": f"max_iterations={getattr(args, 'max_iterations', None)}",
            "fix": "max_iterations を正の値にする（再計画の有限停止）"})
    if int(getattr(args, "max_retries", 3) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "warn", "title": "サーキットブレーカーが無効（max_retries≤0）",
            "evidence": f"max_retries={getattr(args, 'max_retries', None)}",
            "fix": "max_retries を正の値にする（達成不能な完了条件での無限作り直しを防ぐ）"})
    if float(getattr(args, "lease", 1800.0) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "warn", "title": "claim リースが非正（lease≤0）",
            "evidence": f"lease={getattr(args, 'lease', None)}",
            "fix": "lease を正の秒数にする（claim の横取り防止）"})
    if int(getattr(args, "argv_limit", 100000) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "info", "title": "argv_limit が無効（≤0）",
            "evidence": f"argv_limit={getattr(args, 'argv_limit', None)}",
            "fix": "argv_limit を正のバイト数にする（大きなプロンプトの ARG_MAX 回避）"})
    return findings


def collect_doctor_signals(args) -> dict:
    """bus 上の直近 run から滞留・失敗・再計画ループ・kiro-cli エラーを決定的に集める（有界）。"""
    probe = make_bus(args, "doctor")
    try:
        probe.sync_pull()
    except Exception:  # noqa: BLE001  バス取得失敗は env 所見側で拾う
        pass
    runs = probe.list_runs()
    metas = [(rid, probe.run_meta(rid)) for rid in runs]
    metas.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    recent = metas[:_DOCTOR_RECENT_RUNS]
    stuck, failed, errors = [], [], []
    for rid, meta in recent:
        st = meta.get("status")
        age = _age_hours(meta)
        view = probe.run_view(rid)
        nodes = (view.read_graph() or {}).get("nodes", {})
        node_states = {nid: view.node_state(nid) for nid in nodes}
        failed_nodes = [nid for nid, s in node_states.items() if s == "failed"]
        if st not in TERMINAL and age >= _DOCTOR_STUCK_HOURS:
            stuck.append({"run": rid, "status": st, "age_h": round(age, 1),
                          "claimed": sum(1 for s in node_states.values() if s == "claimed"),
                          "pending": sum(1 for s in node_states.values() if s == "pending")})
        if st == "failed" or failed_nodes:
            failed.append({"run": rid, "status": st, "failed_nodes": failed_nodes[:8],
                           "iteration": (view.read_graph() or {}).get("iteration", 0)})
        for e in view.recent_events(30):
            kind = str(e.get("kind", ""))
            msg = str(e.get("error") or e.get("detail") or "")
            if kind in ("error", "failed") or any(
                    k in msg for k in ("kiro-cli", "失敗", "Traceback", "タイムアウト", "Error")):
                errors.append({"run": rid, "who": e.get("who"), "kind": kind,
                               "msg": msg[:200]})
        for nid in failed_nodes[:3]:
            out = str((view.read_result(nid) or {}).get("output", ""))[:300]
            if out:
                errors.append({"run": rid, "node": nid, "output": out})
    return {
        "runs_total": len(runs),
        "recent": [{"run": rid, "status": m.get("status"),
                    "age_h": round(_age_hours(m), 1), "request": (m.get("request") or "")[:80]}
                   for rid, m in recent],
        "stuck": stuck[:10], "failed": failed[:10], "errors": errors[:20],
    }


def _doctor_prompt(signals: dict, deterministic: "list[dict]") -> str:
    sig = json.dumps(signals, ensure_ascii=False, indent=2)[:6000]
    det = json.dumps(deterministic, ensure_ascii=False, indent=2)[:2000]
    return (
        "あなたは分散 Dynamic Workflow エンジン（kiro-flow）の稼働診断医です。以下の run 状態・"
        "イベント・失敗出力・決定的チェックから稼働の問題を洗い出し、3カテゴリに分類してください。\n"
        "- env     : ユーザー環境固有（kiro-cli/git 不在・権限・PATH・worker/daemon 未起動・ネットワーク等）。\n"
        "- config  : 設定の問題（有限停止の無効化・矛盾した planner/executor・lease/argv_limit 不正等）。\n"
        "- program : kiro-flow 自体のプログラム上の不具合（想定外の例外・グラフ生成や claim/再計画の"
        "ロジック欠陥・正しい環境/設定でも再現する failed）。コード修正が必要でイシュー起票の対象。\n"
        "**判断は保守的に。** 滞留(stuck)は worker/daemon 未起動という env がよくある原因。env/config で"
        "説明できるものを安易に program にしない。\n\n"
        f"=== 決定的チェック（既出の所見・重複可）===\n{det}\n\n"
        f"=== 稼働シグナル（recent / stuck / failed / errors）===\n{sig}\n\n"
        "出力は次の形の JSON 配列だけ（説明文なし。問題が無ければ [] ）:\n"
        '[{"category":"env|config|program","severity":"critical|warn|info",'
        '"title":"簡潔な要約","evidence":"根拠（どの run/イベントか）",'
        '"fix":"env/config は具体的な修正手順 / program は不具合の説明と再現条件"}]')


def _parse_doctor_findings(text: str) -> "list[dict] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(arr, list):
        return None
    out: list[dict] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        cat = str(it.get("category", "")).strip().lower()
        if cat not in _DOCTOR_CATEGORIES:
            continue
        sev = str(it.get("severity", "warn")).strip().lower()
        out.append({
            "category": cat,
            "severity": sev if sev in _DOCTOR_SEVERITIES else "warn",
            "title": str(it.get("title", "")).strip()[:200],
            "evidence": str(it.get("evidence", "")).strip()[:600],
            "fix": str(it.get("fix", "")).strip()[:600],
            "source": "agent"})
    return out


def diagnose_with_agent(args, signals: dict, deterministic: "list[dict]",
                        kiro_run=None) -> "list[dict] | None":
    """kiro-cli に稼働を診断させ、分類済み finding を得る。kiro-cli 不在・解析不能は None。"""
    run = kiro_run or run_kiro
    try:
        out = run(_doctor_prompt(signals, deterministic), getattr(args, "model", None))
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等
        return None
    return _parse_doctor_findings(out)


def _dedupe_findings(findings: "list[dict]") -> "list[dict]":
    """(category, 正規化 title) で重複を畳む（決定的チェックを優先して残す）。"""
    seen: dict = {}
    for f in findings:
        key = (f["category"], _doctor_norm(f.get("title", "")))
        if key not in seen:
            seen[key] = f
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(seen.values(),
                  key=lambda f: (_DOCTOR_CATEGORIES.index(f["category"]),
                                 order.get(f["severity"], 1)))


def find_skill(name: str, home: "str | None" = None) -> "str | None":
    """名前付きスキルのディレクトリを探す（無ければ None）。検索順: $KIRO_SKILLS_HOME →
    cwd から上方向の .github/skills → ~/.kiro/skills → ~/.claude/skills → ~/.github/skills。"""
    cands: list[str] = []
    env = home or os.environ.get("KIRO_SKILLS_HOME")
    if env:
        cands.append(os.path.join(os.path.expanduser(env), name))
    cur = os.getcwd()
    while True:
        cands.append(os.path.join(cur, ".github", "skills", name))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for base in ("~/.kiro/skills", "~/.claude/skills", "~/.github/skills"):
        cands.append(os.path.join(os.path.expanduser(base), name))
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def apply_doctor_fix(args, finding: dict) -> str:
    """env/config の finding を決定的に修正する（既知の fix_action のみ）。結果文を返す。"""
    if finding.get("fix_action") == "ensure-bus":
        os.makedirs(os.path.abspath(args.bus), exist_ok=True)
        return f"バスのルートを作成しました（{os.path.abspath(args.bus)}）"
    return ""


def file_issues_via_gitlab_idd(args, program: "list[dict]", skill_dir: str,
                               kiro_run=None) -> bool:
    """program カテゴリの不具合を gitlab-idd スキルのリクエスター役で起票させる（kiro-cli 委譲）。"""
    run = kiro_run or run_kiro
    items = "\n".join(
        f"{i}. {f['title']}\n   - 根拠: {f.get('evidence', '')}\n   - 詳細: {f.get('fix', '')}"
        for i, f in enumerate(program, 1))
    prompt = (
        "あなたは gitlab-idd スキルのリクエスター役です。kiro-flow の稼働診断で見つかった"
        "『プログラム上の不具合』について、gitlab-idd スキルの手順に従い GitLab イシューを起票して"
        f"ください（スキル: {skill_dir}）。各不具合ごとに目的・再現条件・『## 受け入れ条件』を含む"
        "1 イシューを作成し、既に同一不具合のイシューがあれば重複起票しないこと。\n\n"
        f"=== 不具合一覧 ===\n{items}")
    try:
        run(prompt, getattr(args, "model", None))
        return True
    except Exception:  # noqa: BLE001  kiro-cli 不在・失敗 → 起票せず（呼び出し側で出力）
        return False


def cmd_doctor(args, kiro_run=None, skill_finder=find_skill) -> int:
    """稼働を診断し env/config を（--fix で）修正、program は gitlab-idd で起票する。
    終了コード: 0=健康 / 1=未解決の所見あり / 2=未解決の critical あり。"""
    fix = bool(getattr(args, "fix", False))
    as_json = bool(getattr(args, "json", False))
    deterministic = doctor_env_findings(args)
    for f in deterministic:
        f["source"] = "check"
    signals = collect_doctor_signals(args)
    agent = diagnose_with_agent(args, signals, deterministic, kiro_run=kiro_run)
    findings = _dedupe_findings(deterministic + (agent or []))

    applied: list = []
    if fix:
        for f in findings:
            if f["category"] in ("env", "config"):
                msg = apply_doctor_fix(args, f)
                if msg:
                    f["resolved"] = msg
                    applied.append(f)
        still = {(g["category"], _doctor_norm(g.get("title", "")))
                 for g in doctor_env_findings(args)}
        for f in findings:
            if f.get("source") == "check" and not f.get("resolved"):
                if (f["category"], _doctor_norm(f.get("title", ""))) not in still:
                    f["resolved"] = "修正により解消"

    program = [f for f in findings if f["category"] == "program"]
    skill_dir = skill_finder("gitlab-idd")
    filed = False
    if fix and program:
        if skill_dir:
            filed = file_issues_via_gitlab_idd(args, program, skill_dir, kiro_run=kiro_run)
            if filed:
                for f in program:
                    f["resolved"] = f"gitlab-idd で起票（{os.path.basename(skill_dir)}）"

    unresolved = [f for f in findings if not f.get("resolved")]
    has_critical = any(f["severity"] == "critical" for f in unresolved)
    code = 2 if has_critical else (1 if unresolved else 0)

    if as_json:
        print(json.dumps({
            "tool": "kiro-flow", "agent_used": agent is not None,
            "skill_available": bool(skill_dir), "fix": fix, "findings": findings,
            "applied": len(applied), "issues_filed": filed, "unresolved": len(unresolved),
        }, ensure_ascii=False, indent=2))
        return code

    print("=== kiro-flow doctor（稼働診断）===")
    print(f"診断: {'kiro-cli' if agent is not None else '決定的チェックのみ（kiro-cli 不在/解析不能）'}"
          f"  / 所見 {len(findings)} 件")
    if not findings:
        print("問題は見つかりませんでした（healthy）。")
        return 0
    label = {"env": "環境", "config": "設定", "program": "プログラム"}
    mark = {"critical": "✗", "warn": "−", "info": "·"}
    for cat in _DOCTOR_CATEGORIES:
        group = [f for f in findings if f["category"] == cat]
        if not group:
            continue
        print(f"\n[{label[cat]}] {len(group)} 件")
        for f in group:
            print(f"  {mark.get(f['severity'], '−')} {f['title']}")
            if f.get("evidence"):
                print(f"      根拠: {f['evidence']}")
            if f.get("fix"):
                print(f"      対処: {f['fix']}")
            if f.get("resolved"):
                print(f"      ✓ {f['resolved']}")
    print()
    if fix:
        print(f"修正: env/config {len(applied)} 件を適用。")
        if program:
            if skill_dir and filed:
                print(f"起票: program {len(program)} 件を gitlab-idd で起票しました。")
            elif skill_dir and not filed:
                print(f"起票: gitlab-idd への委譲に失敗（kiro-cli 不在等）。program "
                      f"{len(program)} 件は未起票です。")
            else:
                print(f"起票: gitlab-idd スキルが見つからないため、program {len(program)} 件は"
                      f"出力のみ（イシュー未起票）。")
    else:
        print("（--fix で env/config の修正と program のイシュー起票を実行します）")
    return code


# --------------------------------------------------------------------------
def self_path() -> str:
    return os.path.abspath(__file__)


# --------------------------------------------------------------------------
# 自動アップデート — スキルリポジトリ（main）の更新を取り込み graceful 再起動する
# --------------------------------------------------------------------------
# doctor と同じ流儀（知能は委譲・操作は決定的）で、本体は「決定的な取り込み」だけを行う:
#   1. git ls-remote でスキルリポジトリ main の最新コミットを得る
#   2. 適用済み SHA（state ファイル）と違えば「更新あり」
#   3. アイドル時に temp 領域へ sparse-checkout（このツールの tools/kiro-flow/ だけ）
#   4. install.sh を実行して ~/.local/bin の本体を更新
#   5. 動いていた cwd のまま os.execv で新しい本体へ graceful 再起動
# update_repo 未設定 or update_check_interval<=0 のときは完全に無効（既定 off）。
def _update_state_path() -> str:
    base = os.environ.get("KIRO_STATE_HOME") or os.path.expanduser("~/.kiro")
    return os.path.join(base, "kiro-flow.update.json")


def read_update_state() -> dict:
    return read_json(_update_state_path()) or {}


def write_update_state(state: dict) -> None:
    write_json_atomic(_update_state_path(), state)


def remote_branch_sha(repo: str, branch: str, runner=None) -> "str | None":
    """git ls-remote でリモート branch の先頭コミット SHA を得る（取得不能なら None）。"""
    if not repo:
        return None
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=60))
    try:
        r = run(["git", "ls-remote", repo, f"refs/heads/{branch}"])
    except Exception:  # noqa: BLE001  git 不在・ネットワーク不通・タイムアウト
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    line = (getattr(r, "stdout", "") or "").strip().splitlines()
    if not line:
        return None
    sha = line[0].split()[0].strip()
    return sha if len(sha) >= 7 else None


def find_skill_registry(home: "str | None" = None) -> "str | None":
    """install.py が生成する skill-registry.json を探す（無ければ None）。
    $KIRO_SKILL_REGISTRY（ファイル or ディレクトリ）が指定されていれば**それを権威として使い**
    （フォールバックしない）、未指定なら各エージェントホーム（~/.kiro / ~/.claude 等）を探す。"""
    env = home or os.environ.get("KIRO_SKILL_REGISTRY")
    if env:
        p = os.path.expanduser(env)
        cand = os.path.join(p, "skill-registry.json") if os.path.isdir(p) else p
        return cand if os.path.isfile(cand) else None
    for d in _AGENT_HOME_DIRS:
        c = os.path.join(os.path.expanduser("~"), d, "skill-registry.json")
        if os.path.isfile(c):
            return c
    return None


def registry_update_source(registry: "str | None" = None) -> "tuple[str | None, str | None]":
    """skill-registry.json からスキルリポジトリの (url, branch) を解決する（無ければ (None, None)）。
    repositories の origin（無ければ priority 昇順の先頭）を採り、url が無ければ install_dir
    （インストール元のローカルクローン＝『自動更新の参照元』）にフォールバックする。"""
    path = registry or find_skill_registry()
    if not path or not os.path.isfile(path):
        return (None, None)
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return (None, None)
    repos = reg.get("repositories") or []
    chosen = next((r for r in repos if r.get("name") == "origin"), None)
    if chosen is None and repos:
        chosen = sorted(repos, key=lambda r: r.get("priority", 99))[0]
    if chosen and chosen.get("url"):
        return (chosen["url"], chosen.get("branch") or "main")
    idir = reg.get("install_dir")               # フォールバック: ローカルクローンを直接 clone 元に
    if idir and os.path.isdir(idir):
        return (idir, (chosen.get("branch") if chosen else None) or "main")
    return (None, None)


def resolve_update_target(args) -> "tuple[str, str]":
    """更新元リポジトリと branch を確定する。優先順位 設定の update_repo > skill-registry.json > 無効。
    update_repo 未指定（自動）のときは registry の branch を採用（設定 update_branch が既定 main のまま時）。"""
    repo = getattr(args, "update_repo", "") or ""
    branch = getattr(args, "update_branch", "main") or "main"
    if not repo:
        rurl, rbranch = registry_update_source()
        if rurl:
            repo = rurl
            if rbranch and branch == "main":     # 設定で branch を変えていなければ registry を採用
                branch = rbranch
    return repo, branch


def check_update(args, runner=None) -> dict:
    """更新の有無を判定する（取り込みはしない）。戻り値の dict:
      {enabled, repo, branch, remote_sha, applied_sha, available, baseline}
    repo は設定 update_repo か skill-registry.json から解決する。
    初回（applied_sha 未記録）は現在の本体を最新とみなし remote_sha をベースライン記録して
    available=False を返す（無用な初回更新ループを避ける）。"""
    repo, branch = resolve_update_target(args)
    info = {"enabled": bool(repo), "repo": repo, "branch": branch, "remote_sha": None,
            "applied_sha": None, "available": False, "baseline": False}
    if not repo:
        return info
    state = read_update_state()
    info["applied_sha"] = state.get("applied_sha")
    remote = remote_branch_sha(repo, branch, runner=runner)
    info["remote_sha"] = remote
    if not remote:
        return info
    if not info["applied_sha"]:
        state["applied_sha"] = remote
        state["baseline_at"] = now_iso()
        write_update_state(state)
        info["applied_sha"] = remote
        info["baseline"] = True
        return info
    info["available"] = (remote != info["applied_sha"])
    return info


def sparse_checkout_tool(repo: str, branch: str, subdir: str, dest: str, runner=None) -> str:
    """repo の branch から subdir 以下だけを dest へ sparse-checkout し dest/subdir のパスを返す。
    無関係ファイルを取得しないため --no-checkout + blob フィルタ + sparse-checkout を使う。"""
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   timeout=600, **k))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    r = run(["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none",
             "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:   # blob フィルタ非対応サーバ向けフォールバック
        r = run(["git", "clone", "--no-checkout", "--depth", "1", "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:
        raise RuntimeError(f"git clone 失敗: {(getattr(r, 'stderr', '') or '').strip()[:300]}")

    def g(cmd):
        return run(["git", "-C", dest] + cmd)
    g(["sparse-checkout", "init", "--cone"])
    g(["sparse-checkout", "set", subdir])
    co = g(["checkout", branch])
    if getattr(co, "returncode", 1) != 0:
        raise RuntimeError(f"git checkout 失敗: {(getattr(co, 'stderr', '') or '').strip()[:300]}")
    tool_dir = os.path.join(dest, subdir)
    if not os.path.isdir(tool_dir):
        raise RuntimeError(f"sparse-checkout 後に {subdir} が見つかりません（リポジトリ構成を確認）")
    return tool_dir


def run_installer(tool_dir: str, installer: str = "install.sh", runner=None) -> "tuple[bool, str]":
    """tool_dir 内の installer を実行して本体を更新する。(成功, 末尾出力) を返す。"""
    path = os.path.join(tool_dir, installer)
    if not os.path.isfile(path):
        return False, f"インストーラが見つかりません: {path}"
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   timeout=600, **k))
    try:
        r = run(["bash", path], cwd=tool_dir)
    except Exception as e:  # noqa: BLE001
        return False, f"インストーラ実行に失敗: {e}"
    out = ((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")).strip()
    return getattr(r, "returncode", 1) == 0, out[-2000:]


def apply_update(args, info: dict, runner=None) -> bool:
    """temp 領域へ sparse-checkout → install.sh → 適用済み SHA を記録。成功で True。
    temp は必ず後始末する。失敗時は state を変えない（次回再試行）。"""
    subdir = getattr(args, "update_subdir", TOOL_SUBDIR) or TOOL_SUBDIR
    installer = getattr(args, "update_installer", "install.sh") or "install.sh"
    tmp = tempfile.mkdtemp(prefix="kiro-flow-update-")
    dest = os.path.join(tmp, "repo")
    try:
        tool_dir = sparse_checkout_tool(info["repo"], info["branch"], subdir, dest, runner=runner)
        ok, out = run_installer(tool_dir, installer, runner=runner)
        if not ok:
            log("update", f"install.sh 失敗（更新を見送り）: {out[-300:]}")
            return False
        state = read_update_state()
        state["applied_sha"] = info["remote_sha"]
        state["applied_at"] = now_iso()
        write_update_state(state)
        log("update", f"更新を適用しました（{info['remote_sha'][:8]}）。")
        return True
    except Exception as e:  # noqa: BLE001  clone/checkout/installer の失敗は次回再試行
        log("update", f"更新の取り込みに失敗（次回再試行）: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_self(cwd: "str | None" = None) -> None:
    """更新後の本体へ os.execv で graceful 再起動する。動いていた cwd を保ったまま起動し直す。"""
    if cwd and os.path.isdir(cwd):
        try:
            os.chdir(cwd)
        except OSError:
            pass
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, self_path()] + sys.argv[1:])


def maybe_self_update(args, idle: bool, state: dict, runner=None) -> bool:
    """daemon のループから定期的に呼ぶ自己更新チェック。更新を適用したら True
    （呼び出し側は graceful shutdown して restart_self する）。
    state は {"last": <epoch>} を持つ可変 dict（呼び出し側がループ間で保持）。
    update_enabled=false / update_check_interval<=0 で無効。アイドルでなければ何もしない。"""
    if not getattr(args, "update_enabled", True):
        return False
    interval = float(getattr(args, "update_check_interval", 0) or 0)
    if interval <= 0 or not idle:
        return False
    now = time.time()
    if now - state.get("last", 0.0) < interval:
        return False
    state["last"] = now
    info = check_update(args, runner=runner)
    if not info.get("available"):
        return False
    log("update", f"スキルリポジトリ {info['branch']} に更新を検出: "
                  f"{(info['applied_sha'] or '')[:8]} → {(info['remote_sha'] or '')[:8]}")
    return apply_update(args, info, runner=runner)


def cmd_update(args) -> int:
    """手動アップデート: 更新の有無を確認し、--now で取り込んで再起動する。
    終了コード: 0=最新/ベースライン記録/更新あり表示 / 1=取り込み失敗 / 2=未設定・取得不能。"""
    info = check_update(args)
    if not info["enabled"]:
        print("[kiro-flow] update: update_repo が未設定です（設定ファイルで指定してください）。",
              file=sys.stderr)
        return 2
    if info["remote_sha"] is None:
        print(f"[kiro-flow] update: リモート {info['repo']}@{info['branch']} を取得できませんでした。",
              file=sys.stderr)
        return 2
    if info.get("baseline"):
        print(f"[kiro-flow] update: ベースラインを記録しました（{info['remote_sha'][:8]}）。"
              "以降この地点からの更新を検出します。")
        return 0
    if not info["available"]:
        print(f"[kiro-flow] update: 最新です（{info['applied_sha'][:8]}）。")
        return 0
    print(f"[kiro-flow] update: 更新があります {info['applied_sha'][:8]} → {info['remote_sha'][:8]}")
    if getattr(args, "check", False) or not getattr(args, "now", False):
        print("  取り込むには `kiro-flow update --now` を実行してください。")
        return 0
    if apply_update(args, info):
        print("  install.sh を実行して更新しました。再起動します。")
        restart_self(os.getcwd())   # 戻らない
    print("  更新の取り込みに失敗しました（ログを確認してください）。", file=sys.stderr)
    return 1


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
    p.add_argument("--lock-dir", dest="lock_dir", default=None,
                   help="daemon singleton ロックの置き場（設定ファイル lock_dir と同義。"
                        "外部起動の daemon を別ツールから発見させるため起動側と一致させる）")
    p.add_argument("--executor-dir", dest="executor_dir", default=None,
                   help="executor プラグイン（<name>.py）の追加検索ディレクトリ（設定 executor_dir と同義）")
    p.add_argument("--repo", dest="repos", action="append", default=None,
                   help="成果物リポジトリ（複数指定可）。素の URL でも、構造化 JSON "
                        "（{url,name,path,base,target,role,readonly,desc}）でも可。worker が temp 領域へ"
                        " clone（base 指定があればそのブランチ）してから作業し、作業後に必ず消す。"
                        "role は write（成果物コミット先・単一）/ read（参照のみ・push しない・複数可）/ "
                        "未指定（auto＝executor が判断）。readonly:true は role:read と同義。"
                        "path はモノレポの作業フォルダを表す")
    p.add_argument("--granularity", default=None, choices=["coarse", "fine", "finest"],
                   help="タスク分解の細かさ（設定 granularity と同義）。coarse=現状 / fine=1段細かい / "
                        "finest=2段細かい（既定）。細かいほど小さなタスクに多く分解する")
    p.add_argument("--exemplar-first", dest="exemplar_first", action="store_const", const=True,
                   default=None,
                   help="map-reduce の fan-out を見本先行にする（設定 exemplar_first と同義）。"
                        "先頭1件を検証ゲートに通してから残りを展開し、同様手順を1件で固めてから流す")
    p.add_argument("--lease", type=float, default=None,
                   help="claim のリース秒数（超過すると他ノードが再 claim 可能。既定 1800）")
    p.add_argument("--argv-limit", dest="argv_limit", type=int, default=None,
                   help="kiro-cli へ argv で渡すプロンプトの最大バイト数（設定 argv_limit と同義）。"
                        "超過分は一時ファイルへ退避し参照渡しにする（既定 100000）")
    p.add_argument("--keep-clone", dest="cleanup_clone", action="store_const", const=False,
                   default=None,
                   help="作業後に sparse-checkout クローンを削除せず残す（既定: 削除して再利用しない）")
    p.add_argument("--cleanup-per-node", dest="cleanup_per_node", action="store_const", const=True,
                   default=None,
                   help="各ノード完了後に成果物リポジトリの clone を即削除する（設定 cleanup_per_node と同義）。"
                        "長命 worker（--keep-alive）のディスク積み上がりを抑える（既定: worker 終了時に一括削除）")
    # サブコマンド未指定なら daemon として扱う（required=False）
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="単発実行。既存 --run-id なら再開、無ければ新規（状態で自動判断）")
    run.add_argument("request", nargs="?", default=None,
                     help="ワークフローへの要求（再開時は省略可）")
    run.add_argument("--workers", type=int, default=None)
    run.add_argument("--planner", choices=["kiro", "stub", "flow-planner"], default=None)
    run.add_argument("--executor", default=None,
                     help="ワーカーバス: 組み込み kiro / stub、または executor プラグイン名"
                          "（例 gitlab）/ .py パス（opt-in。gitlab はタスクを GitLab イシューに"
                          "して委譲し approved まで待つ）")
    run.add_argument("--max-iterations", type=int, default=None,
                     help="再計画（evaluator-optimizer）の最大反復回数")
    run.add_argument("--max-fanout", type=int, default=None,
                     help="データ駆動 fan-out の最大展開数（既定 50）")
    run.add_argument("--max-retries", type=int, default=None,
                     help="同一系統の作り直し打ち切り回数（サーキットブレーカー, 既定 3）")
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
    orch.add_argument("--executor", default=None,
                      help="ワーカーバス（kiro/stub/プラグイン名/.py パス）。"
                           "評価役（evaluator）は stub 以外ならローカル kiro で判断")
    orch.add_argument("--max-iterations", type=int, default=None)
    orch.add_argument("--max-fanout", type=int, default=None)
    orch.add_argument("--max-retries", type=int, default=None)
    orch.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    orch.add_argument("--no-review", dest="review", action="store_const", const=False)
    orch.add_argument("--node-id", default="orchestrator")
    orch.add_argument("--model_opt", dest="model", default=None)
    orch.add_argument("--poll", type=float, default=None)
    orch.set_defaults(func=cmd_orchestrate)

    work = sub.add_parser("work", help="ワーカー役")
    work.add_argument("--node-id", default=f"{socket.gethostname()}-{os.getpid()}")
    work.add_argument("--executor", default=None,
                      help="ワーカーバス（kiro/stub/プラグイン名/.py パス）")
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
    dm.add_argument("--executor", default=None,
                    help="ワーカーバス（kiro/stub/プラグイン名/.py パス）")
    dm.add_argument("--max-iterations", type=int, default=None)
    dm.add_argument("--max-fanout", type=int, default=None)
    dm.add_argument("--max-retries", type=int, default=None)
    dm.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    dm.add_argument("--no-review", dest="review", action="store_const", const=False)
    dm.add_argument("--model", default=None)
    dm.add_argument("--poll", type=float, default=None)
    dm.add_argument("--cleanup-interval", dest="cleanup_interval", type=float, default=None,
                    help="一時ファイル自動掃除の実行間隔（秒, 既定 3600）。0 以下で無効化")
    dm.add_argument("--cleanup-age", dest="cleanup_age", type=float, default=None,
                    help="孤立クローンを掃除するまでのアイドル時間（時間, 既定 24）")
    dm.add_argument("--no-cleanup", dest="cleanup_interval", action="store_const", const=0.0,
                    help="一時ファイルの自動掃除を無効化する")
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

    rs = sub.add_parser("result",
                        help="完了した run の最終結果を探して提示（status 相当・進捗でなく成果を返す）")
    rs.add_argument("--json", action="store_true", help="機械可読な JSON で出力")
    rs.set_defaults(func=cmd_result)

    fin = sub.add_parser("finalize",
                         help="verify 通過後の納品アクションを executor へ委譲（gitlab: MR マージ＋"
                              "イシュークローズ）。done の*帰結*であって done を確定するものではない")
    fin.add_argument("--executor", default=None,
                     help="ワーカーバス（kiro/stub/プラグイン名/.py パス）。finalize を持つ "
                          "executor（例 gitlab）でのみ実効。kiro 等は no-op")
    fin.add_argument("--node-id", default=None, help="このノードのみ finalize（既定: 全ノード）")
    fin.add_argument("--action", default=None, choices=["merge", "close"],
                     help="finalize アクション（既定 merge＝MR マージ＋イシュークローズ）")
    fin.add_argument("--delivery-json", default=None,
                     help="バスを読まず、この delivery（JSON の dict か配列）を直接 finalize する。"
                          "人の承認が後日（run が gc 済み）でも保持した delivery で確実に納品確定できる")
    fin.set_defaults(func=cmd_finalize)

    gc = sub.add_parser("gc", help="古い run を掃除（対応する inbox 要求・claim も削除）")
    gc.add_argument("--older-than", type=float, default=7.0, help="この日数より古い run が対象")
    gc.add_argument("--keep", type=int, default=3, help="新しい順にこの件数は無条件で保護")
    gc.add_argument("--status", default=None, help="この status の run のみ対象（例: done）")
    gc.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示")
    gc.set_defaults(func=cmd_gc)

    dr = sub.add_parser("doctor", help="ログ/状態/環境から稼働を診断（kiro-cli）。env/config は "
                                       "--fix で修正・program は gitlab-idd でイシュー起票")
    dr.add_argument("--json", action="store_true", help="JSON で出力（連携呼び出し用の findings を含む）")
    dr.add_argument("--fix", action="store_true",
                    help="env/config の問題を修正し、program の不具合を gitlab-idd で起票"
                         "（スキルが無ければ出力のみ。既定は診断のみ）")
    dr.set_defaults(func=cmd_doctor)

    up = sub.add_parser("update",
                        help="スキルリポジトリ(main)の更新を確認。--now で temp に sparse-checkout "
                             "して install.sh を実行し再起動する")
    up.add_argument("--now", action="store_true",
                    help="更新があれば即座に install.sh を実行して再起動する")
    up.add_argument("--check", action="store_true", help="更新の有無だけを表示（取り込まない）")
    up.set_defaults(func=cmd_update)

    args = p.parse_args()
    # CLI 未指定の設定値を設定ファイル→組み込み既定で確定（CLI > config > 既定）
    resolve_config(args)
    # args を持たない free 関数（run_kiro 等）が読む閾値をモジュール変数へ確定させる
    _configure_thresholds(args)
    # 成果物リポジトリ clone の削除を二重化（main の finally に加え、想定外の早期 exit でも回収）
    atexit.register(cleanup_work_repos)
    # 子プロセスから渡る空文字の --model_opt は「モデル指定なし」を意味する
    if getattr(args, "model", None) == "":
        args.model = None
    # executor の早期検証: 不正名のまま worker を起動すると run がハングするため、
    # 親プロセスでプラグイン解決を試し、解決できなければここで明確に失敗する。
    spec = getattr(args, "executor", None)
    if spec and spec not in BUILTIN_EXECUTORS and _resolve_executor_plugin(spec) is None:
        dirs = "、".join(_executor_search_dirs())
        print(f"[kiro-flow] executor '{spec}' を解決できません。組み込み（kiro/stub）か、"
              f"プラグイン .py（検索: {dirs}）か、明示パスを指定してください。", file=sys.stderr)
        return 2
    # サブコマンド未指定 → daemon として処理
    try:
        if getattr(args, "func", None) is None:
            args.node_id = getattr(args, "node_id", None)
            return cmd_daemon(args)
        return args.func(args)
    finally:
        # 作業後に sparse-checkout クローンを削除する（--keep-clone で抑止可）
        if getattr(args, "cleanup_clone", True):
            cleanup_active_clones()
        cleanup_work_repos()   # 成果物リポジトリの clone は常に消す（作業後クリーンは必須）


if __name__ == "__main__":
    sys.exit(main())
