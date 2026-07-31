"""agentcore.protocol — claim / lease の共通実装（設計 §4.1・R1）。

flow のタスク claim・amigos のロール claim・板の入札という 3 つの重複実装を、この 1 つの
ファイルベースのアルゴリズムに統一する:

- 各クレーマは自分専用のファイル `<claim_dir>/<who>.json` を書く（ファイル名が衝突しない
  ので git 転送に乗せても add/add コンフリクトにならない）。
- 勝者は「lease 内（`lease_until >= now`）の claim のうち `(ts, who)` が最小の 1 件」に
  決定的に定まる。ローカル/ git どちらの転送でも同じロジックで唯一の勝者が決まる。
- 同一マシン上の並行 claim はプロセス間排他ロック（`claim_dir` ごとに 1 本）で直列化し、
  「先着読みの勝者」と「決定的タイブレークの勝者」の食い違いによる二重勝者を防ぐ。

git 分散（別ノード）はクローンごとに別ロックなので直列化されないが、その整合は
sync 後の決定的タイブレーク＋lease が担う。転送（push/pull）はこのモジュールの外
（呼び出し側が `agentcore.transport` 等で行う）— このモジュールはコールバックとして
受け取るだけで、転送方式に依存しない。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Callable, Optional

try:
    import fcntl  # POSIX（macOS/Linux/WSL）
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore
try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore

from .heartbeat import now_iso

__all__ = [
    "unique_ts", "read_json", "write_json_atomic",
    "list_claims", "winner", "write_claim", "try_claim",
    "extend_claim", "renew_lease", "release_claim",
]

_ts_lock = threading.Lock()
_last_ts = 0.0


def unique_ts() -> float:
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


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def write_json_atomic(path: str, data) -> None:
    """JSON を「tmp へ書く → fsync → rename」で置き換える。

    rename だけでは**電源断でサイズ 0 のファイルが残る**: rename のメタデータが先に永続化され、
    中身がまだフラッシュされていない状態があるため。git のオブジェクトに対しては同じ問題を
    `core.fsync=all`（`transport._DURABLE_GIT_CONFIG`）で塞いでいるのに、こちらの契約ファイル
    （`engine/status.json`・claim・lease・run の meta.json）は素通しだった——読み手はどれも
    「読めなければ存在しない」と解釈するので、空ファイルは黙って状態の欠損になる。
    ディレクトリの fsync まで行い、rename そのものも永続化させる。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # ディレクトリエントリ（rename）の永続化。Windows は O_RDONLY でディレクトリを開けず、
    # そもそも rename のジャーナリング挙動が異なるので失敗は黙って許容する。
    try:
        dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def safe_name(who: str) -> str:
    """claim ファイル名に使える形へ（英数・`._-` 以外は `-` へ）。"""
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(who)) or "x"


def _lock_path(claim_dir: str) -> str:
    """claim 用の排他ロックファイルのパス（バス外の一時領域に置く）。
    同一マシンの同一 claim_dir には同一パスが対応し、プロセス/スレッド間で排他になる。"""
    h = hashlib.sha1(os.path.abspath(claim_dir).encode()).hexdigest()
    d = os.path.join(tempfile.gettempdir(), "agentcore-claim-locks")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.lock")


@contextlib.contextmanager
def _file_lock(path: str):
    """プロセス間の排他ロック。POSIX は fcntl.flock、Windows は msvcrt.locking で実装する。
    どちらも無い環境のみ no-op に落ちる。"""
    if fcntl is None and msvcrt is None:  # pragma: no cover — 想定外の環境のみ
        yield
        return
    f = open(path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:  # Windows: 先頭 1 バイトの領域ロックで排他（獲得までブロッキング再試行）
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
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        f.close()


def list_claims(claim_dir: str) -> "dict[str, dict]":
    out: "dict[str, dict]" = {}
    if os.path.isdir(claim_dir):
        for name in os.listdir(claim_dir):
            if name.endswith(".json"):
                info = read_json(os.path.join(claim_dir, name))
                if info:
                    out[info.get("who", name[:-5])] = info
    return out


def _as_float(value, default: float = 0.0) -> "Optional[float]":
    """claim レコードの数値フィールドを float へ。数値として読めなければ None。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def winner(claim_dir: str, now: "Optional[float]" = None) -> "Optional[str]":
    """lease 内の claim から `(ts, who)` 最小の勝者を決定的に選ぶ。無ければ None。

    `ts` / `lease_until` が数値として読めない claim は無視する（壊れた 1 ファイルが
    比較例外で勝者判定そのものを止め、そのロール/委譲が誰にも取れなくなるのを防ぐ）。"""
    now = now if now is not None else time.time()
    live = []
    for who, info in list_claims(claim_dir).items():
        lease_until = _as_float(info.get("lease_until"))
        ts = _as_float(info.get("ts"))
        if lease_until is None or ts is None or lease_until < now:
            continue
        live.append((ts, who))
    return min(live)[1] if live else None


def write_claim(claim_dir: str, who: str, lease_sec: float,
                 extra: "Optional[dict]" = None) -> None:
    """`<claim_dir>/<who>.json` を新規の ts で書く（既存を上書き。タイブレークの根拠が
    動くため、延長したいだけなら extend_claim / renew_lease を使うこと）。"""
    os.makedirs(claim_dir, exist_ok=True)
    rec = {"who": who, "ts": unique_ts(), "claimed_at": now_iso(),
           "lease_until": time.time() + lease_sec}
    if extra:
        rec.update(extra)
    write_json_atomic(os.path.join(claim_dir, f"{safe_name(who)}.json"), rec)


def try_claim(claim_dir: str, who: str, lease_sec: float,
              on_write: "Optional[Callable[[], None]]" = None,
              on_sync: "Optional[Callable[[], None]]" = None,
              on_withdraw: "Optional[Callable[[], None]]" = None,
              extra: "Optional[dict]" = None) -> bool:
    """名前空間付き claim。勝てたら True。

    ロック内で: 現勝者を確認 → 自分の claim を書く → `on_write`（転送があれば push 相当）→
    `on_sync`（転送があれば pull 相当。他ノードの claim を取り込んでから勝敗判定するため）→
    勝者判定。負けたら自分の claim ファイルを削除し `on_withdraw`（push 相当）を呼ぶ
    ——負けた claim を残すと、勝者の release 後に敗者の古い lease が zombie 勝者になりうる。
    ローカル専用（転送なし）の呼び出しは on_write/on_sync/on_withdraw を省略してよい。"""
    os.makedirs(claim_dir, exist_ok=True)
    with _file_lock(_lock_path(claim_dir)):
        w = winner(claim_dir)
        if w is not None and w != who:
            return False  # 既に他者が勝者（lease 内）
        write_claim(claim_dir, who, lease_sec, extra)
        if on_write:
            on_write()
        if on_sync:
            on_sync()
        if winner(claim_dir) == who:
            return True
        with contextlib.suppress(OSError):
            os.remove(os.path.join(claim_dir, f"{safe_name(who)}.json"))
        if on_withdraw:
            on_withdraw()
        return False


def extend_claim(claim_dir: str, who: str, lease_sec: float) -> bool:
    """実行中ワーカーの心拍用: 自分の claim の lease_until **だけ**を無条件に延長する。
    ts / claimed_at は書き換えない（新しい ts を振り直すと勝者タイブレークの根拠が動く）。
    claim ロックの中で行い、自分の claim が消えている（release / withdraw 済み）か、
    lease 失効中に他者が正当な勝者になっていれば延長せず False を返す
    ——失った claim を心拍が無条件に書き戻すと二重実行になる。"""
    path = os.path.join(claim_dir, f"{safe_name(who)}.json")
    os.makedirs(claim_dir, exist_ok=True)
    with _file_lock(_lock_path(claim_dir)):
        cur = read_json(path)
        if not cur:
            return False
        w = winner(claim_dir)
        if w is not None and w != who:
            return False
        cur["lease_until"] = time.time() + lease_sec
        write_json_atomic(path, cur)
    return True


def renew_lease(claim_dir: str, who: str, lease_sec: float,
                 extra: "Optional[dict]" = None, create_if_missing: bool = True) -> bool:
    """`<claim_dir>/<who>.json` を書く／更新する。既存が無ければ新規（ts はいま）、あれば
    残 lease が半分未満のときだけ lease_until を延長する。(ts, who) タイブレークの根拠 ts は
    温存し、毎回書き換えて先勝ちの意味を壊さない・転送頻度も抑える。書いたら True。
    win/lose の判定は行わない（claim の存在＝応募/占有の維持だけを表す。tie-break が
    要るケースは try_claim を使うこと）。

    `create_if_missing=False` は「既にある自分の claim の延長だけ」を行い、消えていれば
    False を返す（心拍用）。手放した／取り下げられた claim を心拍が無条件に書き戻すと、
    誰も動いていないのに占有され続ける zombie 勝者を作るため、実行中ワーカーの心拍からは
    必ず False で呼ぶこと。"""
    path = os.path.join(claim_dir, f"{safe_name(who)}.json")
    with _file_lock(_lock_path(claim_dir)):
        cur = read_json(path)
        now = time.time()
        if not isinstance(cur, dict) and not create_if_missing:
            return False
        if isinstance(cur, dict):
            if float(cur.get("lease_until", 0) or 0) - now > lease_sec / 2.0:
                return False  # まだ十分残っている → 今回は延長不要
            ts = cur.get("ts", now)
            claimed_at = cur.get("claimed_at", now_iso())
        else:
            ts = now
            claimed_at = now_iso()
        rec = {"who": who, "ts": ts, "claimed_at": claimed_at, "lease_until": now + lease_sec}
        if extra:
            rec.update(extra)
        write_json_atomic(path, rec)
    return True


def release_claim(claim_dir: str, who: str) -> None:
    """自分の claim ファイルを消して手放す。呼び出し側は心拍を停止してから呼ぶこと
    ——停止前に消すと直後の心拍が claim を書き戻す。"""
    with contextlib.suppress(OSError):
        os.remove(os.path.join(claim_dir, f"{safe_name(who)}.json"))
