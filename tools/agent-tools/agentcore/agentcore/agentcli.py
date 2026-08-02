"""agentcore.agentcli — エージェント CLI 定義（agents/<name>.json）の唯一のローダ。

## なぜここに 1 実装を置くか

同じ「この CLI をどう起動するか」を 4 者が別々に実装していた:

- `agent_project/prioritize.py:_agent_cmd` … 組み込み 4 CLI の分岐 + 自前プラグインローダ
- `agent_flow/agent.py`                    … 同上
- `agent_amigos/agentcli.py`               … 同上
- `agent-dashboard` の `agent.js`          … 同上 + 対話 argv + 読み取り専用フラグ

`agentcore.repolocal` が解決したのと同型の問題（3 者の URL 正規化の吸収規則が食い違い、同じ
2 つの URL が経路によって一致したりしなかったりした）で、`empty_output_is_error` の扱いや
`{model}` の省略規則が実装ごとにずれれば、**同じ定義ファイルがツールによって別の argv になる**。

Python 側（agent-project / agent-flow / agent-amigos）はここを共有する。agent-dashboard だけは
UI の応答性のため JS の自前ローダを持ち（Python を起こすと候補一覧の描画がプロセス起動待ちに
なる）、同じ定義から同じ argv が出ることをゴールデンテストで固定する。

## argv の組み立て順（契約）

    command + (write_args | readonly_args) + no_session_args? + spill.args?
            + model_flag model + command_suffix + argv 渡しのプロンプト

`command` 内の `{model}` / `{output_file}` は置換され、`{model}` はモデル未指定ならトークンごと
落ちる。`model_flag` は `command` に `{model}` が無くモデル指定があるときだけ付く。

契約の正典は `schemas/agent-cli.schema.json`。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

# 探索順（schemas/agent-cli.schema.json の規約）。環境変数はテストが実ホームを汚さないための seam。
_AGENTS_DIR_ENV = "KIRO_AGENTS_DIR"
_AGENTS_HOME_ENV = "AGENT_PROJECT_AGENTS_HOME"
_AGENTS_HOME_DIR = ".agents"
_AGENTS_HOME_LEGACY = ".agent"

_USAGE_RE = re.compile(
    r"(?m)^@agent-usage\s+tokens_in=(\d+)\s+tokens_out=(\d+)\s*$")

def _bundled_dir() -> "Path | None":
    """ツール同梱の定義（このリポジトリの agents/）。install.py が ~/.agents/agents/ へ配るが、
    リポジトリから直接動かす開発環境でも解決できるよう、上へ辿って最後の候補として見る。"""
    for parent in Path(__file__).resolve().parents:
        cand = parent / "agents"
        if (cand / "kiro.json").is_file():
            return cand
    return None


class AgentCliError(RuntimeError):
    """定義が見つからない / 壊れている。黙って別 CLI へ倒さないための明示エラー。"""


def parse_usage(stderr: str) -> "tuple[int | None, int | None]":
    """CLI アダプターが stderr に出す実測 usage を読む。本文(stdout)は信頼しない。"""
    matches = list(_USAGE_RE.finditer(str(stderr or "")))
    if not matches:
        return None, None
    match = matches[-1]
    return int(match.group(1)), int(match.group(2))


class UsageText(str):
    """文字列互換の応答に、台帳へ渡す実測値だけを添える。"""

    def __new__(cls, text: str, tokens_in=None, tokens_out=None):
        value = super().__new__(cls, text)
        value.tokens_in = tokens_in
        value.tokens_out = tokens_out
        return value


def _agents_home() -> Path:
    """共通ホーム。スキーマどおり **agents サブディレクトリ単位**で新旧を判定する。

    `~/.agents/agents/` が無く旧 `~/.agent/agents/` が実在するときだけ後者を使う。
    親ディレクトリ（`~/.agents`）だけの存在では旧へ倒さない——無関係な `.agents`
    ディレクトリがレガシー定義を隠すのを防ぐ。
    """
    override = os.environ.get(_AGENTS_HOME_ENV)
    if override:
        return Path(override)
    home = Path.home()
    new, old = home / _AGENTS_HOME_DIR, home / _AGENTS_HOME_LEGACY
    if (new / "agents").is_dir():
        return new
    if (old / "agents").is_dir():
        return old
    return new


def plugin_dirs(project_dir=None) -> "list[Path]":
    """定義の探索順。first-wins（上位に置けば同梱定義を上書きできる）。"""
    dirs: "list[Path]" = []
    envd = os.environ.get(_AGENTS_DIR_ENV)
    if envd:
        dirs.append(Path(envd).expanduser())
    dirs.append(Path(project_dir).expanduser() / "agents" if project_dir
                else Path.cwd() / "agents")
    dirs.append(_agents_home() / "agents")
    dirs.append(Path.home() / ".kiro" / "agents")
    bundled = _bundled_dir()
    if bundled:
        dirs.append(bundled)
    return dirs


def _strs(raw, field: str, path) -> "list[str]":
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise AgentCliError(f"エージェント定義 {path}: {field} は文字列配列が必要です")
    return list(raw)


def normalize(name: str, raw: dict, path) -> dict:
    """定義を正規化する。壊れた定義は黙って無視せず例外（設定ミスの静かな握り潰しを作らない）。"""
    if not isinstance(raw, dict):
        raise AgentCliError(f"エージェント定義 {path}: オブジェクトが必要です")
    command = _strs(raw.get("command"), "command", path)
    if not command:
        raise AgentCliError(f"エージェント定義 {path}: command は 1 要素以上の文字列配列が必須です")
    output = str(raw.get("output", "stdout"))
    if output not in ("stdout", "file"):
        raise AgentCliError(f"エージェント定義 {path}: output は stdout か file です")
    if output == "file" and not any("{output_file}" in c for c in command):
        raise AgentCliError(f"エージェント定義 {path}: output=file には command 中の "
                            "{output_file} プレースホルダが必要です")
    prompt_via = str(raw.get("prompt_via", "stdin"))
    if prompt_via not in ("stdin", "argv"):
        raise AgentCliError(f"エージェント定義 {path}: prompt_via は stdin か argv です")
    env_raw = raw.get("env") or {}
    if not isinstance(env_raw, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                 for k, v in env_raw.items()):
        raise AgentCliError(f"エージェント定義 {path}: env は文字列→文字列のオブジェクトです")
    errors = []
    for e in (raw.get("errors") or []):
        if not isinstance(e, dict):
            raise AgentCliError(f"エージェント定義 {path}: errors[] はオブジェクト配列です")
        try:
            errors.append((str(e.get("class", "env")),
                           re.compile(str(e.get("match", "")), re.I),
                           str(e.get("hint", ""))))
        except re.error as ex:
            raise AgentCliError(
                f"エージェント定義 {path}: errors.match が正規表現として不正です: {ex}") from ex
    spill_raw = raw.get("spill") or {}
    if spill_raw and not isinstance(spill_raw, dict):
        raise AgentCliError(f"エージェント定義 {path}: spill はオブジェクトです")
    inter_raw = raw.get("interactive") or {}
    if inter_raw and not isinstance(inter_raw, dict):
        raise AgentCliError(f"エージェント定義 {path}: interactive はオブジェクトです")
    readonly = str(raw.get("readonly", "best-effort"))
    if readonly not in ("enforced", "best-effort"):
        raise AgentCliError(f"エージェント定義 {path}: readonly は enforced か best-effort です")
    spec = {
        "name": str(raw.get("name") or name),
        "path": str(path),
        "command": command,
        "command_suffix": _strs(raw.get("command_suffix"), "command_suffix", path),
        # スキル起動の行頭記号（既定 `/`。codex は `$skill-name`）。対話セッションへ
        # テキストを送る経路が skill_command_prefix() 経由で参照する。
        "skill_command_prefix": str(raw.get("skill_command_prefix") or "/"),
        "prompt_via": prompt_via,
        "prompt_flag": raw.get("prompt_flag"),
        "model_flag": raw.get("model_flag"),
        "default_model": raw.get("default_model"),
        "output": output,
        "env": dict(env_raw),
        "timeout": raw.get("timeout"),
        "empty_output_is_error": bool(raw.get("empty_output_is_error", True)),
        "write_args": _strs(raw.get("write_args"), "write_args", path),
        "readonly_args": _strs(raw.get("readonly_args"), "readonly_args", path),
        "readonly": readonly,
        "no_session_args": _strs(raw.get("no_session_args"), "no_session_args", path),
        "spill": {
            "args": _strs(spill_raw.get("args"), "spill.args", path),
            "instruction": str(spill_raw.get("instruction") or ""),
        },
        "errors": errors,
    }
    if inter_raw:
        icmd = _strs(inter_raw.get("command"), "interactive.command", path)
        if not icmd:
            raise AgentCliError(f"エージェント定義 {path}: interactive.command は 1 要素以上必要です")
        inject = str(inter_raw.get("prompt_inject", "send-keys"))
        if inject not in ("send-keys", "file"):
            raise AgentCliError(
                f"エージェント定義 {path}: interactive.prompt_inject は send-keys か file です")
        spec["interactive"] = {
            "command": icmd,
            # 対話の既定モードで付けるフラグ。トップレベルの write_args はヘッドレス専用の
            # 危険フラグ（--dangerously-skip-permissions 等）を含むので継承しない
            "write_args": _strs(inter_raw.get("write_args"), "interactive.write_args", path),
            # 省略時はトップレベルを継承する（同じ知識を 2 度書かせない）
            "readonly_args": (_strs(inter_raw["readonly_args"], "interactive.readonly_args", path)
                              if "readonly_args" in inter_raw else spec["readonly_args"]),
            "no_session_args": (_strs(inter_raw["no_session_args"],
                                      "interactive.no_session_args", path)
                                if "no_session_args" in inter_raw else spec["no_session_args"]),
            "ready_pattern": str(inter_raw.get("ready_pattern") or ""),
            "ready_timeout_sec": float(inter_raw.get("ready_timeout_sec") or 60),
            "prompt_inject": inject,
        }
    else:
        spec["interactive"] = None
    return spec


_CACHE: "dict[str, dict]" = {}


def load_cli(name: str, project_dir=None, *, use_cache: bool = True) -> dict:
    """agents/<name>.json を探索順に読み、正規化して返す。

    見つからなければ AgentCliError。以前は未知の agent_cli が黙って kiro-cli へ落ちており、
    設定ミスに気づけなかった。組み込み名も定義ファイル化した今、「見つからない」は
    ほぼインストールの破損なので、そう読めるメッセージにする。
    """
    key = str(name or "").strip().lower()
    if not key or not re.fullmatch(r"[\w.-]+", key):
        raise AgentCliError(f"agent_cli の名前が不正です: {name!r}")
    cache_key = f"{key}\0{project_dir or ''}"
    if use_cache and cache_key in _CACHE:
        return _CACHE[cache_key]
    dirs = plugin_dirs(project_dir)
    for d in dirs:
        p = d / f"{key}.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            if isinstance(e, ValueError):
                raise AgentCliError(f"エージェント定義 {p}: JSON として読めません: {e}") from e
            continue
        spec = normalize(key, raw, p)
        if use_cache:
            _CACHE[cache_key] = spec
        return spec
    raise AgentCliError(
        f"未知の agent_cli です: {key!r}（agents/{key}.json が見つかりません）\n"
        f"  探索順: {' → '.join(str(d) for d in dirs)}\n"
        "  組み込み CLI もこの定義ファイルで動きます。インストールが壊れている可能性が"
        "あります（install.sh の再実行を検討してください）。")


def clear_cache() -> None:
    _CACHE.clear()


def _mode_args(spec: dict, *, interactive: bool, readonly: bool, no_session: bool) -> "list[str]":
    src = spec["interactive"] if interactive and spec.get("interactive") else spec
    args = list(src["readonly_args"]) if readonly else list(src["write_args"])
    if no_session:
        for tok in src["no_session_args"]:
            if tok not in args:          # readonly と no_session が同じフラグを持つ CLI の重複を防ぐ
                args.append(tok)
    return args


def _expand(tokens, model: str, output_file_holder: dict) -> "list[str]":
    out: "list[str]" = []
    for tok in tokens:
        if "{model}" in tok:
            if not model:
                continue                 # モデル未指定なら {model} を含むトークンごと落とす
            out.append(tok.replace("{model}", model))
            continue
        if "{output_file}" in tok:
            if not output_file_holder.get("path"):
                fd, path = tempfile.mkstemp(prefix="agentcli-", suffix=".txt")
                os.close(fd)
                output_file_holder["path"] = path
            out.append(tok.replace("{output_file}", output_file_holder["path"]))
            continue
        out.append(tok)
    return out


DEFAULT_ARGV_LIMIT = 100000
"""argv でプロンプトを渡すときの既定の上限バイト数（`spill_prompt`）。

OS の `ARG_MAX`（Linux で概ね 2MB・環境変数と共有）より十分小さく取る。超えると
`execve` が E2BIG で失敗し、プロセス起動そのものが立たない。"""


def spill_instruction(what: str, *, then: str = "その指示に従ってください") -> str:
    """argv 退避時に本文の代わりに渡す短い指示（`{file}` を含む・P2-5）。

    **枠だけをここに置く。** 呼び出し側が決めるのは `what`（何の全文か）と `then`（読んだ
    あと何をするか）で、役割ごとに違うのはそこだけ。「必ず読み込ませる」という**効き目に
    関わる部分は共通**なので、3 者が全文を自前で持つと、言い回しの改善が 1 か所にしか
    入らない（そして入っていない方は誰も気付かない）。

    **定義側の `spill.instruction`（`agents/<cli>.json`）とは別物**。あちらは権限フラグの
    置き換えを伴う読み取り専用の退避モード用で、`headless_cmd(spill_path=…)` が使う
    （Python からの消費者は無く、dashboard の診断だけ）。混同しないよう、こちらを使うのは
    `spill_prompt` 経由に限る。
    """
    return f"以下のファイルに{what}があります。必ずファイルの内容を読み込み、{then}: {{file}}"


def spill_prompt(prompt: str, limit: "int | None" = None, *, prompt_via: str,
                 prefix: str, instruction: str) -> "tuple[str | None, str]":
    """argv 長制限を超えるプロンプトを一時ファイルへ退避し、`(退避先, 短い指示)` を返す。

    退避が要らなければ `(None, prompt)`。退避先の削除は呼び出し側の責務
    （`subprocess.run` を囲む `finally`）——ここは実行しないので寿命を知らない。

    **`headless_cmd(spill_path=…)` とは別物**。あちらは定義の `spill.args` で
    **権限フラグを置き換える**（kiro-cli では `--trust-all-tools` → `--trust-tools=fs_read`）
    ので、コマンドを実行して確かめる用途のヘッドレス呼び出し（検証エージェント等）に
    使うと実行権限ごと失われる。ここが見ているのは OS の `ARG_MAX` であって CLI の癖では
    ないので、**権限フラグには触らない**。

    `instruction` は `{file}` を含む呼び出し側の文（「何の全文か」は役割ごとに違う）。
    """
    if str(prompt_via) != "argv":
        return None, prompt                  # stdin 渡しは ARG_MAX に当たらない
    cap = int(limit) if limit and int(limit) > 0 else DEFAULT_ARGV_LIMIT
    if len(str(prompt).encode("utf-8")) <= cap:
        return None, prompt
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(prompt))
    return path, str(instruction).replace("{file}", path)


def headless_cmd(spec: dict, model: "str | None", prompt: str, *,
                 readonly: bool = False, no_session: bool = False,
                 spill_path: "str | None" = None) -> dict:
    """ヘッドレス 1 回分を組み立てる（実行はしない・決定的）。

    戻り値: {argv, stdin, output_file, env, empty_output_is_error, timeout, readonly_warning}
    spill_path を渡すと、渡された prompt の末尾へ spill.instruction（{file} 置換済み）を足し、
    権限フラグを spill.args で置き換える（kiro-cli が positional プロンプト併用時に stdin を
    読まない癖への対処）。本文はファイル側にある前提で、prompt には短い指示だけを渡す。

    **argv 長制限の退避には使わない**（`spill_prompt` を使う）。権限フラグの置き換えは
    「本文を読ませるために読み取りだけ許す」読み取り専用の用途に閉じた振る舞いで、
    実行して確かめる呼び出しに掛けると、退避したときだけ何も実行できなくなる。
    """
    m = str(model or spec.get("default_model") or "")
    holder: dict = {}
    argv = _expand(spec["command"], m, holder)
    use_spill = bool(spill_path and spec["spill"]["instruction"])
    mode = _mode_args(spec, interactive=False, readonly=readonly, no_session=no_session)
    if use_spill and spec["spill"]["args"]:
        # 退避時の権限フラグは、そのモードの権限フラグを **置き換える**。退避は「本文を読ませる
        # ためにファイル読み取りだけは許す」という固有のモードで、追加にすると kiro のように
        # `--trust-tools=` と `--trust-tools=fs_read` が並んで後勝ちに賭けることになる。
        mode = [t for t in mode if t not in spec["readonly_args"] and t not in spec["write_args"]]
        mode = list(spec["spill"]["args"]) + mode
    argv += _expand(mode, m, holder)
    if m and spec["model_flag"] and not any("{model}" in t for t in spec["command"]):
        argv += [str(spec["model_flag"]), m]
    argv += _expand(spec["command_suffix"], m, holder)

    text = str(prompt if prompt is not None else "")
    if use_spill:
        # 指示は **置き換えず付け足す**。呼び出し側の指示（役割・出力書式）はプロンプト本文とは
        # 別に argv へ載っていることがあり（Doctor がまさにそれ）、置き換えると役割ごと消える。
        text = (text + " " if text else "") + \
            spec["spill"]["instruction"].replace("{file}", str(spill_path))
    stdin = None
    if spec["prompt_via"] == "argv":
        if spec["prompt_flag"]:
            argv += [str(spec["prompt_flag"]), text]
        else:
            argv.append(text)
    else:
        stdin = text
    return {
        "argv": argv,
        "stdin": stdin,
        "output_file": holder.get("path"),
        "env": dict(spec["env"]),
        "empty_output_is_error": spec["empty_output_is_error"],
        "timeout": spec["timeout"],
        "readonly_warning": readonly_warning(spec, readonly),
    }


def interactive_cmd(spec: dict, model: "str | None", *,
                    readonly: bool = False, no_session: bool = False) -> "list[str]":
    """対話起動 argv を組み立てる。interactive セクションを持たない定義は AgentCliError。"""
    inter = spec.get("interactive")
    if not inter:
        raise AgentCliError(
            f"{spec['name']} は対話起動に対応していません"
            f"（{spec['path']} に interactive.command がありません）")
    m = str(model or spec.get("default_model") or "")
    if any("{model}" in t for t in inter["command"]) and not m:
        raise AgentCliError(f"{spec['name']} の対話起動にはモデルの指定が必要です")
    holder: dict = {}
    argv = _expand(inter["command"], m, holder)
    argv += _expand(_mode_args(spec, interactive=True, readonly=readonly,
                               no_session=no_session), m, holder)
    if m and spec["model_flag"] and not any("{model}" in t for t in inter["command"]):
        argv += [str(spec["model_flag"]), m]
    if holder.get("path"):               # 対話起動でファイル出力は意味を成さない
        raise AgentCliError(f"{spec['name']} の interactive.command に {{output_file}} は使えません")
    return argv


def readonly_warning(spec: dict, readonly: bool) -> str:
    """読み取り専用を要求したが CLI が保証しないときの警告文（S9 未決 7 の決着）。

    このレイヤは宣言どおりの argv を組み立てるだけで、フラグを無視する CLI への防御は持たない。
    できるのは「保証できない」ことを人に伝えることだけなので、判断材料として返す。
    """
    if not readonly or spec["readonly"] == "enforced":
        return ""
    return (f"{spec['name']} は読み取り専用を保証しません"
            "（助言のみのつもりでもファイル変更やコマンド実行が起こりえます）")


def ready_pattern(spec: dict, default: str = "") -> str:
    inter = spec.get("interactive") or {}
    return inter.get("ready_pattern") or default


def ready_timeout_sec(spec: dict, default: float = 60) -> float:
    inter = spec.get("interactive") or {}
    return float(inter.get("ready_timeout_sec") or default)


def prompt_inject(spec: dict) -> str:
    inter = spec.get("interactive") or {}
    return str(inter.get("prompt_inject") or "send-keys")


def classify_error(spec: dict, blob: str) -> "tuple[str, str] | None":
    """定義の errors[] で失敗本文を分類する（組み込みの汎用パターンより先に評価される想定）。"""
    text = str(blob or "")
    for cls, pattern, hint in spec.get("errors", []):
        if pattern.search(text):
            return cls, hint
    return None


def skill_command_prefix(spec: dict) -> str:
    """スキル起動コマンドの行頭記号（既定 `/`。codex は `$`）。"""
    return str(spec.get("skill_command_prefix") or "/")


_SKILL_CMD_RE = re.compile(r"^/(?=[A-Za-z0-9_-]+(?:[\s:]|$))", re.M)


def rewrite_skill_commands(text: str, prefix: str) -> str:
    """セッションへ送るテキストの行頭 `/` を、その CLI のスキル起動記号へ差し替える。

    人は `/skill-name` と書くが、codex は `$skill-name` でないとスキルが起動しない。
    既定 `/` の CLI では何も変えない。行頭が `/` + 英数字トークンのときだけ対象にするので、
    パス（`/home/...`）は 2 つ目の `/` があるため書き換わらない。
    """
    p = str(prefix or "/")
    s = "" if text is None else str(text)
    return s if p == "/" else _SKILL_CMD_RE.sub(p, s)
