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

種別は 4 つ（設計 §3.2 の表）。この段で表を持つのは **A（セッション操作）だけ**で、
B（実行形）・C（用途）・D（スキル）は後段で載る。D は探索が要る（`SKILL.md` の実在）ので
表には載らず、`lookup()` が None を返したときに呼び出し側がスキルとして解決する
——いまの `ollama_skills.expand` / `toolloop._tl_resolve_skill` の分担をそのまま残す。
"""
from __future__ import annotations

import re
from typing import NamedTuple

# 名前の正典。install.py が配るスキルのディレクトリ名と同じ字種。
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# 先頭ブロックのコマンド行。引数は自由文字列。
_LINE_RE = re.compile(r"^/([a-z0-9][a-z0-9._-]*)(?:[ \t]+(.*))?$")
# 同じ規約を大小文字無視で読むための版。**プロンプト本文の切り出しには使わない**
# （スキル名は小文字が規約で、`/README.md` を呼び出しと誤認したくない）。人が打つ面
# ——TUI——だけが `/HELP` を受けるためにこちらを引く。
_LINE_RE_I = re.compile(_LINE_RE.pattern, re.I)

# 種別（設計 §3.2）。A のみこの段で表を持つ。
KIND_SESSION = "session"   # A: セッション操作（実体はコード内の関数）
KIND_SHAPE = "shape"       # B: 実行形（ハーネス / toolset の切替）
KIND_PURPOSE = "purpose"   # C: 用途（宣言 1 枚）
KIND_SKILL = "skill"       # D: スキル（SKILL.md を材料へ載せる）


class Command(NamedTuple):
    """ルート表の 1 行。`summary` / `arg_hint` は `/help` の見え方をここへ寄せるためにある。"""

    name: str
    kind: str
    summary: str = ""
    arg_hint: str = ""
    aliases: "tuple[str, ...]" = ()
    onoff: bool = False       # 引数が on|off のトグル（Tab 補完がこれを見る）
    hidden: bool = False      # `/help` の一覧には出さない（別名など）

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

_TABLE: "dict[str, Command]" = {}
for _cmd in _SESSION_COMMANDS:
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
    rows = _SESSION_COMMANDS
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


def render_help(kind: str = KIND_SESSION) -> str:
    """`/help` の本文（表の並び順・別名は出さない）。"""
    return "\n".join(f"  {cmd.spell:<17}{cmd.summary}"
                     for cmd in commands(kind) if not cmd.hidden)


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
    """用途の 1 語 → 起動形。`{"agent_cli", "model", "variant"}` を返す。

    設計 2026-08-27 §3.3。**engine は許可リストを持たない**——用途の 1 語を渡すだけで、
    振り替えるかどうかは定義側（`agents/<name>.json` の `variants`）の宣言が決める。
    以前は flow / project / audit が各々の許可リストを持ち、harness は許可リスト無しで
    直に引いていた（G2）。同じ 15 キーを 3 通りに書いていたので、「宣言したのに効かない」
    が静かに起きていた。宣言が唯一の許可リストになれば、その事故は起こりようがない。

    モデルの調停規則は agent-flow が持っていたものを正典に昇格させる（G4）:

    - `explicit_model` … 呼び出し元（人の設定・control の上書き・run 単位の固定）が
      モデルを名指ししている。**変種の既定で上書きしない。**
    - `by_purpose` … `selection_policy.by_purpose` 由来の決定である。これはその用途の
      **実測に基づく選択**なので、変種の既定で上書きしない——上書きすると、例えば judge で
      bounded-review の裏付けを持つモデルが選ばれたのに変種の既定（base の弱いモデル）へ
      黙って戻り、その用途では blocked と実測されているモデルで走る。
    - どちらでもないとき（用途を知らない共通の順位表・既定）だけ、変種の用途専用
      チューニング（`default_model`）を採る——そちらの方が良い推定だから。

    **種別 A / B の綴りは用途ではない**ので振り替えない。名前空間は 1 つで、`/model` が
    用途としても解釈される状態を作らない。
    """
    name = str(command or "").strip().lower()
    result = {"agent_cli": str(cli or "").strip().lower(), "model": model, "variant": False}
    if not name or lookup(name) is not None:
        return result
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
