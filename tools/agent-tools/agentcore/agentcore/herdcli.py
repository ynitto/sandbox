"""agentcore.herdcli — agent-herd のサブコマンド面（busybox 型ディスパッチャ）。

## 何をするモジュールか

`agent-herd` / `agent-aider` / `agent-ollama` は**同じ 1 つの zipapp**で、
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

from agentcore import agentcli, procgroup, slashroute

PROG = "agent-herd"

# basename(argv[0]) → サブコマンド。ここに無い名前は argv[1] をサブコマンドとして読む。
ALIAS_BY_ARGV0 = {
    "agent-aider": "aider",
    "agent-ollama": "ollama",
}

# adapter サブコマンド → その実体を返す関数（import は呼ばれたときだけ行う。
# 起動のたびに全 adapter を読み込むと、observability だけの `defs` まで重くなる）。
def _aider_main():
    from agentcore import aider_adapter
    return aider_adapter.main


def _ollama_main():
    from agentcore import ollama_adapter
    return ollama_adapter.main


def _edit_main():
    from agentcore import editagent
    return editagent.main


ADAPTERS = {
    "aider": _aider_main,
    "ollama": _ollama_main,
    # aider を使わない編集適用（去就を測るための対照実装。設計 §3.6・未決 5）。
    "edit": _edit_main,
}

# 観測モードの別名。第 2 実装ではなく、ollama adapter の同名フラグへそのまま渡す
# （`agent-ollama --status` の綴りも残るので、外部の手順書を壊さない）。
OBSERVE_ALIASES = {
    "status": "--status",
    "follow": "--follow",
    "replay": "--replay",
}

HELP = f"""使い方: {PROG} [オプション]              # クラウド CLI と同型の入口
       {PROG} <サブコマンド> [オプション]  # 従来の綴り（すべて温存）

  LAN 上の ollama を動かす実行系の入口。`agent-aider` / `agent-ollama` は
  この実行ファイルへの別名で、打ち方も出力も従来どおり。

  そのまま打つ（引数なし＝対話、-p ＝非対話 1 回）:
    （引数なし）          対話（TUI）で開く
    -p ["…"]              1 回だけ実行する（値を省くと本文は stdin）
    --agent <名前>        バックエンド。agents/<名前>.json の**定義名**
                          （ollama-json のような profile 綴りも解ける）
    --model <モデル>      モデル
    --purpose <用途>      用途の 1 語（宣言 / variants が起動形を決める）
    --readonly            読み取り専用で走らせる
    --dir <パス>          作業ディレクトリ
    --continue            直前のセッションを続ける
    --resume <ID>         ID を指定して再開する（ID はログの名前。status で見える）

  実行（adapter を直に叩く。引数は adapter へ素通し）:
    aider …               Aider をヘッドレスで回す（= agent-aider …）
    ollama …              ollama を回す（= agent-ollama …。--tools / --tui も含む）
    edit …                aider を使わない編集適用（SEARCH/REPLACE を自前で当てる）

  定義経由（agents/<名前>.json を読んで argv を組む）:
    exec <cli> [オプション]  定義どおりにヘッドレス実行する（本文は stdin）
    defs [<名前>]           定義の一覧・実効 argv を見る（--json / --purpose / --model）

  対話:
    chat [<cli>]          定義の interactive で対話起動する（--model）

  工程（tmux もデーモンも要らない）:
    harness statemachine … ステートマシンを完走させる
    harness run PROMPT…    プロンプト 1 件を 1 回実行する

  判定（抽出 → 機械判定。採否はモデルではなく機械が決める）:
    decide --decision <契約>  候補（stdin）から事実を抽出し、契約どおりに選別する

  観測（LLM を呼ばない）:
    status [LOG]          いまの進捗を 1 行 JSON で返す
    follow [LOG]          進捗ログを追尾表示する
    replay [PATH] …       記録済みプロンプトを再生して品質を測る

  各 adapter の詳しい使い方はその adapter に聞く: {PROG} ollama --help

サブコマンドは **adapter の名前**であって定義の名前ではない。ollama-json のような
定義を指定して回すときは `{PROG} exec ollama-json …`（または `--agent ollama-json`）を使う。

継続の実体はバックエンドで違う。ネイティブのセッション機能を持つ CLI は定義の
`continue_args` / `resume_args` がそのまま argv へ載り、持たない自前 CLI
（agent-ollama / agent-aider）は**前回の会話を材料として組み直す**。どちらも無い定義は
明示エラーで止まる（黙って新規セッションとして走らせない）。"""


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
    via_variant = False
    if purpose:
        # 用途 → 起動形の調停は slashroute の 1 実装（設計 2026-08-27 §3.3）。
        routed = slashroute.resolve(command=purpose, cli=name, model=model,
                                    explicit_model=bool(model))
        via_variant = routed["variant"]
        if via_variant:
            resolved_name = routed["agent_cli"]
            spec = agentcli.load_cli(resolved_name)
            resolved_model = routed["model"]
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
        # 用途別の起動形（profile）は同じエージェントの中にある。台帳と格付けのキーは
        # `name` のほうで、profile は起動差でしかない。
        "profile": spec.get("profile") or "",
        "profiles": sorted(spec.get("profiles") or {}),
        "resolved_via_variant": via_variant,
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


def _role_line(spec: dict) -> str:
    """定義 1 件の役割を**宣言から導いて** 1 行にする（新しいフィールドは足さない）。

    一覧が名前を並べるだけだと、ローカルに 2 つ（`aider` / `ollama`）並んだときに
    「どっちを選ぶのか」が読み取れない。実際には選ぶ場面はほとんど無い——用途別の
    振り替えは `variants` が宣言していて、15 用途はどちらを base にしても同じ profile へ
    行く。**分かれるのは編集・実装をどちらでやるかの 1 点だけ**なので、それが読める
    ように「自分で何をするか」と「何を振り替えるか」を出す。
    """
    if str((spec.get("command") or [""])[0]) != PROG:
        return ""                      # クラウド CLI はこの入口を通らない（仕様書 §1）
    own = ("渡したファイルを直す編集役（自分では探索しない）"
           if spec.get("headless_autonomy") == "single-shot"
           else "自分で調べて実行するツールループ")
    variants = spec.get("variants") or {}
    if not variants:
        return own
    targets = sorted(set(variants.values()))
    return f"{own}。{len(variants)} 用途は {', '.join(targets)} へ振り替え"


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
            try:
                spec = agentcli.load_cli(item)
            except agentcli.AgentCliError:
                print(f"  {item}", file=out)
                continue
            profiles = sorted(spec.get("profiles") or {})
            suffix = f"    profiles: {', '.join(profiles)}" if profiles else ""
            print(f"  {item}{suffix}", file=out)
            role = _role_line(spec)
            if role:
                print(f"      {role}", file=out)
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
    if payload["profile"]:
        print(f"  ← {payload['requested']} = {payload['name']} の profile "
              f"{payload['profile']!r}", file=out)
    elif payload["resolved_via_variant"]:
        print(f"  ← {payload['requested']} の variant として解決", file=out)
    if payload["profiles"]:
        print(f"  profiles : {', '.join(payload['profiles'])}", file=out)
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
            routed = slashroute.resolve(command=purpose, cli=name, model=model,
                                        explicit_model=bool(model))
            if routed["variant"]:
                spec = agentcli.load_cli(routed["agent_cli"])
                model = routed["model"]
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
# decide — 抽出 → 機械判定（決定化パイプ）の入口
# ---------------------------------------------------------------------------
DEFAULT_DECIDE_CLI = "ollama-json"


def cmd_decide(argv, *, err=None, runner=None, stdin=None, out=None) -> int:
    """判定契約に沿って候補を選別する。**モデルは事実の抽出だけ、採否は機械が決める。**

    多基準の採否をモデルに訊くと 0/5、事実だけ抽出させて機械が決めると 5/5（実測
    2026-08-29）。この差は本番（agent-flow の filter / judge）では効いていたが、
    agent-herd から直に使う口が無く、statemachine やスクリプトは各自でモデルへ
    「選べ」と訊くしかなかった。

    判定規則はここに書かない——`agentcore.nodecontract` の 1 実装
    （fact_extraction_directive → normalize_facts → decide_candidates）を並べるだけ。
    2 実装に割れると、同じ契約で本番と CLI の結論が変わる。
    """
    err = err or sys.stderr
    out = out or sys.stdout
    tokens = list(argv)
    decision_arg = None
    name = DEFAULT_DECIDE_CLI
    model = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("--decision", "--agent", "--model"):
            if i + 1 >= len(tokens):
                _err(f"{token} には値が必要です", err=err)
                return 2
            i += 1
            if token == "--decision":
                decision_arg = tokens[i]
            elif token == "--agent":
                name = tokens[i]
            else:
                model = tokens[i]
        else:
            _err(f"decide は {token} を受け取りません"
                 "（契約は --decision、候補の本文は stdin）", err=err)
            return 2
        i += 1
    if not decision_arg:
        _err("--decision に判定契約（JSON のパスか JSON そのもの）が必要です", err=err)
        return 2

    from agentcore import llmjson, nodecontract

    try:
        path = Path(decision_arg).expanduser()
        raw = path.read_text(encoding="utf-8") if path.is_file() else decision_arg
        decision = json.loads(raw)
    except (OSError, ValueError) as exc:
        _err(f"判定契約を読めません: {exc}", err=err)
        return 2
    errors = nodecontract.decision_contract_errors(decision)
    if errors:
        # 黙って「モデルに訊く」へ倒さない。倒すと、宣言したのに機械判定が効いていない
        # という一番わかりにくい形になる（この設計が潰そうとしているもの）。
        _err("判定契約が不正です: " + " / ".join(errors), err=err)
        return 2

    body = _read_prompt(stdin)
    if not body.strip():
        _err("候補の本文が空です（stdin で渡します）", err=err)
        return 2
    prompt = nodecontract.fact_extraction_directive(decision) + "\n\n候補:\n" + body
    try:
        spec = agentcli.load_cli(name)
        built = agentcli.headless_cmd(spec, model, prompt, readonly=True)
    except agentcli.AgentCliError as exc:
        _err(str(exc), err=err)
        return 1
    run = runner or _capture_argv
    rc, text = run(built)
    if rc != 0:
        _err(f"{name} が失敗しました（rc={rc}）", err=err)
        return 1
    try:
        data = llmjson.extract_json(text, what="抽出結果")
    except ValueError as exc:
        _err(f"抽出結果を JSON として読めません: {exc}", err=err)
        return 1
    facts = nodecontract.normalize_facts(decision, data)
    result = nodecontract.decide_candidates(decision.get("criteria"), facts,
                                            tie_break=decision.get("tie_break"))
    print(json.dumps(result, ensure_ascii=False), file=out)
    # 欠測が残る＝機械が決め切れていない。静かに合否へ倒さず、終了コードで伝える。
    return 1 if result["undecided"] else 0


def _capture_argv(built: dict) -> "tuple[int, str]":
    """定義どおりに 1 回実行し、(終了コード, stdout) を返す。"""
    env = dict(os.environ)
    env.update(built.get("env") or {})
    try:
        result = procgroup.run(built["argv"], env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               input=built.get("stdin"))
    except FileNotFoundError:
        _err(f"{built['argv'][0]} が見つかりません")
        return 127, ""
    return result.returncode, result.stdout or ""


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
# トップレベルのフラグ — クラウド CLI と同型の入口
# ---------------------------------------------------------------------------
# 設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.1。
#
# **正は「クラウド CLI がどう見えるか」である。** claude / codex では、引数なしが対話で
# `-p` が非対話 1 回、モデルと権限と作業ディレクトリはフラグである。agent-herd もそう
# 振る舞えばよい——**新しい実行経路は足さない**。ここがやるのは、既に `chat` と `exec` が
# 持っている当て先へフラグを翻訳することだけである。
#
# 既存のサブコマンド（`aider` / `ollama` / `chat` / `exec` / `defs` / `harness` …）は
# **別名として温存**する。仕様書 §3 の綴りを壊さない——help の下段へ降ろすだけ。
TOPLEVEL_FLAGS = ("-p", "--prompt", "--agent", "--model", "--purpose",
                  "--readonly", "--dir", "-d", "--continue", "--resume")
# セッション継続の実体は 2 つある（設計 §4 未決 1 の決着・2026-08-29）。
#
# - **ネイティブのセッション機能を持つ CLI**（claude / codex / copilot / cursor）は
#   定義が `continue_args` / `resume_args` を宣言し、入口はそれを argv へ差し込むだけ。
#   文脈はその CLI の側に残るので、こちらは材料を組み直さない。
# - **持たない自前 CLI**（agent-ollama / agent-aider）は**材料の再構築**である。前回の
#   会話（自分の JSONL ログの `message` イベント）を読み、本文の頭へ載せて渡す。
#   ローカルの単発実行は毎回新しいプロセスなので、これ以外の「継続」は存在しない。
#
# どちらも無い定義は明示エラーにする。黙って無視すると「継続したつもりで毎回まっさらに
# 走る」になり、しかも出力からは見分けが付かない。
_CONTINUE_HEADER = "（前回までのやり取り。続きとして扱ってください）"
# 材料に載せる会話の上限。継続のたびに全履歴を積むと文脈が太る（F4）ので、直近だけを運ぶ。
_CONTINUE_MAX_MESSAGES = 6


def _is_toplevel_invocation(sub: "str | None") -> bool:
    """`agent-herd` 自身へのフラグとして読むべきか。

    引数なし（＝対話）か、先頭がフラグのとき。`-h` / `--help` / `--version` は
    サブコマンド側が先に拾うので、ここへは来ない。
    """
    return sub is None or sub.startswith("-")


def _continuation_material(session: str) -> str:
    """自前 CLI の「継続」＝前回の会話を材料として組み直す（設計 §4 未決 1 の決着）。

    読むのは**自分の JSONL ログだけ**。他 CLI のネイティブストアを読むのは agent-audit の
    仕事で、そちらのパーサを agentcore へ写すと同じ形式に 2 実装ができる（C7）。
    見つからなければ空文字を返し、呼び出し側が明示エラーにする。
    """
    from agentcore import ollama_events
    path = ollama_events.log_path_for(session)
    if path is None:
        return ""
    messages = ollama_events.read_messages(path, limit=_CONTINUE_MAX_MESSAGES)
    if not messages:
        return ""
    lines = [_CONTINUE_HEADER]
    for role, content in messages:
        lines.append(f"[{'あなた' if role == 'assistant' else '依頼'}] {content.strip()}")
    return "\n".join(lines)


def cmd_toplevel(argv, *, err=None, runner=None, launcher=None, stdin=None) -> int:
    """`agent-herd [フラグ]` を `chat` / `exec` と同じ当て先へ落とす。"""
    err = err or sys.stderr
    tokens = list(argv)
    name = DEFAULT_CHAT_CLI
    model = purpose = None
    prompt: "str | None" = None
    headless = False
    readonly = False
    session_continue = False
    session_id = ""
    work_dir: "str | None" = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--continue":
            session_continue = True
        elif token == "--resume":
            if i + 1 >= len(tokens):
                _err("--resume にはセッション ID が必要です"
                     f"（ID はログの名前。{PROG} status で見えます）", err=err)
                return 2
            i += 1
            session_continue, session_id = True, tokens[i]
        elif token in ("-p", "--prompt"):
            headless = True
            # 値は任意。次が値らしくなければ本文は stdin から読む（フィルタの作法）。
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 1
                prompt = tokens[i]
        elif token == "--readonly":
            readonly = True
        elif token in ("--agent", "--model", "--purpose", "--dir", "-d"):
            if i + 1 >= len(tokens):
                _err(f"{token} には値が必要です", err=err)
                return 2
            i += 1
            value = tokens[i]
            if token == "--agent":
                name = value
            elif token == "--model":
                model = value
            elif token == "--purpose":
                purpose = value
            else:
                work_dir = value
        elif token.startswith("-"):
            _err(f"{PROG} は {token} を受け取りません"
                 f"（使えるのは {', '.join(TOPLEVEL_FLAGS)}。"
                 f"サブコマンドの一覧は {PROG} --help）", err=err)
            return 2
        else:
            _err(f"{PROG} は位置引数を受け取りません: {token!r}"
                 f"（本文は -p か stdin、バックエンドは --agent で渡します）", err=err)
            return 2
        i += 1

    if work_dir is not None:
        # `--dir` は**このプロセスの作業ディレクトリ**そのものである。ヘッドレスは
        # 子プロセスへ、対話は exec / in-process の adapter へ、同じ 1 回で効く。
        target = Path(work_dir).expanduser()
        if not target.is_dir():
            _err(f"ディレクトリが存在しません: {target}", err=err)
            return 2
        os.chdir(target)

    try:
        spec = agentcli.load_cli(name)
        if purpose:
            routed = slashroute.resolve(command=purpose, cli=name, model=model,
                                        explicit_model=bool(model))
            if routed["variant"] or routed["declared"]:
                spec = agentcli.load_cli(routed["agent_cli"])
                model = routed["model"]
        native = bool(agentcli.session_args(spec, resume=session_id)) if session_continue else False
        if session_continue and not native:
            # ネイティブを持たない定義。ここで材料を組めなければ「継続したつもりで
            # まっさらに走る」になるので、黙って続けない。
            material = _continuation_material(session_id)
            if not material:
                _err(f"{spec['name']} は継続の材料を持っていません"
                     + (f"（セッション {session_id} のログが見つかりません）" if session_id
                        else "（前回の会話ログがありません）")
                     + "。ネイティブのセッション機能を使う定義なら continue_args /"
                     " resume_args を宣言してください", err=err)
                return 2
        if not headless:
            if session_continue and not native:
                _err(f"{spec['name']} の継続は材料の再構築なので、本文と一緒に渡します"
                     "（-p で実行してください）", err=err)
                return 2
            return (launcher or _launch)(
                agentcli.interactive_cmd(spec, model, readonly=readonly,
                                         session_continue=session_continue,
                                         session_id=session_id))
        body = _read_prompt(stdin) if prompt is None else prompt
        if session_continue and not native:
            body = material + "\n\n" + body
        built = agentcli.headless_cmd(spec, model, body, readonly=readonly,
                                      session_continue=session_continue,
                                      session_id=session_id)
    except agentcli.AgentCliError as exc:
        _err(str(exc), err=err)
        return 1
    warning = built.get("readonly_warning")
    if warning:
        print(f"@agent-note {warning}", file=err)
    return (runner or _run_argv)(built)


# ---------------------------------------------------------------------------
# harness — 限定ツール契約のハーネスを tmux もデーモンも無しに回す
# ---------------------------------------------------------------------------
HARNESS_KINDS = ("statemachine", "run")

HARNESS_HELP = f"""使い方: {PROG} harness <種別> [オプション]

  種別:
    statemachine --workflow PATH   ステートマシンを完走させる
    statemachine --entry NAME      agent-loop.yaml の entry の宣言どおりに回す
    run PROMPT…                    プロンプト 1 件を 1 回実行する

  共通:
    --agent-cli NAME   agents/<名前>.json の CLI 名（既定: aider）
    --model MODEL      実行モデル（省略時は定義の default_model）
    --dir DIR          作業ディレクトリ（省略時: カレント）

  statemachine のみ:
    --param KEY=VALUE  実行パラメータ（繰り返し可）
    --input TEXT       input パラメータ
    --entry NAME       agent-loop.yaml の prompts エントリ名。そのエントリの
                       statemachine と実行条件（input: のマップ / prompt の自由文）で回す
    --config PATH      --entry を引く設定ファイル（省略時は agent-loop と同じ順で探す）

  run のみ:
    --acceptance TEXT  受入条件（繰り返し可。省略すると done を機械検証できない）
    --judge            パスを含まない受入条件を検証エージェントに判定させる
    --deliverable PATH 成果物のパス（繰り返し可）。2 つ以上なら 1 スロット 1 回ずつ
                       実行する（小さいモデルは 2 つ同時だと片方を落とす）

  実体は agentcore.harness（agent_loop からの移植）。agent-loop 経由と同じ契約で、
  終了時に `RESULT {{json}}` を 1 行出す。tmux で様子を見せたいときは、このコマンドを
  tmux ウィンドウの中で起動する（tmux は送る手段・見る手段であって実行契約ではない）。"""


def cmd_harness(argv, *, err=None, runner=None) -> int:
    """種別ごとの引数を組み立てて、移植した harness の cmd_* へそのまま渡す。

    引数の綴りは `agent-loop statemachine` / `agent-loop run` と同じにしてある——
    同じハーネスの 2 つの入口なので、片方だけ違う名前で覚えることを人に強いない。
    """
    err = err or sys.stderr
    tokens = list(argv)
    if not tokens or tokens[0] in ("-h", "--help", "help"):
        print(HARNESS_HELP)
        return 0 if tokens else 2
    kind, tokens = tokens[0], tokens[1:]
    if kind not in HARNESS_KINDS:
        _err(f"未知のハーネス種別: {kind!r}（使えるのは {', '.join(HARNESS_KINDS)}）", err=err)
        return 2

    import argparse

    parser = argparse.ArgumentParser(prog=f"{PROG} harness {kind}", add_help=False)
    # `--agent-cli` の既定は None。打たなかったことを **打った "aider" と区別する**ため
    # で、解決の既定そのものは従来どおり aider（statemachine.DEFAULT_HARNESS_CLI）。
    # 区別が要るのは `--entry` が entry の宣言した CLI を使うから。
    parser.add_argument("--agent-cli", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--dir", "-d", default=None)
    if kind == "statemachine":
        # どちらか一方が要る（両方は明示エラー）。required にしないのは、
        # 「--workflow が無い」ではなく「--entry でもよい」と伝えるため。
        parser.add_argument("--workflow", default=None)
        parser.add_argument("--entry", default=None)
        parser.add_argument("--config", default=None)
        parser.add_argument("--param", action="append", default=[])
        parser.add_argument("--input", default=None)
    else:
        parser.add_argument("prompt", nargs="+")
        parser.add_argument("--acceptance", action="append", default=[])
        parser.add_argument("--judge", action="store_true")
        # 成果物スロット。2 つ以上なら 1 スロット 1 回の直列で実行する（割り方は
        # agentcore.nodecontract の 1 実装。agent-flow の planner 経路と同じ）。
        parser.add_argument("--deliverable", action="append", default=[])
    try:
        args = parser.parse_args(tokens)
    except SystemExit as exc:               # argparse は 2 で落ちる。入口の綴りへ揃える
        return int(exc.code or 2)

    if kind == "statemachine" and bool(args.workflow) == bool(args.entry):
        # 「無い」と「両方ある」を同じ 2 で断る。どちらもハーネスを起こす前に分かる
        # 入力の問題で、走り出してから落とす理由が無い。
        _err("--workflow か --entry のどちらか一方が必要です", err=err)
        return 2

    cwd = Path(args.dir).expanduser().resolve() if args.dir else Path.cwd()
    if runner is not None:
        return runner(kind, args, cwd)
    return _run_harness(kind, args, cwd)


def _run_harness(kind: str, args, cwd: "Path") -> int:
    """移植した cmd_* を呼ぶ。あちらは終了を sys.exit で表すので、それを終了コードへ戻す。"""
    from agentcore.harness import statemachine, toolloop

    entry = statemachine.cmd_statemachine if kind == "statemachine" else toolloop.cmd_run
    try:
        entry(args, cwd)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


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

    if sub in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if sub in ("--version", "version"):
        from agentcore import __version__
        print(f"{PROG} {__version__}")
        return 0

    # 別名（`agent-aider …`）は argv0 で決まっているので、フラグより先に拾う
    # ——あちらの引数面は adapter への素通しで、ここが解釈してはいけない。
    if sub in ADAPTERS:
        return ADAPTERS[sub]()(rest)
    # 引数なし＝対話、先頭がフラグ＝自分自身への指定（設計 §3.1）。
    if _is_toplevel_invocation(sub):
        return cmd_toplevel(tokens)
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
    if sub == "decide":
        return cmd_decide(rest)

    # 未知。定義名なら exec を案内する（黙って別解釈しない）。
    if _known_definition(sub):
        _err(f"{sub!r} は定義であって adapter ではありません。"
             f"定義を指定して回すなら: {PROG} exec {sub} [--model <モデル>]")
        return 2
    known = sorted({*ADAPTERS, *OBSERVE_ALIASES, "defs", "exec", "chat", "harness", "decide"})
    _err(f"未知のサブコマンド: {sub!r}（使えるのは {', '.join(known)}）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
