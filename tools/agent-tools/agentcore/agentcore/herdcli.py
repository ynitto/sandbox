"""agentcore.herdcli — agent-herd のサブコマンド面（busybox 型ディスパッチャ）。

## 何をするモジュールか

`agent-herd` / `agent-aider` / `agent-ollama` / `agent-opencode` は**同じ 1 つの zipapp**で、
`basename(argv[0])` を見てサブコマンドへ振り分ける。`agent-aider …` と
`agent-herd aider …` は完全に同じコードパスに落ちる（別名は互換シムではなく本体そのもの
なので、「シムだけ古い」という不整合が構造的に起きない）。

## 契約の背骨

**サブコマンドは adapter の名前であって定義の名前ではない。**
`ollama-json` / `ollama-list` / `ollama-verify` は `agents/*.json` の**定義**であって
adapter ではない（実体はどれも ollama adapter にフラグを足したもの）。だからサブコマンドに
は載せず、`exec` から引く。誤って `agent-herd ollama-json` と打った場合は黙って別解釈せず、
`exec` を案内する明示エラーで止める。

設計: docs/plans/2026-08-25-agent-herd-unified-entry-design.md §3 / §4。
仕様: docs/specs/agent-herd-spec.md（綴り・終了コード・エラー文はあちらが正典）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agentcore import agentcli

PROG = "agent-herd"

# basename(argv[0]) → サブコマンド。ここに無い名前は argv[1] をサブコマンドとして読む。
ALIAS_BY_ARGV0 = {
    "agent-aider": "aider",
    "agent-ollama": "ollama",
    "agent-opencode": "opencode",
}

# adapter サブコマンド → その実体を返す関数（import は呼ばれたときだけ行う。
# 起動のたびに 3 adapter を読み込むと、observability だけの `defs` まで重くなる）。
def _aider_main():
    from agentcore import aider_adapter
    return aider_adapter.main


def _ollama_main():
    from agentcore import ollama_adapter
    return ollama_adapter.main


def _opencode_main():
    from agentcore import opencode_adapter
    return opencode_adapter.main


ADAPTERS = {
    "aider": _aider_main,
    "ollama": _ollama_main,
    "opencode": _opencode_main,
}

# 観測モードの別名。第 2 実装ではなく、ollama adapter の同名フラグへそのまま渡す
# （`agent-ollama --status` の綴りも残るので、外部の手順書を壊さない）。
OBSERVE_ALIASES = {
    "status": "--status",
    "follow": "--follow",
    "replay": "--replay",
}

HELP = f"""使い方: {PROG} <サブコマンド> [オプション]

  LAN 上の ollama を動かす実行系の入口。`agent-aider` / `agent-ollama` /
  `agent-opencode` はこの実行ファイルへの別名で、打ち方も出力も従来どおり。

  実行（adapter を直に叩く。引数は adapter へ素通し）:
    aider …               Aider をヘッドレスで回す（= agent-aider …）
    ollama …              ollama を回す（= agent-ollama …。--tools / --tui も含む）
    opencode …            opencode を回す（= agent-opencode …）

  定義経由（agents/<名前>.json を読んで argv を組む）:
    exec <cli> [オプション]  定義どおりにヘッドレス実行する（本文は stdin）
    defs [<名前>]           定義の一覧・実効 argv を見る（--json / --purpose / --model）

  対話:
    chat [<cli>]          定義の interactive で対話起動する（--model）

  工程:
    harness <種別> …       限定ツール契約のハーネスを単独で回す

  観測（LLM を呼ばない）:
    status [LOG]          いまの進捗を 1 行 JSON で返す
    follow [LOG]          進捗ログを追尾表示する
    replay [PATH] …       記録済みプロンプトを再生して品質を測る

  各 adapter の詳しい使い方はその adapter に聞く: {PROG} ollama --help

サブコマンドは **adapter の名前**であって定義の名前ではない。ollama-json のような
定義を指定して回すときは `{PROG} exec ollama-json …` を使う。"""


def _err(message: str, *, err=None) -> None:
    print(f"[agent-error:env] {PROG}: {message}", file=err or sys.stderr)


def _known_definition(name: str) -> bool:
    """定義として解決できる名前か（未知サブコマンドの案内を正確にするためだけに使う）。"""
    try:
        agentcli.load_cli(name)
    except Exception:
        return False
    return True


def _definition_names() -> "list[str]":
    names: "list[str]" = []
    for directory in agentcli.plugin_dirs():
        try:
            entries = sorted(Path(directory).glob("*.json"))
        except OSError:
            continue
        for path in entries:
            if path.stem not in names:
                names.append(path.stem)
    return sorted(names)


# ---------------------------------------------------------------------------
# defs — 定義の観測
# ---------------------------------------------------------------------------
def _defs_payload(name: str, *, model: "str | None", purpose: "str | None") -> dict:
    spec = agentcli.load_cli(name)
    resolved_name, resolved_model = name, model
    variant = None
    if purpose:
        variant = agentcli.resolve_variant(name, purpose)
        if variant:
            resolved_name = variant["agent_cli"]
            spec = agentcli.load_cli(resolved_name)
            resolved_model = model or variant.get("default_model")
    built_write = agentcli.headless_cmd(spec, resolved_model, "<PROMPT>", readonly=False)
    built_read = agentcli.headless_cmd(spec, resolved_model, "<PROMPT>", readonly=True)
    try:
        interactive = agentcli.interactive_cmd(spec, resolved_model)
    except agentcli.AgentCliError:
        interactive = None
    return {
        "name": spec["name"],
        "path": str(spec["path"]),
        "requested": name,
        "resolved_via_variant": bool(variant),
        "headless_autonomy": spec.get("headless_autonomy"),
        "readonly": spec.get("readonly"),
        "relative_cost": spec.get("relative_cost"),
        "default_model": spec.get("default_model"),
        "model": resolved_model or spec.get("default_model"),
        "prompt_via": spec.get("prompt_via"),
        "variants": spec.get("variants") or {},
        "argv_write": built_write["argv"],
        "argv_readonly": built_read["argv"],
        "argv_interactive": interactive,
        "timeout": built_write.get("timeout"),
    }


def cmd_defs(argv, *, out=None, err=None) -> int:
    out = out or sys.stdout
    as_json = False
    name = None
    model = purpose = None
    tokens = list(argv)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
        elif token in ("--model", "--purpose"):
            if i + 1 >= len(tokens):
                _err(f"{token} には値が必要です", err=err)
                return 2
            i += 1
            if token == "--model":
                model = tokens[i]
            else:
                purpose = tokens[i]
        elif token.startswith("-"):
            _err(f"defs は {token} を受け取りません", err=err)
            return 2
        elif name is None:
            name = token
        else:
            _err(f"defs が受け取る定義名は 1 つだけです（2 つ目: {token}）", err=err)
            return 2
        i += 1

    if name is None:
        names = _definition_names()
        if as_json:
            print(json.dumps({"definitions": names}, ensure_ascii=False), file=out)
            return 0
        if not names:
            _err("解決できる定義がありません（agents/*.json を配置してください）", err=err)
            return 1
        print("解決できる定義:", file=out)
        for item in names:
            print(f"  {item}", file=out)
        print(f"\n1 件の中身と実効 argv: {PROG} defs <名前>", file=out)
        return 0

    try:
        payload = _defs_payload(name, model=model, purpose=purpose)
    except agentcli.AgentCliError as exc:
        _err(str(exc), err=err)
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False), file=out)
        return 0
    print(f"{payload['name']}  ({payload['path']})", file=out)
    if payload["resolved_via_variant"]:
        print(f"  ← {payload['requested']} の variant として解決", file=out)
    print(f"  autonomy={payload['headless_autonomy']}  readonly={payload['readonly']}"
          f"  cost={payload['relative_cost']}  model={payload['model']}", file=out)
    print(f"  write    : {' '.join(payload['argv_write'])}", file=out)
    print(f"  readonly : {' '.join(payload['argv_readonly'])}", file=out)
    if payload["argv_interactive"]:
        print(f"  chat     : {' '.join(payload['argv_interactive'])}", file=out)
    if payload["variants"]:
        pairs = ", ".join(f"{k}→{v}" for k, v in sorted(payload["variants"].items()))
        print(f"  variants : {pairs}", file=out)
    return 0


# ---------------------------------------------------------------------------
# exec — 定義経由のヘッドレス実行
# ---------------------------------------------------------------------------
def cmd_exec(argv, *, err=None, runner=None, stdin=None) -> int:
    err = err or sys.stderr
    tokens = list(argv)
    if not tokens:
        _err(f"exec には定義名が必要です（例: {PROG} exec ollama-json --model gemma4:e4b）", err=err)
        return 2
    name, tokens = tokens[0], tokens[1:]
    model = purpose = None
    readonly = False
    files: "list[str]" = []
    read_files: "list[str]" = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--readonly":
            readonly = True
        elif token in ("--model", "--purpose", "--file", "--read"):
            if i + 1 >= len(tokens):
                _err(f"{token} には値が必要です", err=err)
                return 2
            i += 1
            value = tokens[i]
            if token == "--model":
                model = value
            elif token == "--purpose":
                purpose = value
            elif token == "--file":
                files.append(value)
            else:
                read_files.append(value)
        else:
            _err(f"exec は {token} を受け取りません", err=err)
            return 2
        i += 1

    try:
        spec = agentcli.load_cli(name)
        if purpose:
            variant = agentcli.resolve_variant(name, purpose)
            if variant:
                spec = agentcli.load_cli(variant["agent_cli"])
                model = model or variant.get("default_model")
        prompt = _read_prompt(stdin)
        built = agentcli.headless_cmd(spec, model, prompt, readonly=readonly,
                                      files=files or None, read_files=read_files or None)
    except agentcli.AgentCliError as exc:
        _err(str(exc), err=err)
        return 1
    warning = built.get("readonly_warning")
    if warning:
        print(f"@agent-note {warning}", file=err)
    run = runner or _run_argv
    return run(built)


def _read_prompt(stream=None) -> str:
    """本文は stdin から読む（フィルタとしての既定）。

    端末が繋がっているとき（人が引数だけ打って Enter した場合）は読まない——
    プロンプトも出さずに黙って入力待ちで固まるのが一番わかりにくい失敗なので、
    本文なしとして先へ進め、定義側の empty_output_is_error 等に判断させる。
    """
    stream = sys.stdin if stream is None else stream
    try:
        if stream.isatty():
            return ""
    except (AttributeError, ValueError):
        pass
    try:
        return stream.read()
    except (OSError, ValueError):
        return ""


def _run_argv(built: dict) -> int:
    env = dict(os.environ)
    env.update(built.get("env") or {})
    try:
        result = subprocess.run(built["argv"], env=env,
                                input=(built.get("stdin") or "").encode("utf-8")
                                if built.get("stdin") is not None else None)
    except FileNotFoundError:
        _err(f"{built['argv'][0]} が見つかりません")
        return 127
    return result.returncode


# ---------------------------------------------------------------------------
# chat — 対話の統合入口
# ---------------------------------------------------------------------------
DEFAULT_CHAT_CLI = "ollama"


def cmd_chat(argv, *, err=None, launcher=None) -> int:
    err = err or sys.stderr
    tokens = list(argv)
    name = DEFAULT_CHAT_CLI
    model = None
    i = 0
    seen_name = False
    while i < len(tokens):
        token = tokens[i]
        if token == "--model":
            if i + 1 >= len(tokens):
                _err("--model には値が必要です", err=err)
                return 2
            i += 1
            model = tokens[i]
        elif token.startswith("-"):
            _err(f"chat は {token} を受け取りません", err=err)
            return 2
        elif not seen_name:
            name, seen_name = token, True
        else:
            _err(f"chat が受け取る CLI 名は 1 つだけです（2 つ目: {token}）", err=err)
            return 2
        i += 1

    try:
        spec = agentcli.load_cli(name)
        argv_out = agentcli.interactive_cmd(spec, model)
    except agentcli.AgentCliError as exc:
        _err(str(exc), err=err)
        return 1
    return (launcher or _launch)(argv_out)


def _launch(argv: "list[str]") -> int:
    """対話起動。自分自身を指しているなら in-process で入る（余計なプロセスを挟まない）。

    定義の interactive.command は配布名（`agent-ollama --tui …`）で書いてあるので、
    開発木のように PATH にそれが無い環境でも動くよう、まず自分の中の adapter へ落とす。
    """
    head = os.path.basename(argv[0])
    sub = ALIAS_BY_ARGV0.get(head)
    if head == PROG and len(argv) > 1:
        sub, argv = argv[1], argv[1:]
    if sub in ADAPTERS:
        return ADAPTERS[sub]()(argv[1:])
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        _err(f"対話起動に失敗しました（{argv[0]}）: {exc}")
        return 127
    return 0  # pragma: no cover - execvp は戻らない


# ---------------------------------------------------------------------------
# harness — P2 で agentcore へ移設する（いまは所在を答えるだけ）
# ---------------------------------------------------------------------------
def cmd_harness(argv, *, err=None) -> int:
    _err("harness はまだこの入口にありません（移行フェーズ P2）。"
         "いまは agent-loop 側の実装を使ってください: "
         "agent-loop statemachine --workflow <定義> --cli <名前>", err=err)
    return 2


# ---------------------------------------------------------------------------
# ディスパッチ
# ---------------------------------------------------------------------------
def resolve(prog: str, argv: "list[str]") -> "tuple[str | None, list[str]]":
    """(サブコマンド, 残りの引数) を決める。判定は basename(argv[0]) の 1 回だけ。"""
    alias = ALIAS_BY_ARGV0.get(os.path.basename(prog or ""))
    if alias:
        return alias, list(argv)
    tokens = list(argv)
    if not tokens:
        return None, []
    return tokens[0], tokens[1:]


def main(argv=None, prog=None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    sub, rest = resolve(prog if prog is not None else sys.argv[0], tokens)

    if sub is None or sub in ("-h", "--help", "help"):
        print(HELP)
        return 0 if sub else 2
    if sub in ("--version", "version"):
        from agentcore import __version__
        print(f"{PROG} {__version__}")
        return 0

    if sub in ADAPTERS:
        return ADAPTERS[sub]()(rest)
    if sub in OBSERVE_ALIASES:
        return ADAPTERS["ollama"]()([OBSERVE_ALIASES[sub], *rest])
    if sub == "defs":
        return cmd_defs(rest)
    if sub == "exec":
        return cmd_exec(rest)
    if sub == "chat":
        return cmd_chat(rest)
    if sub == "harness":
        return cmd_harness(rest)

    # 未知。定義名なら exec を案内する（黙って別解釈しない）。
    if _known_definition(sub):
        _err(f"{sub!r} は定義であって adapter ではありません。"
             f"定義を指定して回すなら: {PROG} exec {sub} [--model <モデル>]")
        return 2
    known = sorted({*ADAPTERS, *OBSERVE_ALIASES, "defs", "exec", "chat", "harness"})
    _err(f"未知のサブコマンド: {sub!r}（使えるのは {', '.join(known)}）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
