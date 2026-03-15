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

ワークスペーススキル（ワークスペースのスキルディレクトリ）にあり
~/.copilot/skills/ にないものは
レジストリ未登録でも source_repo="workspace" で自動登録する。
レジストリが存在しない場合は何もしない（エラーにしない）。
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone

# registry.py の __file__ ベースのパス解決を利用
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from registry import _registry_path, _skill_home, _agent_home


def _metrics_log_path() -> str:
    return os.path.join(_agent_home(), "metrics-log.jsonl")


def _repo_root() -> str:
    """git リポジトリルートを返す。git 管理外の場合は CWD を返す。"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else os.getcwd()


def is_workspace_skill(skill_name: str) -> bool:
    """ワークスペーススキルかどうかを判定する。

    ワークスペースのスキルディレクトリに
    <name>/SKILL.md が存在し、
    かつ ~/.copilot/skills/<name>/SKILL.md が存在しない場合に True。
    リポジトリルートからの絶対パスで判定するため CWD に依存しない。
    """
    root = _repo_root()
    ws_md = os.path.join(root, ".github", "skills", skill_name, "SKILL.md")
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


def _append_metrics_log(
    skill_name: str,
    verdict: str,
    note: str,
    duration_sec: float | None,
    subagent_calls: int | None,
    co_skills: list[str],
    sprint_id: str | None,
    node_id: str | None,
) -> None:
    """metrics-log.jsonl に生メトリクスイベントを1行追記する。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    event = {
        "event_id": f"evt-{ts}-{secrets.token_hex(3)}",
        "skill_name": skill_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_sec": duration_sec,
        "verdict": verdict,
        "note": note,
        "subagent_calls": subagent_calls,
        "co_executed_skills": co_skills,
        "context": {
            "sprint_id": sprint_id,
            "node_id": node_id,
        },
    }
    log_path = _metrics_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _update_duration_metrics(metrics: dict, duration_sec: float | None) -> None:
    """実行時間メトリクスをインクリメンタル更新（移動平均）する。"""
    if duration_sec is None:
        return
    total = metrics.get("total_executions", 1)
    prev_avg = metrics.get("avg_duration_sec")
    if prev_avg is None:
        metrics["avg_duration_sec"] = round(duration_sec, 1)
    else:
        # 累積移動平均
        metrics["avg_duration_sec"] = round(
            prev_avg + (duration_sec - prev_avg) / total, 1
        )
    # p90 はバッチ集計(metrics_collector.py)で正確に算出する
    # ここでは max を近似値として保持
    prev_max = metrics.get("p90_duration_sec")
    if prev_max is None or duration_sec > prev_max:
        metrics["p90_duration_sec"] = round(duration_sec, 1)


def _update_subagent_metrics(metrics: dict, subagent_calls: int | None) -> None:
    """サブエージェント呼び出し回数メトリクスをインクリメンタル更新する。"""
    if subagent_calls is None:
        return
    total = metrics.get("total_executions", 1)
    prev_avg = metrics.get("avg_subagent_calls")
    if prev_avg is None:
        metrics["avg_subagent_calls"] = round(subagent_calls, 1)
    else:
        metrics["avg_subagent_calls"] = round(
            prev_avg + (subagent_calls - prev_avg) / total, 1
        )


def record_feedback(
    skill_name: str,
    verdict: str,
    note: str,
    reg: dict,
    *,
    duration_sec: float | None = None,
    subagent_calls: int | None = None,
    co_skills: list[str] | None = None,
    sprint_id: str | None = None,
) -> dict:
    """フィードバックを記録してレジストリを返す。"""
    skill = next(
        (s for s in reg.get("installed_skills", []) if s["name"] == skill_name),
        None,
    )
    if not skill:
        return reg

    co_skills = co_skills or []

    # ノード ID を取得
    node_id = reg.get("node", {}).get("id")

    # JSONL に生メトリクスイベントを記録
    _append_metrics_log(
        skill_name, verdict, note,
        duration_sec, subagent_calls, co_skills,
        sprint_id, node_id,
    )

    skill.setdefault("feedback_history", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "note": note,
        "refined": False,
    })

    # メトリクス更新
    history = skill["feedback_history"]
    total = len(history)
    ok_count = sum(1 for e in history if e["verdict"] == "ok")
    metrics = skill.setdefault("metrics", {})
    metrics["total_executions"] = total
    metrics["ok_rate"] = round(ok_count / total, 3) if total > 0 else 0.0
    metrics["last_executed_at"] = datetime.now(timezone.utc).isoformat()

    # 拡張メトリクスのインクリメンタル更新
    _update_duration_metrics(metrics, duration_sec)
    _update_subagent_metrics(metrics, subagent_calls)

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
        unrefined_count = _unrefined_problem_count(skill)
        pending = skill.get("pending_refinement", False)
        if pending or unrefined_count > 0:
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
    parser.add_argument(
        "--duration", type=float, default=None,
        help="実行時間（秒）",
    )
    parser.add_argument(
        "--subagent-calls", type=int, default=None,
        help="サブエージェント呼び出し回数",
    )
    parser.add_argument(
        "--co-skills", nargs="*", default=[],
        help="同一セッションで使用した他スキル",
    )
    parser.add_argument(
        "--sprint-id", default=None,
        help="スプリントID（scrum-master経由時）",
    )
    args = parser.parse_args()

    registry_path = _registry_path()
    if not os.path.isfile(registry_path):
        print(f"⚠️  レジストリが見つかりません: {registry_path}")
        print("   'git-skill-manager repo add' でリポジトリを登録してください")
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

    reg = record_feedback(
        skill_name, args.verdict, args.note, reg,
        duration_sec=args.duration,
        subagent_calls=args.subagent_calls,
        co_skills=args.co_skills,
        sprint_id=args.sprint_id,
    )

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
