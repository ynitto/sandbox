"""agentcore.loopentry — agent-loop の entry から「どのステートマシンを、どの条件で回すか」を解く。

## なぜ agentcore に置くか

同じ宣言を読む入口が 3 つある——常駐デーモン（agent-loop の scheduler）、
`agent-herd harness statemachine --entry`、agent-dashboard の「今すぐ実行」。
写しを 3 つ持つと、`prompt` と `input:` のどちらが勝つか・名前をどう `.statemachine/…`
へ展開するかが入口ごとにずれる（ずれても実行はできてしまうので、気づくのは
「dashboard から回すと条件が違う」と人が言い出したとき）。だから宣言の解釈は
ここ 1 つに閉じ、agent-loop も agent-herd もこれを呼ぶ。
dashboard（JS）は同じ規則を discover.js / cowork.js に持つが、規則の正典はここと仕様書。

## 実行条件の書き方は 2 つ、正典は `input:`

    statemachine: digest        # .statemachine/digest/workflow.yaml
    input:                      # 名前のある条件（ワークフローのパラメータ面と 1:1）
      topic: llm
    prompt: 今日の要約を書いて   # 名前の無い自由文 → `input` パラメータ 1 個ぶん

`input:` を正典にするのは、ワークフローが自分のパラメータ面（`{{topic}}` /
`context:`）を宣言しているからだ。マップはその面と 1:1 なので、キーの過不足を
**実行前に**突き合わせられる。自由文が確実に届く先は `input` の 1 スロットだけで、
2 つ以上の条件を自由文で書くと割り付けはモデルの推測になり、外した実行は
`check:` まで進んで初めて落ちる（1 回ぶんの課金と時間を捨てる）。
両方書くのは許す。衝突するのは `input` キーだけで、そこは黙って片方を勝たせず落とす。

仕様: docs/specs/agent-loop-spec.md §2.3 / §3.5。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# agent-loop の設定ファイル名と探索順（agent_loop/config.py の DEFAULT_CONFIG_NAMES と
# load_config に合わせる。あちらが正典で、ここはその読み取り側）。
DEFAULT_CONFIG_NAMES = ("agent-loop.yaml", "agent-loop.yml", "agent-loop.json")
AGENT_HOME = ".agents"
AGENT_HOME_LEGACY = ".agent"

STATEMACHINE_DIR = ".statemachine"
WORKFLOW_FILE = "workflow.yaml"

# `.statemachine/<名前>/` として使える名前。パス区切りを含まない値はこの規約で展開する。
_SM_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class LoopEntryError(Exception):
    """entry の宣言そのものが読めない（＝人が直すまで実行できない）。"""


def _scalar(value) -> str:
    return "" if value is None else str(value).strip()


def workflow_reference(value) -> str:
    """`statemachine:` の値を**作業ディレクトリからの相対パス**へ正規化する。

    - 区切りを含まない名前 → `.statemachine/<名前>/workflow.yaml`
    - `.yaml` / `.yml` で終わるパス → そのまま
    - それ以外のパス（`.statemachine/digest` 等）→ 末尾に `workflow.yaml` を足す

    絶対パスと `..` は受けない。ハーネスは作業ディレクトリの外を読まないので、
    渡してもあちらで落ちる——落ちるなら設定を読んだ時点のほうが直しやすい。
    """
    raw = _scalar(value)
    if not raw:
        raise LoopEntryError("statemachine の値が空です")
    if raw.startswith("~"):
        raise LoopEntryError(f"statemachine にホーム展開は使えません: {raw}")
    normalized = raw.replace("\\", "/")
    if "/" not in normalized:
        if not _SM_NAME_RE.match(normalized) or normalized in (".", ".."):
            raise LoopEntryError(f"statemachine の名前が不正です: {raw}")
        return f"{STATEMACHINE_DIR}/{normalized}/{WORKFLOW_FILE}"
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise LoopEntryError(f"statemachine に絶対パスは使えません: {raw}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise LoopEntryError(f"statemachine に上位ディレクトリは使えません: {raw}")
    if not parts:
        raise LoopEntryError(f"statemachine の値が不正です: {raw}")
    if not parts[-1].lower().endswith((".yaml", ".yml")):
        parts.append(WORKFLOW_FILE)
    return "/".join(parts)


def workflow_display_name(reference: str) -> str:
    """正規化済みの参照から `.statemachine/<名前>` の名前を取り出す（無ければ空文字）。

    dashboard が発見済みのステートマシン（フォルダ名）と突き合わせるために使う。
    """
    parts = [p for p in str(reference or "").split("/") if p]
    if len(parts) >= 3 and parts[-3] == STATEMACHINE_DIR:
        return parts[-2]
    return ""


def _input_map(value) -> "dict[str, str]":
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LoopEntryError("input はマップです")
    params: "dict[str, str]" = {}
    for key, raw in value.items():
        name = _scalar(key)
        if not name:
            raise LoopEntryError("input のキーが空です")
        if raw is None:
            raise LoopEntryError(f"input の値が空です: {name}"
                                 "（値を書かない条件は宣言しない）")
        if isinstance(raw, (dict, list, tuple)):
            raise LoopEntryError(
                f"input の値はスカラです: {name}"
                "（入れ子は `context.<キー>: <値>` のようにキー側へ書く）")
        if isinstance(raw, bool):
            params[name] = "true" if raw else "false"
        else:
            params[name] = str(raw)
    return params


def statemachine_spec(entry, *, prompt=None) -> "dict | None":
    """entry の statemachine 宣言を正規化する。宣言が無ければ None。

    返り値: `{"workflow": <相対パス>, "name": <表示名>, "input": {...},
              "parameters": {...}, "prompt_is_input": bool}`

    `input` は宣言そのまま、`parameters` は自由文（prompt）を `input` パラメータへ
    載せた後の**実行へ渡す値**。2 つ返すのは、正規化した entry を保存して後から
    もう一度この関数へ通せるようにするため（デーモンの reload と dispatch）。

    `prompt` を渡すと entry の `prompt` の代わりに使う（フックが本文を決めた実行）。
    """
    if not isinstance(entry, dict):
        raise LoopEntryError("entry はマップです")
    declared = entry.get("statemachine")
    if declared is None or _scalar(declared) == "":
        return None
    if isinstance(declared, (dict, list, tuple, bool)):
        raise LoopEntryError("statemachine は文字列です")
    reference = workflow_reference(declared)
    declared_input = _input_map(entry.get("input"))
    parameters = dict(declared_input)
    text = _scalar(entry.get("prompt") if prompt is None else prompt)
    prompt_is_input = False
    if text:
        if "input" in parameters:
            raise LoopEntryError(
                "prompt と input.input の両方が実行条件の `input` を指しています"
                "（自由文は prompt か input.input のどちらか一方に書く）")
        parameters["input"] = text
        prompt_is_input = True
    return {
        "workflow": reference,
        "name": workflow_display_name(reference),
        "input": declared_input,
        "parameters": parameters,
        "prompt_is_input": prompt_is_input,
    }



# `shlex.split` が 1 トークンとして読む綴り。**引用は必要なときだけ**——条件の値は
# ほとんど日本語で、`shlex.quote` のように ASCII 以外を一律で包むと、ペインに出る 1 行が
# 引用符だらけになって人が読めない。空白とシェルが特別扱いする記号だけを見る。
# dashboard 側の写しは `cowork.js` の `shlexQuote`（同じ規則・同じ出力）。
_UNSAFE_RE = re.compile(r"""[\s'"\\$`|&;<>()\[\]{}*?!#~]""")


def _shell_token(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return "''"
    return "'" + raw.replace("'", "'\\''") + "'" if _UNSAFE_RE.search(raw) else raw


def statemachine_command(spec: "dict | None", *, slash: bool) -> str:
    """対話ペインへ送る**実行形の 1 行**。宣言が無ければ空文字。

    `slash=True`（agent-herd 一族）は共通 TUI のコマンド面へ `/sm <名前> [--param k=v]`。
    TUI がそれを受けてヘッドレスのハーネスへ回す（agent-herd 設計 2026-08-27 §7.5）。
    `slash=False`（クラウド CLI）はスキル発動文。あちらは自分でスキルを見つけて
    1 セッションで通せるので、state ごとにヘッドレス起動するより起動と文脈再構築の
    ぶんだけ安い。

    **`/sm` は本文の先頭行でなければ効かない**（ルータは先頭ブロックしか読まない）。
    呼び出し側はこの戻り値の前へ何も足さないこと——共通指示も含めて、足すと本文になる。
    """
    if not spec:
        return ""
    if not slash:
        conditions = "".join(f"\n- {key}: {value}"
                             for key, value in sorted((spec.get("parameters") or {}).items()))
        return (f"statemachine-use スキルで{spec['name']}ステートマシンを実行して"
                + (f"\n\n入力:{conditions}" if conditions else ""))
    parts = ["/sm " + _shell_token(str(spec["workflow"]))]
    for key, value in sorted((spec.get("parameters") or {}).items()):
        parts.append("--param " + _shell_token(f"{key}={value}"))
    return " ".join(parts)

# ---------------------------------------------------------------------------
# 設定ファイルから entry を引く（`--entry` の実体）
# ---------------------------------------------------------------------------
def config_candidates(cwd) -> "list[Path]":
    """設定ファイルの探索順。agent_loop.load_config と同じ並びにする。"""
    workspace = Path(cwd).expanduser().resolve()
    home = Path.home().resolve()
    directories = [workspace, workspace / AGENT_HOME, workspace / AGENT_HOME_LEGACY,
                   home / AGENT_HOME, home / AGENT_HOME_LEGACY]
    seen: "set[str]" = set()
    out: "list[Path]" = []
    for directory in directories:
        for name in DEFAULT_CONFIG_NAMES:
            candidate = directory / name
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def find_config(cwd) -> "Path | None":
    for candidate in config_candidates(cwd):
        if candidate.is_file():
            return candidate
    return None


def load_prompts(path) -> "list[dict]":
    """設定ファイルの `prompts[]` を読む（値の解釈はしない）。"""
    file = Path(path).expanduser()
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise LoopEntryError(f"設定ファイルを読めません: {file}（{exc}）") from exc
    if file.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise LoopEntryError(f"設定ファイルを解析できません: {file}（{exc}）") from exc
    else:
        try:
            import yaml as _yaml  # type: ignore
        except ImportError as exc:
            raise LoopEntryError("PyYAML が必要です（pip install pyyaml）") from exc
        try:
            data = _yaml.safe_load(text)
        except Exception as exc:  # yaml.YAMLError も含めて 1 つの契約で返す
            raise LoopEntryError(f"設定ファイルを解析できません: {file}（{exc}）") from exc
    prompts = (data or {}).get("prompts") if isinstance(data, dict) else None
    if prompts is None:
        return []
    if not isinstance(prompts, list):
        raise LoopEntryError(f"prompts は list です: {file}")
    return [e for e in prompts if isinstance(e, dict)]


def find_entry(name, *, cwd, config=None) -> "tuple[dict, Path]":
    """名前で entry を 1 件引く。見つからなければ候補名を添えて落とす。"""
    wanted = _scalar(name)
    if not wanted:
        raise LoopEntryError("entry の名前が空です")
    path = Path(config).expanduser() if config else find_config(cwd)
    if path is None:
        searched = ", ".join(str(p) for p in config_candidates(cwd)[:3])
        raise LoopEntryError(f"agent-loop の設定ファイルが見つかりません（探索先: {searched} …）")
    if not path.is_file():
        raise LoopEntryError(f"設定ファイルがありません: {path}")
    entries = load_prompts(path)
    for entry in entries:
        if _scalar(entry.get("name")) == wanted:
            return entry, path
    known = ", ".join(_scalar(e.get("name")) for e in entries if _scalar(e.get("name")))
    raise LoopEntryError(f"entry が見つかりません: {wanted}"
                         + (f"（{path} にあるのは: {known}）" if known else f"（{path} は空です）"))


def resolve_entry(name, *, cwd, config=None) -> dict:
    """`--entry` の解決結果。ステートマシンを宣言していない entry はここで断る。

    返り値: `{"entry", "config", "workflow", "parameters", "agent_cli", "model", "cwd"}`
    """
    entry, path = find_entry(name, cwd=cwd, config=config)
    spec = statemachine_spec(entry)
    if spec is None:
        raise LoopEntryError(
            f"entry「{_scalar(entry.get('name'))}」は statemachine を宣言していません"
            f"（{path}）。ワークフローを直に回すときは --workflow を使ってください")
    entry_cwd = _scalar(entry.get("cwd"))
    return {
        "entry": entry,
        "config": str(path),
        "workflow": spec["workflow"],
        "parameters": dict(spec["parameters"]),
        "agent_cli": _scalar(entry.get("agent_cli")) or "",
        "model": _scalar(entry.get("model")) or "",
        "cwd": os.path.expanduser(entry_cwd) if entry_cwd else "",
    }
