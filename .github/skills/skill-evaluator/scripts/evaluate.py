#!/usr/bin/env python3
"""スキルを評価する。

レジストリの feedback_history を読み取り、各スキルの推奨アクションを判定する。
ワークスペーススキル（試用中）とインストール済みスキル（ホーム領域）の両方に対応。
git-skill-manager のスクリプトには依存しない（レジストリを直接読む）。

使い方:
    python evaluate.py                          # 全スキルを評価
    python evaluate.py --type workspace         # ワークスペーススキルのみ
    python evaluate.py --type installed         # インストール済みスキルのみ
    python evaluate.py --skill <skill-name>     # 特定スキルのみ評価
"""
import argparse
import json
import os
import sys


def _agent_skills_home() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", "..", ".."))
    if os.path.isdir(os.path.join(candidate, "skills")):
        return candidate
    if "AGENT_SKILLS_HOME" in os.environ:
        return os.environ["AGENT_SKILLS_HOME"]
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    legacy = os.path.join(home, ".copilot")
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(home, ".agent-skills")


def _registry_path() -> str:
    return os.path.join(_agent_skills_home(), "skill-registry.json")


def load_registry() -> dict | None:
    path = _registry_path()
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _maturity_stage(total_feedback: int) -> str:
    """総フィードバック数から成熟度ステージを返す。

    Returns:
        "initial"   : データ不足（< 2件）
        "evaluable" : 評価可能（2〜4件）
        "mature"    : 十分な実績（≥ 5件）
    """
    if total_feedback < 2:
        return "initial"
    elif total_feedback >= 5:
        return "mature"
    else:
        return "evaluable"


def evaluate_skill(skill: dict) -> dict:
    """1スキルの評価結果を返す。

    Returns:
        {
            "name": str,
            "source_repo": str,
            "ok_count": int,
            "broken_count": int,
            "problem_count": int,        # broken + needs-improvement（未改良）
            "total_feedback": int,
            "maturity_stage": "initial" | "evaluable" | "mature",
            "pending_refinement": bool,
            "recommendation": "promote" | "refine" | "continue" | "ok",
        }

    recommendation の意味:
        - "promote"  : ワークスペーススキルが昇格条件を満たした
        - "refine"   : 改良が必要（ワークスペース・インストール済み共通）
        - "continue" : ワークスペーススキルで試用継続
        - "ok"       : インストール済みスキルが正常稼働中

    評価基準（詳細）:
        - broken は深刻度「高」。1件でも即要改良（ok 数に関わらず）
        - needs-improvement は深刻度「中」。問題ありとしてカウント
        - maturity_stage が "initial" の場合は昇格条件を満たしても試用継続を優先
    """
    source = skill.get("source_repo", "")
    is_workspace = source == "workspace"

    history = skill.get("feedback_history", [])
    ok_count = sum(1 for e in history if e.get("verdict") == "ok")
    broken_count = sum(
        1 for e in history
        if e.get("verdict") == "broken" and not e.get("refined")
    )
    needs_improvement_count = sum(
        1 for e in history
        if e.get("verdict") == "needs-improvement" and not e.get("refined")
    )
    problem_count = broken_count + needs_improvement_count
    total_feedback = len(history)
    maturity = _maturity_stage(total_feedback)
    pending = skill.get("pending_refinement", False)

    if is_workspace:
        # broken は深刻度「高」: ok 数に関わらず即要改良
        if pending or broken_count > 0 or needs_improvement_count > 0:
            recommendation = "refine"
        elif ok_count >= 2 and maturity != "initial":
            recommendation = "promote"
        else:
            recommendation = "continue"
    else:
        # インストール済みスキル: 昇格はなし、改良か正常のみ
        if pending or problem_count > 0:
            recommendation = "refine"
        else:
            recommendation = "ok"

    return {
        "name": skill["name"],
        "source_repo": source,
        "ok_count": ok_count,
        "broken_count": broken_count,
        "problem_count": problem_count,
        "total_feedback": total_feedback,
        "maturity_stage": maturity,
        "pending_refinement": pending,
        "recommendation": recommendation,
    }


_MATURITY_LABEL = {
    "initial":   "📊 初期",
    "evaluable": "📊 評価可",
    "mature":    "📊 実績十分",
}


def _print_workspace_results(results: list) -> None:
    print("📋 ワークスペーススキル（試用中）:\n")
    for ev in results:
        ok = ev["ok_count"]
        broken = ev["broken_count"]
        prob = ev["problem_count"]
        rec = ev["recommendation"]
        maturity = _MATURITY_LABEL[ev["maturity_stage"]]

        if rec == "promote":
            mark = "✅ 昇格推奨"
        elif rec == "refine":
            mark = "⚠️  要改良後昇格"
            if broken > 0:
                mark += f"  ※broken:{broken}"
        else:
            mark = "🔄 試用継続"

        print(f"  {ev['name']:30s}  ok:{ok} 問題:{prob}  {maturity}  → {mark}")

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
    print()


def _print_installed_results(results: list) -> None:
    print("📋 インストール済みスキル（ホーム領域）:\n")
    for ev in results:
        ok = ev["ok_count"]
        broken = ev["broken_count"]
        prob = ev["problem_count"]
        rec = ev["recommendation"]
        src = ev["source_repo"]
        src_label = f"[{src}]"
        maturity = _MATURITY_LABEL[ev["maturity_stage"]]

        if rec == "refine":
            mark = "⚠️  要改良"
            if broken > 0:
                mark += f"  ※broken:{broken}"
        else:
            mark = "✅ 正常"
        print(f"  {ev['name']:30s}  ok:{ok} 問題:{prob}  {maturity}  → {mark}  {src_label}")

    print()
    refinable = [e for e in results if e["recommendation"] == "refine"]
    ok_list   = [e for e in results if e["recommendation"] == "ok"]

    if refinable:
        print("要改良: " + ", ".join(e["name"] for e in refinable))
    if ok_list:
        print("正常:   " + ", ".join(e["name"] for e in ok_list))
    print()


def run_evaluation(target_skill: str = None, skill_type: str = "all") -> list:
    """評価を実行して結果リストを返す。"""
    reg = load_registry()
    if reg is None:
        print("[ERROR] レジストリが見つかりません", file=sys.stderr)
        sys.exit(1)

    all_skills = reg.get("installed_skills", [])

    if target_skill:
        skills = [s for s in all_skills if s["name"] == target_skill]
        if not skills:
            print(f"ℹ️  '{target_skill}' はレジストリに見つかりません")
            return []
    elif skill_type == "workspace":
        skills = [s for s in all_skills if s.get("source_repo") == "workspace"]
        if not skills:
            print("ℹ️  試用中のワークスペーススキルはありません")
            return []
    elif skill_type == "installed":
        skills = [s for s in all_skills if s.get("source_repo") != "workspace"]
        if not skills:
            print("ℹ️  インストール済みスキルはありません")
            return []
    else:  # "all"
        skills = all_skills
        if not skills:
            print("ℹ️  スキルが登録されていません")
            return []

    results = [evaluate_skill(s) for s in skills]

    workspace_results  = [r for r in results if r["source_repo"] == "workspace"]
    installed_results  = [r for r in results if r["source_repo"] != "workspace"]

    if workspace_results:
        _print_workspace_results(workspace_results)
    if installed_results:
        _print_installed_results(installed_results)

    return results


def main():
    parser = argparse.ArgumentParser(description="スキルを評価する")
    parser.add_argument("--skill", help="特定スキルのみ評価する")
    parser.add_argument(
        "--type",
        choices=["all", "workspace", "installed"],
        default="all",
        help="評価対象のスキル種別 (default: all)",
    )
    args = parser.parse_args()

    run_evaluation(args.skill, args.type)


if __name__ == "__main__":
    main()
