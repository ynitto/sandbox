"""agent CLI 呼び出し層 — 全 LLM 呼び出しの単一チョークポイント。

- 組み込み: kiro / claude / copilot / codex / stub。
- それ以外は agents/<name>.json プラグイン定義（契約: schemas/agent-cli.schema.json、
  探索順: $KIRO_AGENTS_DIR → <cwd>/agents → ~/.agent/agents → ~/.kiro/agents）。
  agent-flow / agent-project と同じデータ契約を読む（ローダは自前 = コード依存を作らない）。
- 失敗は決定的トリアージで [agent-error:<class>] タグを付ける
  （agent-cli-plugin-design.md。quota/auth/env は環境要因 → amigo を paused に）。
- stub は LLM を使わないプロトコル検証用で、呼び出し側（runner）が封筒を組み立てるため
  ここでは使わない（agent_cli=stub は runner が横取りする）。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile

from .util import strip_ansi

BUILTIN_CLIS = ("kiro", "claude", "copilot", "codex", "stub")
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

_PLUGIN_CACHE: "dict[str, dict | None]" = {}


def _plugin_dirs() -> list:
    dirs = []
    envd = os.environ.get("KIRO_AGENTS_DIR")
    if envd:
        dirs.append(os.path.expanduser(envd))
    dirs.append(os.path.join(os.getcwd(), "agents"))
    dirs.append(os.path.expanduser("~/.agent/agents"))
    dirs.append(os.path.expanduser("~/.kiro/agents"))
    return dirs


def _normalize_plugin(name: str, raw: dict, path: str) -> dict:
    cmd = raw.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise RuntimeError(f"エージェント定義 {path}: command は文字列配列が必須です")
    output = str(raw.get("output", "stdout"))
    if output == "file" and not any("{output_file}" in c for c in cmd):
        raise RuntimeError(f"エージェント定義 {path}: output=file には command 中の "
                           "{output_file} プレースホルダが必要です")
    errors = []
    for e in (raw.get("errors") or []):
        try:
            errors.append((str(e.get("class", "env")),
                           re.compile(str(e.get("match", "")), re.I),
                           str(e.get("hint", ""))))
        except re.error as ex:
            raise RuntimeError(f"エージェント定義 {path}: errors.match が正規表現として不正です: {ex}")
    return {"name": name, "command": list(cmd),
            "prompt_via": str(raw.get("prompt_via", "stdin")),
            "prompt_flag": raw.get("prompt_flag"),
            "model_flag": raw.get("model_flag"),
            "default_model": raw.get("default_model"),
            "output": output, "env": dict(raw.get("env") or {}),
            "timeout": raw.get("timeout"),
            "empty_output_is_error": bool(raw.get("empty_output_is_error", True)),
            "errors": errors, "path": str(path)}


def load_agent_plugin(name: str) -> "dict | None":
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key in _PLUGIN_CACHE:
        return _PLUGIN_CACHE[key]
    spec = None
    for d in _plugin_dirs():
        p = os.path.join(d, f"{key}.json")
        try:
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except ValueError as e:
            raise RuntimeError(f"エージェント定義 {p} を JSON として読めません: {e}")
        except OSError:
            continue
        spec = _normalize_plugin(key, raw, p)
        break
    _PLUGIN_CACHE[key] = spec
    return spec


def _plugin_error_patterns() -> tuple:
    out = []
    for spec in _PLUGIN_CACHE.values():
        if spec:
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


def _plugin_cmd(plug: dict, model: "str | None", prompt: str):
    """プラグイン定義から (argv, stdin テキスト, 最終応答ファイル) を組み立てる（決定的）。"""
    model = model or plug.get("default_model") or None
    out_file = None
    cmd = []
    used_model = False
    for part in plug["command"]:
        if "{output_file}" in part:
            if out_file is None:
                fd, out_file = tempfile.mkstemp(prefix=f"agent-amigos-{plug['name']}-", suffix=".txt")
                os.close(fd)
            part = part.replace("{output_file}", out_file)
        if "{model}" in part:
            if not model:
                continue
            part = part.replace("{model}", model)
            used_model = True
        cmd.append(part)
    if model and not used_model and plug.get("model_flag"):
        cmd += [str(plug["model_flag"]), model]
    if plug["prompt_via"] == "argv":
        if plug.get("prompt_flag"):
            cmd += [str(plug["prompt_flag"]), prompt]
        else:
            cmd.append(prompt)
        return cmd, None, out_file
    return cmd, prompt, out_file


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
    stdin_text = None
    spill = None
    out_file = None
    plug = None
    if cli == "claude":
        cmd = ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        stdin_text = prompt
    elif cli == "codex":
        fd, out_file = tempfile.mkstemp(prefix="agent-amigos-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        cmd.append("-")
        stdin_text = prompt
    elif cli in ("copilot", "kiro"):
        if cli == "copilot":
            cmd = ["copilot", "-s", "--allow-all-tools", "--allow-all-paths", "--no-color"]
        else:
            cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
        if model:
            cmd += ["--model", model]
        if len(prompt.encode("utf-8")) > DEFAULT_ARGV_LIMIT:
            spill, prompt = _spill_prompt(prompt)
        cmd += (["-p", prompt] if cli == "copilot" else [prompt])
    else:
        plug = load_agent_plugin(cli)
        if plug is None:
            raise RuntimeError(
                f"[agent-error:env] 未知の agent_cli です: {cli!r}（組み込みは "
                f"kiro/claude/copilot/codex/stub。それ以外は agents/{cli}.json 定義が必要です — "
                f"契約: schemas/agent-cli.schema.json）")
        if plug["prompt_via"] == "argv" and len(prompt.encode("utf-8")) > DEFAULT_ARGV_LIMIT:
            spill, prompt = _spill_prompt(prompt)
        cmd, stdin_text, out_file = _plugin_cmd(plug, model, prompt)
    env = {**os.environ, **((plug or {}).get("env") or {})}
    eff_timeout = (plug or {}).get("timeout") or timeout or DEFAULT_TIMEOUT
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
        if not text and plug is not None and not plug.get("empty_output_is_error", True):
            return ""
        if not text:
            raise RuntimeError(f"{cmd[0]} の応答が空でした")
        return text
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)
