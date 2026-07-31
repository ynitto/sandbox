"""turnmark — 実行中の手番を**ノード内**に印す pid ファイル（実装計画 W1-5）。

PC 単位の同時実行上限（agent-project 常駐体の `max_concurrent`）は、常駐体が起こした
手番だけを数えても守れない。設計 §1.3 C14 はスキル起動の単発実行（人が
`agent-amigos run --once` / `drive` を直接叩く）との併走を明示的に許すため、
**プロセスを跨いで観測できるターン粒度の印**が要る。

バス上の `status/<node>--<role>.json` は使えない。あれは**ロールの在籍状態**で、
ターンが終わっても `state` は `working` のまま残る（`runner._turn_once` の末尾が
そう書く）ため、「走っているか」の観測に流用すると終わった手番を走行中と誤読する。

置き場はバスの**外**（`~/.agents/amigos/turns/`。`$AGENT_AMIGOS_TURNS_DIR` で上書き可）。
バスは同期対象なので、ローカルの実行状況を書くと他ノードへ伝播してしまう——
上限は PC 単位の性質なので、印もその PC の中だけに置く。

ファイル形式（ツール横断のデータ契約。読み手は agent-project の常駐体）:

    <safe(mission)>--<safe(role)>.json
      {"pid": 12345, "mission": "am-x", "role": "impl", "started": "<iso>"}

`mission` / `role` は**本文の値が正**（ファイル名は衝突回避のための派生で、
role_id に `--` が入ると分解できない）。生存判定は pid（`os.kill(pid, 0)`）で行い、
落ちたプロセスの書き残しは自然に無視される——鮮度タイムアウトのような当て推量は要らない。
"""
from __future__ import annotations

import contextlib
import os

from agentcore.protocol import safe_name

from .configfile import agent_home_subdir
from .util import now_iso, read_json, write_json_atomic


def turns_dir() -> str:
    """手番マーカーの置き場。共通ホームの解決はノード予算台帳と同じ流儀。"""
    return os.path.abspath(agent_home_subdir("AGENT_AMIGOS_TURNS_DIR", "amigos", "turns"))


def mark_path(mission_id: str, role_id: str) -> str:
    return os.path.join(turns_dir(),
                        f"{safe_name(mission_id)}--{safe_name(role_id)}.json")


def pid_alive(pid: int) -> bool:
    """pid が生存しているか（POSIX）。0/負や不在は False。別ユーザのプロセスは生存扱い。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # 別ユーザの生存プロセス（シグナルを送れないだけ）
    except OSError:
        return False
    return True


@contextlib.contextmanager
def held(mission_id: str, role_id: str):
    """手番の実行中だけマーカーを置く。**印が置けなくても手番は止めない**——
    観測の失敗で仕事が進まなくなる方が害が大きい（上限の超過は一時的で自己修復する）。

    後始末では**自分の pid が書かれている場合だけ**消す。同じロールの手番が二重に
    起動した場合（C14 の併走）、先に終わった側が相手の印を消して観測が空振りするのを防ぐ。"""
    path = mark_path(mission_id, role_id)
    mine = os.getpid()
    with contextlib.suppress(OSError):
        os.makedirs(turns_dir(), exist_ok=True)
        write_json_atomic(path, {"pid": mine, "mission": str(mission_id),
                                 "role": str(role_id), "started": now_iso()})
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            if int((read_json(path) or {}).get("pid") or 0) == mine:
                os.remove(path)


def running() -> "set[tuple[str, str]]":
    """この PC で**いま走っている**手番の (mission_id, role_id)。

    生存していない pid の印は無視し、ついでに掃除する（落ちたプロセスの書き残しで
    スロットを永久に埋めない）。agent-project の常駐体は同じ形式を直接読む——
    ツールを跨ぐのはデータ契約だけで、実装は import しない。"""
    out: "set[tuple[str, str]]" = set()
    d = turns_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        mark = read_json(path)
        if not isinstance(mark, dict):
            continue
        try:
            pid = int(mark.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if not pid_alive(pid):
            with contextlib.suppress(OSError):
                os.remove(path)
            continue
        mission, role = str(mark.get("mission") or ""), str(mark.get("role") or "")
        if mission and role:
            out.add((mission, role))
    return out
