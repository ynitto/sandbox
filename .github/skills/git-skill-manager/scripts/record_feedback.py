#!/usr/bin/env python3
"""スキル使用後フィードバックを記録する。

使い方:
    python record_feedback.py <skill-name> --verdict ok
    python record_feedback.py <skill-name> --verdict needs-improvement --note "改善点の説明"
    python record_feedback.py <skill-name> --verdict broken --note "壊れている箇所"

verdict:
    ok                 - 問題なく動作した
    needs-improvement  - 動作したが改善余地がある
    broken             - 動作しなかった

レジストリの installed_skills[].feedback_history に追記する。

pending_refinement トリガーのしきい値:
    ワークスペーススキル (source_repo="workspace"): 未改良問題が 1件 → 即トリガー
    インストール済みスキル (source_repo=その他):    未改良問題が 3件 → トリガー（デフォルト）
    スキルエントリの refine_threshold フィールドで個別上書き可能。
    mark_refined() 実行後は未改良カウントがリセットされる。

EVAL_RECOMMEND シグナル:
    ワークスペーススキル: promote / refine / continue を毎回出力
    インストール済みスキル: しきい値を超えて pending になったタイミングのみ refine を出力

ワークスペーススキル（.github/skills/ にあり ~/.copilot/skills/ にないもの）は
レジストリ未登録でも source_repo="workspace" で自動登録する。
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



def _refine_threshold(skill: dict) -> int:
    """スキルの改良トリガーしきい値を返す。

    ワークスペーススキルは 1（即時）、インストール済みスキルは 3（デフォルト）。
    レジストリの refine_threshold フィールドで個別上書き可能。
    """
    source = skill.get("source_repo", "")
    default = 1 if source == "workspace" else 3
    return skill.get("refine_threshold", default)


def _unrefined_problem_count(skill: dict) -> int:
    """未改良の問題フィードバック数を返す（mark_refined 後はリセットされる）。"""
    return sum(
        1 for e in skill.get("feedback_history", [])
        if not e.get("refined") and e["verdict"] in ("needs-improvement", "broken")
    )


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

    # しきい値を超えた未改良の問題が蓄積された場合に pending_refinement を立てる。
    # workspace: 1件で即トリガー / それ以外: デフォルト3件蓄積でトリガー。
    if verdict in ("needs-improvement", "broken"):
        threshold = _refine_threshold(skill)
        if _unrefined_problem_count(skill) >= threshold:
            skill["pending_refinement"] = True

    mark = {"ok": "✅", "needs-improvement": "⚠️", "broken": "❌"}.get(verdict, "📝")
    print(f"{mark} {skill_name}: フィードバックを記録しました ({verdict})")

    source = skill.get("source_repo", "")
    if source == "workspace":
        # ワークスペーススキル: promote / refine / continue を判定して出力
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
    elif skill.get("pending_refinement"):
        # インストール済みスキル: しきい値を超えて pending になったタイミングのみ出力
        count = _unrefined_problem_count(skill)
        print(f"EVAL_RECOMMEND: refine  # {count}件の問題が蓄積されました")

    return reg


def main():
    parser = argparse.ArgumentParser(
        description="スキル使用後フィードバックを記録する"
    )
    parser.add_argument("skill_name", help="スキル名")
    parser.add_argument(
        "--verdict",
        choices=["ok", "needs-improvement", "broken"],
        required=True,
        help="フィードバックの種類",
    )
    parser.add_argument("--note", default="", help="補足コメント（任意）")
    args = parser.parse_args()

    registry_path = _registry_path()
    if not os.path.isfile(registry_path):
        sys.exit(1)

    with open(registry_path, encoding="utf-8") as f:
        reg = json.load(f)

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


if __name__ == "__main__":
    main()
