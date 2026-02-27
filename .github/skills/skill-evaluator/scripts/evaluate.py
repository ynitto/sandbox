#!/usr/bin/env python3
"""ワークスペーススキルを評価する。

レジストリの feedback_history を読み取り、各スキルの昇格推奨度を判定する。
git-skill-manager のスクリプトには依存しない（レジストリを直接読む）。

使い方:
    python evaluate.py                        # 全ワークスペーススキルを評価
    python evaluate.py --skill <skill-name>   # 特定スキルのみ評価
"""
import argparse
import json
import os
import sys


def _registry_path() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skill-registry.json")


def load_registry() -> dict | None:
    path = _registry_path()
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def evaluate_skill(skill: dict) -> dict:
    """1スキルの評価結果を返す。

    Returns:
        {
            "name": str,
            "ok_count": int,
            "problem_count": int,
            "pending_refinement": bool,
            "recommendation": "promote" | "refine" | "continue",
        }
    """
    history = skill.get("feedback_history", [])
    ok_count = sum(1 for e in history if e.get("verdict") == "ok")
    problem_count = sum(
        1 for e in history
        if e.get("verdict") in ("needs-improvement", "broken") and not e.get("refined")
    )
    pending = skill.get("pending_refinement", False)

    if pending or problem_count > 0:
        recommendation = "refine"
    elif ok_count >= 2:
        recommendation = "promote"
    else:
        recommendation = "continue"

    return {
        "name": skill["name"],
        "ok_count": ok_count,
        "problem_count": problem_count,
        "pending_refinement": pending,
        "recommendation": recommendation,
    }


def run_evaluation(target_skill: str = None) -> list:
    """評価を実行して結果リストを返す。"""
    reg = load_registry()
    if reg is None:
        print("[ERROR] レジストリが見つかりません", file=sys.stderr)
        sys.exit(1)

    workspace_skills = [
        s for s in reg.get("installed_skills", [])
        if s.get("source_repo") == "workspace"
    ]

    if target_skill:
        workspace_skills = [s for s in workspace_skills if s["name"] == target_skill]

    if not workspace_skills:
        if target_skill:
            print(f"ℹ️  '{target_skill}' はワークスペーススキルではありません")
        else:
            print("ℹ️  試用中のワークスペーススキルはありません")
        return []

    results = [evaluate_skill(s) for s in workspace_skills]

    # 表示
    print("📋 ワークスペーススキルの評価:\n")
    for ev in results:
        ok = ev["ok_count"]
        prob = ev["problem_count"]
        rec = ev["recommendation"]

        if rec == "promote":
            mark = "✅ 昇格推奨"
        elif rec == "refine":
            mark = "⚠️  要改良後昇格"
        else:
            mark = "🔄 試用継続"

        print(f"  {ev['name']:30s}  ok:{ok} 問題:{prob}  → {mark}")

    print()
    promotable = [e for e in results if e["recommendation"] == "promote"]
    refinable  = [e for e in results if e["recommendation"] == "refine"]
    continuing = [e for e in results if e["recommendation"] == "continue"]

    if promotable:
        print("昇格推奨: " + ", ".join(e["name"] for e in promotable))
    if refinable:
        print("要改良:   " + ", ".join(e["name"] for e in refinable))
    if continuing:
        print("試用継続: " + ", ".join(e["name"] for e in continuing))

    return results


def main():
    parser = argparse.ArgumentParser(description="ワークスペーススキルを評価する")
    parser.add_argument("--skill", help="特定スキルのみ評価する")
    args = parser.parse_args()

    run_evaluation(args.skill)


if __name__ == "__main__":
    main()
