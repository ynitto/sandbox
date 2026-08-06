"""デバッグ用の行指向ビュー — ラウンド毎の動きを見る / 固まっていないことを確かめる。

設計: docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md F-2 の実装形態。

**全画面（alternate screen）にしない**のが最大の制約。agent-loop / kiro-loop は
tmux の `send-keys` で入力を送り `capture-pane` で画面を読むため、全画面へ切り替えると
向こうからは何も見えなくなる。よってここでは、

- イベントは通常の行として**素直にスクロールさせる**（scrollback に残る = capture-pane で読める）
- 現在地（ラウンド・経過・最終進捗からの秒数・tok/s）だけを**最下段 1 行**に貼り付け、
  カーソル移動で更新する

という形にする。rich があれば色付けにだけ使い、無ければ素の ANSI で同じ情報を出す
（rich をハード依存にしない——エンジン側 zipapp の「標準ライブラリのみ」を壊さないため）。
"""
from __future__ import annotations

import os
import sys
import time

from agentcore import ollama_events, ollama_skills

try:  # 任意依存。install.sh --with-rich で zipapp へ同梱できる。
    from rich.console import Console as _RichConsole
except Exception:  # pragma: no cover - rich 未導入が既定
    _RichConsole = None

_STYLES = {
    "run_start": "bold",
    "round_start": "dim",
    "llm_end": "cyan",
    "tool_exec": "yellow",
    "skill_load": "magenta",
    "stall": "bold red",
    "run_end": "bold",
    "error": "red",
    "context_warn": "yellow",
    "context_exhausted": "bold red",
}


def _tok(count) -> str:
    """トークン数を短く（4200 → 4.2k）。ステータス行は 1 行に収めたい。"""
    try:
        value = int(count or 0)
    except (TypeError, ValueError):
        return "0"
    if value < 1000:
        return str(value)
    return f"{value / 1000:.1f}k"


def context_text(used, limit, pct=None) -> str:
    """`ctx 4.2k/8k (52%)`。上限が分からないときは使用量だけを出す。"""
    used_text = _tok(used)
    if not limit:
        return f"ctx {used_text}"
    text = f"ctx {used_text}/{_tok(limit)}"
    if pct is not None:
        text += f" ({pct:.0f}%)"
    return text


def _dur(sec: float) -> str:
    sec = max(0.0, float(sec or 0.0))
    if sec < 60:
        return f"{sec:.1f}s"
    return f"{int(sec // 60)}m{int(sec % 60):02d}s"


def _hhmmss(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts or time.time()))


def event_line(event: dict) -> str:
    """イベント 1 件を 1 行へ。heartbeat / llm_progress は None（ステータス行だけで扱う）。

    毎秒流れる進捗をスクロールさせるとログが埋まり、肝心のラウンド遷移が読めなくなる。
    """
    kind = str(event.get("kind") or "")
    ts = _hhmmss(float(event.get("ts") or 0))
    rnd = event.get("round") or 0
    tag = f"R{rnd}" if rnd else "--"
    if kind in ("llm_heartbeat", "llm_progress", "llm_start"):
        return ""
    if kind == "run_start":
        return (f"{ts} ▶ {event.get('model', '')} mode={event.get('mode', '')} "
                f"log={event.get('log', '')}")
    if kind == "skill_load":
        return f"{ts} {tag} + スキル {event.get('name', '')}（{event.get('chars', 0)} 文字）"
    if kind == "round_start":
        return f"{ts} {tag} 開始（最大 {event.get('rounds_max', '?')}）"
    if kind == "llm_end":
        line = (f"{ts} {tag} llm {_dur(event.get('duration_sec', 0))} "
                f"in={event.get('tokens_in', 0)}tk out={event.get('tokens_out', 0)}tk "
                f"({event.get('tokens_per_sec', 0)} tok/s)")
        if event.get("context_used"):
            line += "  " + context_text(event.get("context_used"), event.get("context_limit"),
                                        event.get("context_pct"))
        return line
    if kind == "context_warn":
        return (f"{ts} {tag} ⚠ 文脈が上限に近づいています: "
                f"{context_text(event.get('context_used'), event.get('context_limit'), event.get('context_pct'))}"
                f"（{event.get('context_source', '')}）")
    if kind == "context_exhausted":
        return (f"{ts} {tag} ⚠ 文脈の残りが足りないため打ち切りました: "
                f"{context_text(event.get('context_used'), event.get('context_limit'), event.get('context_pct'))}")
    if kind == "tool_exec":
        command = str(event.get("command") or "").replace("\n", " ⏎ ")
        return f"{ts} {tag} $ {command}"
    if kind == "tool_result":
        return (f"{ts} {tag} → exit {event.get('exit_code', '?')} "
                f"({_dur(event.get('duration_sec', 0))}, {event.get('output_chars', 0)} 文字)")
    if kind == "round_end":
        return f"{ts} {tag} 終了（{event.get('reason', '')}）"
    if kind == "stall":
        return (f"{ts} {tag} ⚠ 無進捗で打ち切り: {event.get('phase', '')} が "
                f"{event.get('waiting_sec', 0)}s（上限 {event.get('limit_sec', 0)}s）")
    if kind == "run_end":
        return (f"{ts} ■ {event.get('status', '')} rounds={event.get('rounds', 0)} "
                f"in={event.get('tokens_in', 0)}tk out={event.get('tokens_out', 0)}tk "
                f"{_dur(event.get('duration_sec', 0))}")
    if kind == "error":
        return f"{ts} {tag} ✖ {event.get('message', '')}"
    return f"{ts} {tag} {kind}"


class Renderer:
    """イベントを受けて描くだけの薄いビュー（ループ核とは疎結合）。"""

    def __init__(self, out=None, use_rich: "bool | None" = None) -> None:
        self.out = out or sys.stdout
        self.tty = bool(getattr(self.out, "isatty", lambda: False)())
        if use_rich is None:
            use_rich = _RichConsole is not None and os.environ.get("AGENT_OLLAMA_NO_RICH") != "1"
        self.console = _RichConsole(file=self.out, highlight=False, soft_wrap=True) \
            if (use_rich and _RichConsole is not None) else None
        self._status = ""
        self._phase = ""
        self._round = 0
        self._rounds_max = 0
        self._tps = 0.0
        self._tokens_out = 0
        self._waiting = 0.0
        self._started = time.time()
        self._running = False
        self._ctx_used = 0
        self._ctx_limit = 0
        self._ctx_pct = None

    # -- 出力の最小単位 ---------------------------------------------------
    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def _clear_status(self) -> None:
        if self.tty and self._status:
            self._write("\r\x1b[K")

    def _draw_status(self) -> None:
        if not self.tty or not self._running:
            return
        self._write("\r\x1b[K" + self._status_text())

    def _status_text(self) -> str:
        parts = []
        if self._round:
            parts.append(f"R{self._round}/{self._rounds_max or '?'}")
        parts.append(self._phase or "…")
        parts.append(f"経過 {_dur(time.time() - self._started)}")
        if self._waiting:
            parts.append(f"最終進捗 {_dur(self._waiting)}前")
        if self._tps:
            parts.append(f"{self._tps} tok/s")
        if self._tokens_out:
            parts.append(f"out={self._tokens_out}tk")
        if self._ctx_used:
            parts.append(context_text(self._ctx_used, self._ctx_limit, self._ctx_pct))
        self._status = "  ".join(parts)
        return self._status

    def line(self, text: str, style: str = "") -> None:
        """スクロールする 1 行を出す（ステータス行は退避して貼り直す）。"""
        if not text:
            return
        self._clear_status()
        if self.console is not None and style:
            self.console.print(text, style=style)
        else:
            self._write(text + "\n")
        self._draw_status()

    # -- イベントのシンク -------------------------------------------------
    def event(self, event: dict) -> None:
        kind = str(event.get("kind") or "")
        if kind == "run_start":
            self._running = True
            self._started = float(event.get("ts") or time.time())
        if event.get("round"):
            try:
                self._round = int(event["round"])
            except (TypeError, ValueError):
                pass
        if event.get("rounds_max"):
            try:
                self._rounds_max = int(event["rounds_max"])
            except (TypeError, ValueError):
                pass
        if event.get("phase"):
            self._phase = str(event["phase"])
        if event.get("tokens_per_sec") is not None:
            try:
                self._tps = float(event["tokens_per_sec"])
            except (TypeError, ValueError):
                pass
        if event.get("tokens_out") is not None:
            try:
                self._tokens_out = int(event["tokens_out"])
            except (TypeError, ValueError):
                pass
        self._waiting = float(event.get("waiting_sec") or 0.0)
        if event.get("context_used") is not None:
            try:
                self._ctx_used = int(event["context_used"])
                self._ctx_limit = int(event.get("context_limit") or 0)
                self._ctx_pct = (float(event["context_pct"])
                                 if event.get("context_pct") is not None else None)
            except (TypeError, ValueError):
                pass

        self.line(event_line(event), _STYLES.get(kind, ""))
        if kind in ("llm_heartbeat", "llm_progress"):
            self._draw_status()
        if kind == "run_end":
            self.finish()

    def finish(self) -> None:
        """ステータス行を片付ける（本文をそのまま貼れる状態に戻す）。"""
        if self.tty and self._running:
            self._write("\r\x1b[K")
        self._running = False


def follow(path: "str | None" = None, out=None, from_start: bool = True) -> int:
    """進捗ログを追尾表示する（`agent-ollama --follow`）。

    **ヘッドレスで走っている最中の実行へ後からアタッチする**のが本命の使い方。
    agent-flow の worker が回している最中でも、そのログを指すだけで中を覗ける。
    """
    out = out or sys.stdout
    target = path or ollama_events.latest_log_path()
    if target is None:
        print("追尾できるログがありません（実行が始まってから指定してください）。",
              file=sys.stderr)
        return 1
    renderer = Renderer(out=out)
    print(f"追尾: {target}（Ctrl-C で終了）", file=sys.stderr)
    try:
        for event in ollama_events.follow_events(target, from_start=from_start):
            renderer.event(event)
    except KeyboardInterrupt:
        renderer.finish()
        return 130
    renderer.finish()
    return 0


_HELP = """\
ローカルコマンド（LLM へは送りません）:
  /skills          読めるスキルの一覧
  /tools on|off    ツール実行ループの切り替え
  /think on|off    思考モードの切り替え
  /model <name>    モデルの切り替え
  /ctx             直近の文脈使用量（使用トークン / 上限 / 割合）
  /status          いまの進捗（JSON）
  /help            この一覧
  /quit            終了
それ以外の行はプロンプトとして送ります（先頭の /<スキル名> は展開されます）。"""


def repl(runner, *, model: str, tools: bool, think: "bool | None" = None,
         out=None, in_=None) -> int:
    """デバッグ用の対話ループ（`agent-ollama --tui`）。

    行指向に徹する（`input()` 相当の 1 行読み）。tmux `send-keys` は「文字列 + Enter」を
    送るだけなので、この形なら agent-dashboard の対話診断・agent-loop の定期送信の
    どちらからもそのまま操作できる。

    `runner(prompt, *, model, tools, think, renderer)` が 1 回の実行を担う
    （実体は adapter 側。ここは描画と入力だけを持つ）。
    """
    out = out or sys.stdout
    in_ = in_ or sys.stdin
    print(f"agent-ollama TUI — model={model} tools={'on' if tools else 'off'} "
          f"think={'既定' if think is None else ('on' if think else 'off')}", file=out)
    print("'/help' でローカルコマンド一覧。", file=out)
    while True:
        out.write("> ")
        out.flush()
        line = in_.readline()
        if not line:                       # EOF（パイプ入力の終わり）
            return 0
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in ("/quit", "/exit"):
            return 0
        if lowered == "/help":
            print(_HELP, file=out)
            continue
        if lowered == "/skills":
            found = ollama_skills.list_skills()
            if not found:
                print("スキルが見つかりません（探索先: "
                      + ", ".join(str(d) for d in ollama_skills.skill_dirs()) + "）", file=out)
            for name, path in found:
                print(f"  /{name}  {path}", file=out)
            continue
        if lowered.startswith("/tools"):
            arg = lowered.replace("/tools", "").strip()
            tools = arg in ("on", "true", "1", "yes") if arg else not tools
            print(f"tools={'on' if tools else 'off'}", file=out)
            continue
        if lowered.startswith("/think"):
            arg = lowered.replace("/think", "").strip()
            think = True if arg in ("on", "true", "1", "yes") else (
                False if arg in ("off", "false", "0", "no") else None)
            print(f"think={'既定' if think is None else ('on' if think else 'off')}", file=out)
            continue
        if lowered.startswith("/model"):
            arg = text[len("/model"):].strip()
            if arg:
                model = arg
            print(f"model={model}", file=out)
            continue
        if lowered == "/ctx":
            snap = ollama_events.read_status()
            if not snap.get("context_used"):
                print("まだ文脈使用量を観測していません（1 回実行すると出ます）。", file=out)
            else:
                print(f"  {context_text(snap.get('context_used'), snap.get('context_limit'), snap.get('context_pct'))}"
                      f"  実測元={snap.get('context_source', '')}", file=out)
            continue
        if lowered == "/status":
            import json as _json
            print(_json.dumps(ollama_events.read_status(), ensure_ascii=False), file=out)
            continue

        renderer = Renderer(out=out)
        try:
            body = runner(text, model=model, tools=tools, think=think, renderer=renderer)
        except KeyboardInterrupt:
            renderer.finish()
            print("（中断しました）", file=out)
            continue
        except Exception as exc:
            renderer.finish()
            print(f"✖ {exc}", file=out)
            continue
        renderer.finish()
        if body:
            print(body.rstrip("\n"), file=out)
