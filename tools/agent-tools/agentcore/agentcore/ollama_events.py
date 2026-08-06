"""ローカル推論の進捗イベント（JSONL）— 「遅い」と「死んだ」を区別するための証跡。

設計: docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md §0.1 R2 / F-2 補遺 3。

バックアップ実行系（クラウド CLI が使えないときに作業を止めないための構成）では、
1 呼び出しが数十分かかることが**正常**である。よって失敗検知を壁時計に置けない
——置くと正常な実行が殺される。代わりに、

1. ループ核が進捗イベントを**追記のみの JSONL** へ常時書き（= その時刻に進んでいた証拠）、
2. 人は TUI / `--follow` で、プログラムは `read_status()` でそれを読む、
3. 打ち切りは**無進捗（stall）**でだけ行う、

という三層にする。この 3 つのうち 1・3 の土台がこのモジュール。

イベントは 1 行 1 JSON。共通キーは `ts`（Unix 秒・float）と `kind`。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 進捗が無いときでも「生きている」ことを示すため、この間隔で heartbeat を打つ。
# read_status() の生存判定もこの値を基準にする（一致させるためここに 1 つ置く）。
HEARTBEAT_INTERVAL_SEC = 5.0

# 実行中とみなす最終イベントからの猶予（heartbeat の取りこぼし 2 回分 + 余裕）。
_ALIVE_GRACE_SEC = HEARTBEAT_INTERVAL_SEC * 3 + 5.0

# 進捗があるときの llm_progress の間引き間隔（トークン毎に書くとログが膨れる）。
PROGRESS_INTERVAL_SEC = 1.0

# read_status() が末尾から読む量。ログ全体を読まないための上限。
_TAIL_BYTES = 65536

_TERMINAL_KINDS = {"run_end"}


def log_dir() -> Path:
    """JSONL の置き場（`AGENT_OLLAMA_LOG_DIR` で移せる）。"""
    raw = os.environ.get("AGENT_OLLAMA_LOG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".agents" / "logs" / "ollama"


def new_log_path(model: str = "", now: "float | None" = None) -> Path:
    """新しいログの所在。同一秒に複数プロセスが始まっても衝突しないよう pid を混ぜる。"""
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now if now is not None else time.time()))
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (model or "model"))
    return log_dir() / f"{stamp}-{os.getpid()}-{safe}.jsonl"


def latest_log_path() -> "Path | None":
    """最も新しいログ（`--status` / `--follow` の引数省略時に使う）。"""
    try:
        files = [p for p in log_dir().iterdir() if p.is_file() and p.suffix == ".jsonl"]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


class EventLog:
    """イベントの発行口。JSONL への追記と、任意のシンク（TUI 等）への配送を行う。

    **描画を知らない**のが要点。ループ核はこのクラスへ emit するだけで、ヘッドレス／
    TUI／`--follow` のどれで見られているかを意識しない（F-2 のシンク分離）。
    書き込みに失敗しても推論は続ける（証跡はあくまで観測手段で、実行の条件ではない）。
    """

    def __init__(self, path: "str | Path | None" = None, sink=None) -> None:
        self.path = Path(path).expanduser() if path else None
        self.sink = sink
        self._fh = None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
            except OSError:
                self._fh = None

    def emit(self, kind: str, **fields) -> dict:
        event = {"ts": round(time.time(), 3), "kind": str(kind)}
        event.update(fields)
        if self._fh is not None:
            try:
                self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                self._fh.flush()
            except (OSError, ValueError):
                pass
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:
                # 描画側の失敗で推論を止めない。
                pass
        return event

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _tail_events(path: Path, max_bytes: int = _TAIL_BYTES) -> "list[dict]":
    """末尾から max_bytes だけ読んで、解釈できた行を古い順に返す。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # 途中で切れた行は捨てる
            raw = fh.read()
    except OSError:
        return []
    events: "list[dict]" = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def read_status(path: "str | Path | None" = None, now: "float | None" = None) -> dict:
    """ログから「いまどこにいるか」を機械可読で返す（`agent-ollama --status`）。

    外部監視（agent-dashboard の doctor 等）がこれを読めば、**壁時計で長く沈黙している
    実行を異常と決めつけずに済む**——`state=running` かつ `since_last_event_sec` が
    小さければ、それは「遅い」であって「固まった」ではない。
    """
    now = time.time() if now is None else now
    target = Path(path).expanduser() if path else latest_log_path()
    if target is None or not Path(target).is_file():
        return {"state": "unknown", "alive": False, "log": str(target) if target else ""}

    events = _tail_events(Path(target))
    if not events:
        return {"state": "unknown", "alive": False, "log": str(target)}

    status: dict = {
        "log": str(target), "state": "running", "phase": "", "round": 0,
        "model": "", "mode": "", "tokens_in": 0, "tokens_out": 0, "tokens_per_sec": 0.0,
        "context_used": 0, "context_limit": 0, "context_source": "unknown",
    }
    last_progress_at = 0.0
    for event in events:
        kind = str(event.get("kind") or "")
        ts = float(event.get("ts") or 0.0)
        if kind == "run_start":
            status["model"] = str(event.get("model") or "")
            status["mode"] = str(event.get("mode") or "")
            status["started_at"] = ts
        if "round" in event:
            try:
                status["round"] = int(event.get("round") or 0)
            except (TypeError, ValueError):
                pass
        if "phase" in event:
            status["phase"] = str(event.get("phase") or "")
        for key in ("tokens_in", "tokens_out", "context_used", "context_limit"):
            if event.get(key) is not None:
                try:
                    status[key] = int(event[key])
                except (TypeError, ValueError):
                    pass
        for key in ("tokens_per_sec", "context_pct"):
            if event.get(key) is not None:
                try:
                    status[key] = float(event[key])
                except (TypeError, ValueError):
                    pass
        if event.get("context_source"):
            status["context_source"] = str(event["context_source"])
        # 「進捗」= トークンが出た / ツールが動いた。heartbeat は進捗ではない
        # （生存の証拠であって、前に進んだ証拠ではない——ここを混ぜると stall を見逃す）。
        if kind in ("llm_progress", "llm_end", "tool_exec", "tool_result", "round_start"):
            last_progress_at = max(last_progress_at, ts)
        if kind in _TERMINAL_KINDS:
            status["state"] = str(event.get("status") or "done")
        elif kind == "stall":
            status["state"] = "stalled"

    last_event_at = float(events[-1].get("ts") or 0.0)
    status["last_event_at"] = last_event_at
    status["last_progress_at"] = last_progress_at or last_event_at
    status["since_last_event_sec"] = round(max(0.0, now - last_event_at), 3)
    status["since_last_progress_sec"] = round(max(0.0, now - status["last_progress_at"]), 3)
    if status.get("started_at"):
        status["elapsed_sec"] = round(max(0.0, now - float(status["started_at"])), 3)
    status["alive"] = bool(
        status["state"] == "running" and status["since_last_event_sec"] <= _ALIVE_GRACE_SEC)
    return status


def follow_events(path: "str | Path", *, from_start: bool = True, poll_sec: float = 0.3,
                  stop=None):
    """JSONL を追尾して 1 件ずつ yield する（`--follow` の土台）。

    まだ存在しないファイルも待てる（ヘッドレス実行の開始前にアタッチできる）。
    `stop` は「そろそろ止めるか」を返す callable（None なら Ctrl-C まで続ける）。
    """
    target = Path(path).expanduser()
    offset = 0
    if not from_start and target.is_file():
        try:
            offset = target.stat().st_size
        except OSError:
            offset = 0
    buffer = ""
    while stop is None or not stop():
        try:
            with target.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        except OSError:
            chunk = b""
        if chunk:
            buffer += chunk.decode("utf-8", "replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
                    if str(event.get("kind") or "") in _TERMINAL_KINDS:
                        return
        else:
            time.sleep(poll_sec)
