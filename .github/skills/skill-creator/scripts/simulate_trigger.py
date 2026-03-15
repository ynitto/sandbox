#!/usr/bin/env python3
"""ユーザーリクエストに対してどのスキルが発動するかをシミュレートする。

使い方:
    python simulate_trigger.py "テストが落ちてる"
    python simulate_trigger.py "スキルをpullして" --top 5
    python simulate_trigger.py --conflicts
    python simulate_trigger.py --conflicts --threshold 0.5
    python simulate_trigger.py "デバッグして" --all     # ユーザースキルも含める
    python simulate_trigger.py "デバッグして" --workspace /path/to/skills
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def _default_workspace() -> str:
    """このスクリプトの2階層上（現在のスキルベース）を返す。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", ".."))


def _user_skill_home() -> str:
    # __file__ 相対: <base>/skills/<skill>/scripts/ → 2階層上 = <base>/skills
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", ".."))
    if os.path.isfile(os.path.join(candidate, "..", "skill-registry.json")):
        return candidate
    if "AGENT_SKILLS_HOME" in os.environ:
        return os.path.join(os.environ["AGENT_SKILLS_HOME"], "skills")
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    legacy = os.path.join(home, ".copilot", "skills")
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(home, ".agent-skills", "skills")


# ---------------------------------------------------------------------------
# スキル読み込み
# ---------------------------------------------------------------------------

def _find_skill_dirs(base: str) -> list[tuple[str, str]]:
    """(name, skill_dir) のリストを返す。"""
    if not os.path.isdir(base):
        return []
    results = []
    for entry in sorted(os.listdir(base)):
        skill_md = os.path.join(base, entry, "SKILL.md")
        if os.path.isfile(skill_md):
            results.append((entry, os.path.join(base, entry)))
    return results


def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    raw = parts[1].strip()
    if yaml:
        try:
            data = yaml.safe_load(raw)
        except Exception:
            data = {}
    else:
        data = {}
        for line in raw.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip()
    return data if isinstance(data, dict) else {}


def load_skills(search_paths: list[str]) -> list[dict]:
    """スキルメタデータのリストを返す。同名スキルは先に見つかった方を優先。"""
    skills = []
    seen: set[str] = set()
    for base in search_paths:
        for name, skill_dir in _find_skill_dirs(base):
            if name in seen:
                continue
            seen.add(name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            try:
                with open(skill_md, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            fm = _parse_frontmatter(content)
            desc = fm.get("description", "")
            if desc:
                skills.append({"name": name, "description": str(desc), "path": skill_dir})
    return skills


# ---------------------------------------------------------------------------
# スコアリング
# ---------------------------------------------------------------------------

def _extract_triggers(description: str) -> list[str]:
    """「...」で囲まれたトリガーフレーズを抽出する。"""
    return re.findall(r"「([^」]+)」", description)


def _bigrams(text: str) -> set[str]:
    """文字バイグラムセットを返す（句読点・記号を除去してから生成）。"""
    cleaned = re.sub(r'[\s\u3000「」『』【】（）。、・\-/\\|]', "", text)
    if len(cleaned) < 2:
        return set(cleaned)
    return {cleaned[i: i + 2] for i in range(len(cleaned) - 1)}


def score_skill(query: str, description: str) -> float:
    """クエリと description のマッチスコアを返す (0.0〜)。

    3つのシグナルを加算する:
      1. 完全包含: description がクエリ文字列をそのまま含む
      2. トリガー照合: 「...」フレーズとの一致・部分一致
      3. バイグラム: クエリのバイグラムが description に含まれる割合
    """
    if not description:
        return 0.0

    score = 0.0

    # 1. description がクエリをそのまま含む
    if query in description:
        score += 1.5

    # 2. トリガーフレーズとの照合
    triggers = _extract_triggers(description)
    best_trigger = 0.0
    for t in triggers:
        if t == query:
            best_trigger = max(best_trigger, 1.0)
        elif t in query or query in t:
            best_trigger = max(best_trigger, 0.8)
        else:
            q_set, t_set = set(query), set(t)
            union = q_set | t_set
            overlap = len(q_set & t_set) / len(union) if union else 0.0
            best_trigger = max(best_trigger, overlap * 0.5)
    score += best_trigger

    # 3. バイグラム: クエリのバイグラムが description に含まれる割合
    q_bg = _bigrams(query)
    d_bg = _bigrams(description)
    if q_bg and d_bg:
        common = len(q_bg & d_bg)
        coeff = common / len(q_bg)
        score += coeff * 0.5

    return score


# ---------------------------------------------------------------------------
# シミュレート表示
# ---------------------------------------------------------------------------

def simulate(query: str, skills: list[dict], top: int = 5) -> None:
    results = [(score_skill(query, s["description"]), s["name"]) for s in skills]
    results = [(sc, name) for sc, name in results if sc > 0]
    results.sort(reverse=True)

    print(f"\nクエリ: 「{query}」\n")
    if not results:
        print("  マッチするスキルが見つかりません")
        return

    shown = results[:top]
    max_score = shown[0][0]
    for rank, (sc, name) in enumerate(shown, 1):
        bar_len = int((sc / max(max_score, 0.01)) * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {rank}. {name:<35} {bar}  {sc:.2f}")

    # 上位2件のスコア差が小さい場合に競合警告
    if len(results) >= 2 and results[1][0] > 0:
        gap = results[0][0] - results[1][0]
        if gap < 0.3:
            print(
                f"\n  ⚠️  競合注意: 上位2件のスコア差が小さいです (gap={gap:.2f})\n"
                f"     '{results[0][1]}' と '{results[1][1]}' の description を確認してください"
            )


# ---------------------------------------------------------------------------
# 競合検出
# ---------------------------------------------------------------------------

def detect_conflicts(skills: list[dict], threshold: float = 0.4) -> None:
    """description が類似しているスキルペアを検出して表示する。"""
    pairs = []
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            a, b = skills[i], skills[j]
            bg_a = _bigrams(a["description"])
            bg_b = _bigrams(b["description"])
            if not bg_a or not bg_b:
                continue
            inter = len(bg_a & bg_b)
            union = len(bg_a | bg_b)
            sim = inter / union if union else 0.0
            if sim >= threshold:
                pairs.append((sim, a["name"], b["name"]))

    pairs.sort(reverse=True)
    print(f"\n競合検出 (threshold={threshold})\n")
    if not pairs:
        print("  競合しているスキルペアはありません ✅")
        return
    for sim, name_a, name_b in pairs:
        level = "🔴" if sim >= 0.6 else "🟡"
        print(f"  {level} {name_a:<35} ↔  {name_b:<35}  similarity={sim:.2f}")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="スキルトリガーをシミュレートする")
    parser.add_argument("query", nargs="?", help="ユーザーリクエスト文字列")
    parser.add_argument("--top", type=int, default=5, metavar="N",
                        help="上位N件を表示（デフォルト: 5）")
    parser.add_argument("--conflicts", action="store_true",
                        help="description が類似したスキルペアを検出する")
    parser.add_argument("--threshold", type=float, default=0.4, metavar="FLOAT",
                        help="競合検出の類似度閾値 0.0〜1.0（デフォルト: 0.4）")
    parser.add_argument("--all", action="store_true", dest="include_user",
                        help="ユーザースキル（~/.copilot/skills/）も含める")
    parser.add_argument("--workspace", metavar="DIR",
                        help="スキルディレクトリを手動で指定（デフォルト: スクリプト配置から自動解決）")
    args = parser.parse_args()

    workspace = args.workspace if args.workspace else _default_workspace()
    search_paths = [workspace]
    if args.include_user:
        search_paths.append(_user_skill_home())

    skills = load_skills(search_paths)
    if not skills:
        print(f"エラー: スキルが見つかりません（検索パス: {', '.join(search_paths)}）")
        sys.exit(1)

    if args.conflicts:
        detect_conflicts(skills, threshold=args.threshold)
    elif args.query:
        simulate(args.query, skills, top=args.top)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
