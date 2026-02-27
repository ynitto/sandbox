#!/usr/bin/env python3
"""昇格ポリシーエンジン。

レジストリの promotion_policy 設定に従い、
各スキルが中央リポジトリへ貢献すべき状態かを自動判定する。

使い方:
    python promotion_policy.py                   # 全スキルを評価
    python promotion_policy.py --skill <name>    # 特定スキルのみ
    python promotion_policy.py --queue           # 昇格候補をキューに追加
    python promotion_policy.py --show-policy     # 現在のポリシー設定を表示
    python promotion_policy.py --set-policy min_ok_count=5  # ポリシー値を変更
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from registry import load_registry, save_registry


def _ok_rate(skill: dict) -> float | None:
    """ok率を計算する（フィードバックがない場合は None）。"""
    history = skill.get("feedback_history", [])
    if not history:
        return None
    ok = sum(1 for e in history if e.get("verdict") == "ok")
    return ok / len(history)


def _problem_rate(skill: dict) -> float:
    """問題率を計算する（needs-improvement + broken の割合）。"""
    history = skill.get("feedback_history", [])
    if not history:
        return 0.0
    problems = sum(1 for e in history if e.get("verdict") in ("needs-improvement", "broken"))
    return problems / len(history)


def evaluate_promotion(skill: dict, policy: dict) -> dict:
    """1スキルの昇格適性を評価する。

    Returns:
        {
            "eligible": bool,
            "reasons": list[str],    # 条件を満たした理由
            "blockers": list[str],   # 昇格を阻害している理由
        }
    """
    reasons = []
    blockers = []

    history = skill.get("feedback_history", [])
    ok_count = sum(1 for e in history if e.get("verdict") == "ok")
    problem_rate = _problem_rate(skill)
    pending = skill.get("pending_refinement", False)
    pinned = skill.get("pinned_commit") is not None
    local_modified = skill.get("lineage", {}).get("local_modified", False)
    version_ahead = skill.get("version_ahead", False)

    # --- 必須条件チェック ---

    min_ok = policy.get("min_ok_count", 3)
    if ok_count >= min_ok:
        reasons.append(f"ok:{ok_count}件（閾値:{min_ok}件）")
    else:
        blockers.append(f"ok件数不足: {ok_count}/{min_ok}件")

    max_prob = policy.get("max_problem_rate", 0.1)
    if problem_rate <= max_prob:
        if history:
            reasons.append(f"問題率:{problem_rate:.0%}（上限:{max_prob:.0%}）")
    else:
        blockers.append(f"問題率超過: {problem_rate:.0%} > {max_prob:.0%}")

    if policy.get("require_local_modified", True):
        if local_modified or version_ahead:
            reasons.append("ローカル改善あり")
        else:
            blockers.append("ローカル改善なし（中央版と同一）")

    # --- 除外条件チェック ---

    if pending:
        blockers.append("未解決の問題あり（pending_refinement=true）")

    if pinned:
        blockers.append("バージョン固定中（pinned_commit が設定済み）")

    eligible = len(blockers) == 0 and len(reasons) > 0

    return {
        "eligible": eligible,
        "reasons": reasons,
        "blockers": blockers,
    }


def run_evaluation(target_skill: str | None = None, add_to_queue: bool = False) -> list:
    """全スキルを評価し、結果を返す。"""
    reg = load_registry()
    policy = reg.get("promotion_policy", {})
    skills = reg.get("installed_skills", [])

    if target_skill:
        skills = [s for s in skills if s["name"] == target_skill]

    if not skills:
        print("ℹ️  評価対象のスキルがありません")
        return []

    results = []
    eligible_skills = []

    for skill in skills:
        result = evaluate_promotion(skill, policy)
        result["name"] = skill["name"]
        result["source_repo"] = skill.get("source_repo", "?")
        results.append(result)

        if result["eligible"]:
            eligible_skills.append(skill["name"])

    # 表示
    print("📊 昇格ポリシー評価:\n")
    for r in results:
        status = "✅ 昇格適格" if r["eligible"] else "⏳ 条件未達"
        print(f"  {r['name']:30s}  {status}")
        for reason in r["reasons"]:
            print(f"    ✓ {reason}")
        for blocker in r["blockers"]:
            print(f"    ✗ {blocker}")

    print()
    if eligible_skills:
        print(f"昇格適格スキル: {', '.join(eligible_skills)}")
    else:
        print("現時点で昇格適格なスキルはありません")

    # キューへの追加
    if add_to_queue and eligible_skills:
        queue = reg.setdefault("contribution_queue", [])
        queued_names = {q["skill_name"] for q in queue if q["status"] != "rejected"}
        newly_queued = []

        for skill_name in eligible_skills:
            if skill_name not in queued_names:
                skill = next(s for s in reg["installed_skills"] if s["name"] == skill_name)
                r = next(r for r in results if r["name"] == skill_name)
                queue.append({
                    "skill_name": skill_name,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "; ".join(r["reasons"]),
                    "status": "pending_review",
                    "node_id": reg.get("node", {}).get("id"),
                })
                newly_queued.append(skill_name)

        if newly_queued:
            save_registry(reg)
            print(f"\n📬 貢献キューに追加しました: {', '.join(newly_queued)}")
            print("   'git-skill-manager push' でリポジトリへ送信できます")
        else:
            print("\nℹ️  全ての昇格適格スキルは既にキューに登録済みです")

    return results


def show_policy() -> None:
    """現在のポリシー設定を表示する。"""
    reg = load_registry()
    policy = reg.get("promotion_policy", {})
    print("⚙️  昇格ポリシー設定:")
    print(f"   min_ok_count:          {policy.get('min_ok_count', 3)}")
    print(f"   max_problem_rate:      {policy.get('max_problem_rate', 0.1):.0%}")
    print(f"   require_local_modified:{policy.get('require_local_modified', True)}")
    print(f"   auto_pr:               {policy.get('auto_pr', False)}")
    print(f"   notify_on_eligible:    {policy.get('notify_on_eligible', True)}")


def set_policy(key: str, value: str) -> None:
    """ポリシー値を変更する。"""
    reg = load_registry()
    policy = reg.setdefault("promotion_policy", {})

    # 型変換
    if key == "min_ok_count":
        policy[key] = int(value)
    elif key == "max_problem_rate":
        policy[key] = float(value)
    elif key in ("require_local_modified", "auto_pr", "notify_on_eligible"):
        policy[key] = value.lower() in ("true", "1", "yes")
    else:
        print(f"❌ 不明なポリシーキー: {key}")
        print("   有効なキー: min_ok_count, max_problem_rate, require_local_modified, auto_pr, notify_on_eligible")
        return

    save_registry(reg)
    print(f"✅ ポリシーを更新しました: {key} = {policy[key]}")


def main():
    parser = argparse.ArgumentParser(description="昇格ポリシーエンジン")
    parser.add_argument("--skill", help="特定スキルのみ評価する")
    parser.add_argument("--queue", action="store_true", help="昇格適格スキルを貢献キューに追加する")
    parser.add_argument("--show-policy", action="store_true", help="現在のポリシー設定を表示する")
    parser.add_argument("--set-policy", metavar="KEY=VALUE", help="ポリシー値を変更する（例: min_ok_count=5）")
    args = parser.parse_args()

    if args.show_policy:
        show_policy()
        return

    if args.set_policy:
        if "=" not in args.set_policy:
            print("❌ 形式が不正です。KEY=VALUE の形式で指定してください")
            sys.exit(1)
        key, value = args.set_policy.split("=", 1)
        set_policy(key.strip(), value.strip())
        return

    run_evaluation(target_skill=args.skill, add_to_queue=args.queue)


if __name__ == "__main__":
    main()
