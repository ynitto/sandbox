"""ローカル推論の実行核 — ストリーミング呼び出しと bash 1 ツールの最小ループ。

設計: docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md 案 F-2。

## なぜストリーミングなのか

`stream=false` だと「prefill 中（正常だが長い）」と「ハング」が外から区別できない。
CPU 推論では prefill だけで数分〜10 分かかるので、この区別が付かないまま壁時計で
打ち切ると**正常な実行を殺す**。ストリーミングにすると、

- 最初のトークンが出るまでの待ちを `phase=prefill` として heartbeat で示せる（生存の証拠）
- トークンが出始めた後の無進捗を `phase=decode` の stall として検知できる

の 2 つが手に入る。「遅い」は通し、「進んでいない」だけを落とす（§0.1 R2）。

## なぜツールが bash 1 つだけなのか

ツール定義（JSON スキーマ）は毎リクエストの prefill に載る固定費である。opencode の
1〜2 万トークンの主犯がこれで、CPU ではそこだけで数分焼ける。ツールを bash 1 つに絞り、
呼び出しもスキーマではなく**テキスト規約**（コードブロック = 実行するコマンド）にすると、
固定費はシステムプロンプトの数百トークンだけになる。

標準ライブラリのみ（pip 依存なし）。
"""
from __future__ import annotations

import json
import os
import queue
import re
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from agentcore.ollama_events import HEARTBEAT_INTERVAL_SEC, PROGRESS_INTERVAL_SEC

# 既定値。すべて環境変数で上書きできる（バックアップ運転の現場で調整する余地を残す）。
DEFAULT_STALL_TIMEOUT_SEC = 180.0     # decode 中の無進捗の上限。これだけが失敗検知の主役
DEFAULT_FIRST_TOKEN_TIMEOUT_SEC = 0.0  # prefill の上限。既定 0 = 無制限（遅さは異常ではない）
DEFAULT_CONNECT_TIMEOUT_SEC = 120.0   # 混雑時はモデルロード中に応答ヘッダすら遅れる
DEFAULT_MAX_ROUNDS = 12
DEFAULT_COMMAND_TIMEOUT_SEC = 300.0
DEFAULT_MAX_OUTPUT_CHARS = 4000
_MAX_NUDGES = 2                        # 規約を外した応答へ言い直しを促す回数の上限
# ゲート拒否の上限。無料にすると、モデルが全ラウンドを権限の探りだけで焼き切れる
# （ツール開示設計 §4.3「昇格試行に予算を切る」。nudge と同じ形の上限にする）。
_MAX_DENIALS = 2
# これ以下しか文脈が残っていないなら、ツール結果を足しても意味を成さない。
# ここで明示的に止める（サーバに黙って切り捨てさせるより、止まった理由が残る方がよい）。
_MIN_TOOL_OUTPUT_CHARS = 200

_FENCE_RE = re.compile(r"```(?:bash|sh|shell|console)?[ \t]*\r?\n(.*?)```", re.S)
_DONE_MARKER = "TASK_COMPLETE"

# --- ツールセット（`--tools <セット>`）---------------------------------------
# 設計: docs/plans/2026-08-07-agent-ollama-tool-disclosure-design.md §4.2 / 適用拡大設計 §6。
# 「ツールが 1 つも無い」と「無制限のシェルが 1 つある」の間に段を作る。**強制は実行の
# 手前のゲートで行う**——「作業ディレクトリの外を変更しない」の類はシステムプロンプトの
# 一文でしかなく、強制力がゼロだった。読み込み時間は増やさない（語彙は規約数行で済む）。
TOOLSETS = ("bash", "read")
DEFAULT_TOOLSET = "bash"
# `edit` セットは適用拡大設計 §7（段 4）。実装前に名前だけ受けて明示的に断る——
# 黙ってモデル名として解釈されると、原因の分からない起動失敗になる。
PLANNED_TOOLSETS = ("edit",)

# read セットの語彙。**ファイルを変えられないコマンドだけ**を置く。sed（-i）・awk
# （print > file）・tee・xargs・シェル類は自前の書き込み手段を持つので入れない。
_READ_COMMANDS = {
    "basename", "cat", "cut", "date", "diff", "dirname", "echo", "file", "find",
    "grep", "head", "ls", "nl", "pwd", "readlink", "realpath", "rg", "sort",
    "stat", "tail", "tree", "uniq", "wc", "which",
}
# git は副作用のある部分コマンドが多いので、読む部分コマンドだけを名指しで許す。
_READ_GIT_SUBCOMMANDS = {"blame", "branch", "describe", "diff", "grep", "log",
                         "ls-files", "ls-tree", "rev-parse", "shortlog", "show", "status"}
# find は語彙に入れるが、自前で書き込み・実行ができる述語だけ個別に弾く。
_FIND_WRITE_PREDICATES = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                          "-fls", "-fprint", "-fprint0", "-fprintf"}
# read セットではシェルを介さず argv を直接実行するので、メタ文字は「危険」と
# 「黙って効かない」の両方の理由で弾く（`>` 1 文字で読み取り専用が崩れ、`*` は
# 展開されないまま検索語として渡って結果が静かに間違う）。
# **判定は引用の外だけ**。`find . -name '*.py'` の `*` は find 自身が解釈する正しい
# 使い方で、シェルに渡らない以上ただの文字である——ここを一律で弾くと、read セットで
# 一番よく使う探索が通らなくなる。
_METACHARS = set("|&;<>()$`\\*?[]{}~\n\r")


def _unquoted_metachars(text: str) -> "list[str]":
    """引用符の外にあるメタ文字を拾う（引用符の中は文字として扱う）。"""
    found: "set[str]" = set()
    quote = ""
    for ch in text:
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in _METACHARS:
            found.add(ch)
    return sorted(found)


class StallError(RuntimeError):
    """無進捗（stall）で打ち切った。壁時計超過ではないので transient として扱う。"""


class OllamaError(RuntimeError):
    """接続・HTTP・応答形式の失敗。"""


def host_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    return host


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def load_options() -> dict:
    """`AGENT_OLLAMA_OPTIONS`（JSON）を `options` へ合流させる（案 E）。

    サーバ全体の環境変数を触らずに `num_ctx` などをリクエスト単位で決められる。
    壊れた JSON は黙って無視する（推論を止める理由にはしない）。
    """
    raw = os.environ.get("AGENT_OLLAMA_OPTIONS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_think(explicit: "bool | None" = None) -> "bool | None":
    """think の解決（CLI 引数 → `AGENT_OLLAMA_THINK` → 宣言しない）。

    宣言しない（None）ときはフィールドを送らず、モデル/サーバの既定に委ねる。
    プロンプトへ `/no_think` を混ぜる方式は採らない——モデル依存で、成果物本文へ
    漏れる事故もある。API のフィールドで指定するのが唯一の確実な口。
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get("AGENT_OLLAMA_THINK", "").strip().lower()
    if raw in ("off", "false", "0", "no"):
        return False
    if raw in ("on", "true", "1", "yes"):
        return True
    return None


def _payload(model: str, *, think: "bool | None", options: "dict | None",
             fmt: "str | None" = None) -> dict:
    body: dict = {"model": model, "stream": True}
    merged = load_options()
    if options:
        merged.update(options)
    if merged:
        body["options"] = merged
    if fmt:
        # `format` と think は**併用できない**。文法制約は thinking チャネルの 1 トークン目から
        # 効くため、モデルは答えの JSON を丸ごと thinking に吐き、本文は空のまま `done` で終える
        # （qwen3.5:9b 実測 39/39 件が空応答 → `empty_output_is_error` が transient を上げ、
        # 呼び出し側は heal ループに落ちる）。思考も JSON に縛られる以上、有効にしても推論は
        # 1 文字も増えない。だから **off を明示する**——未宣言（None）はモデル既定に委ねる
        # ことになり、思考モデルでは既定が on なので同じ空応答を踏む。
        # 代償: think 非対応モデルへ `think` を送ると 400 になる。空応答の沈黙より、
        # メッセージの出るエラーのほうがましなので受ける。
        think = False
    if think is not None:
        body["think"] = bool(think)
    if fmt:
        # `format` は**デコード時の文法制約**。プロンプトに 1 トークンも足さないので
        # 読み込み時間の固定費が増えない（適用拡大設計 §4.1）。JSON 契約の役割で
        # 「妥当な JSON でない出力」という故障モードを消すのが目的。
        body["format"] = fmt
    keep_alive = os.environ.get("AGENT_OLLAMA_KEEP_ALIVE", "").strip()
    if keep_alive:
        body["keep_alive"] = keep_alive
    return body


def _abort_response(res) -> None:
    """応答を強制的に打ち切る（読み取り中のスレッドを解く）。

    **`res.close()` を呼ばない**のが要点。`http.client` の応答は `BufferedReader` の
    上に載っており、close() はそのロックを取ろうとする。ところがロックは受信で
    ブロックしているリーダースレッドが握っているので、**打ち切ろうとした側が固まる**
    ——「無進捗を検知したのにプロセスが終われない」という最悪の形になる（実測で踏んだ）。
    ソケットを直接 shutdown すれば、ブロック中の recv が即座に戻ってスレッドが解ける。
    """
    if res is None:
        return
    raw = getattr(getattr(res, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        return
    # ソケットへ手が届かない実装（テスト用の偽応答など）だけ close() に頼る。
    try:
        res.close()
    except Exception:
        pass


def _reader(req, holder: dict, mailbox: "queue.Queue") -> None:
    """接続と行読みを別スレッドで行う。

    本体（呼び出し側スレッド）を絶対にブロックさせないのが役目。ソケットに
    タイムアウトを掛けない（prefill が何分でも待てる）代わりに、待ちの上限判断と
    打ち切りは呼び出し側の watchdog が持つ——`res.close()` でこのスレッドを解く。
    """
    try:
        res = urllib.request.urlopen(req, timeout=None)
        holder["res"] = res
        mailbox.put(("open", None))
        for raw in res:
            mailbox.put(("line", raw))
        mailbox.put(("eof", None))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        mailbox.put(("fail", OllamaError(f"ollama API error ({exc.code}): {detail}")))
    except urllib.error.URLError as exc:
        mailbox.put(("fail", OllamaError(f"ollama に接続できません: {exc.reason}")))
    except Exception as exc:  # 打ち切りで close された場合もここに来る
        mailbox.put(("closed", exc))


def stream_call(endpoint: str, body: dict, *, delta_of, emit=None, round_no: int = 0,
                stall_timeout: "float | None" = None,
                first_token_timeout: "float | None" = None,
                connect_timeout: "float | None" = None,
                tracker=None,
                heartbeat: float = HEARTBEAT_INTERVAL_SEC) -> dict:
    """ollama のストリーミング API を 1 回叩き、本文と実測トークンを集約して返す。

    `emit(kind, **fields)` があれば進捗イベントを出す（EventLog.emit をそのまま渡せる）。
    打ち切りは 3 つの局面で別々の上限を持つ:

    | 局面      | 上限                        | 既定           |
    |-----------|-----------------------------|----------------|
    | connect   | connect_timeout             | 120s           |
    | prefill   | first_token_timeout         | 0（無制限）    |
    | decode    | stall_timeout               | 180s           |

    prefill を無制限にしてあるのが要点。CPU 推論では「最初のトークンまで 10 分」が
    正常なので、ここに上限を置くと正常な実行を殺す（§0.1 R2）。

    上限の判定は heartbeat の刻みで行うため、検知は最大 `heartbeat` 秒だけ遅れる
    （既定 5 秒。分単位の上限に対して十分な粒度で、待ち受けを 1 か所に保てる）。
    """
    stall_timeout = _env_float("AGENT_OLLAMA_STALL_TIMEOUT", DEFAULT_STALL_TIMEOUT_SEC) \
        if stall_timeout is None else stall_timeout
    first_token_timeout = _env_float(
        "AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT", DEFAULT_FIRST_TOKEN_TIMEOUT_SEC) \
        if first_token_timeout is None else first_token_timeout
    connect_timeout = _env_float("AGENT_OLLAMA_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT_SEC) \
        if connect_timeout is None else connect_timeout

    url = f"{host_url()}{endpoint}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")

    mailbox: "queue.Queue" = queue.Queue()
    holder: dict = {}
    thread = threading.Thread(target=_reader, args=(req, holder, mailbox), daemon=True)

    phase = "connect"
    started = time.monotonic()
    phase_started = started
    last_progress = started
    last_emit = 0.0
    text_parts: "list[str]" = []
    thinking_chars = 0
    tokens_out = 0
    final: dict = {}

    def limit_for(current_phase: str) -> float:
        if current_phase == "connect":
            return connect_timeout
        if current_phase == "prefill":
            return first_token_timeout
        return stall_timeout

    def abort() -> None:
        _abort_response(holder.get("res"))

    if emit is not None:
        emit("llm_start", round=round_no, phase=phase, model=str(body.get("model") or ""))
    thread.start()
    try:
        while True:
            try:
                kind, payload = mailbox.get(timeout=heartbeat)
            except queue.Empty:
                waited = time.monotonic() - max(phase_started, last_progress)
                limit = limit_for(phase)
                # heartbeat = 「生きているが進んでいない」の定期証跡。進捗ではないので
                # last_progress は動かさない（動かすと stall を永遠に検知できない）。
                if emit is not None:
                    emit("llm_heartbeat", round=round_no, phase=phase,
                         waiting_sec=round(waited, 1), tokens_out=tokens_out,
                         limit_sec=round(limit, 1))
                if limit > 0 and waited >= limit:
                    if emit is not None:
                        emit("stall", round=round_no, phase=phase, waiting_sec=round(waited, 1),
                             limit_sec=round(limit, 1))
                    abort()
                    raise StallError(
                        f"応答が停止しました: {phase} のまま {waited:.0f} 秒無進捗"
                        f"（上限 {limit:.0f} 秒）。ollama サーバの状態を確認してください。")
                continue

            if kind == "fail":
                raise payload
            if kind == "closed":
                raise OllamaError(f"ollama との通信が切れました: {payload}")
            if kind == "open":
                phase = "prefill"
                phase_started = time.monotonic()
                continue
            if kind == "eof":
                break

            line = payload.decode("utf-8", "replace").strip() if isinstance(payload, bytes) else str(payload).strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                raise OllamaError(f"ollama API error: {chunk['error']}")

            delta, thinking = delta_of(chunk)
            if thinking:
                thinking_chars += len(thinking)
            if delta:
                text_parts.append(delta)
                tokens_out += 1          # 到着チャンク数。実測は最終行の eval_count で上書きする
            if delta or thinking:
                now = time.monotonic()
                if phase != "decode":
                    phase = "decode"
                last_progress = now
                if emit is not None and (now - last_emit) >= PROGRESS_INTERVAL_SEC:
                    last_emit = now
                    elapsed = max(now - started, 1e-6)
                    # thinking も進捗として出す。think 有効時は本文が始まるまで tokens_out が
                    # 0 のままなので、これを載せないと「out=0 / tps=0.0」が延々と並び、
                    # **長考が停止と見分けられない**（実際 6500 トークン思考中の実行を
                    # ハングと誤読した）。停滞判定はここではなく last_progress が持つ。
                    emit("llm_progress", round=round_no, phase=phase, tokens_out=tokens_out,
                         tokens_per_sec=round(tokens_out / elapsed, 2),
                         thinking_chars=thinking_chars,
                         since_last_token_sec=0.0)
            if chunk.get("done"):
                final = chunk
    finally:
        abort()

    tokens_in = int(final.get("prompt_eval_count") or 0)
    measured_out = int(final.get("eval_count") or 0) or tokens_out
    duration = time.monotonic() - started
    # 文脈使用量は「この応答の実測」から導けるので、usage と同じ 1 か所（llm_end）へ載せる
    # ——別イベントに分けると、見る側が 2 つを突き合わせないと現在地が分からなくなる。
    context = tracker.observe(tokens_in, measured_out) if tracker is not None else {}
    if emit is not None:
        emit("llm_end", round=round_no, phase="done", tokens_in=tokens_in,
             tokens_out=measured_out, duration_sec=round(duration, 2),
             tokens_per_sec=round(measured_out / max(duration, 1e-6), 2),
             thinking_chars=thinking_chars, **context)
    return {
        "text": "".join(text_parts),
        "tokens_in": tokens_in,
        "tokens_out": measured_out,
        "duration_sec": duration,
        "done_reason": str(final.get("done_reason") or ""),
        "context": context,
    }


def _generate_delta(chunk: dict) -> "tuple[str, str]":
    return str(chunk.get("response") or ""), str(chunk.get("thinking") or "")


def _chat_delta(chunk: dict) -> "tuple[str, str]":
    message = chunk.get("message")
    if not isinstance(message, dict):
        return "", ""
    return str(message.get("content") or ""), str(message.get("thinking") or "")


def run_plain(model: str, prompt: str, *, think: "bool | None" = None, emit=None,
              options: "dict | None" = None, fmt: "str | None" = None, **limits) -> dict:
    """単発 text → text（ツールなし）。案 A の主経路をストリーミングで回す版。

    ツールを持たないので `readonly: enforced` の宣言が嘘にならない——このモードは
    ファイルもコマンドも触れない。
    """
    body = _payload(model, think=think, options=options, fmt=fmt)
    body["prompt"] = prompt
    return stream_call("/api/generate", body, delta_of=_generate_delta, emit=emit, **limits)


def chat_once(model: str, messages: "list[dict]", *, think: "bool | None" = None, emit=None,
              options: "dict | None" = None, round_no: int = 0,
              fmt: "str | None" = None, **limits) -> dict:
    body = _payload(model, think=think, options=options, fmt=fmt)
    body["messages"] = messages
    return stream_call("/api/chat", body, delta_of=_chat_delta, emit=emit,
                       round_no=round_no, **limits)


def check_command(command: str, toolset: str = DEFAULT_TOOLSET) -> str:
    """実行前のゲート。許可なら空文字、拒否なら**モデルへ返す理由**を返す。

    判定はコマンド名の語彙 + メタ文字の有無だけで行う（パーサは持たない）。ここを
    甘く作ると read セットの保証が形だけになるので、**判定できない形は全部拒否**する
    ——`bash` セット（現行の挙動）だけが素通しで、それ以外は明示的な許可制。
    """
    if (toolset or DEFAULT_TOOLSET) == "bash":
        return ""
    text = str(command or "").strip()
    if not text:
        return "コマンドが空です。"
    bad = _unquoted_metachars(text)
    if bad:
        return (f"シェルの記号 {' '.join(bad)} は {toolset} セットでは使えません"
                "（パイプ・リダイレクト・変数展開・ワイルドカードは効きません）。"
                "コマンドは 1 つだけ、記号を文字として渡すときは引用符で囲んでください。")
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        return f"コマンドを解釈できません（{exc}）。"
    if not argv:
        return "コマンドが空です。"
    name = os.path.basename(argv[0])
    if name == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        if sub in _READ_GIT_SUBCOMMANDS:
            return ""
        return (f"git {sub} は {toolset} セットでは使えません。使えるのは: "
                f"{' '.join(sorted(_READ_GIT_SUBCOMMANDS))}")
    if name not in _READ_COMMANDS:
        return (f"{name} は {toolset} セットでは使えません。使えるのは: "
                f"{' '.join(sorted(_READ_COMMANDS))} と git の読み取り部分コマンド。")
    if name == "find":
        hit = [a for a in argv[1:] if a in _FIND_WRITE_PREDICATES]
        if hit:
            return f"find の {' '.join(hit)} は {toolset} セットでは使えません。"
    return ""


def system_prompt(cwd: str, toolset: str = DEFAULT_TOOLSET) -> str:
    """ツールループの規約。**短さが要件**（毎ラウンドの prefill に載る固定費）。"""
    if (toolset or DEFAULT_TOOLSET) != "bash":
        return (
            "あなたはローカル調査エージェント。道具は**読み取り専用のコマンド**だけです。\n"
            "\n"
            "出力の規約（厳守）:\n"
            "1. 調べるときはコードブロックを 1 つだけ出す。中身はコマンド 1 つ。\n"
            "   実行結果は次のターンで渡されるので、結果を待たずに続きを書かない。\n"
            f"2. 使えるのは {' '.join(sorted(_READ_COMMANDS))} と "
            f"git の {' '.join(sorted(_READ_GIT_SUBCOMMANDS))}。\n"
            "3. パイプ・リダイレクト・変数展開・ワイルドカード（`|` `>` `$` `*` 等）は"
            "使えません（`-name '*.py'` のように引用符で囲めば文字として渡せます）。"
            "ファイルの作成・変更・削除もできません。\n"
            f"4. 完了したらコードブロックを出さず、成果を報告して最後の行に {_DONE_MARKER} と書く。\n"
            "5. 人へ質問はできない。曖昧なら最も妥当な前提を選び、採用した前提を報告に明記する。\n"
            f"6. 作業ディレクトリは {cwd}。\n"
        )
    return (
        "あなたはローカル実行エージェント。道具はシェル（bash）1 つだけです。\n"
        "\n"
        "出力の規約（厳守）:\n"
        "1. コマンドを実行するときは bash のコードブロックを 1 つだけ出す。\n"
        "   実行結果は次のターンで渡されるので、結果を待たずに続きを書かない。\n"
        "2. 結果を見てから次の 1 手を決める。1 ターンに 1 ブロック。\n"
        f"3. 完了したらコードブロックを出さず、成果を報告して最後の行に {_DONE_MARKER} と書く。\n"
        "4. 人へ質問はできない。曖昧なら最も妥当な前提を選び、採用した前提を報告に明記する。\n"
        f"5. 作業ディレクトリは {cwd}。この範囲の外を変更しない。\n"
    )


def extract_command(text: str) -> str:
    """最後のコードブロックを実行するコマンドとして取り出す（無ければ空文字）。"""
    blocks = _FENCE_RE.findall(text or "")
    return blocks[-1].strip() if blocks else ""


def strip_done_marker(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip() != _DONE_MARKER]
    return "\n".join(lines).strip()


def _clip(text: str, limit: int) -> str:
    """長い出力を頭と尻だけ残して詰める（次ラウンドの prefill を膨らませない）。"""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2):]
    return f"{head}\n…（中略 {len(text) - limit} 文字）…\n{tail}"


def run_command(command: str, *, cwd: str, timeout: float, max_chars: int,
                toolset: str = DEFAULT_TOOLSET) -> dict:
    """コマンドを 1 つ実行する（`--tools` = 書き込みモードでのみ呼ばれる）。

    `bash` セットは従来どおりログインシェル素通し。制限セットは **シェルを介さず
    argv を直接実行する**——ゲート（`check_command`）を通った文字列であっても、
    シェルに渡す限り境界はシェルの解釈次第になる。プロセス起動の形そのもので
    「メタ文字は効かない」を担保する。
    """
    started = time.monotonic()
    if (toolset or DEFAULT_TOOLSET) == "bash":
        argv = ["bash", "-lc", command]
    else:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return {"exit_code": 127, "output": f"（解釈できませんでした: {exc}）",
                    "duration_sec": 0.0}
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        out, code = f"（{timeout:.0f} 秒でタイムアウトしました）", 124
    except OSError as exc:
        out, code = f"（実行できませんでした: {exc}）", 127
    return {
        "exit_code": code,
        "output": _clip(out.strip(), max_chars),
        "duration_sec": round(time.monotonic() - started, 2),
    }


def run_loop(model: str, task: str, *, cwd: "str | None" = None, emit=None,
             think: "bool | None" = None, max_rounds: int = DEFAULT_MAX_ROUNDS,
             command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SEC,
             max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
             options: "dict | None" = None, tracker=None,
             toolset: str = DEFAULT_TOOLSET, fmt: "str | None" = None, **limits) -> dict:
    """bash 1 ツールの最小エージェントループ。

    1 ラウンド = 「モデルに聞く → コードブロックがあれば実行して結果を返す」。
    ブロックが無ければ完了（`TASK_COMPLETE`）とみなす。規約から外れた応答には
    最大 `_MAX_NUDGES` 回だけ言い直しを促し、それでも駄目なら最後の本文を成果とする
    ——バックアップ実行系なので、**曖昧な成果でも返す方が止まるより良い**（§0.1 R1）。

    `tracker`（ContextTracker）を渡すと文脈の面倒も見る: 使用量を各ラウンドの
    `llm_end` へ載せ、上限へ近づいたら警告し、**ツール出力を残り容量に合わせて詰め**、
    それでも入らなくなったら `context_exhausted` で明示的に止める。サーバに黙って
    切り捨てさせない（切り捨てられると、指示を失ったまま尤もらしい答えが返る）。

    `toolset` が `bash` 以外なら、実行の手前で `check_command` のゲートを通す。拒否は
    実行せずに理由だけを会話へ**追記**して次ラウンドへ回し（既出は書き換えない＝全再
    prefill を起こさない）、`_MAX_DENIALS` 回で `tool_denied` として止める。
    """
    workdir = str(cwd or os.getcwd())
    messages = [
        {"role": "system", "content": system_prompt(workdir, toolset)},
        {"role": "user", "content": task},
    ]

    # 会話の本文を進捗ログへ残す（`kind="message"`）。他の CLI（claude / codex）が
    # ネイティブのセッション記録に本文を持つのと揃える——持たないと「あの工程で何を
    # 指示して何が返ったか」を後から読める CLI と読めない CLI が混ざる。system は
    # 会話ではなく指示の一部なので出さない（読み手は user / assistant だけを会話とする）。
    def say(role: str, content: str) -> None:
        messages.append({"role": role, "content": content})
        if emit is not None:
            emit("message", role=role, content=content)

    if emit is not None:
        emit("message", role="user", content=task)
    if tracker is not None:
        tracker.add_text(messages[0]["content"] + task)
    tokens_in = tokens_out = 0
    nudges = 0
    denials = 0
    last_text = ""
    status = "max_rounds"
    round_no = 0

    for round_no in range(1, max_rounds + 1):
        if emit is not None:
            emit("round_start", round=round_no, rounds_max=max_rounds)
        result = chat_once(model, messages, think=think, emit=emit, options=options,
                           round_no=round_no, tracker=tracker, fmt=fmt, **limits)
        tokens_in += int(result.get("tokens_in") or 0)
        tokens_out += int(result.get("tokens_out") or 0)
        text = str(result.get("text") or "")
        last_text = text or last_text
        say("assistant", text)
        if tracker is not None and emit is not None and tracker.should_warn():
            emit("context_warn", round=round_no, **tracker.snapshot())

        command = extract_command(text)
        if not command:
            if _DONE_MARKER in text or nudges >= _MAX_NUDGES or not text.strip():
                status = "done" if _DONE_MARKER in text else "no_command"
                if emit is not None:
                    emit("round_end", round=round_no, reason=status)
                break
            nudges += 1
            say("user", "規約から外れています。次の 1 手を bash のコードブロック 1 つで示すか、"
                        f"完了なら成果を報告して最後の行に {_DONE_MARKER} と書いてください。")
            if emit is not None:
                emit("round_end", round=round_no, reason="nudge")
            continue

        # 権限のゲート。拒否は tool_exec を出す前に決める（実行していないコマンドが
        # 実行されたようにログへ残らないこと）。理由は末尾追記でモデルへ返す。
        denied = check_command(command, toolset)
        if denied:
            denials += 1
            if emit is not None:
                emit("tool_denied", round=round_no, toolset=toolset, denials=denials,
                     command=_clip(command, 400), reason=denied)
            if denials > _MAX_DENIALS:
                status = "tool_denied"
                break
            feedback = (f"そのコマンドは実行していません: {denied}\n"
                        "許された範囲で次の 1 手を出すか、この範囲では無理なら理由を報告して "
                        f"最後の行に {_DONE_MARKER} と書いてください。")
            say("user", feedback)
            if tracker is not None:
                tracker.add_text(text + feedback)
            if emit is not None:
                emit("round_end", round=round_no, reason="denied")
            continue

        # 残り容量に合わせてツール出力の上限を絞る。足りなければ、切り捨てられるのを
        # 待たずにこちらで止める——サーバ側の切り捨ては黙って起きるので気づけない。
        # 判定は tool_exec を出す**前**に行う（出してから止めると、実行していない
        # コマンドが実行されたようにログへ残る）。
        allowed = max_output_chars
        if tracker is not None:
            room = tracker.remaining_chars()
            if room >= 0:
                if room < _MIN_TOOL_OUTPUT_CHARS:
                    if emit is not None:
                        emit("context_exhausted", round=round_no,
                             command=_clip(command, 400), **tracker.snapshot())
                    status = "context_exhausted"
                    break
                allowed = min(allowed, room)

        if emit is not None:
            emit("tool_exec", round=round_no, command=_clip(command, 400))
        outcome = run_command(command, cwd=workdir, timeout=command_timeout,
                              max_chars=allowed, toolset=toolset)
        if emit is not None:
            emit("tool_result", round=round_no, exit_code=outcome["exit_code"],
                 duration_sec=outcome["duration_sec"], output_chars=len(outcome["output"]))
        feedback = (f"実行結果（終了コード {outcome['exit_code']}）:\n"
                    f"```\n{outcome['output']}\n```\n"
                    "続けてください（完了なら報告と TASK_COMPLETE）。")
        say("user", feedback)
        if tracker is not None:
            tracker.add_text(text + feedback)
        if emit is not None:
            emit("round_end", round=round_no, reason="tool")

    return {
        "text": strip_done_marker(last_text),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "rounds": round_no,
        "status": status,
        "context": tracker.snapshot() if tracker is not None else {},
    }
