"""resident.supervisor — 子プロセス（プロジェクトループ）の起動・監視・再起動・隔離・
graceful 停止（設計 §4.2、実装計画 W1-4）。

`resident.scheduler` が「いつ動くか」（周期表）を扱うのに対し、supervisor は「何を子として
持つか」（プロセスのライフサイクル）を扱う。両者は独立で組み合わせられる — 典型的には
scheduler の 1 tick が `Supervisor.check_health()` を呼ぶ（常駐体本体への配線は W1-11）。

**「親 → 子への指示」の実体**: 子を止めるかどうかは常に `Supervisor` 側が決め、SIGTERM/SIGKILL
で伝える。子が自分の意思で終了を決める経路（availability の自 SIGTERM・self-update の
execv・グローバル drain フラグ — いずれも agent_project.coordination / update に現存）は、
この supervisor が実際に子プロセスを起動・監督するようになった時点で不要になる（配線は
W1-11。本モジュール自体は既存の `run --watch` 実装を書き換えない — 新しい親側の受け皿）。
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ChildSpec:
    name: str
    argv: "list[str]"
    cwd: "str | None" = None
    env: "dict | None" = None
    # None = クラッシュ検知のみ（プロセス終了で死亡判定）。設定すると鮮度でハングも検知する
    # （呼び出し側が status.json 等の実ファイルを読んで判定する — supervisor は形式を知らない）。
    is_healthy: "Callable[[], bool] | None" = None
    stop_grace: float = 10.0          # SIGTERM → SIGKILL までの猶予秒
    backoff_base: float = 5.0         # 連続死の指数バックオフ初項
    backoff_max: float = 300.0
    quarantine_after: int = 5         # quarantine_window 内の連続死がこれを超えたら隔離
    quarantine_window: float = 600.0


@dataclass
class _ChildState:
    spec: ChildSpec
    proc: "subprocess.Popen | None" = None
    deaths: "list[float]" = field(default_factory=list)
    quarantined: bool = False
    next_restart_at: float = 0.0
    # 計画停止中（親が意図して止めている）。`check_health` は再起動しない。
    # 隔離（quarantined）と分けるのが要点——隔離は「壊れているので触らない」、こちらは
    # 「今は動かさないと決めた」で、条件が戻れば親がそのまま再開する。
    paused: bool = False


class Supervisor:
    """1 ノード分の子プロセス群を監督する。`popen`/`now` は差し替え可能（テスト用）。"""

    def __init__(self, *, popen=subprocess.Popen, now: "Callable[[], float]" = time.time,
                 on_event: "Callable[[str, str], None] | None" = None):
        self._popen = popen
        self._now = now
        self._on_event = on_event or (lambda name, event: None)
        self._children: "dict[str, _ChildState]" = {}

    def add(self, spec: ChildSpec) -> None:
        self._children[spec.name] = _ChildState(spec)

    def names(self) -> "list[str]":
        return list(self._children)

    def start(self, name: str) -> None:
        st = self._children[name]
        if st.proc is not None and st.proc.poll() is None:
            return   # 既に生きている（二重起動しない）
        st.proc = self._popen(st.spec.argv, cwd=st.spec.cwd, env=st.spec.env)
        self._on_event(name, "started")

    def _record_death(self, st: _ChildState) -> None:
        now = self._now()
        st.deaths = [t for t in st.deaths if now - t <= st.spec.quarantine_window]
        st.deaths.append(now)
        if len(st.deaths) > st.spec.quarantine_after:
            st.quarantined = True
            self._on_event(st.spec.name, "quarantined")
            return
        backoff = min(st.spec.backoff_base * (2 ** (len(st.deaths) - 1)), st.spec.backoff_max)
        st.next_restart_at = now + backoff
        self._on_event(st.spec.name, "died")

    def _kill(self, st: _ChildState, *, escalate: bool) -> None:
        if st.proc is None or st.proc.poll() is not None:
            return
        try:
            st.proc.terminate()
        except OSError:
            return
        if not escalate:
            return
        deadline = self._now() + st.spec.stop_grace
        while self._now() < deadline and st.proc.poll() is None:
            time.sleep(0.05)
        if st.proc.poll() is None:
            try:
                st.proc.kill()
            except OSError:
                pass

    def check_health(self) -> None:
        """1 巡分の健康診断: クラッシュ/ハングを検知し、隔離されていない子は backoff 後に
        再起動する。`resident.scheduler` の tick から呼ぶ想定（呼び出し間隔がそのまま
        ハング検知の粒度になる）。"""
        now = self._now()
        for name, st in self._children.items():
            if st.quarantined or st.paused:
                continue
            dead = st.proc is None or st.proc.poll() is not None
            if not dead and st.spec.is_healthy is not None and not st.spec.is_healthy():
                self._on_event(name, "hung")
                self._kill(st, escalate=True)
                dead = True
            if dead:
                if st.proc is not None:
                    self._record_death(st)
                    st.proc = None
                if not st.quarantined and now >= st.next_restart_at:
                    self.start(name)

    def stop(self, name: str) -> None:
        """親→子への graceful 停止指示（SIGTERM → 猶予 → SIGKILL）。子が自分で終了を
        決める経路の代わり — 止めるかどうかは常に親（このメソッドの呼び出し側）が決める。"""
        st = self._children[name]
        self._kill(st, escalate=True)
        st.proc = None

    def stop_all(self) -> None:
        for name in list(self._children):
            self.stop(name)

    def pause(self, name: str) -> None:
        """計画停止: 止めたうえで `check_health` の再起動対象から外す（夜間停止など）。

        `stop()` だけでは止まらない——`check_health` は「proc が無い＝死んだ」と読んで
        即座に再起動するため、止めた側が意図を残さないと止め続けられない。
        **死亡回数には数えない**（計画停止を繰り返すと隔離に達してしまう）。"""
        st = self._children[name]
        if not st.paused:
            st.paused = True
            self._on_event(name, "paused")
        self._kill(st, escalate=True)
        st.proc = None

    def resume(self, name: str) -> None:
        """計画停止の解除。次の `check_health` が通常どおり起動する（backoff も待たない
        ——計画停止は失敗ではないので、再開を遅らせる理由が無い）。"""
        st = self._children[name]
        if st.paused:
            st.paused = False
            st.next_restart_at = 0.0
            self._on_event(name, "resumed")

    def paused_names(self) -> "list[str]":
        return [n for n, st in self._children.items() if st.paused]

    def unquarantine(self, name: str) -> None:
        """隔離解除（原因修正後に人/doctor が呼ぶ）。"""
        st = self._children[name]
        st.quarantined = False
        st.deaths = []

    def status(self) -> dict:
        return {name: {"alive": st.proc is not None and st.proc.poll() is None,
                       "quarantined": st.quarantined, "deaths": len(st.deaths),
                       "paused": st.paused}
               for name, st in self._children.items()}


def graceful_shutdown(supervisor: Supervisor, *,
                      release_claims: "Callable[[], None] | None" = None,
                      release_lease: "Callable[[], None] | None" = None,
                      announce_away: "Callable[[], None] | None" = None,
                      final_push: "Callable[[], None] | None" = None,
                      on_step: "Callable[[str], None] | None" = None) -> None:
    """設計 §4.2 の graceful 停止シーケンス: 全子の停止 → claims 解放 → controller lease
    解放 → away 宣言（amigos）→ 最終 push。各ステップは省略可（None なら skip）。

    resident はこれらステップの実装を持たない — 呼び出し側が agent_project /
    agent-amigos / 板クライアントの実関数を注入する（設計 R10「入口を絞る」— 内部部品を
    横断依存させない）。板 status への away 書き込みは announce_away 側の実装に含める想定
    （専用の別ステップは現状の契約に無い — 設計 §5 参照）。"""
    def _step(label: str, fn) -> None:
        if fn is None:
            return
        if on_step:
            on_step(label)
        fn()

    supervisor.stop_all()
    _step("release_claims", release_claims)
    _step("release_lease", release_lease)
    _step("announce_away", announce_away)
    _step("final_push", final_push)
