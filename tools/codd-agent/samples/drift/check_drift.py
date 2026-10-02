#!/usr/bin/env python3
"""codd-drift の下請け。モデルが書いた候補と判断の形を確かめ、終わりの報告を出す（依存は python3 と git だけ）。

    candidates  .codd/drift-candidates.md の組が決まった形で、指す行が実在するか
    judged      .codd/drift.md が候補の組をすべて判断し、ずれに「やりたいこと」を添えたか
    report      判断をまとめて出す（report の段がそのまま伝える）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

MAX_PAIRS = 10
VERDICTS = ("ずれ", "合っている", "判断できない")
_PAIR = re.compile(r"^- (?:(?P<verdict>[^:\s]+): )?(?P<a>\S+) ⇔ (?P<b>\S+) — (?P<why>.+)$")
_ASK = re.compile(r"^\s+- やりたいこと: \S")


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("git のリポジトリの中で実行してください")
    return Path(out.stdout.strip())


def refs(root: Path) -> dict[str, Path]:
    config = root / ".statemachine/codd/codd.json"
    data = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    named = {}
    for r in data.get("refs", []):
        if "path" in r:   # 名前を省けば、パスの最後のフォルダ名（codd と同じ）
            named[r.get("name") or Path(str(r["path"]).rstrip("/\\")).name] = (root / r["path"]).resolve()
    return named


def where_problem(root: Path, named: dict[str, Path], token: str) -> str | None:
    """`パス:行` か `参照先の名前:パス:行` が実在する行を指しているか。"""
    path, _, line = token.rpartition(":")
    if not line.isdigit() or not path:
        return f"{token} — `パス:行` の形ではありません"
    name, sep, rest = path.partition(":")
    if sep and name in named:
        bases, path = [named[name]], rest
    else:
        bases = [root, *named.values()]   # 名前が無ければ自分、無ければ参照先（参照先が 1 つならパスだけでよい）
    target = next((b / path for b in bases if (b / path).is_file()), None)
    if target is None:
        return f"{token} — ファイルがありません"
    count = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
    if not 1 <= int(line) <= count:
        return f"{token} — {count} 行までのファイルです"
    return None


def pairs(path: Path) -> tuple[list[re.Match], list[str], bool]:
    """（組, 形の違う行, 「なし」か）。"""
    if not path.is_file():
        return [], [f"{path.name} がありません"], False
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if [ln.strip() for ln in lines] == ["なし"]:
        return [], [], True
    found, bad = [], []
    for ln in lines:
        if _ASK.match(ln) or not ln.startswith("- "):
            continue
        m = _PAIR.match(ln)
        (found.append(m) if m else bad.append(f"形が違います: {ln[:120]}"))
    return found, bad, False


def key(m: re.Match) -> tuple[str, str]:
    return m.group("a"), m.group("b")


def check_candidates(root: Path) -> list[str]:
    found, problems, none = pairs(root / ".codd/drift-candidates.md")
    if none:
        return []
    if not found and not problems:
        problems.append("候補の組がありません（無ければ `なし` とだけ書く）")
    if len(found) > MAX_PAIRS:
        problems.append(f"組は多くても {MAX_PAIRS} 個です（今 {len(found)} 個。比べる意味の大きいものに絞る）")
    named = refs(root)
    for m in found:
        if m.group("verdict"):
            problems.append(f"候補には判断を付けません: {m.group(0)[:120]}")
        problems += [p for t in key(m) if (p := where_problem(root, named, t))]
    return problems


def check_judged(root: Path) -> list[str]:
    cands, _, none = pairs(root / ".codd/drift-candidates.md")
    path = root / ".codd/drift.md"
    if none:
        return []
    judged, problems, _ = pairs(path)
    seen = {key(m): m.group("verdict") for m in judged}
    for m in cands:
        if key(m) not in seen:
            problems.append(f"判断していない組があります: {m.group('a')} ⇔ {m.group('b')}")
    for m in judged:
        if m.group("verdict") not in VERDICTS:
            problems.append(f"判断は {' / '.join(VERDICTS)} のどれかです: {m.group(0)[:120]}")
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for i, ln in enumerate(lines):
        m = _PAIR.match(ln)
        if m and m.group("verdict") == "ずれ" and not (i + 1 < len(lines) and _ASK.match(lines[i + 1])):
            problems.append(f"ずれには、次の行に `  - やりたいこと: …` を添えます: {m.group('a')} ⇔ {m.group('b')}")
    return problems


def report(root: Path) -> int:
    _, _, none = pairs(root / ".codd/drift-candidates.md")
    path = root / ".codd/drift.md"
    if none or not path.is_file():
        print("比べる組はありませんでした（codd を通らずに入った変更が無いか、相手の側で同じことを述べた箇所が無い）")
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    judged, _, _ = pairs(path)
    counts = {v: sum(1 for m in judged if m.group("verdict") == v) for v in VERDICTS}
    print("意味のずれの点検: " + "、".join(f"{v} {n}" for v, n in counts.items()) + "（全文: .codd/drift.md）")
    for i, ln in enumerate(lines):
        m = _PAIR.match(ln)
        if m and m.group("verdict") in ("ずれ", "判断できない"):
            print(ln)
            if i + 1 < len(lines) and _ASK.match(lines[i + 1]):
                print(lines[i + 1])
    return 0


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    root = repo_root()
    if mode == "report":
        return report(root)
    if mode not in ("candidates", "judged"):
        print(__doc__, file=sys.stderr)
        return 2
    problems = check_candidates(root) if mode == "candidates" else check_judged(root)
    for p in problems:
        print(f"- {p}", file=sys.stderr)
    if problems:
        print(f"NG {len(problems)} 件", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
