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

# 終端 status（これに達した run は active_runs から外れ、孤児 reclaim も resume しない）。
# canceled は人の明示指示（cmd_cancel）による恒久停止。done/failed と同じく終端だが、
# 「成果あり(done)」でも「異常(failed)」でもない「意図的な打ち切り」を表す。
TERMINAL = {"done", "failed", "canceled"}


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
    # バスはカレントディレクトリ（=プロジェクトルート）直下の bus/。kiro-project の既定
    # <root>/bus と同じ場所を指す（1 root = 1 プロジェクト・root 相対で両ツールが一致する）。
    "bus": "./bus",
    "git": None,
    "git_branch": "main",
    "git_subdir": "",
    "lock_dir": None,   # daemon singleton ロックの置き場（外部 daemon の発見性を担保。既定 tempdir 配下）
    # 状態の git 保存・共有（state_git）: ローカルバスのワーク内容（runs/・inbox/）を共有 git
    # リポジトリへ双方向同期し、リモートの kiro-projects-viewer が run の進捗/結果を読めるようにする。
    # GitBus（--git）とは独立（--git 指定時はバス自体が共有 git なので state_git は無視される）。
    "state_git": None,                  # 共有リポジトリ（URL/パス）。None で無効
    "state_git_branch": "main",         # 同期先ブランチ
    "state_git_subdir": "kiro-flow",    # リポジトリ内の保存先サブディレクトリ（多重コミッタとの名前空間分離）
    "state_git_interval": 300.0,        # fetch/push の最短間隔（秒）。0 で毎同期（リモート負荷は増える）
    "status_interval": 0.0,             # daemon アイドル中の status.json 生存信号更新間隔（秒）。既定 0=無効
    "lease": 1800.0,
    "poll": 2.0,
    "model": None,
    # LLM 実行に使うエージェント CLI: kiro（kiro-cli chat）/ claude（Claude Code `claude -p`）/
    # copilot（GitHub Copilot CLI `copilot -p`）/ codex（OpenAI Codex CLI `codex exec`）。
    # planner・executor・verify 等、このツールが行う LLM 呼び出しすべてに効く。
    "agent_cli": "kiro",
    # 役割毎のエージェント上書き（yaml 専用）。キーは planner / evaluator / worker（全 kind の
    # 既定）/ 個別 kind（work/generate/classify/synthesize/verify/filter/judge/reduce/split/map）、
    # 値は {agent_cli, model}。未指定はグローバル agent_cli / model。
    "agents": {},
    "planner": "flow-planner",
    "executor": "agent",
    # executor=agent の実行系プロンプトを供給するスキル（worker/verify/evaluator）。
    # flow-planner と同じ検索順で自動発見し、見つからなければ組み込みプロンプトに
    # フォールバックする。none/builtin/空 で常に組み込みを使う（yaml 専用）。
    "worker_skill": "flow-worker",
    "granularity": "finest",   # 分解の細かさ: coarse(現状)/fine(1段細)/finest(2段細・既定)
    "exemplar_first": False,   # map-reduce で「1件先行→検証ゲート→残り展開」の見本先行分解にする
    "max_workers": 4,
    # daemon が同時に実行する run（orchestrator プロセス）の上限。バックログ一括投入
    # （kiro-project の act_async 等）や再起動直後の孤児一斉再開で「run 数ぶんの orchestrator
    # ＋計画エージェント」が同時に立ち上がるのを防ぐ。全ノードが park（承認待ち等）の run は
    # worker も計画エージェントも使わないため枠に数えない（gitlab 長期委譲は上限で詰まらない）。
    # 超過した要求は inbox に残り、枠が空いた poll で受理される（取りこぼさない）。
    # 0 以下で無制限（従来動作）。
    "max_runs": 8,
    "max_iterations": 3,
    "max_fanout": 50,
    # judge/評価役のサーキットブレーカー: 同一系統（verify/失敗）の作り直しをこの回数で打ち切る。
    # 達成不可能な完了条件で無限に再タスクを生み続けるのを防ぐ（max_iterations と二重ガード）。
    "max_retries": 3,
    # 孤児 run（owning daemon の消失＝PC シャットダウン・クラッシュ等）の自動再開の上限。
    # 「前回の再開から進捗（新しい results/）ゼロのままの連続再開」をこの回数で打ち切り
    # failed に確定する（起動のたびに即死する壊れた run を無限に蘇生しない）。進捗があれば
    # 数え直すため、毎日シャットダウンされる PC 上の長期 run は何日でも再開を継続できる。
    # 0 以下で自動再開を無効化（従来どおり孤児は即 failed）。
    "max_resumes": 3,
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
    # ラベル（レビュー承認）が付いたら、クリーンな関連 MR（コンフリクト無し・未解決レビュー
    # コメント無し）を**自動マージしてイシューをクローズ**する（auto_merge・既定 on。
    # gitlab-review-viewer の承認ボタンと同じ規則。false で従来の人マージ待ちに戻す）。
    # イシュー API は GitLab REST を stdlib で直叩き（gl.py 不要・フォールバックもしない）。
    # 起票先 URL は repo_url が権威（git origin へ流れない）。トークンはここには置かず、
    # gl.py と同じ場所（connections.yaml / 環境変数 GITLAB_TOKEN・GL_TOKEN / シェル rc）から解決する。
    # ※ 自動マージには api スコープのトークンが必要（read 系のみだとマージで 403 になり、
    #   人が GitLab 上でマージするまで待ち続ける）。
    "gitlab": {
        "conn_label": "default",            # connections.yaml の接続ラベル（トークン解決に使用）
        "repo_url": "",                     # 起票先プロジェクト URL（権威）。必ずこの URL を使う
        "labels": "status:open,assignee:any",  # 起票するイシューに付ける初期ラベル
        "priority": "priority:normal",      # 付与する優先度ラベル（空文字で付けない）
        "poll_interval": 300.0,             # イシュー1件の最短再確認間隔（秒）。レビューは遅延しうる
                                            # 前提で即応性は求めない（十分待つ）
        # 完了＝approved のクリーンな MR を自動マージ＝イシュークローズ。
        # レビュー往復は時間がかかるため待機は長めにする（0/負で無限）。
        # gitlab executor プラグインの _DEFAULTS と一致させる（以前ここだけ 86400 で食い違っていた）。
        "timeout": 604800.0,                # 全体タイムアウト（既定 7 日）。決着に至るまでの上限
        "approved_timeout": 1209600.0,      # レビュー活動検知後の猶予（既定 14 日）
        "approved_label": "status:approved",  # この状態に達したら自動マージ判定に入る（= 受け入れ承認）
        "done_label": "status:done",        # approved 以外に完了とみなすラベル
        "auto_merge": True,                 # 自動承認: approved＋クリーンな MR を自動マージ・クローズ
                                            # （false で従来の「人が関連 MR を管理」モード）
        "close_issues": "auto",             # イシューのクローズ主体。auto=決着時に executor がクローズ／
                                            # manual=クローズは人（承認条件が揃ったら案内ノートを出して
                                            # 人がクローズするのを監視。クローズで決着）
        "rework_label": "status:needs-rework",  # 差し戻し時に approved から付け替えるラベル
        # park & poll（承認待ちを worker スロットから切り離す）のパラメータ。
        # defer_waits=false で park & poll を無効化し、従来モード（worker がイシューを監視して
        # ブロック待機。1 worker=1 イシュー）に戻す。承認待ちが max_workers を占有するが、
        # 挙動が単純で分散の監視分担も不要。既定 true（park & poll 有効）。
        "defer_waits": True,
        "max_open_issues": 0,               # 同時に開ける未決着イシューの上限（0=無制限）。
                                            # 上限到達で起票を一時停止＝バックプレッシャ（エラーにしない）。
                                            # defer_waits=false のときは無効（park しないため）。
        "watch_interval": 90.0,             # service_waits が park をまとめて再確認する間隔（秒）
        # --- 人/エージェント判別（gitlab-idd 実行前提。人コメントのみを還元へ運ぶ）---
        # gitlab-idd の worker/reviewer が動くアカウント（username/id・カンマ区切り）を
        # エージェント扱いで除外。空でも bot 名・全 gitlab-idd マーカー・per-issue 自動学習で除外する。
        "agent_authors": "",
        "human_reviewers": "",              # 人間レビュアーの allowlist（指定するとそれ以外を除外・最も厳密）
        "trust_unmarked_comments": False,   # 著者不明の曖昧コメントも拾うか（既定 False＝precision 優先）
        # 途中の差し戻し: 人コメントの見出しにこの語があれば approve/reject 決着を待たず却下級として拾う
        # （汎用コントラクト decision=rejected+guidance へ変換。空で無効）。
        "rework_heading": "差し戻し",
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
       2. カレントディレクトリ（=プロジェクトルート）直下の kiro-flow.{yaml,yml,json}
       3. カレントディレクトリの .kiro/kiro-flow.{yaml,yml,json}
       4. ~/.kiro/kiro-flow.{yaml,yml,json}
    ルート直下を最優先にするのは 1 root = 1 プロジェクト構成でこのファイルが
    プロジェクトのマニフェスト（発見マーカー）を兼ねるため（kiro-project と同じ規則）。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            print(f"[kiro-flow] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    for base in (os.getcwd(),
                 os.path.join(os.getcwd(), ".kiro"),
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
        # cancel マーカー（人の明示指示）。inbox/ 配下＝git 同期でリモート優先で全 PC へ伝わり、
        # 監視主体（daemon/run）がこれを見て run スコープで恒久停止する。
        self.inbox_cancels_dir = os.path.join(root, "inbox", "cancels")
        self.run_dir = os.path.join(root, "runs", run_id)
        self.tasks_dir = os.path.join(self.run_dir, "tasks")
        self.claims_dir = os.path.join(self.run_dir, "claims")
        # waits/<node>.json … 人の承認待ち等でノードを「park（保留）」した記録。executor が
        # 決着まで worker をブロックする代わりに DeferDecision を投げ、worker が claim を
        # 解放してここに書き残す。監視主体（daemon/run）の service_waits がバッチで再確認する。
        # runs/ 配下＝git バスで同期され、daemon 消失を跨いで生存する（孤児 reclaim と同じ耐性）。
        self.waits_dir = os.path.join(self.run_dir, "waits")
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
        for d in (self.tasks_dir, self.claims_dir, self.waits_dir,
                  self.results_dir, self.events_dir):
            os.makedirs(d, exist_ok=True)

    def ensure_run(self, request: str, workspace: "dict | None" = None,
                   references: "list[dict] | None" = None) -> None:
        self.ensure_dirs()
        if read_json(self.meta_path) is None:
            write_json_atomic(self.meta_path, {
                "request": request,
                # この run（=バックログ単位）の唯一の書込先リポジトリ（worker が clone し、
                # 作業ブランチを作って作業する）。None なら読み取り専用 run（commit/push しない）。
                "workspace": workspace or None,
                # 参照リポジトリ（読むだけ・書き込まない）。executor がイシュー/プロンプトに描画する。
                "references": list(references or []),
                "status": "planning",
                "created_at": now_iso(),
            })

    def run_workspace(self) -> "dict | None":
        """この run の唯一の書込先ワークスペース spec（meta に記録）。無ければ None（読み取り専用 run）。"""
        meta = read_json(self.meta_path) or {}
        w = meta.get("workspace")
        return w if isinstance(w, dict) and w.get("url") else None

    def run_references(self) -> "list[dict]":
        """この run の参照リポジトリ spec 一覧（読むだけ。meta に記録、executor が描画する）。"""
        meta = read_json(self.meta_path) or {}
        r = meta.get("references")
        return [s for s in r if isinstance(s, dict) and s.get("url")] if isinstance(r, list) else []

    # --- メタ / グラフ ---
    def set_status(self, status: str) -> None:
        meta = read_json(self.meta_path) or {}
        meta["status"] = status
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)

    def note_executor(self, executor: str) -> None:
        """この run を駆動する executor 名を meta に記録する（冪等）。
        viewer が「GitLab 連携の UI を出すか」を executor で切り替えるための表示用メタデータ
        （gitlab executor を使っていない run にイシュー突き合わせ等を出しても意味がない）。"""
        ex = str(executor or "").strip()
        meta = read_json(self.meta_path) or {}
        if not ex or meta.get("executor") == ex:
            return
        meta["executor"] = ex
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

    def release_claim(self, node_id: str, who: str) -> None:
        """自分の claim ファイルを消して node を手放す（park 時に worker スロットを空けるため）。
        心拍（Heartbeat）を停止してから呼ぶこと——停止前に消すと直後の心拍が claim を書き戻す。"""
        try:
            os.remove(os.path.join(self._claim_dir(node_id), f"{who}.json"))
        except OSError:
            pass
        self.sync_push(f"release {node_id} by {who}")

    # --- park（保留待ち）プロトコル ---
    #
    # 承認待ち等の長い外部待機を worker スロットから切り離すための記録。claim と同じ
    # lease セマンティクス（wait_lease_until が生存判定）に相乗りし、失効すれば node_state は
    # pending に縮退＝full worker が token 再アタッチで拾い直す（行き止まりにしない）。
    # レコードにトークン等の秘密は載せない（バスは git 同期・共有されうるため）。
    def wait_path(self, node_id: str) -> str:
        return os.path.join(self.waits_dir, f"{node_id}.json")

    def read_wait(self, node_id: str):
        return read_json(self.wait_path(node_id))

    def write_wait(self, node_id: str, rec: dict) -> None:
        os.makedirs(self.waits_dir, exist_ok=True)
        write_json_atomic(self.wait_path(node_id), rec)

    def clear_wait(self, node_id: str) -> None:
        """park 記録を消す（決着して result を書いたとき／node を pending へ戻すとき）。"""
        try:
            os.remove(self.wait_path(node_id))
        except OSError:
            pass

    def list_waits(self) -> "list[dict]":
        """この run の park 記録一覧（id を含む dict の列）。無ければ空。"""
        out = []
        if not os.path.isdir(self.waits_dir):
            return out
        for name in sorted(os.listdir(self.waits_dir)):
            if name.endswith(".json"):
                rec = read_json(os.path.join(self.waits_dir, name))
                if rec:
                    rec.setdefault("id", name[:-5])
                    out.append(rec)
        return out

    def wait_is_live(self, node_id: str) -> bool:
        """park 記録が生存（wait_lease_until が未失効）か。失効＝監視主体が居ない/止まった
        とみなし、node_state は pending へ縮退させて full worker の再アタッチに委ねる。"""
        rec = self.read_wait(node_id)
        return bool(rec) and float(rec.get("wait_lease_until", 0) or 0) >= time.time()

    def open_wait_count(self) -> int:
        """この run で「起票済み・未決着」の park 記録数（throttle の同時イシュー上限に使う）。
        throttled（イシュー未作成で枠待ち）のレコードは数えない。"""
        return sum(1 for r in self.list_waits()
                   if not r.get("throttled") and (r.get("issue") or {}).get("iid") is not None)

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
        # 優先順: result（終端） > claimed（生存 lease） > waiting（生存 wait_lease） > pending。
        # waiting は「park 済みで監視主体が生存確認中」。wait_lease 失効時は pending へ縮退させ、
        # full worker が token 再アタッチで拾えるようにする（park を行き止まりにしない）。
        res = self.read_result(node_id)
        if res:
            return res.get("status", "done")
        if self._winner(node_id) is not None:
            return "claimed"
        if self.wait_is_live(node_id):
            return "waiting"
        if os.path.exists(os.path.join(self.tasks_dir, f"{node_id}.json")):
            return "pending"
        return "unknown"

    def all_terminal(self) -> bool:
        ids = self.task_ids()
        return bool(ids) and all(self.node_state(i) in TERMINAL for i in ids)

    def retry_failed(self) -> "list[str]":
        """failed 状態の run を「再実行できる状態」へ戻す。失敗ノード（results が failed）の結果と
        claim を消して pending へ戻し（＝再 claim・再実行の対象にする）、確定済み done ノードは温存する。
        併せて meta の終端・孤児簿記（failure_reason/superseded/orphaned/resume_count 等）を掃除し、
        status を running に戻す。戻したノード id 一覧を返す（commit/push は呼び出し側）。

        failed run はそのままでは再開しても全ノードが終端（node_state=failed）のまま静止し、
        何も再実行されない。人/消費者の明示 retry でだけこの reset を行い、失敗した所だけをやり直す。"""
        reset: "list[str]" = []
        for nid in self.task_ids():
            res = self.read_result(nid)
            if res and res.get("status") == "failed":
                try:
                    os.remove(self.result_path(nid))
                except OSError:
                    pass
                shutil.rmtree(self._claim_dir(nid), ignore_errors=True)   # 失効前の claim も掃除
                reset.append(nid)
        meta = read_json(self.meta_path) or {}
        for k in ("failure_reason", "superseded", "superseded_by",
                  "resume_count", "resume_progress"):
            meta.pop(k, None)
        meta["status"] = "running"
        meta["updated_at"] = now_iso()
        write_json_atomic(self.meta_path, meta)
        return reset

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
        try:
            os.remove(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"))
        except OSError:
            pass

    def run_view(self, run_id: str) -> "Bus":
        """同じ作業ツリー上の別 run を読み取るための軽量ビュー（git 再クローンしない）。"""
        return Bus(self.root, run_id)

    # --- リトライ時の引き継ぎ（先行 run のデータ破棄設計） ---
    def _seed_from(self, old: "Bus") -> int:
        """先行 run `old` の再利用可能な状態をこの（新しい）run dir へコピーする。
        戻り値＝引き継いだ done ノード数。graph.json（計画）・tasks/（ノード仕様）・
        artifacts/（node-id で決定的にアドレスされる中間成果物）を丸ごと、results/ は
        status==done のノードだけ引き継ぐ（failed はやり直させる）。workspace 付き run では
        確定済みノードの commit を失わないよう、新 run の作業ブランチを旧ブランチ kf/<old> から
        派生させる（spec.base を旧ブランチに差す。旧ブランチが無ければ clone 側が既定へ
        フォールバックするので安全）。meta の lease/resume 簿記・claims/・events/ は引き継がない
        （wall-clock リースや孤児判定を汚染しないため）。"""
        old_id = os.path.basename(old.run_dir)
        self.ensure_dirs()
        g = read_json(old.graph_path)
        if g is not None:
            write_json_atomic(self.graph_path, g)
        for nid in old.task_ids():                     # ノード仕様（tasks/<id>.json）
            spec = read_json(os.path.join(old.tasks_dir, f"{nid}.json"))
            if spec is not None:
                write_json_atomic(os.path.join(self.tasks_dir, f"{nid}.json"), spec)
        if os.path.isdir(old.artifacts_dir):           # 中間成果物（node-id アドレス）
            shutil.copytree(old.artifacts_dir, self.artifacts_dir, dirs_exist_ok=True)
        seeded = 0
        for nid in old.task_ids():                     # 確定済み（done）ノードの結果だけ
            res = old.read_result(nid)
            if res and res.get("status") == "done":
                write_json_atomic(self.result_path(nid), res)
                seeded += 1
        old_meta = read_json(old.meta_path) or {}
        ws = old_meta.get("workspace")
        if isinstance(ws, dict) and ws.get("url"):
            ws = dict(ws)
            ws["base"] = run_branch_name(old_id)       # 旧ブランチから派生＝done の commit を保つ
        write_json_atomic(self.meta_path, {
            "request": old_meta.get("request", ""),
            "workspace": ws or None,
            "references": list(old_meta.get("references") or []),
            "status": "planning",
            "created_at": now_iso(),
            "inherited_from": old_id,                  # 由来（可視化・監査用）
        })
        return seeded

    def inherit_from(self, old_run_id: str, orphan_grace: float = 0.0) -> dict:
        """タイムアウト/失敗した先行 run から再利用可能な状態をこの run へ引き継ぎ、先行 run を
        削除する。リトライで毎回ゼロからやり直して確定済みノードの作業（トークン/時間）を捨てるのを
        防ぐための「引き継いでから掃除する」操作。

        安全条件: 先行 run が終端（done/failed）か孤児（生存リース切れ）のときだけ触る。実行中で
        リースが有効な run には seed も削除もしない（走っている run を壊さない）。
        先行 run が「完全に done」（全ノード確定＝verify=NG 等）なら状態は引き継がず掃除だけ行う
        （同一出力で即 done→再び NG の無限ループを避け、feedback 付きで新規にやり直させる）。
        戻り値: {inherited, seeded_nodes, deleted, reason}。"""
        if old_run_id == os.path.basename(self.run_dir):
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": "自分自身は引き継がない"}
        old = self.run_view(old_run_id)
        old_meta = read_json(old.meta_path)
        if old_meta is None:
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": "先行 run が見つからない"}
        terminal = old_meta.get("status") in TERMINAL
        if not terminal and not self.run_is_orphaned(old_run_id, orphan_grace):
            return {"inherited": False, "seeded_nodes": 0, "deleted": False,
                    "reason": f"先行 run は実行中（status={old_meta.get('status')}）＝触らない"}
        ids = old.task_ids()
        fully_done = bool(ids) and all(old.node_state(i) == "done" for i in ids)
        seeded = 0
        # この run が既に実体を持つ（別経路で再開中）なら seed しない＝上書き事故を防ぐ
        if read_json(self.meta_path) is None and not fully_done:
            seeded = self._seed_from(old)
        self.remove_run(old_run_id)                    # 終端/孤児のみ到達＝安全に掃除
        return {"inherited": seeded > 0, "seeded_nodes": seeded, "deleted": True,
                "reason": ("完全 done のため状態は引き継がず掃除のみ" if fully_done
                           else f"確定済み {seeded} ノードを引き継いで先行 run を掃除")}

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
        result/status を待つ消費者（kiro-project の submit 待ちなど）が永久待機に陥らないようにする。
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

    def mark_run_superseded(self, run_id: str, superseded_by: str = "") -> bool:
        """run_id がまだ終端でなければ status を failed に確定する（世代交代による停止）。
        kiro-project はリトライ時に先行 run を明示 cancel せず、inherit_from 付きで次世代を
        inbox へ投入する。inherit_from は実行中の先行 run を安全のため殺さないので、旧世代の run が
        非終端のまま inbox に残る。owning daemon 消失後（PC シャットダウン等）に daemon を再起動
        すると、これら旧世代の孤児が一斉に adopt（再開）され、世代交代で消えるべき旧リトライが
        復活して二重実行になる。これを防ぐため、次世代に引き継がれた先行 run を再開せず終端化する。
        failed（≒ 異常終了）や canceled（人の明示指示）と区別できるよう superseded=True を記録する。
        終端化後は次世代の inherit_from が確定済みノードを引き継いでから掃除できる（作業は失わない）。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "failed"
        meta["updated_at"] = now_iso()
        meta["superseded"] = True
        if superseded_by:
            meta["superseded_by"] = superseded_by
        meta["failure_reason"] = (
            f"superseded: 新世代のリトライ {superseded_by} に引き継がれた旧 run（再開しない）"
            if superseded_by else "superseded: 新世代のリトライに引き継がれた旧 run（再開しない）")
        write_json_atomic(v.meta_path, meta)
        return True

    # --- cancel（人の明示指示による run スコープの恒久停止） ---
    def cancel_request(self, run_id: str, who: str, reason: str = "",
                       close_issues: bool = False) -> None:
        """cancel マーカーを inbox/cancels/ に書く（git 同期でリモート優先で全 PC へ伝わる）。
        監視主体（daemon/run/orchestrator）がこれを見て run を canceled に終端化し、その run の
        orchestrator/worker を止め、park 済みノードの再ポーリングを止める。"""
        os.makedirs(self.inbox_cancels_dir, exist_ok=True)
        write_json_atomic(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"), {
            "id": run_id, "who": who, "reason": reason,
            "close_issues": bool(close_issues), "requested_at": now_iso(),
        })

    def is_canceled_requested(self, run_id: str) -> bool:
        """run_id に cancel マーカーがあるか（＝人が停止を指示したか）。"""
        return os.path.exists(os.path.join(self.inbox_cancels_dir, f"{run_id}.json"))

    def cancel_info(self, run_id: str) -> dict:
        return read_json(os.path.join(self.inbox_cancels_dir, f"{run_id}.json")) or {}

    def list_cancels(self) -> "list[str]":
        d = self.inbox_cancels_dir
        if not os.path.isdir(d):
            return []
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))

    def mark_canceled(self, run_id: str, reason: str = "") -> bool:
        """run_id がまだ終端でなければ status を canceled に確定する（cancel マーカーの適用）。
        終端化できたら True、既に終端 / run が存在しないなら False。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path)
        if not meta or meta.get("status") in TERMINAL:
            return False
        meta["status"] = "canceled"
        meta["updated_at"] = now_iso()
        if reason:
            meta["cancel_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def clear_waits_for_run(self, run_id: str) -> int:
        """run_id の park 記録をすべて消す（cancel 時に再ポーリングを止める）。消した件数を返す。"""
        v = self.run_view(run_id)
        n = 0
        if os.path.isdir(v.waits_dir):
            for name in os.listdir(v.waits_dir):
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(v.waits_dir, name))
                        n += 1
                    except OSError:
                        pass
        return n

    def fail_request(self, req_id: str, reason: str = "") -> bool:
        """inbox 要求 req_id を failed run として終端化する（run 未作成でも）。
        orchestrator が run の meta を一度も書けずに死に続ける（例: クローンの git ロック残骸で
        sync_push が失敗し続ける）と run_exists が偽のままになり、daemon が毎 poll 同じ要求を
        再 claim → orchestrator 起動 → 即死 を繰り返す無限ループに陥る。meta が無ければ failed で
        新規作成して run_exists を真にし、このループを断ち切る（消費者も失敗を即検知できる）。
        既に run があれば mark_run_failed に委ねる（終端済みなら上書きせず False）。"""
        v = self.run_view(req_id)
        if read_json(v.meta_path) is not None:
            return self.mark_run_failed(req_id, reason)
        req = self.read_inbox(req_id) or {}
        meta = {
            "request": req.get("request", ""),
            "workspace": req.get("workspace"),
            "references": list(req.get("references") or []),
            "status": "failed",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        if reason:
            meta["failure_reason"] = reason
        write_json_atomic(v.meta_path, meta)
        return True

    def cancel_request_run(self, req_id: str, reason: str = "") -> bool:
        """run 化前に cancel された要求を canceled run として終端化する（fail_request の canceled 版）。
        既に run があれば mark_canceled に委ねる。これで消費者は「取り下げ」を終端として観測でき、
        daemon が同じ要求を毎 poll 受理し直すのを止める。"""
        v = self.run_view(req_id)
        if read_json(v.meta_path) is not None:
            return self.mark_canceled(req_id, reason)
        req = self.read_inbox(req_id) or {}
        write_json_atomic(v.meta_path, {
            "request": req.get("request", ""),
            "workspace": req.get("workspace"),
            "references": list(req.get("references") or []),
            "status": "canceled",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "cancel_reason": reason or "cancel 指示（run 化前）",
        })
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

    def record_resume(self, run_id: str) -> int:
        """自動再開の試行を meta に記録し、「進捗なしの連続再開回数」を返す。
        前回の再開以降に results/ が増えていれば 1 から数え直す＝進捗のある長期 run は
        （毎日の PC シャットダウンを跨いで）何度でも再開できる。進捗ゼロのまま数字だけ
        増える壊れた run だけが max_resumes に達して failed に確定される。"""
        v = self.run_view(run_id)
        meta = read_json(v.meta_path) or {}
        try:
            done_now = sum(1 for f in os.listdir(v.results_dir) if f.endswith(".json"))
        except OSError:
            done_now = 0
        prev = meta.get("resume_progress")
        if prev is None or done_now > int(prev):
            n = 1                                     # 進捗あり（または初回）→ 数え直し
        else:
            n = int(meta.get("resume_count", 0) or 0) + 1
        meta["resume_count"] = n
        meta["resume_progress"] = done_now
        meta["resumed_at"] = now_iso()
        meta["updated_at"] = now_iso()
        write_json_atomic(v.meta_path, meta)
        return n

    # --- inbox（要求キュー）と要求 claim ---
    def submit_request(self, req_id: str, request: str, submitter: str,
                       workspace: "dict | None" = None,
                       references: "list[dict] | None" = None,
                       inherit_from: "str | None" = None) -> None:
        rec = {
            "id": req_id,
            "request": request,
            "submitter": submitter,
            "workspace": workspace or None,   # 唯一の書込先を daemon の orchestrate へ伝搬する
            "references": list(references or []),  # 参照リポジトリも daemon の orchestrate へ伝搬する
            "submitted_at": now_iso(),
        }
        if inherit_from:                      # リトライ: 先行 run の引き継ぎ元を orchestrate へ伝搬
            rec["inherit_from"] = inherit_from
        write_json_atomic(os.path.join(self.inbox_dir, f"{req_id}.json"), rec)

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

    def reclaim_request(self, req_id: str, who: str, lease_sec: float) -> bool:
        """孤児 run の再開担当を 1 台に決める。run が既に存在していても claim できる点が
        claim_request と違う（あちらは新規要求の受理用）。消失した旧 owner の claim は
        lease 切れで勝者判定から自然に外れるため、再起動後の自分や別 daemon が引き継げる
        （lease がまだ残っていれば False＝claim 失効まで次の poll で再試行される）。"""
        self.sync_pull()
        return self._try_claim_in(os.path.join(self.inbox_claims_dir, req_id),
                                  who, lease_sec, f"reclaim request {req_id} by {who}")


# --------------------------------------------------------------------------
# GitBus — git 共有リポジトリをバスにする（複数 PC 分散）
# --------------------------------------------------------------------------
# 初回クローンの最大試行回数（push/pull と同じ指数バックオフでリトライ）。
CLONE_RETRIES = 5
# .git 直下のロック（index.lock 等）を「異常終了の残骸」と断定する最小経過秒。
# バスの git 操作は数 KB の JSON の add/commit で数秒あれば終わるため、これ以上
# 更新の無いロックは SIGKILL・電源断・daemon の terminate が残した残骸とみなせる。
# 新しいロックは（同一クローンを共有する）稼働中の git が保持している可能性があるので残す。
GIT_LOCK_STALE_SEC = 30.0
# ロック起因で git コマンドが失敗したときの再試行回数（合間に 1,2,4s バックオフ）。
GIT_LOCK_RETRIES = 4

# --- 電源断によるオブジェクト破損への耐性（durable write / 自己修復） -----------------
# git は既定で loose object を「一時ファイル→rename」で書くが *中身の fsync をしない*。
# PC の定期シャットダウン/電源断が書き込み途中に起きると、rename のメタデータだけがジャーナル
# で残り中身（データブロック）は未フラッシュ——再起動後に **サイズ 0 のオブジェクトファイル**
# が残る（症状: `error: object file .git/objects/xx/yy… is empty` → 以後 add/commit/push/
# checkout が全滅し、バスが同期不能になる）。
#   対策 A（予防）: 管理クローンとローカルパスのリモートに core.fsync=all / fsyncMethod=batch
#     を設定し、rename 前に中身を durable 化する（batch により tiny JSON の書き込みでも安価）。
#   対策 B（自己修復）: それでも壊れたクローンは検知して捨て、リモート（真実）から作り直す。
#     クローンは使い捨て設計（未 push の作業は孤児 reclaim で続きから再実行される）なので安全。
_DURABLE_GIT_CONFIG = (("core.fsync", "all"), ("core.fsyncMethod", "batch"))
# git がオブジェクト破損時に stderr へ出す代表的シグネチャ（LC_ALL=C 固定なので英語で判定できる）。
# 一過性のネットワーク/権限エラー（"unable to access" 等）とは重ならない、破損に固有の語だけに絞る
# （誤検知しても捨てて作り直すだけで情報は失われないが、無駄な再クローンは避けたい）。
_GIT_CORRUPT_MARKERS = (
    "object file", "loose object", "corrupt", "did not match content",
    "bad object", "sha1 mismatch", "unable to unpack", "invalid object",
    "unable to read tree", "unable to read sha1",
)


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
        # ロック競合の検知はエラーメッセージの文字列マッチに頼るため、翻訳されない C ロケールに固定する
        env["LC_ALL"] = "C"
        return env

    # 異常終了した git が .git 直下に残すロックの残骸。これがあると以後の add/commit/
    # checkout/pull が「File exists」で失敗し続け、orchestrator の run 作成（sync_push）が
    # 恒久的に失敗する（→ daemon が同じ要求を再 claim し続ける）原因になる。
    _STALE_GIT_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "shallow.lock",
                        "packed-refs.lock")

    def _remove_stale_git_locks(self, min_age_sec: float) -> int:
        """min_age_sec 以上更新の無いロック残骸を削除して削除数を返す。
        新しいロックは稼働中の git が保持している可能性があるため残す。"""
        removed = 0
        gitdir = os.path.join(self.workdir, ".git")
        now = time.time()
        for name in self._STALE_GIT_LOCKS:
            path = os.path.join(gitdir, name)
            try:
                if os.path.isfile(path) and now - os.path.getmtime(path) >= min_age_sec:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def _is_lock_error(p) -> bool:
        err = p.stderr or ""
        return ".lock" in err and ("File exists" in err or "another git process" in err.lower())

    @staticmethod
    def _is_corrupt_error(p) -> bool:
        """git のオブジェクト破損（空/壊れた loose object 等）を示す stderr かを判定する。
        電源断で生じるサイズ 0 のオブジェクトは `error: object file … is empty` 等で表面化する。"""
        err = (p.stderr or "").lower()
        return any(m in err for m in _GIT_CORRUPT_MARKERS)

    def _apply_durable_writes(self, cwd: str) -> None:
        """cwd のリポジトリに durable-write 設定（core.fsync/fsyncMethod）を冪等に適用する。
        rename 前にオブジェクト内容を fsync させ、電源断でのサイズ 0 オブジェクト発生を防ぐ。
        古い git が値を知らなくても無害（未知の core.fsync トークンは無視される）。設定 lock 競合等の
        一過性失敗は無視する（次回起動で再適用される。予防設定が一度失敗しても致命ではない）。"""
        for key, val in _DURABLE_GIT_CONFIG:
            try:
                cur = subprocess.run(["git", "-C", cwd, "config", "--local", "--get", key],
                                     capture_output=True, text=True, env=self._git_env())
                if cur.returncode == 0 and cur.stdout.strip() == val:
                    continue  # 既に設定済み（冪等・書き込み lock を無駄に取らない）
                subprocess.run(["git", "-C", cwd, "config", "--local", key, val],
                               capture_output=True, text=True, env=self._git_env())
            except OSError:
                pass

    def _harden_remote_durability(self) -> None:
        """リモートがローカルパスの共有リポジトリなら、そちらにも durable-write 設定を適用する。
        ローカルパスのリモートへ push すると receive-pack がリモート側にオブジェクトを書くため、
        リモート自身が電源断で壊れる経路を塞ぐ。URL（http/ssh 等）のリモートは触れないので黙って skip。"""
        try:
            if not self.remote or not os.path.isdir(self.remote):
                return
            probe = subprocess.run(["git", "-C", self.remote, "rev-parse", "--git-dir"],
                                   capture_output=True, text=True, env=self._git_env())
            if probe.returncode == 0:
                self._apply_durable_writes(self.remote)
        except OSError:
            pass

    def _probe_integrity(self) -> bool:
        """再利用クローンのオブジェクトが健全か軽量に確認する。破損（空オブジェクト等）なら False。
        --connectivity-only は内容ハッシュ検証を省くが到達可能オブジェクトの読み取りは行うため、
        サイズ 0 の loose object があれば非 0 で失敗する。バス履歴は tiny なので高速。"""
        try:
            p = subprocess.run(
                ["git", "-C", self.workdir, "fsck", "--connectivity-only", "--no-dangling",
                 "--no-reflogs"], capture_output=True, text=True, env=self._git_env())
        except OSError:
            return False
        # fsck 自体が動かない（git dir 破損等）ケースも破損として扱い作り直させる。
        return p.returncode == 0 and not self._is_corrupt_error(p)

    def _rebuild_clone(self) -> None:
        """破損したノード専用クローンを丸ごと捨て、リモート（真実）から作り直す。
        未 push の作業は孤児 reclaim が続きから再実行するため、捨てても情報は失われない。"""
        log(os.path.basename(self.workdir),
            f"クローン {self.workdir} のオブジェクト破損を検知——リモートから作り直します")
        self._reset_clone_dir()
        self._ensure_clone()

    def _git(self, args, check=True):
        p = None
        for i in range(GIT_LOCK_RETRIES):
            p = subprocess.run(["git", "-C", self.workdir] + args, capture_output=True, text=True,
                               env=self._git_env())
            if p.returncode == 0 or not self._is_lock_error(p):
                break
            # ロック起因の失敗: 残骸（十分古い）なら消して即再試行、稼働中の他 git が
            # 保持する新しいロックなら短く待って再試行する。クローンはノード専有が原則
            # なので、恒久的に残るロックはほぼ残骸＝ここで自己回復できる。
            if self._remove_stale_git_locks(GIT_LOCK_STALE_SEC) == 0 and i < GIT_LOCK_RETRIES - 1:
                time.sleep(2 ** i)
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

    def _reset_clone_dir(self) -> None:
        """失敗したクローンが残した部分ディレクトリを消す（再試行が「宛先が空でない」で
        失敗しないように）。対象はクローン専用の workdir のみ。非空の管理外ディレクトリは
        _ensure_clone の事前ガードで既に除外済みなので、ここで消すのは自前のクローン残骸だけ。"""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _clone_once(self):
        """blob フィルタ付き → 非対応サーバ向けフォールバックの順でクローンを 1 回試みる。"""
        r = subprocess.run(
            ["git", "clone", "--no-checkout", "--filter=blob:none", self.remote, self.workdir],
            capture_output=True, text=True)
        if r.returncode != 0:
            # blob filter 非対応サーバ向けフォールバック（フィルタ版が残した部分クローンを消してから）
            self._reset_clone_dir()
            r = subprocess.run(["git", "clone", "--no-checkout", self.remote, self.workdir],
                               capture_output=True, text=True)
        return r

    def _clone_with_retry(self):
        """初回クローンを指数バックオフ（2,4,8,16s）でリトライする。push/pull と同じ流儀で、
        一過性のネットワーク障害による起動失敗を吸収する。成否は CompletedProcess で返す
        （最終的に失敗なら returncode != 0）。"""
        r = None
        for i in range(CLONE_RETRIES):
            r = self._clone_once()
            if r.returncode == 0:
                return r
            if i < CLONE_RETRIES - 1:
                self._reset_clone_dir()                 # 部分クローンを消してから
                time.sleep(2 ** i if i < 4 else 16)     # バックオフして再試行
        return r

    def _recover_reused_clone(self) -> None:
        """再利用する管理クローンから、前プロセスの異常終了（SIGKILL・電源断・daemon の
        terminate）が残した残骸を回復する。ロック残骸は以後の add/checkout が「File exists」
        で失敗し続ける原因、中断 rebase の残骸は以後の pull --rebase が失敗し続ける原因になる。"""
        self._remove_stale_git_locks(GIT_LOCK_STALE_SEC)
        gitdir = os.path.join(self.workdir, ".git")
        if any(os.path.isdir(os.path.join(gitdir, d)) for d in ("rebase-merge", "rebase-apply")):
            self._git(["rebase", "--abort"], check=False)
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(os.path.join(gitdir, d), ignore_errors=True)

    def _setup_worktree(self, strict: bool = True) -> bool:
        """コミット用 ID・sparse-checkout・対象ブランチへの checkout を整える。
        strict=False は失敗を False で返す（呼び出し側がクローンを作り直して再試行する）。"""
        # コミット用 ID（未設定環境向けのフォールバック）
        if not self._git(["config", "user.email"], check=False).stdout.strip():
            self._git(["config", "user.email", "kiro-flow@local"], check=False)
            self._git(["config", "user.name", "kiro-flow"], check=False)
        # durable write（電源断でのサイズ 0 オブジェクト対策）を毎回冪等に適用する
        self._apply_durable_writes(self.workdir)
        # sparse checkout（cone モード）を設定 — バスのサブツリーだけ作業ツリーに置く
        self._git(["sparse-checkout", "init", "--cone"], check=False)
        self._git(["sparse-checkout", "set"] + self._sparse_paths(), check=False)
        # 対象ブランチへ。無ければ作成（空リポジトリ初回も含む）
        if self._git(["checkout", self.branch], check=False).returncode == 0:
            return True
        return self._git(["checkout", "-B", self.branch], check=strict).returncode == 0

    def _ensure_clone(self) -> None:
        # workdir が自前管理の sparse バスクローンなら回復して再利用。そうでなければ新規 clone する。
        # （ユーザーのフルチェックアウトや親/別リポジトリへ sparse-checkout を効かせて作業ツリーを
        #   壊さないため、「自前のバスクローンである」ことを確認してからでないと sparse-checkout に進まない。）
        self._harden_remote_durability()  # ローカルパスのリモートにも durable write を効かせる
        if self._is_managed_bus_clone():
            self._recover_reused_clone()
            # 電源断でオブジェクトが空/破損したクローンは lock/rebase 回復では直らない。
            # 健全性を確認し、破損していれば以下の「作り直し」へ落とす（真実はリモート側）。
            if self._probe_integrity() and self._setup_worktree(strict=False):
                return
            # 回復しても使えない（新しいロックを他プロセスが握ったまま消えた・index 破損・
            # 電源断でのオブジェクト破損等）。バスの真実はリモート側にあり管理クローンは使い捨てに
            # できるため、作り直して自己回復する（作り直せないままだと orchestrator の run 作成が
            # 失敗し続け、daemon が同じ要求を毎 poll 再 claim する無限ループの起点になる）。
            log(os.path.basename(self.workdir),
                f"再利用クローン {self.workdir} を回復できないため作り直します")
            self._reset_clone_dir()
        elif os.path.isdir(self.workdir) and os.listdir(self.workdir):
            # 既存の非空ディレクトリ（ユーザーの作業チェックアウト・親/別リポジトリ等）は上書きせず中断。
            # ここで sparse-checkout すると subdir 以外の追跡ファイルを作業ツリーから隠してしまう。
            raise RuntimeError(
                f"クローン先 {self.workdir} が空でない既存ディレクトリ（kiro-flow 管理外のクローン/作業"
                f"ツリー）です。sparse-checkout で作業ファイルを隠す事故を防ぐため中断します"
                f"（専用の空ディレクトリを --bus に指定してください）。")
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        # sparse checkout: --no-checkout で取得し、必要なパスだけ展開する。
        # 一過性のネットワーク障害で起動時クローンが即死しないよう、push/pull と同様に
        # 指数バックオフでリトライする（分散・委譲構成では各ノードが起動毎に clone するため、
        # ここがネットワーク不安定時の「起動できない」原因になりやすい）。
        r = self._clone_with_retry()
        if r.returncode != 0:
            if self._is_corrupt_error(r):
                # クローンできない破損は「リモート（共有リポジトリ本体）」側にある。クローンは使い捨て
                # なので作り直しでは直らない——健全な PC のクローンから objects を移植するか、
                # `git fsck` で壊れたオブジェクトを特定して復旧する必要がある（README「破損リポジトリの
                # 復旧」参照）。ここでは作り直しループに陥らないよう明確な理由付きで中断する。
                raise RuntimeError(
                    f"共有リポジトリ {self.remote} 自体のオブジェクトが破損している可能性があります"
                    f"（clone がオブジェクト破損で失敗）。健全な PC のクローンから復旧してください: "
                    f"{r.stderr.strip()[:300]}")
            raise RuntimeError(
                f"git clone が {CLONE_RETRIES} 回失敗しました: {r.stderr.strip()[:300]}")
        if not self._is_own_repo_root():
            # clone 後も workdir 自身がリポジトリのルートでなければ、以降の sparse-checkout が
            # 親リポジトリへ波及しうる。安全側に倒して中断する。
            raise RuntimeError(
                f"git clone 後も {self.workdir} がクローンのルートになっていません。"
                "親リポジトリへの sparse-checkout を防ぐため中断します。")
        self._git(["config", self.MANAGED_FLAG, "1"])   # 自前管理クローンの目印
        self._setup_worktree(strict=True)

    def sync_pull(self) -> None:
        # リモートに当該ブランチが無い初回などは黙って無視
        p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
        # 電源断で pull 先クローンのオブジェクトが壊れていれば、作り直してもう一度だけ引き直す。
        if p.returncode != 0 and self._is_corrupt_error(p):
            self._rebuild_clone()
            self._git(["pull", "--rebase", "origin", self.branch], check=False)

    def _commit_pending(self, msg: str) -> None:
        """作業ツリーの未確定分を add + commit する（コミット対象が無ければ何もしない）。
        add/commit がローカルオブジェクト破損で失敗したらクローンを作り直して 1 度だけ再コミットする。"""
        p = self._git(["add", "-A"], check=False)
        if p.returncode != 0 and self._is_corrupt_error(p):
            self._rebuild_clone()
            self._git(["add", "-A"], check=False)
        # commit の失敗は「対象なし」（正常・頻出）と破損を区別する。破損時のみ作り直す。
        c = self._git(["commit", "-m", msg], check=False)
        if c.returncode != 0 and self._is_corrupt_error(c):
            self._rebuild_clone()
            self._git(["add", "-A"], check=False)
            self._git(["commit", "-m", msg], check=False)

    def sync_push(self, msg: str = "kiro-flow update") -> None:
        self._commit_pending(msg)
        for i in range(5):
            push = self._git(["push", "-u", "origin", self.branch], check=False)
            if push.returncode == 0:
                return
            # push 中に露見したローカル破損 → 作り直して即座に再 push へ（バックオフ不要）。
            if self._is_corrupt_error(push):
                self._rebuild_clone()
                self._commit_pending(msg)
                continue
            # 競合 → 取り込んで再試行（disjoint なので基本コンフリクトしない）。破損なら作り直す。
            p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
            if p.returncode != 0 and self._is_corrupt_error(p):
                self._rebuild_clone()
                self._commit_pending(msg)
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


def ensure_bus_root(args) -> None:
    """起動初回にバスフォルダが無ければ作成する。git バスでは各ノードのクローンが
    作業後に削除されてフォルダが空になるため、空ディレクトリを git 管理下に残せるよう
    .gitkeep も置く（既にあれば触らない＝冪等）。"""
    bus_root = os.path.abspath(args.bus)
    os.makedirs(bus_root, exist_ok=True)
    if getattr(args, "git", None):
        keep = os.path.join(bus_root, ".gitkeep")
        if not os.path.exists(keep):
            with open(keep, "w", encoding="utf-8"):
                pass


def cleanup_active_clones() -> None:
    """このプロセスが作った sparse-checkout クローンを作業後にまとめて削除する。"""
    while _active_clones:
        bus = _active_clones.pop()
        try:
            bus.cleanup_clone()
        except Exception:  # noqa: BLE001 — 掃除失敗で終了処理を止めない
            pass


# --------------------------------------------------------------------------
# 状態の git 保存・共有（state_git）— kiro-project の同名機能と同じ流儀
# --------------------------------------------------------------------------
# ローカルバスのワーク内容（<bus>/runs/・<bus>/inbox/）を共有 git リポジトリへ保存し、
# リモートの kiro-projects-viewer（フロータブ）が run の進捗/結果を読めるようにする。
# GitBus（--git）が「バスそのものを git にして実行を分散する」のに対し、これは
# 「実行はローカルのまま、状態の鏡だけを共有する」——実行は state_git に一切依存しない。
#   ・リモート負荷を抑える: subdir だけの sparse・blob:none の管理クローンを 1 本再利用し、
#     fetch/push は state_git_interval（既定 300 秒）で律速。push は共有すべきローカル
#     コミットがあるときだけ（run の終端時は間隔を待たず押し出す）。
#   ・多重コミッタ前提: 同一リポジトリには他プログラム（kiro-project の state_git・
#     viewer 側の git-file-sync 等）もコミットする。ステージは自 subdir のみ、push 競合は
#     pull --rebase → 再 push の指数バックオフで吸収し、force push はしない。
#   ・双方向: 機械の状態（runs/）は外へ、人の投入（inbox/ の要求ドロップ）は中へ。前回同期
#     スナップショット（manifest）基準の 3-way で発生源を判定し、同時変更のみ
#     「inbox/ はリモート優先・runs/ 等の機械状態はローカル優先」で決定的に裁定する。
STATE_GIT_MARKER = "kiro-flow.stateclone"       # 自前管理クローンの目印（git config）
_STATE_LOCK_STALE_SEC = 30.0                    # これ以上古い .git ロックは残骸とみなし自己回復
_STATE_GIT_RETRIES = 4                          # ロック起因の git 失敗の再試行回数
_STATE_PUSH_RETRIES = 5                         # push 競合の再試行回数（2,4,8,16s バックオフ）


class _StateGitCorrupt(Exception):
    """state_git クローンの電源断オブジェクト破損を検知した内部シグナル（sync が捕捉して作り直す）。"""


class StateGit:
    """ローカルバス状態 ⇔ 共有 git リポジトリの双方向同期（GitBus と同じ管理クローン流儀）。

    真実は常にファイル側（ローカルはバス・リモートは共有リポジトリ）にあり、このクラスは
    「前回同期時点のスナップショット（manifest）」を基準に差分の発生源を判定して橋渡しするだけ。
    クローンや manifest を失っても、次の同期が裁定規則で決定的に再収束させる。"""

    def __init__(self, bus_root: str, remote: str, branch: str = "main",
                 subdir: str = "kiro-flow", interval: float = 300.0,
                 clone_dir: "str | None" = None):
        self.bus_root = os.path.abspath(bus_root)
        self.remote = remote
        self.branch = branch or "main"
        self.subdir = (subdir or "").strip("/")
        self.interval = max(0.0, interval)
        self.clone = clone_dir or os.path.join(self.bus_root, ".state-git")
        self._ready = False
        self._last_remote = 0.0     # 最後にリモートへ触れた時刻（fetch/push の間隔律速）
        self._last_attempt = 0.0    # クローン準備の失敗も間隔律速（不通のリモートを連打しない）

    # --- git 低レベル（GitBus と同じ護り: ceiling / C ロケール / ロック残骸の自己回復） ---
    def _env(self) -> dict:
        env = dict(os.environ)
        parent = os.path.dirname(os.path.realpath(self.clone)) or "/"
        ceil = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = parent + (os.pathsep + ceil if ceil else "")
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        env["LC_ALL"] = "C"              # ロック競合の検知は英語メッセージの文字列マッチに頼る
        env["GIT_EDITOR"] = "true"       # rebase --continue がエディタを開かないように
        return env

    _STALE_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "shallow.lock", "packed-refs.lock")

    def _remove_stale_locks(self) -> int:
        removed = 0
        gitdir = os.path.join(self.clone, ".git")
        now = time.time()
        for name in self._STALE_LOCKS:
            p = os.path.join(gitdir, name)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) >= _STATE_LOCK_STALE_SEC:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def _is_lock_error(p) -> bool:
        err = p.stderr or ""
        return ".lock" in err and ("File exists" in err or "another git process" in err.lower())

    @staticmethod
    def _is_corrupt_error(p) -> bool:
        """git のオブジェクト破損（電源断でのサイズ 0 loose object 等）を示す stderr か。"""
        err = (p.stderr or "").lower()
        return any(m in err for m in _GIT_CORRUPT_MARKERS)

    def _apply_durable_writes(self, cwd: str) -> None:
        """cwd のリポジトリに durable-write 設定（core.fsync/fsyncMethod）を冪等に適用する。
        rename 前にオブジェクト内容を fsync させ、電源断でのサイズ 0 オブジェクト発生を防ぐ。"""
        for key, val in _DURABLE_GIT_CONFIG:
            try:
                cur = subprocess.run(["git", "-C", cwd, "config", "--local", "--get", key],
                                     capture_output=True, text=True, env=self._env())
                if cur.returncode == 0 and cur.stdout.strip() == val:
                    continue
                subprocess.run(["git", "-C", cwd, "config", "--local", key, val],
                               capture_output=True, text=True, env=self._env())
            except OSError:
                pass

    def _harden_remote_durability(self) -> None:
        """リモートがローカルパスの共有リポジトリなら、そちらにも durable-write を効かせる。"""
        try:
            if not self.remote or not os.path.isdir(self.remote):
                return
            probe = subprocess.run(["git", "-C", self.remote, "rev-parse", "--git-dir"],
                                   capture_output=True, text=True, env=self._env())
            if probe.returncode == 0:
                self._apply_durable_writes(self.remote)
        except OSError:
            pass

    def _probe_integrity(self) -> bool:
        """再利用クローンのオブジェクトが健全か軽量に確認する。破損なら False。"""
        try:
            p = subprocess.run(
                ["git", "-C", self.clone, "fsck", "--connectivity-only", "--no-dangling",
                 "--no-reflogs"], capture_output=True, text=True, env=self._env())
        except OSError:
            return False
        return p.returncode == 0 and not self._is_corrupt_error(p)

    def _git(self, *args: str, check: bool = False):
        p = None
        for i in range(_STATE_GIT_RETRIES):
            p = subprocess.run(["git", "-C", self.clone, *args],
                               capture_output=True, text=True, env=self._env())
            if p.returncode == 0 or not self._is_lock_error(p):
                break
            if self._remove_stale_locks() == 0 and i < _STATE_GIT_RETRIES - 1:
                time.sleep(2 ** i)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {(p.stderr or '').strip()[:300]}")
        return p

    # --- クローンの用意（自前管理クローンのみ再利用。他人の作業ツリーは決して触らない） ---
    def _is_managed(self) -> bool:
        if not os.path.isdir(os.path.join(self.clone, ".git")):
            return False
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if not top or os.path.realpath(top) != os.path.realpath(self.clone):
            return False
        origin = self._git("remote", "get-url", "origin").stdout.strip()
        same_origin = origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))
        return same_origin and self._git("config", "--get", STATE_GIT_MARKER).stdout.strip() == "1"

    def _recover(self) -> None:
        """前プロセスの異常終了が残したロック残骸・中断 rebase を自己回復する。"""
        self._remove_stale_locks()
        gitdir = os.path.join(self.clone, ".git")
        if any(os.path.isdir(os.path.join(gitdir, d)) for d in ("rebase-merge", "rebase-apply")):
            self._git("rebase", "--abort")
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(os.path.join(gitdir, d), ignore_errors=True)

    def _setup_worktree(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "kiro-flow@local")
            self._git("config", "user.name", "kiro-flow")
        self._apply_durable_writes(self.clone)   # 電源断でのサイズ 0 オブジェクト対策（冪等）
        if self.subdir:                  # 自分の名前空間だけを作業ツリーに展開（他者のパスを引かない）
            self._git("sparse-checkout", "init", "--cone")
            self._git("sparse-checkout", "set", self.subdir)
        if self._git("checkout", self.branch).returncode != 0:
            self._git("checkout", "-B", self.branch, check=True)   # 空リポジトリ初回など

    def _ensure_clone(self) -> None:
        self._harden_remote_durability()   # ローカルパスのリモートにも durable write を効かせる
        if self._is_managed():
            self._recover()
            # 電源断でオブジェクトが空/破損した再利用クローンは lock/rebase 回復では直らない。
            # 健全なら再利用、破損していれば捨てて作り直す（真実はローカルバスとリモート側にあり、
            # manifest を失っても次の同期が裁定規則で決定的に再収束する）。
            if self._probe_integrity():
                self._setup_worktree()
                return
            shutil.rmtree(self.clone, ignore_errors=True)
        elif os.path.isdir(self.clone) and os.listdir(self.clone):
            raise RuntimeError(
                f"state_git のクローン先 {self.clone} が管理外の非空ディレクトリです"
                "（作業ツリーを壊さないため中断。空のパスを指定してください）")
        os.makedirs(os.path.dirname(self.clone) or ".", exist_ok=True)
        # blob:none で履歴の実体を引かない（非対応サーバはフィルタ無しへフォールバック）
        for extra in (["--filter=blob:none"], []):
            r = subprocess.run(["git", "clone", "--no-checkout", *extra, self.remote, self.clone],
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
            shutil.rmtree(self.clone, ignore_errors=True)
        if r.returncode != 0:
            if self._is_corrupt_error(r):
                raise RuntimeError(
                    f"state_git 共有リポジトリ {self.remote} 自体のオブジェクトが破損している"
                    f"可能性があります。健全な PC のクローンから復旧してください: "
                    f"{(r.stderr or '').strip()[:300]}")
            raise RuntimeError(f"state_git クローン失敗: {(r.stderr or '').strip()[:300]}")
        self._git("config", STATE_GIT_MARKER, "1")
        self._setup_worktree()

    # --- 3-way 同期（manifest = 前回同期時点の path→sha256 スナップショット） ---
    @property
    def _manifest_path(self) -> str:
        return os.path.join(self.clone, ".git", "kiro-flow-state.json")

    def _load_manifest(self) -> dict:
        try:
            with open(self._manifest_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_manifest(self, manifest: dict) -> None:
        tmp = self._manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self._manifest_path)

    @staticmethod
    def _excluded(parts: "tuple[str, ...]") -> bool:
        # "." 始まり（.state-git 自身・.gitkeep 等の管理領域）と書きかけの .tmp は同期しない
        return any(s.startswith(".") for s in parts) or parts[-1].endswith(".tmp")

    @staticmethod
    def _remote_wins(rel: str) -> bool:
        """同時変更の裁定: 人の投入口 inbox/（claims を除く）はリモート優先、機械状態はローカル優先。"""
        parts = tuple(rel.split("/"))
        return bool(parts) and parts[0] == "inbox" and "claims" not in parts

    @classmethod
    def _scan(cls, root: str) -> "dict[str, str]":
        """root 配下の同期対象ファイルを {相対パス: sha256} で返す（除外規則は両側で同一）。"""
        out: "dict[str, str]" = {}
        if not os.path.isdir(root):
            return out
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_base = os.path.relpath(base, root)
            for name in files:
                rel = name if rel_base == "." else f"{rel_base}/{name}"
                parts = tuple(rel.replace(os.sep, "/").split("/"))
                if cls._excluded(parts):
                    continue
                p = os.path.join(base, name)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                try:
                    with open(p, "rb") as f:
                        out["/".join(parts)] = hashlib.sha256(f.read()).hexdigest()
                except OSError:
                    pass
        return out

    def _remote_root(self) -> str:
        return os.path.join(self.clone, self.subdir) if self.subdir else self.clone

    def _three_way(self) -> "tuple[int, int]":
        """manifest 基準の 3-way でローカル⇔クローンを橋渡しする。(imported, exported) を返す。"""
        base = self._load_manifest()
        lroot, rroot = self.bus_root, self._remote_root()
        local, remote = self._scan(lroot), self._scan(rroot)
        manifest: "dict[str, str]" = {}
        imported = exported = 0
        for rel in sorted(set(base) | set(local) | set(remote)):
            lh, rh, bh = local.get(rel), remote.get(rel), base.get(rel)
            if lh == rh:                      # 一致（双方無し含む）→ そのまま
                if lh is not None:
                    manifest[rel] = lh
                continue
            if rh == bh:                      # ローカルだけが変えた（or 消した）→ export
                take_local = True
            elif lh == bh:                    # リモートだけが変えた → import
                take_local = False
            else:                             # 同時変更 → 決定的裁定
                take_local = not self._remote_wins(rel)
            src, dst, h = (lroot, rroot, lh) if take_local else (rroot, lroot, rh)
            sub = rel.replace("/", os.sep)
            try:
                if h is None:                 # 片側の削除を伝播（gc/cleanup の掃除もリモートへ届く）
                    try:
                        os.remove(os.path.join(dst, sub))
                    except FileNotFoundError:
                        pass
                else:
                    d = os.path.join(dst, sub)
                    os.makedirs(os.path.dirname(d) or ".", exist_ok=True)
                    shutil.copyfile(os.path.join(src, sub), d)
                    manifest[rel] = h
                imported, exported = (imported, exported + 1) if take_local \
                    else (imported + 1, exported)
            except OSError:
                if bh is not None:            # 反映できなかった分は次回また差分として現れるように
                    manifest[rel] = bh
        self._save_manifest(manifest)
        return imported, exported

    # --- push（多重コミッタ吸収: rebase 再試行・コンフリクトは裁定規則で決着・force しない） ---
    def _resolve_rebase(self) -> None:
        """pull --rebase が同一ファイルの同時変更で止まったら、パス種別の裁定で決着して続行する。
        rebase 中は --ours=リモート（upstream）/ --theirs=ローカルのコミット側。"""
        gitdir = os.path.join(self.clone, ".git")
        for _ in range(50):                   # 有限（1 コミットずつしか進まない）
            if not any(os.path.isdir(os.path.join(gitdir, d))
                       for d in ("rebase-merge", "rebase-apply")):
                return
            conflicted = [ln for ln in self._git(
                "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if ln.strip()]
            for path in conflicted:
                rel = path[len(self.subdir) + 1:] if self.subdir and \
                    path.startswith(self.subdir + "/") else path
                side = "--ours" if self._remote_wins(rel) else "--theirs"
                if self._git("checkout", side, "--", path).returncode != 0:
                    self._git("rm", "-q", "--", path)   # add/delete 衝突: 消えた側に合わせる
                self._git("add", "--", path)
            if self._git("rebase", "--continue").returncode != 0 and \
                    self._git("rebase", "--skip").returncode != 0:
                self._git("rebase", "--abort")          # 進められない → 次回の 3-way で再収束
                return

    def _ahead(self) -> int:
        r = self._git("rev-list", "--count", f"origin/{self.branch}..HEAD")
        if r.returncode == 0:
            try:
                return int(r.stdout.strip() or 0)
            except ValueError:
                return 0
        # リモートにブランチが無い（初回）→ ローカルにコミットがあれば push が必要
        return 1 if self._git("rev-parse", "-q", "--verify", "HEAD").returncode == 0 else 0

    def _push(self) -> None:
        for i in range(_STATE_PUSH_RETRIES):
            push = self._git("push", "-u", "origin", self.branch)
            if push.returncode == 0:
                self._last_remote = time.time()
                return
            if self._is_corrupt_error(push):
                raise _StateGitCorrupt()      # 電源断でのローカル破損 → sync 側で作り直す
            self._git("pull", "--rebase", "origin", self.branch)   # 競合 → 取り込んで再試行
            self._resolve_rebase()
            self._last_remote = time.time()
            if i < _STATE_PUSH_RETRIES - 1:
                time.sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"state_git push が {self.branch} へ反映できませんでした")

    def _rebuild(self) -> None:
        """破損したクローンを捨て、次の sync で作り直させる（真実はローカルバス＋リモート側）。"""
        log("state_git",
            f"クローン {self.clone} のオブジェクト破損を検知——次回同期で作り直します")
        shutil.rmtree(self.clone, ignore_errors=True)
        self._ready = False

    def sync(self, force: bool = False) -> "tuple[int, int]":
        """双方向同期を 1 回行い (imported, exported) を返す。リモート操作もバス走査も
        interval で律速する（daemon の毎 poll から呼ばれても負荷を一定に保つ）。
        force=True は間隔を待たず同期する（run 終端時の結果共有用）。"""
        now = time.time()
        due = force or self.interval <= 0 or (now - self._last_remote) >= self.interval
        if not due:
            return (0, 0)
        if not self._ready:
            if not force and self.interval > 0 and now - self._last_attempt < self.interval:
                return (0, 0)                 # 不通のリモートへの再クローン連打を防ぐ
            self._last_attempt = now
            self._ensure_clone()
            self._ready = True
        try:
            with _file_lock(self.clone + ".lock"):   # 同一ホストの多重プロセスを直列化
                pull = self._git("pull", "--rebase", "origin", self.branch)
                if pull.returncode != 0 and self._is_corrupt_error(pull):
                    raise _StateGitCorrupt()
                self._resolve_rebase()
                self._last_remote = now
                imported, exported = self._three_way()
                pathspec = self.subdir or "."
                self._git("add", "-A", "--", pathspec)               # 自分の名前空間だけをステージ
                # 空コミットを試みない: unborn ブランチでの失敗 commit は index を汚し以後の pull を壊す
                if self._git("status", "--porcelain", "--", pathspec).stdout.strip():
                    # 未 push の連続 state sync は --amend で 1 コミットに束ねる（push 済み履歴は
                    # 書き換えず、他コミッタのコミットが HEAD のときは通常コミットで積む）
                    amend = ["--amend"] if (self._ahead() > 0 and self._git(
                        "log", "-1", "--format=%s").stdout.strip().startswith(
                            "kiro-flow: state sync")) else []
                    self._git("commit", "-q", *amend, "-m", f"kiro-flow: state sync {now_iso()}")
                if self._ahead() > 0:
                    self._push()
        except _StateGitCorrupt:
            # 電源断でクローンのオブジェクトが壊れた → 捨てて次回作り直す（今回分は次回に持ち越し）
            self._rebuild()
            return (0, 0)
        return imported, exported


# バス単位で管理クローンを再利用する（daemon の毎 poll・run の待機ループで作り直さない）
_STATE_GITS: "dict[tuple, StateGit]" = {}


def state_git_for(args) -> "StateGit | None":
    """state_git 設定時のみ StateGit を返す。GitBus（--git）はバス自体が共有 git なので対象外。"""
    if not getattr(args, "state_git", None) or getattr(args, "git", None):
        return None
    bus_root = os.path.abspath(args.bus)
    key = (bus_root, args.state_git, args.state_git_branch, args.state_git_subdir)
    if key not in _STATE_GITS:
        _STATE_GITS[key] = StateGit(bus_root, args.state_git, args.state_git_branch,
                                    args.state_git_subdir, args.state_git_interval)
    return _STATE_GITS[key]


def daemon_status_path(bus: Bus) -> str:
    return os.path.join(bus.root, "status.json")


def _daemon_status_fresh_after_sec(args) -> float:
    """リモート viewer が『稼働中』と信じてよい経過秒数の目安。state_git/status の同期間隔
    から書き手（自分の設定を知っている側）が計算し、viewer 側は単純比較だけで済むようにする。
    kiro-project の同名関数（write_status 側）と同じ考え方。"""
    intervals = [v for v in (getattr(args, "state_git_interval", 0.0),
                             getattr(args, "status_interval", 0.0)) if v and v > 0]
    return max([2.0 * v for v in intervals] + [120.0])


def write_daemon_status(args, bus: Bus, daemon_id: str, orchestrators: dict, workers: list) -> None:
    """status.json（生存信号）を書く。state_git（鏡）越しにリモートの kiro-projects-viewer が
    『daemon が今も生きているか』を判定するための最小スナップショット（bus.root 直下）。
    _scan() はバスのツリー全体を走査するため、ここに置くだけで既存の StateGit がそのまま
    同期対象に含める（GitBus 側のような sparse-checkout の追加設定は不要）。
    実イベント（run 終端・生存リース push）のタイミングで呼べば、そのイベントで既に走る
    state_sync/push に相乗りする＝これ単体で追加の push を生まない。

    GitBus（--git）モードでは書かない: GitBus の sparse-checkout は `runs/`/`inbox/`（or
    --git-subdir）しか作業ツリーに展開しないため、bus_root 直下のファイルは対象外の
    パスになり、GitBus.sync_push() の `git add -A` を壊しかねない（state_git と --git は
    元々ここでも相互排他 — state_git_for() と同じ前提）。"""
    if getattr(args, "git", None):
        return
    rec = {
        "host": socket.gethostname(), "pid": os.getpid(), "node_id": daemon_id,
        "orchestrators": len(orchestrators), "workers": len(workers),
        "updated_iso": now_iso(), "fresh_after_sec": _daemon_status_fresh_after_sec(args),
    }
    try:
        p = daemon_status_path(bus)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def maybe_heartbeat_daemon_status(args, bus: Bus, daemon_id: str, orchestrators: dict,
                                  workers: list) -> None:
    """daemon アイドル中の任意の生存信号更新（`--status-interval`。既定 0＝無効）。
    無効時は status.json に一切触れない＝state_git の commit-if-diff で追加コミットを
    作らない（idle の git 負荷は今日と同じゼロ）。有効時も前回書き込みから
    status_interval 秒経つまでは触らず、書き込み頻度を利用者の指定した間隔に抑える。
    GitBus（--git）モードでは何もしない（write_daemon_status 側の理由と同じ）。"""
    if getattr(args, "git", None):
        return
    interval = float(getattr(args, "status_interval", 0.0) or 0.0)
    if interval <= 0:
        return
    try:
        age = time.time() - os.path.getmtime(daemon_status_path(bus))
    except OSError:
        age = float("inf")     # 未作成 → 書く
    if age >= interval:
        write_daemon_status(args, bus, daemon_id, orchestrators, workers)


def state_git_status_line(args) -> str:
    """起動時に「state_git が有効か・どこへ鏡写しするか」を一行で示す。無効時は理由も出す
    （silent な設定ミス＝バスが見えない原因の切り分けを容易にする）。"""
    if getattr(args, "git", None):
        return "state-git: 無効（--git バス使用時はバス自体が共有 git のため不要）"
    if not getattr(args, "state_git", None):
        return ("state-git: 無効（未設定）。リモート viewer にバスを見せるには kiro-flow.yaml に "
                "state_git を設定し、この daemon がその設定を読めていること（--config か "
                "起動 cwd の .kiro/kiro-flow.yaml）を確認")
    return (f"state-git: 有効 → {args.state_git} subdir={args.state_git_subdir} "
            f"interval={args.state_git_interval}s（バス {os.path.abspath(args.bus)} をリモートへ鏡写し）")


def state_sync(args, force: bool = False) -> None:
    """状態の git 同期（best-effort）。ネットワーク断・リポジトリ不通でもループは殺さず
    ログに残して続行する（run の実行・終端は state_git に一切依存しない）。"""
    sg = state_git_for(args)
    if sg is None:
        return
    try:
        imported, exported = sg.sync(force=force)
        if imported or exported:
            log("state-git", f"同期: import={imported} export={exported}")
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        log("state-git", f"同期失敗（続行）: {e}")


# --------------------------------------------------------------------------
# 共有 git キャッシュ + worktree（docs/designs/git-worktree-cache-pattern.md）
#   リモート URL 単位のホスト共有 bare ミラー（--mirror --filter=blob:none）を 1 本持ち、
#   タスク/検証のたびに detached worktree を temp へ生やす。フル clone を「初回1回+増分」へ圧縮し、
#   GitLab の重い pack 生成を避ける。kiro-project の verify/acceptance と同じ root を共有する。
#   不変条件: INV-1 鮮度（毎 fetch→fetch 後 SHA で worktree）/ INV-2 直列化・自己修復・gc.auto=0 /
#   INV-3 失敗時は従来の direct clone へフォールバック（下限を現状に固定）。
# --------------------------------------------------------------------------
_CACHE_CORRUPT = ("not a git repository", "bad object", "corrupt", "broken link",
                  "unable to read", "object directory", "fatal: bad")
_provisioned_urls: "set[str]" = set()   # cleanup で worktree prune する対象 URL
# provision_from_local が手元のクローンに登録した worktree（cleanup で外す）: [(local, dest), …]
_local_worktrees: "list[tuple[str, str]]" = []


def cache_root() -> str:
    """ホスト共有 git キャッシュの root。環境変数 KIRO_GIT_CACHE_DIR で上書き可
    （kiro-project と必ず同じ既定にすること＝ホスト内でミラーを共有するため）。"""
    return os.environ.get("KIRO_GIT_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "kiro-git-cache")


def _cache_path_for(url: str) -> str:
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    return os.path.join(cache_root(), f"{h}.git")


@contextlib.contextmanager
def _cache_lock(url: str):
    """URL 単位のホスト内ロック（INV-2: cache の全変更を直列化）。"""
    root = cache_root()
    os.makedirs(root, exist_ok=True)
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    with _file_lock(os.path.join(root, f"{h}.lock")):
        yield


def _git_cache(cache: str, *args: str, timeout: float = 600):
    return subprocess.run(["git", "-C", cache, *args],
                          capture_output=True, text=True, timeout=timeout)


def _is_cache_valid(cache: str) -> bool:
    if not os.path.isdir(cache):
        return False
    try:
        return _git_cache(cache, "rev-parse", "--git-dir", timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mirror_clone(url: str, cache: str) -> bool:
    """url を blob:none の bare ミラーとして cache に作る。partial 非対応サーバには filter 無しで再試行。"""
    shutil.rmtree(cache, ignore_errors=True)
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    attempts = [["git", "clone", "--mirror", "--filter=blob:none", url, cache],
                ["git", "clone", "--mirror", url, cache]]   # INV-3: partial 非対応フォールバック
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            _git_cache(cache, "config", "gc.auto", "0")            # INV-2: 自動 repack 事故を防ぐ
            # --mirror が付ける remote.origin.mirror=true を無効化（refspec 付き push が拒否されるため）。
            _git_cache(cache, "config", "remote.origin.mirror", "false")
            _git_cache(cache, "config", "user.email", "kiro-flow@local")
            _git_cache(cache, "config", "user.name", "kiro-flow")
            return True
        shutil.rmtree(cache, ignore_errors=True)
    return False


def ensure_cache(url: str) -> "str | None":
    """URL の共有 bare ミラーを用意（無ければ作成・壊れていれば再作成）。ここでは fetch しない
    （鮮度は provision 側＝INV-1）。失敗時 None（呼び出し側は direct clone へフォールバック）。要 _cache_lock。"""
    cache = _cache_path_for(url)
    if _is_cache_valid(cache):
        return cache
    for i in range(CLONE_RETRIES):
        if _mirror_clone(url, cache):
            return cache
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)
    return None


def _cache_fetch(cache: str) -> bool:
    """INV-1: 全 heads を増分 fetch（リトライ付き）。blob:none ミラーなので転送はメタデータ差分のみ。
    破損系エラーは False（呼び出し側で nuke & re-mirror を誘発）。"""
    for i in range(CLONE_RETRIES):
        try:
            r = _git_cache(cache, "fetch", "--prune", "--no-tags", "origin",
                           "+refs/heads/*:refs/heads/*")
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return True
        if r is not None and any(s in (r.stderr or "").lower() for s in _CACHE_CORRUPT):
            return False
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)
    return False


def _resolve_sha(cache: str, refs: "list[str]") -> str:
    """優先順 refs の先頭で解決できたコミット SHA を返す（"" は既定ブランチ=HEAD）。無ければ ""。"""
    for ref in refs:
        cand = f"refs/heads/{ref}" if ref else "HEAD"
        try:
            r = _git_cache(cache, "rev-parse", "--verify", "--quiet",
                           f"{cand}^{{commit}}", timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""


def provision_worktree(url: str, refs: "list[str]", dest: str) -> "str | None":
    """INV-1/2 を満たして dest に detached worktree を用意する（要 _cache_lock）。失敗時 None。
    refs は作業起点の優先順（例: [run ブランチ, base, ""=既定]）。"""
    cache = ensure_cache(url)
    if not cache:
        return None
    if not _cache_fetch(cache):
        shutil.rmtree(cache, ignore_errors=True)   # INV-2: 破損疑い → 一度だけ再ミラー
        cache = ensure_cache(url)
        if not cache or not _cache_fetch(cache):
            return None
    sha = _resolve_sha(cache, refs)
    if not sha:
        return None
    dest = os.path.abspath(dest)   # `git -C <cache> worktree add` は相対パスを cache 基準で解くため絶対化
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    for _ in range(2):
        try:
            r = _git_cache(cache, "worktree", "add", "--detach", "--force",
                           dest, sha, timeout=300)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return dest
        _git_cache(cache, "worktree", "prune", timeout=60)   # locked/registered → prune して再試行
        shutil.rmtree(dest, ignore_errors=True)
    return None


def _local_remote_url(local: str) -> str:
    """ローカルクローンの origin URL（取れなければ ""）。"""
    try:
        r = subprocess.run(["git", "-C", local, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _same_repo(a: str, b: str) -> bool:
    """git URL が同じリポジトリを指すか（末尾の .git / スラッシュ / 大小文字の揺れを吸収）。"""
    def norm(u: str) -> str:
        u = str(u or "").strip().rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        return u.lower()
    return bool(norm(a)) and norm(a) == norm(b)


def provision_from_local(local: str, url: str, refs: "list[str]", dest: str) -> "str | None":
    """手元にある同じリポジトリのクローンから detached worktree を切り出す（失敗時 None）。

    ネットワーク越しに bare ミラーを取り直す必要がなくなる（速い・オフラインでも動く）。
    worktree は別ディレクトリ・別 index なので、**ローカルの作業ツリーと index には触らない**
    （人がそこで作業していても巻き込まない）。origin URL が一致するクローンだけを使う。"""
    if not local or not os.path.isdir(local):
        return None
    if not _same_repo(_local_remote_url(local), url):
        return None                       # 別のリポジトリ → 使わない（取り違え防止）
    # 手元が古いと worker が古い base で作業するので、まず取り込む（失敗しても手元の範囲で続行）
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["git", "-C", local, "fetch", "--quiet", "origin"],
                       capture_output=True, timeout=180)
    # 作業起点の優先順: run ブランチ → base → 既定。ローカル/リモート追跡の両方を見る。
    sha = ""
    for ref in [*refs, ""]:
        for cand in ([f"refs/heads/{ref}", f"refs/remotes/origin/{ref}"] if ref else ["HEAD"]):
            try:
                r = subprocess.run(["git", "-C", local, "rev-parse", "--verify", "--quiet",
                                    f"{cand}^{{commit}}"], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and r.stdout.strip():
                sha = r.stdout.strip()
                break
        if sha:
            break
    if not sha:
        return None
    dest = os.path.abspath(dest)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        r = subprocess.run(["git", "-C", local, "worktree", "add", "--detach", dest, sha],
                           capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    _local_worktrees.append((local, dest))    # 後始末（worktree remove）のため覚えておく
    return dest


def cleanup_local_worktrees() -> None:
    """provision_from_local が作った worktree の登録をローカルクローンから外す。
    （dest 自体は _workspace_root ごと rmtree される。登録だけが残ると git worktree list が汚れる）"""
    for local, dest in list(_local_worktrees):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["git", "-C", local, "worktree", "remove", "--force", dest],
                           capture_output=True, timeout=60)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["git", "-C", local, "worktree", "prune"],
                           capture_output=True, timeout=60)
    _local_worktrees.clear()


def provision_tree(url: str, refs: "list[str] | str", dest: str,
                   local: str = "") -> "str | None":
    """作業ツリーを用意する。順に:
       1. local（手元の同じリポジトリのクローン）から detached worktree を切る — 取得ゼロで最速
       2. 共有 bare ミラーから detached worktree（INV-1/2）
       3. direct clone（INV-3 フォールバック）
    返り値: 作業ツリーのパス、または None（最終的に失敗）。"""
    ref_list = [refs] if isinstance(refs, str) else list(refs)
    if local:
        wt = provision_from_local(local, url, ref_list, dest)
        if wt:
            return wt
    try:
        with _cache_lock(url):
            wt = provision_worktree(url, ref_list, dest)
        if wt:
            _provisioned_urls.add(url)
            return wt
    except Exception:  # noqa: BLE001 — cache 系の想定外失敗は黙ってフォールバックへ
        pass
    base = next((r for r in ref_list if r), "")
    return _clone_repo(url, base, dest) or None


def _prune_caches(urls) -> None:
    """指定 URL の共有 cache の worktree 登録を回収する（temp を rmtree した後の後始末）。"""
    for url in list(urls):
        try:
            with _cache_lock(url):
                cache = _cache_path_for(url)
                if os.path.isdir(cache):
                    _git_cache(cache, "worktree", "prune", timeout=60)
        except Exception:  # noqa: BLE001
            pass


def sweep_cache_dirs(min_age_sec: float) -> int:
    """長期間未使用の共有ミラーを削除し、削除数を返す（disk 逼迫対策）。生存中の worktree は
    prune してから、mtime が min_age 以上古い bare ミラーのみ消す。共有のため通常は残す。"""
    root = cache_root()
    if not os.path.isdir(root):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(root):
        if not name.endswith(".git"):
            continue
        cache = os.path.join(root, name)
        if not os.path.isdir(cache):
            continue
        try:
            age = now - os.path.getmtime(cache)
        except OSError:
            continue
        _git_cache(cache, "worktree", "prune", timeout=60)   # 生存 worktree の登録は常に整理
        if age < min_age_sec:
            continue
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    return removed


# --------------------------------------------------------------------------
# ワークスペース — この run（=バックログ単位）の唯一の書込先リポジトリ。
#   worker が temp 領域へ clone し、作業ブランチ kf/<run_id> を base から作って作業する。
#   変更があれば kiro-flow が commit して push する（エージェントは編集のみ）。読み取り専用
#   グラフ（変更ゼロ）なら何も push しない。参照だけのリポジトリはワークスペースではなく、
#   タスク記述（goal 本文）として伝搬する（kiro-project が埋め込む）。
#   リポジトリの同一性は (url, path, base) で判定する（同 URL でも path/ブランチが違えば別）。
# --------------------------------------------------------------------------
_workspace_clone: "dict[tuple, str]" = {}   # (url,path,base) -> clone パス（""=clone 失敗）
_workspace_root: "str | None" = None


def _repo_name(url: str) -> str:
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return _safe(base) or "repo"


def parse_workspace(token: "str | None") -> "dict | None":
    """`--workspace` トークンをワークスペース spec に正規化する。素の URL でも、kiro-project が
    付ける JSON（{url,path,base,target,desc,branch}）でも受ける。url が無ければ None（読み取り専用 run）。
    `branch` は任意の**明示作業ブランチ**（kiro-project のタスク単位ブランチ kp/<task-id> 等）。
    指定があれば run 毎の kf/<run-id> の代わりにそこへ push する＝リトライ（別 run-id）でも
    同一ブランチへ成果を積み増せる。"""
    token = (token or "").strip()
    if not token:
        return None
    spec = {"url": "", "path": "", "base": "", "target": "", "desc": ""}
    if token.startswith("{"):
        try:
            d = json.loads(token)
        except (ValueError, TypeError):
            d = None
        if isinstance(d, dict) and d.get("url"):
            for k in ("url", "path", "base", "target", "desc", "branch"):
                if d.get(k):
                    spec[k] = str(d[k]).strip()
            return spec
        return None
    spec["url"] = token                           # 素の URL（メタ無し）
    return spec


def parse_references(tokens: "list[str] | None") -> "list[dict]":
    """`--reference` トークン列を参照リポジトリ spec 列へ正規化する（読むだけ・書き込まない）。
    各トークンは素の URL でも JSON（{url,path,base,desc}）でも可。url の無いものは捨てる。"""
    out: "list[dict]" = []
    seen: "set[str]" = set()
    for tok in (tokens or []):
        spec = parse_workspace(tok)               # 同じ正規化を流用（target は参照では未使用）
        if spec and spec["url"] and spec["url"] not in seen:
            seen.add(spec["url"])
            out.append(spec)
    return out


def reference_instruction(refs: "list[dict]") -> str:
    """参照リポジトリ（読むだけ）をエージェントへ伝える指示ブロック。書込先ではないことを明示する。"""
    if not refs:
        return ""
    lines = ["【参照リポジトリ】読み取り専用。変更・commit・push はしないこと。必要に応じて内容を参照する:"]
    for s in refs:
        label = s["url"]
        tags = []
        if s.get("path"):
            tags.append(f"フォルダ {s['path']}")
        if s.get("base"):
            tags.append(f"ブランチ {s['base']}")
        line = f"  - {label}" + ("（" + "・".join(tags) + "）" if tags else "")
        if s.get("desc"):
            line += f": {s['desc']}"
        lines.append(line)
    return "\n".join(lines)


def workspace_id(spec: dict) -> tuple:
    """ワークスペースの一意キー = (url, path, base)。同 URL でも path（モノレポのフォルダ）や
    base（作業ブランチ）が違えば別ワークスペースとして扱う。"""
    return (spec.get("url", ""), spec.get("path", ""), spec.get("base", ""))


def run_branch_name(run_id: str) -> str:
    """この run の作業ブランチ名。worker が base から作り、変更を push する先。"""
    return f"kf/{_safe(run_id)}"


def _clone_repo(url: str, base: str, dest: str) -> str:
    """url を dest へ clone する。base 指定があればそのブランチを checkout（無ければ既定にフォールバック）。
    成功で dest、失敗で "" を返す。一過性のネットワーク障害に備え、バスクローン／push／pull と同じ
    指数バックオフでリトライする（委譲される側＝実作業ノードが起動毎にワークスペースを clone するため、
    ここがネットワーク不安定時に「clone 失敗→タスク失敗」になりやすい）。"""
    attempts = []
    if base:
        attempts.append(["git", "clone", "-b", base, url, dest])
    attempts.append(["git", "clone", url, dest])
    for i in range(CLONE_RETRIES):
        for cmd in attempts:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if r.returncode == 0:
                    return dest
            except (OSError, subprocess.SubprocessError):
                pass
            if os.path.exists(dest):              # 失敗の残骸を消してからフォールバック／再試行
                shutil.rmtree(dest, ignore_errors=True)
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)   # バックオフして再試行
    return ""


def _ws_git(clone: str, *args: str):
    """clone 内で git を実行（capture, check しない）。"""
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True)


def _prepare_run_branch(clone: str, branch: str, base: str) -> None:
    """作業ツリーを run の作業起点に整える（commit 用の identity を保証する）。
    worktree は detached のまま・direct clone フォールバックは現在の HEAD（base/既定）から作業し、
    実際の作業ブランチは finalize_workspace が push 時に `HEAD:refs/heads/<branch>` で作る。
    ブランチを checkout しないので「同一ブランチを2つの worktree で同時 checkout 不可」制約を受けない。
    既存の run ブランチへの追従は provision 時に refs 優先順 [branch, base] で起点に反映済み。"""
    if not _ws_git(clone, "config", "user.email").stdout.strip():
        _ws_git(clone, "config", "user.email", "kiro-flow@local")
        _ws_git(clone, "config", "user.name", "kiro-flow")


def ensure_workspace_clone(spec: "dict | None", run_id: str) -> "dict | None":
    """run のワークスペースを worker 専用 temp へ clone し、作業ブランチを用意する。
    ブランチは spec の明示 `branch`（kiro-project のタスク単位ブランチ等）＞ run 毎の kf/<run_id>。
    (url,path,base) 単位でプロセス内キャッシュ。spec が無ければ None（読み取り専用 run）。
    返り値は spec に clone 先パス（clone="" は失敗）と branch を足した dict。"""
    global _workspace_root
    if not spec or not spec.get("url"):
        return None
    branch = str(spec.get("branch") or "").strip() or run_branch_name(run_id)
    key = workspace_id(spec)
    if key in _workspace_clone:
        return {**spec, "clone": _workspace_clone[key], "branch": branch}
    if _workspace_root is None:
        # pid を名に埋める → SIGKILL 等で残った孤立 clone を janitor が安全に回収できる。
        _workspace_root = tempfile.mkdtemp(prefix=f"kiro-flow-ws-{os.getpid()}-")
    stem = _repo_name(spec["url"])
    dest = os.path.join(_workspace_root, stem)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(_workspace_root, f"{stem}-{n}")
        n += 1
    base = spec.get("base") or ""
    # 作業起点の優先順: 既存の run ブランチ → base → 既定（detached worktree で作り、push 時に作業ブランチ化）。
    # repos に local（手元の同じリポジトリのクローン）があれば、そこから worktree を切る。
    # 目の前に同じリポジトリがあるのに毎回ネットワーク越しにミラーを取り直すのは無駄で、
    # オフラインでも動かない。local の作業ツリー・index には触らない（別 worktree なので）。
    path = provision_tree(spec["url"], [branch, base], dest,
                          local=str(spec.get("local") or "")) or ""
    if path:
        _prepare_run_branch(path, branch, base)
    _workspace_clone[key] = path
    return {**spec, "clone": path, "branch": branch}


def finalize_workspace(ws: "dict | None", run_id: str, node_id: str) -> "dict | None":
    """エージェント実行後、ワークスペースに変更があれば作業ブランチへ commit し push する
    （rebase リトライで分散ワーカーの push を統合）。変更が無ければ何もしない＝読み取り専用
    グラフ（調査タスク等）ではブランチを push しない。返り値: 反映したデリバリ dict か None。"""
    if not ws:
        return None
    clone, branch = ws.get("clone"), ws.get("branch")
    if not clone or not os.path.isdir(clone):
        return None
    _ws_git(clone, "add", "-A")
    if _ws_git(clone, "diff", "--cached", "--quiet").returncode == 0:
        return None                               # 変更なし → commit/push しない
    _ws_git(clone, "commit", "-m", f"[kiro-flow] {node_id} ({run_id})")
    for i in range(5):
        # detached HEAD のまま作業ブランチへ push（ローカルでブランチを checkout しない）。
        if _ws_git(clone, "push", "origin", f"HEAD:refs/heads/{branch}").returncode == 0:
            head = _ws_git(clone, "rev-parse", "HEAD").stdout.strip()
            return {"url": ws.get("url"), "branch": branch, "commit": head,
                    "target": ws.get("target") or ws.get("base") or "", "path": ws.get("path") or ""}
        # reject → リモートの branch を FETCH_HEAD に取り込み（共有 cache の ref は書き換えない）、
        # detached のまま rebase して再 push。分散ワーカーの push を統合する。
        _ws_git(clone, "fetch", "--quiet", "origin", branch)
        _ws_git(clone, "rebase", "FETCH_HEAD")
        time.sleep(2 ** i if i < 4 else 16)
    raise RuntimeError(f"workspace push が {branch} へ反映できませんでした")


def cleanup_workspace() -> None:
    """worker の作業ツリー（temp の worktree／フォールバック clone）を丸ごと削除する（作業後クリーンは必須）。
    共有 cache 本体は残し、worktree 登録だけ prune して回収する。"""
    global _workspace_root
    cleanup_local_worktrees()   # 手元のクローンに残した worktree 登録を先に外す（消す前に）
    if _workspace_root and os.path.isdir(_workspace_root):
        shutil.rmtree(_workspace_root, ignore_errors=True)
    _workspace_root = None
    _workspace_clone.clear()
    _prune_caches(_provisioned_urls)
    _provisioned_urls.clear()


def workspace_instruction(ws: "dict | None") -> str:
    """唯一の書込先ワークスペースをエージェントに伝える決定的な指示ブロック。
    clone 先・対象フォルダ(path)・作業ブランチ(base→target)・役割(desc) を示し、編集だけ行わせる
    （commit/push は kiro-flow が行う）。この指示は call_executor 経由で executor へ goal とは別引数
    （repo_instruction）として渡る（gitlab executor は起票先の解決とイシュー本文に使う）。"""
    if not ws:
        return ""
    if not ws.get("clone"):
        return f"【ワークスペース】clone に失敗しました（{ws.get('url') or ''}）。書き込みはできません。"
    lines = [f"【ワークスペース】このタスクの唯一の書込先リポジトリ（clone 済み）: {ws.get('url')}",
             f"  作業ディレクトリ: {ws['clone']}"]
    if ws.get("path"):
        lines.append(f"  変更してよいのは {ws['path']} 配下のみ（他フォルダは触らないこと）")
    br = f"  作業ブランチ: {ws.get('branch')}"
    if ws.get("base"):
        br += f"（{ws['base']} から分岐"
        if ws.get("target") and ws["target"] != ws["base"]:
            br += f"・最終的な MR/PR ターゲット = {ws['target']}"
        br += "）"
    lines.append(br)
    if ws.get("desc"):
        lines.append(f"  役割: {ws['desc']}")
    lines.append("  作業ツリー内のファイルを編集すること。commit と push は kiro-flow が自動で行うので、"
                 "あなたは commit/push やブランチ切替をしないこと。変更が不要（調査のみ）なら何も書き換えない。")
    return "\n".join(lines)


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


def plan_strategy_kiro(request: str, model: str | None, review="auto", granularity="finest"):
    """kiro-cli にパターン選択・並列数・初期グラフを決めさせる。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。
    granularity で分解の細かさを指示し、返ってきた並列数も粒度倍率でスケールする。
    ワークスペース（唯一の書込先）は run 単位なので、ノードへの repo 割当はしない。"""
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
        + "出力は JSON オブジェクトのみ:\n"
        '{"patterns": ["..."], "parallelism": N, "reason": "...", '
        '"tasks": [{"id": "t1", "goal": "...", "deps": [], "kind": "work"}]}\n\n'
        f"要求: {request}"
    )
    try:
        data = extract_json(run_kiro(prompt, model, purpose="planner"))
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


def _find_skill_script(skill: str, script: str):
    """スキルの scripts/{script} を探す（flow-planner / flow-worker 共通）。
    検索順: .github/skills/{skill}/ → git root/.github/skills/ → ~/.kiro/skills/ → {skill_home}/"""
    candidates = []
    # ワークスペース内
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, ".github", "skills", skill, "scripts", script))
    # リポジトリルート（git rev-parse で探す）
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True
        ).stdout.strip()
        if root:
            candidates.append(os.path.join(root, ".github", "skills", skill, "scripts", script))
    except Exception:  # noqa: BLE001
        pass
    # ~/.kiro/skills 直下を直接確認
    kiro_skills = os.path.expanduser("~/.kiro/skills")
    candidates.append(os.path.join(kiro_skills, skill, "scripts", script))
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
                    candidates.append(os.path.join(home, skill, "scripts", script))
            except Exception:  # noqa: BLE001
                pass
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _find_flow_planner_script():
    """flow-planner スキルの plan.py を探す。"""
    return _find_skill_script("flow-planner", "plan.py")


def plan_strategy_flow_planner(request: str, model: str | None, review="auto", granularity="finest"):
    """flow-planner スキルの3段パイプラインを呼び出す。
    スキルが見つからない / 失敗した場合は plan_strategy_kiro にフォールバック。
    granularity はスキルへ `--granularity` で渡し、返ってきた並列数も粒度倍率でスケールする。"""
    script = _find_flow_planner_script()
    if not script:
        # flow-planner スキル未インストール → kiro planner にフォールバック
        return plan_strategy_kiro(request, model, review, granularity)
    # 計画に使う CLI/モデルは planner の設定（agents: planner: {agent_cli, model}）に従わせる。
    # スキル側の既定は kiro-cli だが、それを黙って使うと agent_cli を claude/codex にしていても
    # 計画だけ kiro-cli で走り、kiro-cli が使えない環境では毎回失敗して stub へ落ちていた。
    cli, model_ov = _agent_for("planner")
    cmd = [sys.executable, script, request, "--granularity", str(granularity),
           "--agent-cli", cli]
    model = model_ov or model
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
# LLM 実行に使うエージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）。
_AGENT_CLI: str = str(CONFIG_DEFAULTS["agent_cli"])
# 役割（purpose）毎の上書き（設定 agents: の正規化済みマップ）。キーは planner / evaluator /
# worker（全 kind の既定）/ 個別 kind（work/generate/classify/synthesize/verify/filter/judge/
# reduce/split/map）。値は {agent_cli, model}。子プロセスへは --config 伝搬で同じ設定が届く。
_AGENT_OVERRIDES: "dict[str, dict]" = {}
AGENT_ROLES = ("planner", "evaluator", "worker")
# executor=agent の実行系プロンプトを供給するスキル名（設定 worker_skill）。
# none/builtin/空 で無効＝常に組み込みプロンプト。
_WORKER_SKILL: str = str(CONFIG_DEFAULTS["worker_skill"])
# agent_cli の設定値 → doctor が PATH 確認すべき実行ファイル名（未知の agent_cli はそのまま使う）。
_AGENT_CLI_BINARIES = {"kiro": "kiro-cli", "claude": "claude", "copilot": "copilot",
                       "codex": "codex"}


def _normalize_agent_overrides(raw) -> "dict[str, dict]":
    """設定 agents:（役割毎の agent_cli/model 上書き）を正規化する。有効キーは AGENT_ROLES
    と各ノード kind（VALID_KINDS）。不正な値は黙って落とす（設定ミスで run を殺さない）。"""
    out: "dict[str, dict]" = {}
    if not isinstance(raw, dict):
        return out
    valid = set(AGENT_ROLES) | set(VALID_KINDS)
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in valid or not isinstance(v, dict):
            continue
        ov: dict = {}
        if v.get("agent_cli"):
            ov["agent_cli"] = str(v["agent_cli"]).strip().lower()
        if v.get("model"):
            ov["model"] = str(v["model"]).strip()
        if ov:
            out[key] = ov
    return out


def _agent_for(purpose: str) -> "tuple[str, str | None]":
    """役割（purpose）の実効エージェント (agent_cli, model 上書き)。解決順:
    agents[purpose] ＞（purpose がノード kind なら）agents["worker"] ＞ グローバル agent_cli。"""
    ov = _AGENT_OVERRIDES.get(purpose)
    if ov is None and purpose in VALID_KINDS:
        ov = _AGENT_OVERRIDES.get("worker")
    ov = ov or {}
    return (str(ov.get("agent_cli") or _AGENT_CLI).lower(), ov.get("model") or None)


def _configure_thresholds(args) -> None:
    """設定ファイル/CLI（resolve_config 済み）の閾値をモジュール変数へ確定させる。
    run_kiro / executor 解決は args を受け取らないため、プロセス起動時に一度だけ値を固定する。"""
    global _ARGV_LIMIT, _EXECUTOR_DIR, _KIRO_TIMEOUT, _STUB_SLEEP_MAX, _AGENT_CLI, _AGENT_OVERRIDES
    global _WORKER_SKILL
    ac = getattr(args, "agent_cli", None)
    if ac:
        _AGENT_CLI = str(ac).lower()
    _AGENT_OVERRIDES = _normalize_agent_overrides(getattr(args, "agents", None))
    wsk = getattr(args, "worker_skill", None)
    if wsk is not None:
        _WORKER_SKILL = str(wsk).strip()
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


# --- エージェント CLI プラグイン（データ契約: schemas/agent-cli.schema.json） -----------------
# 組み込み（kiro/claude/copilot/codex）以外の CLI（cursor / ollama / hermes …）を、
# 定義ファイル agents/<name>.json だけで差し込む公式の口。kiro-project も同じ契約を読む
# （結合はデータ契約のみ・ローダは各ツールが自前で持つ = ツール間のコード依存を作らない）。
_AGENT_PLUGIN_CACHE: "dict[str, dict | None]" = {}


def _agent_plugin_dirs() -> list:
    dirs = []
    envd = os.environ.get("KIRO_AGENTS_DIR")
    if envd:
        dirs.append(os.path.expanduser(envd))
    dirs.append(os.path.join(os.getcwd(), "agents"))
    dirs.append(os.path.expanduser("~/.kiro/agents"))
    return dirs


def _normalize_agent_plugin(name: str, raw: dict, path: str) -> dict:
    cmd = raw.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise RuntimeError(f"エージェント定義 {path}: command は文字列配列が必須です")
    output = str(raw.get("output", "stdout"))
    if output == "file" and not any("{output_file}" in c for c in cmd):
        raise RuntimeError(f"エージェント定義 {path}: output=file には command 中の "
                           "{output_file} プレースホルダが必要です")
    errors = []
    for e in (raw.get("errors") or []):
        try:
            errors.append((str(e.get("class", "env")),
                           re.compile(str(e.get("match", "")), re.I),
                           str(e.get("hint", ""))))
        except re.error as ex:
            raise RuntimeError(f"エージェント定義 {path}: errors.match が正規表現として不正です: {ex}")
    return {"name": name, "command": list(cmd),
            "prompt_via": str(raw.get("prompt_via", "stdin")),
            "prompt_flag": raw.get("prompt_flag"),
            "model_flag": raw.get("model_flag"),
            "default_model": raw.get("default_model"),
            "output": output, "env": dict(raw.get("env") or {}),
            "timeout": raw.get("timeout"),
            "empty_output_is_error": bool(raw.get("empty_output_is_error", True)),
            "errors": errors, "path": str(path)}


def load_agent_plugin(name: str) -> "dict | None":
    """agents/<name>.json を探索順（$KIRO_AGENTS_DIR → <cwd>/agents → ~/.kiro/agents）に読む。
    無ければ None（プロセス内キャッシュ）。壊れた定義は黙って無視せず RuntimeError。"""
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key in _AGENT_PLUGIN_CACHE:
        return _AGENT_PLUGIN_CACHE[key]
    spec = None
    for d in _agent_plugin_dirs():
        p = os.path.join(d, f"{key}.json")
        try:
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except ValueError as e:
            raise RuntimeError(f"エージェント定義 {p} を JSON として読めません: {e}")
        except OSError:
            continue
        spec = _normalize_agent_plugin(key, raw, p)
        break
    _AGENT_PLUGIN_CACHE[key] = spec
    return spec


def _plugin_agent_cmd(plug: dict, model: "str | None", prompt: str):
    """プラグイン定義から (argv, stdin テキスト, 最終応答ファイル) を組み立てる（決定的）。"""
    model = model or plug.get("default_model") or None
    out_file = None
    cmd = []
    used_model = False
    for part in plug["command"]:
        if "{output_file}" in part:
            if out_file is None:
                fd, out_file = tempfile.mkstemp(prefix=f"kiro-agent-{plug['name']}-", suffix=".txt")
                os.close(fd)
            part = part.replace("{output_file}", out_file)
        if "{model}" in part:
            if not model:
                continue                          # モデル未指定 → トークンごと省く
            part = part.replace("{model}", model)
            used_model = True
        cmd.append(part)
    if model and not used_model and plug.get("model_flag"):
        cmd += [str(plug["model_flag"]), model]
    if plug["prompt_via"] == "argv":
        if plug.get("prompt_flag"):
            cmd += [str(plug["prompt_flag"]), prompt]
        else:
            cmd.append(prompt)
        return cmd, None, out_file
    return cmd, prompt, out_file


def _plugin_error_patterns() -> tuple:
    out = []
    for spec in _AGENT_PLUGIN_CACHE.values():
        if spec:
            out.extend(spec.get("errors") or [])
    return tuple(out)


# --- 失敗トリアージ（決定的） -------------------------------------------------------------
# エラー本文から「誰が直すか」を分類し、メッセージ先頭の機械可読タグ [agent-error:<class>] で運ぶ。
# kiro-flow は run の打ち切り（環境要因なら全ノードでリトライを焼かない）、kiro-project は
# リトライ節約と人への説明、viewer は行動提示に同じ判定を使う。
#   quota=利用上限（時間をおけば回復）/ auth=認証切れ（人が直す）/ env=実行環境の問題（人が直す）
#   / transient=一時的（通常リトライで解ける）。該当なし＝内容の問題（タスク単位の retry / 再計画）。
AGENT_ERROR_ENV_CLASSES = ("quota", "auth", "env")
_AGENT_ERROR_TAG_RE = re.compile(r"\[agent-error:(quota|auth|env|transient)\]")
_AGENT_ERROR_PATTERNS = (
    ("quota", re.compile(r"usage limit|quota exceeded|rate.?limit|too many requests", re.I),
     "利用上限に達しています（時間をおくか、プラン・クレジットを見直してください）"),
    ("auth", re.compile(r"AccessDenied|Unauthorized|authentication failed|not authenticated"
                        r"|SendMessageError|please (re)?login", re.I),
     "認証に失敗しています（再ログインが必要です）"),
    ("env", re.compile(r"issue with the selected model|invalid model"
                       r"|model .{0,40}(not found|does not exist)|may not have access to it"
                       r"|command not found|No such file or directory", re.I),
     "実行環境の問題です（モデル名・CLI の導入・PATH を確認してください）"),
    ("transient", re.compile(r"timed? ?out|connection (reset|refused|closed)|ECONNRESET"
                             r"|ETIMEDOUT|temporarily unavailable|service unavailable|overloaded",
                             re.I),
     "一時的なエラーです（自動でやり直します）"),
)


def classify_agent_failure(blob: str) -> "tuple[str, str] | None":
    """エラー本文を (class, hint) に分類する（該当なしは None＝内容の問題）。
    既にタグ付きならそれが正。プラグイン定義の errors を汎用パターンより先に評価する。"""
    text = str(blob or "")
    m = _AGENT_ERROR_TAG_RE.search(text)
    if m:
        hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == m.group(1)), "")
        return m.group(1), hint
    for cls, pat, hint in _plugin_error_patterns() + _AGENT_ERROR_PATTERNS:
        if pat.search(text):
            return cls, hint
    return None


def _agent_failure(cli: str, rc: int, out: str, err: str) -> str:
    """エージェント CLI の失敗を、人が原因に辿り着ける文言にする。

    CLI は起動バナー（workdir / model / プロンプト全文）を stderr へ流す。先頭だけを切り取ると
    肝心のエラーがバナーに埋もれて消える — 実際 codex の「利用上限に達した」を丸ごと取り逃し、
    全ノードが理由不明の failed になった。エラーは末尾に出るので末尾を拾い、分類（トリアージ）は
    機械可読タグとして先頭に載せる。"""
    blob = f"{out or ''}\n{err or ''}"
    triage = classify_agent_failure(blob)
    head = f"{cli} 失敗 (rc={rc})"
    if triage:
        cls, hint = triage
        head = f"[agent-error:{cls}] {head}" + (f": {hint}" if hint else "")
    tail = (err or out or "").strip()
    return f"{head}\n{tail[-500:]}" if tail else head


def run_kiro(prompt: str, model: str | None, purpose: str = "") -> str:
    """エージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）を 1 回呼び出してテキスト応答を返す。
    このツールの LLM 呼び出しはすべてここを通る（planner / executor / verify / 裁定）。
    purpose（planner / evaluator / ノード kind）を渡すと設定 agents: の役割毎上書きが効く
    （kind は agents["worker"] へフォールバック）。model は 上書き ＞ 呼び出し値。"""
    cli, model_ov = _agent_for(purpose)
    model = model_ov or model
    stdin_text = None
    spill = None
    out_file = None
    if cli == "claude":
        # Claude Code ヘッドレス。プロンプトは stdin 渡し（ARG_MAX に当たらないためスピル不要）。
        cmd = ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        stdin_text = prompt
    elif cli == "codex":
        # OpenAI Codex CLI ヘッドレス（codex exec）。プロンプトは stdin 渡し（"-"）。
        # stdout には実行イベントログが混ざるため、最終応答は --output-last-message の
        # ファイルから読む。--skip-git-repo-check は git リポジトリ外でも動かすため。
        fd, out_file = tempfile.mkstemp(prefix="kiro-flow-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        cmd.append("-")
        stdin_text = prompt
    elif cli in ("copilot", "kiro", ""):
        if cli == "copilot":
            # GitHub Copilot CLI ヘッドレス。-s で応答本文のみ、--allow-all-tools は
            # 非対話モードの必須フラグ（--allow-all-paths はファイル読み書きの許可）。
            # プロンプトは -p の引数（argv）なので kiro と同じスピル退避を適用する。
            cmd = ["copilot", "-s", "--allow-all-tools", "--allow-all-paths", "--no-color"]
        else:
            cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
        if model:
            cmd += ["--model", model]
        # プロンプトが大きすぎて argv 長制限に達する恐れがあれば、一時ファイルへ退避して
        # 「そのファイルを読んで実行」する短い指示に置き換える（成果物の受け渡しを参照渡しに）。
        if len(prompt.encode("utf-8")) > _kiro_argv_limit():
            fd, spill = tempfile.mkstemp(prefix="kiro-flow-prompt-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            prompt = ("以下のファイルにこのタスクの全文（依存タスクの成果物を含む）があります。"
                      f"必ずファイルの内容を読み込み、その指示に従ってタスクを実行してください: {spill}")
        cmd += (["-p", prompt] if cli == "copilot" else [prompt])
    else:
        # 組み込み以外 → プラグイン定義（agents/<name>.json・契約は schemas/agent-cli.schema.json）。
        # 以前は未知の agent_cli が黙って kiro-cli に落ちていた（設定ミスに気づけない罠）。
        plug = load_agent_plugin(cli)
        if plug is None:
            raise RuntimeError(
                f"未知の agent_cli です: {cli!r}（組み込みは kiro/claude/copilot/codex。"
                f"それ以外は agents/{cli}.json 定義が必要です — 契約: schemas/agent-cli.schema.json・"
                f"探索順: $KIRO_AGENTS_DIR → <cwd>/agents → ~/.kiro/agents）")
        if plug["prompt_via"] == "argv" and len(prompt.encode("utf-8")) > _kiro_argv_limit():
            fd, spill = tempfile.mkstemp(prefix="kiro-flow-prompt-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            prompt = ("以下のファイルにこのタスクの全文（依存タスクの成果物を含む）があります。"
                      f"必ずファイルの内容を読み込み、その指示に従ってタスクを実行してください: {spill}")
        cmd, stdin_text, out_file = _plugin_agent_cmd(plug, model, prompt)
    plug = _AGENT_PLUGIN_CACHE.get(cli)   # プラグインなら env/timeout の上書きが効く
    env = {**os.environ, **((plug or {}).get("env") or {})}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=stdin_text,
                              timeout=(plug or {}).get("timeout") or _kiro_timeout(), env=env)
    except subprocess.TimeoutExpired:
        # 失敗として上位へ。タスクは failed 記録 → 再計画で retry に回り、run は前進する
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)
        raise RuntimeError(f"{cmd[0]} タイムアウト（{_kiro_timeout():.0f}s 超過）")
    finally:
        if spill:
            with contextlib.suppress(OSError):
                os.remove(spill)
    try:
        if proc.returncode != 0:
            raise RuntimeError(_agent_failure(cmd[0], proc.returncode, proc.stdout, proc.stderr))
        text = strip_ansi(proc.stdout).strip()
        if out_file:   # codex 等: 最終応答ファイルが取れればそれを正とする（stdout はイベントログ）
            with contextlib.suppress(OSError):
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
        if not text and plug is not None and not plug.get("empty_output_is_error", True):
            return ""
        if not text:
            # rc=0 でも本文が空で返る CLI がある（kiro-cli は AWS 認証が切れるとバナーだけ出して
            # rc=0 で終わる）。空を成功として扱うと、worker は「空の成果物で done」、planner は
            # stub 戦略へ黙って落ちる＝LLM を呼べていないのに動いているように見える。失敗にする。
            raise RuntimeError(_agent_failure(cmd[0], 0, proc.stdout, proc.stderr)
                               .replace("失敗 (rc=0)", "が空の応答を返しました (rc=0)"))
        return text
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)


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


# flow-worker スキルの prompt.py の解決結果メモ（プロセス内。未発見 = None も記憶する）。
_worker_skill_script: "dict[str, str | None]" = {}


def _flow_worker_prompt(payload: dict) -> "str | None":
    """flow-worker スキルのプロンプトビルダーを呼び、実行規律入りプロンプトを得る。

    flow-planner と同じ作戦: スキル未インストール・生成失敗なら None を返し、
    呼び出し側は組み込みプロンプトへフォールバックする（run を止めない）。
    ビルダーは決定的（LLM 無し）で、LLM 呼び出し・役割別ルーティングは従来どおり
    run_kiro が担う。payload は stdin JSON 渡し（依存成果が大きくても ARG_MAX に当たらない）。"""
    skill = (_WORKER_SKILL or "").strip().lower()
    if not skill or skill in ("none", "builtin", "off"):
        return None
    if skill not in _worker_skill_script:
        _worker_skill_script[skill] = _find_skill_script(skill, "prompt.py")
    script = _worker_skill_script[skill]
    if not script:
        return None
    try:
        proc = subprocess.run([sys.executable, script],
                              input=json.dumps(payload, ensure_ascii=False, default=str),
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:300])
        return proc.stdout.strip() or None
    except Exception:  # noqa: BLE001 — スキル失敗は組み込みプロンプトで続行
        return None


def execute_kiro(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = "", workspace: "dict | None" = None,
                 references: "list[dict] | None" = None, request: str = ""):
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
    art_note = artifact_instruction(art_dir, dep_arts)
    # flow-worker スキルがあれば実行規律入りプロンプトを使う（無ければ従来の組み込み）。
    # 出力契約（verify の JSON・split の配列等）はスキル側でも同一に保たれている。
    prompt = _flow_worker_prompt({
        "role": "worker", "kind": kind, "goal": goal, "request": request,
        "deps": {d: {"output": _dep_text(r), "data": _dep_data(r)} for d, r in deps.items()},
        "repo_instruction": repo_instruction, "artifact_note": art_note,
        "workspace": workspace, "references": references or [],
    })
    if not prompt:
        prompt = f"あなたは分散 Dynamic Workflow の{role}\nタスク({kind}): {goal}\n"
        if repo_instruction:  # 成果物リポジトリの clone 指示（ローカル実行のエージェントへ伝える）
            prompt += repo_instruction + "\n"
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
    text = run_kiro(prompt, model, purpose=kind)   # agents: の kind 別上書き（無ければ worker）
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
# executor プラグイン — agent/stub は組み込み、それ以外はプラグインを動的ロードする
#
#   kiro-loop の event_hook と同じ流儀で、executor をプラグイン化する。`--executor`
#   （設定 executor）には次のいずれかを指定できる:
#     - "agent" / "stub"  : 組み込み executor（agent はエージェント CLI に委譲。設定 agent_cli
#       で kiro/claude/copilot を切替）
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
BUILTIN_EXECUTORS = {"agent": "execute_kiro", "stub": "execute_stub"}


def _executor_accepts(execute, name: str) -> bool:
    """executor が キーワード引数 `name` を受け取れるか（名前付き引数 or **kwargs）。"""
    try:
        sig = inspect.signature(execute)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def call_executor(execute, kind: str, goal: str, dep_results: dict, model: "str | None",
                  art_dir, dep_arts, repo_instruction: str = "", workspace: "dict | None" = None,
                  references: "list[dict] | None" = None, request: str = ""):
    """executor を呼ぶ単一の入口。
    - `repo_instruction`（ワークスペース＋参照の作業指示テキスト）は、受け取れる executor には**別引数**で
      渡して goal を汚さない（gitlab のイシュータイトル/目的が指示で埋まらないようにする）。
    - `workspace`（構造化 spec dict: url/path/base/target）は、受け取れる executor へそのまま渡す
      （gitlab は起票先プロジェクトをこの url から解決する）。
    - `references`（参照リポジトリ spec 列）も、受け取れる executor へそのまま渡す
      （gitlab はイシュー本文に参照節を出す）。
    どれも受け取れない executor には、指示を goal 先頭へ結合して渡す。"""
    kwargs = {}
    if repo_instruction and _executor_accepts(execute, "repo_instruction"):
        kwargs["repo_instruction"] = repo_instruction
    if workspace is not None and _executor_accepts(execute, "workspace"):
        kwargs["workspace"] = workspace
    if references and _executor_accepts(execute, "references"):
        kwargs["references"] = references
    if request and _executor_accepts(execute, "request"):
        kwargs["request"] = request  # run の元要求（worker が全体文脈として使う）
    if kwargs or not repo_instruction:
        return execute(kind, goal, dep_results, model, art_dir, dep_arts, **kwargs)
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


# executor 名 → 解決済みプラグインパスのキャッシュ。_executor_search_dirs() が git rev-parse を
# 走らせるため、service_waits の毎 tick 解決で無用なサブプロセスを撒かないよう一度だけ解決する。
_executor_path_cache: "dict[str, str | None]" = {}


def _resolve_executor_plugin(spec: str) -> "str | None":
    """executor 名 or パスからプラグイン .py の絶対パスを解決する。無ければ None。
    プロセス内で結果をキャッシュする（同一 spec の再解決で git rev-parse を繰り返さない）。"""
    if spec in _executor_path_cache:
        return _executor_path_cache[spec]
    resolved = None
    # 明示パス（.py）
    p = os.path.expanduser(spec)
    if p.endswith(".py") and os.path.isfile(p):
        resolved = os.path.abspath(p)
    # 検索ディレクトリの <name>.py
    elif not os.sep in spec and not spec.endswith(".py"):
        for d in _executor_search_dirs():
            cand = os.path.join(d, f"{spec}.py")
            if os.path.isfile(cand):
                resolved = cand
                break
    _executor_path_cache[spec] = resolved
    return resolved


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
    spec = getattr(args, "executor", None) or "agent"
    if spec in BUILTIN_EXECUTORS:
        return None
    cfg = getattr(args, spec, None)
    if isinstance(cfg, dict) and cfg:
        return json.dumps(cfg, ensure_ascii=False)
    return None


def make_executor(args):
    """args.executor を解決し、execute(kind, goal, dep_results, model, art_dir, dep_arts)
    形の呼び出し可能オブジェクトを返す。プラグインのときは設定ブロックを環境変数で渡す。"""
    spec = getattr(args, "executor", None) or "agent"
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


# --------------------------------------------------------------------------
# park & poll — 承認待ち等の長い外部待機を worker スロットから切り離す
# --------------------------------------------------------------------------
# 設計: executor が決着していないとき DeferDecision を投げ、worker は claim を解放して
# waits/<node>.json に park 記録を残す（node_state は "waiting"）。監視主体（daemon/run）の
# service_waits が全 park をバッチで再確認し、決着なら終端 result を直接書く。これにより
# 「ブロック worker N 台 ×(1/poll)」を「監視 1 本 ×(1/watch_interval) のバッチ」へ畳み、
# worker スロット占有と GitLab ポーリングの二重負荷を同時に消す。gitlab は承認時にローカル
# workspace を finalize する必要がない（成果はマージ済み MR にある）ため、service_waits が
# worker/clone 無しで終端 result を材料化できるのが成立の鍵。
def _executor_module(args):
    """executor プラグインのモジュールを返す（組み込み agent/stub や未解決は None）。
    service_waits が poll()/on_cancel() フックを取り出すために使う。"""
    spec = getattr(args, "executor", None) or "agent"
    if spec in BUILTIN_EXECUTORS:
        return None
    path = _resolve_executor_plugin(spec)
    if not path:
        return None
    try:
        return _load_executor_module(path)
    except RuntimeError:
        return None


def executor_hook(args, name: str):
    """executor プラグインの任意フック（poll / on_cancel）を返す。無ければ None。
    これらは execute() と同じくプラグイン側にあり executor 非依存の本体からは任意。"""
    mod = _executor_module(args)
    fn = getattr(mod, name, None) if mod else None
    return fn if callable(fn) else None


def _executor_cfg(args) -> dict:
    """executor 名と同名の設定ブロック（例 gitlab:）を dict で返す。max_open_issues /
    watch_interval など park & poll のパラメータをここから読む。無ければ空 dict。"""
    spec = getattr(args, "executor", None) or "agent"
    cfg = getattr(args, spec, None)
    return cfg if isinstance(cfg, dict) else {}


def _executor_cfg_from_env() -> dict:
    """worker プロセス内では設定は環境変数 KIRO_FLOW_EXECUTOR_CONFIG(JSON) で届く。
    cmd_work の throttle 判定（max_open_issues）用にそれを読む。無ければ空 dict。"""
    raw = os.environ.get("KIRO_FLOW_EXECUTOR_CONFIG")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _defer_enabled(args) -> bool:
    """park & poll（deferral）を有効にするか。executor 設定 defer_waits（既定 true）で決まる。
    false なら従来モード（worker がイシューを監視してブロック待機）へ戻す。daemon/run が
    この判定で worker への環境変数 KIRO_FLOW_DEFER_WAITS を出し分け、service_waits も出番が無くなる。"""
    return bool(_executor_cfg(args).get("defer_waits", True))


def _watch_interval(cfg: dict) -> float:
    """service_waits が park をバッチ再確認する間隔（秒）。既定 90。"""
    try:
        v = float(cfg.get("watch_interval", 90.0))
        return v if v > 0 else 90.0
    except (TypeError, ValueError):
        return 90.0


def _wait_lease_window(watch_interval: float) -> float:
    """park 記録の生存リース秒。健康な監視主体は watch_interval 毎に更新するので、その数倍を
    確保すれば一過性の遅延で誤って pending へ縮退させない。逆に監視が数回分止まれば失効し、
    node_state が pending へ落ちて full worker の token 再アタッチに引き継がれる（行き止まり回避）。"""
    return max(watch_interval * 3.0, 300.0)


def _wait_deadline(rec: dict):
    """park 記録から現在の締切（絶対エポック）を導く。人の作業を検知(active_seen)後は
    approved_timeout、未検知なら timeout。当該 timeout が 0 以下なら無限（None）。
    ブロック版 _wait_for_decision の猶予延長ロジックと同じ意味を service_waits 側で再現する。"""
    started = float(rec.get("started_at", 0) or 0)
    if rec.get("active_seen"):
        since = float(rec.get("active_since", started) or started)
        at = float(rec.get("approved_timeout", 0) or 0)
        return (since + at) if at > 0 else None
    to = float(rec.get("timeout", 0) or 0)
    return (started + to) if to > 0 else None


def build_wait_record(nid, who, kind, defer: dict, watch_interval: float) -> dict:
    """DeferDecision.defer と現在時刻から waits/<node>.json のレコードを組み立てる。
    started_at は「park を開始した時刻」＝ブロック版が time.time() から締切を測るのと同じ基準。"""
    now = time.time()
    pi = float(defer.get("poll_interval", 30.0) or 30.0)
    return {
        "id": nid, "who": who, "kind": kind,
        "executor": defer.get("executor", ""),
        "issue": defer.get("issue"),                 # throttled は None（イシュー未作成）
        "task_token": defer.get("task_token"),       # 秘密ではない（再アタッチ用の決定的トークン）
        "expected_target": defer.get("expected_target", ""),  # MR ターゲット検証（park を跨いで保つ）
        "throttled": bool(defer.get("throttled")),
        "reason": defer.get("reason", "wait"),
        "active_seen": bool(defer.get("active_seen")),
        "active_since": now if defer.get("active_seen") else None,
        "poll_interval": pi,
        "timeout": float(defer.get("timeout", 0.0) or 0.0),
        "approved_timeout": float(defer.get("approved_timeout", 0.0) or 0.0),
        "started_at": now,
        "next_poll_at": now + pi,
        "wait_lease_until": now + _wait_lease_window(watch_interval),
        "created_at": now_iso(),
    }


def park_node(bus: Bus, nid: str, who: str, rec: dict) -> None:
    """ノードを park（保留）する: waits 記録を先に書き、その後 claim を解放する。
    この順序が肝——先に解放すると crash 窓で wait を失う。書いてから解放すれば、途中で
    死んでも claim（lease）が残り、失効後に wait が governing する（wait を失わない）。"""
    bus.write_wait(nid, rec)
    bus.release_claim(nid, who)
    bus.event(who, "parked", node=nid, reason=rec.get("reason", "wait"))
    bus.sync_push(f"park {nid} by {who} ({rec.get('reason','wait')})")


def _finish_wait(v: Bus, rec: dict, status: str, text: str, data) -> None:
    """park の決着を終端 result として書き、wait 記録を消す（service_waits から）。"""
    nid = rec["id"]
    v.write_result(nid, rec.get("who", "service_waits"), status, text, data)
    v.clear_wait(nid)
    v.event("service_waits", "result", node=nid, status=status)
    v.sync_push(f"result {nid} [{status}] by service_waits")


def _service_one_wait(v: Bus, rec: dict, poll, watch_interval: float,
                      wait_lease: float, daemon_id: str) -> None:
    """park 済み（起票済み）ノードを 1 件 poll して決着/未決着を反映する。"""
    nid = rec["id"]
    # 締切超過（人が動かないまま timeout / approved_timeout）→ failed（消費者の永久待機を防ぐ）
    dl = _wait_deadline(rec)
    if dl is not None and time.time() >= dl:
        iid = (rec.get("issue") or {}).get("iid")
        phase = "MR の決着" if rec.get("active_seen") else "レビュー/MR 作成"
        _finish_wait(v, rec, "failed",
                     f"[gitlab] park タイムアウト: イシュー #{iid} が期限内に {phase} に至らず",
                     {"decision": "rejected", "reason": "park-timeout", "issue_iid": iid})
        log(daemon_id, f"park タイムアウト: {nid}（#{iid}）→ failed")
        return
    try:
        r = poll({"issue": rec.get("issue"), "active_seen": rec.get("active_seen", False),
                  "expected_target": rec.get("expected_target", "")})
    except Exception as e:  # noqa: BLE001 — poll 失敗は run を止めない。lease を更新して次回再試行
        log(daemon_id, f"service_waits poll 失敗（無視して次回再試行）: {nid}: {e}")
        rec["next_poll_at"] = time.time() + max(watch_interval, float(rec.get("poll_interval", 30) or 30))
        rec["wait_lease_until"] = time.time() + wait_lease
        v.write_wait(nid, rec)
        return
    decision = (r or {}).get("decision")
    if decision == "approved":
        _finish_wait(v, rec, "done", (r or {}).get("text", ""), (r or {}).get("data"))
        log(daemon_id, f"park 決着（承認）: {nid} → done")
        return
    if decision == "rejected":
        _finish_wait(v, rec, "failed", (r or {}).get("text", ""), (r or {}).get("data"))
        log(daemon_id, f"park 決着（却下）: {nid} → failed")
        return
    # 未決着 → active_seen/締切/次回時刻/lease を更新して据え置く
    active_now = bool((r or {}).get("active_seen"))
    if active_now and not rec.get("active_seen"):
        rec["active_since"] = time.time()
        log(daemon_id, f"park: {nid} 人の作業を検知（猶予を approved_timeout へ延長）")
    rec["active_seen"] = rec.get("active_seen") or active_now
    rec["next_poll_at"] = time.time() + max(watch_interval, float(rec.get("poll_interval", 30) or 30))
    rec["wait_lease_until"] = time.time() + wait_lease
    v.write_wait(nid, rec)


def _service_throttled(v: Bus, rec: dict, cap: int, wait_lease: float, daemon_id: str) -> None:
    """throttled park（同時イシュー上限で起票を見送ったノード）を面倒見る。枠が空いたら解除
    （clear_wait → node は pending に戻り worker が通常起票）。まだ満杯なら lease を延ばして
    pending への無用な flap を防ぐ。エラーにはしない＝バックプレッシャで発行がペーシングされるだけ。"""
    nid = rec["id"]
    if cap <= 0 or v.open_wait_count() < cap:
        v.clear_wait(nid)
        v.sync_push(f"throttle release {nid}")
        log(daemon_id, f"throttle 解除: {nid}（同時イシューの枠が空いた）")
        return
    rec["wait_lease_until"] = time.time() + wait_lease
    v.write_wait(nid, rec)


def service_waits(bus: Bus, args, only_runs: "list | None" = None,
                  daemon_id: str = "service_waits") -> int:
    """監視主体（daemon/run）が park 済みノードをバッチ再確認する単一ポーラ。処理した run 数を返す。
    起動モード非依存（daemon でも cmd_run でも同じこれを回す）。executor が poll() を持たない
    （kiro/stub）なら何もしない＝park & poll は deferring executor（gitlab）だけで働き、他は不変。

    分散（git バス）で監視を**公平に分担**するため、`only_runs` に「この監視主体が担当する run」を
    渡す（daemon は自分が orchestrator を駆動している run、cmd_run は自分の run 1 件）。渡すと
    その run だけを監視する＝**1 run の park は駆動オーナー 1 台だけがポーリング**し、N 台が全 park を
    重複ポーリングするのを防ぐ。run 自体は request-claim で各 PC に分散するため監視も自然に分散する。
    オーナーが消えても孤児 reclaim が run（＝監視）を別 PC へ移すのでクラッシュ耐性はそのまま。
    None（担当未指定）のときは全 active run を見る（単一 PC / 後方互換）。"""
    if not _defer_enabled(args):
        return 0                       # 従来モード（deferral 無効）＝park は無いので監視も不要
    poll = executor_hook(args, "poll")
    if poll is None:
        return 0
    cfg = _executor_cfg(args)
    # poll() は自プロセス内で走るので、executor 設定（起票先/接続ラベル等）を環境変数で届ける
    # （daemon/run は make_executor を経由しないため、ここで明示的に渡す）。
    if cfg:
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(cfg, ensure_ascii=False)
    watch_interval = _watch_interval(cfg)
    wait_lease = _wait_lease_window(watch_interval)
    cap = int(cfg.get("max_open_issues", 0) or 0)
    now = time.time()
    run_ids = list(only_runs) if only_runs is not None else bus.active_runs()
    serviced = 0
    for rid in run_ids:
        v = bus.run_view(rid)
        waits = v.list_waits()
        if not waits:
            continue
        serviced += 1
        for rec in waits:
            nid = rec.get("id")
            if not nid or v.has_result(nid):
                v.clear_wait(nid)                    # 別経路で決着済み → 記録を掃除
                continue
            if rec.get("throttled"):
                _service_throttled(v, rec, cap, wait_lease, daemon_id)
                continue
            if float(rec.get("next_poll_at", 0) or 0) > now:
                continue                             # まだ再確認時刻でない（per-issue バックオフ）
            _service_one_wait(v, rec, poll, watch_interval, wait_lease, daemon_id)
    return serviced


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


def human_feedback_from_results(results: dict, limit: int = 1200) -> str:
    """ノード結果の**構造化 data から人フィードバック**（`guidance` / `notes[].body`）を集める。
    executor 非依存: gitlab に限らず、委譲系 executor が結果コントラクトに載せた人の指摘を汎用に読む
    （`decision` の有無や executor 名で分岐しない）。評価役（replan）へ「人の指摘」として渡し、
    待機ノードの付け替え・ノード追加を人フィードバック駆動で決めさせるための材料。"""
    out: list[str] = []
    for nid, r in (results or {}).items():
        d = (r or {}).get("data")
        if not isinstance(d, dict):
            continue
        g = str(d.get("guidance") or "").strip()
        if g:
            out.append(f"[{nid}] {g}")
        for note in d.get("notes") or []:
            if isinstance(note, dict):
                b = str(note.get("body") or "").strip()
                if b:
                    out.append(f"[{nid}] {b}")
    return "\n".join(out)[:limit]


_INFLIGHT_FB_MARK = "\n\n[人からの指摘・反映すること]"


def _inflight_amend_pending(bus, graph, who, args, consumed_fb: set) -> int:
    """静止を待たず、settled ノードに新しく載った人フィードバック（`data.guidance`/`notes`・差し戻し含む）を
    **待機（pending）ノードの spec に即時反映**する。実行中(claimed)・監視中(waiting)・終端ノードは触らない
    ＝作業中は不変（安全）。決定的（LLM 不要）・冪等（同一発生源の指摘は二度入れない）。反映した待機ノード数を返す。
    executor 非依存: guidance/notes を汎用に読む（gitlab 固有の分岐は無い）。**ノード追加**は二重生成を避けるため
    静止時の評価役（continue_*）に委ね、本関数は既存待機ノードの書き換えに限定する。"""
    nodes = graph["nodes"]
    new_pieces = []
    for nid in list(nodes.keys()):
        d = (bus.read_result(nid) or {}).get("data")
        if not isinstance(d, dict):
            continue
        pieces = [str(d.get("guidance") or "").strip()] if str(d.get("guidance") or "").strip() else []
        for note in d.get("notes") or []:
            if isinstance(note, dict) and str(note.get("body") or "").strip():
                pieces.append(str(note["body"]).strip())
        if not pieces:
            continue
        text = " / ".join(pieces)
        k = f"{nid}:{len(text)}"                        # 発生源ノード＋長さで冪等キー
        if k in consumed_fb:
            continue
        consumed_fb.add(k)
        new_pieces.append(text)
    if not new_pieces:
        return 0
    inject = _INFLIGHT_FB_MARK + "\n" + "\n".join(f"- {p}" for p in new_pieces)
    amended = 0
    for nid, entry in list(nodes.items()):
        if bus.node_state(nid) != "pending":           # 待機ノードのみ（実行中/監視中/終端は不変）
            continue
        entry["goal"] = str(entry.get("goal") or "") + inject
        spec = {"id": nid, "goal": entry["goal"], "deps": entry.get("deps", []),
                "kind": entry.get("kind", "work")}
        if entry.get("retries"):
            spec["retries"] = entry["retries"]
        bus.write_task(spec)                           # 待機ノードの spec を書き換え（claim 前なので安全）
        amended += 1
    if amended:
        bus.write_graph(graph)
        bus.event(who, "inflight_amend", nodes=amended)
        bus.sync_push(f"in-flight 反映 run {args.run_id}: 待機 {amended} ノードへ人指摘")
        log(who, f"in-flight: 待機 {amended} ノードへ人の指摘を反映（実行中は不変）")
    return amended


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
    # 人フィードバック（委譲 executor の guidance/notes・差し戻し含む）を評価役へ明示する。
    # これにより replan を「人の指摘駆動」で決められる（待機ノードの付け替え／ノード追加）。
    hf = human_feedback_from_results(results)
    hf_block = (f"\n\n人からの指摘（最優先で反映すること。executor 非依存の結果コントラクト由来）:\n{hf}"
                if hf else "")
    # flow-worker スキルがあれば評価規律入りプロンプトを使う（無ければ従来の組み込み）。
    # decision JSON の出力契約はスキル側でも同一に保たれている。
    prompt = _flow_worker_prompt({
        "role": "evaluator", "request": request, "results_summary": summary,
        "human_feedback": hf, "patterns_catalog": catalog,
        "max_retries": max_retries, "iteration": iteration,
    })
    if not prompt:
        prompt = (
        "あなたは分散 Dynamic Workflow の評価役です。7 パターンを踏まえ、現在の結果が要求を満たすか判定し、"
        "必要なら次のタスクを追加してください（例: 分類結果に応じた専門タスク、検証 fail の作り直し、"
        "統合や追加候補の生成）。**人からの指摘があれば最優先で反映**し、必要なら新タスク追加や、"
        "まだ着手されていない**待機ノードの差し替え（replaces で置換）**で対応してください"
        "（実行中のノードは触らない＝評価は run が静止したときだけ行われます）。\n"
        f"ただし同じ完了条件のために作り直しを繰り返しても改善しない場合（達成不可能な条件など）は、"
        f"同一タスクの作り直しは最大 {max_retries} 回までとし、それを超えるなら無理に再タスクを足さず "
        '"done" を返してください。\n'
        f"パターン:\n{catalog}\n\n"
        "出力は JSON のみ: "
        '{"decision":"done"|"replan","reason":"...",'
        '"new_tasks":[{"id":"...","goal":"...","deps":[],"kind":"work","replaces":"<任意: 差し替える待機ノード id>"}]}\n'
        "既存 id と重複しない id を使うこと。done のとき new_tasks は空配列。\n\n"
        f"元の要求: {request}{hf_block}\n\n現在の結果:\n{summary}"
    )
    try:
        data = extract_json(run_kiro(prompt, None, purpose="evaluator"))
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
    if args.planner == "agent":
        return plan_strategy_kiro(args.request, args.model, review, gran)
    return plan_strategy_stub(args.request, review, gran)


def _env_failure_reason(results: dict) -> "str | None":
    """失敗結果に環境要因（quota/auth/env）のトリアージタグがあれば、その説明を返す。

    環境が壊れているとき（認証切れ・利用上限・CLI 不在）は、どのノードをリトライしても
    同じ理由で落ちる。実際 codex の利用上限で全ノードが 1 つずつリトライを焼き尽くし、
    26 ノード × max_retries 回の無駄な LLM 起動と「理由不明の全滅」が起きた。
    タスクの内容の問題（タグ無し）とは区別し、run を即座に失敗で終端して人に環境を直させる
    （直後の resume-run / kiro-project の自動再開で done は温存されたまま続きから走る）。"""
    for nid, r in results.items():
        if r.get("status") != "failed":
            continue
        m = _AGENT_ERROR_TAG_RE.search(str(r.get("output", "")))
        if m and m.group(1) in AGENT_ERROR_ENV_CLASSES:
            hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == m.group(1)), "")
            return (f"[agent-error:{m.group(1)}] 環境要因の失敗（{nid}）: {hint} "
                    "リトライを打ち切りました。環境を直してから再開してください"
                    "（完了済みの工程は温存されます）。")
    return None


def _continue(args, request, nodes, results, iteration, strategy=None):
    # 失敗トリアージ: 環境要因（quota/auth/env）の失敗が 1 つでもあれば再計画せず打ち切る。
    # planner（stub/kiro）に依らず先に判定する（LLM 評価も同じ環境で失敗するため）。
    env_fail = _env_failure_reason(results)
    if env_fail:
        return "failed", [], env_fail
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
    if t.get("retries"):  # サーキットブレーカー用の作り直し回数（>0 のときだけ保持）
        e["retries"] = int(t["retries"])
    return e


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


def _finalize_run(bus, args, iteration: int, failure: "str | None" = None) -> None:
    """全ノードの結果を集約して final.json を書き出し、run を終端して push・ログ出力する。
    failure（環境要因の打ち切り等）が渡されたら done でなく failed で終端し、理由を
    meta.failure_reason に残す（トリアージタグ付き → kiro-project / viewer が同じ判定を読む）。"""
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
        **({"failure_reason": failure} if failure else {}),
    })
    if failure:
        bus.mark_run_failed(args.run_id, failure)
        log(args.node_id, f"打ち切り（iteration={iteration}）: {failure}")
    else:
        bus.set_status("done")
        log(args.node_id, f"完了（iteration={iteration}）。final.json を書き出しました。")
    bus.sync_push(f"finalize run {args.run_id}")
    log(args.node_id, "結果サマリ:\n" + summary)


def _orch_check_canceled(bus: Bus, args, who: str) -> bool:
    """cancel マーカーがあれば run を canceled に終端化して True を返す（orchestrator の停止用）。
    orchestrator が set_status("running") で canceled を上書きし返すのを防ぐため、ループの要所で確認する。"""
    if not bus.is_canceled_requested(args.run_id):
        return False
    reason = bus.cancel_info(args.run_id).get("reason") or "cancel 指示"
    bus.mark_canceled(args.run_id, reason)
    bus.event(who, "canceled", run=args.run_id, reason=reason)
    bus.sync_push(f"cancel run {args.run_id}: {reason}")
    log(who, f"cancel 指示を検知（{reason}）。orchestrator を終了します。")
    return True


def cmd_orchestrate(args) -> int:
    who = args.node_id
    bus = make_bus(args, who)
    bus.sync_pull()
    # リトライ: 先行 run（--inherit-from）から確定済みノードを引き継ぎ、先行 run を掃除する。
    # ensure_run より前に行う＝seed した meta を ensure_run が上書きしないようにする。
    inh = getattr(args, "inherit_from", None)
    if inh and read_json(bus.meta_path) is None:
        info = bus.inherit_from(inh, getattr(args, "orphan_grace", 0.0) or 0.0)
        log(who, f"先行 run {inh} を処理: {info['reason']}"
                 f"（引き継ぎ {info['seeded_nodes']} ノード・削除={info['deleted']}）")
        bus.sync_push(f"inherit {inh} -> {args.run_id}: {info['reason']}")
    bus.ensure_run(args.request, parse_workspace(getattr(args, "workspace", None)),
                   parse_references(getattr(args, "references", None)))
    bus.note_executor(getattr(args, "executor", None) or "agent")   # viewer の表示切替用
    # 生存リース（heartbeat）は orchestrator 自身が張る。daemon 経由の run だけが lease を持つと、
    # kiro-flow run で都度起動される run（kiro-project の主経路）には lease が永久に書かれず、
    # 消費者側の「停滞 run か？」判定（run_is_orphaned / _run_resumable）が lease の不在を
    # 「生きている」とも「死んでいる」とも決められない。orchestrator が消えた run は永久に
    # status=running のまま残り、失敗ノードも pending ノードも二度と実行されなくなる。
    lease_window = _run_lease_window(args)
    _last_touch = [0.0]

    def heartbeat(force: bool = False) -> None:
        """「この run は駆動中」を meta に刻む。

        git バスでは meta の書き換えを未コミットのまま残せない: sync_pull は pull --rebase なので
        dirty な作業ツリーでは失敗し続け、他ノードの結果を永久に取り込めなくなる（静止判定に
        到達せず run が止まる）。更新したぶんは必ず sync_push で確定させる。push は転送を伴う
        ので、毎 poll ではなくリースの 1/3 ごとに間引く（ローカルバスでは sync_push は no-op）。"""
        now = time.time()
        if not force and now - _last_touch[0] < lease_window / 3.0:
            return
        _last_touch[0] = now
        bus.touch_run(args.run_id, lease_window)
        bus.sync_push(f"heartbeat run {args.run_id}")

    heartbeat(force=True)      # 計画（LLM）は数十秒かかる。その前に張る。
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
    consumed_fb: set = set()   # in-flight 反映済みの人フィードバック発生源（同一 settlement を二度反映しない）
    while True:
        if _orch_check_canceled(bus, args, who):
            return 0
        heartbeat()               # 評価・再計画は長い（LLM）ので周回ごとに更新
        graph = bus.read_graph()
        while not _quiesced(bus, graph["nodes"]):
            bus.sync_pull()
            heartbeat()          # 走っている限りリースを延ばす
            if _orch_check_canceled(bus, args, who):
                return 0
            graph = bus.read_graph()
            # in-flight 差し戻し: 静止を待たず、人の指摘を待機ノードへ即時反映（実行中は不変）。
            # ノード追加は静止時の評価役に委ねる（二重生成回避）。
            _inflight_amend_pending(bus, graph, who, args, consumed_fb)
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

    # 全ノード結果を集約 → final.json 書き出し → 終端（done / 環境要因なら failed）・push
    _finalize_run(bus, args, iteration,
                  failure=(reason if decision == "failed" else None))
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
    """run が静止したか: 実行中(claimed)も、park 待機中(waiting)も、今すぐ claim 可能な
    pending も無い状態。依存が失敗してブロックされた pending は静止扱い（継続判断で付け替えられる）。
    waiting（承認待ち等で park 済み）は in-flight 扱い＝静止させない。これにより orchestrator は
    park 中のノードを見て早まって再計画/完了せず、service_waits が決着を書くまで待つ。"""
    for nid, node in nodes.items():
        st = bus.node_state(nid)
        if st in ("claimed", "waiting"):
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
    # executor を一度だけ解決する（組み込み agent/stub or プラグイン）。
    execute = make_executor(args)
    # park & poll: 親（daemon/run）が service_waits で面倒を見るときだけ deferral を有効化する。
    # 無効時（standalone work 等）は executor が従来どおりブロック待機へフォールバックする。
    defer_enabled = os.environ.get("KIRO_FLOW_DEFER_WAITS") == "1"
    ecfg = _executor_cfg_from_env()
    issue_cap = int(ecfg.get("max_open_issues", 0) or 0)   # 同時イシュー上限（0=無制限）
    watch_interval = _watch_interval(ecfg)
    # 親（run/daemon）からの SIGTERM でもワークスペースの clone を消してから抜ける
    signal.signal(signal.SIGTERM, lambda *_: (cleanup_workspace(), sys.exit(143)))
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

        # throttle（バックプレッシャ）: 同時未決着イシューが上限に達していたら、起票せず
        # throttled park して claim を解放する。エラーにはしない＝人のレビュー速度に発行を
        # ペーシングするだけ（枠が空けば service_waits が解除 → 通常起票）。deferring executor
        # かつ max_open_issues>0 のときだけ働く（kiro/stub 等は waits が空なので発火しない）。
        if defer_enabled and issue_cap > 0 and bus.open_wait_count() >= issue_cap:
            rec = build_wait_record(nid, who, kind,
                                    {"executor": args.executor, "issue": None,
                                     "task_token": None, "throttled": True,
                                     "reason": "throttled:max_open_issues"}, watch_interval)
            park_node(bus, nid, who, rec)
            log(who, f"throttle: 同時イシュー上限({issue_cap})到達 → {nid} を park（起票見送り）")
            time.sleep(random.uniform(0, 0.3))
            continue

        # 依存の成果は構造化データ込みの完全な result dict で渡す
        dep_results = _collect_dep_results(bus, node, kind)
        # run の元要求（全体文脈）。対応 executor（agent の flow-worker プロンプト等）へ渡す。
        run_request = str((read_json(bus.meta_path) or {}).get("request", ""))
        # 中間成果物プロトコル: 自ノードの出力先を用意し、依存ノードの成果物パスを集める。
        # これにより大きな成果物は output/data に貼らずファイル参照で受け渡せる。
        art_dir = bus.ensure_artifact_dir(nid)
        dep_arts = {d: bus.node_artifact_dir(d) for d in node.get("deps", [])}
        # ワークスペース（この run の唯一の書込先）を temp 領域へ clone し、作業ブランチ kf/<run_id>
        # を base から作ってエージェントへ渡す（書込先が無ければ読み取り専用 run）。
        goal = node["goal"]
        ws = ensure_workspace_clone(bus.run_workspace(), args.run_id)
        # 作業指示は goal に結合せず別引数で渡す（goal を汚さない）。対応 executor は本来の goal を
        # そのまま使い（gitlab はタイトル/目的に出す）、ワークスペース指示・spec は別枠で扱う。
        # 参照リポジトリ（読むだけ）は run メタから取り、ワークスペース指示に続けてエージェントへ伝える。
        references = bus.run_references()
        ref_note = reference_instruction(references)
        instruction = "\n".join(s for s in (workspace_instruction(ws) if ws else "", ref_note) if s)
        # 実行中は心拍で lease を延長し続け、長時間タスクでも再 claim されないようにする
        hb = Heartbeat(bus, nid, who, args.lease)
        hb.start()
        rdata = None
        delivery = None
        try:
            output, rdata = call_executor(execute, kind, goal, dep_results, args.model,
                                          art_dir, dep_arts, instruction, workspace=ws,
                                          references=references, request=run_request)
            # エージェントが編集したらワークスペースの作業ブランチへ commit して push する
            # （変更が無ければ何もしない＝調査タスク等ではブランチを作らない）。
            delivery = finalize_workspace(ws, args.run_id, nid)
            rstatus = "done"
        except Exception as e:  # noqa: BLE001 — 結果として記録する
            # park シグナル（DeferDecision.defer）: 承認待ち等で未決着＝終端 result を書かず、
            # 心拍を止めてから wait を書き claim を解放する（この順序で claim の書き戻し競合を防ぐ）。
            # スロットを空けて次の claim 可能タスクへ回り、決着は service_waits が書く。
            defer = getattr(e, "defer", None)
            if isinstance(defer, dict):
                hb.stop()
                rec = build_wait_record(nid, who, kind, defer, watch_interval)
                park_node(bus, nid, who, rec)
                log(who, f"park: {nid}（{defer.get('reason', 'wait')}）— claim 解放しスロットを空ける")
                if ws:
                    cleanup_workspace()   # park 中は clone を持たない（ディスク解放）
                time.sleep(random.uniform(0, 0.3))
                continue
            output = f"実行エラー: {e}"
            rstatus = "failed"
            # executor が例外に載せた構造化データ（gitlab 却下の issue_iid / guidance 等）は
            # 承認と対称に failed result の data として残す（消費側の文字列マッチ依存を無くす）
            edata = getattr(e, "data", None)
            if isinstance(edata, dict):
                rdata = edata
        finally:
            hb.stop()

        # 生成された中間成果物を run_dir 相対パスで記録（後続・status から発見できる）
        artifacts = [os.path.relpath(p, bus.run_dir) for p in bus.list_artifacts(nid)]
        if delivery:  # ワークスペースへ push したブランチ/コミットを result に残す（消費側が追跡）
            rdata = {**(rdata if isinstance(rdata, dict) else {}), "delivery": delivery}
        bus.write_result(nid, who, rstatus, output, rdata, artifacts=artifacts)
        bus.event(who, "result", node=nid, status=rstatus)
        bus.sync_push(f"result {nid} [{rstatus}] by {who}")
        log(who, f"完了: {nid} [{rstatus}]")
        if getattr(args, "cleanup_per_node", False) and ws:
            cleanup_workspace()  # ノード完了/失敗ごとに clone を即削除（長命 worker のディスク抑制）
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
    ac = getattr(args, "agent_cli", None)
    if ac:
        base += ["--agent-cli", str(ac)]  # LLM 実行 CLI（kiro/claude）を子へ引き継ぐ
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


def _resume_run(bus: Bus, daemon_id: str, args, base: list, req_id: str, req: dict,
                lease_window: float, spawn=None):
    """孤児 run の orchestrator を同じ run-id で再起動する（cmd_orchestrate の resume）。
    確定済みの results/ はバスに残っているため、未完了ノードだけが続きから実行される。
    進捗なしの連続再開が max_resumes を超えたら None を返す（呼び出し側が failed に確定）。"""
    n = bus.record_resume(req_id)
    max_r = int(getattr(args, "max_resumes", 3) or 0)
    if n > max_r:
        return None
    p = (spawn or _spawn_orchestrator)(base, args, req_id, req)
    bus.touch_run(req_id, lease_window)   # 引き継ぎ直後に生存リースを張る（孤児の再判定を防ぐ）
    bus.run_view(req_id).event(daemon_id, "run-resumed", run=req_id, resume=n)
    bus.sync_push(f"run {req_id} resumed（孤児を引き継ぎ #{n}）")
    return p


def _superseded_run_ids(bus: Bus) -> dict:
    """inbox 要求の inherit_from から「新世代のリトライに引き継がれた先行 run」の
    {先行 run_id: 新世代 req_id} を作る。kiro-project はリトライ時に先行 run を明示 cancel せず、
    inherit_from 付きで次世代を投入する（inherit_from は実行中の先行 run を安全のため殺さない）。
    そのため旧世代の run が非終端のまま inbox に残る。この集合の run は世代交代で役目を終えた旧
    リトライ＝daemon 再起動時の一斉 adopt で復活させてはいけない。"""
    superseded: dict = {}
    for req_id in bus.list_inbox():
        rec = bus.read_inbox(req_id)
        prev = rec.get("inherit_from") if rec else None
        if prev and prev != req_id:
            superseded[prev] = req_id
    return superseded


def _run_fully_parked(bus: Bus, run_id: str) -> bool:
    """run の in-flight が全て park（承認待ち等）か。claim 中のノードも今すぐ claim 可能な
    pending も無く、生存 park が 1 つ以上ある run は worker も計画エージェントも使わない＝
    実行枠（max_runs）に数えない（gitlab 長期委譲が枠を占有して新規 run が詰まらないように）。"""
    v = bus.run_view(run_id)
    graph = v.read_graph()
    if not graph:
        return False                     # グラフ未作成（計画中）は実行中扱い
    parked = False
    for nid, node in graph["nodes"].items():
        st = v.node_state(nid)
        if st == "claimed" or (st == "pending" and deps_satisfied(v, node)):
            return False
        if st == "waiting":
            parked = True
    return parked


def _busy_run_count(bus: Bus, run_ids) -> int:
    """実行枠（max_runs）を消費している run 数（駆動中のうち全 park の run を除く）。"""
    return sum(1 for r in run_ids if not _run_fully_parked(bus, r))


def _adopt_orphan_runs(bus: Bus, daemon_id: str, owned: set, lease_window: float,
                       args, base: list, spawn=None,
                       slots: "int | None" = None) -> "tuple[dict, list]":
    """inbox 由来で owning daemon が消失した（生存リース切れ）非終端 run を引き継ぐ。

    PC の毎日シャットダウン等で daemon ごと消えても run を失敗にしない中核。孤児を
    見つけたら reclaim（1 台に決める）→ orchestrator を同じ run-id で再起動（resume）し、
    途中まで確定した results/ を活かして続きから回す。再開できないもの——自動再開が
    無効（max_resumes<=0）・要求ファイル欠損・進捗なしの連続再開が上限超過——だけを
    従来どおり failed に確定し、result を待つ消費者（kiro-project の submit 等）の
    永久待機を防ぐ。`owned` は自分が今回している run（誤引き継ぎしない）。

    ただし新世代のリトライに inherit_from で引き継がれた先行 run（世代交代で消えるべき旧
    リトライ）は再開しない。素朴に全孤児を再開すると再起動時に旧世代が一斉に復活して二重実行
    になるため、これらは終端化して next-gen の inherit_from が確定済みノードを引き継いでから
    掃除できるようにする（作業は失わない）。
    戻り値は（再開した run_id→Popen, 終端化した run_id 一覧）。"""
    adopted: dict = {}
    failed: "list[str]" = []
    used = 0                     # 実行枠（slots）を消費した引き継ぎ数（全 park の run は数えない）
    max_r = int(getattr(args, "max_resumes", 3) or 0)
    superseded = _superseded_run_ids(bus)
    for req_id in bus.list_inbox():
        if req_id in owned or not bus.run_exists(req_id):
            continue
        if not bus.run_is_orphaned(req_id, lease_window):
            continue
        if req_id in superseded:
            # 新世代のリトライに引き継がれた旧 run。孤児化しているが再開すると世代交代で消える
            # べき旧リトライが復活して二重実行になる。再開せず終端化する（next-gen の
            # inherit_from が確定済みノードを引き継いでから掃除する＝作業は失わない）。
            if bus.mark_run_superseded(req_id, superseded[req_id]):
                bus.run_view(req_id).event(daemon_id, "run-superseded", run=req_id,
                                           by=superseded[req_id])
                bus.sync_push(f"run {req_id} superseded（新世代 {superseded[req_id]} に引き継ぎ）")
                failed.append(req_id)
                log(daemon_id, f"孤児 run を終端化: {req_id} → superseded"
                               f"（新世代 {superseded[req_id]} に引き継ぎ・再開しない）")
            continue
        req = bus.read_inbox(req_id)
        why = "自動再開が無効（max_resumes<=0）" if max_r <= 0 else "要求ファイルを読めない"
        if req and max_r > 0:
            # 実行枠（max_runs 由来の slots）: 全 park の run は枠を要さないため無条件に引き継ぐ
            # （service_waits の監視オーナーが必要）。それ以外は枠が無ければ今回は再開せず
            # 次 poll へ持ち越す（failed にはしない＝再起動直後の一斉再開でプロセスが溢れない）。
            parked = slots is not None and _run_fully_parked(bus, req_id)
            if slots is not None and not parked and used >= slots:
                continue
            if not bus.reclaim_request(req_id, daemon_id, args.lease):
                continue      # 旧 owner の claim がまだ lease 内 → 失効後の poll で再試行
            p = _resume_run(bus, daemon_id, args, base, req_id, req, lease_window, spawn)
            if p is not None:
                adopted[req_id] = p
                if slots is not None and not parked:
                    used += 1
                continue
            why = f"進捗なしの連続再開が上限超過（max_resumes={max_r}）"
        if bus.mark_run_failed(req_id, f"orphaned: owning daemon が消失（生存リース切れ・{why}）"):
            bus.run_view(req_id).event(daemon_id, "run-orphaned", run=req_id)
            bus.sync_push(f"run {req_id} failed: orphaned（生存リース切れ・{why}）")
            failed.append(req_id)
    return adopted, failed


def _spawn_orchestrator(base: list, args, req_id: str, req: dict):
    """要求 req を担当する orchestrator を base argv から起動する（daemon のオンデマンド起動）。"""
    ws = req.get("workspace")   # 要求に紐づく唯一の書込先ワークスペースを run meta へ載せる
    ws_args = ["--workspace", json.dumps(ws, ensure_ascii=False)] if ws else []
    for r in (req.get("references") or []):   # 参照リポジトリも run meta へ伝搬する
        ws_args += ["--reference", json.dumps(r, ensure_ascii=False)]
    inh = req.get("inherit_from")             # リトライ: 先行 run の引き継ぎ元を orchestrate へ
    return subprocess.Popen(base + ws_args + [
        "--granularity", str(getattr(args, "granularity", "finest") or "finest"),
        *(["--exemplar-first"] if getattr(args, "exemplar_first", False) else []),
        "--run-id", req_id, "orchestrate", "--request", req["request"],
        # --inherit-from は orchestrate サブコマンドの引数（グローバルではない）。
        # サブコマンド名より前に置くと親 parser に拾われ usage エラーで即死するため、
        # 必ず "orchestrate" の後ろに付ける（cmd_run の起動と同じ並び）。
        *(["--inherit-from", inh] if inh else []),
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
    # park & poll: daemon は service_waits で park を面倒見るので worker の deferral を有効化する
    # （承認待ちで worker スロットをブロックさせず、承認待ちは waits/ へ退避させる）。
    # 設定 defer_waits=false のときは有効化せず、従来モード（worker がブロック待機）に戻す。
    if _defer_enabled(args):
        env["KIRO_FLOW_DEFER_WAITS"] = "1"
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
        status = meta.get("status")
        # 停滞した run（orchestrator が消えて非終端のまま止まったもの）も、失敗 run と同じく
        # 「失敗ノードを戻して続きから」やり直す。
        # status だけを見ると救えない: orchestrator が落ちる（停止・クラッシュ・マシン再起動）と
        # run は status=running のままリースだけが切れて残り、失敗ノードも pending ノードも誰も
        # 進めない。再開しても failed の results が終端として残るので、その工程は永久に再実行
        # されない。生存リースで実態を見て、止まっているなら失敗ノードを pending へ戻す。
        stalled = probe.run_is_orphaned(args.run_id,
                                        float(getattr(args, "orphan_grace", 0.0) or 0.0))
        if status == "failed" or stalled:
            reset = probe.retry_failed()
            why = "失敗" if status == "failed" else "停滞（orchestrator 消失）"
            probe.sync_push(f"retry {'failed' if status == 'failed' else 'stalled'} run "
                            f"{args.run_id}: reset {len(reset)} failed node(s)")
            print(f">>> {why} run {args.run_id} を再実行します"
                  f"（失敗ノード {len(reset)} 件を pending へ戻し、done は温存）", flush=True)
        else:
            print(f">>> 既存 run {args.run_id} を再開します（status={status}）", flush=True)
    else:
        if not args.request:
            print("エラー: 新規実行には <要求> が必要です（再開なら既存の --run-id を指定）",
                  file=sys.stderr)
            return 2
        args.run_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    run_id = args.run_id

    bus_root = os.path.abspath(args.bus)
    # グローバル引数（バス・転送・run_id・ワークスペース・分解粒度）を子プロセスへ引き継ぐ
    base = _child_base(args, bus_root) + ["--run-id", run_id]
    if getattr(args, "workspace", None):
        base += ["--workspace", args.workspace]   # 唯一の書込先を orchestrator/worker へ伝搬
    for r in (getattr(args, "references", None) or []):
        base += ["--reference", r]                # 参照リポジトリを orchestrator/worker へ伝搬
    base += ["--granularity", str(getattr(args, "granularity", "finest") or "finest")]  # 分解粒度
    if getattr(args, "exemplar_first", False):
        base += ["--exemplar-first"]   # 見本先行分解を orchestrator へ伝搬
    mode = _mode_string(args, bus_root)

    procs = []
    orch = subprocess.Popen(base + [
        "orchestrate", "--request", args.request,
        *(["--inherit-from", args.inherit_from] if getattr(args, "inherit_from", None)
          and not resuming else []),   # 新規時のみ: 先行 run から引き継ぐ（再開時は不要）
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

    # park & poll: cmd_run も監視ループで service_waits を回すので worker の deferral を有効化する。
    # 設定 defer_waits=false のときは有効化せず従来モード（worker がブロック待機）に戻す。
    worker_env = os.environ.copy()
    if _defer_enabled(args):
        worker_env["KIRO_FLOW_DEFER_WAITS"] = "1"
    else:
        worker_env.pop("KIRO_FLOW_DEFER_WAITS", None)
    for i in range(args.workers):
        wid = f"worker-{i+1}"
        w = subprocess.Popen(base + [
            "work", "--node-id", wid, "--executor", args.executor,
            "--model_opt", args.model or "", "--poll", str(args.poll),
        ], env=worker_env)
        procs.append((wid, w))

    print(f"\n>>> kiro-flow run: run_id={run_id} bus={mode} ({'resume' if resuming else 'new'})")
    print(f">>> {state_git_status_line(args)}", flush=True)
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

    # park & poll: この run の park 済みノードを監視ループで面倒見る（daemon と同じ service_waits）。
    # deferring executor（gitlab 等）でなければ no-op。watch_interval 毎に間引いて再確認する。
    next_wait_service = 0.0
    watch_interval = _watch_interval(_executor_cfg(args))
    # run が終端に達するか orchestrator が落ちるまで待機
    try:
        while True:
            bus.sync_pull()
            state_sync(args)   # 状態 git: 進捗をリモートの viewer へ共有（間隔律速・ローカルバス時のみ）
            if time.time() >= next_wait_service:
                try:
                    service_waits(bus, args, only_runs=[run_id], daemon_id="run")
                except Exception as e:  # noqa: BLE001 — 監視失敗は run を止めない
                    print(f">>> service_waits でエラー（無視して継続）: {e}", flush=True)
                next_wait_service = time.time() + watch_interval
            if bus.is_canceled_requested(run_id) and bus.get_status() not in TERMINAL:
                # cancel 指示: この run を canceled に終端化し、park の再ポーリングを止め、
                # 子（orchestrator/worker）を停止する。--close-issues は cmd_cancel 側で実施済み。
                bus.mark_canceled(run_id, bus.cancel_info(run_id).get("reason") or "cancel 指示")
                bus.clear_waits_for_run(run_id)
                bus.sync_push(f"cancel run {run_id}")
                print(f"\n>>> run {run_id} は cancel されました。停止します。", flush=True)
                break
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
    state_sync(args, force=True)   # 状態 git: run の結末（results/final/meta）を間隔を待たず共有側へ
    final = read_json(bus.final_path)
    if final:
        print("\n=== 最終結果 ===")
        print(final.get("summary", ""))
    # run が failed で終端したら非 0 を返す（委譲先の却下など、上位＝kiro-project が
    # act 失敗として検知しリトライできるようにする）。done は 0。
    return 1 if bus.get_status() == "failed" else 0


# --------------------------------------------------------------------------
# submit — 要求を inbox に投入（デーモンが拾って orchestrator を起動する）
# --------------------------------------------------------------------------
def cmd_submit(args) -> int:
    req_id = args.run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000,9999)}"
    # ノード ID に pid を含め、並行 submit（kiro-project の一括 offload 等）が同じ
    # クローン作業ツリーを共有して index.lock を取り合う事故を避ける（クローンは
    # 終了時に削除され、SIGKILL 残骸も daemon の cleanup が回収する）。
    bus = make_bus(args, f"submitter-{os.getpid()}")
    bus.sync_pull()
    bus.submit_request(req_id, args.request, f"{socket.gethostname()}-{os.getpid()}",
                       workspace=parse_workspace(getattr(args, "workspace", None)),
                       references=parse_references(getattr(args, "references", None)),
                       inherit_from=getattr(args, "inherit_from", None))
    bus.sync_push(f"submit request {req_id}")
    print(req_id)  # run-id を標準出力（スクリプトから拾える）
    print(f">>> 要求を投入しました: {req_id}（デーモンが拾います）", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# cancel — run スコープの恒久停止（人の明示指示による緊急回避手段）
# --------------------------------------------------------------------------
def _apply_on_cancel(bus: Bus, args, run_id: str) -> None:
    """--close-issues 指定時に、run の park 済みイシューを executor の on_cancel フックで後始末する。
    フック非対応の executor では何もしない。ベストエフォート（失敗は無視）。"""
    on_cancel = executor_hook(args, "on_cancel")
    if on_cancel is None:
        return
    cfg = _executor_cfg(args)
    if cfg:
        os.environ["KIRO_FLOW_EXECUTOR_CONFIG"] = json.dumps(cfg, ensure_ascii=False)
    records = [r for r in bus.run_view(run_id).list_waits() if (r.get("issue") or {}).get("iid")]
    if not records:
        return
    try:
        on_cancel(records)
        log("cancel", f"run {run_id}: park 済みイシュー {len(records)} 件を後始末（close-issues）")
    except Exception as e:  # noqa: BLE001
        log("cancel", f"run {run_id}: on_cancel 後始末で例外（無視）: {e}")


def cmd_cancel(args) -> int:
    """run を canceled に終端化する（人の明示指示による唯一の hard-stop）。
    cancel マーカーを inbox に置いて全 PC / daemon へ伝え、run が存在すれば即 status=canceled を
    確定する（監視主体が居なくても止まる）。park 済みノードの再ポーリングを止め、--close-issues なら
    起票済みイシューも後始末する。既に終端した run には効かない（done/failed/canceled は不可逆）。"""
    bus = make_bus(args, f"cancel-{os.getpid()}")
    bus.sync_pull()
    rid = args.run_id
    if not bus.run_exists(rid) and rid not in bus.list_inbox():
        print(f"[kiro-flow] run {rid} が見つかりません（バス: {os.path.abspath(args.bus)}）",
              file=sys.stderr)
        return 2
    cur = bus.run_meta(rid).get("status")
    if cur in TERMINAL:
        print(f">>> run {rid} は既に終端（status={cur}）。cancel は不要です。")
        return 0
    reason = getattr(args, "reason", "") or "手動 cancel"
    bus.cancel_request(rid, socket.gethostname(), reason, bool(getattr(args, "close_issues", False)))
    # --close-issues は waits を消す前に実施する（イシュー座標は park 記録が握っているため）。
    if getattr(args, "close_issues", False):
        _apply_on_cancel(bus, args, rid)
    cleared = bus.clear_waits_for_run(rid)     # park 済みノードの再ポーリングを止める
    marked = bus.mark_canceled(rid, reason)    # run が存在すれば即終端化（監視主体が居なくても止まる）
    bus.sync_push(f"cancel run {rid}: {reason}")
    tail = "・status=canceled 確定" if marked else "（daemon が受理して終端化します）"
    print(f">>> run {rid} をキャンセルしました{tail}。park 解除 {cleared} 件、"
          f"理由: {reason}")
    if not marked and not bus.run_exists(rid):
        print(f">>> 注: 要求 {rid} はまだ run 化されていません。daemon が受理時に canceled で終端します。")
    return 0


# --------------------------------------------------------------------------
# daemon — 常駐し、要求に応じて orchestrator/worker をオンデマンド起動
# --------------------------------------------------------------------------
def daemon_lock_dir(lock_dir: "str | None" = None) -> str:
    """daemon ロックを置く共有ディレクトリ。
    起動側とプローブ側（kiro-project 等）で必ず一致させる必要があるため、
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

    max_runs = int(getattr(args, "max_runs", 0) or 0)
    log(daemon_id, f"daemon 起動 bus={mode} max_workers={args.max_workers} "
                   f"max_runs={max_runs if max_runs > 0 else '無制限'} poll={args.poll}")
    log(daemon_id, state_git_status_line(args))   # バスがリモートへ鏡写しされるかを起動時に明示
    # 起動直後に一度だけ書いておく（ここでは push しない＝新規 push トリガーは増やさない）。
    # state_git 有効時は既存の毎 tick state_sync(args) が自分の interval で自然に拾って
    # 押し出すため、完全アイドルのままでも state_git_interval 以内に生存が可視化される。
    write_daemon_status(args, bus, daemon_id, orchestrators, workers)
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
    # park & poll: 全 active run の park 済みノードをバッチ再確認する（承認待ちを worker から
    # 切り離す監視主体）。watch_interval 毎に間引く。deferring executor でなければ no-op。
    watch_interval = _watch_interval(_executor_cfg(args))
    next_wait_service = 0.0

    while not stop["v"]:
        bus.sync_pull()
        state_sync(args)   # 状態 git: バス状態の共有と inbox 投入の取り込み（間隔律速・ローカルバス時のみ）
        maybe_heartbeat_daemon_status(args, bus, daemon_id, orchestrators, workers)  # --status-interval のときだけ
        # cancel 指示の受理: マーカーのある run を canceled に終端化し、その run の
        # orchestrator/worker を止め、park の再ポーリングを止める（--close-issues ならイシューも
        # 後始末）。これで承認待ちで park 中の run も、暴走中の run も、run スコープで恒久停止できる。
        for rid in bus.list_cancels():
            meta = bus.run_meta(rid)
            if meta and meta.get("status") in TERMINAL:
                continue                              # 既に終端（処理済み）→ 何もしない
            info = bus.cancel_info(rid)
            reason = info.get("reason") or "cancel 指示"
            if info.get("close_issues"):
                _apply_on_cancel(bus, args, rid)      # waits を消す前にイシューを後始末
            bus.clear_waits_for_run(rid)
            # この daemon が駆動中の子を止める（run スコープ）
            if rid in orchestrators and orchestrators[rid].poll() is None:
                orchestrators[rid].terminate()
            for _, wp in [(r, p) for r, p in workers if r == rid]:
                if wp.poll() is None:
                    wp.terminate()
            marked = bus.mark_canceled(rid, reason)
            bus.run_view(rid).event(daemon_id, "canceled", run=rid, reason=reason)
            bus.sync_push(f"cancel run {rid}: {reason}")
            if marked:
                log(daemon_id, f"cancel 受理: {rid} を canceled に終端化（{reason}）")
        # park & poll: 承認待ち等で park されたノードをまとめて再確認し、決着なら終端 result を書く。
        # 監視は**自分が駆動している run だけ**を対象にする（分散時に N 台が全 park を重複ポーリング
        # しないよう、1 run の監視は駆動オーナー 1 台に分担する）。オーナー消失時は孤児 reclaim が
        # run（＝監視）を別 PC へ移すので取りこぼさない。
        if time.time() >= next_wait_service:
            try:
                n = service_waits(bus, args, only_runs=list(orchestrators), daemon_id=daemon_id)
                if n:
                    write_daemon_status(args, bus, daemon_id, orchestrators, workers)
            except Exception as e:  # noqa: BLE001 — 監視失敗は daemon を止めない
                log(daemon_id, f"service_waits でエラー（無視して継続）: {e}")
            next_wait_service = time.time() + watch_interval
        # 一時ファイルの自動クリーンアップ（ロック / 中間 .tmp / 孤立クローン）を定期実行
        if cleanup_interval > 0 and time.time() - last_cleanup >= cleanup_interval:
            last_cleanup = time.time()
            try:
                c = run_cleanup(args, bus)
                if any(c.values()):
                    log(daemon_id, f"cleanup: locks={c['locks']} tmp={c['tmp']} "
                                   f"clones={c['clones']} work_repos={c['work_repos']} "
                                   f"cache={c.get('cache', 0)}")
            except Exception as e:  # noqa: BLE001 — 掃除失敗は daemon を止めない
                log(daemon_id, f"cleanup でエラー（無視して継続）: {e}")
        # 死んだ子を刈り取る。orchestrator が done を書く前に異常終了（クラッシュ / kill /
        # 起動失敗）した場合は run が終端に達さないまま放置され、result/status を待つ消費者
        # （kiro-project の charter 駆動 watch など）が永久待機に陥る。終端でなければ
        # まず同じ run-id で再起動（resume。確定済み results/ を活かして続きから）を試み、
        # 進捗なしの連続再開が max_resumes を超えたときだけ failed に確定する。
        finished_runs = False   # このラウンドで終端に達した run（state git へ間隔を待たず押し出す）
        superseded_now = _superseded_run_ids(bus)
        for rid in [r for r, p in orchestrators.items() if p.poll() is not None]:
            rc = orchestrators[rid].poll()
            del orchestrators[rid]
            if bus.run_meta(rid).get("status") in TERMINAL:
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）")
                finished_runs = True
                continue
            if rid in superseded_now and bus.mark_run_superseded(rid, superseded_now[rid]):
                # 実行中に新世代のリトライへ引き継がれた旧 run が異常終了した。ここで再開すると
                # 世代交代で消えるべき旧リトライが復活して二重実行になるため、再開せず終端化する。
                bus.run_view(rid).event(daemon_id, "run-superseded", run=rid,
                                        by=superseded_now[rid])
                bus.sync_push(f"run {rid} superseded（新世代 {superseded_now[rid]} に引き継ぎ）")
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）→ superseded"
                               f"（新世代 {superseded_now[rid]} に引き継ぎ・再開しない）")
                finished_runs = True
                continue
            req = bus.read_inbox(rid)
            p = None
            if req and int(args.max_resumes or 0) > 0 and not stop["v"]:
                p = _resume_run(bus, daemon_id, args, base, rid, req, lease_window)
            if p is not None:
                orchestrators[rid] = p
                log(daemon_id, f"orchestrator 異常終了: {rid}（rc={rc}）→ 同じ run-id で再開"
                               f"（resume #{bus.run_meta(rid).get('resume_count', '?')}）")
            elif bus.fail_request(rid, f"orchestrator が終端化前に終了しました（rc={rc}）"):
                # fail_request は run 未作成（orchestrator が meta を一度も push できずに死んだ）
                # でも failed run を作って終端化する。ここで終端化しないと run_exists が偽の
                # ままになり、次 poll の受理ループが同じ要求を再 claim（commit/push）し続ける。
                bus.run_view(rid).event(daemon_id, "run-failed", run=rid, rc=rc)
                bus.sync_push(f"run {rid} failed: orchestrator 異常終了（rc={rc}）")
                log(daemon_id, f"orchestrator 異常終了: {rid}（rc={rc}）→ run を failed に確定")
                finished_runs = True
            else:
                log(daemon_id, f"orchestrator 終了: {rid}（rc={rc}）")
        workers = [(r, p) for r, p in workers if p.poll() is None]
        if finished_runs:
            write_daemon_status(args, bus, daemon_id, orchestrators, workers)  # 相乗り（追加 push 無し）
            state_sync(args, force=True)   # 状態 git: 終端した run の結果を間隔を待たず共有側へ

        # 自分が回している run の生存リースを更新（再起動後の自分・別デーモンへ「駆動中」を示す）。
        # ローカル meta は毎 poll 更新し、git バスへの伝搬は間引いて push する。
        for rid in orchestrators:
            bus.touch_run(rid, lease_window)
        if orchestrators and time.time() >= next_heartbeat_push:
            write_daemon_status(args, bus, daemon_id, orchestrators, workers)  # 相乗り（追加 push 無し）
            bus.sync_push("heartbeat: 駆動中の run の生存リースを更新")
            next_heartbeat_push = time.time() + lease_window / 3.0

        # 孤児 run の引き継ぎ: owning daemon が消失した非終端 run（PC シャットダウン・クラッシュ等）
        # を同じ run-id で再開する（続きから）。再開できないものだけ failed に確定する（再起動した
        # 新プロセスが status:running を放置せず、消費者が act_timeout まで待たずに復旧できるように）。
        if not stop["v"]:
            slots = None
            if max_runs > 0:   # 実行枠の残り（全 park の run は消費しない）。孤児の一斉再開を律速する
                slots = max(0, max_runs - _busy_run_count(bus, set(orchestrators)))
            adopted, orphan_failed = _adopt_orphan_runs(
                bus, daemon_id, set(orchestrators), lease_window, args, base, slots=slots)
            for rid, p in adopted.items():
                orchestrators[rid] = p
                log(daemon_id, f"孤児 run を引き継ぎ: {rid} → 再開"
                               f"（resume #{bus.run_meta(rid).get('resume_count', '?')}）")
            for rid in orphan_failed:
                log(daemon_id, f"孤児 run を回収: {rid} → failed（owning daemon 消失・再開不可）")

        # 1) 新しい要求を受理 → orchestrator をオンデマンド起動（分散時は 1 台だけ担当）。
        #    max_runs>0 なら「実行中（全 park を除く）の run 数」で受理を律速する。超過した要求は
        #    inbox に残り、枠が空いた poll で受理される（バックログ一括投入で orchestrator と
        #    計画エージェントがバックログ分同時に立ち上がるのを防ぐ）。cancel の受理は枠と無関係。
        busy = _busy_run_count(bus, set(orchestrators)) if max_runs > 0 else None
        for req_id in bus.list_inbox():
            if bus.run_exists(req_id) or req_id in orchestrators:
                continue
            if bus.is_canceled_requested(req_id):
                # run 化前に cancel された要求は起動せず canceled で終端化する（＝受理しない）。
                if bus.cancel_request_run(req_id, bus.cancel_info(req_id).get("reason") or ""):
                    bus.sync_push(f"cancel request {req_id}（run 化前）")
                    log(daemon_id, f"cancel: 要求 {req_id} を run 化前に canceled で終端化")
                continue
            if busy is not None and busy >= max_runs:
                continue   # 受理枠なし → inbox に残す（取りこぼさない。枠が空いた poll で受理）
            req = bus.read_inbox(req_id)
            if not req:
                continue
            if bus.claim_request(req_id, daemon_id, args.lease):
                orchestrators[req_id] = _spawn_orchestrator(base, args, req_id, req)
                bus.touch_run(req_id, lease_window)   # 受理直後に生存リースを張る（孤児誤判定を防ぐ）
                if busy is not None:
                    busy += 1
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


_WORK_REPO_DIR_RE = re.compile(r"^kiro-flow-ws-(\d+)-")


def sweep_work_repo_dirs(min_age_sec: float = 3600.0) -> int:
    """SIGKILL/OOM/電源断で finally が走らず残ったワークスペースの孤立 clone を回収し、削除数を返す。
    名前に埋めた pid（`kiro-flow-ws-<pid>-…`）で所有プロセスの生死を判定し、**死んでいるものだけ**消す
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
    # 共有 git キャッシュ: 生存 worktree を prune し、長期未使用のミラーを回収
    n_cache = sweep_cache_dirs(float(args.cleanup_age) * 3600.0)
    return {"locks": n_lock, "tmp": n_tmp, "clones": n_clone,
            "work_repos": n_work, "cache": n_cache}


# --------------------------------------------------------------------------
# gc — 古い run を掃除
# --------------------------------------------------------------------------
def _age_hours(meta) -> float:
    # run メタは updated_at/created_at、inbox 要求レコードは submitted_at を持つ（両方に使える）。
    ts = meta.get("updated_at") or meta.get("created_at") or meta.get("submitted_at")
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

    # 孤児 inbox 要求の掃除: run を伴わない inbox 要求は、daemon がこれを「新規要求」と誤認して
    # 再び orchestrator を起動し **不要な run を走らせる**原因になる（受理ゲートは run_exists のみ）。
    # remove_run は対応 inbox を消すので通常は run と一緒に片付くが、旧バージョンや外部ツールが
    # run だけ消した／crash 等で取り残された要求は掃除されず残る。ここで run が無く十分古く、かつ
    # 現在 claim されていない（lease 内で担当 daemon が処理中でない）要求を掃除する。フレッシュな
    # 未受理要求（--older-than 未満）は正規の受理待ちとして保護し、--status 指定時は「run の status で
    # 絞る」意図なので触らない。
    reaped = []
    if not args.status:
        for req_id in bus.list_inbox():
            if bus.run_exists(req_id):
                continue                          # run があるものは上の run-gc が対応（inbox も一緒に消える）
            rec = bus.read_inbox(req_id) or {}
            if _age_hours(rec) < args.older_than * 24.0:
                continue                          # まだ新しい＝受理待ちの正規要求かも → 保護
            claim_dir = os.path.join(bus.inbox_claims_dir, req_id)
            if bus._winner_in(claim_dir) is not None:
                continue                          # lease 内で担当 daemon が処理中 → 触らない
            reaped.append(req_id)
    for req_id in reaped:
        tag = "[dry-run] " if args.dry_run else ""
        age = _age_hours(bus.read_inbox(req_id) or {})
        print(f"{tag}孤児 inbox 掃除: {req_id}（run 無し・{age:.1f}h前）")
        if not args.dry_run:
            bus.remove_run(req_id)                # run 無しでも inbox 要求・claim・cancel を消す

    if (to_delete or reaped) and not args.dry_run:
        bus.sync_push(f"gc: removed {len(to_delete)} run(s), {len(reaped)} orphan inbox")
    tail = f" ＋ 孤児 inbox {len(reaped)} 件" if reaped else ""
    print(f"削除 {len(to_delete)} / 全 {len(runs)} runs{tail}"
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
# doctor（稼働診断）— bus 上の run（meta/events/results）と環境から稼働状況を
#   kiro-cli に診断させ、原因を env（ユーザー環境固有）/ config（設定）/
#   program（プログラム上の不具合）へ分類する。env/config は --fix で修正、program は
#   gitlab-idd スキルでイシュー起票（無ければ出力のみ）。収集・修正・起票の駆動は決定的、
#   診断と分類は kiro-cli へ委譲する。`kiro-flow doctor --json` は単独でも、
#   kiro-project の doctor からの連携呼び出しでも使える（同一スキーマの findings を返す）。
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
    needs_cli = (getattr(args, "executor", "agent") == "agent"
                 or getattr(args, "planner", "") == "agent")
    agent_cli = str(getattr(args, "agent_cli", "kiro") or "kiro")
    agent_bin = _AGENT_CLI_BINARIES.get(agent_cli, agent_cli)
    if needs_cli and not which(agent_bin):
        findings.append({
            "category": "env", "severity": "critical",
            "title": f"{agent_bin} が PATH に見つからない",
            "evidence": (f"executor={getattr(args, 'executor', '?')} "
                         f"planner={getattr(args, 'planner', '?')} agent_cli={agent_cli} は "
                         f"{agent_bin} を要求する"),
            "fix": f"{agent_bin} をインストールして PATH を通す（暫定回避は --executor stub / --planner stub）"})
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


def _tree_digest(root: str) -> str:
    """ツールディレクトリの内容ダイジェスト（.git を除く、相対パス＋内容の sha256）。
    「リポジトリの HEAD は進んだが本体（update_subdir）は変わっていない」を判定する
    （コミット SHA の比較では判別できない）。"""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            h.update(os.path.relpath(p, root).encode("utf-8"))
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except OSError:
                continue
    return h.hexdigest()


def apply_update(args, info: dict, runner=None) -> bool:
    """temp 領域へ sparse-checkout → install.sh → 適用済み SHA を記録。成功で True。
    temp は必ず後始末する。失敗時は state を変えない（次回再試行）。
    subdir の内容が前回適用時と同一なら installer を実行せずベースラインだけ進めて False
    （state_git 等で自分の push が update_repo の新コミットになる構成での自己増殖ループ防止）。"""
    subdir = getattr(args, "update_subdir", TOOL_SUBDIR) or TOOL_SUBDIR
    installer = getattr(args, "update_installer", "install.sh") or "install.sh"
    tmp = tempfile.mkdtemp(prefix="kiro-flow-update-")
    dest = os.path.join(tmp, "repo")
    try:
        tool_dir = sparse_checkout_tool(info["repo"], info["branch"], subdir, dest, runner=runner)
        digest = _tree_digest(tool_dir)
        state = read_update_state()
        if digest == state.get("applied_digest"):
            state["applied_sha"] = info["remote_sha"]
            state["skipped_at"] = now_iso()
            write_update_state(state)
            log("update", f"本体（{subdir}）に変更なし——適用をスキップし "
                          f"ベースラインを {info['remote_sha'][:8]} へ進めました。")
            return False
        ok, out = run_installer(tool_dir, installer, runner=runner)
        if not ok:
            log("update", f"install.sh 失敗（更新を見送り）: {out[-300:]}")
            return False
        state = read_update_state()
        state["applied_sha"] = info["remote_sha"]
        state["applied_digest"] = digest
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
    # 前回チェック時刻は state ファイルにも持続化して参照する。自己更新は restart_self の
    # 新プロセスになり呼び出し側の state dict がリセットされるため、メモリだけだと再起動
    # 直後に即時再チェック→再適用→再起動…の自己増殖ループになる。
    try:
        persisted = float(read_update_state().get("last_check_at") or 0.0)
    except (TypeError, ValueError):
        persisted = 0.0
    if now - max(state.get("last", 0.0), persisted) < interval:
        return False
    state["last"] = now
    st = read_update_state()
    st["last_check_at"] = now
    write_update_state(st)
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
    if read_update_state().get("applied_sha") == info.get("remote_sha"):
        print("  本体（update_subdir）に変更が無かったため適用をスキップし、ベースラインだけ進めました。")
        return 0
    print("  更新の取り込みに失敗しました（ログを確認してください）。", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """CLI パーサを構築して返す。main と、子プロセス起動 argv の妥当性を検証する
    テスト（_spawn_orchestrator/_spawn_worker が組み立てた argv を parse できるか）で共有する。
    グローバル引数とサブコマンド引数の置き場を取り違えると usage エラーで子が即死するため、
    その回帰を単体テストで捕まえられるように公開関数として切り出している。"""
    p = argparse.ArgumentParser(description="kiro-flow — git 共有型・分散 Dynamic Workflow")
    # 設定値の優先順位: CLI > 設定ファイル(kiro-flow.yaml) > 組み込み既定。
    # 設定ファイル対象のオプションは既定 None にし、parse 後 resolve_config で確定する。
    p.add_argument("--config", default=None,
                   help="設定ファイルのパス（未指定なら ./ → ./.kiro → ~/.kiro の kiro-flow.{yaml,yml,json}）")
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
    p.add_argument("--state-git", dest="state_git", default=None,
                   help="ワーク内容（ローカルバスの runs/・inbox/）を保存・共有する git リポジトリ"
                        "（URL/パス）。リモートの kiro-projects-viewer が進捗/結果を読める"
                        "（未指定で無効。--git のバス分散とは独立で、--git 指定時は無視）")
    p.add_argument("--state-git-branch", dest="state_git_branch", default=None,
                   help="state_git の同期先ブランチ（既定 main）")
    p.add_argument("--state-git-subdir", dest="state_git_subdir", default=None,
                   help="state_git リポジトリ内の保存先サブディレクトリ（既定 kiro-flow）。"
                        "同一リポジトリへ他プログラムもコミットする前提の名前空間分離")
    p.add_argument("--state-git-interval", dest="state_git_interval", type=float, default=None,
                   help="state_git の fetch/push の最短間隔（秒。既定 300）。リモートサーバへの"
                        "負荷を一定に保つ律速。0 で毎同期")
    p.add_argument("--executor-dir", dest="executor_dir", default=None,
                   help="executor プラグイン（<name>.py）の追加検索ディレクトリ（設定 executor_dir と同義）")
    p.add_argument("--workspace", dest="workspace", default=None,
                   help="この run（=バックログ単位）の唯一の書込先リポジトリ。素の URL でも、構造化 JSON "
                        "（{url,path,base,target,desc}）でも可。worker が temp 領域へ clone し、作業ブランチ "
                        "kf/<run-id> を base から作って作業、変更があれば kiro-flow が commit/push する。"
                        "path はモノレポの作業フォルダ、target は MR/PR のターゲットブランチ。"
                        "省略時は読み取り専用 run")
    p.add_argument("--reference", dest="references", action="append", default=None,
                   help="参照リポジトリ（読むだけ・書き込まない／複数可）。素の URL でも JSON "
                        "（{url,path,base,desc}）でも可。エージェントのプロンプトと gitlab イシュー本文に"
                        "参照節として載る（clone はしない）")
    p.add_argument("--agent-cli", dest="agent_cli", default=None, choices=["kiro", "claude", "copilot", "codex"],
                   help="LLM 実行に使うエージェント CLI（設定 agent_cli と同義）。kiro=kiro-cli chat（既定）/ "
                        "claude=Claude Code ヘッドレス（claude -p）/ copilot=GitHub Copilot CLI（copilot -p）/ "
                        "codex=OpenAI Codex CLI（codex exec）")
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
    run.add_argument("--planner", choices=["agent", "stub", "flow-planner"], default=None)
    run.add_argument("--executor", default=None,
                     help="ワーカーバス: 組み込み agent / stub、または executor プラグイン名"
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
    run.add_argument("--inherit-from", dest="inherit_from", default=None,
                     help="リトライ: 指定した先行 run-id から確定済み（done）ノードの結果・計画・"
                          "中間成果物を引き継ぎ、先行 run を掃除する（新規時のみ有効）。先行 run が"
                          "完全 done なら状態は引き継がず掃除だけ行う（feedback 付きで新規にやり直す）")
    run.set_defaults(func=cmd_run)

    orch = sub.add_parser("orchestrate", help="計画役")
    orch.add_argument("--request", required=True)
    orch.add_argument("--planner", choices=["agent", "stub", "flow-planner"], default=None)
    orch.add_argument("--executor", default=None,
                      help="ワーカーバス（agent/stub/プラグイン名/.py パス）。"
                           "評価役（evaluator）は stub 以外ならローカルのエージェント CLI で判断")
    orch.add_argument("--max-iterations", type=int, default=None)
    orch.add_argument("--max-fanout", type=int, default=None)
    orch.add_argument("--max-retries", type=int, default=None)
    orch.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    orch.add_argument("--no-review", dest="review", action="store_const", const=False)
    orch.add_argument("--node-id", default="orchestrator")
    orch.add_argument("--model_opt", dest="model", default=None)
    orch.add_argument("--poll", type=float, default=None)
    orch.add_argument("--inherit-from", dest="inherit_from", default=None,
                      help="リトライ: 先行 run-id から確定済みノードを引き継ぎ先行 run を掃除する")
    orch.set_defaults(func=cmd_orchestrate)

    work = sub.add_parser("work", help="ワーカー役")
    work.add_argument("--node-id", default=f"{socket.gethostname()}-{os.getpid()}")
    work.add_argument("--executor", default=None,
                      help="ワーカーバス（agent/stub/プラグイン名/.py パス）")
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
    dm.add_argument("--max-runs", dest="max_runs", type=int, default=None,
                    help="同時に実行する run（orchestrator）の上限（既定 8）。全 park（承認待ち）の "
                         "run は数えない。超過要求は inbox に残り枠が空き次第受理。0 以下で無制限")
    dm.add_argument("--planner", choices=["agent", "stub", "flow-planner"], default=None)
    dm.add_argument("--executor", default=None,
                    help="ワーカーバス（agent/stub/プラグイン名/.py パス）")
    dm.add_argument("--max-iterations", type=int, default=None)
    dm.add_argument("--max-fanout", type=int, default=None)
    dm.add_argument("--max-retries", type=int, default=None)
    dm.add_argument("--max-resumes", dest="max_resumes", type=int, default=None,
                    help="孤児 run（owning daemon 消失）の自動再開の上限（進捗なしの連続回数, "
                         "既定 3）。進捗があれば数え直す。0 以下で無効（孤児は即 failed）")
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
    dm.add_argument("--status-interval", dest="status_interval", type=float, default=None,
                    help="state_git（鏡）越しにリモートの kiro-projects-viewer が daemon の生存を"
                         "判定するための status.json を、アイドル中もこの間隔（秒）で更新する"
                         "（既定 0＝無効。無効時はアイドル中 status.json に一切触れず、state_git の"
                         "commit-if-diff で追加コミットを作らない）。real な run イベント時は"
                         "この設定に関わらず既存の sync に相乗りして常に最新化される")
    dm.set_defaults(func=cmd_daemon)

    sb = sub.add_parser("submit", help="要求を inbox に投入（デーモンが拾う）")
    sb.add_argument("request", help="ワークフローへの要求")
    sb.add_argument("--inherit-from", dest="inherit_from", default=None,
                    help="リトライ: 先行 run-id から確定済みノードを引き継ぎ先行 run を掃除する"
                         "（daemon の orchestrate に伝搬される）")
    sb.set_defaults(func=cmd_submit)

    cn = sub.add_parser("cancel",
                        help="run を canceled に終端化（人の明示指示による run スコープの恒久停止）。"
                             "承認待ちで park 中の run も暴走中の run も止められる緊急回避手段")
    cn.add_argument("run_id", help="キャンセルする run-id（submit の戻り値／status --list で確認）")
    cn.add_argument("--reason", default="", help="キャンセル理由（meta / イベントに記録）")
    cn.add_argument("--close-issues", dest="close_issues", action="store_true",
                    help="park 済みの GitLab イシューに取消コメントを付けてクローズする"
                         "（既定: イシューは残し、追跡だけやめる）")
    cn.set_defaults(func=cmd_cancel)

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

    gc = sub.add_parser("gc", help="古い run を掃除（対応する inbox 要求・claim も削除）。"
                                   "run を伴わない孤児 inbox 要求（不要 run の再起動元）も掃除する")
    gc.add_argument("--older-than", type=float, default=7.0,
                    help="この日数より古い run が対象（孤児 inbox 要求もこの閾値で掃除）")
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
    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()
    # CLI 未指定の設定値を設定ファイル→組み込み既定で確定（CLI > config > 既定）
    resolve_config(args)
    # args を持たない free 関数（run_kiro 等）が読む閾値をモジュール変数へ確定させる
    _configure_thresholds(args)
    # ワークスペース clone の削除を二重化（main の finally に加え、想定外の早期 exit でも回収）
    atexit.register(cleanup_workspace)
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
    # 起動初回にバスフォルダが無ければ作成する（git バスでは .gitkeep も置く）。
    # 診断/読み取り専用コマンドは副作用を持たせない（doctor の「未作成」所見を潰さない）。
    if getattr(args, "func", None) in (
            cmd_run, cmd_daemon, cmd_orchestrate, cmd_work, cmd_submit, cmd_cancel, None):
        ensure_bus_root(args)
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
        cleanup_workspace()   # ワークスペースの clone は常に消す（作業後クリーンは必須）


if __name__ == "__main__":
    sys.exit(main())
