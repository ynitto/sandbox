"""スキルの明示・遅延読み込み — カタログを LLM へ見せない前処理。

設計: docs/plans/2026-08-06-opencode-ollama-cpu-inference-proposals.md F-2 補遺 2。

方針は「**明示されたものだけを、そのときだけ読む**」。スキル一覧をシステムプロンプトへ
載せる（= モデルに選ばせる）方式は採らない——それは prefill を常時太らせる行為で、
ローカル推論では真っ先に切るべきコストだから。未使用時の追加コストは正確に 0 になる。

発動は決定的に検出できる 2 形態だけ。自然文からの推測はしない。

1. `--skill <name>`（複数可）— 設定ファイルや定期プロンプトなどプログラム経路の明示指定
2. プロンプト**先頭ブロックのスラッシュ行** `/<name> [args]` — TUI・send-keys 経路の人手指定

先頭ブロックに限る理由は誤爆の予防。本文中まで見ると、貼り付けたコードの `/usr/bin/...`
やパス片を「スキル呼び出し」と誤認する。行頭からの連続したスラッシュ行だけを見る。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# スキル名の規約（install.py が配布するディレクトリ名と同じ字種）。
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# 先頭ブロックのスラッシュ行。引数は自由文字列。
_SLASH_LINE_RE = re.compile(r"^/([a-z0-9][a-z0-9._-]*)(?:[ \t]+(.*))?$")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n?", re.S)


class SkillNotFound(Exception):
    """明示指定（`--skill`）されたスキルが見つからない。env 分類で人に直させる。"""


class SkillToolsetMismatch(Exception):
    """同梱スクリプト前提のスキルを、汎用シェルの無いツールセットで使おうとした。

    黙って続けると「スキルは読まれたのに手順が実行されない」＝成功に見える失敗になる。
    `SkillNotFound` と同じく env 分類で人に直させる（ツール開示設計 §6.1）。
    """


def skill_dirs() -> "list[Path]":
    """探索順。最初に共通ホーム `~/.agents/skills` を読む。

    `AGENT_OLLAMA_SKILLS_DIR` は `:` 区切りで追加できる（先に書いた方が勝つ）。
    """
    home = Path.home()
    dirs: "list[Path]" = [home / ".agents" / "skills"]
    raw = os.environ.get("AGENT_OLLAMA_SKILLS_DIR", "").strip()
    for part in raw.split(os.pathsep):
        if part.strip():
            dirs.append(Path(part.strip()).expanduser())
    dirs.append(home / ".claude" / "skills")
    return dirs


def find_skill(name: str) -> "Path | None":
    """`<dir>/<name>/SKILL.md` を探索順に探す。見つからなければ None。"""
    if not NAME_RE.match(name or ""):
        return None
    for base in skill_dirs():
        candidate = base / name / "SKILL.md"
        if candidate.is_file():
            return candidate
    return None


def list_skills() -> "list[tuple[str, Path]]":
    """利用可能なスキル（名前, SKILL.md）。TUI のローカルコマンド `/skills` 用。

    **LLM へは送らない**。人が思い出すための一覧で、プロンプトには載らない。
    """
    seen: "dict[str, Path]" = {}
    for base in skill_dirs():
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            skill_md = entry / "SKILL.md"
            if entry.is_dir() and skill_md.is_file() and entry.name not in seen:
                seen[entry.name] = skill_md
    return sorted(seen.items())


def strip_frontmatter(text: str) -> str:
    """YAML frontmatter（name/description/metadata）を落として本文だけにする。

    frontmatter は「どのスキルを選ぶか」のための情報で、選び終わった後には要らない
    ——載せるとその分だけ prefill が増える。
    """
    return _FRONTMATTER_RE.sub("", text or "", count=1).lstrip("\n")


def split_leading_slashes(prompt: str) -> "tuple[list[tuple[str, str]], str]":
    """先頭ブロックのスラッシュ行を取り出す → ([(name, args), …], 残りの本文)。

    空行はブロックの終わりとみなす（空行の後ろは本文）。
    """
    lines = (prompt or "").splitlines()
    calls: "list[tuple[str, str]]" = []
    index = 0
    for index, line in enumerate(lines):
        match = _SLASH_LINE_RE.match(line.strip())
        if match is None:
            break
        calls.append((match.group(1), (match.group(2) or "").strip()))
    else:
        index = len(lines)
    return calls, "\n".join(lines[index:]).lstrip("\n")


def _render(name: str, path: Path, args: str) -> "tuple[str, bool]":
    """スキル本文を組み立てて (本文, 同梱スクリプト前提か) を返す。"""
    body = strip_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    # {skill_dir} は SKILL.md 側が同梱スクリプトを指すための置換点。ツール実行モード
    # （--tools）では、この実パスを使って scripts/ をそのまま叩ける。裏を返せば
    # **汎用シェルを持たないツールセットでは動かない**ので、置換点の有無を呼び出し側へ返す。
    scripts = "{skill_dir}" in body
    body = body.replace("{skill_dir}", str(path.parent))
    head = f"# スキル: {name}"
    if args:
        head += f"（引数: {args}）"
    return f"{head}\n\n{body}", scripts


def expand(prompt: str, explicit=(), *, enabled: bool = True, warn=None) -> "tuple[str, list[dict]]":
    """スキルを解決してプロンプトへ前置きし、(新しいプロンプト, 読んだスキル情報) を返す。

    - `explicit`（`--skill`）が見つからないときは `SkillNotFound`。プログラム経路の
      明示指定は黙って無視してはいけない（指定した処理が行われないまま成功に見える）。
    - スラッシュ行が見つからないときは**警告して素通し**する。スキル名でない普通の行
      （`/tmp を消して` 等）かもしれないので、偽陽性でプロンプトを壊さない。
    """
    prompt = prompt or ""
    calls: "list[tuple[str, str]]" = [(str(name), "") for name in explicit]
    body = prompt
    if enabled:
        slash_calls, rest = split_leading_slashes(prompt)
        if slash_calls:
            resolved = [(name, args) for name, args in slash_calls if find_skill(name)]
            if len(resolved) == len(slash_calls):
                # 先頭ブロックが全部スキルとして解決できたときだけ本文から切り離す。
                # 一部だけ解決できた場合は「ただの本文」の可能性が高いので触らない。
                calls += slash_calls
                body = rest
            else:
                for name, _args in slash_calls:
                    if not find_skill(name) and warn is not None:
                        warn(f"スキルが見つかりません: /{name}（本文として送ります）")

    loaded: "list[dict]" = []
    blocks: "list[str]" = []
    seen: "set[str]" = set()
    for name, args in calls:
        path = find_skill(name)
        if path is None:
            raise SkillNotFound(
                f"スキルが見つかりません: {name}\n"
                f"  探索先: {', '.join(str(d) for d in skill_dirs())}\n"
                f"  配布は `python install.py --agent claude --all-skills` 等で行います。")
        if name in seen:
            continue
        seen.add(name)
        block, scripts = _render(name, path, args)
        blocks.append(block)
        loaded.append({"name": name, "path": str(path), "chars": len(block),
                       "scripts": scripts})

    if not blocks:
        return prompt, []
    return "\n\n".join(blocks) + "\n\n---\n\n" + body, loaded
