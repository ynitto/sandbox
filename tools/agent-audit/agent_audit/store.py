"""audit ストア — 唯一の書き先（設計 §3）。

records/<YYYYMMDD>.jsonl（追記専用）・state.json（収集カーソル・処理済み管理）・
transcripts/・observations/・insights/・reports/。records に transcript 本文は入れない
（共有可能な層と不可の層の物理分離。C1）。
"""
from __future__ import annotations

import hashlib
import os
import time

from .util import append_jsonl, iter_jsonl, read_json, utc_day, write_json_atomic


def record_id(source: str, store: str, native_id: str) -> str:
    """冪等キー。同じ源泉・同じ実体は何度収集しても同じ id になる。"""
    h = hashlib.sha256(f"{source}::{store}::{native_id}".encode("utf-8")).hexdigest()
    return f"aud-{h[:16]}"


def observation_id(rec_id: str, index: int) -> str:
    h = hashlib.sha256(f"{rec_id}::{index}".encode("utf-8")).hexdigest()
    return f"obs-{h[:16]}"


def home_relative(path: str) -> str:
    """絶対パスをホーム相対（~/…）へ正規化する。ノード外へ絶対パスを出さない（C1）。"""
    if not path:
        return path
    p = os.path.abspath(os.path.expanduser(str(path)))
    home = os.path.expanduser("~")
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~/" + os.path.relpath(p, home).replace(os.sep, "/")
    return p


class Store:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.records_dir = os.path.join(self.root, "records")
        self.transcripts_dir = os.path.join(self.root, "transcripts")
        self.observations_dir = os.path.join(self.root, "observations")
        self.insights_dir = os.path.join(self.root, "insights")
        self.decisions_dir = os.path.join(self.root, "decisions")
        self.reports_dir = os.path.join(self.root, "reports")
        self.state_path = os.path.join(self.root, "state.json")
        self._state = None

    # -- state.json（収集カーソル・処理済み・実行時刻） ------------------------
    @property
    def state(self) -> dict:
        if self._state is None:
            self._state = read_json(self.state_path) or {}
        self._state.setdefault("cursors", {})
        self._state.setdefault("seen", {})        # record id → 1（収集済み。gc より長生き）
        self._state.setdefault("extracted", {})   # record id → 1（extract 済み）
        self._state.setdefault("last", {})        # 段 → epoch（gc / extract / distill）
        return self._state

    def save_state(self) -> None:
        if self._state is not None:
            write_json_atomic(self.state_path, self._state)

    def cursor(self, key: str):
        return self.state["cursors"].get(key)

    def set_cursor(self, key: str, value) -> None:
        self.state["cursors"][key] = value

    def last_run(self, stage: str) -> float:
        try:
            return float(self.state["last"].get(stage) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def note_run(self, stage: str, ts: "float | None" = None) -> None:
        self.state["last"][stage] = ts if ts is not None else time.time()

    # -- records --------------------------------------------------------------
    def has_record(self, rec_id: str) -> bool:
        return rec_id in self.state["seen"]

    def append_record(self, rec: dict) -> bool:
        """新規レコードだけ追記する。既収集（seen）なら False。
        seen は records の gc 後も残る——消したレコードを再収集して LLM 段へ
        再投入しないため（仕様書 §7）。"""
        rid = rec["id"]
        if self.has_record(rid):
            return False
        ts = rec.get("_epoch") or time.time()
        rec = {k: v for k, v in rec.items() if not k.startswith("_")}
        append_jsonl(os.path.join(self.records_dir, f"{utc_day(ts)}.jsonl"), rec)
        self.state["seen"][rid] = 1
        return True

    def iter_records(self, since_epoch: float = 0.0):
        """records/*.jsonl を日付ファイル順に読む（since はファイル名の日付で粗く枝刈り）。"""
        try:
            names = sorted(n for n in os.listdir(self.records_dir) if n.endswith(".jsonl"))
        except OSError:
            return
        floor = utc_day(since_epoch) if since_epoch > 0 else ""
        for name in names:
            if floor and name[:-6] < floor:
                continue
            yield from iter_jsonl(os.path.join(self.records_dir, name))

    # -- observations ---------------------------------------------------------
    def append_observation(self, obs: dict) -> None:
        append_jsonl(os.path.join(self.observations_dir, f"{utc_day(time.time())}.jsonl"), obs)

    def iter_observations(self):
        try:
            names = sorted(n for n in os.listdir(self.observations_dir) if n.endswith(".jsonl"))
        except OSError:
            return
        for name in names:
            yield from iter_jsonl(os.path.join(self.observations_dir, name))

    # -- insights -------------------------------------------------------------
    def write_insight(self, ins: dict) -> None:
        write_json_atomic(os.path.join(self.insights_dir, f"{ins['id']}.json"), ins)

    def iter_insights(self):
        try:
            names = sorted(n for n in os.listdir(self.insights_dir) if n.endswith(".json"))
        except OSError:
            return
        for name in names:
            data = read_json(os.path.join(self.insights_dir, name))
            if isinstance(data, dict):
                yield data

    # -- tuning decisions ----------------------------------------------------
    def write_decision(self, decision: dict) -> None:
        write_json_atomic(os.path.join(self.decisions_dir, f"{decision['id']}.json"), decision)

    def iter_decisions(self):
        try:
            names = sorted(n for n in os.listdir(self.decisions_dir) if n.endswith(".json"))
        except OSError:
            return
        for name in names:
            data = read_json(os.path.join(self.decisions_dir, name))
            if isinstance(data, dict):
                yield data
