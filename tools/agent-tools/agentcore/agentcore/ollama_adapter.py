"""agent-ollama — Ollama を agents/<名前>.json 契約へ合わせるアダプター。

設計: docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md（案 A / E / F-2）。

## これは何のためにあるか

**クラウドのエージェント CLI がガバナンスや予算の事情で使えなくなったときに、
agent-tools シリーズの作業を止めないためのバックアップ実行系**（同設計 §0.1）。
速度と品質は犠牲にしてよい。その代わり次の 2 つを満たす:

- **R1 止めない**: 契約（ヘッドレス / 読み取り専用 / 書き込み / 対話 / 実測 usage /
  エラー分類）に完全適合し、`agent_cli: ollama` の 1 行で全エンジンから使える。
- **R2 監視できる**: 1 呼び出しが数十分になるのが正常なので、「長い沈黙」を異常と
  扱わない証拠を出し続ける。打ち切りは壁時計ではなく**無進捗（stall）**でだけ行う。

## モード

| 起動 | 何をするか | 契約上の位置 |
|---|---|---|
| `agent-ollama <model>` | 単発 text → text（ツールなし） | 既定・`readonly: enforced` |
| `agent-ollama --tools <model>` | bash 1 ツールの最小ループ | `write_args` |
| `agent-ollama --tui <model>` | デバッグ用の対話ビュー | `interactive` |
| `agent-ollama --follow [LOG]` | 実行中のログを追尾表示 | 観測（LLM を呼ばない） |
| `agent-ollama --status [LOG]` | いまの進捗を 1 行 JSON で返す | 観測（LLM を呼ばない） |

標準ライブラリのみ（pip 依存なし。rich があれば TUI の色付けにだけ使う）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from agentcore import ollama_events, ollama_loop, ollama_skills

USAGE = """使い方: agent-ollama [オプション] <model>

  プロンプトは stdin。本文は stdout、診断と `@agent-usage` は stderr へ出る。

  実行モード:
    --tools               bash 1 つを道具にした実行ループ（書き込みモード）
    --tui                 デバッグ用の対話ビュー（行指向・tmux から操作できる）
    --follow [LOG]        進捗ログを追尾表示する（省略時は最新のログ）
    --status [LOG]        いまの進捗を 1 行 JSON で返す（省略時は最新のログ）

  推論:
    --think on|off        思考モード（既定は AGENT_OLLAMA_THINK → モデル既定）
    --skill NAME          スキルを明示指定（複数可）
    --no-skills           先頭スラッシュ行によるスキル展開をしない

  進捗と打ち切り（「遅い」は通し「進んでいない」だけを落とす）:
    --stall-timeout SEC   生成開始後の無進捗の上限（既定 180・0 で無効）
    --first-token-timeout SEC  最初のトークンまでの上限（既定 0 = 無制限）
    --log PATH            進捗ログ（JSONL）の置き場   --no-log 書かない

  ループ（--tools のとき）:
    --max-rounds N        最大ラウンド（既定 12）
    --command-timeout SEC コマンド 1 つの上限（既定 300）
    --cwd DIR             作業ディレクトリ（既定は現在の位置）

  環境変数: OLLAMA_HOST / AGENT_OLLAMA_THINK / AGENT_OLLAMA_OPTIONS(JSON) /
    AGENT_OLLAMA_KEEP_ALIVE / AGENT_OLLAMA_LOG_DIR / AGENT_OLLAMA_SKILLS_DIR /
    AGENT_OLLAMA_STALL_TIMEOUT / AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT / OLLAMA_TIMEOUT
"""

_FLAGS = {"--tools", "--tui", "--no-skills", "--no-log", "-h", "--help"}
_VALUED = {"--think", "--skill", "--stall-timeout", "--first-token-timeout",
           "--max-rounds", "--command-timeout", "--cwd", "--log", "--model"}
_OPTIONAL_VALUED = {"--follow", "--status"}


class ArgError(ValueError):
    """引数の誤り（使い方を出して終わる）。"""


def _as_bool(text: str, name: str) -> bool:
    lowered = str(text).strip().lower()
    if lowered in ("on", "true", "1", "yes"):
        return True
    if lowered in ("off", "false", "0", "no"):
        return False
    raise ArgError(f"{name} は on か off です: {text}")


def _as_float(text: str, name: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ArgError(f"{name} は数値です: {text}") from None


def parse_args(tokens: "list[str]") -> dict:
    """argv を解釈する（**オプションは位置に依存しない**）。

    契約の argv は `command` の展開後に `write_args` を足すため、権限フラグは
    positional なモデル名の**後ろ**に並ぶ（例: `agent-ollama --think off M --tools`）。
    よって「先に来たものがオプション」という前提を置けない。
    """
    opts: dict = {
        "model": "", "tools": False, "tui": False, "help": False,
        "think": None, "skills": [], "skills_enabled": True,
        "stall_timeout": None, "first_token_timeout": None,
        "max_rounds": ollama_loop.DEFAULT_MAX_ROUNDS,
        "command_timeout": ollama_loop.DEFAULT_COMMAND_TIMEOUT_SEC,
        "cwd": None, "log": None, "no_log": False,
        "follow": False, "status": False, "log_target": None,
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in ("-h", "--help"):
            opts["help"] = True
        elif token == "--tools":
            opts["tools"] = True
        elif token == "--tui":
            opts["tui"] = True
        elif token == "--no-skills":
            opts["skills_enabled"] = False
        elif token == "--no-log":
            opts["no_log"] = True
        elif token in _OPTIONAL_VALUED:
            opts["follow" if token == "--follow" else "status"] = True
            if index < len(tokens) and not tokens[index].startswith("-"):
                opts["log_target"] = tokens[index]
                index += 1
        elif token in _VALUED or "=" in token and token.split("=", 1)[0] in _VALUED:
            if "=" in token and token.split("=", 1)[0] in _VALUED:
                name, value = token.split("=", 1)
            else:
                name = token
                if index >= len(tokens):
                    raise ArgError(f"{name} に値がありません")
                value = tokens[index]
                index += 1
            if name == "--think":
                opts["think"] = _as_bool(value, name)
            elif name == "--skill":
                opts["skills"].append(value)
            elif name == "--stall-timeout":
                opts["stall_timeout"] = _as_float(value, name)
            elif name == "--first-token-timeout":
                opts["first_token_timeout"] = _as_float(value, name)
            elif name == "--max-rounds":
                opts["max_rounds"] = max(1, int(_as_float(value, name)))
            elif name == "--command-timeout":
                opts["command_timeout"] = _as_float(value, name)
            elif name == "--cwd":
                opts["cwd"] = value
            elif name == "--log":
                opts["log"] = value
            elif name == "--model":
                opts["model"] = value
        elif token.startswith("-") and token != "-":
            raise ArgError(f"知らないオプションです: {token}")
        elif not opts["model"]:
            opts["model"] = token
        else:
            raise ArgError(f"引数が多すぎます: {token}")
    return opts


# ---------------------------------------------------------------------------
# 非ストリーミングの 1 発呼び出し（後方互換のため残す）
# ---------------------------------------------------------------------------
def _request_timeout_sec() -> float:
    """HTTP 待ち上限。呼び出し側（agent-project 既定 300s 等）より短くしない。

    `OLLAMA_TIMEOUT` で上書きできる。未設定時は 600s——冷起動や大きめモデルで
    120s 打ち切りになると、外側の timeout より先にアダプタが落ちて誤って env 扱いになる。
    """
    raw = os.environ.get("OLLAMA_TIMEOUT", "600")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 600.0
    return value if value > 0 else 600.0


def generate(model: str, prompt: str, *, think: "bool | None" = None) -> dict:
    """`/api/generate` を非ストリーミングで 1 回叩く（進捗は取れない）。

    通常経路は `ollama_loop.run_plain`（ストリーミング）を使う——沈黙の理由が
    prefill なのかハングなのか区別できないと、正常な実行を壁時計で殺してしまうから。
    こちらは「進捗が不要な最小呼び出し」のために残してある。
    """
    host = ollama_loop.host_url()
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if think is not None:
        payload["think"] = bool(think)
    options = ollama_loop.load_options()
    if options:
        payload["options"] = options
    req = urllib.request.Request(
        f"{host}/api/generate", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_request_timeout_sec()) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"ollama API error ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ollama に接続できません: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("ollama API がタイムアウトしました") from exc
    if not isinstance(data, dict):
        raise RuntimeError("ollama API がオブジェクト以外を返しました")
    return data


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
def _limits(opts: dict) -> dict:
    return {"stall_timeout": opts.get("stall_timeout"),
            "first_token_timeout": opts.get("first_token_timeout")}


def run_request(prompt: str, opts: dict, *, model: str = "", tools: "bool | None" = None,
                think: "bool | None" = None, renderer=None, warn=None) -> dict:
    """1 回分の実行（スキル展開 → ログ開始 → 単発 or ループ）。

    戻り値: {text, tokens_in, tokens_out, status, log}
    """
    model = model or opts["model"]
    use_tools = opts["tools"] if tools is None else tools
    think = opts["think"] if think is None else think
    warn = warn or (lambda message: print(message, file=sys.stderr))

    prompt, loaded = ollama_skills.expand(
        prompt, opts.get("skills") or (), enabled=opts.get("skills_enabled", True), warn=warn)

    log_path = None if opts.get("no_log") else (opts.get("log") or ollama_events.new_log_path(model))
    sink = renderer.event if renderer is not None else None
    started = time.monotonic()
    with ollama_events.EventLog(log_path, sink=sink) as events:
        events.emit("run_start", model=model, mode="tools" if use_tools else "plain",
                    log=str(log_path or ""), prompt_chars=len(prompt),
                    think=("既定" if think is None else bool(think)))
        for item in loaded:
            events.emit("skill_load", **item)
        try:
            if use_tools:
                result = ollama_loop.run_loop(
                    model, prompt, cwd=opts.get("cwd"), emit=events.emit, think=think,
                    max_rounds=opts["max_rounds"], command_timeout=opts["command_timeout"],
                    **_limits(opts))
            else:
                result = ollama_loop.run_plain(
                    model, prompt, think=think, emit=events.emit, round_no=1, **_limits(opts))
                result = dict(result, rounds=1, status="done")
        except BaseException as exc:
            events.emit("error", message=str(exc), kind_of=type(exc).__name__)
            events.emit("run_end", status="failed", rounds=0, tokens_in=0, tokens_out=0,
                        duration_sec=round(time.monotonic() - started, 2))
            raise
        events.emit("run_end", status=result.get("status") or "done",
                    rounds=result.get("rounds") or 1,
                    tokens_in=result.get("tokens_in") or 0,
                    tokens_out=result.get("tokens_out") or 0,
                    duration_sec=round(time.monotonic() - started, 2))
    return dict(result, log=str(log_path or ""))


def _tui_runner(opts: dict):
    def runner(prompt: str, *, model: str, tools: bool, think, renderer):
        result = run_request(prompt, opts, model=model, tools=tools, think=think,
                             renderer=renderer)
        return str(result.get("text") or "")
    return runner


def main(argv=None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    try:
        opts = parse_args(tokens)
    except ArgError as exc:
        print(f"{exc}\n\n{USAGE}", file=sys.stderr)
        return 2
    if opts["help"]:
        print(USAGE)
        return 0

    # 観測モード（LLM を呼ばない）。
    if opts["status"]:
        print(json.dumps(ollama_events.read_status(opts.get("log_target")), ensure_ascii=False))
        return 0
    if opts["follow"]:
        from agentcore import ollama_tui
        return ollama_tui.follow(opts.get("log_target"))

    if not opts["model"]:
        print(f"モデルを指定してください。\n\n{USAGE}", file=sys.stderr)
        return 2

    if opts["tui"]:
        from agentcore import ollama_tui
        try:
            return ollama_tui.repl(_tui_runner(opts), model=opts["model"],
                                   tools=opts["tools"], think=opts["think"])
        except KeyboardInterrupt:
            return 130

    try:
        result = run_request(sys.stdin.read(), opts)
    except ollama_skills.SkillNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ollama_loop.StallError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ollama_loop.OllamaError, RuntimeError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    sys.stdout.write(str(result.get("text") or ""))
    sys.stdout.flush()
    print(f"@agent-usage tokens_in={int(result.get('tokens_in') or 0)} "
          f"tokens_out={int(result.get('tokens_out') or 0)}", file=sys.stderr)
    if result.get("log"):
        print(f"@agent-log {result['log']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
