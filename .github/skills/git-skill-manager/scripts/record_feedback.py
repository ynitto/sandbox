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
ワークスペーススキル（.github/skills/ にあり ~/.copilot/skills/ にないもの）は
レジストリ未登録でも source_repo="workspace" で自動登録する。
レジストリが存在しない場合は何もしない（エラーにしない）。
"""
import json
import os
import sys
from datetime import datetime, timezone


def _registry_path() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skill-registry.json")


def _skill_home() -> str:
    home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(home, ".copilot", "skills")


def is_workspace_skill(skill_name: str) -> bool:
    """ワークスペーススキルかどうかを判定する。

    .github/skills/<name>/SKILL.md が存在し、
    かつ ~/.copilot/skills/<name>/SKILL.md が存在しない場合に True。
    """
    ws_md = os.path.join(".github", "skills", skill_name, "SKILL.md")
    user_md = os.path.join(_skill_home(), skill_name, "SKILL.md")
    return os.path.isfile(ws_md) and not os.path.isfile(user_md)


def auto_register_workspace_skill(reg: dict, skill_name: str) -> dict:
    """ワークスペーススキルをレジストリに自動登録する。"""
    reg.setdefault("installed_skills", []).append({
        "name": skill_name,
        "source_repo": "workspace",
        "source_path": os.path.join(".github", "skills", skill_name),
        "commit_hash": "-",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": True,
        "pinned_commit": None,
        "feedback_history": [],
        "pending_refinement": False,
    })
    return reg



def record_feedback(skill_name: str, verdict: str, note: str, reg: dict) -> dict:
    """フィードバックを記録してレジストリを返す。"""
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        return reg

    skill.setdefault("feedback_history", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "note": note,
        "refined": False,
    })

    if verdict in ("needs-improvement", "broken"):
        skill["pending_refinement"] = True

    mark = {"ok": "✅", "needs-improvement": "⚠️", "broken": "❌"}.get(verdict, "📝")
    print(f"{mark} {skill_name}: フィードバックを記録しました ({verdict})")

    # ワークスペーススキルの場合は評価推奨シグナルを出力（skill-evaluator が受け取る）
    if skill.get("source_repo") == "workspace":
        history = skill.get("feedback_history", [])
        ok_count = sum(1 for e in history if e.get("verdict") == "ok")
        problem_count = sum(1 for e in history if e.get("verdict") in ("needs-improvement", "broken"))
        pending = skill.get("pending_refinement", False)
        if pending or problem_count > 0:
            rec = "refine"
        elif ok_count >= 2:
            rec = "promote"
        else:
            rec = "continue"
        print(f"EVAL_RECOMMEND: {rec}")

    return reg


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
    import argparse
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
        if check_discovery(reg):
            print("SUGGEST_DISCOVERY")
            sys.exit(0)
        else:
            sys.exit(1)

    if not args.verdict:
        parser.error("--verdict が必要です（--check-discovery を使わない場合）")

    skill_name = args.skill_name

    # ワークスペーススキルがレジストリ未登録なら自動登録
    existing = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not existing and is_workspace_skill(skill_name):
        reg = auto_register_workspace_skill(reg, skill_name)
        print(f"📝 {skill_name}: ワークスペーススキルとしてレジストリに登録しました")

    reg = record_feedback(skill_name, args.verdict, args.note, reg)

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)

    # スキル発見の提案タイミングを確認
    if check_discovery(reg):
        print()
        print("💡 最近の使い方パターンから新しいスキル候補を発見できるかもしれません。")
        print("   'git-skill-manager discover' で分析できます。")


if __name__ == "__main__":
    main()
