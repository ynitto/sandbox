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
| `agent-ollama --replay [PATH]` | 記録済みプロンプトを再生して品質を測る | 測定（道具は持たない） |

標準ライブラリのみ（pip 依存なし。rich があれば TUI の色付けにだけ使う）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from agentcore import ollama_context, ollama_events, ollama_loop, ollama_skills

USAGE = """使い方: agent-ollama [オプション] <model>

  プロンプトは stdin。本文は stdout、診断と `@agent-usage` は stderr へ出る。

  実行モード:
    --tools [セット]      道具を持つ実行ループ（書き込みモード）。セットは
                          bash（既定・制限なし）| read（読み取り専用コマンドのみ）
    --tui                 デバッグ用の対話ビュー（行指向・tmux から操作できる。
                          矢印キー・履歴・Tab 補完が効く。一覧は TUI 内の /keys）
    --follow [LOG]        進捗ログを追尾表示する（省略時は最新のログ）
    --status [LOG]        いまの進捗を 1 行 JSON で返す（省略時は最新のログ）
    --context <model>     文脈の上限だけを調べて出す（LLM は呼ばない）
    --replay [PATH]       記録済みプロンプトを再生して品質を測る（省略時はログ置き場）

  再生（--replay。品質をライブ実行を焼かずに測る口）:
    --arm SPEC            当てる設定。複数指定でき、同じ入力へ順に当たる。
                          例: --arm model=qwen3,think=off,format=json
                          キー: model / think / format / label / repeat
                          （repeat は同じ設定を何回引くか＝自己一貫性の測定）
    --replay-limit N      再生する件数の上限（新しい実行から順に採る）
    --replay-out PATH     記録（JSONL）の書き出し先。既定はログ置き場
                          ※再生は常に道具なしで行う（記録されたコマンドは再実行しない）

  推論:
    --think on|off        思考モード（既定は AGENT_OLLAMA_THINK → モデル既定）
    --format json|array|text
                          出力の文法を強制する（json = 妥当な JSON しか出せなくなる。
                          ただしトップレベルは必ずオブジェクトになる。array = 文字列の
                          JSON 配列だけを出せるようにする（split のような配列契約用）。
                          プロンプトは 1 トークンも増えない。text = 強制しない）
    --skill NAME          スキルを明示指定（複数可）
    --no-skills           先頭スラッシュ行によるスキル展開をしない

  進捗と打ち切り（「遅い」は通し「進んでいない」だけを落とす）:
    --stall-timeout SEC   生成開始後の無進捗の上限（既定 180・0 で無効）
    --first-token-timeout SEC  最初のトークンまでの上限（既定 0 = 無制限）
    --log PATH            進捗ログ（JSONL）の置き場   --no-log 書かない

  文脈（黙った切り捨てを起こさせない）:
    --context-limit N     文脈の上限を明示する（既定は num_ctx → /api/ps → /api/show）
    --context-warn-pct P  この割合を超えたら 1 回警告する（既定 90・0 で無効）

  ループ（--tools のとき）:
    --max-rounds N        最大ラウンド（既定 12）
    --command-timeout SEC コマンド 1 つの上限（既定 300）
    --cwd DIR             作業ディレクトリ（既定は現在の位置）

  環境変数: OLLAMA_HOST / AGENT_OLLAMA_THINK / AGENT_OLLAMA_OPTIONS(JSON) /
    AGENT_OLLAMA_KEEP_ALIVE / AGENT_OLLAMA_LOG_DIR / AGENT_OLLAMA_SKILLS_DIR /
    AGENT_OLLAMA_STALL_TIMEOUT / AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT /
    AGENT_OLLAMA_CONNECT_TIMEOUT / OLLAMA_TIMEOUT /
    AGENT_OLLAMA_HISTORY / AGENT_OLLAMA_NO_READLINE（TUI の行編集を切る）
    OLLAMA_HOST が未設定なら ~/.profile を読んで OLLAMA_* / AGENT_OLLAMA_* を補完する
    （エンジンからの非ログインシェル起動で環境変数が届かない場合の救済）
"""

_FLAGS = {"--tools", "--tui", "--no-skills", "--no-log", "--context", "-h", "--help"}
_VALUED = {"--think", "--skill", "--stall-timeout", "--first-token-timeout",
           "--max-rounds", "--command-timeout", "--cwd", "--log", "--model",
           "--context-limit", "--context-warn-pct", "--format",
           "--arm", "--replay-limit", "--replay-out"}
_OPTIONAL_VALUED = {"--follow", "--status", "--replay"}
# `--tools` の後ろに続いてよい語。未実装セット（edit）も**名前としては受ける**——
# 受けないと positional なモデル名として解釈され、原因の分からない失敗になる。
_TOOLSET_WORDS = set(ollama_loop.TOOLSETS) | set(ollama_loop.PLANNED_TOOLSETS)


class ArgError(ValueError):
    """引数の誤り（使い方を出して終わる）。"""


# 完走しなかった status → 人と呼び出し側へ渡す理由。既定文を持つのは、ここに載っていない
# status を「完走」に見せないため（status が増えたときに黙って done 扱いへ落とさない）。
_INCOMPLETE_REASONS = {
    "no_command": "規約どおりのコマンドも完了宣言も出せず打ち切りました（成果は途中経過です）",
    "max_rounds": "最大ラウンドに達して打ち切りました",
    "context_exhausted": "文脈が尽きて打ち切りました",
    "tool_denied": "許可されないコマンドの繰り返しで打ち切りました",
    "no_progress": "同じコマンドを同じ結果で繰り返しており、進んでいないので打ち切りました",
}


# ---------------------------------------------------------------------------
# 環境の補完（非ログインシェル対策）
# ---------------------------------------------------------------------------
_PROFILE_ENV_PREFIXES = ("OLLAMA_", "AGENT_OLLAMA_")


def load_profile_env(path: str = "~/.profile") -> dict:
    """`OLLAMA_HOST` 未設定なら ~/.profile を評価して OLLAMA_* / AGENT_OLLAMA_* を補完する。

    エンジン（agent-project / agent-flow / agent-amigos）は agent-ollama を
    **非ログインシェルの subprocess** として起動するため、~/.profile に書いた
    `export OLLAMA_HOST=...` は届かない。届かないと既定の 127.0.0.1 へ向かって
    「接続できません」で env 落ちする——設定はしてあるのに動かない、という
    一番説明しづらい失敗になるので、CLI の入口で 1 回だけ自力で読む。

    - 環境に既にある変数が常に勝つ（呼び出し側の明示指定を profile で潰さない）
    - `OLLAMA_HOST` が設定済みなら何もしない（構成済みの環境へ余計な subprocess を足さない）
    - profile の評価は sh の子プロセスに閉じ込め、失敗は黙って無視する
      （profile が壊れていても推論を止める理由にはしない）

    戻り値: 実際に取り込んだ変数（テストと診断のため）。
    """
    if os.environ.get("OLLAMA_HOST"):
        return {}
    profile = os.path.expanduser(path)
    if not os.path.isfile(profile):
        return {}
    # profile を source した後の環境を JSON で受け取る。stdin は閉じる——
    # このプロセスの stdin はプロンプト本文なので、profile に読ませてはいけない。
    dump = "import json, os; print(json.dumps(dict(os.environ)))"
    try:
        proc = subprocess.run(
            ["sh", "-c", '. "$1" >/dev/null 2>&1; exec "$2" -c "$3"',
             "sh", profile, sys.executable, dump],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    imported: dict = {}
    for name, value in data.items():
        if (name.startswith(_PROFILE_ENV_PREFIXES) and name not in os.environ
                and isinstance(value, str)):
            os.environ[name] = value
            imported[name] = value
    return imported


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
        "model": "", "tools": False, "toolset": ollama_loop.DEFAULT_TOOLSET,
        "tui": False, "help": False, "format": None,
        "think": None, "skills": [], "skills_enabled": True,
        "stall_timeout": None, "first_token_timeout": None,
        "max_rounds": ollama_loop.DEFAULT_MAX_ROUNDS,
        "command_timeout": ollama_loop.DEFAULT_COMMAND_TIMEOUT_SEC,
        "cwd": None, "log": None, "no_log": False,
        "follow": False, "status": False, "log_target": None,
        "context_limit": 0, "context_warn_pct": ollama_context.DEFAULT_WARN_PCT,
        "context_query": False,
        "replay": False, "replay_target": None, "arms": [],
        "replay_limit": 0, "replay_out": None,
    }
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token in ("-h", "--help"):
            opts["help"] = True
        elif token == "--tools" or token.startswith("--tools="):
            opts["tools"] = True
            name = token.split("=", 1)[1] if "=" in token else ""
            if not name and index < len(tokens) and tokens[index] in _TOOLSET_WORDS:
                name = tokens[index]
                index += 1
            if name and name not in ollama_loop.TOOLSETS:
                raise ArgError(f"--tools のセットは {' / '.join(ollama_loop.TOOLSETS)} です"
                               f"（{name} は未実装）")
            if name:
                opts["toolset"] = name
        elif token == "--tui":
            opts["tui"] = True
        elif token == "--no-skills":
            opts["skills_enabled"] = False
        elif token == "--no-log":
            opts["no_log"] = True
        elif token == "--context":
            opts["context_query"] = True
        elif token in _OPTIONAL_VALUED:
            name = token.lstrip("-")
            opts[name] = True
            # 追尾・状態はログ 1 本を指すが、再生はディレクトリも指せるので鍵を分ける
            # （同じ `log_target` に混ぜると「最新のログ 1 本」の意味と衝突する）。
            target_key = "replay_target" if token == "--replay" else "log_target"
            if index < len(tokens) and not tokens[index].startswith("-"):
                opts[target_key] = tokens[index]
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
            elif name == "--format":
                fmt = str(value).strip().lower()
                if fmt not in ("json", "array", "text"):
                    raise ArgError(f"--format は json か array か text です: {value}")
                # text は「強制しない」。API へフィールドを送らないことで表す。
                opts["format"] = fmt if fmt in ("json", "array") else None
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
            elif name == "--context-limit":
                opts["context_limit"] = max(0, int(_as_float(value, name)))
            elif name == "--context-warn-pct":
                opts["context_warn_pct"] = _as_float(value, name)
            elif name == "--model":
                opts["model"] = value
            elif name == "--arm":
                opts["arms"].append(value)
            elif name == "--replay-limit":
                opts["replay_limit"] = max(0, int(_as_float(value, name)))
            elif name == "--replay-out":
                opts["replay_out"] = value
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


def make_tracker(model: str, opts: dict) -> "ollama_context.ContextTracker":
    """文脈の上限を 1 回だけ解決してトラッカーを作る（失敗しても上限 0 で動く）。"""
    limit, source = ollama_context.resolve_limit(
        model, options=ollama_loop.load_options(), explicit=opts.get("context_limit") or 0,
        host=ollama_loop.host_url())
    return ollama_context.ContextTracker(
        limit=limit, source=source, warn_pct=opts.get("context_warn_pct")
        if opts.get("context_warn_pct") is not None else ollama_context.DEFAULT_WARN_PCT)


def run_request(prompt: str, opts: dict, *, model: str = "", tools: "bool | None" = None,
                think: "bool | None" = None, renderer=None, warn=None) -> dict:
    """1 回分の実行（スキル展開 → ログ開始 → 単発 or ループ）。

    戻り値: {text, tokens_in, tokens_out, status, log, context}
    """
    model = model or opts["model"]
    use_tools = opts["tools"] if tools is None else tools
    think = opts["think"] if think is None else think
    toolset = str(opts.get("toolset") or ollama_loop.DEFAULT_TOOLSET)
    fmt = opts.get("format")
    warn = warn or (lambda message: print(message, file=sys.stderr))

    prompt, loaded = ollama_skills.expand(
        prompt, opts.get("skills") or (), enabled=opts.get("skills_enabled", True), warn=warn)
    # スキルとツールセットは暗黙に結合している（ツール開示設計 §6.1）: 同梱スクリプトを
    # 叩く前提のスキルは、汎用シェルを含まないセットでは動かない。黙って無視すると
    # 「スキルは読まれたのに手順が実行されない」成功に見える失敗になるので、ここで落とす。
    if use_tools and toolset != ollama_loop.DEFAULT_TOOLSET:
        bundled = [item["name"] for item in loaded if item.get("scripts")]
        if bundled:
            raise ollama_skills.SkillToolsetMismatch(
                f"スキルと --tools セットが噛み合いません: {', '.join(bundled)} は同梱の "
                f"scripts/ を実行する前提ですが、{toolset} セットではシェルを使えません。"
                "--tools bash で起動するか、そのスキルの指定を外してください。")

    log_path = None if opts.get("no_log") else (opts.get("log") or ollama_events.new_log_path(model))
    sink = renderer.event if renderer is not None else None
    tracker = make_tracker(model, opts)
    started = time.monotonic()
    with ollama_events.EventLog(log_path, sink=sink) as events:
        events.emit("run_start", model=model, mode="tools" if use_tools else "plain",
                    log=str(log_path or ""), prompt_chars=len(prompt),
                    # 作業ディレクトリ。読み手（agent-audit sessions / dashboard の
                    # 「会話を見る」）が、どの作業のセッションかを絞るのに使う。
                    cwd=str(opts.get("cwd") or os.getcwd()),
                    think=("既定" if think is None else bool(think)),
                    toolset=(toolset if use_tools else ""), format=(fmt or ""),
                    options=ollama_loop.load_options(),
                    context_limit=tracker.limit, context_limit_source=tracker.limit_source)
        for item in loaded:
            events.emit("skill_load", **item)
        try:
            if use_tools:
                result = ollama_loop.run_loop(
                    model, prompt, cwd=opts.get("cwd"), emit=events.emit, think=think,
                    max_rounds=opts["max_rounds"], command_timeout=opts["command_timeout"],
                    tracker=tracker, toolset=toolset, fmt=fmt, **_limits(opts))
            else:
                # 会話の本文を残す（tools 経路は run_loop が同じ `message` を出す）。
                events.emit("message", role="user", content=prompt)
                result = ollama_loop.run_plain(
                    model, prompt, think=think, emit=events.emit, round_no=1,
                    tracker=tracker, fmt=fmt, **_limits(opts))
                events.emit("message", role="assistant", content=str(result.get("text") or ""))
                result = dict(result, rounds=1, status="done")
                if tracker.should_warn():
                    events.emit("context_warn", round=1, **tracker.snapshot())
        except BaseException as exc:
            events.emit("error", message=str(exc), kind_of=type(exc).__name__)
            events.emit("run_end", status="failed", rounds=0, tokens_in=0, tokens_out=0,
                        duration_sec=round(time.monotonic() - started, 2))
            raise
        events.emit("run_end", status=result.get("status") or "done",
                    rounds=result.get("rounds") or 1,
                    tokens_in=result.get("tokens_in") or 0,
                    tokens_out=result.get("tokens_out") or 0,
                    duration_sec=round(time.monotonic() - started, 2),
                    **tracker.snapshot())
    return dict(result, log=str(log_path or ""), context=tracker.snapshot())


def run_replay(opts: dict, *, generate=None, out=None, err=None) -> int:
    """`--replay` — 記録済みプロンプトを再生し、集計を 1 行 JSON で返す。

    stdout は集計だけにする（`--status` と同じで、機械が読むのは 1 行）。1 件ごとの
    記録は JSONL のファイルへ落とし、場所を `@agent-log` で知らせる——再生は件数 ×
    腕の数だけ行が出るので、本文へ混ぜると読み手が集計を拾えない。
    """
    out = out or sys.stdout
    err = err or sys.stderr
    from agentcore import ollama_replay

    try:
        arms = [ollama_replay.parse_arm(spec) for spec in (opts.get("arms") or [])]
    except ollama_replay.ArmError as exc:
        print(str(exc), file=err)
        return 2
    if not arms:
        # 腕の指定が無ければ、いまの起動オプションを 1 本の腕とみなす（既定の再現）。
        arms = [ollama_replay.parse_arm("")]
        arms[0].update({"model": opts.get("model") or "", "think": opts.get("think"),
                        "format": opts.get("format")})
        arms[0]["label"] = ollama_replay.label_of(arms[0])

    cases = ollama_replay.collect_cases(opts.get("replay_target"),
                                        limit=int(opts.get("replay_limit") or 0))
    if not cases:
        print("再生できる記録がありません（run_start と user メッセージを持つ JSONL が要ります）",
              file=err)
        return 1

    record_path = opts.get("replay_out") or ollama_replay.new_record_path()
    total = len(cases) * sum(int(arm.get("repeat") or 1) for arm in arms)
    print(f"@agent-note 再生 {len(cases)} 件 × 腕 {len(arms)} = {total} 回。"
          "道具は使いません（記録されたコマンドは再実行しません）。", file=err)

    done = 0

    def emit_record(record: dict) -> None:
        nonlocal done
        done += 1
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        # 失敗を先に見る。接続できなかった呼び出しは「空応答」ではない。
        mark = "失敗" if not record.get("ok") else ("空" if record.get("empty") else "ok")
        print(f"@agent-note [{done}/{total}] {record.get('arm')} {mark} "
              f"{record.get('duration_sec')}s", file=err)

    try:
        path = Path(str(record_path)).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
    except OSError as exc:
        print(f"再生記録を書けません: {exc}", file=err)
        return 1
    try:
        records = ollama_replay.replay(cases, arms, generate=generate, on_record=emit_record)
    finally:
        handle.close()

    summary = ollama_replay.summarize(records)
    print(json.dumps(summary, ensure_ascii=False), file=out)
    print(f"@agent-log {path}", file=err)
    # 1 件も返ってこなかったのは測定結果ではなく環境の問題（サーバ・モデル）。集計は
    # 出したうえで rc で伝える——全滅を rc=0 で返すと、空の結果が測定として流通する。
    if records and summary.get("failed") == len(records):
        print("@agent-note 全ての再生が失敗しました（ollama サーバとモデルを確認してください）",
              file=err)
        return 1
    return 0


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

    # 非ログインシェルから起動されても ~/.profile の OLLAMA_HOST 等が効くようにする。
    # 観測モードより前に呼ぶ（AGENT_OLLAMA_LOG_DIR も profile 由来でありうる）。
    load_profile_env()

    # 観測モード（LLM を呼ばない）。
    if opts["status"]:
        print(json.dumps(ollama_events.read_status(opts.get("log_target")), ensure_ascii=False))
        return 0
    if opts["follow"]:
        from agentcore import ollama_tui
        return ollama_tui.follow(opts.get("log_target"))

    # 再生。モデルは腕か記録側から来るので、positional のモデル指定は要らない。
    if opts["replay"]:
        try:
            return run_replay(opts)
        except KeyboardInterrupt:
            return 130

    if not opts["model"]:
        print(f"モデルを指定してください。\n\n{USAGE}", file=sys.stderr)
        return 2

    if opts["context_query"]:
        limit, source = ollama_context.resolve_limit(
            opts["model"], options=ollama_loop.load_options(),
            explicit=opts.get("context_limit") or 0, host=ollama_loop.host_url())
        print(json.dumps({"model": opts["model"], "context_limit": limit,
                          "context_limit_source": source}, ensure_ascii=False))
        return 0 if limit else 1

    if opts["tui"]:
        from agentcore import ollama_tui
        try:
            return ollama_tui.repl(_tui_runner(opts), model=opts["model"],
                                   tools=opts["tools"], think=opts["think"])
        except KeyboardInterrupt:
            return 130

    try:
        result = run_request(sys.stdin.read(), opts)
    except (ollama_skills.SkillNotFound, ollama_skills.SkillToolsetMismatch) as exc:
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

    status = str(result.get("status") or "done")
    incomplete = status != "done"
    reason = _INCOMPLETE_REASONS.get(status, f"status={status} で打ち切りました")
    sys.stdout.write(str(result.get("text") or ""))
    # 完走していないなら、成果本文の末尾に機械可読な未完了の封筒を足す。成果は返す
    # （R1 止めない）が、呼び出し側が**本文を読まずに**未完了と判別できないと、途中経過が
    # rc=0 の成果として done になる——実際 no_command の途中経過（未実行のコマンドを含む
    # 報告）が完了として成果ブランチへ push された。封筒の形は engine 側の worker 契約
    # （agent-flow work.py の `ok is False` 判定）と同じものを使う。
    # `--format json` のときは足さない（本文全体が JSON 契約なので壊せない。JSON 契約の
    # 役割の未完了は engine 側の形式修復リトライが拾う）。
    if incomplete and not opts.get("format"):
        sys.stdout.write("\n\n" + json.dumps(
            {"ok": False, "issues": [f"agent-ollama: {reason}（status={status}）"]},
            ensure_ascii=False) + "\n")
    sys.stdout.flush()
    print(f"@agent-usage tokens_in={int(result.get('tokens_in') or 0)} "
          f"tokens_out={int(result.get('tokens_out') or 0)}", file=sys.stderr)
    context = result.get("context") or {}
    if context.get("context_used"):
        # 契約の `@agent-usage`（累計の仕事量）とは別の行にする。文脈使用量は
        # 「いまどれだけ埋まっているか」で、意味が違う——同じ行に混ぜない。
        print(f"@agent-context used={context['context_used']} "
              f"limit={context.get('context_limit', 0)} "
              f"pct={context.get('context_pct', '')} "
              f"source={context.get('context_source', '')}", file=sys.stderr)
    # 人向けの注記。done 以外はすべて出す——`no_command`（規約から外れたまま打ち切り）が
    # ここから漏れていたため、一番起きる未完了が「静かな成功」に見えていた。
    if incomplete:
        print(f"@agent-note {reason}（{status}）。成果は最後の応答までの分です。", file=sys.stderr)
    if result.get("log"):
        print(f"@agent-log {result['log']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
