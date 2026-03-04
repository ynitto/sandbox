#!/usr/bin/env python3
"""
phase_runner.py — scrum-master-agent のフェーズ状態機械

使用法:
  python phase_runner.py status          現在のフェーズと次アクションを表示
  python phase_runner.py advance N       current_phase を N に更新
  python phase_runner.py init [goal]     plan.json を初期化して Phase 1 から開始
  python phase_runner.py recover         plan.json の破損・欠損から回復
  python phase_runner.py force-advance N ゲートを無視して強制進行（ユーザー指示時のみ）
  python phase_runner.py debug           plan.json の全内容をダンプ
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PLAN_FILE = Path("plan.json")

PHASE_NAMES = {
    1: "スキル探索",
    2: "バックログ作成",
    3: "スキルギャップ解決",
    4: "スプリントプランニング",
    5: "タスク実行",
    6: "スプリントレビュー",
    7: "進捗レポート",
}

PHASE_GATE_REQUIREMENTS = {
    1: "スキル一覧JSONが取得済みであること",
    2: "plan.json がルートに保存され goal と backlog[] が含まれていること",
    3: "スキルギャップが解消され current_phase = 3 であること",
    4: "ユーザーがスプリントプランを承認済みであること",
    5: "全ウェーブの実行が完了（または中断選択）していること",
    6: "sprint-reviewer によるレビューとフィードバック収集が完了していること",
    7: "ユーザーが次アクションを選択済みであること",
}

MAX_SPRINTS = 5
MAX_SKILL_CREATION_RETRY = 2
MAX_VALIDATION_RETRY = 3
MAX_SUBAGENT_RETRY = 1


def load_plan() -> dict | None:
    if not PLAN_FILE.exists():
        return None
    try:
        with PLAN_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] plan.json の読み込みに失敗: {e}", file=sys.stderr)
        return None


def save_plan(plan: dict) -> None:
    plan["_updated_at"] = datetime.now(timezone.utc).isoformat()
    with PLAN_FILE.open("w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"[OK] plan.json を保存しました (current_phase={plan.get('current_phase')})")


def make_initial_plan(goal: str = "") -> dict:
    return {
        "current_phase": 1,
        "goal": goal,
        "requirements_source": "direct",
        "backlog": [],
        "sprints": [],
        "velocity": {
            "completed_per_sprint": [],
            "remaining": 0,
        },
        "_created_at": datetime.now(timezone.utc).isoformat(),
        "_updated_at": datetime.now(timezone.utc).isoformat(),
        "_agent": "scrum-master-agent",
        "_version": "1.0.0",
    }


def cmd_status() -> int:
    plan = load_plan()
    if plan is None:
        print("=" * 60)
        print("STATUS: plan.json が存在しません")
        print("ACTION: `phase_runner.py init [goal]` で Phase 1 から開始してください")
        print("=" * 60)
        return 0

    phase = plan.get("current_phase", 1)
    goal = plan.get("goal", "(未設定)")
    backlog = plan.get("backlog", [])
    sprints = plan.get("sprints", [])
    pending = [t for t in backlog if t.get("status") == "pending"]
    completed = [t for t in backlog if t.get("status") == "completed"]

    print("=" * 60)
    print(f"CURRENT PHASE : {phase} — {PHASE_NAMES.get(phase, '不明')}")
    print(f"GOAL          : {goal}")
    print(f"BACKLOG       : {len(backlog)} タスク ({len(completed)} 完了 / {len(pending)} 残)")
    print(f"SPRINTS       : {len(sprints)} / {MAX_SPRINTS} スプリント完了")
    print("-" * 60)
    print(f"GATE ({phase}→{phase+1}): {PHASE_GATE_REQUIREMENTS.get(phase, 'N/A')}")
    print("-" * 60)

    # ガードレール警告
    if len(sprints) >= MAX_SPRINTS:
        print(f"[WARN] スプリント上限 ({MAX_SPRINTS}) に達しています。継続にはユーザー確認が必要です。")

    # 現在のスプリントのスキル作成リトライ数チェック
    skill_retry = plan.get("_skill_creation_retry", 0)
    if skill_retry >= MAX_SKILL_CREATION_RETRY:
        print(f"[WARN] スキル作成リトライが上限 ({MAX_SKILL_CREATION_RETRY}) に達しています。ユーザーに相談してください。")

    validation_retry = plan.get("_validation_retry", 0)
    if validation_retry >= MAX_VALIDATION_RETRY:
        print(f"[WARN] バリデーションリトライが上限 ({MAX_VALIDATION_RETRY}) に達しています。手動修正が必要です。")

    print("=" * 60)
    return 0


def cmd_advance(target_phase: int) -> int:
    plan = load_plan()
    if plan is None:
        print("[ERROR] plan.json が存在しません。先に `init` を実行してください。", file=sys.stderr)
        return 1

    current = plan.get("current_phase", 1)
    if target_phase < current:
        print(f"[ERROR] 後退はできません (current={current} → target={target_phase})", file=sys.stderr)
        print("後退が必要な場合は `force-advance` を使用してください（ユーザー指示時のみ）")
        return 1

    if target_phase > current + 1:
        print(f"[ERROR] フェーズを飛ばすことはできません (current={current} → target={target_phase})", file=sys.stderr)
        print(f"Phase {current} のゲート条件を満たしてから Phase {current+1} へ進んでください。")
        print(f"ゲート条件: {PHASE_GATE_REQUIREMENTS.get(current, 'N/A')}")
        return 1

    if target_phase > 7:
        print("[OK] 全フェーズ完了。スプリントを終了します。")
        plan["current_phase"] = 8  # 完了状態
        save_plan(plan)
        return 0

    plan["current_phase"] = target_phase
    save_plan(plan)
    print(f"=== PHASE {target_phase}: {PHASE_NAMES.get(target_phase, '')} 開始 ===")
    return 0


def cmd_init(goal: str = "") -> int:
    if PLAN_FILE.exists():
        print("[WARN] plan.json が既に存在します。上書きしますか？ (y/N): ", end="")
        ans = input().strip().lower()
        if ans != "y":
            print("キャンセルしました。")
            return 0

    plan = make_initial_plan(goal)
    save_plan(plan)
    print("=== PHASE 1: スキル探索 開始 ===")
    print(f"ゴール: {goal or '(未設定 — Phase 2 で定義)'}")
    return 0


def cmd_recover() -> int:
    plan = load_plan()

    if plan is None:
        print("[RECOVER] plan.json が存在しないか破損しています。新規作成します。")
        plan = make_initial_plan()
        save_plan(plan)
        print("[OK] Phase 1 から再開します。")
        return 0

    issues = []

    # 必須フィールドチェック
    if "current_phase" not in plan:
        plan["current_phase"] = 1
        issues.append("current_phase が欠損 → 1 に設定")

    if "goal" not in plan:
        plan["goal"] = ""
        issues.append("goal が欠損 → 空文字に設定")

    if "backlog" not in plan or not isinstance(plan["backlog"], list):
        plan["backlog"] = []
        issues.append("backlog が欠損/不正 → 空配列に設定")

    if "sprints" not in plan or not isinstance(plan["sprints"], list):
        plan["sprints"] = []
        issues.append("sprints が欠損/不正 → 空配列に設定")

    if "velocity" not in plan or not isinstance(plan["velocity"], dict):
        plan["velocity"] = {"completed_per_sprint": [], "remaining": 0}
        issues.append("velocity が欠損/不正 → デフォルト値に設定")

    # バックログのステータス整合性チェック
    valid_statuses = {"pending", "in_progress", "completed", "failed", "skipped"}
    for task in plan.get("backlog", []):
        if task.get("status") not in valid_statuses:
            task["status"] = "pending"
            issues.append(f"タスク {task.get('id', '?')} の status が不正 → pending にリセット")

    if issues:
        print("[RECOVER] 以下の問題を修正しました:")
        for issue in issues:
            print(f"  - {issue}")
        save_plan(plan)
    else:
        print("[OK] plan.json に問題は見つかりませんでした。")

    print(f"現在のフェーズ: {plan['current_phase']} — {PHASE_NAMES.get(plan['current_phase'], '不明')}")
    return 0


def cmd_force_advance(target_phase: int) -> int:
    plan = load_plan()
    if plan is None:
        print("[ERROR] plan.json が存在しません。", file=sys.stderr)
        return 1

    current = plan.get("current_phase", 1)
    print(f"[FORCE] Phase {current} → Phase {target_phase} に強制進行します。")
    print("[WARN] ゲート条件の確認をスキップします。ユーザー指示による実行のみ許可されます。")
    plan["current_phase"] = target_phase
    plan["_force_advanced"] = True
    save_plan(plan)
    print(f"=== PHASE {target_phase}: {PHASE_NAMES.get(target_phase, '')} 開始 (強制) ===")
    return 0


def cmd_debug() -> int:
    plan = load_plan()
    if plan is None:
        print("[DEBUG] plan.json が存在しません")
        return 1

    print("[DEBUG] plan.json の内容:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_increment_retry(retry_type: str) -> int:
    """リトライカウンターをインクリメント。上限チェックあり。"""
    plan = load_plan()
    if plan is None:
        return 1

    key = f"_{'skill_creation' if retry_type == 'skill' else 'validation'}_retry"
    limit = MAX_SKILL_CREATION_RETRY if retry_type == "skill" else MAX_VALIDATION_RETRY

    current_count = plan.get(key, 0) + 1
    plan[key] = current_count
    save_plan(plan)

    if current_count >= limit:
        print(f"[WARN] リトライ上限 ({limit}) に達しました。ユーザーに相談してください。")
        return 2  # 上限到達を示す終了コード
    else:
        print(f"[INFO] リトライ {current_count}/{limit}")
        return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    cmd = args[0]

    if cmd == "status":
        return cmd_status()

    elif cmd == "advance":
        if len(args) < 2:
            print("[ERROR] advance にはフェーズ番号が必要です。例: phase_runner.py advance 3", file=sys.stderr)
            return 1
        try:
            target = int(args[1])
        except ValueError:
            print(f"[ERROR] フェーズ番号が無効です: {args[1]}", file=sys.stderr)
            return 1
        return cmd_advance(target)

    elif cmd == "init":
        goal = " ".join(args[1:]) if len(args) > 1 else ""
        return cmd_init(goal)

    elif cmd == "recover":
        return cmd_recover()

    elif cmd == "force-advance":
        if len(args) < 2:
            print("[ERROR] force-advance にはフェーズ番号が必要です。", file=sys.stderr)
            return 1
        try:
            target = int(args[1])
        except ValueError:
            print(f"[ERROR] フェーズ番号が無効です: {args[1]}", file=sys.stderr)
            return 1
        return cmd_force_advance(target)

    elif cmd == "debug":
        return cmd_debug()

    elif cmd == "retry":
        if len(args) < 2:
            print("[ERROR] retry には種別 (skill|validation) が必要です。", file=sys.stderr)
            return 1
        return cmd_increment_retry(args[1])

    else:
        print(f"[ERROR] 不明なコマンド: {cmd}", file=sys.stderr)
        print("使用法: python phase_runner.py {status|advance|init|recover|force-advance|debug|retry}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
