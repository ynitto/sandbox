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

from agentcore import slashroute

# スキル名の規約（install.py が配布するディレクトリ名と同じ字種）。
# 綴りの正典は slashroute——スキル名とスラッシュコマンド名は同じ名前空間にある。
NAME_RE = slashroute.NAME_RE

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

    切り出しの実装は `slashroute`（ルータが argv より先に読む行と同じもの）。ここは
    既存の呼び出し元のための綴りとして残す。
    """
    return slashroute.split_leading(prompt)


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


def skill_exists(name: str) -> bool:
    """ルータへ渡すスキル解決器。探索先も言えるようにしておく（エラー文へ載る）。"""
    return find_skill(name) is not None


skill_exists.search_dirs = skill_dirs


def expand(prompt: str, explicit=(), *, enabled: bool = True, warn=None,
           plan=None, strict: bool = True) -> "tuple[str, list[dict]]":
    """スキルを解決してプロンプトへ前置きし、(新しいプロンプト, 読んだスキル情報) を返す。

    - `explicit`（`--skill`）が見つからないときは `SkillNotFound`。プログラム経路の
      明示指定は黙って無視してはいけない（指定した処理が行われないまま成功に見える）。
    - 先頭ブロックの読みは `slashroute.plan`（起動前に読む 1 実装）。既に読んである
      ランチャは `plan=` で渡す——同じ行を 2 回解釈しないため。
    - `strict=True`（既定）では、ルート表にも宣言にもスキルにも無い名前は
      `slashroute.UnknownCommand` で止まる（設計 2026-08-27 §3.2）。`strict=False` は
      従来の寛容な扱いで、警告して先頭ブロックを 1 行も消費しない。
    """
    prompt = prompt or ""
    calls: "list[tuple[str, str]]" = [
        (str(name), "") if isinstance(name, str) else (str(name[0]), str(name[1]))
        for name in explicit]
    body = prompt
    if enabled:
        if plan is None:
            plan = slashroute.plan(prompt, skill_exists=skill_exists, strict=strict,
                                   warn=warn)
        calls += list(plan.skills)
        body = plan.body

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
        # **`prompt` ではなく `body` を返す。** ルータが消費したコマンド行（`/find` など）は
        # スキルが 1 枚も載らなくても本文から消えている。ここで元へ戻すと、実行形を決めた
        # あとの行がそのままモデルへ流れる。
        return body, []
    return "\n\n".join(blocks) + "\n\n---\n\n" + body, loaded
