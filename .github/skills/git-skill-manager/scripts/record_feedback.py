#!/usr/bin/env python3
"""スキル使用後フィードバックを記録する。

使い方:
    python record_feedback.py <skill-name> --verdict ok
    python record_feedback.py <skill-name> --verdict needs-improvement --note "改善点の説明"
    python record_feedback.py <skill-name> --verdict broken --note "壊れている箇所"
    python record_feedback.py <skill-name> --check-discovery   # 発見トリガー判定のみ

verdict:
    ok                 - 問題なく動作した
    needs-improvement  - 動作したが改善余地がある
    broken             - 動作しなかった

レジストリの installed_skills[].feedback_history に追記する。
needs-improvement / broken の場合は pending_refinement を true にする。
レジストリが存在しない場合は何もしない（エラーにしない）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone


def _registry_path() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skill-registry.json")


def record_feedback(skill_name: str, verdict: str, note: str) -> None:
    registry_path = _registry_path()
    if not os.path.isfile(registry_path):
        return

    with open(registry_path, encoding="utf-8") as f:
        reg = json.load(f)

    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        return

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "note": note,
        "refined": False,
    }
    if "feedback_history" not in skill:
        skill["feedback_history"] = []
    skill["feedback_history"].append(entry)

    if verdict in ("needs-improvement", "broken"):
        skill["pending_refinement"] = True

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)

    mark = {"ok": "✅", "needs-improvement": "⚠️", "broken": "❌"}.get(verdict, "📝")
    print(f"{mark} {skill_name}: フィードバックを記録しました ({verdict})")
    if skill.get("pending_refinement"):
        print(f"   改善待ち: 'git-skill-manager refine {skill_name}' で改良できます")


def check_discovery(reg: dict) -> bool:
    """skill_discovery の suggest_interval_days が経過しているか判定する。
    True = 発見提案をすべきタイミング。
    """
    discovery = reg.get("skill_discovery", {})
    last_run = discovery.get("last_run_at")
    interval_days = discovery.get("suggest_interval_days", 7)

    if not last_run:
        return True

    try:
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_dt).days
        return elapsed >= interval_days
    except ValueError:
        return True


def main():
    parser = argparse.ArgumentParser(
        description="スキル使用後フィードバックを記録する"
    )
    parser.add_argument("skill_name", help="スキル名")
    parser.add_argument(
        "--verdict",
        choices=["ok", "needs-improvement", "broken"],
        help="フィードバックの種類",
    )
    parser.add_argument("--note", default="", help="補足コメント（任意）")
    parser.add_argument(
        "--check-discovery",
        action="store_true",
        help="スキル発見の提案タイミングか判定して終了する（終了コード 0=提案すべき, 1=まだ早い）",
    )
    args = parser.parse_args()

    registry_path = _registry_path()
    if not os.path.isfile(registry_path):
        sys.exit(1)

    with open(registry_path, encoding="utf-8") as f:
        reg = json.load(f)

    if args.check_discovery:
        should_suggest = check_discovery(reg)
        if should_suggest:
            print("SUGGEST_DISCOVERY")
            sys.exit(0)
        else:
            sys.exit(1)

    if not args.verdict:
        parser.error("--verdict が必要です（--check-discovery を使わない場合）")

    record_feedback(args.skill_name, args.verdict, args.note)

    # フィードバック記録後に発見提案タイミングを確認
    if check_discovery(reg):
        print()
        print("💡 最近の使い方パターンから新しいスキル候補を発見できるかもしれません。")
        print("   'git-skill-manager discover' で分析できます。")


if __name__ == "__main__":
    main()
