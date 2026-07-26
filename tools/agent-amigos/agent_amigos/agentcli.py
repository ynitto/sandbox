"""agent CLI 呼び出し層 — 全 LLM 呼び出しの単一チョークポイント。

- **全 CLI が agents/<name>.json 定義で動く**（組み込み kiro/claude/copilot/codex も同じ・S9）。
  読み込みと argv 組み立ては agentcore.agentcli の 1 実装（agent-project / agent-flow と共有）。
  契約: schemas/agent-cli.schema.json。探索順: $KIRO_AGENTS_DIR → <cwd>/agents →
  ~/.agents/agents → ~/.kiro/agents → 同梱 agents/（first-wins）。
- stub は LLM を使わないプロトコル検証用で、runner が横取りするためここには来ない。
- 失敗は決定的トリアージで [agent-error:<class>] タグを付ける
  （agent-cli-plugin-design.md。quota/auth/env は環境要因 → amigo を paused に）。
- stub は LLM を使わないプロトコル検証用で、呼び出し側（runner）が封筒を組み立てるため
  ここでは使わない（agent_cli=stub は runner が横取りする）。
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile

from agentcore import agentcli

from .util import strip_ansi

# stub は LLM を使わないプロトコル検証用（runner が横取りする）。組み込み CLI の一覧は
# S9 で持たなくなった——全 CLI が agents/<name>.json 定義で動く。
BUILTIN_CLIS = ("stub",)
AGENT_ERROR_ENV_CLASSES = ("quota", "auth", "env")

DEFAULT_TIMEOUT = 600.0
DEFAULT_ARGV_LIMIT = 100000

_AGENT_ERROR_TAG_RE = re.compile(r"\[agent-error:(quota|auth|env|transient)\]")
_AGENT_ERROR_PATTERNS = (
    ("quota", re.compile(r"usage limit|quota exceeded|rate.?limit|too many requests", re.I),
     "利用上限に達しています（時間をおくか、プラン・クレジットを見直してください）"),
    ("auth", re.compile(r"AccessDenied|Unauthorized|authentication failed|not authenticated"
                        r"|SendMessageError|please (re)?login", re.I),
     "認証に失敗しています（再ログインが必要です）"),
    ("env", re.compile(r"issue with the selected model|invalid model"
                       r"|model .{0,40}(not found|does not exist)|may not have access to it"
                       r"|command not found|No such file or directory", re.I),
     "実行環境の問題です（モデル名・CLI の導入・PATH を確認してください）"),
    ("transient", re.compile(r"timed? ?out|connection (reset|refused|closed)|ECONNRESET"
                             r"|ETIMEDOUT|temporarily unavailable|service unavailable|overloaded",
                             re.I),
     "一時的なエラーです（自動でやり直します）"),
)

_PLUGIN_CACHE: "dict[str, dict]" = {}


def load_agent_plugin(name: str) -> dict:
    """agents/<name>.json を読む（agentcore へ委譲）。見つからない・壊れているは RuntimeError。"""
    key = str(name or "").strip().lower()
    if key in _PLUGIN_CACHE:
        return _PLUGIN_CACHE[key]
    try:
        # キャッシュはここで持つ（二重キャッシュにすると定義の差し替えが効かなくなる）
        spec = agentcli.load_cli(key, use_cache=False)
    except agentcli.AgentCliError as e:
        raise RuntimeError(f"[agent-error:env] {e}") from e
    _PLUGIN_CACHE[key] = spec
    return spec


def _plugin_error_patterns() -> tuple:
    out = []
    for spec in _PLUGIN_CACHE.values():
        out.extend(spec.get("errors") or [])
    return tuple(out)


def classify_agent_failure(blob: str) -> "tuple[str, str] | None":
    """エラー本文を (class, hint) に分類する（該当なしは None＝内容の問題）。
    既にタグ付きならそれが正。プラグイン定義の errors を汎用パターンより先に評価する。"""
    text = str(blob or "")
    m = _AGENT_ERROR_TAG_RE.search(text)
    if m:
        hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == m.group(1)), "")
        return m.group(1), hint
    for cls, pat, hint in _plugin_error_patterns() + _AGENT_ERROR_PATTERNS:
        if pat.search(text):
            return cls, hint
    return None


def _failure_message(cli: str, rc: int, out: str, err: str) -> str:
    """失敗を人が原因に辿り着ける文言にする。エラーは末尾に出るので末尾を拾い、
    トリアージ結果は機械可読タグとして先頭に載せる（agent-flow の教訓を踏襲）。"""
    blob = f"{out or ''}\n{err or ''}"
    triage = classify_agent_failure(blob)
    head = f"{cli} 失敗 (rc={rc})"
    if triage:
        cls, hint = triage
        head = f"[agent-error:{cls}] {head}" + (f": {hint}" if hint else "")
    tail = (err or out or "").strip()
    return f"{head}\n{tail[-500:]}" if tail else head


def _spill_prompt(prompt: str) -> "tuple[str, str]":
    """argv 長制限に当たるプロンプトを一時ファイルへ退避し、参照渡しの短い指示に置き換える。"""
    fd, spill = tempfile.mkstemp(prefix="agent-amigos-prompt-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(prompt)
    return (spill,
            "以下のファイルにこのターンの全文（役割・ミッション・新着メッセージを含む）があります。"
            f"必ずファイルの内容を読み込み、その指示に従ってください: {spill}")


def run_agent(prompt: str, cli: str, model: "str | None" = None,
              timeout: "float | None" = None) -> str:
    """agent CLI を 1 回呼び出してテキスト応答を返す。失敗は RuntimeError
    （トリアージタグ付き文言）。stub はここに来ない（runner が横取りする）。"""
    cli = (cli or "kiro").strip().lower()
    spill = None
    plug = load_agent_plugin(cli)
    # argv 渡しで長すぎるプロンプトは一時ファイルへ退避し、参照渡しの短い指示に置き換える。
    # argv 長制限は OS の事情なので定義ではなくここで見る（定義側の spill は「stdin を読まない
    # CLI の癖」への対処で別物）。
    if plug["prompt_via"] == "argv" and len(prompt.encode("utf-8")) > DEFAULT_ARGV_LIMIT:
        spill, prompt = _spill_prompt(prompt)
    built = agentcli.headless_cmd(plug, model, prompt)
    cmd, stdin_text, out_file = built["argv"], built["stdin"], built["output_file"]
    # 発生源で色を抑止（NO_COLOR/TERM=dumb）。残った ANSI は strip_ansi で除去する二段構え
    # （agent-project と同じ扱い）。プラグイン定義の env は最後に載せるので上書きできる。
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", **(plug.get("env") or {})}
    eff_timeout = plug.get("timeout") or timeout or DEFAULT_TIMEOUT
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", input=stdin_text, timeout=eff_timeout, env=env)
    except subprocess.TimeoutExpired:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)
        raise RuntimeError(f"[agent-error:transient] {cmd[0]} タイムアウト（{eff_timeout:.0f}s 超過）")
    except FileNotFoundError:
        raise RuntimeError(f"[agent-error:env] {cmd[0]} が見つかりません（PATH を確認してください）")
    finally:
        if spill:
            with contextlib.suppress(OSError):
                os.remove(spill)
    try:
        if proc.returncode != 0:
            raise RuntimeError(_failure_message(cmd[0], proc.returncode, proc.stdout, proc.stderr))
        text = strip_ansi(proc.stdout).strip()
        if out_file:
            with contextlib.suppress(OSError):
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
        if not text and not plug.get("empty_output_is_error", True):
            return ""
        if not text:
            raise RuntimeError(f"{cmd[0]} の応答が空でした")
        return text
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)
