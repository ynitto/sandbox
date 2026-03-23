#!/usr/bin/env python3
"""スキルを評価する。

レジストリの feedback_history と metrics を読み取り、各スキルの推奨アクションと
定量的品質スコアを算出する。
ワークスペーススキル（試用中）とインストール済みスキル（ホーム領域）の両方に対応。

使い方:
    python evaluate.py                          # 全スキルを評価
    python evaluate.py --type workspace         # ワークスペーススキルのみ
    python evaluate.py --type installed         # インストール済みスキルのみ
    python evaluate.py --skill <skill-name>     # 特定スキルのみ評価
    python evaluate.py --auto-collect           # 評価前にメトリクスを自動集計
"""
import argparse
import json
import os
import subprocess
import sys

# registry.py の __file__ ベースのパス解決を利用
# このスクリプト: {skill_home}/skill-evaluator/scripts/evaluate.py
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_HOME = os.path.dirname(os.path.dirname(_HERE))
_REG_SCRIPTS = os.path.join(_SKILL_HOME, "git-skill-manager", "scripts")
if _REG_SCRIPTS not in sys.path:
    sys.path.insert(0, _REG_SCRIPTS)
from registry import _registry_path


def load_registry() -> dict | None:
    path = _registry_path()
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_auto_collect(skill_name: str | None = None) -> None:
    """metrics_collector.py を実行してレジストリのメトリクスを最新化する。"""
    collector = os.path.join(_SKILL_HOME, "git-skill-manager", "scripts", "metrics_collector.py")
    if not os.path.isfile(collector):
        print("⚠️  metrics_collector.py が見つかりません。スキップします", file=sys.stderr)
        return
    cmd = [sys.executable, collector]
    if skill_name:
        cmd += ["--skill", skill_name]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"📊 メトリクスを自動集計しました")
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"   {line}")
    else:
        print(f"⚠️  メトリクス集計に失敗しました: {result.stderr.strip()}", file=sys.stderr)
    print()


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


def compute_quality_score(skill: dict) -> float | None:
    """metrics フィールドから定量的品質スコア（0〜100）を算出する。

    スコア構成:
        - Pass率（ok_rate）  : 最大 70 点
        - 実績（使用回数）   : 最大 20 点（10 回以上で満点）
        - リトライ少なさ     : 最大 10 点（avg_subagent_calls が低いほど高得点）

    metrics が存在しない、または total_executions = 0 の場合は None を返す。
    """
    m = skill.get("metrics")
    if not m:
        return None
    total = m.get("total_executions", 0)
    if total == 0:
        return None

    ok_rate = m.get("ok_rate", 0.0)
    avg_subagent = m.get("avg_subagent_calls")

    pass_score = ok_rate * 70
    usage_score = min(total / 10, 1.0) * 20
    if avg_subagent is not None:
        # リトライ 0 回 → 10 点、5 回以上 → 0 点
        retry_score = max(0.0, 10.0 - avg_subagent * 2)
    else:
        retry_score = 5.0  # データなし: 中間点

    return round(pass_score + usage_score + retry_score, 1)


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
            "quality_score": float | None,   # 定量品質スコア 0〜100
            "metrics_summary": dict | None,  # 集計メトリクスのサマリ
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

    # 定量品質スコア（metrics_collector.py で集計済みのデータを使用）
    quality_score = compute_quality_score(skill)

    # メトリクスサマリ（表示用）
    m = skill.get("metrics")
    metrics_summary = None
    if m and m.get("total_executions", 0) > 0:
        metrics_summary = {
            "total_executions": m.get("total_executions", 0),
            "ok_rate": m.get("ok_rate", 0.0),
            "avg_subagent_calls": m.get("avg_subagent_calls"),
            "trend_7d": m.get("trend_7d"),
        }

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
        "quality_score": quality_score,
        "metrics_summary": metrics_summary,
    }


_MATURITY_LABEL = {
    "initial":   "📊 初期",
    "evaluable": "📊 評価可",
    "mature":    "📊 実績十分",
}


def _format_quality_score(score: float | None) -> str:
    """品質スコアを表示用文字列にフォーマットする。"""
    if score is None:
        return "スコア:-"
    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"
    return f"スコア:{score:.0f}/100({grade})"


def _format_metrics_line(ms: dict | None) -> str:
    """メトリクスサマリを1行文字列にフォーマットする。"""
    if not ms:
        return ""
    total = ms["total_executions"]
    ok_rate = ms["ok_rate"]
    avg_sub = ms.get("avg_subagent_calls")
    trend = ms.get("trend_7d", {})
    trend_str = f"7d:{trend.get('executions', 0)}回" if trend else ""
    sub_str = f"retry:{avg_sub:.1f}" if avg_sub is not None else ""
    parts = [f"実行:{total}回", f"Pass:{ok_rate:.0%}"]
    if sub_str:
        parts.append(sub_str)
    if trend_str:
        parts.append(trend_str)
    return "  [" + " / ".join(parts) + "]"


def _print_workspace_results(results: list) -> None:
    print("📋 ワークスペーススキル（試用中）:\n")
    for ev in results:
        ok = ev["ok_count"]
        broken = ev["broken_count"]
        prob = ev["problem_count"]
        rec = ev["recommendation"]
        maturity = _MATURITY_LABEL[ev["maturity_stage"]]
        score_str = _format_quality_score(ev["quality_score"])
        metrics_line = _format_metrics_line(ev["metrics_summary"])

        if rec == "promote":
            mark = "✅ 昇格推奨"
        elif rec == "refine":
            mark = "⚠️  要改良後昇格"
            if broken > 0:
                mark += f"  ※broken:{broken}"
        else:
            mark = "🔄 試用継続"

        print(f"  {ev['name']:30s}  ok:{ok} 問題:{prob}  {maturity}  {score_str}  → {mark}")
        if metrics_line:
            print(f"  {'':30s}{metrics_line}")

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
        score_str = _format_quality_score(ev["quality_score"])
        metrics_line = _format_metrics_line(ev["metrics_summary"])

        if rec == "refine":
            mark = "⚠️  要改良"
            if broken > 0:
                mark += f"  ※broken:{broken}"
        else:
            mark = "✅ 正常"
        print(f"  {ev['name']:30s}  ok:{ok} 問題:{prob}  {maturity}  {score_str}  → {mark}  {src_label}")
        if metrics_line:
            print(f"  {'':30s}{metrics_line}")

    print()
    refinable = [e for e in results if e["recommendation"] == "refine"]
    ok_list   = [e for e in results if e["recommendation"] == "ok"]

    if refinable:
        print("要改良: " + ", ".join(e["name"] for e in refinable))
    if ok_list:
        print("正常:   " + ", ".join(e["name"] for e in ok_list))
    print()


def run_evaluation(target_skill: str = None, skill_type: str = "all", auto_collect: bool = False) -> list:
    """評価を実行して結果リストを返す。"""
    if auto_collect:
        run_auto_collect(skill_name=target_skill)

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
    parser.add_argument(
        "--auto-collect",
        action="store_true",
        help="評価前に metrics_collector.py を実行してメトリクスを最新化する",
    )
    args = parser.parse_args()

    run_evaluation(args.skill, args.type, auto_collect=args.auto_collect)


if __name__ == "__main__":
    main()
