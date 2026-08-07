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

## 入力行の編集（矢印キー・ショートカット）

素の `readline()` で 1 行読むと、矢印キーは `^[[A` のようなエスケープ列が**そのまま
本文へ混ざる**。打ち間違いを直す手段が Backspace しか無く、長いプロンプトを書く
デバッグ用途では実用にならない。そこで標準ライブラリの `readline` を噛ませて、
左右キーでのカーソル移動・上下キーでの履歴・emacs 風の編集キー・Ctrl-R の履歴検索・
Tab 補完を効かせる。

`readline` を選ぶのは**行指向のまま**という制約を壊さないからでもある。curses 系の
全画面に行くと tmux `capture-pane` から中身が読めなくなるが、readline は「いま編集中の
1 行」を書き換えるだけで、確定した行は通常どおりスクロールへ流れる。よって
agent-loop / agent-dashboard から見た画面は今までと同じに保てる。

有効にするのは**本物の端末で対話しているときだけ**（`in_`/`out` が tty で、かつ実体が
sys.stdin/sys.stdout のとき）。パイプ入力・テスト・ログへのリダイレクトでは素の 1 行読み
へ落ちる——非 tty で編集用のエスケープを吐くと、出力を読む側（capture-pane・テスト）が
壊れるため。`AGENT_OLLAMA_NO_READLINE=1` で明示的に切ることもできる。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from agentcore import ollama_events, ollama_skills

try:  # 環境によっては欠ける（readline 無しでビルドされた python 等）。
    import readline as _readline
except Exception:  # pragma: no cover - 通常は標準で入っている
    _readline = None

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
  /keys            使えるキー操作の一覧
  /help            この一覧
  /quit            終了
それ以外の行はプロンプトとして送ります（先頭の /<スキル名> は展開されます）。"""

_KEYS = """\
キー操作（本物の端末で対話しているときだけ効きます）:
  ← →              カーソル移動        Ctrl-A / Ctrl-E  行頭 / 行末へ
  ↑ ↓              履歴を辿る          Ctrl-R           履歴を遡って検索
  Tab              補完（ローカルコマンド・スキル名・on|off）
  Ctrl-W           直前の語を削除      Ctrl-U / Ctrl-K  カーソルより前 / 後ろを削除
  Ctrl-L           画面をクリア        Ctrl-C           入力中の行を捨てる（終了しない）
  Ctrl-D           終了（空行のとき）
履歴はセッションをまたいで残ります: {history}
キー割り当ては ~/.inputrc で変えられます（readline の設定がそのまま効きます）。
AGENT_OLLAMA_NO_READLINE=1 で行編集を切れます（素の 1 行読みに戻ります）。"""

# ローカルコマンド（Tab 補完の候補。`/help` の一覧と揃える）。
_LOCAL_COMMANDS = ("/ctx", "/exit", "/help", "/keys", "/model", "/quit", "/skills",
                   "/status", "/think", "/tools")
_ONOFF_COMMANDS = ("/tools", "/think")
_HISTORY_LIMIT = 1000


def history_path() -> Path:
    """入力履歴の置き場（`AGENT_OLLAMA_HISTORY` で移せる）。

    共通ホーム（`~/.agents`。旧 `~/.agent` の判定は agentcli と同じ規則）の下に置く。
    ログ（`~/.agents/logs/ollama`）とは分ける——履歴は実行の証跡ではなく人の入力で、
    ログの gc に巻き込んで消していいものではない。
    """
    raw = os.environ.get("AGENT_OLLAMA_HISTORY", "").strip()
    if raw:
        return Path(raw).expanduser()
    from agentcore.agentcli import _agents_home  # 新旧ホーム判定の正典（複製しない）
    return _agents_home() / "ollama" / "tui-history"


def completions(buffer: str, text: str) -> "list[str]":
    """Tab 補完の候補。`buffer` は編集中の行全体、`text` は補完しようとしている語。

    出すのは**先頭の語がスラッシュで始まるときだけ**にする。プロンプト本文の途中に
    出てくる `/`（パスや日付）で候補を出すと、Tab がただの邪魔になるため。
    """
    head = buffer.lstrip()
    for name in _ONOFF_COMMANDS:
        if head.startswith(name) and head[len(name):len(name) + 1] in (" ", "\t"):
            return [value for value in ("on", "off") if value.startswith(text)]
    if not text.startswith("/") or head[:len(text)] != text:
        return []
    names = set(_LOCAL_COMMANDS)
    try:
        # スキルは `/<名前>` で展開されるので、ローカルコマンドと同じ土俵で補完できる。
        names.update("/" + name for name, _path in ollama_skills.list_skills())
    except Exception:
        pass  # スキルが読めなくても補完自体は動かす
    return sorted(name for name in names if name.startswith(text))


class LineReader:
    """入力 1 行の読み取り。端末なら readline で編集を効かせ、そうでなければ素で読む。

    「素で読む」側を残すのが要点。テスト・パイプ入力・非 tty では readline を**触らない**
    ——編集用のエスケープを吐くと、出力を読む側（tmux capture-pane・テストの比較）が壊れる。
    """

    def __init__(self, out, in_, enabled: "bool | None" = None) -> None:
        self.out = out
        self.in_ = in_
        if enabled is None:
            enabled = (
                _readline is not None
                and os.environ.get("AGENT_OLLAMA_NO_READLINE") != "1"
                # input() が readline を通すのは実体が sys.stdin/sys.stdout のときだけ。
                # 差し替えられている（テスト等）なら素の経路へ落とす。
                and in_ is sys.stdin and out is sys.stdout
                and bool(getattr(in_, "isatty", lambda: False)())
                and bool(getattr(out, "isatty", lambda: False)()))
        self.enabled = bool(enabled)
        self._prev_completer = None
        self._history = history_path()
        if self.enabled:
            self._setup()

    # -- readline の構成 ---------------------------------------------------
    def _setup(self) -> None:
        rl = _readline
        try:
            self._prev_completer = rl.get_completer()
            rl.set_completer(self._complete)
            # 語の区切りを空白だけにする。既定の区切りは `/` を含むので、
            # そのままだと `/he` の補完対象が `he` になって候補が出ない。
            rl.set_completer_delims(" \t\n")
            # libedit（macOS 標準の readline 互換実装）は bind 構文が違う。
            if "libedit" in (rl.__doc__ or ""):
                rl.parse_and_bind("bind ^I rl_complete")
            else:
                rl.parse_and_bind("tab: complete")
            # bracketed paste は貼り付け時に `\e[?2004h` を画面へ出す。全画面化では
            # ないので実害は小さいが、tmux capture-pane で ready_pattern（`^\s*>\s*$`）を
            # 突き合わせる側から見ると余計な文字なので、こちらからは出さない。
            rl.parse_and_bind("set enable-bracketed-paste off")
            try:
                rl.read_history_file(str(self._history))
            except (OSError, ValueError):
                pass  # 初回は履歴が無い。壊れていても新しく書き直せばよい
            rl.set_history_length(_HISTORY_LIMIT)
        except Exception:
            # 端末や readline の実装差で構成に失敗しても、対話そのものは続けられる
            # ようにする（編集が効かないだけで、入力は読める）。
            self.enabled = False

    def _complete(self, text: str, state: int):
        if state == 0:
            try:
                self._matches = completions(_readline.get_line_buffer(), text)
            except Exception:
                self._matches = []
        try:
            return self._matches[state]
        except (AttributeError, IndexError):
            return None

    # -- 読み取り ----------------------------------------------------------
    def read(self, prompt: str) -> "str | None":
        """1 行読む。`None` は EOF（セッションの終わり）。改行は含めないことがある。"""
        if not self.enabled:
            self.out.write(prompt)
            self.out.flush()
            line = self.in_.readline()
            return None if not line else line
        try:
            return input(prompt)
        except EOFError:                    # Ctrl-D
            self.out.write("\n")
            self.out.flush()
            return None

    def close(self) -> None:
        """履歴を残し、readline の状態を元へ戻す。失敗は無視する（終了を妨げない）。"""
        if not self.enabled:
            return
        try:
            self._history.parent.mkdir(parents=True, exist_ok=True)
            _readline.write_history_file(str(self._history))
        except (OSError, ValueError):
            pass
        try:
            _readline.set_completer(self._prev_completer)
        except Exception:
            pass


def repl(runner, *, model: str, tools: bool, think: "bool | None" = None,
         out=None, in_=None) -> int:
    """デバッグ用の対話ループ（`agent-ollama --tui`）。

    行指向に徹する（1 行読んで 1 回実行）。tmux `send-keys` は「文字列 + Enter」を
    送るだけなので、この形なら agent-dashboard の対話診断・agent-loop の定期送信の
    どちらからもそのまま操作できる。人が直接叩くときは readline が噛んで矢印キー・
    履歴・Tab 補完が効く（`LineReader`。非 tty では素の 1 行読みへ落ちる）。

    `runner(prompt, *, model, tools, think, renderer)` が 1 回の実行を担う
    （実体は adapter 側。ここは描画と入力だけを持つ）。
    """
    out = out or sys.stdout
    in_ = in_ or sys.stdin
    reader = LineReader(out, in_)
    print(f"agent-ollama TUI — model={model} tools={'on' if tools else 'off'} "
          f"think={'既定' if think is None else ('on' if think else 'off')}", file=out)
    print("'/help' でローカルコマンド一覧"
          + ("、'/keys' でキー操作。" if reader.enabled else "。"), file=out)
    try:
        return _loop(reader, runner, model=model, tools=tools, think=think, out=out)
    finally:
        reader.close()


def _loop(reader, runner, *, model: str, tools: bool, think: "bool | None", out) -> int:
    while True:
        try:
            line = reader.read("> ")
        except KeyboardInterrupt:
            # Ctrl-C は「いま書いている行を捨てる」であって終了ではない（shell と同じ）。
            # 実行中の Ctrl-C は下の runner 側で拾う——止めたいのが入力か推論かで意味が違う。
            print("", file=out)
            continue
        if line is None:                   # EOF（Ctrl-D / パイプ入力の終わり）
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
        if lowered == "/keys":
            print(_KEYS.format(history=history_path()), file=out)
            if not reader.enabled:
                print("（いまは行編集が無効です: 端末ではないか、"
                      "AGENT_OLLAMA_NO_READLINE=1 が効いています）", file=out)
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
