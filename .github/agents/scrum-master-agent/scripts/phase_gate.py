#!/usr/bin/env python3
"""
phase_gate.py — フェーズ遷移のゲート条件バリデーター

各フェーズの開始前・完了後に plan.json を検証し、
フェーズスキップ・不完全な完了・委譲漏れを検出する。

使用法:
  python phase_gate.py pre N        Phase N 開始前チェック（スキップ検出）
  python phase_gate.py post N       Phase N 完了後チェック（ゲート条件検証）
  python phase_gate.py delegation N Phase N の委譲チェックリストを表示
  python phase_gate.py all          全フェーズのゲート状態サマリー

終了コード:
  0 = PASS  1 = FAIL  2 = WARN
"""

import json
import sys
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

# フェーズNで runSubagent に委譲すべき処理
DELEGATION_RULES = {
    2: ["requirements-definer による要件定義"],
    3: ["skill-creator / codebase-to-skill / skill-recruiter によるスキル作成・改良"],
    5: [
        "各タスクの実行（例外なし — すべて runSubagent へ）",
        "コードの実装・テスト・デプロイ",
    ],
    6: [
        "sprint-reviewer によるスプリントレビュー",
        "skill-evaluator によるスキル評価",
        "record_feedback.py によるフィードバック記録",
    ],
}


def load_plan() -> dict | None:
    if not PLAN_FILE.exists():
        return None
    try:
        with PLAN_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── ポストゲートチェッカー（フェーズ別） ───────────────────────

def _post1(plan: dict) -> list[str]:
    """Phase 1: plan.json が存在すればOK（discover_skills.py はスクリプトで検証不可）"""
    return [] if plan else ["plan.json が存在しません。`phase_runner.py init` を実行してください。"]


def _post2(plan: dict) -> list[str]:
    """Phase 2: goal と backlog が plan.json に記録されていること"""
    errors = []
    if not plan.get("goal", "").strip():
        errors.append("goal が空です。ゴールを定義してください。")
    backlog = plan.get("backlog", [])
    if not isinstance(backlog, list) or len(backlog) == 0:
        errors.append("backlog が空です。タスクを作成してください。")
    else:
        required = ["id", "action", "priority", "done_criteria", "status"]
        for i, task in enumerate(backlog):
            for f in required:
                if f not in task:
                    errors.append(f"backlog[{i}] に {f} フィールドがありません。")
    return errors


def _post3(plan: dict) -> list[str]:
    """Phase 3: スキルギャップが解消されていること"""
    errors = []
    if plan.get("current_phase", 1) < 3:
        errors.append("current_phase が 3 未満です。スキルギャップ解消が完了していません。")
    for task in plan.get("backlog", []):
        if "skill" not in task:
            errors.append(f"タスク {task.get('id', '?')} に skill フィールドがありません。")
    return errors


def _post4(plan: dict) -> list[str]:
    """Phase 4: スプリントが作成され execution_groups があること"""
    errors = []
    sprints = plan.get("sprints", [])
    if not sprints:
        errors.append("sprints[] が空です。スプリントプランを作成してください。")
        return errors
    latest = sprints[-1]
    if not latest.get("task_ids"):
        errors.append("最新スプリントの task_ids が空です。")
    if not latest.get("execution_groups"):
        errors.append("最新スプリントの execution_groups（ウェーブ）がありません。")
    return errors


def _post5(plan: dict) -> list[str]:
    """Phase 5: スプリント内の全タスクが終端状態であること"""
    errors = []
    sprints = plan.get("sprints", [])
    if not sprints:
        errors.append("sprints[] が空です。Phase 4 を先に完了してください。")
        return errors
    task_ids = set(sprints[-1].get("task_ids", []))
    backlog = {t["id"]: t for t in plan.get("backlog", []) if "id" in t}
    terminal = {"completed", "failed", "skipped"}
    incomplete = [
        f"{tid}(status={backlog[tid].get('status', '?')})"
        for tid in task_ids
        if tid in backlog and backlog[tid].get("status") not in terminal
    ]
    missing = [tid for tid in task_ids if tid not in backlog]
    if incomplete:
        errors.append(f"未完了タスクがあります: {', '.join(incomplete)}")
    if missing:
        errors.append(f"backlog に存在しないタスク ID があります: {', '.join(missing)}")
    return errors


def _post6(plan: dict) -> list[str]:
    """Phase 6: レビュー・レトロスペクティブが記録されていること"""
    errors = []
    sprints = plan.get("sprints", [])
    if not sprints:
        errors.append("sprints[] が空です。")
        return errors
    latest = sprints[-1]
    if not latest.get("review", "").strip():
        errors.append("最新スプリントの review が空です。sprint-reviewer による評価を完了させてください。")
    if not latest.get("retro", "").strip():
        errors.append("最新スプリントの retro（レトロスペクティブ）が空です。")
    return errors


def _post7(plan: dict) -> list[str]:
    """Phase 7: velocity.remaining が実際の pending タスク数と一致していること"""
    backlog = plan.get("backlog", [])
    actual_remaining = len([t for t in backlog if t.get("status") == "pending"])
    recorded = plan.get("velocity", {}).get("remaining", -1)
    if recorded != actual_remaining:
        return [
            f"velocity.remaining ({recorded}) が "
            f"実際の pending タスク数 ({actual_remaining}) と一致しません。"
        ]
    return []


POST_CHECKERS = {1: _post1, 2: _post2, 3: _post3, 4: _post4, 5: _post5, 6: _post6, 7: _post7}


# ── コマンド ────────────────────────────────────────────────────

def cmd_pre(phase: int) -> int:
    plan = load_plan()
    print(f"[PRE-CHECK] Phase {phase}: {PHASE_NAMES.get(phase, '不明')}")
    print("-" * 50)

    if plan is None and phase > 1:
        print("[FAIL] plan.json がありません。`phase_runner.py init` を実行してください。")
        return 1

    if plan is not None:
        current = plan.get("current_phase", 1)
        if phase > current + 1:
            print(f"[FAIL] フェーズスキップを検出しました。")
            print(f"  現在: Phase {current} ({PHASE_NAMES.get(current, '?')})")
            print(f"  要求: Phase {phase} ({PHASE_NAMES.get(phase, '?')})")
            print(f"  → Phase {current} を完了してから Phase {current + 1} に進んでください。")
            return 1

    print(f"[PASS] Phase {phase} の開始条件を満たしています。")

    if phase in DELEGATION_RULES:
        print(f"\n[委譲チェックリスト] 以下は runSubagent に委譲すること（直接実行禁止）:")
        for rule in DELEGATION_RULES[phase]:
            print(f"  ✔ {rule}")

    return 0


def cmd_post(phase: int) -> int:
    plan = load_plan()
    print(f"[POST-CHECK] Phase {phase}: {PHASE_NAMES.get(phase, '不明')}")
    print("-" * 50)

    if plan is None:
        print("[FAIL] plan.json が存在しません。")
        return 1

    checker = POST_CHECKERS.get(phase)
    if checker is None:
        print(f"[WARN] Phase {phase} のチェッカーが未定義です。")
        return 2

    errors = checker(plan)
    if errors:
        print(f"[FAIL] ゲート条件未達成 ({len(errors)} 件):")
        for e in errors:
            print(f"  ✗ {e}")
        print(f"\n→ 解消後に `phase_runner.py advance {phase + 1}` を実行してください。")
        return 1

    print(f"[PASS] Phase {phase} のゲート条件を満たしています。")
    print(f"→ `phase_runner.py advance {phase + 1}` で次のフェーズへ進んでください。")
    return 0


def cmd_delegation(phase: int) -> int:
    rules = DELEGATION_RULES.get(phase)
    if not rules:
        print(f"[INFO] Phase {phase} に委譲ルールはありません。")
        return 0
    print(f"[DELEGATION] Phase {phase}: {PHASE_NAMES.get(phase, '不明')}")
    print("-" * 50)
    print("以下を runSubagent に委譲すること（直接実行禁止）:")
    for rule in rules:
        print(f"  ✗ {rule}")
    print("\nテンプレート: ${SKILLS_DIR}/scrum-master/references/subagent-templates.md")
    return 0


def cmd_all() -> int:
    plan = load_plan()
    print("=" * 60)
    print("全フェーズ ゲート状態")
    print("=" * 60)

    if plan is None:
        print("[未開始] plan.json が存在しません")
        return 0

    current = plan.get("current_phase", 1)

    for phase in range(1, 8):
        checker = POST_CHECKERS.get(phase)
        if phase > current:
            status = "⏳ 未到達"
        elif checker is None:
            status = "❓ チェッカー未定義"
        else:
            errors = checker(plan)
            status = "✓ PASS" if not errors else f"✗ FAIL ({len(errors)} 件) — {errors[0]}"

        marker = "→" if phase == current else " "
        print(f"{marker} Phase {phase} ({PHASE_NAMES[phase]}): {status}")

    print("=" * 60)
    return 0


# ── エントリポイント ────────────────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    cmd = args[0]

    if cmd in ("pre", "post", "delegation"):
        if len(args) < 2:
            print(f"[ERROR] {cmd} N が必要です。", file=sys.stderr)
            return 1
        try:
            phase = int(args[1])
        except ValueError:
            print(f"[ERROR] 無効なフェーズ番号: {args[1]}", file=sys.stderr)
            return 1
        if cmd == "pre":
            return cmd_pre(phase)
        elif cmd == "post":
            return cmd_post(phase)
        else:
            return cmd_delegation(phase)

    elif cmd == "all":
        return cmd_all()

    else:
        print(f"[ERROR] 不明なコマンド: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
