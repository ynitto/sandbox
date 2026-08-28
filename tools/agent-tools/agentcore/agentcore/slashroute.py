"""スラッシュ行のルータ — 起動形を argv より先に決める 1 実装。

設計: docs/plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md §3.2。

**正はクラウド CLI がどう見えるかである。** claude / codex では、スラッシュコマンドは
人も engine も同じ 1 行で投げる CLI 自身のコマンド面で、面が 1 つだから分裂しようがない。
agent-herd はそこが 3 か所に分かれていた——`harness.toolloop.run_prompt` の層別分岐・
`ollama_tui` のローカルコマンド表・`ollama_skills` の先頭スラッシュ切り出し。同じ
「先頭の `/name` をどう読むか」を 3 通りに書いていたので、片方だけ直る／片方だけ知らない
が静かに起きる。本モジュールはその 3 つが引く 1 枚の表であり、解釈そのものである。

規約は次の 2 つだけで、どちらも既存の実装から昇格させたものである。

- **名前**は `^[a-z0-9][a-z0-9._-]*$`（`agent_loop.scheduler._SLASH_NAME_RE` と
  `ollama_skills.NAME_RE` が同じ字種を別々に書いていた。ここを正典にする）。
- **コマンド行**は本文の**先頭から連続する** `/name [args]` の行（空行でブロックが終わる）。
  本文中まで見ると、貼り付けたコードの `/usr/bin/...` を呼び出しと誤認するため。

判定は文字列マッチだけで、LLM は 1 回も呼ばれない——**起動形（どのハーネス・どの
toolset・どの候補）は argv を組む前に決まらなければならない**からである。

種別は 4 つ（設計 §3.2 の表）。**表を持つのは A（セッション操作）と B（実行形）だけ**で、
この 2 つは agentcore が用意するコード内の定数である（設定ファイルにしない。§8 非目標）。
C（用途）は人が置く宣言 1 枚（`~/.agents/commands/<name>.md`）、D（スキル）は
`SKILL.md` の実在がそのまま答えなので、どちらも表には載らない——`lookup()` が None を
返したら宣言を探し、それも無ければ呼び出し側のスキル解決器へ尋ねる。探索先を面ごとに
持ち替えられるよう、スキルの解決だけは呼び出し側から渡してもらう（TUI は
`ollama_skills.find_skill`、ハーネスは `_tl_resolve_skill` で探索順が違う）。

入口は 3 つある。

- :func:`plan` … ランチャが**起動前に 1 回**読む。本文の先頭ブロックを解釈して、
  道具立て・ハーネス・宣言の起動形・材料へ載せるスキル・残りの本文へ割る。
- :func:`resolve` … engine が用途の 1 語だけを渡して起動形を受け取る（§3.3）。
- :func:`lookup` / :func:`declaration` / :func:`classify` … 表と宣言を素で引く。
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

# 名前の正典。install.py が配るスキルのディレクトリ名と同じ字種。
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# 先頭ブロックのコマンド行。引数は自由文字列。
_LINE_RE = re.compile(r"^/([a-z0-9][a-z0-9._-]*)(?:[ \t]+(.*))?$")
# 同じ規約を大小文字無視で読むための版。**プロンプト本文の切り出しには使わない**
# （スキル名は小文字が規約で、`/README.md` を呼び出しと誤認したくない）。人が打つ面
# ——TUI——だけが `/HELP` を受けるためにこちらを引く。
_LINE_RE_I = re.compile(_LINE_RE.pattern, re.I)

# 種別（設計 §3.2）。**表を持つのは A と B だけ**——この 2 つは agentcore が用意する
# コード内の定数で、設定ファイルにしない（§8 非目標）。人が書くのは種別 C の宣言だけで、
# D（スキル）は `SKILL.md` の実在が答えなので表に載らない。
KIND_SESSION = "session"   # A: セッション操作（実体はコード内の関数）
KIND_SHAPE = "shape"       # B: 実行形（ハーネス / toolset の切替）
KIND_PURPOSE = "purpose"   # C: 用途（宣言 1 枚。`~/.agents/commands/<name>.md`）
KIND_SKILL = "skill"       # D: スキル（SKILL.md を材料へ載せる）

# 種別 B が選ぶ道具立て。`ollama_loop.TOOLSETS` の綴りをそのまま使う（写さない）。
TOOLSET_NONE = ""          # 道具なし＝推論だけ
TOOLSET_READ = "read"      # 読み取り専用の語彙
TOOLSET_BASH = "bash"      # 汎用シェル（既定）

# 種別 B が選ぶハーネス。当て先はすべて実装済み（設計 §3.2 の表）。
HARNESS_TOOLLOOP = "toolloop"          # harness/toolloop.run_goal（限定ツール契約）
HARNESS_STATEMACHINE = "statemachine"  # harness/statemachine.cmd_statemachine


class Command(NamedTuple):
    """ルート表の 1 行。`summary` / `arg_hint` は `/help` の見え方をここへ寄せるためにある。"""

    name: str
    kind: str
    summary: str = ""
    arg_hint: str = ""
    aliases: "tuple[str, ...]" = ()
    onoff: bool = False       # 引数が on|off のトグル（Tab 補完がこれを見る）
    hidden: bool = False      # `/help` の一覧には出さない（別名など）
    # 種別 B（実行形）が決めるもの。ここに書いた分だけモデルの判断から 1 語へ移る。
    tools: "bool | None" = None    # ツール実行ループを使うか（None = 触らない）
    toolset: "str | None" = None   # どのツールセットか（None = 触らない）
    harness: str = ""              # どのハーネスへ回すか（"" = 回さない）
    # 引数をルータが食べるか。既定は **食べない**——`/ask 富士山の高さは?` の引数は
    # 依頼そのもので、本文へ回らないと「コマンドだけ書いて中身が消えた」になる。
    # 食べるのは引数が起動形そのものを名指しするとき（`/sm <名前>`）だけ。
    consumes_args: bool = False

    @property
    def takes_args(self) -> bool:
        """引数を取るか（`arg_hint` の有無がそのまま契約）。

        取らないコマンドに引数が付いていたらコマンドではない——`/status` は `/status` で、
        `/status なにか` は本文である。この 1 行が「本文をコマンドと読み違える」を止める。
        """
        return bool(self.arg_hint)

    @property
    def spell(self) -> str:
        """`/help` の左列。`/tools on|off` のように引数の型まで含めた綴り。"""
        return f"/{self.name}" + (f" {self.arg_hint}" if self.arg_hint else "")


# 種別 A: セッション操作。**並び順が `/help` の並び順**である（実体は ollama_tui の関数）。
_SESSION_COMMANDS: "tuple[Command, ...]" = (
    Command("skills", KIND_SESSION, "読めるスキルの一覧"),
    Command("tools", KIND_SESSION, "ツール実行ループの切り替え", arg_hint="on|off", onoff=True),
    Command("think", KIND_SESSION, "思考モードの切り替え", arg_hint="on|off", onoff=True),
    Command("model", KIND_SESSION, "モデルの切り替え", arg_hint="<name>"),
    Command("ctx", KIND_SESSION, "直近の文脈使用量（使用トークン / 上限 / 割合）"),
    Command("status", KIND_SESSION, "いまの進捗（JSON）"),
    Command("keys", KIND_SESSION, "使えるキー操作の一覧"),
    Command("help", KIND_SESSION, "この一覧"),
    Command("quit", KIND_SESSION, "終了", aliases=("exit",)),
)

# 種別 B: 実行形。**ツールセットの選択がモデルの判断から 1 語へ移る**のがこの表の要点で、
# 弱いモデル向けの自由度削減はその副産物である（設計 §3.4）。いままでは「ツールを使うかは
# モデルが決める」（bash ループが自分でコマンドを選ぶ）だったものを、人か engine が
# 1 語で固定する。当て先はすべて実装済みで、新しい機構は足していない。
_SHAPE_COMMANDS: "tuple[Command, ...]" = (
    Command("ask", KIND_SHAPE, "道具なしで答えさせる（推論だけ）",
            tools=False, toolset=TOOLSET_NONE),
    Command("find", KIND_SHAPE, "読み取り専用の道具で調べさせる",
            tools=True, toolset=TOOLSET_READ),
    Command("edit", KIND_SHAPE, "編集ハーネスで直させる", arg_hint="[指示]",
            harness=HARNESS_TOOLLOOP),
    Command("sm", KIND_SHAPE, "ステートマシンを走らせる",
            arg_hint="<名前> [--param k=v]", harness=HARNESS_STATEMACHINE,
            consumes_args=True),
)

_TABLE: "dict[str, Command]" = {}
for _cmd in _SESSION_COMMANDS + _SHAPE_COMMANDS:
    _TABLE[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _TABLE[_alias] = _cmd
del _cmd


# -- 表を引く ---------------------------------------------------------------

def lookup(name: str) -> "Command | None":
    """コマンド名 → ルート表の行。載っていなければ None（呼び出し側がスキルを探す）。

    名前は大小文字を区別しない（TUI が `/HELP` を受けていた挙動をそのまま持つ）。
    """
    return _TABLE.get(str(name or "").strip().lower())


def commands(kind: "str | None" = None) -> "tuple[Command, ...]":
    """表に載っている行（別名は含まない）。`kind` で絞れる。"""
    rows = _SESSION_COMMANDS + _SHAPE_COMMANDS
    return rows if kind is None else tuple(c for c in rows if c.kind == kind)


def spellings(kind: str = KIND_SESSION) -> "tuple[str, ...]":
    """`/name` の綴り（別名を含む）。Tab 補完の候補に使う。"""
    names: "list[str]" = []
    for cmd in commands(kind):
        names.append("/" + cmd.name)
        names.extend("/" + alias for alias in cmd.aliases)
    return tuple(sorted(names))


def onoff_spellings(kind: str = KIND_SESSION) -> "tuple[str, ...]":
    """引数が `on|off` のコマンド綴り。補完が値側の候補を出すために引く。"""
    return tuple("/" + cmd.name for cmd in commands(kind) if cmd.onoff)


def _display_width(text: str) -> int:
    """端末での見た目の桁数。全角は 2 桁として数える。

    `str` の長さで桁を揃えると、引数ヒントに日本語が入った行だけ列が崩れる
    （`/help` は tmux の `capture-pane` からも読まれるので、崩れが画面判定に乗る）。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def render_help(kind: str = KIND_SESSION, *, width: int = 17, project_dir=None) -> str:
    """`/help` の本文（表の並び順・別名は出さない）。

    `width` は左列の最小桁数。実際の綴りがそれより広ければ広い方へ揃える——種別 A の
    見え方（左列 17 桁）を保ったまま、引数ヒントの長い種別 B でも列が崩れない。

    種別 C（用途の宣言）は表に無いので、`kind=KIND_PURPOSE` のときだけ宣言から組む
    ——ここが空なら、その環境には宣言が 1 枚も置かれていない。
    """
    if kind == KIND_PURPOSE:
        rows = [d.as_command() for d in declarations(project_dir)
                if lookup(d.name) is None]
    else:
        rows = [cmd for cmd in commands(kind) if not cmd.hidden]
    if not rows:
        return ""
    column = max([width] + [_display_width(cmd.spell) + 2 for cmd in rows])
    return "\n".join(
        "  " + cmd.spell + " " * (column - _display_width(cmd.spell)) + cmd.summary
        for cmd in rows)


# -- コマンド行を切り出す ---------------------------------------------------

def parse_line(line: str, *, casefold: bool = False) -> "tuple[str, str] | None":
    """1 行 → (name, args)。コマンド行でなければ None。

    `casefold=True` は名前の大小文字を無視して読み、名前だけを小文字へ畳む（引数は
    そのまま返す——`/model Gemma4:12B` のモデル名を潰さない）。
    """
    match = (_LINE_RE_I if casefold else _LINE_RE).match((line or "").strip())
    if match is None:
        return None
    name = match.group(1)
    return (name.lower() if casefold else name), (match.group(2) or "").strip()


def split_leading(prompt: str) -> "tuple[list[tuple[str, str]], str]":
    """先頭ブロックのコマンド行を取り出す → ([(name, args), …], 残りの本文)。

    空行はブロックの終わりとみなす（空行の後ろは本文）。
    """
    lines = (prompt or "").splitlines()
    calls: "list[tuple[str, str]]" = []
    index = 0
    for index, line in enumerate(lines):
        parsed = parse_line(line)
        if parsed is None:
            break
        calls.append(parsed)
    else:
        index = len(lines)
    return calls, "\n".join(lines[index:]).lstrip("\n")


def normalize_line(line: str) -> str:
    """`<name> [args]` / `/<name> [args]` を `<name> [args]`（先頭 `/` 無し）へ揃える。

    設定ファイル（agent-loop の `slash:`）は先頭 `/` を剥がして持つ規約なので、
    渡ってきた行のどちらの綴りも受けられるようにしておく。
    """
    text = str(line or "").strip()
    return text[1:].strip() if text.startswith("/") else text


# -- 種別 C: 用途の宣言 1 枚 ------------------------------------------------
#
# 規約は `llm`（simonw/llm）の Template から借りる。コードは持ち込まない（設計 §3.3・§8.1）。
# `llm/templates.py` の Template は name / prompt / system / model / options / tools /
# schema_object を 1 枚に束ねており、これは「**コマンド名 → システムプロンプト・モデル・
# ツール集合・出力契約の束縛**」そのものである。いま `agents/*.json` の `variants` と
# engine 側の許可リストへ散っていたものが、この 1 枚に畳める。
#
# frontmatter は**平らな `key: value` だけ**を読む。宣言は平らであるべきもので、YAML の
# 全文法を受けると「読めるが意味が違う」書き方が入り込む——そして agentcore は
# 標準ライブラリだけで動く必要がある（zipapp の制約）ので、pyyaml は前提にできない。

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)
_DECL_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)$")
# 宣言ディレクトリの探索順を差し替える seam（テストと、配布物を差し替えたい人向け）。
_COMMANDS_DIR_ENV = "AGENT_COMMANDS_DIR"


class DeclarationError(RuntimeError):
    """宣言が壊れている。黙って無視せず明示エラー（設定ミスの静かな握り潰しを作らない）。"""


# プロンプト外出しの frontmatter キー → slot 名（設計 §3.5 / 段 13）。frontmatter は
# 平らな 1 行値しか受けないので、値は**宣言ファイルからの相対パス**にし、複数行の
# テンプレート本文はそのファイルに置く（mini-swe-agent の「設定側に出す」と同じ形）。
_TEMPLATE_KEYS = {
    "system-template": "system",
    "instance-template": "instance",
    "observation-template": "observation",
    "format-error-template": "format_error",
}


def _load_templates(fields: dict, path) -> "tuple[tuple[str, str], ...]":
    out = []
    for key, slot in sorted(_TEMPLATE_KEYS.items()):
        if key not in fields:
            continue
        ref = fields[key].strip()
        if not ref:
            raise DeclarationError(f"用途の宣言 {path}: {key} が空です（テンプレートファイルのパスを書きます）")
        target = Path(ref) if os.path.isabs(ref) else Path(path).parent / ref
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise DeclarationError(
                f"用途の宣言 {path}: {key} のファイルを読めません: {target}（{exc}）") from exc
        out.append((slot, text))
    return tuple(out)


class Declaration(NamedTuple):
    """`~/.agents/commands/<name>.md` 1 枚。本文がそのままシステムプロンプトになる。"""

    name: str
    path: str
    description: str = ""
    agent: str = ""                     # 起動形（旧 `variants.<用途>` の宛先）
    model: str = ""                     # 用途専用の既定（実測があればそちらが勝つ）
    tools: "tuple[str, ...] | None" = None   # None=宣言なし / ()=道具なし
    output: str = ""                    # 出力契約（json 等）
    argument_hint: str = ""
    system: str = ""                    # 本文＝システムプロンプト
    # ツールループのプロンプト外出し（設計 §3.5 / 段 13）。(slot, text) の組で、
    # slot は ollama_loop.TEMPLATE_SLOTS のいずれか。frontmatter の値はファイルパス
    # （宣言ファイルからの相対）で、中身をここへ読み込んで持つ。
    templates: "tuple[tuple[str, str], ...]" = ()

    def as_command(self) -> Command:
        """`/help` と補完のためにルート表の 1 行として見せる。"""
        return Command(self.name, KIND_PURPOSE, self.description,
                       arg_hint=self.argument_hint)


def _bundled_commands_dir() -> "Path | None":
    """ツール同梱の宣言（このリポジトリの `commands/`）。

    `agentcli._bundled_dir` と同じ考え方——install が `~/.agents/commands/` へ配るが、
    リポジトリから直接動かす開発環境でも解決できるよう最後の候補として見る。
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "commands").is_dir() and (parent / "agents" / "kiro.json").is_file():
            return parent / "commands"
    return None


def command_dirs(project_dir=None) -> "list[Path]":
    """宣言の探索順。**先勝ち**（上位に置けば同梱の宣言を上書きできる）。

    順序は定義（`agentcli.plugin_dirs`）と同じ規約に揃える——宣言は起動形を決めるもので、
    定義といちばん近い。スキル（`~/.agents/skills`）とも同じ共通ホームの下に置く。
    """
    dirs: "list[Path]" = []
    raw = os.environ.get(_COMMANDS_DIR_ENV, "")
    for part in raw.split(os.pathsep):
        if part.strip():
            dirs.append(Path(part.strip()).expanduser())
    dirs.append((Path(project_dir).expanduser() if project_dir else Path.cwd())
                / ".agents" / "commands")
    dirs.append(Path.home() / ".agents" / "commands")
    bundled = _bundled_commands_dir()
    if bundled:
        dirs.append(bundled)
    return dirs


def _scalar(value: str) -> str:
    """frontmatter の値 1 つ。引用符で囲まれていればその中身、そうでなければ行末コメントを落とす。

    YAML の全文法は受けないが、**引用とコメントだけは同じに読む**——設計の例が
    `argument-hint: "[基準ファイル]"` と書いており、引用符が値に混ざると `/help` の
    左列にそのまま出てしまう。
    """
    text = (value or "").strip()
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote):
            end = text.find(quote, 1)
            if end > 0:
                return text[1:end]
    return text.split(" #", 1)[0].rstrip() if " #" in text else text


def _parse_frontmatter(text: str, path) -> "tuple[dict, str]":
    """frontmatter（平らな `key: value`）と本文へ割る。"""
    match = _FRONTMATTER_RE.match(text or "")
    if match is None:
        return {}, (text or "").lstrip("\n")
    fields: "dict[str, str]" = {}
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        found = _DECL_KEY_RE.match(raw_line.rstrip())
        if found is None:
            raise DeclarationError(
                f"用途の宣言 {path}: frontmatter は平らな `key: value` だけです: {raw_line!r}")
        fields[found.group(1).strip().lower()] = _scalar(found.group(2))
    return fields, text[match.end():].lstrip("\n")


def _parse_list(value: str, key: str, path) -> "tuple[str, ...]":
    """`[]` / `[a, b]` の行内リスト。ブロック形式（`- a`）は受けない。"""
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise DeclarationError(
            f"用途の宣言 {path}: {key} は `[]` か `[a, b]` の形で書きます: {value!r}")
    items = [item.strip().strip("\"'") for item in text[1:-1].split(",")]
    return tuple(item for item in items if item)


def _is_declaration_file(path) -> bool:
    """宣言として読むファイルか。**綴りがそのまま名前**なので大文字は宣言ではない。

    置き場に `README.md` を置けるようにするための 1 行でもある（コマンド名は小文字が
    規約なので、大文字を含む md は説明書として素通しできる）。
    """
    stem = Path(path).stem
    return stem == stem.lower() and bool(NAME_RE.match(stem))


def load_declaration(path) -> Declaration:
    """1 枚読む。壊れていれば `DeclarationError`。"""
    path = Path(path)
    name = path.stem
    if not _is_declaration_file(path):
        raise DeclarationError(f"用途の宣言 {path}: 名前が規約外です（{NAME_RE.pattern}・小文字）")
    fields, body = _parse_frontmatter(
        path.read_text(encoding="utf-8", errors="replace"), path)
    unknown = sorted(set(fields) - {"description", "agent", "model", "tools", "output",
                                    "argument-hint", "name", *_TEMPLATE_KEYS})
    if unknown:
        raise DeclarationError(
            f"用途の宣言 {path}: 知らない項目です: {', '.join(unknown)}"
            "（description / agent / model / tools / output / argument-hint / "
            + " / ".join(sorted(_TEMPLATE_KEYS)) + "）")
    tools = (_parse_list(fields["tools"], "tools", path)
             if "tools" in fields else None)
    if tools is not None and len(tools) > 1:
        raise DeclarationError(
            f"用途の宣言 {path}: tools は道具の詰め合わせ（ツールセット）を 1 つだけ書きます"
            f"（`[]`＝道具なし / `[{TOOLSET_READ}]` / `[{TOOLSET_BASH}]`）: {fields['tools']!r}")
    if tools and tools[0] not in (TOOLSET_READ, TOOLSET_BASH):
        raise DeclarationError(
            f"用途の宣言 {path}: 知らないツールセットです: {tools[0]}"
            f"（{TOOLSET_READ} / {TOOLSET_BASH}）")
    return Declaration(
        name=name, path=str(path), description=fields.get("description", ""),
        agent=fields.get("agent", "").strip().lower(), model=fields.get("model", "").strip(),
        tools=tools, output=fields.get("output", "").strip().lower(),
        argument_hint=fields.get("argument-hint", ""), system=body,
        templates=_load_templates(fields, path))


# 解決結果のキャッシュ。`resolve()` は engine のホットパス（agent-flow はノードごとに
# 引く）にあり、宣言が無い環境では毎回 4 回の `is_file()` になる。`agentcli` と同じ
# 流儀で、テストと配布物の入れ替えのために `clear_cache()` を出しておく。
_DECL_CACHE: "dict[tuple[str, str], Declaration | None]" = {}


def clear_cache() -> None:
    """宣言のキャッシュを捨てる（配布物を入れ替えたとき・テスト）。"""
    _DECL_CACHE.clear()


def declaration(name: str, project_dir=None) -> "Declaration | None":
    """名前 → 宣言。無ければ None。探索順で先に見つかったものが勝つ。"""
    key = str(name or "").strip().lower()
    if not NAME_RE.match(key):
        return None
    cache_key = (str(project_dir or ""), key)
    if cache_key in _DECL_CACHE:
        return _DECL_CACHE[cache_key]
    found = None
    for base in command_dirs(project_dir):
        candidate = base / f"{key}.md"
        if candidate.is_file():
            found = load_declaration(candidate)
            break
    _DECL_CACHE[cache_key] = found
    return found


def declarations(project_dir=None) -> "list[Declaration]":
    """読める宣言の一覧（名前順・先勝ち）。壊れた 1 枚は一覧を止めない。"""
    seen: "dict[str, Declaration]" = {}
    for base in command_dirs(project_dir):
        try:
            entries = sorted(base.glob("*.md"))
        except OSError:
            continue
        for entry in entries:
            key = entry.stem
            if key in seen or not _is_declaration_file(entry):
                continue
            try:
                seen[key] = load_declaration(entry)
            except (DeclarationError, OSError):
                continue          # 一覧は壊れた 1 枚で止めない（引いたときに落ちる）
    return [seen[key] for key in sorted(seen)]


# -- 用途 → 起動形の調停（種別 C。engine の許可リストをここへ畳む） ---------

def _agentcli_module(module=None):
    """CLI 定義のローダ。渡されなければ agentcore のものを遅延 import する。

    引数で受けられるようにしてあるのはハーネスのため——`agent["agentcli"]` として
    host から渡ってきたモジュールをそのまま使う（継ぎ目を 1 か所に保つ）。
    """
    if module is not None:
        return module
    from agentcore import agentcli  # 遅延: 表を引くだけの利用者に定義ローダを背負わせない
    return agentcli


def resolve(*, command: str, cli: str, model: "str | None" = None,
            explicit_model: bool = False, by_purpose: bool = False,
            project_dir=None, agentcli=None) -> dict:
    """コマンド名 1 語 → 起動形。設計 2026-08-27 §3.2・§3.3。

    返すのは次の 1 枚で、呼び出し側は**自分が使える分だけ**読めばよい。

    | 鍵 | 何 | どこから |
    |---|---|---|
    | `agent_cli` / `model` | どのエージェントで走らせるか | 宣言（C）→ 変種（`variants`） |
    | `tools` / `toolset` | 道具立て | 実行形（B）→ 宣言（C） |
    | `harness` | どのハーネスへ回すか | 実行形（B） |
    | `output` / `system` | 出力契約とシステムプロンプト | 宣言（C） |
    | `declared` / `variant` | どちらが効いたか（観測用） | — |

    **engine は許可リストを持たない。** 用途の 1 語を渡すだけで、振り替えるかどうかは
    宣言側（種別 C の 1 枚、無ければ `agents/<name>.json` の `variants`）が決める。以前は
    flow / project / audit が各々の許可リストを持ち、harness は許可リスト無しで直に
    引いていた（G2）。同じ 15 キーを 3 通りに書いていたので「宣言したのに効かない」が
    静かに起きていた。

    **種別 A（セッション操作）の綴りは用途ではない**ので振り替えない。名前空間は 1 つで、
    `/model` が用途としても解釈される状態を作らない。種別 B は用途でもありうる
    ——`/edit` は実行形（編集ハーネス）であり、どのエージェントで編集するかは宣言 1 枚が
    決める（§3.6）。

    モデルの調停規則は agent-flow が持っていたものを正典に昇格させる（G4）:

    - `explicit_model` … 呼び出し元（人の設定・run 単位の固定）がモデルを名指ししている。
      **用途専用の既定で上書きしない。**
    - `by_purpose` … `selection_policy.by_purpose` 由来の決定である。これはその用途の
      **実測に基づく選択**なので上書きしない——上書きすると、例えば judge で bounded-review
      の裏付けを持つモデルが選ばれたのに用途の既定（base の弱いモデル）へ黙って戻り、
      その用途では blocked と実測されているモデルで走る。
    - どちらでもないとき（用途を知らない共通の順位表・既定）だけ、用途専用の
      チューニング（宣言の `model` / 変種の `default_model`）を採る。
    """
    name = str(command or "").strip().lower()
    result = {"agent_cli": str(cli or "").strip().lower(), "model": model,
              "variant": False, "declared": False,
              "tools": None, "toolset": None, "harness": "", "output": "", "system": "",
              "templates": {}}
    if not name:
        return result

    row = lookup(name)
    if row is not None and row.kind == KIND_SESSION:
        return result
    if row is not None:                     # 種別 B: 実行形はコード内の表が決める
        result["tools"] = row.tools
        result["toolset"] = row.toolset
        result["harness"] = row.harness

    try:                                    # 種別 C: 宣言 1 枚が最優先
        decl = declaration(name, project_dir)
    except (DeclarationError, OSError):
        decl = None                         # 壊れた宣言で実行を殺さない（引き直せば落ちる）
    if decl is not None:
        result["declared"] = True
        result["output"] = decl.output
        result["system"] = decl.system
        result["templates"] = dict(decl.templates)
        if decl.tools is not None:
            result["tools"] = bool(decl.tools)
            result["toolset"] = decl.tools[0] if decl.tools else TOOLSET_NONE
        if decl.agent:
            result["agent_cli"] = decl.agent
        if decl.model and not (explicit_model or by_purpose):
            result["model"] = decl.model
            explicit_model = True           # 宣言より弱い既定へ落とさない

    # `variants` は移行期の併読。宣言が `agent` を言っていなければ、今日どおり定義側の
    # 申告が起動形を決める。宣言が言っていれば、その定義の申告をさらに引く——`agent: ollama`
    # ＋ 用途 verify で `ollama-verify` へ落ちるのは、この 2 段があるからである。
    try:
        variant = _agentcli_module(agentcli).resolve_variant(
            result["agent_cli"], name, project_dir)
    except Exception:
        # 定義が読めない・壊れているのは設定の問題で、実行を殺す理由にはしない
        # （agentcli.resolve_variant 自身が None を返す方針と揃える）。
        return result
    if not variant:
        return result
    result["agent_cli"] = variant["agent_cli"]
    result["variant"] = True
    if not (explicit_model or by_purpose) and variant.get("default_model"):
        result["model"] = variant["default_model"]
    return result


# -- 未知のコマンドは明示エラーで止める -------------------------------------

class CommandNotSupportedHere(RuntimeError):
    """コマンドは実在するが、この面では走らせられない。

    `/sm` を agent-ollama 単体へ、`/help` を 1 回実行へ投げたときのように、**綴りは
    正しいが当て先がここに無い**場合。「知らない名前」（`UnknownCommand`）とは分けて
    伝える——直し方が違う（前者は打ち直し、後者は別の入口から打つ）。
    """


class UnknownCommand(RuntimeError):
    """先頭のコマンド行がルート表にも宣言にもスキルにも無い。

    黙って本文として推論へ流さない（設計 §3.2・§3.4）。**先頭が `/` ならルール、そうで
    なければ推論**——この境目が曖昧だと、打ち間違えた `/verfy` が「なぜか普通の依頼として
    実行された」になり、しかも実行ログにはその 1 行が本文として残る。層3 でスキルが
    解決できないときに起動時 fail fast にしているのと同じ方針である。
    """


def classify(name: str, *, project_dir=None, skill_exists=None) -> str:
    """コマンド名 → 種別（`session` / `shape` / `purpose` / `skill` / `unknown`）。

    `skill_exists(name) -> bool` は呼び出し側のスキル解決器。探索順は面によって違う
    （TUI は `ollama_skills.find_skill`、ハーネスは `_tl_resolve_skill`）ので、ルータは
    「実在するか」だけを尋ねて置き場を知らないままにする。
    """
    key = str(name or "").strip().lower()
    if not key:
        return "unknown"
    row = lookup(key)
    if row is not None:
        return row.kind
    try:
        if declaration(key, project_dir) is not None:
            return KIND_PURPOSE
    except (DeclarationError, OSError):
        return KIND_PURPOSE       # 壊れた宣言でも「知らない名前」ではない
    if skill_exists is not None and skill_exists(key):
        return KIND_SKILL
    return "unknown"


def unknown_command_message(name: str, *, project_dir=None, skill_dirs=()) -> str:
    """未知コマンドの明示エラー文。**逃げ道まで書く**のが要点。

    規約が「先頭から連続する `/name` の行」である以上、`/tmp を消して` のような普通の
    依頼も先頭に来ればコマンド行に見える。止めるだけでは直し方が分からないので、
    本文として送る書き方（先頭に空行を 1 つ）をその場で示す。
    """
    known = sorted(set(cmd.name for cmd in commands())
                   | set(d.name for d in declarations(project_dir)))
    lines = [f"知らないコマンドです: /{name}",
             "  使えるコマンド: " + ", ".join("/" + n for n in known)]
    if skill_dirs:
        lines.append("  スキルの探索先: " + ", ".join(str(d) for d in skill_dirs))
    lines.append(f"  用途を足すなら宣言 1 枚: {command_dirs(project_dir)[0]}/{name}.md")
    lines.append("  コマンドではなく本文として送りたいときは、先頭に空行を 1 つ入れてください"
                 "（空行より後ろは本文です）。")
    return "\n".join(lines)


# -- 起動前に読む 1 回（ランチャの入口） ------------------------------------

class Plan(NamedTuple):
    """先頭のコマンド行を読み終えた結果。**argv / opts を組む前にこれが決まる。**

    設計 §3.2 の要点は「スラッシュ行は起動前に読む」——起動形（どのハーネス・どの
    toolset・どの profile・どの候補）は argv を組む前に決まらなければならない。判定は
    文字列マッチだけで、LLM は 1 回も呼ばれない。
    """

    body: str = ""                              # コマンド行を除いた本文
    tools: "bool | None" = None                 # ツール実行ループを使うか
    toolset: "str | None" = None                # どのツールセットか
    harness: str = ""                           # どのハーネスへ回すか
    agent: str = ""                             # 宣言が名指しした起動形
    model: str = ""                             # 用途専用の既定
    output: str = ""                            # 出力契約
    system: str = ""                            # 宣言の本文＝システムプロンプト
    templates: "tuple[tuple[str, str], ...]" = ()  # プロンプト外出し（§3.5 / 段 13）
    skills: "tuple[tuple[str, str], ...]" = ()  # 材料へ載せる (名前, 引数)
    session: "tuple[tuple[str, str], ...]" = () # 種別 A（実体は面が持つ）
    commands: "tuple[tuple[str, str], ...]" = ()  # 読み取った行の全体（観測用）


def plan(prompt: str, *, project_dir=None, skill_exists=None, strict: bool = True,
         warn=None) -> Plan:
    """本文の先頭ブロックを読み、起動形を決める。

    未知の名前は既定で `UnknownCommand`（設計 §3.2 の「どちらでもない → 明示エラー」）。
    `strict=False` は従来の寛容な扱い——**先頭ブロックを 1 行も消費せず**警告して素通し
    する。全部解決できたときだけ切り離す、という all-or-nothing はどちらでも同じである。
    """
    calls, body = split_leading(prompt)
    if not calls:
        return Plan(body=prompt or "")

    shape: "dict" = {"tools": None, "toolset": None, "harness": "",
                     "agent": "", "model": "", "output": "", "system": ""}
    templates: "dict[str, str]" = {}
    skills: "list[tuple[str, str]]" = []
    session: "list[tuple[str, str]]" = []
    unknown: "list[str]" = []
    inline: "list[str]" = []          # コマンド行に書かれた依頼そのもの
    for name, args in calls:
        kind = classify(name, project_dir=project_dir, skill_exists=skill_exists)
        if kind == KIND_SESSION:
            session.append((name, args))
            continue
        if kind == KIND_SKILL:
            skills.append((name, args))
            continue
        if kind == "unknown":
            unknown.append(name)
            continue
        # 種別 B / C。**同じ 1 枚に落として最後の宣言が勝つ**——`/find /verify` のように
        # 実行形と用途を重ねたとき、どちらも自分が言う分だけを埋める。
        # `cli=""` で引く——ここは**本文側の読み**で、どの base エージェントで走るかは
        # まだ決まっていない。変種（`variants`）の解決は engine が実際の CLI を持って
        # `resolve()` を呼ぶときに効く。ここで拾えるのは宣言（種別 C）が言う分だけである。
        routed = resolve(command=name, cli="", project_dir=project_dir)
        for key in ("tools", "toolset"):
            if routed[key] is not None:
                shape[key] = routed[key]
        for key in ("harness", "output", "system"):
            if routed[key]:
                shape[key] = routed[key]
        if routed["templates"]:
            templates.update(routed["templates"])
        if routed["declared"]:
            if routed["agent_cli"]:
                shape["agent"] = routed["agent_cli"]
            if routed["model"]:
                shape["model"] = routed["model"]
        row = lookup(name)
        if args and not (row is not None and row.consumes_args):
            inline.append(args)

    if unknown:
        if strict:
            raise UnknownCommand(unknown_command_message(
                unknown[0], project_dir=project_dir,
                skill_dirs=_skill_dirs_hint(skill_exists)))
        for name in unknown:
            if warn is not None:
                warn(f"知らないコマンドです: /{name}（本文として送ります）")
        return Plan(body=prompt or "", commands=tuple(calls))

    # コマンド行に書かれた依頼は本文の頭へ戻す。`/ask 富士山の高さは?` の 1 行だけで
    # 送れないと、人は「コマンドを打ってから改行して本文」を毎回書かされる。
    if inline:
        body = "\n".join(inline + ([body] if body.strip() else []))
    return Plan(body=body, skills=tuple(skills), session=tuple(session),
                commands=tuple(calls), templates=tuple(sorted(templates.items())),
                **shape)


def _skill_dirs_hint(skill_exists) -> "tuple[str, ...]":
    """エラー文へ載せるスキル探索先。解決器が自分の探索先を言えるときだけ拾う。"""
    dirs = getattr(skill_exists, "search_dirs", None)
    try:
        return tuple(str(d) for d in dirs()) if callable(dirs) else ()
    except Exception:
        return ()


# -- 本文への適用（層2 / 層3 の分岐をここへ畳む） ---------------------------

def apply_to_goal(goal: str, lines, *, native: bool,
                  prefix: str = "/") -> "tuple[str, list[str]]":
    """コマンド行を本文へ適用して (本文, 明示スキル名) を返す。

    `native=True` は**行を残して渡す**——ネイティブのスラッシュを持つ CLI（層2 の
    tool-loop 内蔵 CLI）では、CLI 自身がそれを解釈するのが正しい。`prefix` はその CLI の
    スキル起動記号（codex は `$`）。

    `native=False` は**行を消費する**——ルータの解釈が実装そのものになる。層3
    （single-shot）ではスキルとして解決し、手順に従う旨の 1 行を本文の頭へ置く。
    どちらも `run_prompt` が層別に書いていたものと同じ文面である。
    """
    normalized = [normalize_line(item) for item in (lines or [])]
    normalized = [item for item in normalized if item]
    if not normalized:
        return goal, []
    if native:
        return "\n".join(prefix + line for line in normalized) + "\n\n" + goal, []
    skill_names: "list[str]" = []
    for line in normalized:
        name, _, args = line.partition(" ")
        if name not in skill_names:
            skill_names.append(name)
        note = f"`{name}` スキルの手順に従って実行してください。"
        if args.strip():
            note += f"（引数: {args.strip()}）"
        goal = note + "\n" + goal
    return goal, skill_names
