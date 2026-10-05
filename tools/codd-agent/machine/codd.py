#!/usr/bin/env python3
"""codd-agent — 参照先（実装⇔設計書。いくつでも）を読んで、自分の変更を練るステートマシンの下請け。

ステートマシン（同じフォルダの workflow.yaml）のうち、機械で決まる仕事だけをここに置く。
判断（参照先の前提・制約・その他、ずれ、変更案、影響範囲）はアクションの側でモデルが行う。

    show [--phase P]    この側・参照先の一覧と、守る決まりのファイル、使うスキルと道具（計画を練るとき / 変えるとき）を示す
    rules [--write]     守る決まりのファイルと、決まりらしいマークダウンの候補を示す。--write で候補を codd.json に書く
    explore --term 語   参照先を探す（graphify のグラフを必要なら作り直してから引く）。--ref で絞れる
    impact  --term 語   自分のリポジトリで影響を受ける箇所を探す（同上）
    verify-plan         計画（.plans/日時-名前.md）が決まった形か、根拠が参照先に実在するか（パス・行・見出し・
                        `…` で囲んだ名前）、1 回で扱う範囲（max_files）に収まるかを検査する。
                        参照先の変更案があれば、それを自分に適用したときの影響範囲を測り、計画の影響範囲が
                        測ったファイルをすべて挙げているかも検査する。通ったら、変える前の印を控える
    verify-apply        計画どおりに変えたか（変えてよいのは計画に挙げたファイルだけ。参照先も同じ）と、
                        検査コマンドを確かめる。参照先を変えたら、実際の変更から影響範囲を測り直し、
                        測ったファイルを直したか「変更不要」としたかを検査する。書き足したパスが実在するか、
                        消したファイルを指したままのところが無いかも確かめる。
                        段に分けて変えているときは、途中の段ではその段までのファイルを変え終えたかだけを確かめ、
                        第 1 行を MORE にして次の段へ進める（全体の検査とテストは最後の段で）
    batch               段に分けて変えるとき、今の段で変えるファイルを示す
    draft --name 名前   計画のひな形を .plans/日時-名前.md に置く（名前は英語の短い名前。進めている計画があれば残す）。
                        見出しごとに書き込ませ、全文を一度に書かせない
    record              1 回の終わりに、計画へ確認の答えと結果（report）を書き足して記録として残す（要らない情報は除く）
    decide 答え         確認・相談での利用者の答え（OK / NG / PLAN / APPLY / STOP）と指摘を控える。終わりの報告で計画の記録に書く
    summary             計画の要約（やりたいこと・ずれ・変えるファイル・テスト・今回やらないこと）。確認で全文の代わりに見せる
    report              計画のファイルごとに変えたか、測った影響範囲、今回やらないことをまとめる（終わりの報告）
    skill 名前…         スキルの SKILL.md を出して読み込む。使うと書いたスキルを読み込んだかを検査が確かめる
    rule [--all|パス…]  守る決まりのファイルを出して読み込む。計画の検査は、すべて読み込んだか（中身が変わっていれば読み直したか）を確かめる。
                        この回で読み込み済みで変わっていないものは出し直さない（--again で出す）
    advise              検査で止まった理由を分け、利用者に確かめることと次の手（勧めと選択肢）を示す
    keep-changes        変えた分を残したまま計画を直す（次の計画の検査で、変える前の印を取り直さない）
    rollback            計画の検査が通ったとき（変える前）の中身へ戻す。そのあとに変わったファイルだけ
    lint [--since 日]  本流とは別の点検。リポジトリ全体の食い違い（壊れたパス・文書の書式・写したテストの結果・
                        テスト・codd を通らなかった変更）を探し、本流に渡す「やりたいこと」の 1 行にする。何も直さない

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

名前の一致とは別に、ファイル同士がパスで指し合う「つながり」（注記 `coherence: doc=パス`、文書の `…` のパスとリンク。
codd-gate と同じ書き方）もたどる。計画・変更で動くファイルとつながった相手の側のファイルを、計画が扱っているかを見る。
"""

from __future__ import annotations

import argparse
import hashlib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MACHINE_DIR = Path(__file__).resolve().parent
MACHINE_REL = ".statemachine/codd"
# init.py が書くカスタムエージェント。マシンの一部なので、探す・変わったかを測る対象にしない。
AGENT_FILES = (".kiro/agents/codd.json", ".github/agents/codd.agent.md")
CONFIG_NAME = "codd.json"
DATA_DIRNAME = ".codd"
# 計画の置き場所（自分のリポジトリ）。1 回の実行の計画は、始めるときに一意な名前（日時と英語の短い名前）で置き、
# 利用者はこれを読んで確かめる。終わりに確認の答えと結果を書き足して、判断の記録としてそのまま残す（コミットしてよい）。
# 結果の見出しが無い計画が、いま進めている回の計画。
PLAN_DIR = ".plans"
LEGACY_PLAN_DIRS = ("docs/.plans", "docs/.plan")
RESULT_HEADING = "## 結果"
MACHINE_OWNED = [DATA_DIRNAME, MACHINE_REL, *AGENT_FILES, PLAN_DIR, *LEGACY_PLAN_DIRS]
_PLAN_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SIDES = {"impl": "実装", "design": "設計書"}
OTHER_SIDE = {"impl": "design", "design": "impl"}
PHASES = {"plan": "計画を練るとき", "apply": "変えるとき"}
CONFIG_KEYS = {"side", "refs", "ref_path", "skills", "tools", "rules", "graphify", "check", "scope", "max_files", "batch_files",
               "skill_dirs", "test", "tests", "evidence", "exclude"}
REF_KEYS = {"name", "path", "skills", "scope", "rules", "exclude"}
# 1 回の計画で変えるファイルの上限と、1 つの段（apply を分けた 1 回ぶん）で変えるファイルの数。
# 計画は影響範囲・テストまで漏れなく挙げるので大きくなりやすい。上限は緩め、変えるときは段に分けて、
# 段ごとに挙げたファイルを変え終えたかを確かめてから次へ進む（変え残しをその段のうちに見つける）。
DEFAULT_MAX_FILES = 60
DEFAULT_BATCH_FILES = 10
BATCHES_NAME = "batches.json"
# テストのファイル（単体テスト・API テスト・シナリオテスト・e2e のケース。コードもケースの記述も）。
# コード・仕様書と同じ、整合を取る成果物として扱い、影響を測って計画に挙げさせる。
# codd.json の tests で変えられ、[] でテストを扱わない。
DEFAULT_TEST_PATTERNS = [
    "test/**", "tests/**", "e2e/**", "**/__tests__/**", "**/*.test.*", "**/*.spec.*",
    "**/test_*.py", "**/*_test.py", "**/*_test.go", "**/*Test.java", "**/*Tests.cs",
    "**/*.feature", "**/*.robot", "**/*.http", "**/*.postman_collection.json", "**/scenarios/**",
    "cypress/**", "playwright/**",
]
MAX_TEST_FILES = 2000
# テストで得たもの（確かめた振る舞い・測った時間・撮った画像）を書いたファイル。文書の印（<!-- evidence: id -->）に写し、
# 今と同じか・目安を超えていないかを確かめる。codd.json の evidence で変えられ、[] で扱わない。
DEFAULT_EVIDENCE = []
EVIDENCE_TOLERANCE = 0.2   # 測った値は揺れるので、写した値と 2 割までの違いは同じとみなす
TESTS_HEADING = "## テストの変更案"
# 設定しなくても使うスキルの置き場所（リポジトリのルートから）。フォルダごとに `名前/SKILL.md`。
DEFAULT_SKILL_DIRS = [".agents/skills"]
MAX_REPO_SKILLS = 30

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
    "## テストの変更案",
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
# 検査が測ったのに計画に無かったファイルを、検査がこの印で計画へ書き足す。エージェントは印を判断に書き換える。
PENDING_MARK = "未判断"
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
    """コマンドを実行して（終了コード, 出力）を返す。

    git は日本語のパスをそのまま出させ（core.quotepath=false）、通ったときは標準出力だけを返す
    （改行コードの警告などの標準エラーが、ファイルの一覧に混ざらないように）。
    """
    is_git = bool(argv) and argv[0] == "git"
    if is_git:
        argv = ["git", "-c", "core.quotepath=false", *argv[1:]]
    if os.name == "nt" and argv:
        # Windows では外部コマンドが .cmd なので、PATHEXT で探してから起動する。
        argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"({timeout} 秒で終わらなかったので打ち切りました)"
    except OSError as exc:
        return 127, f"(実行できませんでした: {exc})"
    if is_git and proc.returncode == 0:
        return 0, proc.stdout or ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def git_grep(side: "Side", args: list[str]) -> list[str]:
    """git grep を scope の中で引き、一致した行（-l ならファイル）を返す。終了コード 1 だけが「該当なし」。

    それ以外の失敗（古い git が知らないオプション（129）、壊れたリポジトリ、時間切れ）を「該当なし」と
    取り違えると、影響範囲やテストを黙って取りこぼすので止める。
    """
    rc, out = run(["git", "grep", *args, *side.pathspec()], side.path, GIT_TIMEOUT)
    if rc == 1:
        return []
    if rc != 0:
        raise CoddError(f"{side.path} で git grep が失敗しました（終了コード {rc}）: {out.strip()[:300]}")
    return [ln for ln in out.splitlines() if side.has(ln.split(":", 1)[0])]


GREP_PER_FILE = 3   # 1 つの語で 1 ファイルから拾う行の上限（git の --max-count は 2.38 より前に無いので、ここで絞る）


def per_file(lines: list[str], limit: int = GREP_PER_FILE) -> list[str]:
    counts: dict[str, int] = {}
    out = []
    for ln in lines:
        rel = ln.split(":", 1)[0]
        counts[rel] = counts.get(rel, 0) + 1
        if counts[rel] <= limit:
            out.append(ln)
    return out


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


def exclude_list(value, where: str) -> list[str]:
    """除外するパス・glob。省略か [] なら追加の除外なし。"""
    if value is None:
        return []
    if not (isinstance(value, list) and all(isinstance(p, str) and p.strip() for p in value)):
        raise CoddError(f'{where} は除外パターンの配列です（例: ["**/*.config.*", ".github/"]）')
    out = unique_paths(p.strip().replace("\\", "/") for p in value)
    bad = [p for p in out if p.startswith(("/", "~", "!")) or re.match(r"^[A-Za-z]:", p)
           or ".." in p.split("/") or p.rstrip("/") in ("", ".")]
    if bad:
        raise CoddError(f"{where} にはリポジトリの中のパスか glob を相対で書きます（今: {', '.join(bad)}）")
    return out


def is_argv(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(a, str) for a in value)


def test_commands(value) -> list[tuple[str, list[str]]]:
    """codd.json の test を（名前, コマンド）の並びに。単体テストも API テストもシナリオテストも同じに動かす。"""
    if not value:
        return []
    return [("", value)] if isinstance(value, list) else list(value.items())


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
        ref["exclude"] = exclude_list(ref.get("exclude"), f"{path} の refs[{i}].exclude")
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
    skill_dirs = config.get("skill_dirs", DEFAULT_SKILL_DIRS)
    config["skill_dirs"] = [] if skill_dirs == [] else scope_list(skill_dirs, f"{path} の skill_dirs")
    config["scope"] = scope_list(config.get("scope"), f"{path} の scope")
    config["exclude"] = exclude_list(config.get("exclude"), f"{path} の exclude")
    config.setdefault("graphify", "auto")
    if config["graphify"] not in ("auto", "off"):
        raise CoddError(f"{path} の graphify は auto か off です（今: {config['graphify']!r}）")
    command = config.get("check")
    if command is not None and not is_argv(command):
        raise CoddError(f'{path} の check はコマンドの配列です（例: ["npm", "test"]）')
    test = config.get("test")
    if test is not None and not (is_argv(test) or (isinstance(test, dict) and test and all(
            isinstance(k, str) and k and is_argv(v) for k, v in test.items()))):
        raise CoddError(f'{path} の test はコマンドの配列か、名前ごとのコマンドです'
                        '（例: ["npm", "test"] / {"単体": ["npm", "test"], "API": ["npm", "run", "test:api"]}）')
    evidence = config.get("evidence", DEFAULT_EVIDENCE)
    config["evidence"] = [] if evidence == [] else rule_list(evidence, f"{path} の evidence")
    tests = config.get("tests", DEFAULT_TEST_PATTERNS)
    config["tests"] = [] if tests == [] else rule_list(tests, f"{path} の tests")
    config.setdefault("max_files", DEFAULT_MAX_FILES)
    if not (isinstance(config["max_files"], int) and not isinstance(config["max_files"], bool)
            and config["max_files"] > 0):
        raise CoddError(f"{path} の max_files は 1 以上の整数です（今: {config['max_files']!r}）")
    config.setdefault("batch_files", DEFAULT_BATCH_FILES)
    if not (isinstance(config["batch_files"], int) and not isinstance(config["batch_files"], bool)
            and config["batch_files"] > 0):
        raise CoddError(f"{path} の batch_files は 1 以上の整数です（今: {config['batch_files']!r}）")
    return config


def in_scope(rel: str, scope: list[str]) -> bool:
    return not scope or any(rel == s or rel.startswith(s + "/") for s in scope)


def machine_owned(rel: str) -> bool:
    """このマシンが書くファイル（作業フォルダ・マシン・エージェント・計画）。変えたファイルにも探す対象にも数えない。"""
    return any(rel == p or rel.startswith(p + "/") for p in MACHINE_OWNED)


def covered(rel: str, listed: set[str]) -> bool:
    """rel が計画に挙げたパス（ファイルか、そのファイルを含むフォルダ）に入っているか。"""
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in listed)


def overlaps(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return True
    return any(x == y or x.startswith(y + "/") or y.startswith(x + "/") for x in a for y in b)


@dataclass
class Side:
    """探す・変わったかを測る単位。リポジトリと、その中で受け持つフォルダ（scope。空なら全体）から exclude を除く。"""
    name: str
    path: Path
    scope: list[str]
    exclude: list[str] = field(default_factory=list)

    def pathspec(self) -> list[str]:
        # マシンのファイルは :(exclude) で外さず、出力を has() で外す。git 2.43 などでは、scope の最初のフォルダ名が
        # 除外する名前（.codd）より長いと ls-files が何も返さなくなり、テストや画像を黙って取りこぼすため。
        return ["--", *(self.scope or ["."])]

    def excluded(self, rel: str) -> bool:
        for pattern in self.exclude:
            if pattern.endswith("/"):
                pattern += "**"
            if glob_re(pattern).fullmatch(rel):
                return True
            if not _GLOB_CHARS.search(pattern) and rel.startswith(pattern + "/"):
                return True
        return False

    def has(self, rel: str) -> bool:
        return in_scope(rel, self.scope) and not machine_owned(rel) and not self.excluded(rel)


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

    @property
    def test(self) -> list[str] | dict | None:
        return (self.config or {}).get("test")

    @property
    def evidence_patterns(self) -> list[str]:
        return (self.config or {}).get("evidence", DEFAULT_EVIDENCE)

    @property
    def test_patterns(self) -> list[str]:
        return (self.config or {}).get("tests", DEFAULT_TEST_PATTERNS)


def active_plan(root: Path) -> Path | None:
    """いま進めている回の計画（結果の見出しがまだ無いもの。2 つ以上あれば新しいほう）。"""
    folder = root / PLAN_DIR
    open_plans = [p for p in sorted(folder.glob("*.md")) if RESULT_HEADING not in
                  (read_text(p) or "").splitlines()] if folder.is_dir() else []
    return open_plans[-1] if open_plans else None


def plan_rel(ctx: "Ctx") -> str:
    return ctx.plan.relative_to(ctx.root).as_posix()


NO_PLAN = f"計画がありません（`python3 {MACHINE_REL}/codd.py draft --name 英語の短い名前` でひな形を置く）"


class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_config(MACHINE_DIR)
        self.side = self.config["side"]
        self.own = Side("own", root, self.config["scope"], exclude=self.config["exclude"])
        self.data = root / DATA_DIRNAME
        self.plan = active_plan(root) or root / PLAN_DIR / "（計画がありません）.md"
        self.max_files = self.config["max_files"]
        self.batch_files = self.config["batch_files"]
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
            self.refs.append(Ref(entry["name"], path, entry["scope"], exclude=entry["exclude"],
                                 config=ref_config, label=label, entry_skills=entry["skills"],
                                 entry_rules=entry["rules"]))
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
        for name, side in [("", Side("own", self.root, [])), *[(r.name, Side(r.name, r.path, r.scope)) for r in self.refs]]:
            for rel in discover_rules(side, near=self.own.scope if not name else None):
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
_FRONT = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


@dataclass
class RepoSkill:
    name: str
    path: str          # SKILL.md のパス（リポジトリのルートから）
    description: str


def repo_skills(repo: Path, dirs: list[str]) -> list[RepoSkill]:
    """置き場所（.agents/skills など）にあるスキル。設定しなくても、関係するものは使う。"""
    out: list[RepoSkill] = []
    for d in dirs:
        base = repo / d
        if not base.is_dir():
            continue
        for skill_md in sorted(base.glob("*/SKILL.md")):
            text = read_text(skill_md) or ""
            front = _FRONT.match(text)
            meta = front.group(1) if front else ""
            name = re.search(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", meta, re.MULTILINE)
            desc = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", meta, re.MULTILINE)
            skill = RepoSkill(name.group(1).strip() if name else skill_md.parent.name,
                              skill_md.relative_to(repo).as_posix(), desc.group(1).strip() if desc else "")
            if _SKILL.match(skill.name) and all(x.name != skill.name for x in out):
                out.append(skill)
    return out[:MAX_REPO_SKILLS]


# 名前で指定したスキルを探す置き場所（リポジトリと、利用者のホーム）。skill_dirs を先に見る。
SKILL_SEARCH_DIRS = [".agents/skills", ".kiro/skills", ".github/skills", ".claude/skills", ".copilot/skills"]
SKILLS_READ = "skills-read.json"
NOT_USED_MARK = "使わない"


def find_skill(ctx: "Ctx", spec: str) -> tuple[str, Path] | None:
    """`名前` か `参照先の名前:名前` のスキルの SKILL.md。見つからなければ None。"""
    ref_name, sep, name = spec.partition(":")
    if sep and ref_name in {r.name for r in ctx.refs}:
        r = ctx.ref(ref_name)
        places = [(r.path, [*(r.config or {}).get("skill_dirs", DEFAULT_SKILL_DIRS), *SKILL_SEARCH_DIRS])]
    else:
        name = spec
        places = [(ctx.root, [*ctx.config["skill_dirs"], *SKILL_SEARCH_DIRS]), (Path.home(), SKILL_SEARCH_DIRS),
                  *((r.path, [*(r.config or {}).get("skill_dirs", DEFAULT_SKILL_DIRS), *SKILL_SEARCH_DIRS])
                    for r in ctx.refs if r.path != ctx.root)]
    for base, dirs in places:
        for d in unique_paths(dirs):
            direct = base / d / name / "SKILL.md"
            if direct.is_file():
                return name, direct
            for skill in repo_skills(base, [d]):
                if skill.name == name:
                    return name, base / skill.path
    return None


def skills_read(ctx: "Ctx") -> dict:
    path = ctx.data / SKILLS_READ
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def used_skill_names(body: str, candidates: list[str]) -> list[str]:
    """本文で使ったと書いたスキル（その名前を挙げた行に「使わない」が無いもの）。"""
    used = []
    for line in body.splitlines():
        if NOT_USED_MARK in line:
            continue
        used += [n for n in candidates if mentioned(line, n) and n not in used]
    return used


def unread_skills(ctx: "Ctx", names: list[str], since: float = 0.0) -> list[str]:
    """使うと書いたのに、`codd.py skill` で読み込んでいない（見つかるものだけ確かめる）スキル。"""
    log = skills_read(ctx)
    return [n for n in names if find_skill(ctx, n) and log.get(n, {}).get("time", -1.0) < since]


def unread_problem(names: list[str], where: str) -> list[str]:
    if not names:
        return []
    return [f"{where}スキルを読み込んでいません（`python3 {MACHINE_REL}/codd.py skill 名前` で SKILL.md を読み込み、"
            "その手順に従って使ってください。エージェントが自分でスキルを選ぶのを待たない）: "
            + ", ".join(f"`{n}`" for n in names)]


def cmd_skill(ctx: "Ctx", args: argparse.Namespace) -> int:
    """スキルの SKILL.md を出して読み込ませ、読み込んだことを控える（検査が確かめる）。"""
    log = skills_read(ctx)
    missing = []
    for spec in args.name:
        found = find_skill(ctx, spec)
        if not found:
            missing.append(spec)
            continue
        _, path = found
        print(f"# スキル {spec}（{path}）\n")
        print(read_text(path) or "")
        log[spec] = {"path": str(path), "time": time.time()}
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / SKILLS_READ).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if missing:
        where = ", ".join([*ctx.config["skill_dirs"], *SKILL_SEARCH_DIRS])
        print(f"スキルが見つかりません: {', '.join(missing)}（探した場所: {where}、とホームの同じ場所）", file=sys.stderr)
        return 1
    return 0


def skill_lines(skills: list[RepoSkill], prefix: str = "") -> list[str]:
    return [f"  - `{s.name}` — {s.description[:120] or '（説明なし）'}（{prefix}{s.path}）" for s in skills]
# 決まりらしいマークダウンの目印（パスの語か、最初の見出し）。
_RULE_WORDS = re.compile(
    r"(?:^|[^a-z])(rules?|guidelines?|conventions?|coding|style-?guide|standards?|policy|policies|contributing)"
    r"(?:[^a-z]|$)|規約|ルール|規則|約束|作法|規程|ガイドライン|コーディング")
_NOT_RULES = re.compile(r"(?:^|/)(changelog|history|license)[^/]*$", re.IGNORECASE)


_GLOB_CHARS = re.compile(r"[*?\[]")
_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")


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
                          and not in_scope(ln, MACHINE_OWNED)) if rc == 0 else []
        else:
            hits = [pat] if (repo / pat).is_file() else []
        out += [h for h in hits if h not in out]
    return out


def discover_rules(side: Side, near: list[str] | None = None) -> list[str]:
    """scope の中のマークダウンのうち、パスか最初の見出しが決まりらしいもの。
    near（自分の scope）を渡すと、その中と、その上のフォルダ（ルートを含む）にあるものだけにする。
    同じリポジトリのほかの道具の決まりまで拾わない。"""
    rc, out = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", *side.pathspec()],
                  side.path, GIT_TIMEOUT)
    found = []
    files = out.splitlines() if rc == 0 else []
    # スキルの中のマークダウン（SKILL.md と、その下の rules/ や references/）は、そのスキルを使うときに読むもので、
    # このリポジトリの決まりではない。候補にすると、関係の無い決まりを毎回すべて読み込ませることになる。
    skill_dirs = {rel.rsplit("/", 1)[0] if "/" in rel else "" for rel in files if rel.rsplit("/", 1)[-1] == "SKILL.md"}
    for rel in files:
        if not rel.lower().endswith((".md", ".markdown")) or _NOT_RULES.search(rel) or not side.has(rel):
            continue
        if any(d == "" or rel.startswith(d + "/") for d in skill_dirs):
            continue
        # 日付で始まるファイル（2026-08-15-…-policy-design.md など）は計画や記録で、決まりではない。
        if _DATED.match(rel.rsplit("/", 1)[-1]):
            continue
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if near and not any(in_scope(rel, [f]) or not folder or (f + "/").startswith(folder + "/") for f in near):
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
    # マシンのファイル（計画など）は数えない。計画を書き直すたびにグラフを作り直さないように。
    h.update(diff_without(run(["git", "diff", "HEAD", "--", "."], repo, GIT_TIMEOUT)[1], machine_owned).encode())
    untracked = [p for p in run(["git", "ls-files", "--others", "--exclude-standard", "--", "."],
                                repo, GIT_TIMEOUT)[1].splitlines() if p and not machine_owned(p)]
    for name in sorted(untracked):
        h.update(name.encode())
        try:
            h.update((repo / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def diff_without(diff: str, drop) -> str:
    """git diff の出力から、drop(パス) が真のファイルの部分を外す（パスを git に並べると、多いときに引数が長すぎる）。"""
    out, keep = [], True
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            m = re.match(r'diff --git "?a/(.+?)"? "?b/', line)
            keep = not (m and drop(m.group(1)))
        if keep:
            out.append(line)
    return "".join(out)


def head(repo: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], repo, GIT_TIMEOUT)[1].strip()


def dirty_files(side: Side) -> dict[str, str]:
    """scope の中の作業中の変更・未追跡のファイル → 中身のハッシュ（消えていれば "deleted"）。"""
    out = run(["git", "status", "--porcelain", "--untracked-files=all", *side.pathspec()],
              side.path, GIT_TIMEOUT)[1]
    files = {}
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if not path or not side.has(path):
            continue
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
    parts = ([f"受け持つフォルダ: {', '.join(side.scope)}"] if side.scope else [])
    parts += [f"除外: {', '.join(side.exclude)}"] if side.exclude else []
    return "（" + "。".join(parts) + "）" if parts else ""


def cmd_show(ctx: Ctx, args: argparse.Namespace) -> int:
    print(f"この側: {SIDES[ctx.side]}（{ctx.side}）  {ctx.root}{scope_words(ctx.own)}")
    print("参照先:")
    for r in ctx.refs:
        print(f"  - {r.name}: {r.label}  {r.path}{scope_words(r)}")
    rules = ctx.rule_files()
    print(f"守る決まり（`python3 {MACHINE_REL}/codd.py rule --all` で読み込み、計画の「守る決まり」に挙げる）:")
    for name, rel in rules or [("", "")]:
        print(f"  - {name + ':' if name else ''}{rel}" if rel else "  - なし")
    for pat in ctx.unmatched_rules():
        print(f"  ! {pat} に当たるファイルがありません（{CONFIG_NAME} の rules を確かめてください）")
    candidates = ctx.rule_candidates()
    if candidates:
        print("決まりの候補（設定に無い。決まりなら `codd.py rules --write` で設定に書く）:")
        for name, rel in candidates:
            print(f"  - {name + ':' if name else ''}{rel}")
    own_skills = repo_skills(ctx.root, ctx.config["skill_dirs"])
    if own_skills:
        print("リポジトリのスキル（設定しなくても使う。関係するものは読み込み、SKILL.md の手順に従う。"
              "計画の「使ったスキルと道具」には使ったものだけを書く）:")
        print("\n".join(skill_lines(own_skills)))
    for r in ctx.refs:
        if r.path != ctx.root:
            ref_skills = repo_skills(r.path, (r.config or {}).get("skill_dirs", DEFAULT_SKILL_DIRS))
            if ref_skills:
                print(f"{r.name} のスキル（{r.name} を変えるときに、関係するものを使う）:")
                print("\n".join(skill_lines(ref_skills, f"{r.name}:")))
    phases = [args.phase] if args.phase else list(PHASES)
    for phase in phases:
        print(f"使うスキルと道具（{PHASES[phase]}）:")
        print(f"  - 自分: {skill_words(ctx.config['skills'][phase])}"
              + (f"。道具: {tool_words(ctx.config['tools'][phase])}" if ctx.config['tools'][phase] else ""))
        if phase == "apply":
            for r in ctx.refs:
                print(f"  - {r.name} を変えるとき: {skill_words(r.apply_skills)}"
                      + (f"。道具: {tool_words(r.apply_tools)}" if r.apply_tools else ""))
    print(f"スキルは `python3 {MACHINE_REL}/codd.py skill 名前` で読み込む（使ったと書いたのに読み込んでいないと検査で落ちる）")
    print(f"1 回で変えるファイルの上限: {ctx.max_files}（超えるぶんは計画の「今回やらないこと」へ）。"
          f"変えるときは {ctx.batch_files} ファイルずつの段に分ける")
    if tests_enabled(ctx):
        counts = [f"{'自分' if not k else k} {len(test_files(ctx, k))} files" for k, _ in all_sides(ctx)
                  if test_patterns(ctx, k)]
        print(f"テスト（コード・仕様書と同じに扱う。響くものを「テストの変更案」に挙げる）: {'、'.join(counts)}"
              f"（書き方は {CONFIG_NAME} の tests）")
    evs = {k: e for k, e in all_evidence(ctx).items() if e.files}
    if evs:
        print("テストで得たもの（振る舞い・時間・画像。文書へは `<!-- evidence: id -->` の印で写す。`codd.py evidence` で一覧）: "
              + "、".join(f"{'自分' if not k else k} {len(e.items)} 件" for k, e in evs.items()))
    checks = [("自分", ctx.config.get("test"), ctx.config.get("check")),
              *((r.name, r.test, r.check) for r in ctx.refs)]
    if any(t or c for _, t, c in checks):
        print("変えたあとに実行するもの（作り直すファイルがあれば、それも計画に挙げる）:")
        for who, t, c in checks:
            for name, argv in test_commands(t):
                print(f"  - {who}のテスト{f'（{name}）' if name else ''}: {' '.join(argv)}")
            if c:
                print(f"  - {who}の検査: {' '.join(c)}")
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
           use_graph: bool = True, first_lines: dict[str, int] | None = None) -> tuple[str, list[str], str]:
    """語ごとに graphify と git grep で引き、（本文, 候補のファイル, graphify の状態）を返す。scope の外は捨てる。

    first_lines を渡すと、git grep で一致したファイルごとに最初に一致した行の番号を入れる（文字列の一致だけ）。
    """
    repo = side.path
    exe, graph, note = ensure_graph(ctx, repo) if use_graph else (None, None, "unused")
    lines: list[str] = []
    files: list[str] = []

    def add_file(path: str) -> None:
        if path and path not in files and side.has(path) and (repo / path).is_file():
            files.append(path)

    if exe and graph:
        # 検索時の manifest なども、更新時と同じグラフの保存先に揃える。
        env = {**os.environ, "GRAPHIFY_OUT": str(graph.parent)}
        lines += [f"### graphify {graph_cmd}", ""]
        for term in terms:
            argv = [exe, graph_cmd, term, "--graph", str(graph)]
            if graph_cmd == "query":
                argv += ["--budget", str(GRAPHIFY_BUDGET)]
            _, out = run(argv, repo, GRAPHIFY_TIMEOUT, env)
            out = "\n".join(ln for ln in out.splitlines() if not ln.startswith("[graphify] note")
                            and all(side.has(m.group(1) or m.group(2)) for m in _GRAPHIFY_SRC.finditer(ln)))
            lines += [f"#### {term}", "", "```", out.strip() or "(該当なし)", "```", ""]
            for m in _GRAPHIFY_SRC.finditer(out):
                add_file(m.group(1) or m.group(2))

    lines += ["### 文字列の一致（git grep）", ""]
    for term in terms:
        # --untracked: まだコミットしていない新しいファイルも拾う（.gitignore に載っているものは除く）。
        # 識別子は語単位（-w）で引く。`hello` で `helloWorld` を拾って影響範囲を水増ししない。
        word = ["-w"] if _WORDLIKE.match(term) else []
        hits = per_file(git_grep(side, ["--untracked", "-n", "-I", "-i", "-F", *word, "-e", term]))[:GREP_LINES_PER_TERM]
        lines += [f"#### {term}", "", *([f"- {h[:200]}" for h in hits] or ["- (該当なし)"]), ""]
        for h in hits:
            rel, _, rest = h.partition(":")
            add_file(rel)
            num = rest.split(":", 1)[0]
            if first_lines is not None and num.isdigit():
                first_lines[rel] = min(first_lines.get(rel, int(num)), int(num))
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
    log = explore_log(ctx)
    for r in targets:
        grep_hits: dict[str, int] = {}
        body, found, note = search(ctx, r, terms, "query", first_lines=grep_hits)
        parts.append((f"{r.name}（{r.label}）  {r.path}{scope_words(r)}", note, body))
        files += [f"{r.name}:{p}" if many else p for p in found]
        notes.append(f"{r.name}={note}" if many else note)
        # 文字列の一致で見つかったファイルを控える（graphify は関係の近いものまで広く拾うので、検査には使わない）。
        # 計画の検査は、探したこと（参照先ごと）と、ここに控えたファイルを計画が扱ったかを確かめる。
        hits = [p for p in found if p in grep_hits][:MAX_MEASURED]
        entry = log.setdefault(r.name, {"terms": [], "files": []})
        entry["terms"] = unique([*entry["terms"], *terms])
        entry["files"] = [p for p in unique_paths([*entry["files"], *hits]) if r.has(p)]
        all_lines = {**entry.get("lines", {}), **{p: grep_hits[p] for p in hits}}
        entry["lines"] = {p: line for p, line in all_lines.items() if p in entry["files"]}
    path = write_report(ctx, "explore.md", "参照先で関係する箇所", terms, parts, files)
    # 一致した行をそのまま出す（関係はこの行で判断し、関係しそうなものだけ前後を開く）。
    shown = 0
    for r in targets:
        lines = log.get(r.name, {}).get("lines", {})
        for rel in log.get(r.name, {}).get("files", []):
            if rel not in lines or shown >= MAX_MEASURED:
                continue
            print(f"- {ref_label(ctx, r.name, rel)}:{lines[rel]}  {snippet(r.path / rel, lines[rel], terms)}")
            shown += 1
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / EXPLORE_LOG).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FOUND {len(files)} files (graphify: {', '.join(notes)})")
    print(f"  詳細: {path.relative_to(ctx.root)}")
    return 0


def snippet(path: Path, line: int, terms: list[str], width: int = 100) -> str:
    """ファイルのその行を、一致した語のまわりだけ短くして返す（全文を開かずに関係を判断させる）。"""
    text = (read_text(path) or "").splitlines()
    body = text[line - 1].strip() if 0 < line <= len(text) else ""
    low = body.lower()
    pos = min([i for i in (low.find(t.lower()) for t in terms) if i >= 0] or [0])
    start = max(0, pos - 30)
    return ("…" if start else "") + body[start:start + width] + ("…" if len(body) > start + width else "")


EXPLORE_LOG = "explore.json"
RULES_READ = "rules-read.json"


def explore_log(ctx: Ctx) -> dict:
    path = ctx.data / EXPLORE_LOG
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def explore_problems(ctx: Ctx, bodies: dict[str, str], pending: "Pending") -> list[str]:
    """参照先を探したか（参照先ごと）。探して見つかったのに計画に無いファイルは、一致した行を添えて pending へ。"""
    log = explore_log(ctx)
    unexplored = [r.name for r in ctx.refs if r.name not in log]
    if unexplored:
        return [f"参照先を探していません: {', '.join(unexplored)}（`python3 {MACHINE_REL}/codd.py explore --term 語` で、"
                "やりたいことに関係する語から探し、一致した行で見つかったファイルを扱ってください）"]
    cited = cited_anywhere(ctx, bodies)
    missing = [(name, rel, entry.get("lines", {}).get(rel)) for name, entry in log.items()
               if name in {r.name for r in ctx.refs}
               for rel in entry.get("files", []) if (name, rel) not in cited and ctx.ref(name).has(rel)
               and (ctx.ref(name).path / rel).is_file()]
    for name, rel, line in missing:
        pending.ref(ctx, name, rel, "探して見つかった", line, log[name].get("terms", []))
    return []


def rule_digest(ctx: Ctx, name: str, rel: str) -> str:
    repo = ctx.ref(name).path if name else ctx.root
    try:
        return hashlib.sha256((repo / rel).read_bytes()).hexdigest()
    except OSError:
        return ""


def cmd_rule(ctx: Ctx, args: argparse.Namespace) -> int:
    """守る決まりのファイルを出して読み込ませ、読み込んだことを控える（計画の検査が確かめる）。"""
    rules = ctx.rule_files()
    labels = {(f"{n}:{r}" if n else r): (n, r) for n, r in rules}
    wanted = list(labels) if args.all or not args.path else args.path
    unknown = [w for w in wanted if w not in labels and not any(r == w for _, r in rules)]
    log = rules_read(ctx)
    for want in wanted:
        if want in unknown:
            continue
        name, rel = labels.get(want) or next((n, r) for n, r in rules if r == want)
        label = f"{name}:{rel}" if name else rel
        repo = ctx.ref(name).path if name else ctx.root
        # 練り直しで何度も呼ばれるので、この回で読み込み済みで中身も変わっていないものは出し直さない。
        if not args.again and log.get(label) == rule_digest(ctx, name, rel):
            print(f"# 守る決まり {label}（この回で読み込み済み・変わっていない。出し直すときは --again）\n")
            continue
        print(f"# 守る決まり {label}\n")
        print(read_text(repo / rel) or "（読めませんでした）")
        log[label] = rule_digest(ctx, name, rel)
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / RULES_READ).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not rules:
        print("守る決まりのファイルはありません")
    if unknown:
        print(f"守る決まりにありません: {', '.join(unknown)}（`codd.py show` の一覧から選んでください）", file=sys.stderr)
        return 1
    return 0


def rules_read(ctx: Ctx) -> dict:
    path = ctx.data / RULES_READ
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def unread_rules(ctx: Ctx) -> list[str]:
    """読み込んでいない（か、読み込んだあとに中身が変わった）決まりのファイル。"""
    log = rules_read(ctx)
    out = []
    for name, rel in ctx.rule_files():
        label = f"{name}:{rel}" if name else rel
        if log.get(label) != rule_digest(ctx, name, rel):
            out.append(label)
    return out


def clear_reading_logs(ctx: Ctx) -> None:
    """1 回の実行の終わりに、探した・読んだ記録を消す（次の回で使い回させない）。"""
    for name in (EXPLORE_LOG, RULES_READ, SKILLS_READ, AUTO_NAME):
        (ctx.data / name).unlink(missing_ok=True)


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
    diff = diff_without(run(["git", "diff", "HEAD", *side.pathspec()], repo, GIT_TIMEOUT)[1], lambda p: not side.has(p))
    terms = []
    quoted: dict[str, Counter] = {"+": Counter(), "-": Counter()}
    for line in diff.splitlines():
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        for pat in _DIFF_TERMS:
            m = pat.match(line)
            if m:
                terms.append(m.group(1))
        quoted[line[0]].update(_BACKTICK.findall(line))
    # 直した行に元からある `…` は変わった名前ではない（足した・消した側で数が違うものだけ拾う）。
    terms += [t for t in quoted["+"] | quoted["-"] if quoted["+"][t] != quoted["-"][t]]
    for name in run(["git", "ls-files", "--others", "--exclude-standard", *side.pathspec()],
                    repo, GIT_TIMEOUT)[1].splitlines():
        if not side.has(name):
            continue
        try:
            text = (repo / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        terms += [m.group(1) for ln in text.splitlines() for pat in _DIFF_TERMS
                  for m in [pat.match("+" + ln)] if m]
    return unique(terms)


def diff_by_file(side: Side) -> dict[str, tuple[list[str], list[str]]]:
    """作業中の差分（新しいファイルも）を、ファイルごとの（足した行, 消した行）に分ける。"""
    diff = run(["git", "diff", "HEAD", *side.pathspec()], side.path, GIT_TIMEOUT)[1]
    out: dict[str, tuple[list[str], list[str]]] = {}
    cur, header = None, False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur, header = None, True
        elif header and line.startswith("+++ "):
            path = line[4:]
            cur = path[2:] if path.startswith("b/") and side.has(path[2:]) else None
        elif line.startswith("@@"):
            header = False
        elif not header and cur and line[:1] in "+-":
            out.setdefault(cur, ([], []))[0 if line[0] == "+" else 1].append(line[1:])
    for name in run(["git", "ls-files", "--others", "--exclude-standard", *side.pathspec()],
                    side.path, GIT_TIMEOUT)[1].splitlines():
        if side.has(name):
            try:
                out[name] = ((side.path / name).read_text(encoding="utf-8").splitlines(), [])
            except (OSError, UnicodeDecodeError):
                continue
    return out


# 文字列の値（画面の文言・URL・メッセージ）。e2e のケースはコードの名前ではなく、こうした文字列で書かれる。
_LITERAL = re.compile(r"""(["'`])((?:(?!\1)[^\\\n]){3,60})\1""")
_LETTER = re.compile(r"[^\W\d_]")


def literals_from_diff(ctx: Ctx, key: str, side: Side) -> list[str]:
    """コードの変更で足した・消した文字列（テスト・文書のファイルは除く）。"""
    counts: dict[str, Counter] = {"+": Counter(), "-": Counter()}
    for rel, (plus, minus) in diff_by_file(side).items():
        if is_test(ctx, key, rel) or rel.lower().endswith(DOC_EXTS):
            continue
        for mark, lines in (("+", plus), ("-", minus)):
            counts[mark].update(m.group(2).strip() for ln in lines for m in _LITERAL.finditer(ln))
    return unique(t for t in counts["+"] | counts["-"]
                  if counts["+"][t] != counts["-"][t] and _LETTER.search(t) and "${" not in t)


# 新しく足した名前（テストで確かめるもの）。コードは定義（export しない const・let・var は中の値なので除く）、文書は見出し。
_NEW_CODE_NAMES = (*_DIFF_TERMS[:3], re.compile(r"^[+-]\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]{2,})\s*="),
                   _DIFF_TERMS[4])
_NEW_DOC_NAMES = (_DIFF_TERMS[5],)


def grep_word(term: str) -> list[str]:
    return ["-w"] if _WORDLIKE.match(term) else []


def existed_at_head(side: Side, term: str) -> bool:
    rc, out = run(["git", "grep", "-l", "-I", "-F", *grep_word(term), "-e", term, "HEAD", *side.pathspec()],
                  side.path, GIT_TIMEOUT)
    return rc == 0 and any(side.has(ln.split(":", 1)[1]) for ln in out.splitlines() if ":" in ln)


def added_names(ctx: Ctx, key: str, side: Side) -> dict[str, str]:
    """この回の変更で新しく足した名前（関数・型・文書の見出し）と、そのファイル。前からある名前は除く。"""
    names: dict[str, str] = {}
    for rel, (plus, minus) in diff_by_file(side).items():
        if is_test(ctx, key, rel):
            continue
        pats = _NEW_DOC_NAMES if rel.lower().endswith(DOC_EXTS) else _NEW_CODE_NAMES

        def grab(lines: list[str]) -> set[str]:
            return {m.group(1).strip() for ln in lines for pat in pats for m in [pat.match("+" + ln)] if m}
        for name in sorted(grab(plus) - grab(minus)):
            if not name.startswith("_"):
                names.setdefault(name, rel)
    # 別の側に前からある名前（実装済みの関数を仕様書に書き足した、など）も新しい振る舞いではない。
    return {n: rel for n, rel in names.items() if not any(existed_at_head(s, n) for _, s in all_sides(ctx))}


def tested_anywhere(ctx: Ctx, term: str) -> bool:
    """どこかの側のテストのファイルに、その名前が書かれているか。"""
    for key, side in all_sides(ctx):
        if test_patterns(ctx, key) and any(
                is_test(ctx, key, rel)
                for rel in git_grep(side, ["--untracked", "-l", "-I", "-F", *grep_word(term), "-e", term])):
            return True
    return False


def measure(ctx: Ctx, terms: list[str], name: str, title: str) -> list[str]:
    """変わる名前から、自分のリポジトリで影響を受けるファイルを測る（graphify affected + git grep）。"""
    terms = terms[:MAX_TERMS]
    body, files, note = search(ctx, ctx.own, terms, "affected")
    files = files[:MAX_MEASURED]
    write_report(ctx, name, title, terms, [(f"{ctx.root}{scope_words(ctx.own)}", note, body)], files)
    return files


def measure_refs(ctx: Ctx, terms: list[str], name: str, title: str,
                 lines: dict[str, dict[str, int]] | None = None) -> set[tuple[str, str]]:
    """自分の変更で変わる名前に、参照先のどのファイルが触れているかを測る（git grep。語単位）。

    グラフの query は関係の近いものまで広く拾うので、漏れの検査には文字列の一致だけを使う。
    """
    terms = terms[:MAX_TERMS]
    parts, files, found = [], [], set()
    for r in ctx.refs:
        first: dict[str, int] = {}
        body, hits, _ = search(ctx, r, terms, "query", use_graph=False, first_lines=first)
        if lines is not None:
            lines[r.name] = first
        parts.append((f"{r.name}（{r.label}）  {r.path}{scope_words(r)}", "（文字列の一致だけで測る）", body))
        for rel in hits[:MAX_MEASURED]:
            found.add((r.name, rel))
            files.append(f"{r.name}:{rel}" if len(ctx.refs) > 1 else rel)
    write_report(ctx, name, title, terms, parts, files)
    return found


# 「変えない」と読める言い回し。変更不要の判断は `変更不要: 理由` の 1 つの書き方に寄せる（計画の検査と変えたあとの
# 検査が同じ項目を同じに読むため）。ほかの言い回しは計画の検査で書き直させる。
_NOT_CHANGING = re.compile(r"対応不要|修正不要|変更なし|変更無し|変更しない|直さない|直す必要(?:は)?(?:ない|無い)|"
                           r"手を(?:付け|入れ)ない|影響(?:は)?(?:なし|無し|ない|無い)|対象外|そのまま(?:で)?(?:よい|良い)")
_NO_CHANGE_LEAD = re.compile(rf"^[-*]?\s*{NO_CHANGE_MARK}\s*[:：]\s*")
_COMMAND_CODE = re.compile(r"`[^`]*\s[^`]*`")


@dataclass
class Judgment:
    """影響範囲・テストの変更案の 1 項目の読み方。計画の検査と変えたあとの検査は、どちらもこれで読む。"""
    waived: bool        # 変更不要
    targets: str        # 対象として明示したパス（`, ` 区切り）
    reason: str         # 変更不要の理由（変更不要でなければ ""）


def judgment(item: str) -> Judgment:
    """項目を読む。書き方は 2 つ: `- パス, パス — 変更不要: 理由` と `- 変更不要: パス, パス — 理由`。
    対象は項目の先頭（変更不要: の後ろ）に並べたパスだけで、理由の中のパスは対象に数えない。
    パスの無い `- 変更不要: 理由` は、響くものが無いときの理由だけの項目。"""
    plain = _COMMAND_CODE.sub("", item)
    if NO_CHANGE_MARK not in plain:
        return Judgment(False, test_item_targets(item), "")
    lead = _NO_CHANGE_LEAD.match(item.strip())
    if lead:
        rest = item.strip()[lead.end():]
        targets = test_item_targets(rest)
        head, dash, tail = rest.partition("—")
        if dash and targets:
            reason = tail
        else:
            # 対象の後ろ（無ければ全体）が理由。`変更不要: src/a.py は使わない` は src/a.py と、その理由。
            reason = _COMMAND_CODE.sub("", rest)
            for t in targets.split(", ") if targets else []:
                reason = reason.replace(t, "", 1)
    else:
        targets = test_item_targets(item)
        reason = plain.split(NO_CHANGE_MARK, 1)[1]
    return Judgment(True, targets, _COMMAND_CODE.sub("", reason).strip(" :：—-、,（）()。．`\t"))


def judgment_problems(heading: str, body: str) -> list[str]:
    """影響範囲・テストの変更案の書き方の誤り（変更不要の理由が無い、ほかの言い回しで変えないと書いた）。"""
    if is_none(body):
        return []
    out = []
    for item in items(body):
        if PENDING_MARK in item:
            continue
        j = judgment(item)
        if j.waived and not j.reason:
            out.append(f"{heading} の変更不要の後に理由がありません: {item[:80]}")
        elif not j.waived and _NOT_CHANGING.search(_COMMAND_CODE.sub("", item)):
            out.append(f"{heading} の項目は変えないという判断に読めます。変えないなら `- パス — {NO_CHANGE_MARK}: 理由` と"
                       f"書いてください（変えたあとの検査は、この書き方だけを変更不要と読みます）: {item[:80]}")
    return out


def listed_paths(ctx: Ctx, body: str, only_no_change: bool = False, skip_no_change: bool = False,
                 allow_new: bool = False) -> set[str]:
    paths: set[str] = set()
    for item in items(body) or [body]:
        j = judgment(item)
        if only_no_change and not j.waived:
            continue
        if skip_no_change and (j.waived or PENDING_MARK in item):
            continue
        # 変更不要の項目は、先頭に並べた対象だけ（理由の中のパスまで変更不要にしない）
        paths.update(cited_own(ctx, j.targets if j.waived else item, allow_new))
    return paths


def cited_anywhere(ctx: Ctx, bodies: dict[str, str]) -> set[tuple[str, str]]:
    """計画のどこかで根拠・変更案として挙げた参照先のファイル。"""
    found: set[tuple[str, str]] = set()
    for heading in (*CITED_IN_REFS, "## 参照先の変更案"):
        for item in items(bodies.get(heading, "")):
            found |= cited_refs(ctx, item, allow_new=True).found
    return found | {(k, rel) for k, rel in test_plan(ctx, bodies).listed() if k}


def ref_label(ctx: Ctx, name: str, rel: str) -> str:
    return f"{name}:{rel}" if len(ctx.refs) > 1 else rel


# ---------------------------------------------------------------- つながり（ファイルが指すパス）

# ファイルが別のファイルを指す書き方。どのファイルにも書ける注記 `coherence: doc=パス`（code・test も）と、
# 文書（マークダウンなど）の `…` で囲んだパスと、リンク [文字](パス)。文書のコードブロックと `…` の中の注記は例として拾わない。
_ANNOT = re.compile(r"coherence:\s*(?:doc|code|test)\s*=\s*([^\s`\"'<>]+)")
_INLINE_PATH = re.compile(r"`([^`\n]{2,200})`")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)#?\s]+)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_NOT_PATH_CHARS = set(" \t|$&;<>\"'*?{}()=,")
DOC_EXTS = (".md", ".markdown", ".rst", ".adoc", ".txt")
PATH_EXTS = {
    *DOC_EXTS, ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala", ".sh", ".ps1", ".sql", ".vue", ".svelte",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".html", ".css", ".example",
}
MAX_TRACE_FILES = 200
MAX_READ_BYTES = 1_000_000


@dataclass
class Claim:
    line: int      # 1 始まり
    token: str     # 書かれたまま（`名前:` は付いたまま）
    kind: str      # annot / code / link


def claims_in(rel: str, text: str) -> list[Claim]:
    """ファイルが指しているパスを拾う。"""
    is_doc = rel.lower().endswith(DOC_EXTS)
    out: list[Claim] = []
    fenced = False
    for no, line in enumerate(text.splitlines(), 1):
        if not is_doc:
            out += [Claim(no, m.group(1).rstrip(".,;:）)-"), "annot") for m in _ANNOT.finditer(line)]
            continue
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        # 文書では、`…` の中の注記は書き方の例として扱う（<!-- coherence: … --> のように地の文に書いたものだけ拾う）。
        out += [Claim(no, m.group(1).rstrip(".,;:）)-"), "annot") for m in _ANNOT.finditer(_INLINE_PATH.sub("", line))]
        for m in _INLINE_PATH.finditer(line):
            if "coherence:" not in m.group(1) and pathlike(m.group(1)):
                out.append(Claim(no, m.group(1).strip(), "code"))
        out += [Claim(no, m.group(1), "link") for m in _MD_LINK.finditer(_INLINE_CODE.sub("", line))
                if pathlike(m.group(1))]   # `[説明](パス)` のように `…` の中のリンクは書き方の例
    return out


def pathlike(token: str) -> bool:
    t = token.strip()
    t = t[2:] if t.startswith("./") else t
    if not t or "://" in t or t.startswith(("#", "/", "~", "-")) or t.startswith("mailto:"):
        return False
    if any(c in _NOT_PATH_CHARS for c in t):
        return False
    bare = re.sub(r"(?::\d+(?:-\d+)?)?(?:#.*)?$", "", t)
    if "/" in bare:
        return True
    return Path(bare.split(":", 1)[-1]).suffix.lower() in PATH_EXTS


@dataclass
class Resolved:
    exists: bool                                          # どこかの側に実在する
    hits: set[tuple[str, str]] = field(default_factory=set)   # （側の名前。自分は ""、パス）で、その側の scope の中
    candidates: list[str] = field(default_factory=list)       # 試したパス


def all_sides(ctx: Ctx) -> list[tuple[str, Side]]:
    return [("", ctx.own), *((r.name, r) for r in ctx.refs)]


def resolve(ctx: Ctx, from_side: Side, from_rel: str, claim: Claim) -> Resolved:
    """指しているパスが、どの側のどのファイルかを決める。書いた側のリポジトリにあればそれを採る。"""
    token = re.sub(r"(?::\d+(?:-\d+)?)?(?:#.*)?$", "", claim.token.strip())
    sides = all_sides(ctx)
    m = re.match(r"^([A-Za-z0-9_.-]+):(.+)$", token)
    if m and m.group(1) in {r.name for r in ctx.refs}:
        sides = [(m.group(1), ctx.ref(m.group(1)))]
        token = m.group(2)
    token = token[2:] if token.startswith("./") else token
    cands = [os.path.normpath(os.path.join(os.path.dirname(from_rel), token)).replace("\\", "/")]
    cands = cands + [token.rstrip("/")] if claim.kind == "link" else [token.rstrip("/"), *cands]
    cands = unique_paths(c for c in cands if c and c != "." and not c.startswith("../"))
    out = Resolved(False, candidates=cands)
    near = [(k, s) for k, s in sides if s.path == from_side.path]
    far = [(k, s) for k, s in sides if s.path != from_side.path]
    for group in (near, far):
        for rel in cands:
            repos = {s.path for _, s in group if (s.path / rel).exists()}
            if not repos:
                continue
            out.exists = True
            out.hits |= {(k, rel) for k, s in group if s.path in repos and s.has(rel) and (s.path / rel).is_file()}
        if out.exists:
            return out
    return out


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def files_mentioning(side: Side, words: list[str]) -> list[str]:
    """scope の中で、どれかの語（ファイル名）を含むファイル（未追跡も）。"""
    words = unique(words)
    if not words:
        return []
    args = [a for w in words for a in ("-e", w)]
    return git_grep(side, ["--untracked", "-l", "-I", "-F", *args])[:MAX_TRACE_FILES]


def linked(ctx: Ctx, key: str, rels: set[str]) -> set[tuple[str, str]]:
    """ある側のファイルとつながっている、ほかの側のファイル（どちらが指していてもよい）。"""
    side = ctx.own if not key else ctx.ref(key)
    out: set[tuple[str, str]] = set()
    for rel in sorted(rels):
        text = read_text(side.path / rel) if (side.path / rel).is_file() else None
        for claim in claims_in(rel, text or ""):
            out |= {h for h in resolve(ctx, side, rel, claim).hits if h[0] != key}
    names = [Path(rel).name for rel in rels if Path(rel).name]
    for other_key, other in all_sides(ctx):
        if other_key == key:
            continue
        for rel in files_mentioning(other, names):
            text = read_text(other.path / rel)
            for claim in claims_in(rel, text or ""):
                if any(h[0] == key and covered(h[1], rels) for h in resolve(ctx, other, rel, claim).hits):
                    out.add((other_key, rel))
                    break
    return out


def side_label(ctx: Ctx, key: str, rel: str) -> str:
    return rel if not key else ref_label(ctx, key, rel)


def write_trace(ctx: Ctx, name: str, title: str, groups: list[tuple[str, list[str]]]) -> None:
    ctx.data.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    for heading, rows in groups:
        lines += [f"## {heading}", "", *([f"- {r}" for r in rows] or ["- なし"]), ""]
    (ctx.data / name).write_text("\n".join(lines), encoding="utf-8")


def added_lines(side: Side, rel: str) -> set[int] | None:
    """作業中に足した行の番号（未追跡のファイルなら None = すべて）。"""
    rc, out = run(["git", "diff", "HEAD", "-U0", "--", rel], side.path, GIT_TIMEOUT)
    if rc != 0 or not out.strip():
        tracked = run(["git", "ls-files", "--error-unmatch", "--", rel], side.path, GIT_TIMEOUT)[0] == 0
        return set() if tracked else None
    nums: set[int] = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", out, re.MULTILINE):
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        nums.update(range(start, start + count))
    return nums


def broken_refs(ctx: Ctx, touched: dict[str, set[str]]) -> list[str]:
    """変えたファイルに書き足したパスのうち、どの側にも無いもの。"""
    out = []
    for key, side in all_sides(ctx):
        for rel in sorted(touched.get(key, set())):
            text = read_text(side.path / rel) if (side.path / rel).is_file() else None
            if text is None:
                continue
            added = added_lines(side, rel)
            for claim in claims_in(rel, text):
                if added is not None and claim.line not in added:
                    continue
                if claim.kind == "code" and not intended_path(ctx, claim.token):
                    continue  # `path/to/x.md` のような例は、実在するフォルダで始まるときだけパスとみなす
                r = resolve(ctx, side, rel, claim)
                if r.candidates and not r.exists:
                    out.append(f"{side_label(ctx, key, rel)}:{claim.line} → {claim.token}")
    return out


def intended_path(ctx: Ctx, token: str) -> bool:
    token = token.split(":", 1)[-1] if re.match(r"^[A-Za-z0-9_.-]+:[^\d]", token) else token
    token = token[2:] if token.startswith("./") else token
    first = token.split("/", 1)[0]
    return "/" in token and any((s.path / first).is_dir() for _, s in all_sides(ctx))


def dangling_refs(ctx: Ctx, touched: dict[str, set[str]]) -> list[str]:
    """消したファイルを、まだ指しているファイル。"""
    gone = {(key, rel) for key, side in all_sides(ctx) for rel in touched.get(key, set())
            if not (side.path / rel).exists()}
    if not gone:
        return []
    names = [Path(rel).name for _, rel in gone]
    out = []
    for key, side in all_sides(ctx):
        for rel in files_mentioning(side, names):
            text = read_text(side.path / rel)
            for claim in claims_in(rel, text or ""):
                r = resolve(ctx, side, rel, claim)
                if r.exists:
                    continue
                prefix = claim.token.split(":", 1)[0] if ":" in claim.token else None
                for gkey, grel in gone:
                    if grel in r.candidates and (prefix not in {x.name for x in ctx.refs} or prefix == gkey):
                        out.append(f"{side_label(ctx, key, rel)}:{claim.line} → {side_label(ctx, gkey, grel)}")
    return sorted(set(out))


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


def files_to_change(ctx: Ctx, bodies: dict[str, str], planned: dict[str, set[str]]) -> list[tuple[str, str]]:
    """計画で変えるファイル（側の名前。自分は ""、パス）。変える順（自分の変更案・参照先の変更案・影響範囲・テスト）に、
    同じ見出しの中はパスの順（同じフォルダが続く）に並べる。「変更不要」は数えない。"""
    out: list[tuple[str, str]] = []

    def add(group) -> None:
        for key in sorted(group, key=lambda kr: (kr[0], kr[1])):
            if key not in out:
                out.append(key)
    add({("", rel) for rel in listed_paths(ctx, bodies.get("## 自分の変更案", ""), allow_new=True)})
    add({(k, rel) for k, rels in planned.items() for rel in rels})
    add({("", rel) for rel in listed_paths(ctx, bodies.get("## 影響範囲", ""), skip_no_change=True)})
    add(test_plan(ctx, bodies).change)
    return out


def plan_batches(ctx: Ctx, bodies: dict[str, str]) -> list[list[tuple[str, str]]]:
    files = files_to_change(ctx, bodies, planned_refs(ctx, bodies)[0])
    return [files[i:i + ctx.batch_files] for i in range(0, len(files), ctx.batch_files)]


def plan_budget(ctx: Ctx, bodies: dict[str, str], planned: dict[str, set[str]]) -> list[str]:
    """1 回で変えるファイルが max_files に収まっているか（1 セッションで終わる大きさに保つ）。"""
    total = len(files_to_change(ctx, bodies, planned))
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
    unread = unread_rules(ctx)
    if unread:
        return [f"守る決まりのファイルを読み込んでいません（`python3 {MACHINE_REL}/codd.py rule --all` で読み込んでから、"
                "効く決まりを書いてください。読み込んだあとに変わったものも読み直す）: " + ", ".join(unread)]
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
    problems += test_plan(ctx, bodies).problems
    problems += judgment_problems("## 影響範囲", bodies["## 影響範囲"])
    problems += [p for p in judgment_problems(TESTS_HEADING, bodies[TESTS_HEADING]) if "理由がありません" not in p]
    problems += rules_problems(ctx, bodies["## 守る決まり"])
    problems += used_problems(bodies["## 使ったスキルと道具"],
                              ctx.config["skills"]["plan"] + ctx.config["tools"]["plan"], "使ったスキルと道具")
    configured = set(ctx.config["skills"]["plan"])
    # リポジトリのスキルは、使ったものだけを書く（使わないものを 1 つずつ断らせない。記録に要らない）。
    found = [s.name for s in repo_skills(ctx.root, ctx.config["skill_dirs"]) if s.name not in configured]
    used = used_skill_names(bodies["## 使ったスキルと道具"], unique([*ctx.config["skills"]["plan"], *found]))
    problems += unread_problem(unread_skills(ctx, used), "使ったスキルと道具に挙げた")
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


class Pending:
    """測ったのに計画に無かったファイル。検査が見出しごとに「未判断」の項目として計画へ書き足す。

    利用者もエージェントも、測った結果のファイル（impact.md など）を開かずに、計画の上で 1 行ずつ判断できる。
    """

    def __init__(self) -> None:
        self.lines: dict[str, list[str]] = {}
        self.keys: set = set()

    def add(self, heading: str, key, line: str) -> None:
        """同じファイルは 1 度だけ（いくつもの測り方で見つかっても、最初の理由で 1 行）。"""
        if key in self.keys:
            return
        self.keys.add(key)
        self.lines.setdefault(heading, []).append(line)

    def own(self, ctx: Ctx, rel: str, why: str) -> None:
        # 自分のテストのファイルは、影響範囲ではなくテストの変更案で判断する
        heading = TESTS_HEADING if tests_enabled(ctx) and is_test(ctx, "", rel) else "## 影響範囲"
        self.add(heading, ("", rel), f"- {rel} — {PENDING_MARK}（{why}）")

    def ref(self, ctx: Ctx, name: str, rel: str, why: str, line: int | None = None,
            terms: list[str] | None = None) -> None:
        # 一致した行を添える。たいていはこの行だけで関係を判断でき、ファイルを開かずに済む
        # （` は外す。計画の検査が、添えた行の中の名前やパスを根拠として読まないように）
        seen = f" 「{snippet(ctx.ref(name).path / rel, line, terms or [], 60).replace('`', '')}」" if line else ""
        if tests_enabled(ctx) and is_test(ctx, name, rel):
            # 参照先のテストも、根拠ではなくテストの変更案で判断する（足す・直す・変更不要）
            self.add(TESTS_HEADING, (name, rel), f"- {ref_label(ctx, name, rel)} — {PENDING_MARK}（{why}）{seen}")
            return
        self.add("## 参照先のその他", (name, rel),
                 f"- {PENDING_MARK}: {ref_label(ctx, name, rel)}{f':{line}' if line else ''}（{why}）{seen}")

    def count(self) -> int:
        return sum(len(v) for v in self.lines.values())


def add_pending(text: str, pending: Pending) -> str:
    """計画の見出しの終わりに「未判断」の項目を書き足す。中身が「なし」なら置き換える。"""
    for heading, new in pending.lines.items():
        m = re.search(rf"^{re.escape(heading)}\s*$", text, re.MULTILINE)
        if not m:
            continue
        nxt = re.search(r"^## ", text[m.end():], re.MULTILINE)
        end = m.end() + nxt.start() if nxt else len(text)
        body = text[m.end():end]
        plain = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        kept = re.sub(r"^\s*[-*]?\s*(なし|無し)[。．]?\s*$\n?", "", body, flags=re.MULTILINE) if is_none(plain) else body
        kept = kept.rstrip()
        text = text[:m.end()] + (kept + "\n" if kept else "\n") + "\n".join(new) + "\n\n" + text[end:].lstrip("\n")
    return text.rstrip("\n") + "\n"


def pending_items(bodies: dict[str, str]) -> list[tuple[str, str]]:
    """計画に残っている「未判断」の項目（見出し, 項目）。"""
    return [(h, it) for h, body in bodies.items() for it in items(body) if PENDING_MARK in it]


def pending_problem(found: list[tuple[str, str]], added: int = 0) -> list[str]:
    if not found:
        return []
    heads = "・".join(f"{h[3:]} {n}" for h in unique([h for h, _ in found])
                      for n in [sum(1 for x, _ in found if x == h)])
    lead = (f"測ったのに計画に無かったファイル {added} 件を、計画に「{PENDING_MARK}」として書き足しました。"
            if added else f"計画に「{PENDING_MARK}」の項目が {len(found)} 件残っています。")
    return [lead + f"（{heads}）各行を判断に書き換えてください: 自分のファイルは直し方か「{NO_CHANGE_MARK}: 理由」、"
            "参照先のファイルは示した行の前後だけを読み、前提・制約・ずれの根拠に移すか「関係なし: 理由」、"
            "書式の見本は守り方。ファイル全体は読まなくてよい: "
            + ", ".join(unique(re.split(r"（| — ", it.lstrip("-* ").replace(f"{PENDING_MARK}: ", ""), maxsplit=1)[0].strip()
                               for _, it in found))]


def measure_plan(ctx: Ctx, bodies: dict[str, str], pending: Pending) -> tuple[list[str], list[str], int]:
    """計画の名前から影響を測る。（問題, 自分で測ったファイル, 参照先で測った数）

    測ったのに計画に無いファイルは問題にせず pending へ集める（検査が計画に「未判断」として書き足す）。
    """
    problems: list[str] = []
    own_terms = own_terms_from_plan(bodies)
    terms = unique(own_terms + terms_from_plan(bodies))
    measured: list[str] = []
    if terms:
        # 自分の変更と参照先の変更で動く名前が、自分のどこに響くか。変えるか「変更不要」と書くか。
        measured = measure(ctx, terms, "impact.md",
                           f"計画の変更が自分のリポジトリ（{SIDES[ctx.side]}）に響く範囲（測定）")
        listed = (listed_paths(ctx, bodies["## 自分の変更案"], allow_new=True)
                  | listed_paths(ctx, bodies["## 影響範囲"]) | test_plan(ctx, bodies).paths(""))
        for p in measured:
            if not covered(p, listed):
                pending.own(ctx, p, "変わる名前が出てくる")
    ref_hits: set[tuple[str, str]] = set()
    if own_terms:
        # 自分の変更で動く名前に触れている参照先のファイルを、計画が読んで扱っているか（逆向きの漏れ）。
        ref_lines: dict[str, dict[str, int]] = {}
        ref_hits = measure_refs(ctx, own_terms, "ref-impact.md", "自分の変更で動く名前に触れている参照先のファイル（測定）",
                                ref_lines)
        cited = cited_anywhere(ctx, bodies)
        for n, r in sorted(ref_hits):
            if (n, r) not in cited:
                pending.ref(ctx, n, r, "自分の変更で変わる名前が出てくる", ref_lines.get(n, {}).get(r), own_terms)
    problems += explore_problems(ctx, bodies, pending)
    problems += trace_plan(ctx, bodies, pending)
    problems += tests_plan_problems(ctx, bodies, terms, pending)
    problems += formats_plan_problems(ctx, bodies, pending)
    return problems, measured, len(ref_hits)


def trace_plan(ctx: Ctx, bodies: dict[str, str], pending: Pending) -> list[str]:
    """計画で変えるファイルとつながっている（互いにパスで指している）ほかの側のファイルのうち、計画に無いものを pending へ。"""
    own_plan = own_planned(ctx, bodies)
    planned, _ = planned_refs(ctx, bodies)
    ref_links = sorted(h for h in linked(ctx, "", own_plan) if h[0]) if own_plan else []
    cited = cited_anywhere(ctx, bodies)
    listed = (listed_paths(ctx, bodies.get("## 自分の変更案", ""), allow_new=True)
              | listed_paths(ctx, bodies.get("## 影響範囲", "")) | test_plan(ctx, bodies).paths(""))
    own_links = sorted({rel for name, rels in planned.items() for k, rel in linked(ctx, name, rels) if not k})
    missing_own = [p for p in own_links if not covered(p, listed)]
    write_trace(ctx, "trace.md", "計画で変えるファイルとつながっているファイル（パスで指し合っているもの）", [
        ("自分の変更案のファイルとつながっている参照先のファイル", [ref_label(ctx, n, r) for n, r in ref_links]),
        ("参照先の変更案のファイルとつながっている自分のファイル", own_links),
    ])
    for n, r in ref_links:
        if (n, r) not in cited:
            pending.ref(ctx, n, r, "自分の変更案のファイルとパスでつながっている")
    for p in missing_own:
        pending.own(ctx, p, "参照先の変更案のファイルとパスでつながっている")
    return []


# ---------------------------------------------------------------- テスト（コード・仕様書と同じに扱う）

def glob_re(pattern: str) -> re.Pattern:
    """git の :(glob) と同じ意味の正規表現（`*` はフォルダをまたがず、`**/` はまたぐ）。"""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out, i = out + "/.*", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "[":
            end = i + 1
            if end < len(pattern) and pattern[end] in "!^":
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            end = pattern.find("]", end)
            if end == -1:
                out, i = out + r"\[", i + 1
            else:
                # fnmatch の文字クラスを再利用し、フォルダ区切りには一致させない。
                cls = fnmatch.translate(pattern[i:end + 1])[4:-3]
                out, i = out + "(?!/)" + cls, end + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def side_of(ctx: Ctx, key: str) -> Side:
    return ctx.own if not key else ctx.ref(key)


def test_patterns(ctx: Ctx, key: str) -> list[str]:
    """その側のテストのファイルの書き方。自分は codd.json の tests、参照先は参照先に置いた codd.json の tests。"""
    return ctx.config["tests"] if not key else ctx.ref(key).test_patterns


def is_test(ctx: Ctx, key: str, rel: str) -> bool:
    return any(glob_re(p).fullmatch(rel) for p in test_patterns(ctx, key))


def tests_enabled(ctx: Ctx) -> bool:
    return any(test_patterns(ctx, key) for key, _ in all_sides(ctx))


def test_files(ctx: Ctx, key: str) -> list[str]:
    side = side_of(ctx, key)
    if not test_patterns(ctx, key):
        return []
    rc, out = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", *side.pathspec()],
                  side.path, GIT_TIMEOUT)
    files = [ln for ln in out.splitlines() if side.has(ln) and is_test(ctx, key, ln)] if rc == 0 else []
    return files[:MAX_TEST_FILES]


# テストのファイル名から外す印（test_app.py・app_test.go・LoginTest.java・login.spec.ts → app・login）。
_TEST_STEM_MARKS = (re.compile(r"^tests?[_-]", re.I),
                    re.compile(r"(?:[_-](?:tests?|spec|e2e)|(?<=[a-z0-9])(?:Tests?|Spec))$"))
# ファイル名だけでは何のテストか決まらない名前。
GENERIC_STEMS = {"index", "main", "__init__", "init", "mod", "lib", "util", "utils", "common", "types", "config",
                 "conftest", "setup", "readme", "helpers", "fixtures"}
MAX_TEXTS = 12


def file_stem(rel: str, test: bool = False) -> str:
    """ファイル名の語幹（拡張子と、テストなら test・spec の印を外して小文字に）。"""
    stem = Path(rel).name.split(".", 1)[0]
    if test:
        for mark in _TEST_STEM_MARKS:
            stem = mark.sub("", stem)
    stem = stem.lower()
    return "" if len(stem) < 3 or stem in GENERIC_STEMS else stem


def affected_tests(ctx: Ctx, terms: list[str], changed: dict[str, set[str]],
                   texts: list[str] | None = None) -> dict[tuple[str, str], str]:
    """変える名前・変えるファイルが響くテストのファイル（自分と参照先。同じ側でも数える）と、その理由。

    - 名前: 変わる名前（`…`・差分の定義や見出し）がテストのファイルに書かれている
    - 文字列: 変えた文字列（画面の文言・URL など。差分の引用符の中）がテストのファイルに書かれている
    - ファイル名: テストのファイル名が、変えるファイルと同じ語幹を持つ（app.py と test_app.py、Login.tsx と login.yaml）
    - つながり: テストのファイルが変えるファイルをパスで指している（`coherence: code=…`・`doc=…` など）か、その逆

    e2e のケースは画面の文言や URL で書かれ、コードの名前が出てこないことが多い。文字列とファイル名は、
    テストの道具の書き方を知らずに e2e のケースを拾うための手がかり。
    """
    found: dict[tuple[str, str], str] = {}
    targets = {(k, rel) for k, rels in changed.items() for rel in rels}
    stems = {file_stem(rel) for k, rel in targets if not is_test(ctx, k, rel)} - {""}
    for key, side in all_sides(ctx):
        tests = set(test_files(ctx, key))
        if not tests:
            continue
        if terms:
            _, hits, _ = search(ctx, side, terms[:MAX_TERMS], "affected", use_graph=False)
            for rel in hits:
                if rel in tests and (key, rel) not in targets:
                    found.setdefault((key, rel), "名前")
        if texts:
            args = [a for t in texts[:MAX_TEXTS] for a in ("-e", t)]
            for rel in git_grep(side, ["--untracked", "-l", "-I", "-F", *args]):
                if rel in tests and (key, rel) not in targets:
                    found.setdefault((key, rel), "文字列")
        for rel in sorted(tests):
            if (key, rel) not in targets and file_stem(rel, test=True) in stems:
                found.setdefault((key, rel), "ファイル名")
        names = [Path(rel).name for _, rel in targets if Path(rel).name]
        for rel in sorted(tests & set(files_mentioning(side, names))):
            if (key, rel) in targets:
                continue
            for claim in claims_in(rel, read_text(side.path / rel) or ""):
                if any(covered(h[1], changed.get(h[0], set())) for h in resolve(ctx, side, rel, claim).hits):
                    found[(key, rel)] = "つながり"
                    break
    # 変えるファイルが指しているテスト（仕様書がケースのファイルを挙げている、など）。
    for k, rels in changed.items():
        side = side_of(ctx, k)
        for rel in rels:
            if not (side.path / rel).is_file():
                continue
            for claim in claims_in(rel, read_text(side.path / rel) or ""):
                for hk, hrel in resolve(ctx, side, rel, claim).hits:
                    if is_test(ctx, hk, hrel) and (hk, hrel) not in targets:
                        found[(hk, hrel)] = "つながり"
    return found


@dataclass
class TestPlan:
    """計画の「テストの変更案」。（側の名前。自分は ""、パス）で持つ。"""
    change: set[tuple[str, str]]
    waived: set[tuple[str, str]]
    problems: list[str]
    reason_only: bool      # パスの無い「変更不要: 理由」だけ（響くテストが無いとき）

    def listed(self) -> set[tuple[str, str]]:
        return self.change | self.waived

    def paths(self, key: str, waived: bool = True) -> set[str]:
        return {rel for k, rel in (self.listed() if waived else self.change) if k == key}


def test_item_targets(item: str) -> str:
    """項目の先頭に明示した対象だけ。説明や検索コマンドの入出力パスは拾わない。"""
    prefix = item.split("—", 1)[0].strip()
    # インラインコードがコマンドなら全体を除く（パス単独の `…` は残す）。
    prefix = re.sub(r"`[^`]*\s[^`]*`", "", prefix)
    targets = []
    rest = prefix
    while rest:
        rest = rest.lstrip(" `,、")
        match = _CITE.match(rest)
        if not match:
            break
        targets.append(match.group())
        rest = rest[match.end():]
    return ", ".join(targets)


def test_plan(ctx: Ctx, bodies: dict[str, str]) -> TestPlan:
    body = bodies.get(TESTS_HEADING, "なし")
    plan = TestPlan(set(), set(), [], False)
    if is_none(body):
        return plan
    body = re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1[^\n]*$", "", body)
    listed = items(body)
    if not listed:
        plan.problems.append(f"{TESTS_HEADING} は箇条書きにしてください")
    for item in listed:
        # 理由はコマンドの文字列で代用させない。
        j = judgment(item)
        waived = j.waived
        if waived and not j.reason:
            plan.problems.append(f"{TESTS_HEADING} の変更不要の後に理由がありません: {item[:80]}")
            continue
        targets = j.targets
        hits = {("", rel) for rel in cited_own(ctx, targets, allow_new=True)}
        cited = cited_refs(ctx, targets, allow_new=True)
        hits |= cited.found
        if not hits:
            if waived:
                plan.reason_only = True
                continue
            if re.match(r"^(?:検索(?:コマンド|結果)?[：: ]|`?(?:rg|grep|find|git\s+grep)\s)", item):
                continue  # 検索の記録は補足。テストの変更案の代わりにはならない。
            hint = "（どの参照先か `名前:パス` で書いてください）" if cited.ambiguous else ""
            plan.problems.append(f"{TESTS_HEADING} の項目に、テストのファイルのパスがありません{hint}: {item[:80]}")
            continue
        # 「未判断」は扱った（漏れではない）が、まだ変えると決めていない
        (plan.waived if waived or PENDING_MARK in item else plan.change).update(hits)
    return plan


def plan_changes(ctx: Ctx, bodies: dict[str, str]) -> dict[str, set[str]]:
    """計画で変えるファイル（自分の変更案・参照先の変更案。テストの変更案は除く）。"""
    changed = {"": own_planned(ctx, bodies)}
    for name, rels in planned_refs(ctx, bodies)[0].items():
        changed.setdefault(name, set()).update(rels)
    return changed


def untested_names(ctx: Ctx, bodies: dict[str, str], touched_all: dict[str, set[str]]) -> list[str]:
    """この回で新しく足した名前（関数・型・文書の見出し）のうち、どのテストにも出てこず、計画も扱っていないもの。

    文書の見出しは、この回に変えたテストがその文書をパスで指していれば確かめたとみなす（e2e のケースの注記など）。
    """
    plan_body = bodies.get(TESTS_HEADING, "")
    touched_tests = [(k, rel) for k, rels in touched_all.items() for rel in rels
                     if is_test(ctx, k, rel) and (side_of(ctx, k).path / rel).is_file()]
    out = []
    for key, side in all_sides(ctx):
        if not touched_all.get(key):
            continue
        for name, rel in added_names(ctx, key, side).items():
            if name in plan_body or tested_anywhere(ctx, name):
                continue
            if rel.lower().endswith(DOC_EXTS) and any(
                    (key, rel) in resolve(ctx, side_of(ctx, k), t, claim).hits
                    for k, t in touched_tests
                    for claim in claims_in(t, read_text(side_of(ctx, k).path / t) or "")):
                continue
            out.append(f"`{name}`（{side_label(ctx, key, rel)}）")
    return out


def write_tests_report(ctx: Ctx, name: str, title: str, found: dict[tuple[str, str], str],
                       tp: TestPlan) -> None:
    def row(k: str, rel: str, why: str) -> str:
        state = ("変える" if (k, rel) in tp.change else NO_CHANGE_MARK if (k, rel) in tp.waived else "計画に無い")
        return f"- {side_label(ctx, k, rel)} — {why}（{state}）"
    lines = [f"# {title}", "", *([row(k, r, w) for (k, r), w in sorted(found.items())] or ["- なし"]), ""]
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / name).write_text("\n".join(lines), encoding="utf-8")


def new_names_plan_problems(ctx: Ctx, bodies: dict[str, str]) -> list[str]:
    """変更案で新しく足す名前（どの側にもまだ無い `…`）を、テストの変更案が扱っているか。

    今あるテストに当たらない新しい振る舞いは、測っても響くテストが見つからない。足すテストを計画させる。
    """
    names = name_terms(bodies.get("## 自分の変更案", "") + "\n" + bodies.get("## 参照先の変更案", ""))
    body = bodies.get(TESTS_HEADING, "")
    new = [n for n in names if n not in body
           and not any(git_grep(side, ["--untracked", "-l", "-I", "-i", "-F", *grep_word(n), "-e", n])
                       for _, side in all_sides(ctx))]
    if not new:
        return []
    return [f"新しく足す {'、'.join(f'`{n}`' for n in new)} を確かめるテストが {TESTS_HEADING} にありません。"
            "足すテストのパスと確かめることを、名前を添えて書いてください（要らないなら "
            f"`- `名前` — {NO_CHANGE_MARK}: 理由`）"]


def tests_plan_problems(ctx: Ctx, bodies: dict[str, str], terms: list[str], pending: Pending) -> list[str]:
    """計画が、変更に響くテストをすべて扱っているか（足す・直す・変更不要）。"""
    if not tests_enabled(ctx):
        return []
    tp = test_plan(ctx, bodies)
    problems: list[str] = []
    changed = plan_changes(ctx, bodies)
    if any(changed.values()) and not tp.listed() and not tp.reason_only:
        problems.append(f"{TESTS_HEADING} が「なし」です。コード・仕様書と同じく、足す・直すテストを挙げてください"
                        f"（要らないなら `- {NO_CHANGE_MARK}: 理由`）")
    found = affected_tests(ctx, terms, changed)
    write_tests_report(ctx, "tests.md", "計画の変更が響くテスト（測定）", found, tp)
    problems += new_names_plan_problems(ctx, bodies)
    problems += evidence_plan_problems(ctx, bodies, set(found) | tp.change)
    own_listed = (listed_paths(ctx, bodies.get("## 自分の変更案", ""), allow_new=True)
                  | listed_paths(ctx, bodies.get("## 影響範囲", "")))
    missing_keys = [(k, rel) for (k, rel) in sorted(found) if (k, rel) not in tp.listed()
                    and not (not k and covered(rel, own_listed)) and not (k and covered(rel, changed.get(k, set())))]
    if not missing_keys:
        return problems
    why = {"名前": "変わる名前が出てくる", "ファイル名": "変えるファイルと名前が対になっている",
           "つながり": "変えるファイルとパスでつながっている"}
    for k, rel in missing_keys:
        pending.add(TESTS_HEADING, (k, rel),
                    f"- {side_label(ctx, k, rel)} — {PENDING_MARK}（{why.get(found[(k, rel)], found[(k, rel)])}）")
    # 響くテストを書き足せば、「なし」の指摘は書き足した項目の判断に置き換わる
    return [p for p in problems if "が「なし」です" not in p]


# ---------------------------------------------------------------- テストで得たもの（evidence）を実装・文書に活かす
# テストは合否のほかに、確かめた振る舞い・測った時間・前回と比べた画面を残す（evidence JSON。
# 実行する道具が共通の形で書く）。テストの側は文書を知らない。文書への影響を測って直すのはこのマシンの仕事:
# - 画面: 文書のリポジトリの画像を sha256 で引き、画面のこれまでの版と同じ画像を見つけて今の画面に差し替える
# - 振る舞い・時間: 文書は値を手で写さず `<!-- evidence: id -->…<!-- /evidence -->` の印で写し、今と同じか
#   （`codd.py evidence --write` で写し直す）・目安（max=）を超えていないかを確かめる

_EVIDENCE_MARK = re.compile(r"<!--\s*evidence:\s*(?P<id>[^\s>]+)(?P<opts>[^>]*?)-->(?P<body>.*?)<!--\s*/evidence\s*-->",
                            re.DOTALL)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
STATUS_MARK = {"passed": "✓", "failed": "✗", "skipped": "－"}


@dataclass
class Evidence:
    key: str            # 側（自分は ""）
    root: Path          # 項目のパスの起点
    items: dict[str, dict]
    files: list[str]    # 読んだ evidence のファイル（その側のルートから）


def evidence_patterns(ctx: Ctx, key: str) -> list[str]:
    return ctx.config["evidence"] if not key else ctx.ref(key).evidence_patterns


def load_evidence(ctx: Ctx, key: str) -> Evidence:
    side = side_of(ctx, key)
    ev = Evidence(key, side.path, {}, [])
    for pattern in evidence_patterns(ctx, key):
        for path in sorted(side.path.glob(pattern)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                continue
            ev.files.append(path.relative_to(side.path).as_posix())
            # 項目のパスの起点は、evidence.json の `root`（そのファイルのフォルダからの相対）。無ければその側のルート。
            root = (path.parent / data["root"]).resolve() if isinstance(data.get("root"), str) else side.path
            for item in data["items"]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ev.items[item["id"]] = {**item, "_root": root}
    return ev


def all_evidence(ctx: Ctx) -> dict[str, Evidence]:
    return {key: load_evidence(ctx, key) for key, _ in all_sides(ctx)}


def find_evidence(ctx: Ctx, evs: dict[str, Evidence], doc_key: str, ident: str) -> list[dict]:
    """印の id に当たる項目。`名前:id` は参照先 名前 から。`…/*` は前方一致で並べる。それ以外は文書の側・自分・参照先の順。"""
    name, sep, rest = ident.partition(":")
    order = [doc_key, "", *(r.name for r in ctx.refs)]
    if sep and name in evs:
        order, ident = [name], rest
    elif sep and _NAME.match(name) and not any(ident in e.items for e in evs.values()):
        ident = rest
    for key in dict.fromkeys(order):
        # 画面は印で写さない（文書の画像はハーネスが sha256 で見つけて差し替える。replace_screens）
        items = {i: v for i, v in evs[key].items.items() if v.get("kind") != "image"}
        if ident.endswith("*"):
            hits = [items[i] for i in sorted(items) if i.startswith(ident[:-1])]
        else:
            hits = [items[ident]] if ident in items else []
        if hits:
            return hits
    return []


def render_item(item: dict, doc_path: Path) -> str:
    kind = item.get("kind")
    if kind == "metric":
        return f"{item.get('value')} {item.get('unit', '')}".strip()
    if kind == "image":
        return f"画面 {item.get('status', '')} {item.get('path', '')}".strip()
    status = item.get("status", "")
    return f"{STATUS_MARK.get(status, '')} {item.get('title', item['id'])}".strip()


def render_mark(ident: str, items: list[dict], doc_path: Path) -> str:
    if ident.endswith("*"):
        return "\n" + "\n".join(f"- {render_item(i, doc_path)}" for i in items) + "\n"
    return render_item(items[0], doc_path)


def mark_opts(text: str) -> dict[str, str]:
    return dict(m.groups() for m in re.finditer(r"(\w+)=([^\s]+)", text))


def close_enough(written: str, now: str, tolerance: float) -> bool:
    """数は tolerance の割合まで違ってよい（測った値の揺れ）。数以外は同じであること。"""
    if _NUMBER.sub("#", written.strip()) != _NUMBER.sub("#", now.strip()):
        return False
    for a, b in zip(_NUMBER.findall(written), _NUMBER.findall(now)):
        a, b = float(a), float(b)
        if abs(a - b) > tolerance * max(abs(a), abs(b), 1.0):
            return False
    return True


@dataclass
class Mark:
    key: str
    rel: str
    line: int
    ident: str
    opts: dict[str, str]
    span: tuple[int, int]
    body: str


def marked_docs(ctx: Ctx, key: str) -> list[str]:
    side = side_of(ctx, key)
    return [ln for ln in git_grep(side, ["-l", "-I", "--untracked", "-E", r"<!--[[:space:]]*evidence:"])
            if ln.lower().endswith(DOC_EXTS)]


def mask_examples(text: str) -> str:
    """コードブロックと `…` の中を、同じ長さの空白に置き換える（記入例の印を拾わず、位置は保つ）。"""
    out, fenced = [], False
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if _FENCE.match(line) or fenced:
            fenced = fenced != bool(_FENCE.match(line))
            out.append(" " * len(body) + line[len(body):])
        else:
            out.append(_INLINE_CODE.sub(lambda m: " " * len(m.group(0)), body) + line[len(body):])
    return "".join(out)


def real_marks(text: str) -> list[re.Match]:
    """文書の中の、記入例ではない証跡の印（位置は元の文書と同じ。中身は元の文書から切り出す）。"""
    return list(_EVIDENCE_MARK.finditer(mask_examples(text)))


def marks_in(ctx: Ctx, key: str, rel: str) -> list[Mark]:
    text = read_text(side_of(ctx, key).path / rel) or ""
    return [Mark(key, rel, text.count("\n", 0, m.start()) + 1, m.group("id"), mark_opts(m.group("opts")),
                 m.span("body"), text[m.start("body"):m.end("body")]) for m in real_marks(text)]


def evidence_marks(ctx: Ctx, only: set[tuple[str, str]] | None = None) -> list[Mark]:
    marks: list[Mark] = []
    for key, _ in all_sides(ctx):
        for rel in marked_docs(ctx, key):
            if only is None or (key, rel) in only:
                marks += marks_in(ctx, key, rel)
    return marks


def check_marks(ctx: Ctx, evs: dict[str, Evidence], marks: list[Mark]) -> tuple[list[str], list[str]]:
    """（今と違う印, 目安を超えた・見つからない印）。"""
    stale: list[str] = []
    bad: list[str] = []
    for mk in marks:
        where = f"{side_label(ctx, mk.key, mk.rel)}:{mk.line} {mk.ident}"
        items = find_evidence(ctx, evs, mk.key, mk.ident)
        if not items:
            bad.append(f"{where} — テストの結果にこの id がありません")
            continue
        for item in items:
            limit = mk.opts.get("max", item.get("max"))
            if item.get("kind") == "metric" and limit is not None:
                try:
                    over = float(item.get("value", 0)) > float(limit)
                except (TypeError, ValueError):
                    over = False
                if over:
                    amount = f"{item.get('value')} {item.get('unit', '')}".strip()
                    bad.append(f"{where} — {item.get('title', item['id'])} が {amount} で、目安の {limit} を超えています")
            if item.get("kind") == "behavior" and item.get("status") == "failed":
                bad.append(f"{where} — 確かめた振る舞いが失敗しています: {item.get('title', item['id'])}")
        now = render_mark(mk.ident, items, side_of(ctx, mk.key).path / mk.rel)
        try:
            tolerance = float(mk.opts.get("tolerance", EVIDENCE_TOLERANCE))
        except ValueError:
            tolerance = EVIDENCE_TOLERANCE
        if not close_enough(mk.body, now, tolerance):
            flat = lambda t: " ".join(t.split())[:40]  # noqa: E731
            stale.append(f"{where}（書いてある {flat(mk.body) or '空'} → 今 {flat(now)}）")
    return stale, bad


def evidence_apply_problems(ctx: Ctx, docs: set[tuple[str, str]], tests: set[tuple[str, str]]) -> list[str]:
    """変えた・変える文書の印と、この変更が響くテストの結果を写した印だけを確かめる。

    ほかの文書にもとからある古い印は、直すと計画に無いファイルを変えることになり、どうやっても通らない（lint が拾う）。
    """
    evs = all_evidence(ctx)
    key_of = {id(item): k for k, e in evs.items() for item in e.items.values()}
    marks = [mk for mk in evidence_marks(ctx)
             if (mk.key, mk.rel) in docs
             or any((key_of.get(id(item), ""), str(item.get("file") or "")) in tests
                    for item in find_evidence(ctx, evs, mk.key, mk.ident))]
    if not marks:
        return []
    if not any(e.files for e in evs.values()):
        return ["文書がテストの結果を写していますが、テストで得たもの（evidence）がありません（テストか検査のコマンドが"
                f"書いているか、{CONFIG_NAME} の evidence を確かめてください）"]
    stale, bad = check_marks(ctx, evs, marks)
    problems = []
    if bad:
        problems.append("テストで得たものが、文書の求めを満たしていません（実装を直すか、目安を変えるなら利用者に確かめて"
                        "計画に挙げてから文書を直してください）: " + " / ".join(bad[:8]))
    if stale:
        problems.append("文書に写したテストの結果が今と違います（`python3 .statemachine/codd/codd.py evidence --write 文書のパス` で"
                        "写し直してください。計画に無い文書なら、利用者に確かめて計画に挙げる）: " + " / ".join(stale[:8]))
    return problems


def evidence_plan_problems(ctx: Ctx, bodies: dict[str, str], tests: set[tuple[str, str]]) -> list[str]:
    """変更が響くテストの結果を写している文書を、計画が扱っているか。関係するテストの結果を .codd/evidence.md に控える。"""
    marks = evidence_marks(ctx)
    evs = all_evidence(ctx)
    if not marks and not any(e.items for e in evs.values()):
        return []
    planned = {(k, rel) for k, rels in plan_changes(ctx, bodies).items() for rel in rels}
    # 変える・響くファイルに関係する、今のテストの結果（確かめた振る舞い・時間・画像）
    related: list[tuple[str, dict]] = []
    for key, ev in evs.items():
        for item in ev.items.values():
            paths = {str(item.get("file") or ""), *map(str, item.get("doc") or []), *map(str, item.get("code") or [])}
            hits = {(key, p) for p in paths if p} | {tuple(p.split(":", 1)) for p in paths if ":" in p}
            if hits & (planned | tests):
                related.append((key, item))
    # 響くテストの結果を写している文書
    hit_docs: dict[tuple[str, str], list[str]] = {}
    for mk in marks:
        for item in find_evidence(ctx, evs, mk.key, mk.ident):
            ev_key = next((k for k, e in evs.items() if item["id"] in e.items and e.items[item["id"]] is item), "")
            if (ev_key, str(item.get("file") or "")) in tests:
                hit_docs.setdefault((mk.key, mk.rel), []).append(mk.ident)
    lines = ["# テストで得たもの（計画に関係するもの）", ""]
    lines += [f"- {side_label(ctx, k, i['id'])} — {i.get('kind')}: {render_item(i, side_of(ctx, k).path / 'x')}"
              for k, i in related[:MAX_MEASURED * 2]] or ["- なし"]
    lines += ["", "## 響くテストの結果を写している文書", ""]
    lines += [f"- {side_label(ctx, k, rel)} — " + ", ".join(ids) for (k, rel), ids in sorted(hit_docs.items())] or ["- なし"]
    # 響くテストの画面を貼っている文書の画像（変えたあと、画面が変われば差し替える。知らせるだけ）
    ev_key_of = {id(item): k for k, e in evs.items() for item in e.items.values()}
    shown = [h for h in doc_screens(ctx, evs) if (ev_key_of.get(id(h.item), ""), str(h.item.get("file") or "")) in tests]
    lines += ["", "## 響くテストの画面を貼っている文書の画像（画面が変われば、変えたあとに差し替える）", ""]
    lines += [f"- {side_label(ctx, h.key, h.rel)} ← {h.item['id']}"
              + (f"（{', '.join(docs_showing(ctx, h.key, h.rel))}）" if docs_showing(ctx, h.key, h.rel) else "")
              for h in shown] or ["- なし"]
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / "evidence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ctx.data / "evidence-before.json").write_text(json.dumps(
        {k: {i: {kk: vv for kk, vv in item.items() if kk != "_root"} for i, item in e.items.items()}
         for k, e in evs.items()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    own_listed = (listed_paths(ctx, bodies.get("## 自分の変更案", ""), allow_new=True)
                  | listed_paths(ctx, bodies.get("## 影響範囲", "")))
    cited = cited_anywhere(ctx, bodies) | planned
    missing = [side_label(ctx, k, rel) for (k, rel) in sorted(hit_docs)
               if not (covered(rel, own_listed) if not k else (k, rel) in cited)]
    if missing:
        return ["変更が響くテストの結果を写している文書が、計画にありません（写し直すなら変えるファイルに、要らなければ"
                f"「{NO_CHANGE_MARK}: 理由」を書いてください）: " + ", ".join(missing) + f"（詳細: {DATA_DIRNAME}/evidence.md）"]
    return []


def evidence_changes(ctx: Ctx) -> list[str]:
    """計画のときと今とで、測った時間が揺れの幅を超えて変わったものと、振る舞いの合否が変わったもの（報告用）。"""
    before_file = ctx.data / "evidence-before.json"
    if not before_file.is_file():
        return []
    before = json.loads(before_file.read_text(encoding="utf-8"))
    lines = []
    for key, ev in all_evidence(ctx).items():
        old = before.get(key, {})
        for ident, item in sorted(ev.items.items()):
            prev = old.get(ident)
            label = side_label(ctx, key, ident)
            if prev is None:
                lines.append(f"- {label} — 新しく得た: {render_item(item, ev.root / 'x')}")
            elif item.get("kind") == "metric" and not close_enough(str(prev.get("value")), str(item.get("value")),
                                                                    EVIDENCE_TOLERANCE):
                lines.append(f"- {label} — {prev.get('value')} → {item.get('value')} {item.get('unit', '')}")
            elif item.get("kind") == "image" and prev.get("sha256") != item.get("sha256"):
                lines.append(f"- {label} — 画面が変わった" + (f"（前: {item['previous']}）" if item.get("previous") else ""))
            elif item.get("kind") == "behavior" and prev.get("status") != item.get("status"):
                lines.append(f"- {label} — {prev.get('status')} → {item.get('status')}")
    return lines


IMAGE_EXTS = (".png",)
MAX_IMAGE_FILES = 5000
REPLACED_FILE = "replaced.json"
_IMG_LINK = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)|<img\s[^>]*?src\s*=\s*[\"']([^\"']+)[\"']", re.I)


def image_index(ctx: Ctx) -> dict[str, list[tuple[str, str]]]:
    """各側の画像（git が無視していないもの）を sha256 で引けるようにする。"""
    index: dict[str, list[tuple[str, str]]] = {}
    for key, side in all_sides(ctx):
        rc, out = run(["git", "ls-files", "--cached", "--others", "--exclude-standard", *side.pathspec()],
                      side.path, GIT_TIMEOUT)
        files = [ln for ln in out.splitlines() if side.has(ln) and ln.lower().endswith(IMAGE_EXTS)] if rc == 0 else []
        for rel in files[:MAX_IMAGE_FILES]:
            try:
                digest = hashlib.sha256((side.path / rel).read_bytes()).hexdigest()
            except OSError:
                continue
            index.setdefault(digest, []).append((key, rel))
    return index


@dataclass
class ScreenHit:
    key: str            # 画像のある側
    rel: str            # 画像のパス
    item: dict          # テストの画面
    state: str          # current（今の画面）/ stale（前の版）/ removed（撮らなくなった画面）


def doc_screens(ctx: Ctx, evs: dict[str, Evidence]) -> list[ScreenHit]:
    """文書のリポジトリにある画像のうち、テストの画面（今か前の版）と同じもの。"""
    index = image_index(ctx)
    hits: dict[tuple[str, str], ScreenHit] = {}
    for ev in evs.values():
        for item in ev.items.values():
            if item.get("kind") != "image":
                continue
            current = item.get("sha256")
            for digest in [h for h in item.get("history") or [] if isinstance(h, str)]:
                for key, rel in index.get(digest, []):
                    state = "removed" if item.get("status") == "removed" else "current" if digest == current else "stale"
                    if (key, rel) not in hits or hits[(key, rel)].state == "current":
                        hits[(key, rel)] = ScreenHit(key, rel, item, state)
    return [hits[k] for k in sorted(hits)]


def docs_showing(ctx: Ctx, key: str, rel: str) -> list[str]:
    """その画像を貼っている文書（同じ側のマークダウン）。"""
    side = side_of(ctx, key)
    target = (side.path / rel).resolve()
    out = []
    for doc in files_mentioning(side, [Path(rel).name]):
        if not doc.lower().endswith(DOC_EXTS):
            continue
        text = read_text(side.path / doc) or ""
        for m in _IMG_LINK.finditer(text):
            link = (m.group(1) or m.group(2) or "").split("#")[0].split("?")[0]
            if link and not re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I) and (side.path / doc).parent.joinpath(link).resolve() == target:
                out.append(doc)
                break
    return out


def replaced_files(ctx: Ctx) -> dict:
    file = ctx.data / REPLACED_FILE
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"replaced": [], "removed": []}


def replace_screens(ctx: Ctx) -> dict:
    """テストの画面が変わったら、前の版を貼っている文書の画像を今の画面に差し替える（ハーネスがする変更）。

    差し替えた画像は .codd/replaced.json に控え、「計画に無い変更」に数えない。報告で、どの文書に響いたかを伝える。
    """
    record = replaced_files(ctx)
    done = {(r["side"], r["path"]) for r in record["replaced"]}
    removed = []
    for hit in doc_screens(ctx, all_evidence(ctx)):
        entry = {"side": hit.key, "path": hit.rel, "id": hit.item["id"], "docs": docs_showing(ctx, hit.key, hit.rel)}
        if hit.state == "removed":
            removed.append(entry)
            continue
        if hit.state != "stale":
            continue
        src = hit.item["_root"] / str(hit.item.get("path", ""))
        if not src.is_file():
            continue
        shutil.copyfile(src, side_of(ctx, hit.key).path / hit.rel)
        if (hit.key, hit.rel) not in done:
            record["replaced"].append(entry)
            done.add((hit.key, hit.rel))
    record["removed"] = removed
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / REPLACED_FILE).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def screen_lines(ctx: Ctx, record: dict) -> list[str]:
    lines = [f"- {side_label(ctx, r['side'], r['path'])} ← {r['id']}"
             + (f"（貼っている文書: {', '.join(r['docs'])}）" if r["docs"] else "（どの文書も貼っていない）")
             for r in record["replaced"]]
    lines += [f"- {side_label(ctx, r['side'], r['path'])} — テストで撮らなくなった画面です（{r['id']}）"
              + (f"。貼っている文書: {', '.join(r['docs'])}" if r["docs"] else "") for r in record["removed"]]
    return lines


def cmd_evidence(ctx: Ctx, args: argparse.Namespace) -> int:
    """テストで得たものと、文書の印の様子を示す。--write で印を今の値に写し直す（指定した文書だけ。無ければすべて）。"""
    evs = all_evidence(ctx)
    for key, ev in evs.items():
        if ev.files:
            print(f"{'自分' if not key else key}: {len(ev.items)} 件（{', '.join(ev.files)}）")
    if not any(e.files for e in evs.values()):
        print(f"テストで得たもの（evidence）がありません（{CONFIG_NAME} の evidence: "
              + ", ".join(ctx.config["evidence"] or ["なし"]) + "）")
    only = None
    if args.path:
        only = set()
        for p in args.path:
            name, sep, rel = p.partition(":")
            only.add((name, rel) if sep and name in {r.name for r in ctx.refs} else ("", p))
    marks = evidence_marks(ctx, only)
    if args.write:
        written = []
        for (key, rel) in sorted({(m.key, m.rel) for m in marks}):
            path = side_of(ctx, key).path / rel
            text = path.read_text(encoding="utf-8")

            new, pos = [], 0
            for m in real_marks(text):   # コードブロックと `…` の中の記入例は書き戻さない
                items = find_evidence(ctx, evs, key, m.group("id"))
                body = text[m.start("body"):m.end("body")]
                if not items:
                    continue
                now = render_mark(m.group("id"), items, path)
                tol = float(mark_opts(m.group("opts")).get("tolerance", EVIDENCE_TOLERANCE))
                if not close_enough(body, now, tol):
                    new += [text[pos:m.start("body")], now]
                    pos = m.end("body")
            new = "".join(new) + text[pos:]
            if new != text:
                path.write_text(new, encoding="utf-8")
                written.append(side_label(ctx, key, rel))
        print("写し直した: " + (", ".join(written) or "なし"))
        marks = evidence_marks(ctx, only)
    stale, bad = check_marks(ctx, evs, marks)
    print(f"文書の印: {len(marks)} 件" + (f"（今と違う {len(stale)}・満たさない {len(bad)}）" if stale or bad else "（すべて今と同じ）"))
    for line in stale:
        print(f"  ↻ {line}")
    for line in bad:
        print(f"  ✗ {line}")
    return 1 if stale or bad else 0


# ---------------------------------------------------------------- 文書の書式（コードの決まりと同じに扱う）
# 文書（マークダウン）の今の書式は、コードで言う決まりにあたる。変える文書は今の見出しの並びを、新しく足す文書は
# 同じフォルダの文書がそろって持つ見出しの並びを、決まりとして守らせる。見出しを変えるなら、計画に `## 見出し` と書く。

FORMAT_EXTS = (".md", ".markdown")
FORMATS_FILE = "formats.json"
_DOC_HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*#*\s*$")
_NOT_MODELS = {"readme", "index", "_index", "changelog", "history"}


def doc_headings(text: str) -> list[str]:
    """文書の見出し（## 以下。題名の # は文書ごとに違うので見ない）。コードブロックの中は見ない。"""
    found: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (m := _DOC_HEADING.match(line)):
            found.append(f"{m.group(1)} {m.group(2)}")
    return found


def doc_format(ctx: Ctx, key: str, rel: str) -> dict | None:
    """変える・足す文書が守る書式。{"models": 見本のパス, "headings": 見出しの並び}。決まりが無ければ None。"""
    if not rel.lower().endswith(FORMAT_EXTS) or is_test(ctx, key, rel):
        return None
    side = side_of(ctx, key)
    path = side.path / rel
    if path.is_file():
        headings = doc_headings(read_text(path) or "")
        return {"models": [rel], "headings": headings} if headings else None
    if not path.parent.is_dir():
        return None
    models = sorted(p for p in path.parent.iterdir() if p.is_file() and p.suffix.lower() in FORMAT_EXTS
                    and p.stem.lower() not in _NOT_MODELS)
    if len(models) < 2:
        return None
    lists = [doc_headings(read_text(p) or "") for p in models]
    common = unique(h for h in lists[0] if all(h in other for other in lists[1:]))
    if not common:
        return None
    return {"models": [p.relative_to(side.path).as_posix() for p in models], "headings": common}


def format_targets(ctx: Ctx, bodies: dict[str, str]) -> list[tuple[str, str]]:
    own = own_planned(ctx, bodies) | listed_paths(ctx, bodies.get("## 影響範囲", ""), skip_no_change=True)
    targets = {("", rel) for rel in own}
    for name, rels in planned_refs(ctx, bodies)[0].items():
        targets |= {(name, rel) for rel in rels}
    return sorted(targets)


def model_label(ctx: Ctx, key: str, rel: str) -> str:
    return f"{key}:{rel}" if key else rel


def formats_plan_problems(ctx: Ctx, bodies: dict[str, str], pending: Pending) -> list[str]:
    """変える文書の書式（見本と見出しの並び）を測って控え、「守る決まり」に無い見本を見出しの並びを添えて pending へ。"""
    formats = []
    for key, rel in format_targets(ctx, bodies):
        fmt = doc_format(ctx, key, rel)
        if fmt:
            formats.append({"side": key, "path": rel, **fmt})
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / FORMATS_FILE).write_text(json.dumps(formats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 変える文書が守る書式（見本と見出しの並び）", ""]
    for f in formats:
        lines.append(f"- {side_label(ctx, f['side'], f['path'])}（見本: "
                     + ", ".join(model_label(ctx, f["side"], m) for m in f["models"]) + "）")
        lines += [f"  - {h}" for h in f["headings"]]
    (ctx.data / "formats.md").write_text("\n".join(lines + (["- なし"] if not formats else []) + [""]),
                                         encoding="utf-8")
    body = bodies.get("## 守る決まり", "")
    for f in formats:
        names = [model_label(ctx, f["side"], m) for m in f["models"]]
        if f["side"] and len(ctx.refs) == 1:
            names += f["models"]
        if not any(mentioned(body, n) for n in names):
            heads = " / ".join(h.lstrip("# ") for h in f["headings"][:6]) + (" …" if len(f["headings"]) > 6 else "")
            pending.add("## 守る決まり", ("format", f["side"], f["path"]), f"- {names[0]} — {PENDING_MARK}: "
                        f"{side_label(ctx, f['side'], f['path'])} の書式の見本（見出し: {heads or 'なし'}）")
    return []


def formats_apply_problems(ctx: Ctx, text: str) -> list[str]:
    """変えた文書が、計画のときに控えた書式（見出しの並び）を守っているか。計画に `## 見出し` と書いたものは除く。"""
    file = ctx.data / FORMATS_FILE
    if not file.is_file():
        return []
    renamed = set(name_terms(text))
    problems = []
    for f in json.loads(file.read_text(encoding="utf-8")):
        path = side_of(ctx, f["side"]).path / f["path"]
        if not path.is_file():
            continue
        now = iter(doc_headings(read_text(path) or ""))
        want = [h for h in f["headings"] if h not in renamed]
        missing = [h for h in want if h not in now]   # 順に探す（並びが違っても無いと数える）
        if missing:
            problems.append(f"文書の書式（見出しの並び）が今の書式から外れています: {side_label(ctx, f['side'], f['path'])} — "
                            + " / ".join(missing[:5]) + f"（見本: {model_label(ctx, f['side'], f['models'][0])}。"
                            "書式は決まりとして守る。見出しを変えるなら利用者に確かめて、計画に `## 見出し` と書く）")
    return problems


def write_baseline(ctx: Ctx) -> None:
    """変える前の印。verify-apply はここから「どのファイルを変えたか」を測る。確認の直前に取り直す。

    作業中だったファイルの中身も `.codd/before/` に控え、`rollback` で変える前へ戻せるようにする。
    `keep-changes` の印があれば（変えた分を残して計画を直すとき）、前の印をそのまま使う。
    """
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / "applied.json").unlink(missing_ok=True)
    keep = ctx.data / KEEP_MARK
    if keep.is_file() and (ctx.data / "before.json").is_file():
        keep.unlink()
        return
    keep.unlink(missing_ok=True)
    (ctx.data / REPLACED_FILE).unlink(missing_ok=True)
    shutil.rmtree(ctx.data / "before", ignore_errors=True)
    state = {"own": snapshot(ctx.own), "refs": {r.name: snapshot(r) for r in ctx.refs}}
    for key, side in all_sides(ctx):
        snap = state["refs"][key] if key else state["own"]
        for rel, digest in snap["files"].items():
            if digest != "deleted":
                dest = backup_dir(ctx, key) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(side.path / rel, dest)
    (ctx.data / "before.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


KEEP_MARK = "keep-baseline"
PASSED_PLAN = "passed-plan"   # 計画の検査を通り、確認で直す・やめるとされていない計画の印


def plan_digest(ctx: Ctx) -> str:
    return hashlib.sha256(ctx.plan.read_bytes()).hexdigest()


PLAN_CHECK_TEXT = "plan-check.json"


def plan_text_for_checks(ctx: Ctx) -> str:
    """利用者向けの計画から省いた変更不要の判断は、同じ計画の検査だけで使う。"""
    text = ctx.plan.read_text(encoding="utf-8")
    try:
        saved = json.loads((ctx.data / PLAN_CHECK_TEXT).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return text
    if isinstance(saved, dict) and saved.get("digest") == plan_digest(ctx) and saved.get("path") == plan_rel(ctx) and isinstance(saved.get("text"), str):
        return saved["text"]
    return text


def restore_hidden(ctx: Ctx, text: str) -> str:
    """練り直しで計画を直したとき、通ったときに計画から省いた変更不要の判断を戻す。

    戻さないと、判断済みのファイルがまた「未判断」として書き足され、テストの変更案が「なし」で落ち、毎回やり直しになる。
    """
    try:
        saved = json.loads((ctx.data / PLAN_CHECK_TEXT).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return text
    if not isinstance(saved, dict) or saved.get("path") != plan_rel(ctx) or not isinstance(saved.get("text"), str):
        return text
    _, old = sections(saved["text"], PLAN_HEADINGS)
    _, now = sections(text, PLAN_HEADINGS)
    back = Pending()
    for heading in ("## 影響範囲", TESTS_HEADING):
        if heading not in now:
            continue
        for item in items(old.get(heading, "")):
            j = judgment(item)
            if not j.waived:
                continue
            paths = [t for t in j.targets.split(", ") if t]
            kept = any(p in text for p in paths) if paths else not is_none(now[heading])
            if not kept and f"- {item}" not in text:
                back.add(heading, (heading, item), f"- {item}")
    return add_pending(text, back) if back.count() else text


def approved_plan_digest(ctx: Ctx) -> str:
    # 非表示の判断も、承認後に書き換えられていないか確かめる。
    payload = plan_rel(ctx) + "\n" + plan_digest(ctx) + "\n" + plan_text_for_checks(ctx)
    return hashlib.sha256(payload.encode()).hexdigest()


def plan_unconfirmed(ctx: Ctx) -> list[str]:
    """変えてよいのは、計画の検査を通って、確認で退けられていない計画だけ（止まったところから飛ばして来ても通さない）。"""
    mark = ctx.data / PASSED_PLAN
    if mark.is_file() and mark.read_text(encoding="utf-8") == approved_plan_digest(ctx):
        return []
    return ["計画が、検査を通って利用者が確かめたものではありません（計画を直したか、確認で退けられたか、"
            "計画の検査で止まったままです。計画を練り直し、検査と確認を通してから変えてください）"]


def backup_dir(ctx: Ctx, key: str) -> Path:
    return ctx.data / "before" / ("own" if not key else f"ref-{key}")


MAX_PRINTED = 1800      # 検査の指摘を出す長さ。実行ハーネスは出力の末尾 2000 文字だけをやり直しに渡す
MAX_PROBLEM = 400


def print_problems(ctx: Ctx, phase: str, problems: list[str]) -> None:
    """指摘を控え（全文は .codd/problems.json）、短くして出す。最初の行は件数（止まったときの見出しになる）。"""
    record_problems(ctx.data, phase, problems)
    if not problems:
        return
    stage = "計画の検査" if phase == "plan" else "変えたあとの検査"
    lines = [f"NG {stage}: {len(problems)} 件（全文: {DATA_DIRNAME}/{PROBLEMS_NAME}）"]
    used = len(lines[0])
    for i, p in enumerate(problems):
        if "検査が失敗しました" in p and len(p) > MAX_PROBLEM:
            head_line, _, tail = p.partition("\n")
            p = head_line[:MAX_PROBLEM] + "\n…" + tail[-(MAX_PROBLEM * 2):]   # 失敗の理由は末尾にある
        elif len(p) > MAX_PROBLEM:
            p = p[:MAX_PROBLEM] + "…"
        if used + len(p) > MAX_PRINTED:
            lines.append(f"- ほか {len(problems) - i} 件（{DATA_DIRNAME}/{PROBLEMS_NAME}）")
            break
        lines.append(f"- {p}")
        used += len(p) + 3
    print("\n".join(lines), file=sys.stderr)


def cmd_verify_plan(ctx: Ctx, args: argparse.Namespace) -> int:
    if not ctx.plan.is_file():
        print_problems(ctx, "plan", [NO_PLAN])
        return 1
    text = plan_text_for_checks(ctx)
    restored = restore_hidden(ctx, text) if text == ctx.plan.read_text(encoding="utf-8") else text
    if restored != text:
        ctx.plan.write_text(restored, encoding="utf-8")
        text = restored
    _, bodies = sections(text, PLAN_HEADINGS)
    problems = verify_plan_text(ctx, text) + record_problems_of(ctx)
    measured: list[str] = []
    ref_count = 0
    if all(h in bodies for h in PLAN_HEADINGS):
        # 形の指摘があっても測る。指摘を 1 回で出し切り、やり直しの回数を減らす。
        left = pending_items(bodies)
        pending = Pending()
        measured_problems, measured, ref_count = measure_plan(ctx, bodies, pending)
        if pending.count():
            ctx.plan.write_text(add_pending(text, pending), encoding="utf-8")
        problems += pending_problem(left + [(h, ln) for h, lns in pending.lines.items() for ln in lns],
                                    pending.count())
        problems += measured_problems
    print_problems(ctx, "plan", problems)
    if problems:
        (ctx.data / PASSED_PLAN).unlink(missing_ok=True)
        return 1
    ctx.plan.write_text(compact_plan(text), encoding="utf-8")
    (ctx.data / PLAN_CHECK_TEXT).write_text(json.dumps(
        {"digest": plan_digest(ctx), "path": plan_rel(ctx), "text": text}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_baseline(ctx)
    batches = plan_batches(ctx, bodies)
    (ctx.data / BATCHES_NAME).write_text(json.dumps(
        {"batches": [[list(f) for f in b] for b in batches], "done": 0}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (ctx.data / PASSED_PLAN).write_text(approved_plan_digest(ctx), encoding="utf-8")
    notes = []
    if measured:
        notes.append(f"影響範囲を測った: {len(measured)} files、{DATA_DIRNAME}/impact.md")
    if ref_count:
        notes.append(f"参照先で触れている: {ref_count} files、{DATA_DIRNAME}/ref-impact.md")
    if len(batches) > 1:
        notes.append(f"変えるときは {len(batches)} 段に分ける")
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
    _, bodies = sections(plan_text_for_checks(ctx), PLAN_HEADINGS)
    planned, plan_problems = planned_refs(ctx, bodies)
    # テストの画面から差し替えた画像はハーネスの変更なので、「変えたファイル」に数えない。
    harness = {(r["side"], r["path"]) for r in replaced_files(ctx)["replaced"]}
    return Applied(bodies, {p for p in changed_since(ctx.own, before["own"]) if ("", p) not in harness},
                   {r.name: {p for p in changed_since(r, before.get("refs", {}).get(r.name, {})) if (r.name, p) not in harness}
                    for r in ctx.refs},
                   planned, plan_problems)


def own_planned(ctx: Ctx, bodies: dict[str, str]) -> set[str]:
    body = bodies.get("## 自分の変更案", "なし")
    return set() if is_none(body) else listed_paths(ctx, body, allow_new=True)


def load_batches(ctx: Ctx) -> tuple[list[list[tuple[str, str]]], int]:
    path = ctx.data / BATCHES_NAME
    try:
        rec = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        rec = {}
    batches = [[(str(k), str(rel)) for k, rel in b] for b in rec.get("batches", [])]
    return batches, int(rec.get("done", 0))


def batch_undone(ctx: Ctx, a: "Applied", batch: list[tuple[str, str]]) -> list[str]:
    touched = {"": a.own_touched, **a.touched}
    return [side_label(ctx, k, rel) for k, rel in batch
            if not any(covered(t, {rel}) for t in touched.get(k, set()))]


def batch_step(ctx: Ctx, a: "Applied") -> int | None:
    """段に分けて変えているときの、途中の段の検査。最後の段（か、全部を変え終えたとき）は None を返し、全体を検査する。

    途中の段では、今の段までに挙げたファイルをすべて変えたかだけを確かめる（テストと検査コマンドは最後にまとめて動かす）。
    通れば次の段へ進め、出力の第 1 行を `MORE` にする（ステートマシンは apply へ戻る）。
    """
    batches, done = load_batches(ctx)
    if len(batches) <= 1 or plan_unconfirmed(ctx):
        return None
    left = [i for i, b in enumerate(batches) if batch_undone(ctx, a, b)]
    if not left:
        return None         # すべての段を変え終えた
    first = left[0]
    if first <= done:
        if done >= len(batches) - 1:
            return None     # 最後の段。全体の検査が変え残しも含めて出す
        print_problems(ctx, "apply", [
            f"段 {first + 1}/{len(batches)} のファイルをまだ変えていません（この段で挙げたファイルを変え終えてから"
            f"次の段へ進みます）: " + ", ".join(batch_undone(ctx, a, batches[first]))])
        return 1
    (ctx.data / BATCHES_NAME).write_text(json.dumps(
        {"batches": [[list(f) for f in b] for b in batches], "done": first}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print_problems(ctx, "apply", [])
    print(f"MORE 段 {first}/{len(batches)} まで変えました。次は段 {first + 1}/{len(batches)}"
          f"（`python3 {MACHINE_REL}/codd.py batch` で、その段で変えるファイルを確かめる）")
    return 0


def cmd_batch(ctx: Ctx, args: argparse.Namespace) -> int:
    """今の段で変えるファイルを示す（apply が段ごとに読む）。"""
    batches, done = load_batches(ctx)
    if len(batches) <= 1:
        print("段に分けていません。計画に挙げたファイルをすべて変えてください")
        return 0
    cur = min(done, len(batches) - 1)
    print(f"# 段 {cur + 1}/{len(batches)}" + ("（最後の段。変えたあと、全体の検査とテストが動く）"
                                              if cur == len(batches) - 1 else ""))
    print("")
    print("この段で変えるファイル（計画の該当する項目のとおりに変える）:")
    for k, rel in batches[cur]:
        print(f"- {side_label(ctx, k, rel)}")
    if cur:
        print(f"\n段 1〜{cur} は変え終えています（戻って変え直さない。直す必要に気づいたら、この段のファイルと一緒に直してよい）。")
    rest = [side_label(ctx, k, rel) for b in batches[cur + 1:] for k, rel in b]
    if rest:
        print(f"このあとの段で変えるもの（{len(rest)} files）は、まだ変えない。")
    return 0


def cmd_verify_apply(ctx: Ctx, args: argparse.Namespace) -> int:
    a = load_applied(ctx)
    if isinstance(a, str):
        print_problems(ctx, "apply", [a])
        return 1
    step = batch_step(ctx, a)
    if step is not None:
        return step
    bodies = a.bodies
    problems = plan_unconfirmed(ctx) + list(a.plan_problems)
    tp = test_plan(ctx, bodies)
    problems += tp.problems
    problems += formats_apply_problems(ctx, "\n".join(bodies.values()))
    problems += record_problems_of(ctx)

    # 1. 計画のファイルを最後まで変えたか（途中で止まっていないか）。テストの変更案のファイルも同じ。
    want_own = own_planned(ctx, bodies)
    if not is_none(bodies.get("## 自分の変更案", "なし")) and not a.own_touched:
        problems.append("自分の変更案があるのに、自分のリポジトリが変わっていません")
    else:
        undone = sorted(p for p in want_own if not any(covered(t, {p}) for t in a.own_touched))
        if undone:
            problems.append("自分の変更案のファイルをまだ変えていません（最後まで変えてください。変えなくてよくなったなら、"
                            "利用者に確かめて計画を直してください）: " + ", ".join(undone))

    for key in ["", *(r.name for r in ctx.refs)]:
        touched = a.own_touched if not key else a.touched[key]
        undone = sorted(side_label(ctx, key, p) for p in tp.paths(key, waived=False)
                        if not any(covered(t, {p}) for t in touched))
        if undone:
            problems.append(f"{TESTS_HEADING} のテストをまだ変えていません（コード・仕様書と同じく最後まで変えてください。"
                            "変えなくてよくなったなら、利用者に確かめて計画を直してください）: " + ", ".join(undone))

    # 2. 計画に無いファイルを変えていないか。
    own_allowed = (want_own | listed_paths(ctx, bodies.get("## 影響範囲", ""), allow_new=True)
                   | tp.paths("", waived=False))
    extra_own = sorted(p for p in a.own_touched if not covered(p, own_allowed))
    if extra_own:
        problems.append("計画に無いファイルを変えています（戻すか、利用者に確かめて計画の自分の変更案に足してください）: "
                        + ", ".join(extra_own))
    for r in ctx.refs:
        touched = a.touched[r.name]
        ref_tests = tp.paths(r.name, waived=False)
        if r.name in a.planned and not touched:
            problems.append(f"参照先の変更案で {r.name} を変えるはずなのに、{r.name} が変わっていません")
        elif r.name not in a.planned and not ref_tests and touched:
            problems.append(f"参照先の変更案に {r.name} は無いのに、{r.name} が変わっています（戻してください）")
        elif touched:
            extra = sorted(p for p in touched if not covered(p, a.planned.get(r.name, set()) | ref_tests))
            if extra:
                problems.append(f"{r.name} で参照先の変更案に無いファイルを変えています（戻すか、利用者に確かめて"
                                "計画の参照先の変更案に足してください）: " + ", ".join(extra))
            undone = sorted(p for p in a.planned.get(r.name, set()) if not any(covered(t, {p}) for t in touched))
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
        waived = listed_paths(ctx, bodies.get("## 影響範囲", ""), only_no_change=True) | tp.paths("")
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

    # 3'. 実際の変更が響くテストを、直したか「変更不要」としたか（同じ側のテストも。名前とつながりで測る）。
    affected: dict[tuple[str, str], str] = {}
    if tests_enabled(ctx):
        touched_all = {"": a.own_touched, **a.touched}
        changed_files = {k: {p for p in v if not is_test(ctx, k, p)} for k, v in touched_all.items()}
        texts = unique(t for key, side in all_sides(ctx) if touched_all.get(key)
                       for t in literals_from_diff(ctx, key, side))
        found = affected = affected_tests(ctx, terms, changed_files, texts)
        write_tests_report(ctx, "tests-after.md", "変えたあとに、変更が響くテスト（測定）", found, tp)
        unfixed = [side_label(ctx, k, rel) for (k, rel) in sorted(found)
                   if rel not in touched_all.get(k, set()) and (k, rel) not in tp.waived]
        if unfixed:
            problems.append("変更が響くテストのうち、直していないファイルがあります（直すか、利用者に確かめて計画の"
                            f"{TESTS_HEADING} に「{NO_CHANGE_MARK}: 理由」を書いてください）: " + ", ".join(unfixed)
                            + f"（詳細: {DATA_DIRNAME}/tests-after.md）")
        untested = untested_names(ctx, bodies, touched_all)
        if untested:
            problems.append("新しく足した名前を確かめるテストがありません（テストを足すか、利用者に確かめて計画の"
                            f"{TESTS_HEADING} に「- `名前` — {NO_CHANGE_MARK}: 理由」を書いてください）: "
                            + ", ".join(untested))

    # 4. パスのつながり。変えたファイルとつながっているほかの側のファイルを扱ったか、書き足したパスが実在するか、
    #    消したファイルを指したままのファイルが無いか。
    problems += trace_apply(ctx, a)

    # 5. 変えるときに使うと決めたスキル・道具を使ったか（.codd/apply.md に書く）。
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
    apply_log = ctx.data / "apply.md"
    if apply_log.is_file():
        skills = [*ctx.config["skills"]["apply"], *(s for r in changed for s in r.apply_skills),
                  *(s.name for s in repo_skills(ctx.root, ctx.config["skill_dirs"]))]
        used = used_skill_names(apply_log.read_text(encoding="utf-8"), unique(skills))
        since = (ctx.data / "before.json").stat().st_mtime
        problems += unread_problem(unread_skills(ctx, used, since), f"{DATA_DIRNAME}/apply.md に挙げた")

    # 6. テスト（test。単体・API・シナリオなど）と検査コマンド（check）。
    for name, argv in test_commands(ctx.config.get("test")):
        problems += run_check(ctx.root, argv, f"{SIDES[ctx.side]}のテスト{f'（{name}）' if name else ''}")
    problems += run_check(ctx.root, ctx.config.get("check"), SIDES[ctx.side])
    for r in changed:
        # 参照先のテストと検査は、参照先に置いた同じマシンの設定（codd.json の test・check）を使う。
        for name, argv in test_commands(r.test):
            problems += run_check(r.path, argv, f"{r.name}（{r.label}）のテスト{f'（{name}）' if name else ''}")
        problems += run_check(r.path, r.check, f"{r.name}（{r.label}）")
    # 7. テストで得たもの。変わった画面を貼っている文書の画像を差し替え、振る舞い・時間を写した印が今と合うかを見る。
    screens = replace_screens(ctx)
    for line in screen_lines(ctx, screens):
        print(f"テストの画面から: {line[2:]}")
    touched_all = {"": a.own_touched, **a.touched}
    docs = {(k, rel) for k, rels in touched_all.items() for rel in rels}
    docs |= {("", rel) for rel in own_allowed} | {(k, rel) for k, rels in a.planned.items() for rel in rels}
    tests = (tp.listed() | set(affected)
             | {(k, rel) for k, rels in touched_all.items() for rel in rels if is_test(ctx, k, rel)})
    problems += evidence_apply_problems(ctx, docs, tests)
    print_problems(ctx, "apply", problems)
    if problems:
        return 1
    # 通ったときの中身を控える。report はこれと今を比べ、通ったあとに変わっていないかを確かめる。
    (ctx.data / "applied.json").write_text(json.dumps(applied_state(ctx), ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    refs_note = ",".join(a.changed) or "none"
    print(f"OK own={'changed' if a.own_touched else 'same'} refs={refs_note}"
          + (f" impact={len(measured)} files（{DATA_DIRNAME}/impact-after.md）" if measured else ""))
    return 0


def trace_apply(ctx: Ctx, a: Applied) -> list[str]:
    problems: list[str] = []
    touched = {"": a.own_touched, **a.touched}
    cited = cited_anywhere(ctx, a.bodies)
    own_now = {p for p in a.own_touched if (ctx.root / p).is_file()}
    ref_links = sorted(h for h in linked(ctx, "", own_now) if h[0]) if own_now else []
    missing_refs = [ref_label(ctx, n, r) for n, r in ref_links if (n, r) not in cited and r not in a.touched[n]]
    waived = (listed_paths(ctx, a.bodies.get("## 影響範囲", ""), only_no_change=True)
              | {rel for k, rel in test_plan(ctx, a.bodies).waived if not k})
    own_links = sorted({rel for name in a.changed for k, rel in linked(ctx, name, a.touched[name]) if not k})
    missing_own = [p for p in own_links if p not in a.own_touched and not covered(p, waived)]
    broken = broken_refs(ctx, touched)
    dangling = dangling_refs(ctx, touched)
    write_trace(ctx, "trace-after.md", "変えたファイルとつながっているファイルと、パスの誤り", [
        ("自分の変えたファイルとつながっている参照先のファイル", [ref_label(ctx, n, r) for n, r in ref_links]),
        ("参照先の変えたファイルとつながっている自分のファイル", own_links),
        ("書き足したのに、どこにも無いパス", broken),
        ("消したファイルを、まだ指しているところ", dangling),
    ])
    if missing_refs:
        problems.append("自分の変えたファイルとパスでつながっている参照先のファイルを、計画で扱っていません（利用者に確かめて"
                        "計画を直してください）: " + ", ".join(missing_refs) + f"（詳細: {DATA_DIRNAME}/trace-after.md）")
    if missing_own:
        problems.append("参照先の変えたファイルとパスでつながっている自分のファイルを、直していません（直すか、利用者に確かめて"
                        f"計画の影響範囲に「{NO_CHANGE_MARK}: 理由」を書いてください）: " + ", ".join(missing_own)
                        + f"（詳細: {DATA_DIRNAME}/trace-after.md）")
    if broken:
        problems.append("書き足したパスが、どのリポジトリにもありません（綴りを直すか、指す先のファイルを計画どおりに作ってください）: "
                        + ", ".join(broken))
    if dangling:
        problems.append("消したファイルを、まだ指しているところがあります（指している側も直してください）: " + ", ".join(dangling))
    return problems


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
    tp = test_plan(ctx, a.bodies)
    after = ctx.data / "impact-after.md"
    if after.is_file():
        text = after.read_text(encoding="utf-8")
        measured = [ln[2:] for ln in text.split("## 候補のファイル", 1)[-1].splitlines() if ln.startswith("- ")]
        waived = (listed_paths(ctx, a.bodies.get("## 影響範囲", ""), only_no_change=True)
                  | {rel for key, rel in tp.waived if not key})
        impact_start = len(lines)
        lines += ["", "## 変えたあとに測った影響範囲", ""]
        for p in measured:
            mark = "直した" if p in a.own_touched else NO_CHANGE_MARK if p in waived else "未対応" \
                if not p.startswith("(") else ""
            if mark != NO_CHANGE_MARK:
                lines.append(f"- {p}" + (f" — {mark}" if mark else ""))
        if len(lines) == impact_start + 3:
            del lines[impact_start:]
    tests_after = ctx.data / "tests-after.md"
    if tp.listed() or tp.reason_only or tests_after.is_file():
        touched_all = {"": a.own_touched, **a.touched}
        tests_start = len(lines)
        lines += ["", "## テスト", ""]
        for k, rel in sorted(tp.listed()):
            done = any(covered(t, {rel}) for t in touched_all.get(k, set()))
            mark = NO_CHANGE_MARK if (k, rel) in tp.waived else "変えた" if done else "まだ"
            if mark != NO_CHANGE_MARK:
                lines.append(f"- {side_label(ctx, k, rel)} — {mark}")
        if tests_after.is_file():
            extra = [ln for ln in tests_after.read_text(encoding="utf-8").splitlines()
                     if ln.startswith("- ") and ln.endswith("（計画に無い）")]
            lines += extra
        if len(lines) == tests_start + 3:
            del lines[tests_start:]
    screens = screen_lines(ctx, replaced_files(ctx))
    if screens:
        lines += ["", "## テストの画面から差し替えた文書の画像", "", *screens]
    changes = evidence_changes(ctx)
    if changes:
        lines += ["", "## テストで得たものの変化（計画のときと比べて）", "", *changes[:MAX_MEASURED],
                  "", "（振る舞いや時間を文書に活かすなら `<!-- evidence: id -->…<!-- /evidence -->` で写す。"
                  "画面は結果の画像を文書に貼れば、以後は差し替える）"]
    own_now = sorted(p for p in a.own_touched if (ctx.root / p).is_file())
    lonely = [p for p in own_now if not any(k for k, _ in linked(ctx, "", {p}))]
    if lonely:
        lines += ["", "## 参照先とパスでつながっていない変更", "",
                  *[f"- {p}" for p in lonely],
                  "", "（つなぐなら、ファイルに `coherence: doc=パス` のように書くか、参照先の文書からパスで指す）"]
    todo = a.bodies.get("## 今回やらないこと", "なし")
    lines += ["", "## 次にやること（今回やらないこと）", "", todo if not is_none(todo) else "- なし", "",
              "どちらのリポジトリもコミットしていない。内容を確かめてから、それぞれでコミットする。", ""]
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    clear_reading_logs(ctx)   # 報告で 1 回の実行が終わる。探した・読んだ記録は次の回に持ち越さない
    return 0 if state == "通った" else 1


# ---------------------------------------------------------------- 止まったとき（次の手を示す・やり直す）

PROBLEMS_NAME = "problems.json"


def record_problems(data: Path, phase: str, problems: list[str], kind: str | None = None) -> None:
    """検査で止めた理由を控える（advise が読む）。通ったら消す。"""
    path = data / PROBLEMS_NAME
    if not problems:
        path.unlink(missing_ok=True)
        (data / AUTO_NAME).unlink(missing_ok=True)   # 通ったら、人に訊かずに進めた回数も数え直す
        return
    data.mkdir(parents=True, exist_ok=True)
    items = [{"kind": kind or classify(phase, p), "text": p} for p in problems]
    path.write_text(json.dumps({"phase": phase, "problems": items}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# 止めた理由の分類。上から順に当てる。（種類, 段, 目印）
_KINDS = (
    ("stale", "any", ("計画がありません", "印がありません", "印が古い形")),
    ("unconfirmed", "apply", ("利用者が確かめたものではありません",)),
    ("pending", "plan", (f"「{PENDING_MARK}」",)),
    ("size", "plan", ("上限",)),
    ("extra", "apply", ("計画に無いファイルを変えています", "変更案に無いファイルを変えています", "が変わっています（戻してください）")),
    ("undone", "apply", ("まだ変えていません", "が変わっていません")),
    ("unfixed", "apply", ("直していないファイル", "自分のファイルを、直していません")),
    ("ref-coverage", "any", ("計画で扱っていません",)),
    ("paths", "apply", ("どのリポジトリにもありません", "まだ指しているところ")),
    ("rules", "any", ("守る決まり", "スキル・道具", "リポジトリのスキル")),
    ("check", "apply", ("検査が失敗しました",)),
)


def classify(phase: str, text: str) -> str:
    for kind, where, marks in _KINDS:
        if where in ("any", phase) and any(m in text for m in marks):
            return kind
    return "form"


# 選択肢。（見出し, 先に実行する codd.py のコマンド（無ければ ""）, 止まった段が出す語）
OPTIONS = {
    "replan": ("計画を練り直す", "", "PLAN"),
    "reapply": ("計画はそのままで、変え直す", "", "APPLY"),
    "keep": ("変えた分は残して、計画を直す", "keep-changes", "PLAN"),
    "reset": ("変えた分を戻して、計画から練り直す", "rollback", "PLAN"),
    "stop": ("ここでやめる（変えた分を残すか戻すかも訊く）", "", "STOP"),
}

# 種類ごとの選択肢（最初が勧め）と、利用者に確かめること。最初に挙がった理由の勧めを、全体の勧めにする。
ADVICE = {
    "plan": {
        "stale": (["replan", "stop"], "計画がまだ無いか、読めません。やりたいことをもう一度伝えてもらい、練り直します"),
        "size": (["replan", "stop"], "1 回で変えるには大きすぎます。今回やることを絞ってもらい、残りは「今回やらないこと」に回します"),
        "pending": (["replan", "stop"],
                    f"測ったファイルのうち、計画が「{PENDING_MARK}」のままのものがあります。ファイルごとに、直すか"
                    "「変更不要」か関係が無いかを決めてもらいます"),
        "rules": (["replan", "stop"], "決まり・スキル・道具を計画が扱っていません。それらを使って練り直します"),
        "form": (["replan", "stop"], "計画の形か根拠が決まりどおりではありません。参照先を読み直して練り直します"),
    },
    "apply": {
        "stale": (["reset", "stop"], "変える前の印がありません。計画から練り直します"),
        "unconfirmed": (["keep", "reset", "stop"],
                        "確かめていない計画で変えました。変えた分を残すか戻すかを決めてもらい、計画の検査と確認からやり直します"),
        "extra": (["reapply", "keep", "reset", "stop"],
                  "計画に無いファイルを変えました。その変更を戻して変え直すか、計画に足すかを決めてもらいます"),
        "undone": (["reapply", "keep", "reset", "stop"],
                   "計画のファイルを変え残しています。変え切るか、計画から外すかを決めてもらいます"),
        "unfixed": (["reapply", "keep", "reset", "stop"],
                    "変更の影響を受けるファイルを直していません。直すか、「変更不要」として計画に書くかを決めてもらいます"),
        "ref-coverage": (["keep", "reapply", "reset", "stop"],
                         "計画に無い参照先に響く変更をしました。計画に足すか、響かないように変え直すかを決めてもらいます"),
        "paths": (["reapply", "keep", "stop"], "書いたパスが無いか、消したファイルがまだ指されています。指す先を直します"),
        "rules": (["reapply", "stop"], "変えるときのスキル・道具の記録がありません。使って記録します"),
        "check": (["reapply", "reset", "stop"], "検査コマンドが通りません。直して変え直すか、計画から練り直すかを決めてもらいます"),
        "form": (["reapply", "reset", "stop"], "変えた結果が計画と合いません"),
        "config": (["reapply", "stop"], "設定か環境の誤りです。利用者に直してもらってから、同じ段をやり直します"),
    },
}
ADVICE["plan"]["config"] = (["replan", "stop"], "設定か環境の誤りです。利用者に直してもらってから、練り直します")


# 人の判断が要らない理由（エージェントが自分で直せる。テストや検査の失敗・変え残し・パスの誤り・記録の漏れ・計画の形）。
# これだけで止まったときは、利用者に訊かずに同じ段をやり直す。計画を変える・範囲を絞る・戻すなどの判断が要るものは訊く。
AUTO_KINDS = {"plan": {"pending", "rules", "form"}, "apply": {"check", "undone", "paths", "rules", "form"}}
AUTO_NAME = "auto.json"
MAX_AUTO = 2     # 同じ段で人に訊かずに進める回数。直らない失敗をいつまでも回さない


def auto_step(data: Path, phase: str, kinds: set[str]) -> str | None:
    """人の判断が要らない理由だけなら、利用者に訊かずに進める語（PLAN・APPLY）。上限を超えたら None。"""
    if not kinds or not kinds <= AUTO_KINDS.get(phase, set()):
        return None
    try:
        count = json.loads((data / AUTO_NAME).read_text(encoding="utf-8")).get(phase, 0)
    except (OSError, ValueError, AttributeError):
        count = 0
    if count >= MAX_AUTO:
        return None
    (data / AUTO_NAME).write_text(json.dumps({phase: count + 1}) + "\n", encoding="utf-8")
    word = "PLAN" if phase == "plan" else "APPLY"
    log = []
    try:
        log = json.loads((data / DECISIONS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    log.append({"at": time.strftime("%Y-%m-%d %H:%M"), "answer": word,
                "note": f"人の判断が要らない指摘なので、訊かずにやり直した（{count + 1}/{MAX_AUTO} 回目）"})
    (data / DECISIONS_NAME).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return word


def cmd_advise(root: Path) -> int:
    """止めた理由を読み、何が止めているか・どうしたらいいか（勧めと選択肢）を示す。"""
    data = root / DATA_DIRNAME
    path = data / PROBLEMS_NAME
    if not path.is_file():
        print("止めている理由は控えられていません（検査は通っています）。続きから進めてください")
        return 0
    rec = json.loads(path.read_text(encoding="utf-8"))
    phase = rec.get("phase", "plan")
    table = ADVICE.get(phase, ADVICE["plan"])
    stage = "計画の検査" if phase == "plan" else "変えたあとの検査"
    lines = [f"# {stage}で止まりました", "", "## 止めている理由", ""]
    order: list[str] = []
    asks: list[str] = []
    for item in rec.get("problems", []):
        kind = item.get("kind", "form")
        options, ask = table.get(kind, table["form"])
        lines.append(f"- {item['text']}")
        if ask not in asks:
            asks.append(ask)
        order += [o for o in options if o not in order]
    word = auto_step(data, phase, {item.get("kind", "form") for item in rec.get("problems", [])})
    if word:
        lines += ["", "## 利用者に訊かずに進める", "",
                  f"人の判断が要らない指摘だけです。利用者に訊かず、答えを待たずに `{word}` で同じ段をやり直してください"
                  "（指摘を直す。控えは済んでいるので decide は要らない）"]
        text = "\n".join(lines) + "\n"
        (data / "advice.md").write_text(text, encoding="utf-8")
        print(f"AUTO {word}\n\n" + text, end="")
        return 0
    lines += ["", "## 利用者に確かめること", "", *[f"- {a}" for a in asks], "", "## 選択肢（最初が勧め）", ""]
    for i, key in enumerate(order):
        title, command, word = OPTIONS[key]
        run_it = f"。先に `python3 {MACHINE_REL}/codd.py {command}` を実行" if command else ""
        lines.append(f"{i + 1}. {title}{'（勧め）' if i == 0 else ''} → `{word}`{run_it}")
    text = "\n".join(lines) + "\n"
    (data / "advice.md").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


def cmd_keep_changes(ctx: Ctx, args: argparse.Namespace) -> int:
    """次の計画の検査で、変える前の印を取り直さない（変えた分を残したまま計画を直す）。"""
    if not (ctx.data / "before.json").is_file():
        print("変える前の印がありません。計画から練り直してください", file=sys.stderr)
        return 1
    (ctx.data / KEEP_MARK).write_text("", encoding="utf-8")
    print("変えた分を残します。次の計画の検査は、前の印から変わったファイルを数えます")
    return 0


def cmd_rollback(ctx: Ctx, args: argparse.Namespace) -> int:
    """計画の検査が通ったとき（変える前）の中身へ戻す。戻すのは、そのあとに変わったファイルだけ。"""
    before_file = ctx.data / "before.json"
    if not before_file.is_file():
        print("変える前の印がありません（戻すものはありません）", file=sys.stderr)
        return 1
    before = json.loads(before_file.read_text(encoding="utf-8"))
    plan: list[tuple[str, Side, str, dict]] = []
    for key, side in all_sides(ctx):
        snap = before.get("refs", {}).get(key, {}) if key else before.get("own", {})
        if snap.get("head") and head(side.path) != snap["head"]:
            print(f"{key or '自分'} は途中でコミットされたので戻せません（git で戻してください）",
                  file=sys.stderr)
            return 1
        plan += [(key, side, rel, snap) for rel in sorted(changed_since(side, snap))]
    restored = []
    for key, side, rel, snap in plan:
        target = side.path / rel
        saved = snap.get("files", {}).get(rel)
        if saved and saved != "deleted":
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_dir(ctx, key) / rel, target)
        elif saved != "deleted" and snap.get("head") and run(
                ["git", "cat-file", "-e", f"{snap['head']}:{rel}"], side.path, GIT_TIMEOUT)[0] == 0:
            blob = subprocess.run(["git", "show", f"{snap['head']}:{rel}"], cwd=side.path, capture_output=True,
                                  timeout=GIT_TIMEOUT).stdout
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        elif target.is_file():
            target.unlink()   # 変えたあとに足したファイル（か、変える前にも消えていたファイル）
        restored.append(side_label(ctx, key, rel))
    for name in ("applied.json", KEEP_MARK, PROBLEMS_NAME):
        (ctx.data / name).unlink(missing_ok=True)
    print(f"変える前に戻しました: {len(restored)} files" + ("".join(f"\n  - {r}" for r in restored)))
    return 0


# ---------------------------------------------------------------- 計画を少しずつ書く・短く見せる
#
# Copilot などは 1 回の応答の長さに上限があり、計画を一度に全文書く・全文を貼ると
# 「the response hit the length limit」で止まる。ひな形を置いて見出しごとに書かせ、確認では要約だけを見せる。

SUMMARY_SECTIONS = ("## ずれ", "## 自分の変更案", "## 参照先の変更案", "## 影響範囲", "## テストの変更案", "## 今回やらないこと")
SUMMARY_ITEMS = 12
SUMMARY_WIDTH = 120


def cmd_draft(ctx: Ctx, args: argparse.Namespace) -> int:
    """ひな形を .plans/日時-名前.md に置く（進めている計画があれば残す）。見出しごとにコメントを本文へ置き換えて書く。"""
    if ctx.plan.is_file() and not args.new:
        print(f"計画はもうあります: {plan_rel(ctx)}（直す見出しだけを書き換える。最初から書き直すときは --new）")
        return 0
    name = (args.name or "").strip().lower()
    if not _PLAN_NAME.match(name) or len(name) > 40:
        print("計画の名前を、英語の短い名前で渡してください（小文字・数字・ハイフン。40 文字まで。"
              "例: `--name hello-returns-two`）", file=sys.stderr)
        return 2
    if ctx.plan.is_file():
        ctx.plan.unlink()   # --new: 進めていた計画を捨てて書き直す
    base = f"{time.strftime('%Y-%m-%d-%H%M')}-{name}"
    dest, n = ctx.root / PLAN_DIR / f"{base}.md", 2
    while dest.exists():
        dest, n = ctx.root / PLAN_DIR / f"{base}-{n}.md", n + 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MACHINE_DIR / "templates" / "plan.md", dest)
    print(f"ひな形を置きました: {dest.relative_to(ctx.root).as_posix()}（見出しごとに、コメントを本文に置き換える）")
    return 0


def _clip(line: str) -> str:
    return line if len(line) <= SUMMARY_WIDTH else line[:SUMMARY_WIDTH - 1] + "…"


def cmd_summary(ctx: Ctx, args: argparse.Namespace) -> int:
    """計画の要約（やりたいこと・ずれ・変えるファイル・テスト・今回やらないこと）。確認で全文の代わりに見せる。"""
    if not ctx.plan.is_file():
        print(NO_PLAN, file=sys.stderr)
        return 1
    _, bodies = sections(compact_plan(ctx.plan.read_text(encoding="utf-8")), PLAN_HEADINGS)
    rel = ctx.plan.relative_to(ctx.root).as_posix()
    lines = ["# 計画の要約", "", f"全文: {rel}", ""]
    want = [ln.strip() for ln in bodies.get("## やりたいこと", "").splitlines() if ln.strip()]
    lines += ["## やりたいこと", "", *([_clip(ln) for ln in want[:3]] or ["（未記入）"])]
    counted = [h for h in ("## 守る決まり", "## 使ったスキルと道具", "## 参照先の前提", "## 参照先の制約", "## 参照先のその他")
               if h in bodies and not is_none(bodies[h])]
    if counted:
        lines += ["", "根拠: " + "・".join(
            f"{h[3:]} {0 if is_none(bodies[h]) else sum(1 for ln in bodies[h].splitlines() if re.match(r'^[-*] ', ln))} 件"
            for h in counted)]
    for heading in SUMMARY_SECTIONS:
        body = bodies.get(heading)
        if body is None or is_none(body):
            continue
        items = [ln.strip() for ln in body.splitlines() if re.match(r"^[-*] ", ln.strip())]
        lines += ["", heading, ""]
        if is_none(body) or not items:
            lines.append("- なし" if is_none(body) or not body else _clip(body.splitlines()[0]))
            continue
        lines += [_clip(ln) for ln in items[:SUMMARY_ITEMS]]
        if len(items) > SUMMARY_ITEMS:
            lines.append(f"- ほか {len(items) - SUMMARY_ITEMS} 件（{rel}）")
    print("\n".join(lines))
    return 0


DECISIONS = {"OK": "計画で進める", "NG": "計画を直す", "PLAN": "計画を練り直す", "APPLY": "計画はそのままで変え直す",
             "STOP": "やめる"}
DECISIONS_NAME = "decisions.json"


def load_decisions(ctx: Ctx) -> list[dict]:
    try:
        return json.loads((ctx.data / DECISIONS_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def cmd_decide(ctx: Ctx, args: argparse.Namespace) -> int:
    """利用者の答えを控える。計画の本文は練り直しで書き換わるので、答えは別に持ち、報告で記録に書く。"""
    log = load_decisions(ctx)
    log.append({"at": time.strftime("%Y-%m-%d %H:%M"), "answer": args.answer, "note": args.note.strip()})
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / DECISIONS_NAME).write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.answer in ("NG", "STOP"):
        (ctx.data / PASSED_PLAN).unlink(missing_ok=True)   # 退けた計画では変えさせない
    print(f"控えました: {args.answer}（{DECISIONS[args.answer]}）")
    return 0


def record_problems_of(ctx: Ctx) -> list[str]:
    """終わった回の計画の記録（コミット済み）を書き換えていないか。練り直しで直してよいのは、いま進めている計画だけ。"""
    rc, out = run(["git", "-c", "core.quotepath=false", "status", "--porcelain", "--", PLAN_DIR], ctx.root, GIT_TIMEOUT)
    changed = [ln[3:] for ln in out.splitlines() if rc == 0 and ln[:2].strip() and not ln.startswith("??")
               and ln[3:] != plan_rel(ctx)]
    return ["終わった回の計画の記録を書き換えています（判断の記録なので変えない。直すのは "
            f"いま進めている計画だけ。戻すなら `git checkout -- パス`）: " + ", ".join(changed)] if changed else []


def cmd_record(ctx: Ctx, args: argparse.Namespace) -> int:
    """1 回の実行の終わりに、計画を判断の記録として残す（done・stopped で report のあとに呼ぶ）。"""
    report = ctx.data / "report.md"
    if report.is_file():
        result = report.read_text(encoding="utf-8").splitlines()[2:]
        report.unlink()   # 次の回の記録に、この回の結果を混ぜない
    else:
        result = ["- 変えていない（変えたあとの検査まで進まなかった）"]
    record = finalize_plan(ctx, result)
    if not record:
        print(NO_PLAN, file=sys.stderr)
        return 1
    print(f"計画の記録: {record}（確認の答えと結果を書き足した。コミットしてよい）")
    return 0


# 記録に残さない行: 使わなかったスキル・関係の無かったファイルの判断と、書き換え忘れたひな形の説明。
_NOT_RECORDED = re.compile(r"^-\s*(?:`[^`]+`\s*—\s*)?使わない[:：]|^-\s*関係なし[:：]|^<!--.*-->$")


def compact_plan(text: str) -> str:
    """説明コメントと変更不要の項目を利用者向けの計画から除く。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    out: list[str] = []
    heading, skip = "", False
    for line in text.splitlines():
        if line.startswith("#"):
            heading, skip = line, False
        if heading in ("## 影響範囲", TESTS_HEADING) and re.match(r"^[-*]\s", line.strip()):
            skip = judgment(line.strip()).waived
        elif skip and line.strip() and not line.startswith((" ", "\t")):
            skip = False
        if not skip:
            out.append(line)
    text = "\n".join(out)
    # 既存の検査形式を保つ。省略した判断は内部の plan-check.json で補う。
    _, bodies = sections(text, PLAN_HEADINGS)
    for head, body in bodies.items():
        if not body:
            text = re.sub(rf"^{re.escape(head)}[ \t]*$", head + "\n\nなし", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def prune_plan(text: str) -> str:
    """記録に要らないもの（使わなかったスキル・関係なしとしたファイル・中身が「なし」の見出し）を除く。"""
    out: list[str] = []
    head: list[str] = []      # 見出しと、その下に残す行
    skip = False
    def flush() -> None:
        body = [ln for ln in head[1:] if ln.strip()]
        if head and (not head[0].startswith("## ") or (body and not is_none("\n".join(body)))):
            out.extend(head)
    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            head, skip = [line], False
            continue
        if _NOT_RECORDED.match(line.strip()) and not line.startswith(" "):
            skip = True
            continue
        if skip and line.startswith((" ", "\t")) and line.strip():
            continue      # 除いた項目の続きの行
        skip = False
        head.append(line)
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def finalize_plan(ctx: Ctx, result: list[str]) -> str | None:
    """1 回の実行の計画に、確認の答えと結果を書き足して、判断の記録としてそのまま残す。要らない情報は除く。"""
    if not ctx.plan.is_file():
        return None
    decisions = load_decisions(ctx)
    lines = [prune_plan(compact_plan(ctx.plan.read_text(encoding="utf-8"))), "", "## 確認と判断", ""]
    lines += [f"- {d['at']} {d['answer']}（{DECISIONS.get(d['answer'], '')}）" + (f": {d['note']}" if d["note"] else "")
              for d in decisions] or ["- 記録なし"]
    lines += ["", RESULT_HEADING, "", *[("#" + ln if ln.startswith("## ") else ln) for ln in result], ""]
    ctx.plan.write_text("\n".join(lines), encoding="utf-8")
    (ctx.data / DECISIONS_NAME).unlink(missing_ok=True)
    return plan_rel(ctx)


# ---------------------------------------------------------------- 点検（本流とは別。読むだけ）
#
# 本流は依頼が触れた範囲しか見ないので、codd を通らずに入った変更や、もとからある食い違いは残る。
# 点検はリポジトリ全体に本流と同じ部品を当て、見つけたものを本流に渡す「やりたいこと」の 1 行にする。
# 直すのは本流（変える道を 1 本に保つ）。書くのは作業フォルダの .codd/lint.md だけ。

LINT_SINCE = "30 days ago"


def tracked(side: Side) -> list[str]:
    rc, out = run(["git", "-c", "core.quotepath=false", "ls-files", *side.pathspec()], side.path, GIT_TIMEOUT)
    return [ln for ln in out.splitlines() if side.has(ln)] if rc == 0 else []


def lint_paths(ctx: Ctx) -> list[tuple[str, str]]:
    """文書のリンクと注記（coherence:）が指すパスのうち、どの側にも無いもの。（見つけたこと, やりたいこと）"""
    out = []
    for key, side in all_sides(ctx):
        files = {rel for rel in tracked(side) if rel.lower().endswith(DOC_EXTS)}
        files |= set(git_grep(side, ["-l", "-I", "-F", "coherence:"]))
        for rel in sorted(files):
            for claim in claims_in(rel, read_text(side.path / rel) or ""):
                if claim.kind == "code" or not pathlike(claim.token) or "\\" in claim.token:
                    continue   # `…` のパスは、別のリポジトリのファイルや書き方の例が多い。全体ではリンクと注記だけを見る
                r = resolve(ctx, side, rel, claim)
                if r.candidates and not r.exists:
                    where = f"{side_label(ctx, key, rel)}:{claim.line}"
                    out.append((f"{where} → {claim.token}",
                                f"`{where}` が指す `{claim.token}` が無いので、指す先を今のファイルに合わせたい"))
    return out


def lint_formats(ctx: Ctx) -> list[tuple[str, str]]:
    """同じフォルダのほかの文書がみな持つ見出しを、欠いている文書（ほかに 2 つ以上あるときだけ）。"""
    out = []
    for key, side in all_sides(ctx):
        folders: dict[str, list[str]] = {}
        for rel in tracked(side):
            if rel.lower().endswith(FORMAT_EXTS) and not is_test(ctx, key, rel) \
                    and Path(rel).stem.lower() not in _NOT_MODELS:
                folders.setdefault(str(Path(rel).parent), []).append(rel)
        for rels in folders.values():
            if len(rels) < 3:
                continue
            heads = {rel: doc_headings(read_text(side.path / rel) or "") for rel in rels}
            for rel in rels:
                others = [heads[o] for o in rels if o != rel]
                common = [h for h in others[0] if all(h in o for o in others[1:])]
                missing = [h for h in common if h not in heads[rel]]
                if missing:
                    label = side_label(ctx, key, rel)
                    shown = " / ".join(h.lstrip("# ") for h in missing[:5])
                    out.append((f"{label} — 欠けている見出し: {shown}",
                                f"`{label}` の見出しを、同じフォルダの文書の書式（{shown}）に合わせたい"))
    return out


def lint_evidence(ctx: Ctx, tested: bool) -> list[tuple[str, str]]:
    marks = evidence_marks(ctx)
    if not marks:
        return []
    evs = all_evidence(ctx)
    if not any(e.files for e in evs.values()):
        if not tested:
            return []   # テストを動かしていなければ、結果が無いのは当たり前（結果はふつうコミットしない）
        return [("文書がテストの結果を写していますが、テストで得たもの（evidence）がありません",
                 "テストを動かして、文書に写したテストの結果を確かめたい")]
    stale, bad = check_marks(ctx, evs, marks)
    return ([(b, f"{b.split(' — ')[0]} の求めを満たすよう、実装か文書を直したい") for b in bad]
            + [(st, f"{st.split('（')[0]} に写したテストの結果を、今の値に写し直したい") for st in stale])


def lint_tests(ctx: Ctx) -> list[tuple[str, str]]:
    """両側のテスト（設定の test）を動かす。検査（check）はファイルを作り直すことがあるので動かさない。"""
    out = []
    sides = [("自分", ctx.root, ctx.config.get("test")), *((r.name, r.path, r.test) for r in ctx.refs)]
    for who, repo, test in sides:
        for name, argv in test_commands(test):
            before = run(["git", "status", "--porcelain"], repo, GIT_TIMEOUT)[1]
            label = f"{who}のテスト{f'（{name}）' if name else ''}"
            for p in run_check(repo, argv, label):
                out.append((p, f"{label}（{' '.join(argv)}）が落ちているので直したい"))
            if run(["git", "status", "--porcelain"], repo, GIT_TIMEOUT)[1] != before:
                out.append((f"{label}を動かしたら、作業中のファイルが変わりました（点検は直さないので、確かめてください）",
                            f"{label}が書き換えるファイルを、コミットするものか .gitignore に入れるものかに分けたい"))
    return out


def plan_records(ctx: Ctx) -> str:
    """どの側のリポジトリにもある、終わった回の計画の記録（codd を通った変更の手がかり）。"""
    text = []
    for repo in unique_paths(str(s.path) for _, s in all_sides(ctx)):
        for folder in (Path(repo) / PLAN_DIR,):
            text += [read_text(p) or "" for p in sorted(folder.glob("*.md"))] if folder.is_dir() else []
    return "\n".join(text)


def lint_bypassed(ctx: Ctx, since: str) -> list[tuple[str, str]]:
    """codd を置いたあと、期間のうちにコミットされた変更で、計画の記録に出てこないファイル（codd を通らずに入った変更）。"""
    records = plan_records(ctx)
    out = []
    for key, side in all_sides(ctx):
        # codd を置いたコミットより前は、codd を通りようがないので数えない。
        placed = run(["git", "log", "--format=%H", "--diff-filter=A", "--", f"{MACHINE_REL}/{CONFIG_NAME}"],
                     side.path, GIT_TIMEOUT)[1].split()
        rng = [f"{placed[-1]}..HEAD"] if placed else []
        rc, log = run(["git", "-c", "core.quotepath=false", "log", *rng, f"--since={since}", "--no-merges",
                       "--date=short", "--format=%x00%h %ad", "--name-only", *side.pathspec()], side.path, GIT_TIMEOUT)
        seen: set[str] = set()
        for chunk in (log.split("\0") if rc == 0 else []):
            first, *names = chunk.strip().splitlines() or [""]
            labels = []
            for rel in names:
                rel = rel.strip()
                if not rel or rel in seen or not side.has(rel) or rel in records:
                    continue
                seen.add(rel)   # 新しいコミットから見るので、同じファイルは最後に変えたコミットで挙げる
                labels.append(side_label(ctx, key, rel))
                gone = "（いまは無い）" if not (side.path / rel).exists() else ""
                out.append((f"{labels[-1]}{gone} — {first}", ""))
            if labels:   # やりたいことはコミットごとに 1 行
                shown = ", ".join(f"`{x}`" for x in labels[:5]) + (f" ほか {len(labels) - 5} 件" if len(labels) > 5 else "")
                out[-1] = (out[-1][0], f"codd を通らずに入った変更（{first}: {shown}）に合わせて、"
                                       "コード・文書・テストの食い違いを確かめたい")
    return out


LINT_SECTIONS = (("壊れたパス", "paths"), ("文書の書式", "formats"), ("テストの結果を写した文書", "evidence"),
                  ("テスト", "tests"), ("codd を通らなかった変更", "bypassed"))
LINT_SHOWN = 30


def cmd_lint(ctx: Ctx, args: argparse.Namespace) -> int:
    found = {"paths": lint_paths(ctx), "formats": lint_formats(ctx),
             "tests": [] if args.no_test else lint_tests(ctx)}
    found["evidence"] = lint_evidence(ctx, not args.no_test)     # テストのあとに見る（テストが evidence を書き直す）
    found["bypassed"] = lint_bypassed(ctx, args.since)
    lines = [f"# 点検の結果（{time.strftime('%Y-%m-%d %H:%M')}。変更は {args.since} から）", ""]
    for title, key in LINT_SECTIONS:
        if key == "tests" and args.no_test:
            continue
        lines += [f"## {title}", "", *([f"- {f}" for f, _ in found[key]] or ["- なし"]), ""]
    asks = list(dict.fromkeys(a for key in found for _, a in found[key] if a))
    lines += ["## 本流に渡すやりたいこと", "", *([f"- {a}" for a in asks] or ["- なし"]), ""]
    ctx.data.mkdir(parents=True, exist_ok=True)
    (ctx.data / "lint.md").write_text("\n".join(lines), encoding="utf-8")
    counts = "、".join(f"{t} {len(found[k])}" for t, k in LINT_SECTIONS if not (k == "tests" and args.no_test))
    print(f"{'NG' if asks else 'OK'} 点検: {counts}（全文: {DATA_DIRNAME}/lint.md）")
    for a in asks[:LINT_SHOWN]:
        print(f"- {a}")
    if len(asks) > LINT_SHOWN:
        print(f"- ほか {len(asks) - LINT_SHOWN} 件（{DATA_DIRNAME}/lint.md）")
    return 1 if asks else 0


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
    sub.add_parser("batch", help="段に分けて変えるとき、今の段で変えるファイルを示す")
    sub.add_parser("report", help="変えた結果をまとめる（終わりの報告）")
    dr = sub.add_parser("draft", help="計画のひな形を .plans/日時-名前.md に置く（進めている計画があれば残す）")
    dr.add_argument("--name", help="計画の英語の短い名前（小文字・数字・ハイフン。例: hello-returns-two）")
    dr.add_argument("--new", action="store_true", help="進めている計画を捨ててひな形から書き直す")
    sub.add_parser("summary", help="計画の要約を示す（確認で全文の代わりに見せる）")
    sub.add_parser("record", help="計画に確認の答えと結果を書き足し、判断の記録として残す（終わりに呼ぶ）")
    de = sub.add_parser("decide", help="確認・相談での利用者の答えを控える（終わりの報告で計画の記録に書く）")
    de.add_argument("answer", choices=list(DECISIONS), help="OK / NG（確認）か PLAN / APPLY / STOP（止まったときの相談）")
    de.add_argument("--note", default="", help="利用者の指摘や指示（そのまま）")
    sub.add_parser("advise", help="検査で止まった理由と、次の手（勧めと選択肢）を示す")
    ru2 = sub.add_parser("rule", help="守る決まりのファイルを出して読み込む（読み込んだことを控え、計画の検査が確かめる）")
    ru2.add_argument("path", nargs="*", help="決まりのファイル（`show` の一覧の書き方。参照先のものは `名前:パス`）")
    ru2.add_argument("--all", action="store_true", help="守る決まりのファイルをすべて読み込む")
    ru2.add_argument("--again", action="store_true", help="この回で読み込み済みのものも出し直す")
    sk = sub.add_parser("skill", help="スキルの SKILL.md を出して読み込む（読み込んだことを控え、検査が確かめる）")
    sk.add_argument("name", nargs="+", help="スキルの名前（参照先のものは `参照先の名前:名前`）")
    sub.add_parser("keep-changes", help="変えた分を残したまま計画を直す（次の計画の検査で印を取り直さない）")
    sub.add_parser("rollback", help="計画の検査が通ったとき（変える前）の中身へ戻す")
    ev = sub.add_parser("evidence", help="テストで得たもの（振る舞い・時間・画像）と、それを写した文書の印を示す")
    ev.add_argument("path", nargs="*", help="見る・写し直す文書（参照先は `名前:パス`。既定はすべて）")
    ev.add_argument("--write", action="store_true", help="文書の印を今の値に写し直す")
    au = sub.add_parser("lint", help="本流とは別の点検。リポジトリ全体の食い違いを探し、本流に渡すやりたいことにする（直さない）")
    au.add_argument("--since", default=LINT_SINCE, help=f"codd を通らなかった変更を探す期間の始め（git の日付。既定 {LINT_SINCE!r}）")
    au.add_argument("--no-test", action="store_true", help="テスト（設定の test）を動かさない")
    ru = sub.add_parser("rules", help="守る決まりのファイルと、決まりらしい候補を示す")
    ru.add_argument("--write", action="store_true", help="候補を codd.json の rules / refs[].rules に書く")
    ru.add_argument("--only", action="append", help="書く候補を絞る（`名前:パス` か `パス`。繰り返し可）")
    return p


COMMANDS = {"show": cmd_show, "explore": cmd_explore, "impact": cmd_impact,
            "verify-plan": cmd_verify_plan, "verify-apply": cmd_verify_apply, "batch": cmd_batch, "report": cmd_report,
            "rules": cmd_rules, "keep-changes": cmd_keep_changes, "rollback": cmd_rollback,
            "skill": cmd_skill, "evidence": cmd_evidence, "rule": cmd_rule,
            "draft": cmd_draft, "summary": cmd_summary, "decide": cmd_decide, "record": cmd_record,
            "lint": cmd_lint}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = repo_root(Path.cwd())
        if args.cmd == "advise":   # 設定が壊れていても理由と次の手は示す
            return cmd_advise(root)
        return COMMANDS[args.cmd](Ctx(root), args)
    except CoddError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        if args.cmd in ("verify-plan", "verify-apply"):
            try:
                record_problems(repo_root(Path.cwd()) / DATA_DIRNAME, args.cmd.split("-")[1], [str(exc)], "config")
            except CoddError:
                pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
