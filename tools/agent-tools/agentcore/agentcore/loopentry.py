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

## 固定コマンド（`command:`）も同じ入口で読む

    command: ["python3", "scripts/sync-issue.py", "--iid", "{issue_iid}"]

`statemachine:` と同じく「この entry は本文を送るのではなく実行形で回す」宣言なので、
読み方の正典もここに置く。`{…}` の補完は本文テンプレートと同じ規則（`str.format_map`）で、
材料はフック / webhook が返した辞書。補完そのものは呼ぶ側（scheduler・CLI）が
本文経路と同じ 1 実装で行い、ここは**宣言の正規化だけ**を持つ。

仕様: docs/specs/agent-loop-spec.md §2.3 / §2.3.2 / §3.5。
"""
from __future__ import annotations

import json
import os
import re
import shlex
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

# `command:` の既定タイムアウト（秒）。流用していたフック 4 件の既定と揃える。
COMMAND_TIMEOUT_SEC = 300

# argv の字句に現れたら断るシェル記号。argv を直接実行する（シェルを通さない）ので、
# これらは「効かないのに書けてしまう」——`statemachine-use` の `check:` と同じ検査。
# パイプや条件分岐が要るならスクリプトファイルにして、それを argv に書く。
_COMMAND_SHELL_TOKENS = ("|", "&", ";", "<", ">", "$", "`", "(", ")", "&&", "||")


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



def _command_argv(value) -> "list[str]":
    """`command:` / `command.argv` の値を argv の配列へ。"""
    if isinstance(value, str):
        try:
            parts = shlex.split(value)
        except ValueError as exc:
            raise LoopEntryError(f"command を字句に分けられません: {exc}") from exc
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, (dict, list, tuple, bool)) or item is None:
                raise LoopEntryError(
                    "command の配列は 1 つのコマンドの引数なので、要素は文字列だけです"
                    "（順番に回したいなら `commands:` にコマンドを並べてください）")
            parts.append(str(item))
    else:
        raise LoopEntryError("command は文字列・配列・マップのいずれかです")
    parts = [token for token in (str(t).strip() for t in parts) if token]
    if not parts:
        raise LoopEntryError("command が空です")
    for token in parts:
        for mark in _COMMAND_SHELL_TOKENS:
            if mark in token:
                raise LoopEntryError(
                    f"command にシェル記号 '{mark}' は使えません: {token}"
                    "（argv を直接実行します。パイプや条件分岐が要るならスクリプトに"
                    "まとめ、そのスクリプトを command に書いてください）")
    # 先頭の `~` だけ広げる。`{…}` は補完のプレースホルダなので触らない。
    return [os.path.expanduser(t) if t.startswith("~") else t for t in parts]


def _is_script(value) -> bool:
    """複数行の文字列か（＝1 つのシェルへ一度に渡すものか）。"""
    return isinstance(value, str) and len([line for line in value.splitlines() if line.strip()]) > 1


def _command_script(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LoopEntryError("command.shell は文字列です")
    # 改行は LF へ寄せる。Windows で編集した設定の `\r` は、シェルから見ると行末ではなく
    # コマンド名の一部になる（空行が `$'\r': command not found` で落ちる）。
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _shell_argv(script: str) -> "list[str]":
    """スクリプトを**1 つの**シェルへ渡す argv。

    行をまたぐ `cd` や変数の設定がそのまま効くのは、同じプロセスが最後まで読むからで、
    ここが argv 直接実行との違いになる。失敗した行で止めるのは複数行の従来の振る舞いと
    同じなので `-e`（bash なら `pipefail` も）を付ける——その行だけ見逃したいときは
    `|| true` のように、シェルの書き方で宣言する。
    """
    if os.path.exists("/bin/bash"):
        return ["/bin/bash", "-e", "-o", "pipefail", "-c", script]
    return ["/bin/sh", "-e", "-c", script]


def _command_env(value) -> "dict[str, str]":
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LoopEntryError("command.env はマップです")
    env: "dict[str, str]" = {}
    for key, raw in value.items():
        name = _scalar(key)
        if not name:
            raise LoopEntryError("command.env のキーが空です")
        if raw is None or isinstance(raw, (dict, list, tuple)):
            raise LoopEntryError(f"command.env の値はスカラです: {name}")
        text = "true" if raw is True else "false" if raw is False else str(raw)
        env[name] = os.path.expanduser(text) if text.startswith("~") else text
    return env


def _command_skip_if_missing(value) -> "list[str]":
    """実行の前に存在を確かめるパスの一覧。宣言が無ければ空。

    「未導入なら何もしない」という判断を、フックの中の `if not os.path.isfile(...)` から
    宣言へ出すための口。先頭の `~` は home へ広げ、`{…}` は argv と同じく実行時の補完に
    残す。相対パスは `cwd` から読む（読むのは `commandrun` 側）。
    """
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise LoopEntryError("command.skip_if_missing は文字列または文字列の配列です")
    paths: "list[str]" = []
    for raw in items:
        if not isinstance(raw, str) or not raw.strip():
            raise LoopEntryError(
                f"command.skip_if_missing は文字列または文字列の配列です: {raw!r}")
        text = raw.strip()
        text = os.path.expanduser(text) if text.startswith("~") else text
        if text not in paths:
            paths.append(text)
    return paths


def _command_allow_status(value) -> "list[int]":
    """成功として扱う終了コードの一覧。宣言が無ければ `[0]`。

    段ごとに「この番号なら正常」を持つコマンドがあるので（較正の抽出・蒸留は 1 を
    正常として返す）、`0` を含まない一覧も受ける。空の一覧は「何を返しても失敗」に
    なるだけなので断る。
    """
    if value is None:
        return [0]
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise LoopEntryError("command.allow_status は整数の配列です")
    codes: "list[int]" = []
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise LoopEntryError(f"command.allow_status は整数の配列です: {raw!r}")
        if raw < 0:
            raise LoopEntryError(f"command.allow_status は 0 以上です: {raw}")
        if raw not in codes:
            codes.append(raw)
    if not codes:
        raise LoopEntryError("command.allow_status が空です（成功になる終了コードが無くなります）")
    return codes


_COMMAND_UNIT_KEYS = ("argv", "shell", "timeout_sec", "env", "allow_status",
                      "skip_if_missing", "continue_on_error")
_COMMAND_DEFAULTS = {"timeout_sec": COMMAND_TIMEOUT_SEC, "env": {}, "allow_status": [0],
                     "skip_if_missing": [], "continue_on_error": False}


def _command_inherit(defaults: dict) -> dict:
    """上位の既定を段へ配る（可変の値は複製する——段ごとに書き換わるため）。"""
    return {"timeout_sec": defaults["timeout_sec"], "env": dict(defaults["env"]),
            "allow_status": list(defaults["allow_status"]),
            "skip_if_missing": list(defaults["skip_if_missing"]),
            "continue_on_error": defaults["continue_on_error"]}


def _command_continue_on_error(value) -> bool:
    """その段で列を止めないか。**失敗は失敗のまま**残す（見逃すのは `allow_status`）。"""
    if isinstance(value, bool):
        return value
    raise LoopEntryError(f"command.continue_on_error は true / false です: {value!r}")


def _command_timeout(value) -> int:
    try:
        timeout = int(float(value))
    except (TypeError, ValueError) as exc:
        raise LoopEntryError(f"command.timeout_sec は数値です: {value!r}") from exc
    if timeout < 1:
        raise LoopEntryError(f"command.timeout_sec は 1 以上です: {timeout}")
    return timeout


def _command_options(declared: dict, defaults: dict, *, extra_keys=()) -> dict:
    """1 つの段が持てる宣言（上限・環境変数・許容・飛ばす条件）を読む。

    書かなければ `defaults` を継ぐ——列の各段は、上位に書いた値を既定として受け取り、
    自分で書いたものだけを上書きする（「6 段のうち 2 段だけ 1 を許す」が素直に書ける）。
    """
    unknown = sorted(set(declared) - set(_COMMAND_UNIT_KEYS) - set(extra_keys))
    if unknown:
        raise LoopEntryError(f"command に知らないキーがあります: {', '.join(unknown)}")
    return {
        "timeout_sec": (_command_timeout(declared["timeout_sec"])
                        if "timeout_sec" in declared else defaults["timeout_sec"]),
        "env": (_command_env(declared["env"]) if "env" in declared else dict(defaults["env"])),
        "allow_status": (_command_allow_status(declared["allow_status"])
                         if "allow_status" in declared else list(defaults["allow_status"])),
        "skip_if_missing": (_command_skip_if_missing(declared["skip_if_missing"])
                            if "skip_if_missing" in declared else list(defaults["skip_if_missing"])),
        "continue_on_error": (_command_continue_on_error(declared["continue_on_error"])
                              if "continue_on_error" in declared
                              else defaults["continue_on_error"]),
    }


def _command_unit(declared, defaults: dict) -> dict:
    """コマンド 1 つ分（単体でも、列の 1 段でも同じ）の宣言を正規化する。"""
    if declared is None or isinstance(declared, bool):
        raise LoopEntryError("command は文字列・配列・マップのいずれかです")
    if isinstance(declared, dict):
        if "shell" in declared and "argv" in declared:
            raise LoopEntryError("command に argv と shell の両方は書けません")
        if "shell" not in declared and "argv" not in declared:
            raise LoopEntryError("command.argv は必須です")
        source = declared["shell"] if "shell" in declared else declared["argv"]
        shell = "shell" in declared or _is_script(source)
        options = _command_options(declared, defaults)
    else:
        source, shell, options = declared, _is_script(declared), _command_inherit(defaults)
    if shell:
        script = _command_script(source)
        return {"argv": _shell_argv(script), "shell": script, **options}
    return {"argv": _command_argv(source), **options}


def command_spec(entry) -> "dict | None":
    """entry の `command:` 宣言を正規化する。宣言が無ければ None。

    返り値: `{"argv": [...], "timeout_sec": int, "env": {...}, "allow_status": [int],
    "skip_if_missing": [...]}`。列のときは同じ形の段を `commands` に持ち、上位の値は
    先頭の段と同じになる。

    3 形を受ける（`statemachine-use` の `check:` と同じ綴り——利用者は既にこれを知っている）。

        command: "node scripts/resource-control.js --control-dir ~/.agents/control"
        command: ["agent-audit", "calibrate"]
        command: {argv: [...], timeout_sec: 600, env: {...}, allow_status: [0, 1]}

    複数行の文字列（`shell:` に書いたものも同じ）は 1 つのシェルへ一度に渡す。行をまたぐ
    `cd` や変数の設定が効く代わりに、上限は**全体**に掛かり、シェル記号の検査もしない。

        command: |
          cd build
          ./run.sh | tee last.log

    順番に回すコマンドの列は `commands:` に並べる。段は上位の宣言を既定として継ぎ、
    自分で書いたものだけを上書きする（「6 段のうち 2 段だけ 1 を許す」「この段が失敗しても
    残りは回す」が書ける）。

        command:
          commands:
            - "agent-audit collect"
            - {argv: "agent-audit extract", allow_status: [0, 1]}
          timeout_sec: 600

    `{…}` はそのまま残す。補完はフック / webhook が材料を返した実行時の仕事で、
    宣言を読む時点では誰も値を持っていない。
    """
    if not isinstance(entry, dict):
        raise LoopEntryError("entry はマップです")
    declared = entry.get("command")
    if declared is None or (isinstance(declared, str) and not declared.strip()):
        return None
    if isinstance(declared, bool):
        raise LoopEntryError("command は文字列・配列・マップのいずれかです")

    # 列として読むのは 2 つ——`commands:` に並べたものと、要素が配列 / マップの配列。
    # 要素が全部文字列の配列は 1 つのコマンドの引数（従来どおり）で、ここには来ない。
    # 複数行の文字列は列ではなく**1 つのシェル実行**（`_command_unit` が見る）。
    if isinstance(declared, dict) and "commands" in declared:
        if "argv" in declared or "shell" in declared:
            raise LoopEntryError("command に commands と argv / shell の両方は書けません")
        steps_source = declared["commands"]
        if not isinstance(steps_source, (list, tuple)) or not steps_source:
            raise LoopEntryError("command.commands はコマンドの配列です（空にはできません）")
        defaults = _command_options(declared, _COMMAND_DEFAULTS, extra_keys=("commands",))
    elif (isinstance(declared, (list, tuple)) and declared
            and all(isinstance(item, (list, tuple, dict)) for item in declared)):
        steps_source, defaults = list(declared), _command_inherit(_COMMAND_DEFAULTS)
    else:
        return _command_unit(declared, _COMMAND_DEFAULTS)

    steps = [_command_unit(item, defaults) for item in steps_source]
    # 先頭の段は entry 全体の代表でもある（記録・画面はここを 1 行で見せる）。
    return {**steps[0], "commands": steps}


def render_command(spec, values, *, resolve=None) -> dict:
    # シェル実行のスクリプトへは差し込まない。argv なら値は 1 字句のままだが、シェルへ
    # 渡す文字列では値の中の記号がコマンドとして読まれる（材料が実行になってしまう）。
    rendered = dict(spec) if spec.get("shell") else {
        **spec, "argv": render_argv(spec["argv"], values, resolve=resolve)}
    if spec.get("skip_if_missing"):
        # 存在を確かめるパスも `{…}` を持てる（材料で置き場が決まるため）。規則は argv と同じ。
        rendered["skip_if_missing"] = render_argv(spec["skip_if_missing"], values, resolve=resolve)
    if spec.get("commands"):
        # 段は 1 つの宣言と同じ形なので、同じ関数で差し込む。
        rendered["commands"] = [render_command(step, values, resolve=resolve)
                                for step in spec["commands"]]
        rendered["argv"] = rendered["commands"][0]["argv"]
    return rendered


class _SafeValues(dict):
    """`str.format_map` 用。未定義キーは `{key}` のまま残す（本文テンプレートと同じ）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def render_argv(argv, values, *, resolve=None) -> "list[str]":
    """argv の各**字句**へ `{key}` を差し込む。

    規則は本文テンプレートと同じ（`str.format_map` + 未定義キーは残す）。字句単位なので、
    値に空白や記号があっても引数の数は変わらない——値は材料であってコマンドラインでは
    ないので、置換後の字句にシェル記号の検査は掛けない。

    `resolve` は字句 1 つを先に変換する任意の関数（遅延 lookup の解決を呼ぶ側から渡す。
    `format_map` は `{{` を `{` に潰すので、後からでは解決できない）。
    """
    if not values:
        return [str(token) for token in argv]
    safe = _SafeValues({str(k): v for k, v in dict(values).items()})
    rendered: "list[str]" = []
    for token in argv:
        text = str(token)
        if resolve is not None:
            text = resolve(text)
        try:
            rendered.append(text.format_map(safe))
        except (IndexError, ValueError) as exc:
            raise LoopEntryError(f"command の補完に失敗しました: {token}（{exc}）") from exc
    return rendered


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


def resolve_command_entry(name, *, cwd, config=None) -> dict:
    """`command --entry` の解決結果。コマンドを宣言していない entry はここで断る。

    返り値: `{"entry", "config", "command", "cwd"}`
    """
    entry, path = find_entry(name, cwd=cwd, config=config)
    spec = command_spec(entry)
    if spec is None:
        raise LoopEntryError(
            f"entry「{_scalar(entry.get('name'))}」は command を宣言していません（{path}）")
    entry_cwd = _scalar(entry.get("cwd"))
    return {
        "entry": entry,
        "config": str(path),
        "command": spec,
        "cwd": os.path.expanduser(entry_cwd) if entry_cwd else "",
    }


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


def resolve_date_inputs(values, *, today=None):
    """Resolve explicit calendar defaults at dispatch time, in the execution host's timezone."""
    from datetime import date, timedelta
    today = today or date.today()
    previous_month = today.replace(day=1) - timedelta(days=1)
    replacements = {
        "@date:today": today.isoformat(),
        "@date:yesterday": (today - timedelta(days=1)).isoformat(),
        "@date:month": today.strftime("%Y-%m"),
        "@date:previous-month": previous_month.strftime("%Y-%m"),
    }
    return {key: replacements.get(value, value) if isinstance(value, str) else value
            for key, value in (values or {}).items()}
