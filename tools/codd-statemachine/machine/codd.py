#!/usr/bin/env python3
"""codd-statemachine — 参照先（実装⇔設計書。いくつでも）を読んで、自分の変更を練るステートマシンの下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（参照先の前提・制約・その他、ずれ、変更案、影響範囲）はアクションの側でモデルが行う。

    show [--phase P]    この側・参照先の一覧と、守る決まりのファイル、使うスキルと道具（計画を練るとき / 変えるとき）を示す
    rules [--write]     守る決まりのファイルと、決まりらしいマークダウンの候補を示す。--write で候補を codd.json に書く
    explore --term 語   参照先を探す（graphify のグラフを必要なら作り直してから引く）。--ref で絞れる
    impact  --term 語   自分のリポジトリで影響を受ける箇所を探す（同上）
    verify-plan         計画（.codd/plan.md）が決まった形か、根拠が参照先に実在するか（パス・行・見出し・
                        `…` で囲んだ名前）、1 回で扱う範囲（max_files）に収まるかを検査する。
                        参照先の変更案があれば、それを自分に適用したときの影響範囲を測り、計画の影響範囲が
                        測ったファイルをすべて挙げているかも検査する。通ったら、変える前の印を控える
    verify-apply        計画どおりに変えたか（変えてよいのは計画に挙げたファイルだけ。参照先も同じ）と、
                        検査コマンドを確かめる。参照先を変えたら、実際の変更から影響範囲を測り直し、
                        測ったファイルを直したか「変更不要」としたかを検査する
    report              計画のファイルごとに変えたか、測った影響範囲、今回やらないことをまとめる（終わりの報告）

置き場所は `<リポジトリ>/.statemachine/codd/`。設定は同じフォルダの codd.json、
作業ファイルと graphify のグラフは `<リポジトリ>/.codd/` に置く。依存は python3 と git のみ
（graphify は任意）。

参照先が複数あるとき、計画の根拠は `名前:パス`（codd.json の refs の name）で書く。
参照先が 1 つなら、パスだけでよい。

実装と設計書が同じリポジトリにあるときは、参照先の path を "." にし、自分と参照先の scope
（受け持つフォルダ）を書く。探す・変わったかを測る・根拠を認めるのは、それぞれの scope の中だけになる。

graphify のグラフは、リポジトリの HEAD と作業中の変更から作る「印」を控えておき、
explore / impact のたびに印が変わっていれば `graphify update` で作り直す（自動更新）。
グラフは参照先の中ではなく自分の `.codd/graph/` に書く（探すだけで参照先に何も書かない）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MACHINE_DIR = Path(__file__).resolve().parent
MACHINE_REL = ".statemachine/codd"
CONFIG_NAME = "codd.json"
DATA_DIRNAME = ".codd"
SIDES = {"impl": "実装", "design": "設計書"}
OTHER_SIDE = {"impl": "design", "design": "impl"}
PHASES = {"plan": "計画を練るとき", "apply": "変えるとき"}
CONFIG_KEYS = {"side", "refs", "ref_path", "skills", "tools", "rules", "graphify", "check", "scope", "max_files"}
REF_KEYS = {"name", "path", "skills", "scope", "rules"}
DEFAULT_MAX_FILES = 20

# どのプロジェクトでも、あれば読む決まりのファイル（エージェント向けの約束・貢献の手引き）。codd.json の rules で足せる。
RULE_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md", "CONTRIBUTING.md")

PLAN_HEADINGS = (
    "## やりたいこと",
    "## 守る決まり",
    "## 使ったスキルと道具",
    "## 参照先の前提",
    "## 参照先の制約",
    "## 参照先のその他",
    "## ずれ",
    "## 自分の変更案",
    "## 参照先の変更案",
    "## 影響範囲",
    "## 今回やらないこと",
)
CITED_IN_REFS = ("## 参照先の前提", "## 参照先の制約", "## 参照先のその他", "## ずれ")
# 根拠のファイルに `…` の名前が書かれているかまで確かめる見出し（ずれは自分の側の名前も書くので除く）。
ANCHORED_IN_REFS = ("## 参照先の前提", "## 参照先の制約", "## 参照先のその他")

MAX_TERMS = 12
GREP_LINES_PER_TERM = 20
GIT_TIMEOUT = 60
GRAPHIFY_TIMEOUT = 120
GRAPHIFY_UPDATE_TIMEOUT = 900
GRAPHIFY_BUDGET = 600
CHECK_TIMEOUT = 900

# graphify の出力からファイルを拾う。query は `[src=docs/api.md loc=L3]`、affected は `src/use.py:L4`。
_GRAPHIFY_SRC = re.compile(r"\bsrc=([^\s\]]+)|(?<![\w=])([^\s\[\]=]+):L\d+")
# 根拠のパス。`名前:パス` の名前は参照先の name（複数あるとき）。末尾に `:行`・`:行-行`・`#見出し` を付けられる。
_CITE = re.compile(r"(?:(?P<ref>[A-Za-z0-9_.-]+):)?"
                   r"(?P<path>[\w@.\-]+(?:/[\w@.\-]+)+|[\w@\-]+\.[A-Za-z0-9]{1,8})"
                   r"(?::(?P<line>\d+)(?:-(?P<end>\d+))?)?(?:#(?P<anchor>[^\s)）、,。`]+))?")
# まだ無いファイルとして認める名前（拡張子が英字で始まる。`v1.2` のような語を拾わない）。
_NEW_FILE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_SKILL = re.compile(r"^[A-Za-z0-9_.:-]+$")
_NONE_WORDS = ("なし", "無し")
NO_CHANGE_MARK = "変更不要"
MAX_MEASURED = 40

# 影響範囲を測る語。計画からは `…` で囲んだ名前、参照先の実際の差分からは定義・見出し・`…` を拾う。
_BACKTICK = re.compile(r"`([^`\n]{2,60})`")
_DIFF_TERMS = (
    re.compile(r"^[+-]\s*(?:async\s+)?def\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]{2,})"),
    re.compile(r"^[+-]\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]{2,})\s*="),
    re.compile(r"^[+-]\s*(?:pub\s+)?(?:fn|func|interface|type|struct|enum)\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^[+-]\s*#{1,6}\s+(.{2,60}?)\s*#*\s*$"),
)
_WORDLIKE = re.compile(r"^[A-Za-z0-9_$]+$")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


class CoddError(Exception):
    """利用者が直せる設定・状態の誤り（終了コード 2）。"""


# ---------------------------------------------------------------- 下回り

def run(argv: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"({timeout} 秒で終わらなかったので打ち切りました)"
    except OSError as exc:
        return 127, f"(実行できませんでした: {exc})"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def skill_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if not (isinstance(value, list) and all(isinstance(s, str) and _SKILL.match(s) for s in value)):
        raise CoddError(f"{where} はスキル名の配列です（例: [\"tdd\", \"doc-writer\"]）")
    return list(value)


def scope_list(value, where: str) -> list[str]:
    """受け持つフォルダの一覧（リポジトリのルートからの相対）。空ならリポジトリ全体。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not (isinstance(value, list) and all(isinstance(s, str) and s.strip() for s in value)):
        raise CoddError(f'{where} はフォルダの配列です（例: ["src", "tests"]）')
    out = []
    for s in value:
        s = s.strip().replace("\\", "/")
        s = s[2:] if s.startswith("./") else s
        s = s.rstrip("/")
        if not s or s == "." or s.startswith("/") or ".." in s.split("/"):
            raise CoddError(f"{where} にはリポジトリの中のフォルダを相対で書きます（今: {s!r}）")
        out.append(s)
    return out


def rule_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if not (isinstance(value, list) and all(isinstance(r, str) and r.strip() for r in value)):
        raise CoddError(f'{where} は決まりのファイルの配列です（例: ["docs/coding-rules.md"]）')
    out = unique_paths(r.strip().replace("\\", "/") for r in value)
    bad = [r for r in out if r.startswith("/") or ".." in r.split("/")]
    if bad:
        raise CoddError(f"{where} にはリポジトリの中のパスか glob を相対で書きます（今: {', '.join(bad)}）")
    return out


def load_config(machine_dir: Path) -> dict:
    path = machine_dir / CONFIG_NAME
    if not path.is_file():
        raise CoddError(f"設定がありません: {path}\n"
                        '  例: {"side": "impl", "refs": [{"name": "docs", "path": "../my-docs"}]}')
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoddError(f"{path} が JSON として読めません: {exc}") from exc
    if not isinstance(config, dict):
        raise CoddError(f"{path} は JSON のオブジェクトです")
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        raise CoddError(f"{path} に知らない項目があります: {', '.join(unknown)}"
                        f"（書けるのは {', '.join(sorted(CONFIG_KEYS - {'ref_path'}))}）")
    if config.get("side") not in SIDES:
        raise CoddError(f"{path} の side は impl か design です（今: {config.get('side')!r}）")
    if "refs" not in config and config.get("ref_path"):  # 参照先が 1 つだった頃の書き方
        config["refs"] = [{"path": config["ref_path"]}]
    refs = config.get("refs")
    if not (isinstance(refs, list) and refs):
        raise CoddError(f"{path} の refs が空です（参照先のリポジトリを 1 つ以上書いてください）")
    names = set()
    for i, ref in enumerate(refs):
        if not (isinstance(ref, dict) and isinstance(ref.get("path"), str) and ref["path"]):
            raise CoddError(f"{path} の refs[{i}] には path が要ります")
        unknown = sorted(set(ref) - REF_KEYS)
        if unknown:
            raise CoddError(f"{path} の refs[{i}] に知らない項目があります: {', '.join(unknown)}"
                            f"（書けるのは {', '.join(sorted(REF_KEYS))}）")
        default_name = Path(ref["path"].rstrip("/\\")).name
        ref.setdefault("name", default_name if _NAME.match(default_name or "-") and default_name != "." else "")
        if not _NAME.match(ref["name"]) or ref["name"] in names:
            raise CoddError(f"{path} の refs[{i}] の name が不正か重複しています: {ref['name']!r}"
                            "（英数字と _ . - で、参照先ごとに違う名前）")
        names.add(ref["name"])
        ref["skills"] = skill_list(ref.get("skills"), f"{path} の refs[{i}].skills")
        ref["scope"] = scope_list(ref.get("scope"), f"{path} の refs[{i}].scope")
        ref["rules"] = rule_list(ref.get("rules"), f"{path} の refs[{i}].rules")
    skills = config.get("skills") or {}
    if not isinstance(skills, dict) or set(skills) - set(PHASES):
        raise CoddError(f"{path} の skills は {{\"plan\": [...], \"apply\": [...]}} の形です")
    config["skills"] = {phase: skill_list(skills.get(phase), f"{path} の skills.{phase}") for phase in PHASES}
    tools = config.get("tools") or {}
    if not isinstance(tools, dict) or set(tools) - set(PHASES):
        raise CoddError(f"{path} の tools は {{\"plan\": [...], \"apply\": [...]}} の形です（MCP やコマンドの名前）")
    config["tools"] = {phase: skill_list(tools.get(phase), f"{path} の tools.{phase}") for phase in PHASES}
    config["rules"] = rule_list(config.get("rules"), f"{path} の rules")
    config["scope"] = scope_list(config.get("scope"), f"{path} の scope")
    config.setdefault("graphify", "auto")
    if config["graphify"] not in ("auto", "off"):
        raise CoddError(f"{path} の graphify は auto か off です（今: {config['graphify']!r}）")
    check = config.get("check")
    if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) for a in check)):
        raise CoddError(f'{path} の check はコマンドの配列です（例: ["python3", "-m", "pytest", "-q"]）')
    config.setdefault("max_files", DEFAULT_MAX_FILES)
    if not (isinstance(config["max_files"], int) and not isinstance(config["max_files"], bool)
            and config["max_files"] > 0):
        raise CoddError(f"{path} の max_files は 1 以上の整数です（今: {config['max_files']!r}）")
    return config


def in_scope(rel: str, scope: list[str]) -> bool:
    return not scope or any(rel == s or rel.startswith(s + "/") for s in scope)


def covered(rel: str, listed: set[str]) -> bool:
    """rel が計画に挙げたパス（ファイルか、そのファイルを含むフォルダ）に入っているか。"""
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in listed)


def overlaps(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return True
    return any(x == y or x.startswith(y + "/") or y.startswith(x + "/") for x in a for y in b)


@dataclass
class Side:
    """探す・変わったかを測る単位。リポジトリと、その中で受け持つフォルダ（scope。空なら全体）。"""
    name: str
    path: Path
    scope: list[str]

    def pathspec(self) -> list[str]:
        return ["--", *(self.scope or ["."]), f":(exclude){DATA_DIRNAME}", f":(exclude){MACHINE_REL}"]

    def has(self, rel: str) -> bool:
        return in_scope(rel, self.scope)


@dataclass
class Ref(Side):
    config: dict | None = None     # 参照先に置いた同じマシンの設定（あれば）
    label: str = "参照先"
    entry_skills: list[str] = field(default_factory=list)
    entry_rules: list[str] = field(default_factory=list)   # codd.json の refs[].rules

    @property
    def apply_skills(self) -> list[str]:
        """この参照先を変えるときのスキル。codd.json の refs の skills、無ければ参照先自身の skills.apply。"""
        return self.entry_skills or (self.config or {}).get("skills", {}).get("apply", [])

    @property
    def apply_tools(self) -> list[str]:
        return (self.config or {}).get("tools", {}).get("apply", [])

    @property
    def check(self) -> list[str] | None:
        return (self.config or {}).get("check")


class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_config(MACHINE_DIR)
        self.side = self.config["side"]
        self.own = Side("own", root, self.config["scope"])
        self.data = root / DATA_DIRNAME
        self.plan = self.data / "plan.md"
        self.max_files = self.config["max_files"]
        self.refs: list[Ref] = []
        for entry in self.config["refs"]:
            path = Path(os.path.expanduser(entry["path"]))
            path = (path if path.is_absolute() else root / path).resolve()
            if not path.is_dir() or run(["git", "rev-parse", "--show-toplevel"], path, GIT_TIMEOUT)[0]:
                raise CoddError(f"参照先 {entry['name']} の git リポジトリが見つかりません: {path}\n"
                                f"  {MACHINE_DIR / CONFIG_NAME} の refs を直してください")
            ref_config = None
            label = SIDES[OTHER_SIDE[self.side]] if path == root.resolve() else "参照先"
            ref_machine = path / MACHINE_REL
            if path != root.resolve() and (ref_machine / CONFIG_NAME).is_file():
                try:
                    ref_config = load_config(ref_machine)
                    label = SIDES[ref_config["side"]]
                except CoddError:
                    ref_config = None  # 参照先側の設定の誤りは、参照先で直す。ここでは読むだけ
            self.refs.append(Ref(entry["name"], path, entry["scope"], ref_config, label, entry["skills"],
                                 entry["rules"]))
        self.check_layout()

    def check_layout(self) -> None:
        """同じリポジトリを 2 つ以上の側が使うなら、どの側も scope を持ち、互いに重ならないこと。"""
        sides: list[Side] = [self.own, *self.refs]
        for i, a in enumerate(sides):
            for b in sides[i + 1:]:
                if a.path.resolve() != b.path.resolve():
                    continue
                if not a.scope or not b.scope:
                    raise CoddError(
                        f"{a.name} と {b.name} が同じリポジトリです。{MACHINE_DIR / CONFIG_NAME} に"
                        " scope（自分）と refs[].scope（参照先）で、それぞれが受け持つフォルダを書いてください"
                        '（例: "scope": ["src"], "refs": [{"name": "docs", "path": ".", "scope": ["docs"]}]）')
                if overlaps(a.scope, b.scope):
                    raise CoddError(f"{a.name} と {b.name} の scope が重なっています: {a.scope} / {b.scope}")

    def rule_files(self) -> list[tuple[str, str]]:
        """守る決まりのファイル（（参照先の名前か ""、パス））。自分の分と、別のリポジトリの参照先の分。"""
        out: list[tuple[str, str]] = []
        for rel in expand_rules(self.root, [*RULE_FILES, *self.config["rules"]]):
            out.append(("", rel))
        own = {rel for _, rel in out}
        for r in self.refs:
            same = r.path == self.root  # 同じリポジトリのよくある名前の決まりは、自分の分で読む
            extra = [*r.entry_rules, *(r.config or {}).get("rules", [])]
            for rel in expand_rules(r.path, [*([] if same else RULE_FILES), *extra]):
                if not (same and rel in own):
                    out.append((r.name, rel))
        return out

    def unmatched_rules(self) -> list[str]:
        """設定に書いたのに、1 つのファイルにも当たらない決まり（綴り違い・移動に気付けるように）。"""
        out = [p for p in self.config["rules"] if not expand_rules(self.root, [p])]
        for r in self.refs:
            out += [f"{r.name}:{p}" for p in r.entry_rules if not expand_rules(r.path, [p])]
        return out

    def rule_candidates(self) -> list[tuple[str, str]]:
        """決まりらしいのに、まだ設定に無いマークダウン（（参照先の名前か ""、パス））。"""
        known = set(self.rule_files())
        out = []
        # 自分はリポジトリ全体から探す（決まりは scope の外、ルートにあることが多い）。同じリポジトリの参照先の分は除く。
        same = [r for r in self.refs if r.path == self.root]
        for name, side in [("", Side("own", self.root, [])), *[(r.name, r) for r in self.refs]]:
            for rel in discover_rules(side):
                if not name and any(r.has(rel) and r.scope for r in same):
                    continue
                key = (name, rel)
                if key not in known and ("", rel) not in known and key not in out:
                    out.append(key)
        return out[:MAX_RULE_CANDIDATES]

    def ref(self, name: str) -> Ref:
        for r in self.refs:
            if r.name == name:
                return r
        raise CoddError(f"参照先 {name!r} はありません（{', '.join(r.name for r in self.refs)}）")

    def graph_key(self, repo: Path) -> str:
        # 同じリポジトリなら同じグラフを使う（scope で絞るのは引いたあと）。
        if repo == self.root:
            return "own"
        return "ref-" + next(r.name for r in self.refs if r.path == repo)


MAX_RULE_CANDIDATES = 20
# 決まりらしいマークダウンの目印（パスの語か、最初の見出し）。
_RULE_WORDS = re.compile(
    r"(?:^|[^a-z])(rules?|guidelines?|conventions?|coding|style-?guide|standards?|policy|policies|contributing)"
    r"(?:[^a-z]|$)|規約|ルール|規則|約束|作法|規程|ガイドライン|コーディング")
_NOT_RULES = re.compile(r"(?:^|/)(changelog|history|license)[^/]*$", re.IGNORECASE)


_GLOB_CHARS = re.compile(r"[*?\[]")


def expand_rules(repo: Path, patterns: list[str]) -> list[str]:
    """決まりのパスを実在するファイルに開く。`*`・`?`・`[...]`・`**` を含むものは glob として
    （git の :(glob) と同じ意味。`*` はフォルダをまたがず、`**/` はまたぐ）、追跡中と未追跡のファイルから引く。"""
    out: list[str] = []
    for pat in unique_paths(patterns):
        if _GLOB_CHARS.search(pat):
            # 除外のパス指定を並べると :(glob) が効かなくなる git があるので、作業フォルダとマシンは後から除く。
            rc, found = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", f":(glob){pat}"],
                            repo, GIT_TIMEOUT)
            hits = sorted(ln for ln in found.splitlines() if (repo / ln).is_file()
                          and not in_scope(ln, [DATA_DIRNAME, MACHINE_REL])) if rc == 0 else []
        else:
            hits = [pat] if (repo / pat).is_file() else []
        out += [h for h in hits if h not in out]
    return out


def discover_rules(side: Side) -> list[str]:
    """scope の中のマークダウンのうち、パスか最初の見出しが決まりらしいもの。"""
    rc, out = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", *side.pathspec()],
                  side.path, GIT_TIMEOUT)
    found = []
    for rel in out.splitlines() if rc == 0 else []:
        if not rel.lower().endswith((".md", ".markdown")) or _NOT_RULES.search(rel) or not side.has(rel):
            continue
        hit = _RULE_WORDS.search(rel.lower())
        if not hit:
            try:
                with open(side.path / rel, encoding="utf-8", errors="replace") as f:
                    head_text = f.read(4000)
            except OSError:
                continue
            m = _HEADING.search(head_text)
            hit = m and _RULE_WORDS.search(m.group(1).lower())
        if hit:
            found.append(rel)
    return found


def unique_paths(paths) -> list[str]:
    out: list[str] = []
    for p in paths:
        p = p[2:] if p.startswith("./") else p
        if p not in out:
            out.append(p)
    return out


def repo_root(start: Path) -> Path:
    rc, out = run(["git", "rev-parse", "--show-toplevel"], start, GIT_TIMEOUT)
    if rc != 0:
        raise CoddError(f"git リポジトリではありません: {start}")
    return Path(out.strip()).resolve()


def stamp(repo: Path) -> str:
    """リポジトリの今の中身を表す印（HEAD と、作業中の変更・未追跡のファイルの中身）。graphify の作り直しに使う。"""
    h = hashlib.sha256(f"{head(repo)}\n".encode())
    h.update(run(["git", "diff", "HEAD", "--", ".", f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1].encode())
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "--", ".",
                     f":(exclude){DATA_DIRNAME}"], repo, GIT_TIMEOUT)[1].splitlines()
    for name in sorted(untracked):
        h.update(name.encode())
        try:
            h.update((repo / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def head(repo: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], repo, GIT_TIMEOUT)[1].strip()


def dirty_files(side: Side) -> dict[str, str]:
    """scope の中の作業中の変更・未追跡のファイル → 中身のハッシュ（消えていれば "deleted"）。"""
    out = run(["git", "status", "--porcelain", "--untracked-files=all", *side.pathspec()],
              side.path, GIT_TIMEOUT)[1]
    files = {}
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        try:
            files[path] = hashlib.sha256((side.path / path).read_bytes()).hexdigest()
        except OSError:
            files[path] = "deleted"
    return files


def snapshot(side: Side) -> dict:
    return {"head": head(side.path), "files": dirty_files(side)}


def changed_since(side: Side, before: dict) -> set[str]:
    """控えたときから中身が変わった、scope の中のファイル。"""
    before_files = before.get("files", {})
    now = dirty_files(side)
    changed = {p for p in set(now) | set(before_files) if now.get(p) != before_files.get(p)}
    before_head = before.get("head", "")
    if before_head and head(side.path) != before_head:  # 途中でコミットされても取りこぼさない
        rc, out = run(["git", "diff", "--name-only", before_head, "HEAD", *side.pathspec()], side.path, GIT_TIMEOUT)
        if rc == 0:
            changed |= set(out.splitlines())
    return {p for p in changed if side.has(p)}


# ---------------------------------------------------------------- 設定を示す

def skill_words(names: list[str]) -> str:
    # `名前` スキル の形で書く。statemachine-use の実行ハーネスはこの表記でスキルを読み込む。
    return "、".join(f"`{n}` スキル" for n in names) if names else "なし"


def tool_words(names: list[str]) -> str:
    return "、".join(f"`{n}`" for n in names) if names else "なし"


def scope_words(side: Side) -> str:
    return f"（受け持つフォルダ: {', '.join(side.scope)}）" if side.scope else ""


def cmd_show(ctx: Ctx, args: argparse.Namespace) -> int:
    print(f"この側: {SIDES[ctx.side]}（{ctx.side}）  {ctx.root}{scope_words(ctx.own)}")
    print("参照先:")
    for r in ctx.refs:
        print(f"  - {r.name}: {r.label}  {r.path}{scope_words(r)}")
    rules = ctx.rule_files()
    print("守る決まり（読んで、計画の「守る決まり」に挙げる）:")
    for name, rel in rules or [("", "")]:
        print(f"  - {name + ':' if name else ''}{rel}" if rel else "  - なし")
    for pat in ctx.unmatched_rules():
        print(f"  ! {pat} に当たるファイルがありません（{CONFIG_NAME} の rules を確かめてください）")
    candidates = ctx.rule_candidates()
    if candidates:
        print("決まりの候補（設定に無い。決まりなら `codd.py rules --write` で設定に書く）:")
        for name, rel in candidates:
            print(f"  - {name + ':' if name else ''}{rel}")
    phases = [args.phase] if args.phase else list(PHASES)
    for phase in phases:
        print(f"使うスキルと道具（{PHASES[phase]}）:")
        print(f"  - 自分: {skill_words(ctx.config['skills'][phase])}"
              + (f"。道具: {tool_words(ctx.config['tools'][phase])}" if ctx.config['tools'][phase] else ""))
        if phase == "apply":
            for r in ctx.refs:
                print(f"  - {r.name} を変えるとき: {skill_words(r.apply_skills)}"
                      + (f"。道具: {tool_words(r.apply_tools)}" if r.apply_tools else ""))
    print(f"1 回で変えるファイルの上限: {ctx.max_files}（超えるぶんは計画の「今回やらないこと」へ）")
    if len(ctx.refs) > 1:
        print("計画の根拠は `名前:パス` で書く（例: " + f"{ctx.refs[0].name}:docs/api.md）")
    return 0


def cmd_rules(ctx: Ctx, args: argparse.Namespace) -> int:
    """決まりのファイル（設定済み・よくある名前）と候補を示す。--write で候補を codd.json に書く。"""
    print("守る決まり:")
    for name, rel in ctx.rule_files() or [("", "")]:
        print(f"  - {name + ':' if name else ''}{rel}" if rel else "  - なし")
    for pat in ctx.unmatched_rules():
        print(f"  ! {pat} に当たるファイルがありません（{CONFIG_NAME} の rules を確かめてください）")
    candidates = ctx.rule_candidates()
    if args.only:
        candidates = [c for c in candidates if (f"{c[0]}:{c[1]}" if c[0] else c[1]) in args.only]
    print("候補:" if candidates else "候補: なし")
    for name, rel in candidates:
        print(f"  - {name + ':' if name else ''}{rel}")
    if not (args.write and candidates):
        return 0
    path = MACHINE_DIR / CONFIG_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    for name, rel in candidates:
        if not name:
            raw.setdefault("rules", [])
            target = raw["rules"]
        else:
            entry = next(e for e in raw.get("refs", [])
                         if e.get("name", Path(str(e["path"]).rstrip("/\\")).name) == name)
            entry.setdefault("name", name)
            target = entry.setdefault("rules", [])
        if rel not in target:
            target.append(rel)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(candidates)} 件を {CONFIG_NAME} に書きました（決まりでないものは手で消してください）")
    return 0


# ---------------------------------------------------------------- 探す（graphify + git grep）

def ensure_graph(ctx: Ctx, repo: Path) -> tuple[str | None, Path | None, str]:
    """graphify のグラフを用意する。印が変わっていれば作り直す。戻り値は（実行ファイル, グラフ, 状態）。"""
    if ctx.config["graphify"] == "off":
        return None, None, "off"
    exe = shutil.which("graphify")
    if not exe:
        return None, None, "not-installed"
    out_dir = ctx.data / "graph" / ctx.graph_key(repo)
    graph = out_dir / "graph.json"
    stamp_file = out_dir / "stamp"
    now = stamp(repo)
    if graph.is_file() and stamp_file.is_file() and stamp_file.read_text(encoding="utf-8") == now:
        return exe, graph, "fresh"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GRAPHIFY_OUT": str(out_dir)}
    # --force: 削除や改名でノードが減っても作り直した方を採る（古いノードを残さない）。
    rc, out = run([exe, "update", ".", "--force"], repo, GRAPHIFY_UPDATE_TIMEOUT, env)
    if rc != 0 or not graph.is_file():
        print(f"  graphify update に失敗しました（{rc}）: {out.strip()[:200]}", file=sys.stderr)
        return None, None, "update-failed"
    stamp_file.write_text(now, encoding="utf-8")
    return exe, graph, "updated"


def search(ctx: Ctx, side: Side, terms: list[str], graph_cmd: str,
           use_graph: bool = True) -> tuple[str, list[str], str]:
    """語ごとに graphify と git grep で引き、（本文, 候補のファイル, graphify の状態）を返す。scope の外は捨てる。"""
    repo = side.path
    exe, graph, note = ensure_graph(ctx, repo) if use_graph else (None, None, "unused")
    lines: list[str] = []
    files: list[str] = []

    def add_file(path: str) -> None:
        if path and path not in files and side.has(path) and (repo / path).is_file():
            files.append(path)

    if exe and graph:
        lines += [f"### graphify {graph_cmd}", ""]
        for term in terms:
            argv = [exe, graph_cmd, term, "--graph", str(graph)]
            if graph_cmd == "query":
                argv += ["--budget", str(GRAPHIFY_BUDGET)]
            _, out = run(argv, repo, GRAPHIFY_TIMEOUT)
            out = "\n".join(ln for ln in out.splitlines() if not ln.startswith("[graphify] note"))
            lines += [f"#### {term}", "", "```", out.strip() or "(該当なし)", "```", ""]
            for m in _GRAPHIFY_SRC.finditer(out):
                add_file(m.group(1) or m.group(2))

    lines += ["### 文字列の一致（git grep）", ""]
    for term in terms:
        # --untracked: まだコミットしていない新しいファイルも拾う（.gitignore に載っているものは除く）。
        # 識別子は語単位（-w）で引く。`hello` で `helloWorld` を拾って影響範囲を水増ししない。
        word = ["-w"] if _WORDLIKE.match(term) else []
        rc, out = run(["git", "grep", "--untracked", "-n", "-I", "-i", "-F", *word, "--max-count", "3", "-e", term,
                       *side.pathspec()], repo, GIT_TIMEOUT)
        hits = out.splitlines()[:GREP_LINES_PER_TERM] if rc == 0 else []
        lines += [f"#### {term}", "", *([f"- {h[:200]}" for h in hits] or ["- (該当なし)"]), ""]
        for h in hits:
            add_file(h.split(":", 1)[0])
    return "\n".join(lines), files, note


def terms_of(args: argparse.Namespace) -> list[str]:
    terms = unique(args.term or [])
    if not terms:
        raise CoddError("検索語を --term で渡してください")
    return terms[:MAX_TERMS]


def write_report(ctx: Ctx, name: str, title: str, terms: list[str], parts: list[tuple[str, str, str]],
                 files: list[str]) -> Path:
    """parts は（見出し, graphify の状態, 本文）の列。"""
    ctx.data.mkdir(parents=True, exist_ok=True)
    path = ctx.data / name
    lines = [f"# {title}", "", f"- 検索語: {', '.join(terms)}", ""]
    for heading, note, body in parts:
        lines += [f"## {heading}", "", f"- graphify: {note}", "", body]
    lines += ["## 候補のファイル", "", *([f"- {p}" for p in files] or ["- (見つからない)"]), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def cmd_explore(ctx: Ctx, args: argparse.Namespace) -> int:
    terms = terms_of(args)
    targets = [ctx.ref(n) for n in args.ref] if args.ref else ctx.refs
    many = len(ctx.refs) > 1
    parts, files, notes = [], [], []
    for r in targets:
        body, found, note = search(ctx, r, terms, "query")
        parts.append((f"{r.name}（{r.label}）  {r.path}{scope_words(r)}", note, body))
        files += [f"{r.name}:{p}" if many else p for p in found]
        notes.append(f"{r.name}={note}" if many else note)
    path = write_report(ctx, "explore.md", "参照先で関係する箇所", terms, parts, files)
    print(f"FOUND {len(files)} files (graphify: {', '.join(notes)})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


def cmd_impact(ctx: Ctx, args: argparse.Namespace) -> int:
    terms = terms_of(args)
    body, files, note = search(ctx, ctx.own, terms, "affected")
    path = write_report(ctx, "impact.md", f"自分のリポジトリ（{SIDES[ctx.side]}）で影響を受ける箇所",
                        terms, [(f"{ctx.root}{scope_words(ctx.own)}", note, body)], files)
    print(f"FOUND {len(files)} files (graphify: {note})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


# ---------------------------------------------------------------- 根拠のパス

def slug(text: str) -> str:
    """見出しのアンカー（GitHub と同じく、小文字にして記号を落とし、空白を - にする）。"""
    text = re.sub(r"[`*_~\[\]()（）]", "", text.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def evidence_problem(repo: Path, rel: str, m: re.Match) -> str | None:
    """根拠の `:行` と `#見出し` が、そのファイルで本当に指せるか。指せなければ理由を返す。"""
    line, end, anchor = m.group("line"), m.group("end"), m.group("anchor")
    if not (line or anchor):
        return None
    target = repo / rel
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if line:
        count = len(text.splitlines())
        last = int(end or line)
        if int(line) < 1 or last < int(line) or last > count:
            return f"{rel} の {line}{'-' + end if end else ''} 行目はありません（{count} 行）"
    if anchor and rel.lower().endswith((".md", ".markdown")):
        want = slug(anchor.replace("-", " "))
        heads = [slug(h) for h in _HEADING.findall(text)]
        if want not in heads and not any(want and want in h for h in heads):
            return f"{rel} に見出し #{anchor} がありません"
    return None


@dataclass
class Cited:
    found: set[tuple[str, str]] = field(default_factory=set)   # （参照先の名前, パス）
    ambiguous: list[str] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)                # 行・見出しが指せない根拠


def cited_refs(ctx: Ctx, line: str, allow_new: bool = False) -> Cited:
    """行の中の参照先のパスを拾う。

    `名前:パス` はその参照先で、パスだけなら実在する参照先で探す（複数で見つかれば曖昧）。
    参照先の scope の外のパスは認めない。
    allow_new は、まだ無いファイル（親のフォルダはある）も認める（参照先の変更案で新しく書くとき）。
    """
    def exists(r: Ref, rel: str) -> bool:
        if not r.has(rel):
            return False
        target = r.path / rel
        return target.exists() or (allow_new and bool(_NEW_FILE.search(rel)) and target.parent.is_dir())

    out = Cited()
    names = {r.name for r in ctx.refs}
    for m in _CITE.finditer(line):
        rel = m.group("path")
        rel = rel[2:] if rel.startswith("./") else rel
        if m.group("ref") in names:
            hits = [ctx.ref(m.group("ref"))] if exists(ctx.ref(m.group("ref")), rel) else []
        else:
            hits = [r for r in ctx.refs if exists(r, rel)]
            if len(hits) > 1:
                out.ambiguous.append(rel)
                continue
        if hits:
            out.found.add((hits[0].name, rel))
            problem = evidence_problem(hits[0].path, rel, m)
            if problem:
                out.bad.append(problem)
    return out


def cited_own(ctx: Ctx, line: str, allow_new: bool = False) -> list[str]:
    paths = []
    for m in _CITE.finditer(line):
        rel = m.group("path")
        rel = rel[2:] if rel.startswith("./") else rel
        if m.group("ref") and m.group("ref") in {r.name for r in ctx.refs}:
            continue  # `名前:パス` は参照先のパス
        target = ctx.root / rel
        if not ctx.own.has(rel):
            continue
        if target.exists() or (allow_new and _NEW_FILE.search(rel) and target.parent.is_dir()):
            paths.append(rel)
    return paths


def unanchored_names(ctx: Ctx, item: str, cited: Cited) -> list[str]:
    """項目で `…` に囲んだ名前のうち、根拠に挙げたどのファイルにも書かれていないもの。"""
    texts = []
    for name, rel in cited.found:
        try:
            texts.append((ctx.ref(name).path / rel).read_text(encoding="utf-8", errors="replace").lower())
        except OSError:
            pass
    if not texts:
        return []
    cited_paths = {rel for _, rel in cited.found}
    missing = []
    for term in _BACKTICK.findall(item):
        bare = term.split(":", 1)[-1].split("#", 1)[0]
        if "/" in term or bare in cited_paths or _CITE.fullmatch(term):
            continue  # パスは名前ではない
        core = re.sub(r"\(\)$", "", term).strip()  # `hello()` は hello として探す
        if not any(core.lower() in t for t in texts):
            missing.append(term)
    return missing


# ---------------------------------------------------------------- 影響範囲を測る

def unique(terms) -> list[str]:
    out: list[str] = []
    for t in terms:
        t = t.strip()
        if 2 <= len(t) <= 60 and t not in out:
            out.append(t)
    return out


def name_terms(text: str) -> list[str]:
    """`…` で囲んだもののうち、名前（パスではないもの）。末尾の `()` は落とす。"""
    out = []
    for term in _BACKTICK.findall(text):
        if "/" in term or "\\" in term or (_CITE.fullmatch(term) and _NEW_FILE.search(term)):
            continue
        out.append(re.sub(r"\(\)$", "", term.strip()))
    return unique(out)


def terms_from_plan(bodies: dict[str, str]) -> list[str]:
    """参照先の変更で動く名前（参照先の変更案とずれで `…` に囲んだもの）。"""
    return name_terms(bodies.get("## 参照先の変更案", "") + "\n" + bodies.get("## ずれ", ""))


def own_terms_from_plan(bodies: dict[str, str]) -> list[str]:
    """自分の変更で動く名前（自分の変更案で `…` に囲んだもの）。"""
    body = bodies.get("## 自分の変更案", "")
    return [] if is_none(body) else name_terms(body)


def terms_from_diff(side: Side) -> list[str]:
    """実際の変更（作業中の差分と、新しいファイル）から、変わった名前を拾う。"""
    repo = side.path
    diff = run(["git", "diff", "HEAD", *side.pathspec()], repo, GIT_TIMEOUT)[1]
    terms = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        for pat in _DIFF_TERMS:
            m = pat.match(line)
            if m:
                terms.append(m.group(1))
        terms += _BACKTICK.findall(line)
    for name in run(["git", "ls-files", "--others", "--exclude-standard", *side.pathspec()],
                    repo, GIT_TIMEOUT)[1].splitlines():
        try:
            text = (repo / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        terms += [m.group(1) for ln in text.splitlines() for pat in _DIFF_TERMS
                  for m in [pat.match("+" + ln)] if m]
    return unique(terms)


def measure(ctx: Ctx, terms: list[str], name: str, title: str) -> list[str]:
    """変わる名前から、自分のリポジトリで影響を受けるファイルを測る（graphify affected + git grep）。"""
    terms = terms[:MAX_TERMS]
    body, files, note = search(ctx, ctx.own, terms, "affected")
    files = files[:MAX_MEASURED]
    write_report(ctx, name, title, terms, [(f"{ctx.root}{scope_words(ctx.own)}", note, body)], files)
    return files


def measure_refs(ctx: Ctx, terms: list[str], name: str, title: str) -> set[tuple[str, str]]:
    """自分の変更で変わる名前に、参照先のどのファイルが触れているかを測る（git grep。語単位）。

    グラフの query は関係の近いものまで広く拾うので、漏れの検査には文字列の一致だけを使う。
    """
    terms = terms[:MAX_TERMS]
    parts, files, found = [], [], set()
    for r in ctx.refs:
        body, hits, _ = search(ctx, r, terms, "query", use_graph=False)
        parts.append((f"{r.name}（{r.label}）  {r.path}{scope_words(r)}", "（文字列の一致だけで測る）", body))
        for rel in hits[:MAX_MEASURED]:
            found.add((r.name, rel))
            files.append(f"{r.name}:{rel}" if len(ctx.refs) > 1 else rel)
    write_report(ctx, name, title, terms, parts, files)
    return found


def listed_paths(ctx: Ctx, body: str, only_no_change: bool = False, skip_no_change: bool = False,
                 allow_new: bool = False) -> set[str]:
    paths: set[str] = set()
    for item in items(body) or [body]:
        if only_no_change and NO_CHANGE_MARK not in item:
            continue
        if skip_no_change and NO_CHANGE_MARK in item:
            continue
        paths.update(cited_own(ctx, item, allow_new))
    return paths


def cited_anywhere(ctx: Ctx, bodies: dict[str, str]) -> set[tuple[str, str]]:
    """計画のどこかで根拠・変更案として挙げた参照先のファイル。"""
    found: set[tuple[str, str]] = set()
    for heading in (*CITED_IN_REFS, "## 参照先の変更案"):
        for item in items(bodies.get(heading, "")):
            found |= cited_refs(ctx, item, allow_new=True).found
    return found


def ref_label(ctx: Ctx, name: str, rel: str) -> str:
    return f"{name}:{rel}" if len(ctx.refs) > 1 else rel


# ---------------------------------------------------------------- 計画の検査

def sections(text: str, headings: tuple[str, ...]) -> tuple[list[str], dict[str, str]]:
    """決まった見出しが順にそろい、中身（コメントを除く）が空でないかを見る。"""
    problems: list[str] = []
    found = []
    for heading in headings:
        m = re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if m:
            found.append((m.start(), heading, m.end()))
        else:
            problems.append(f"見出しがありません: {heading}")
    found.sort()
    if [h for _, h, _ in found] != [h for h in headings if h in {f[1] for f in found}]:
        problems.append("見出しの順番がテンプレートと違います")
    bodies = {}
    for _, heading, end in found:
        nxt = re.search(r"^## ", text[end:], re.MULTILINE)
        body = re.sub(r"<!--.*?-->", "", text[end:end + nxt.start()] if nxt else text[end:], flags=re.DOTALL)
        bodies[heading] = body.strip()
        if not bodies[heading]:
            problems.append(f"見出しの中身が空です: {heading}")
    return problems, bodies


def is_none(body: str) -> bool:
    return re.sub(r"^[-*]\s*", "", body.strip()).strip("。． ") in _NONE_WORDS


def items(body: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in body.splitlines() if ln.strip().startswith(("- ", "* "))]


def planned_refs(ctx: Ctx, bodies: dict[str, str]) -> tuple[dict[str, set[str]], list[str]]:
    """参照先の変更案が変える参照先ごとのパスと、その検査で見つかった問題。"""
    planned: dict[str, set[str]] = {}
    problems: list[str] = []
    if is_none(bodies.get("## 参照先の変更案", "なし")):
        return planned, problems
    listed = items(bodies["## 参照先の変更案"])
    if not listed:
        problems.append("## 参照先の変更案 は箇条書きにしてください（無ければ「なし」）")
    for item in listed:
        cited = cited_refs(ctx, item, allow_new=True)
        if cited.ambiguous and not cited.found:
            problems.append(f"## 参照先の変更案 のパスがどの参照先か決まりません。`名前:パス` で書いてください: {item[:80]}")
        elif not cited.found:
            problems.append(f"## 参照先の変更案 の項目に、参照先のパスがありません: {item[:80]}")
        problems += [f"## 参照先の変更案 の根拠を直してください: {b}" for b in cited.bad]
        for name, rel in cited.found:
            planned.setdefault(name, set()).add(rel)
    return planned, problems


def plan_budget(ctx: Ctx, bodies: dict[str, str], planned: dict[str, set[str]]) -> list[str]:
    """1 回で変えるファイルが max_files に収まっているか（1 セッションで終わる大きさに保つ）。"""
    own = listed_paths(ctx, bodies.get("## 自分の変更案", ""), allow_new=True)
    own |= listed_paths(ctx, bodies.get("## 影響範囲", ""), skip_no_change=True)
    total = len(own) + sum(len(p) for p in planned.values())
    if total <= ctx.max_files:
        return []
    return [f"1 回で変えるファイルが {total} あり、上限 {ctx.max_files} を超えています。"
            "やりたいことを絞り、残りは「## 今回やらないこと」に書いてください"
            f"（上限は {CONFIG_NAME} の max_files）"]


def mentioned(body: str, name: str) -> bool:
    return re.search(rf"(?<![\w:.-]){re.escape(name)}(?![\w-])", body) is not None


def rules_problems(ctx: Ctx, body: str) -> list[str]:
    """決まりのファイルをすべて読んで挙げたか（見出し「守る決まり」）。"""
    rules = ctx.rule_files()
    if not rules:
        return []
    own_rels = {rel for name, rel in rules if not name}
    missing = []
    for name, rel in rules:
        ok = mentioned(body, f"{name}:{rel}") if name else mentioned(body, rel)
        if name and not ok and len(ctx.refs) == 1 and rel not in own_rels:
            ok = mentioned(body, rel)  # 参照先が 1 つで、自分に同じ名前の決まりが無ければ名前を省いてよい
        if not ok:
            missing.append(f"{name}:{rel}" if name else rel)
    if is_none(body) or missing:
        return ["守る決まりに、決まりのファイルを読んで挙げてください（このやりたいことに効く決まりと、どう守るか）: "
                + ", ".join(missing or [f"{n}:{r}" if n else r for n, r in rules])]
    return []


def used_problems(body: str, names: list[str], where: str) -> list[str]:
    missing = [n for n in names if not mentioned(body, n)]
    if not missing:
        return []
    return [f"{where}に、設定されたスキル・道具を使った結果がありません（使って、何を得たかを書いてください）: "
            + ", ".join(f"`{n}`" for n in missing)]


def verify_plan_text(ctx: Ctx, text: str) -> list[str]:
    problems, bodies = sections(text, PLAN_HEADINGS)
    if problems:
        return problems
    problems += rules_problems(ctx, bodies["## 守る決まり"])
    problems += used_problems(bodies["## 使ったスキルと道具"],
                              ctx.config["skills"]["plan"] + ctx.config["tools"]["plan"], "使ったスキルと道具")
    for heading in CITED_IN_REFS:
        if is_none(bodies[heading]):
            continue
        listed = items(bodies[heading])
        if not listed:
            problems.append(f"{heading} は箇条書きにしてください（無ければ「なし」）")
        for item in listed:
            cited = cited_refs(ctx, item)
            if not cited.found:
                hint = "（どの参照先か `名前:パス` で書いてください）" if cited.ambiguous else ""
                problems.append(f"{heading} の項目に、参照先に実在する根拠のパスがありません{hint}: {item[:80]}")
                continue
            problems += [f"{heading} の根拠を直してください: {b}" for b in cited.bad]
            if heading in ANCHORED_IN_REFS:
                missing = unanchored_names(ctx, item, cited)
                if missing:
                    problems.append(f"{heading} の項目の名前が、根拠のファイルに見当たりません: "
                                    + ", ".join(f"`{t}`" for t in missing) + f"（{item[:60]}）")
    drift = not is_none(bodies["## ずれ"])
    ref_change = not is_none(bodies["## 参照先の変更案"])
    impact = not is_none(bodies["## 影響範囲"])
    if drift and not ref_change:
        problems.append("ずれがあるのに、参照先の変更案が「なし」です（ずれを残すなら、ずれではなくその他に書く）")
    if not drift and ref_change:
        problems.append("ずれが「なし」なのに、参照先の変更案があります")
    if not is_none(bodies["## 自分の変更案"]):
        for item in items(bodies["## 自分の変更案"]) or [bodies["## 自分の変更案"]]:
            if not cited_own(ctx, item, allow_new=True):
                problems.append(f"自分の変更案の項目に、自分のリポジトリのパスがありません: {item[:80]}")
        if not own_terms_from_plan(bodies):
            problems.append("自分の変更案で変わる・使う名前（関数・API・用語・見出し）を `…` で囲んでください"
                            "（自分と参照先への影響を測る語になります）")
    planned, ref_problems = planned_refs(ctx, bodies)
    problems += ref_problems
    if ref_change and not impact:
        problems.append("参照先の変更案があるのに、影響範囲が「なし」です")
    if impact:
        for item in items(bodies["## 影響範囲"]) or [bodies["## 影響範囲"]]:
            if not cited_own(ctx, item):
                problems.append(f"影響範囲の項目に、自分のリポジトリに実在するパスがありません: {item[:80]}")
    if ref_change and not terms_from_plan(bodies):
        problems.append("参照先の変更案で変わる名前（関数・API・用語・見出し）を `…` で囲んでください"
                        "（影響範囲を測る語になります）")
    problems += plan_budget(ctx, bodies, planned)
    return problems


def measure_plan(ctx: Ctx, bodies: dict[str, str]) -> tuple[list[str], list[str], int]:
    """計画の名前から影響を測り、計画が漏れなく扱っているかを見る。（問題, 自分で測ったファイル, 参照先で測った数）"""
    problems: list[str] = []
    own_terms = own_terms_from_plan(bodies)
    terms = unique(own_terms + terms_from_plan(bodies))
    measured: list[str] = []
    if terms:
        # 自分の変更と参照先の変更で動く名前が、自分のどこに響くか。変えるか「変更不要」と書くか。
        measured = measure(ctx, terms, "impact.md",
                           f"計画の変更が自分のリポジトリ（{SIDES[ctx.side]}）に響く範囲（測定）")
        listed = (listed_paths(ctx, bodies["## 自分の変更案"], allow_new=True)
                  | listed_paths(ctx, bodies["## 影響範囲"]))
        missing = [p for p in measured if not covered(p, listed)]
        if missing:
            problems.append(
                "測った影響範囲のうち、計画に無いファイルがあります（変えるなら自分の変更案か影響範囲に直し方を、"
                f"変えなくてよいなら影響範囲に「{NO_CHANGE_MARK}: 理由」を足してください）: "
                + ", ".join(missing) + f"（詳細: {DATA_DIRNAME}/impact.md）")
    ref_hits: set[tuple[str, str]] = set()
    if own_terms:
        # 自分の変更で動く名前に触れている参照先のファイルを、計画が読んで扱っているか（逆向きの漏れ）。
        ref_hits = measure_refs(ctx, own_terms, "ref-impact.md", "自分の変更で動く名前に触れている参照先のファイル（測定）")
        cited = cited_anywhere(ctx, bodies)
        missing_refs = sorted(ref_label(ctx, n, r) for n, r in ref_hits if (n, r) not in cited)
        if missing_refs:
            problems.append(
                "自分の変更で動く名前に触れている参照先のファイルを、計画で扱っていません（読んで、前提・制約・その他・"
                "ずれの根拠か参照先の変更案に挙げてください。関係が無ければ、その他に「関係なし: 理由」と根拠付きで）: "
                + ", ".join(missing_refs) + f"（詳細: {DATA_DIRNAME}/ref-impact.md）")
    return problems, measured, len(ref_hits)


def write_baseline(ctx: Ctx) -> None:
    """変える前の印。verify-apply はここから「どのファイルを変えたか」を測る。確認の直前に取り直す。"""
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / "applied.json").unlink(missing_ok=True)
    (ctx.data / "before.json").write_text(json.dumps({
        "own": snapshot(ctx.own), "refs": {r.name: snapshot(r) for r in ctx.refs},
    }, indent=2) + "\n", encoding="utf-8")


def cmd_verify_plan(ctx: Ctx, args: argparse.Namespace) -> int:
    if not ctx.plan.is_file():
        print(f"計画がありません: {DATA_DIRNAME}/plan.md", file=sys.stderr)
        return 1
    text = ctx.plan.read_text(encoding="utf-8")
    problems = verify_plan_text(ctx, text)
    measured: list[str] = []
    ref_count = 0
    if not problems:
        _, bodies = sections(text, PLAN_HEADINGS)
        problems, measured, ref_count = measure_plan(ctx, bodies)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    write_baseline(ctx)
    notes = []
    if measured:
        notes.append(f"影響範囲を測った: {len(measured)} files、{DATA_DIRNAME}/impact.md")
    if ref_count:
        notes.append(f"参照先で触れている: {ref_count} files、{DATA_DIRNAME}/ref-impact.md")
    print("OK plan" + (f"（{'／'.join(notes)}）" if notes else ""))
    return 0


# ---------------------------------------------------------------- 変えたあとの検査

def run_check(repo: Path, command: list[str] | None, label: str) -> list[str]:
    if not command:
        return []
    rc, out = run(command, repo, CHECK_TIMEOUT)
    if rc == 0:
        return []
    tail = "\n".join(out.strip().splitlines()[-20:])
    return [f"{label}の検査が失敗しました（{rc}）: {' '.join(command)}\n{tail}"]


@dataclass
class Applied:
    """変えたあとの様子。verify-apply と report が使う。"""
    bodies: dict[str, str]
    own_touched: set[str]
    touched: dict[str, set[str]]
    planned: dict[str, set[str]]
    plan_problems: list[str]

    @property
    def changed(self) -> list[str]:
        return [name for name, files in self.touched.items() if files]


def load_applied(ctx: Ctx) -> Applied | str:
    before_file = ctx.data / "before.json"
    if not ctx.plan.is_file() or not before_file.is_file():
        return "計画か、計画を検査したときの印がありません（計画からやり直してください）"
    before = json.loads(before_file.read_text(encoding="utf-8"))
    if not isinstance(before.get("own"), dict):
        return "変える前の印が古い形です（計画の検査からやり直してください）"
    _, bodies = sections(ctx.plan.read_text(encoding="utf-8"), PLAN_HEADINGS)
    planned, plan_problems = planned_refs(ctx, bodies)
    return Applied(bodies, changed_since(ctx.own, before["own"]),
                   {r.name: changed_since(r, before.get("refs", {}).get(r.name, {})) for r in ctx.refs},
                   planned, plan_problems)


def own_planned(ctx: Ctx, bodies: dict[str, str]) -> set[str]:
    body = bodies.get("## 自分の変更案", "なし")
    return set() if is_none(body) else listed_paths(ctx, body, allow_new=True)


def cmd_verify_apply(ctx: Ctx, args: argparse.Namespace) -> int:
    a = load_applied(ctx)
    if isinstance(a, str):
        print(a, file=sys.stderr)
        return 1
    bodies = a.bodies
    problems = list(a.plan_problems)

    # 1. 計画のファイルを最後まで変えたか（途中で止まっていないか）。
    want_own = own_planned(ctx, bodies)
    if not is_none(bodies.get("## 自分の変更案", "なし")) and not a.own_touched:
        problems.append("自分の変更案があるのに、自分のリポジトリが変わっていません")
    else:
        undone = sorted(p for p in want_own if not any(covered(t, {p}) for t in a.own_touched))
        if undone:
            problems.append("自分の変更案のファイルをまだ変えていません（最後まで変えてください。変えなくてよくなったなら、"
                            "利用者に確かめて計画を直してください）: " + ", ".join(undone))

    # 2. 計画に無いファイルを変えていないか。
    own_allowed = want_own | listed_paths(ctx, bodies.get("## 影響範囲", ""), allow_new=True)
    extra_own = sorted(p for p in a.own_touched if not covered(p, own_allowed))
    if extra_own:
        problems.append("計画に無いファイルを変えています（戻すか、利用者に確かめて計画の自分の変更案に足してください）: "
                        + ", ".join(extra_own))
    for r in ctx.refs:
        touched = a.touched[r.name]
        if r.name in a.planned and not touched:
            problems.append(f"参照先の変更案で {r.name} を変えるはずなのに、{r.name} が変わっていません")
        elif r.name not in a.planned and touched:
            problems.append(f"参照先の変更案に {r.name} は無いのに、{r.name} が変わっています（戻してください）")
        elif touched:
            extra = sorted(p for p in touched if not covered(p, a.planned[r.name]))
            if extra:
                problems.append(f"{r.name} で参照先の変更案に無いファイルを変えています（戻すか、利用者に確かめて"
                                "計画の参照先の変更案に足してください）: " + ", ".join(extra))
            undone = sorted(p for p in a.planned[r.name] if not any(covered(t, {p}) for t in touched))
            if undone:
                problems.append(f"{r.name} で参照先の変更案のファイルをまだ変えていません: " + ", ".join(undone))

    # 3. 実際の変更から影響を測り直す（自分の変更・参照先の変更の両方。計画より広く変えた分も拾う）。
    changed = [ctx.ref(n) for n in a.changed]
    own_diff_terms = terms_from_diff(ctx.own) if a.own_touched else []
    terms = unique([t for r in changed for t in terms_from_diff(r)] + own_diff_terms
                   + terms_from_plan(bodies) + own_terms_from_plan(bodies))
    measured: list[str] = []
    if terms:
        measured = measure(ctx, terms, "impact-after.md",
                           f"変えたあとに、自分のリポジトリ（{SIDES[ctx.side]}）で影響を受ける範囲（測定）")
        waived = listed_paths(ctx, bodies.get("## 影響範囲", ""), only_no_change=True)
        untouched = [p for p in measured if p not in a.own_touched and p not in waived]
        if untouched:
            problems.append(
                "変更の影響を受けるのに、直していないファイルがあります（直すか、利用者に確かめて計画の影響範囲に"
                f"「{NO_CHANGE_MARK}: 理由」を書いてください）: " + ", ".join(untouched)
                + f"（詳細: {DATA_DIRNAME}/impact-after.md）")
    new_own_terms = [t for t in own_diff_terms if t not in own_terms_from_plan(bodies)]
    if new_own_terms:
        # 計画に無い名前まで自分で変えたなら、それに触れている参照先も扱ったか。
        hits = measure_refs(ctx, new_own_terms, "ref-impact-after.md",
                            "自分の実際の変更で動く名前に触れている参照先のファイル（測定）")
        cited = cited_anywhere(ctx, bodies)
        missing = sorted(ref_label(ctx, n, rel) for n, rel in hits
                         if (n, rel) not in cited and rel not in a.touched.get(n, set()))
        if missing:
            problems.append("自分の変更で動く名前に触れている参照先のファイルを、計画で扱っていません（利用者に確かめて"
                            "計画を直すか、その名前を変えないでください）: " + ", ".join(missing)
                            + f"（詳細: {DATA_DIRNAME}/ref-impact-after.md）")

    # 4. 変えるときに使うと決めたスキル・道具を使ったか（.codd/apply.md に書く）。
    names = ctx.config["skills"]["apply"] + ctx.config["tools"]["apply"]
    for r in changed:
        names += r.apply_skills + r.apply_tools
    names = unique(names)
    if names:
        log = ctx.data / "apply.md"
        fresh = log.is_file() and log.stat().st_mtime >= (ctx.data / "before.json").stat().st_mtime
        if not fresh:
            problems.append(f"{DATA_DIRNAME}/apply.md に、変えるときに使ったスキル・道具と何をしたかを書いてください: "
                            + ", ".join(f"`{n}`" for n in names))
        else:
            problems += used_problems(log.read_text(encoding="utf-8"), names, f"{DATA_DIRNAME}/apply.md ")

    # 5. 検査コマンド。
    problems += run_check(ctx.root, ctx.config.get("check"), SIDES[ctx.side])
    for r in changed:
        # 参照先の検査は、参照先に置いた同じマシンの設定（codd.json の check）を使う。
        problems += run_check(r.path, r.check, f"{r.name}（{r.label}）")
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    # 通ったときの中身を控える。report はこれと今を比べ、通ったあとに変わっていないかを確かめる。
    (ctx.data / "applied.json").write_text(json.dumps(applied_state(ctx), ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    refs_note = ",".join(a.changed) or "none"
    print(f"OK own={'changed' if a.own_touched else 'same'} refs={refs_note}"
          + (f" impact={len(measured)} files（{DATA_DIRNAME}/impact-after.md）" if measured else ""))
    return 0


# ---------------------------------------------------------------- 終わりの報告

def applied_state(ctx: Ctx) -> dict:
    return {"own": snapshot(ctx.own), "refs": {r.name: snapshot(r) for r in ctx.refs}}


def cmd_report(ctx: Ctx, args: argparse.Namespace) -> int:
    """計画のファイルごとに変えたかと、測った影響範囲、今回やらないことをまとめる。モデルの記憶に頼らず報告する。"""
    a = load_applied(ctx)
    if isinstance(a, str):
        print(a, file=sys.stderr)
        return 1
    applied_file = ctx.data / "applied.json"
    applied = json.loads(applied_file.read_text(encoding="utf-8")) if applied_file.is_file() else None
    now = applied_state(ctx)
    if applied is None:
        state = "まだ通っていない（変えたあとの検査からやり直してください）"
    elif applied == now:
        state = "通った"
    else:
        state = "通ったあとに、さらに変わっている（変えたあとの検査をもう一度通してください）"

    def rows(planned: set[str], touched: set[str]) -> list[str]:
        out = [f"- {p} — {'変えた' if any(covered(t, {p}) for t in touched) else 'まだ'}" for p in sorted(planned)]
        out += [f"- {p} — 変えた（影響範囲）" for p in sorted(touched) if not covered(p, planned)]
        return out or ["- なし"]

    lines = ["# 結果", "", f"- 変えたあとの検査: {state}", "", f"## 自分（{SIDES[ctx.side]}）  {ctx.root}", ""]
    lines += rows(own_planned(ctx, a.bodies), a.own_touched)
    for r in ctx.refs:
        if r.name in a.planned or a.touched[r.name]:
            lines += ["", f"## {r.name}（{r.label}）  {r.path}", ""]
            lines += rows(a.planned.get(r.name, set()), a.touched[r.name])
    after = ctx.data / "impact-after.md"
    if after.is_file():
        text = after.read_text(encoding="utf-8")
        measured = [ln[2:] for ln in text.split("## 候補のファイル", 1)[-1].splitlines() if ln.startswith("- ")]
        waived = listed_paths(ctx, a.bodies.get("## 影響範囲", ""), only_no_change=True)
        lines += ["", "## 変えたあとに測った影響範囲", ""]
        for p in measured:
            mark = "直した" if p in a.own_touched else NO_CHANGE_MARK if p in waived else "未対応" \
                if not p.startswith("(") else ""
            lines.append(f"- {p}" + (f" — {mark}" if mark else ""))
    todo = a.bodies.get("## 今回やらないこと", "なし")
    lines += ["", "## 次にやること（今回やらないこと）", "", todo if not is_none(todo) else "- なし", "",
              "どちらのリポジトリもコミットしていない。内容を確かめてから、それぞれでコミットする。", ""]
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if state == "通った" else 1


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codd.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="この側・参照先と、守る決まり・使うスキルと道具を示す")
    s.add_argument("--phase", choices=list(PHASES), help="plan（計画を練るとき）か apply（変えるとき）")
    e = sub.add_parser("explore", help="参照先を探す")
    e.add_argument("--term", action="append", help="検索語（繰り返し可）")
    e.add_argument("--ref", action="append", help="探す参照先の名前（繰り返し可。既定はすべて）")
    i = sub.add_parser("impact", help="自分のリポジトリで影響を受ける箇所を探す")
    i.add_argument("--term", action="append", help="検索語（繰り返し可）")
    sub.add_parser("verify-plan", help="計画が決まった形かを検査する")
    sub.add_parser("verify-apply", help="計画どおりに変えたかを検査する")
    sub.add_parser("report", help="変えた結果をまとめる（終わりの報告）")
    ru = sub.add_parser("rules", help="守る決まりのファイルと、決まりらしい候補を示す")
    ru.add_argument("--write", action="store_true", help="候補を codd.json の rules / refs[].rules に書く")
    ru.add_argument("--only", action="append", help="書く候補を絞る（`名前:パス` か `パス`。繰り返し可）")
    return p


COMMANDS = {"show": cmd_show, "explore": cmd_explore, "impact": cmd_impact,
            "verify-plan": cmd_verify_plan, "verify-apply": cmd_verify_apply, "report": cmd_report,
            "rules": cmd_rules}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](Ctx(repo_root(Path.cwd())), args)
    except CoddError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
