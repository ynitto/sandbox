"""core — 小道具・Bus（ローカル）・GitBus・claim プロトコル。

agent-board は agent-flow / agent-amigos の claim プロトコルを『同じ仕様・別実装』で踏襲する
（docs/plans/2026-07-19-delegation-contract-design.md §7 の「共通 claim 仕様書」の趣旨）。
真実は板の上のファイルにあり、転送層（GitBus）は sync_pull / sync_push だけを差し替える。
標準ライブラリのみ（git は分散モードで必要）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

try:
    import fcntl  # POSIX（macOS/Linux/WSL）
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore
try:
    import msvcrt  # Windows
except ImportError:
    msvcrt = None  # type: ignore


# --- 小道具 -----------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def write_json_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


_ts_lock = threading.Lock()
_ts_last = [0.0]


def unique_ts() -> float:
    """プロセス横断で単調増加のタイムスタンプ。等値 ts による二重勝者を防ぐ
    （agent-flow の _unique_ts と同趣旨）。"""
    with _ts_lock:
        t = time.time()
        if t <= _ts_last[0]:
            t = _ts_last[0] + 1e-6
        _ts_last[0] = t
        return t


def _lock_path(key_dir: str) -> str:
    h = hashlib.sha1(os.path.abspath(key_dir).encode()).hexdigest()
    d = os.path.join(tempfile.gettempdir(), "agent-board-locks")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.lock")


@contextlib.contextmanager
def file_lock(path: str):
    """同一マシンの並行 claim を直列化するプロセス間ロック（バス外＝git に乗せない）。"""
    if fcntl is None and msvcrt is None:  # pragma: no cover
        yield
        return
    f = open(path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:  # pragma: no cover — Windows
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.2)
        try:
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(f, fcntl.LOCK_UN)
                else:  # pragma: no cover
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        f.close()


def safe_name(s: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(s)) or "x"


# --- Bus（ローカル）--------------------------------------------------------
#
# 板のレイアウト（schemas/board.schema.json）:
#   nodes/<node-id>.json
#   delegations/<id>/post.json / bids/<who>.json / award.json
#     / status/<who>.json / results/<who>.json / result.json / cancelled.json
#
# 書き込み所有権はパス単位で分割（依頼者 = post/award/result/cancelled、
# 各ノード = nodes/<自分>・bids/<自分>・status/<自分>・results/<自分>）。git でも衝突しない。

TERMINAL = {"done", "failed", "cancelled"}


class Bus:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.nodes_dir = os.path.join(self.root, "nodes")
        self.deleg_dir = os.path.join(self.root, "delegations")

    # 転送フック（ローカルは no-op、GitBus が上書き）
    def sync_pull(self) -> None:
        pass

    def sync_push(self, msg: str = "") -> None:
        pass

    def ensure_root(self) -> None:
        os.makedirs(self.nodes_dir, exist_ok=True)
        os.makedirs(self.deleg_dir, exist_ok=True)

    # --- ノード登録 ---
    def node_path(self, node_id: str) -> str:
        return os.path.join(self.nodes_dir, f"{safe_name(node_id)}.json")

    def write_node(self, node: dict) -> None:
        write_json_atomic(self.node_path(node["node"]), node)

    def list_nodes(self) -> "list[dict]":
        out = []
        if os.path.isdir(self.nodes_dir):
            for name in sorted(os.listdir(self.nodes_dir)):
                if name.endswith(".json"):
                    rec = read_json(os.path.join(self.nodes_dir, name))
                    if rec:
                        out.append(rec)
        return out

    # --- 委譲（delegation）本体 ---
    def d_dir(self, did: str) -> str:
        return os.path.join(self.deleg_dir, safe_name(did))

    def post_path(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "post.json")

    def award_path(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "award.json")

    def result_path(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "result.json")

    def cancelled_path(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "cancelled.json")

    def bids_dir(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "bids")

    def status_dir(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "status")

    def results_dir(self, did: str) -> str:
        return os.path.join(self.d_dir(did), "results")

    def list_delegations(self) -> "list[str]":
        if not os.path.isdir(self.deleg_dir):
            return []
        return sorted(d for d in os.listdir(self.deleg_dir)
                      if os.path.isdir(os.path.join(self.deleg_dir, d)))

    def read_post(self, did: str):
        return read_json(self.post_path(did))

    def write_post(self, did: str, envelope: dict) -> None:
        write_json_atomic(self.post_path(did), envelope)

    def is_cancelled(self, did: str) -> bool:
        return os.path.exists(self.cancelled_path(did))

    def read_result(self, did: str):
        return read_json(self.result_path(did))

    def has_result(self, did: str) -> bool:
        return os.path.exists(self.result_path(did))

    # --- ステータス（実行ノードの自己申告） ---
    def write_status(self, did: str, who: str, rec: dict) -> None:
        rec = {"who": who, "heartbeat": now_iso(), **rec}
        write_json_atomic(os.path.join(self.status_dir(did), f"{safe_name(who)}.json"), rec)

    def read_status(self, did: str, who: str):
        return read_json(os.path.join(self.status_dir(did), f"{safe_name(who)}.json"))

    def list_status(self, did: str) -> "dict[str, dict]":
        out = {}
        sd = self.status_dir(did)
        if os.path.isdir(sd):
            for name in os.listdir(sd):
                if name.endswith(".json"):
                    rec = read_json(os.path.join(sd, name))
                    if rec:
                        out[rec.get("who", name[:-5])] = rec
        return out

    # --- 成果報告（実行ノードの自己申告） ---
    def write_result_report(self, did: str, who: str, rec: dict) -> None:
        rec = {"who": who, **rec}
        write_json_atomic(os.path.join(self.results_dir(did), f"{safe_name(who)}.json"), rec)

    def list_result_reports(self, did: str) -> "list[dict]":
        out = []
        rd = self.results_dir(did)
        if os.path.isdir(rd):
            for name in sorted(os.listdir(rd)):
                if name.endswith(".json"):
                    rec = read_json(os.path.join(rd, name))
                    if rec:
                        out.append(rec)
        return out

    # --- 落札（owner-picks） ---
    def read_award(self, did: str):
        return read_json(self.award_path(did))

    def write_award(self, did: str, node: str, awarded_by: str) -> None:
        write_json_atomic(self.award_path(did), {
            "node": node, "awarded_by": awarded_by, "awarded_at": now_iso(),
        })

    # --- 確定成果（一本化） ---
    def write_result(self, did: str, rec: dict) -> None:
        write_json_atomic(self.result_path(did), {"resolved_at": now_iso(), **rec})

    def write_cancelled(self, did: str, reason: str, who: str) -> None:
        write_json_atomic(self.cancelled_path(did), {
            "reason": reason, "cancelled_by": who, "cancelled_at": now_iso(),
        })

    # --- claim（名前空間付き入札 ＋ 決定的タイブレーク） ---
    #
    # 各ノードは自分専用ファイル bids/<who>.json を書く（ファイル名が衝突しないので git で
    # add/add コンフリクトにならない）。勝者は有効（lease 内）な入札のうち (ts, who) 最小の
    # 1 件に決定的に定まる。ローカルでも git でも全ノードが同じ集合から同じ勝者を導く。

    def _list_bids(self, did: str) -> "dict[str, dict]":
        out = {}
        bd = self.bids_dir(did)
        if os.path.isdir(bd):
            for name in os.listdir(bd):
                if name.endswith(".json"):
                    info = read_json(os.path.join(bd, name))
                    if info:
                        out[info.get("who", name[:-5])] = info
        return out

    def winner(self, did: str) -> "str | None":
        """lease 内の入札から決定的に勝者を選ぶ（(ts, who) 最小）。無ければ None。"""
        now = time.time()
        live = [(info.get("ts", 0.0), who)
                for who, info in self._list_bids(did).items()
                if info.get("lease_until", 0) >= now]
        return min(live)[1] if live else None

    def bid_ranking(self, did: str) -> "list[str]":
        """lease 内の入札を (ts, who) 昇順で並べた who の列（投機の実行順位）。"""
        now = time.time()
        live = [(info.get("ts", 0.0), who)
                for who, info in self._list_bids(did).items()
                if info.get("lease_until", 0) >= now]
        return [who for _ts, who in sorted(live)]

    def _write_bid(self, did: str, who: str, lease_sec: float, extra: dict) -> None:
        rec = {"who": who, "ts": unique_ts(), "claimed_at": now_iso(),
               "lease_until": time.time() + lease_sec, **extra}
        write_json_atomic(os.path.join(self.bids_dir(did), f"{safe_name(who)}.json"), rec)

    def try_bid(self, did: str, who: str, lease_sec: float, extra: "dict | None" = None) -> bool:
        """入札して勝者になれたら True（先勝ち）。owner-picks でも入札（応募）は同じ。"""
        self.sync_pull()
        if self.has_result(did) or self.is_cancelled(did):
            return False
        bd = self.bids_dir(did)
        os.makedirs(bd, exist_ok=True)
        with file_lock(_lock_path(bd)):
            w = self.winner(did)
            if w is not None and w != who:
                return False
            self._write_bid(did, who, lease_sec, extra or {})
            self.sync_push(f"bid {did} by {who}")
            self.sync_pull()  # 他ノードの入札を取り込んでから勝敗判定
            if self.winner(did) == who:
                return True
            # 負けた自分の入札は消す（ゾンビ勝者を作らない）
            try:
                os.remove(os.path.join(bd, f"{safe_name(who)}.json"))
                self.sync_push(f"bid withdraw {who}")
            except OSError:
                pass
            return False

    def extend_bid(self, did: str, who: str, lease_sec: float) -> bool:
        """実行中ノードのハートビート: 自分の入札の lease_until だけ延長する（ts は据え置き）。"""
        bd = self.bids_dir(did)
        path = os.path.join(bd, f"{safe_name(who)}.json")
        os.makedirs(bd, exist_ok=True)
        with file_lock(_lock_path(bd)):
            cur = read_json(path)
            if not cur:
                return False
            w = self.winner(did)
            if w is not None and w != who:
                return False
            cur["lease_until"] = time.time() + lease_sec
            write_json_atomic(path, cur)
        return True

    def remove_delegation(self, did: str) -> None:
        import shutil
        shutil.rmtree(self.d_dir(did), ignore_errors=True)


# --- GitBus（分散） ---------------------------------------------------------

class GitBus(Bus):
    """専用リポジトリを板にする転送層。ノードごとの専用クローン上で動く。
    同期規律は agent-project / agent-flow の state_git と同じ（間隔律速・pull --rebase
    リトライ・force push 禁止・自パスのみステージ）。板の書き込み頻度は低く会話も無いため
    単一ブランチで足りる（設計 §4.2）。"""

    def __init__(self, clone_dir: str, remote: str, branch: str = "main",
                 interval: float = 30.0):
        self.workdir = os.path.abspath(clone_dir)
        self.remote = remote
        self.branch = branch
        self.interval = interval
        self._last_pull = 0.0
        super().__init__(self.workdir)
        self._ensure_clone()

    def _git(self, *args, check=True, **kw):
        return subprocess.run(["git", "-C", self.workdir, *args],
                              capture_output=True, text=True, check=check, **kw)

    def _ensure_clone(self) -> None:
        if os.path.isdir(os.path.join(self.workdir, ".git")):
            return
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        r = subprocess.run(["git", "clone", "--branch", self.branch, self.remote, self.workdir],
                           capture_output=True, text=True)
        if r.returncode != 0:
            # ブランチが未作成なら空クローン → 初期化
            subprocess.run(["git", "clone", self.remote, self.workdir],
                           capture_output=True, text=True, check=True)
            self._git("checkout", "-B", self.branch)
        self.ensure_root()

    def sync_pull(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_pull) < self.interval:
            return
        r = self._git("pull", "--rebase", "origin", self.branch, check=False)
        if r.returncode == 0:
            self._last_pull = now

    def sync_push(self, msg: str = "") -> None:
        self._git("add", "-A", check=False)
        st = self._git("status", "--porcelain", check=False)
        if not st.stdout.strip():
            return
        self._git("commit", "-m", msg or "board update", check=False)
        for i in range(5):
            r = self._git("push", "origin", self.branch, check=False)
            if r.returncode == 0:
                self._last_pull = time.time()
                return
            self._git("pull", "--rebase", "origin", self.branch, check=False)
            time.sleep(min(2 ** i, 16))


def make_bus(spec: str, node_id: str, workdir: "str | None" = None,
             branch: str = "main", interval: float = 30.0) -> Bus:
    """bus spec からバスを作る。git+<url> なら GitBus（ノード専用クローン）、他はローカル dir。"""
    spec = str(spec or ".").strip()
    if spec.startswith("git+"):
        remote = spec[4:]
        base = workdir or os.path.join(
            os.path.expanduser("~/.agents/board"),
            hashlib.sha1(remote.encode()).hexdigest()[:8])
        clone_dir = os.path.join(base, safe_name(node_id))
        return GitBus(clone_dir, remote=remote, branch=branch, interval=interval)
    return Bus(os.path.abspath(spec))
