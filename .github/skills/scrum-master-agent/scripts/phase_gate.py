#!/usr/bin/env python3
"""
phase_gate.py — フェーズ遷移のゲート条件バリデーター

使用法:
  python phase_gate.py pre N        フェーズN開始前の事前チェック
  python phase_gate.py post N       フェーズN完了後の事後チェック（次へ進む前に必須）
  python phase_gate.py delegation N フェーズNで委譲すべき処理を直接実行していないか確認
  python phase_gate.py all          全フェーズのゲート状態をサマリー表示

終了コード:
  0 = PASS（次のフェーズへ進んでよい）
  1 = FAIL（問題があるため進んではいけない）
  2 = WARN（問題はあるが続行可能、ユーザー確認推奨）
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

# フェーズNの「事後ゲート条件」: plan.json に対して検証する関数リスト
def _gate_phase1_post(plan: dict) -> list[str]:
    """Phase 1 完了条件: スキル一覧が取得済み（plan.jsonに記録がある）"""
    errors = []
    # Phase 1 は plan.json への書き込み要件が軽いので current_phase 確認のみ
    # discover_skills.py の実行結果は外部から確認できないため、
    # current_phase >= 1 かつ plan.json が存在すればOKとする
    if not plan:
        errors.append("plan.json が存在しません。`phase_runner.py init` を実行してください。")
    return errors


def _gate_phase2_post(plan: dict) -> list[str]:
    """Phase 2 完了条件: goal と backlog[] が plan.json に含まれている"""
    errors = []
    if not plan.get("goal", "").strip():
        errors.append("plan.json の goal が空です。ゴールを定義してください。")
    backlog = plan.get("backlog", [])
    if not isinstance(backlog, list) or len(backlog) == 0:
        errors.append("plan.json の backlog が空です。タスクを作成してください。")
    else:
        # バックログタスクの必須フィールドチェック
        required_fields = ["id", "action", "priority", "done_criteria", "status"]
        for i, task in enumerate(backlog):
            for field in required_fields:
                if field not in task:
                    errors.append(f"backlog[{i}] に {field} フィールドがありません。")
    return errors


def _gate_phase3_post(plan: dict) -> list[str]:
    """Phase 3 完了条件: スキルギャップが解消されている（current_phase が 3 以上）"""
    errors = []
    current = plan.get("current_phase", 1)
    if current < 3:
        errors.append(f"current_phase が {current} です。Phase 3 完了には current_phase >= 3 が必要です。")
    # backlog の全タスクに skill または null が設定されているかチェック
    backlog = plan.get("backlog", [])
    for task in backlog:
        if "skill" not in task:
            errors.append(f"タスク {task.get('id', '?')} に skill フィールドがありません。")
    return errors


def _gate_phase4_post(plan: dict) -> list[str]:
    """Phase 4 完了条件: 現在スプリントが sprints[] に追加され execution_groups がある"""
    errors = []
    sprints = plan.get("sprints", [])
    if not sprints:
        errors.append("sprints[] が空です。スプリントプランを作成してください。")
        return errors
    latest_sprint = sprints[-1]
    if "task_ids" not in latest_sprint or not latest_sprint["task_ids"]:
        errors.append("最新スプリントの task_ids が空です。タスクを選択してください。")
    if "execution_groups" not in latest_sprint or not latest_sprint["execution_groups"]:
        errors.append("最新スプリントの execution_groups（ウェーブ）がありません。依存関係を分析してウェーブを定義してください。")
    return errors


def _gate_phase5_post(plan: dict) -> list[str]:
    """Phase 5 完了条件: 現在スプリントの全タスクが完了・失敗・スキップのいずれか"""
    errors = []
    sprints = plan.get("sprints", [])
    if not sprints:
        errors.append("sprints[] が空です。Phase 4 を先に完了してください。")
        return errors
    latest_sprint = sprints[-1]
    task_ids = set(latest_sprint.get("task_ids", []))
    backlog = {t["id"]: t for t in plan.get("backlog", []) if "id" in t}
    terminal_statuses = {"completed", "failed", "skipped"}
    incomplete = []
    for tid in task_ids:
        task = backlog.get(tid)
        if task is None:
            errors.append(f"タスク {tid} が backlog に存在しません。")
        elif task.get("status") not in terminal_statuses:
            incomplete.append(f"{tid}(status={task.get('status', '?')})")
    if incomplete:
        errors.append(f"未完了タスクがあります: {', '.join(incomplete)}")
    return errors


def _gate_phase6_post(plan: dict) -> list[str]:
    """Phase 6 完了条件: 最新スプリントに review フィールドがある"""
    errors = []
    sprints = plan.get("sprints", [])
    if not sprints:
        errors.append("sprints[] が空です。")
        return errors
    latest_sprint = sprints[-1]
    if not latest_sprint.get("review", "").strip():
        errors.append("最新スプリントの review が空です。sprint-reviewer による評価を完了させてください。")
    if not latest_sprint.get("retro", "").strip():
        errors.append("最新スプリントの retro（レトロスペクティブ）が空です。")
    return errors


def _gate_phase7_post(plan: dict) -> list[str]:
    """Phase 7 完了条件: ユーザーが次アクションを選択済み（plan.json に記録）"""
    # Phase 7 は対話的なのでスクリプトで完全には検証できない。
    # ここでは remaining velocity の整合性のみチェック。
    errors = []
    backlog = plan.get("backlog", [])
    remaining = len([t for t in backlog if t.get("status") == "pending"])
    velocity = plan.get("velocity", {})
    if velocity.get("remaining", -1) != remaining:
        errors.append(
            f"velocity.remaining ({velocity.get('remaining')}) が "
            f"実際の pending タスク数 ({remaining}) と一致しません。"
        )
    return errors


POST_GATE_CHECKERS = {
    1: _gate_phase1_post,
    2: _gate_phase2_post,
    3: _gate_phase3_post,
    4: _gate_phase4_post,
    5: _gate_phase5_post,
    6: _gate_phase6_post,
    7: _gate_phase7_post,
}

# フェーズNで委譲すべき処理のチェックリスト（自分で実行してはいけないもの）
DELEGATION_RULES = {
    2: [
        "requirements-definer スキルのタスクは必ずサブエージェントに委譲すること",
        "要件定義ドキュメントの作成・編集をスクラムマスター自身が行ってはならない",
    ],
    3: [
        "skill-creator / codebase-to-skill / skill-recruiter は必ずサブエージェントに委譲すること",
        "新しいスキルのSKILL.mdを自分で書いてはならない",
    ],
    5: [
        "バックログの各タスクは必ずサブエージェントに委譲すること（例外なし）",
        "コードの実装・テスト・デプロイを自分で行ってはならない",
        "サブエージェントが返す結果を集約するだけにとどめること",
    ],
    6: [
        "sprint-reviewer による評価はサブエージェントに委譲すること",
        "skill-evaluator による評価はサブエージェントに委譲すること",
        "フィードバック記録（record_feedback.py）はサブエージェントに委譲すること",
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


def cmd_pre(phase: int) -> int:
    """フェーズN開始前の事前チェック"""
    plan = load_plan()

    print(f"[PRE-CHECK] Phase {phase}: {PHASE_NAMES.get(phase, '不明')} 開始前チェック")
    print("-" * 50)

    if plan is None and phase > 1:
        print("[FAIL] plan.json が存在しません。Phase 1 から開始するか `recover` を実行してください。")
        return 1

    if plan is not None:
        current = plan.get("current_phase", 1)
        if phase > current + 1:
            print(f"[FAIL] フェーズスキップを検出しました。")
            print(f"  現在: Phase {current} ({PHASE_NAMES.get(current, '?')})")
            print(f"  要求: Phase {phase} ({PHASE_NAMES.get(phase, '?')})")
            print(f"  → Phase {current} を完了してから Phase {current+1} に進んでください。")
            return 1

    print(f"[PASS] Phase {phase} の開始条件を満たしています。")
    if phase in DELEGATION_RULES:
        print(f"\n[委譲チェックリスト] Phase {phase} で必ず委譲すること:")
        for rule in DELEGATION_RULES[phase]:
            print(f"  ✔ {rule}")
    return 0


def cmd_post(phase: int) -> int:
    """フェーズN完了後の事後チェック"""
    plan = load_plan()

    print(f"[POST-CHECK] Phase {phase}: {PHASE_NAMES.get(phase, '不明')} 完了チェック")
    print("-" * 50)

    if plan is None:
        print("[FAIL] plan.json が存在しません。")
        return 1

    checker = POST_GATE_CHECKERS.get(phase)
    if checker is None:
        print(f"[WARN] Phase {phase} のゲートチェッカーが定義されていません。")
        return 2

    errors = checker(plan)
    if errors:
        print(f"[FAIL] ゲート条件を満たしていません ({len(errors)} 件の問題):")
        for err in errors:
            print(f"  ✗ {err}")
        print(f"\n→ 上記を解消してから `phase_runner.py advance {phase+1}` を実行してください。")
        return 1
    else:
        print(f"[PASS] Phase {phase} のゲート条件をすべて満たしています。")
        print(f"→ `phase_runner.py advance {phase+1}` でPhase {phase+1} に進んでください。")
        return 0


def cmd_delegation(phase: int) -> int:
    """フェーズNの委譲ルールを表示（リマインダー）"""
    rules = DELEGATION_RULES.get(phase)
    if not rules:
        print(f"[INFO] Phase {phase} には特別な委譲ルールはありません。")
        return 0

    print(f"[DELEGATION CHECK] Phase {phase}: {PHASE_NAMES.get(phase, '不明')}")
    print("-" * 50)
    print("以下の処理はサブエージェントに委譲してください（自分で実行禁止）:")
    for rule in rules:
        print(f"  ✗ 禁止: {rule}")
    print("\n委譲テンプレート: scrum-master/references/subagent-templates.md を参照")
    return 0


def cmd_all() -> int:
    """全フェーズのゲート状態サマリー"""
    plan = load_plan()

    print("=" * 60)
    print("全フェーズ ゲート状態サマリー")
    print("=" * 60)

    if plan is None:
        print("[未開始] plan.json が存在しません")
        return 0

    current = plan.get("current_phase", 1)

    for phase in range(1, 8):
        checker = POST_GATE_CHECKERS.get(phase)
        if phase > current:
            status = "⏳ 未到達"
            detail = ""
        elif checker is None:
            status = "❓ チェッカー未定義"
            detail = ""
        else:
            errors = checker(plan)
            if errors:
                status = f"✗ FAIL ({len(errors)} 件)"
                detail = f" — {errors[0]}" if errors else ""
            else:
                status = "✓ PASS"
                detail = ""

        marker = "→ 現在" if phase == current else "  "
        print(f"{marker} Phase {phase} ({PHASE_NAMES[phase]}): {status}{detail}")

    print("=" * 60)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    cmd = args[0]

    if cmd == "pre":
        if len(args) < 2:
            print("[ERROR] pre にはフェーズ番号が必要です。例: phase_gate.py pre 3", file=sys.stderr)
            return 1
        try:
            phase = int(args[1])
        except ValueError:
            print(f"[ERROR] フェーズ番号が無効です: {args[1]}", file=sys.stderr)
            return 1
        return cmd_pre(phase)

    elif cmd == "post":
        if len(args) < 2:
            print("[ERROR] post にはフェーズ番号が必要です。例: phase_gate.py post 3", file=sys.stderr)
            return 1
        try:
            phase = int(args[1])
        except ValueError:
            print(f"[ERROR] フェーズ番号が無効です: {args[1]}", file=sys.stderr)
            return 1
        return cmd_post(phase)

    elif cmd == "delegation":
        if len(args) < 2:
            print("[ERROR] delegation にはフェーズ番号が必要です。", file=sys.stderr)
            return 1
        try:
            phase = int(args[1])
        except ValueError:
            print(f"[ERROR] フェーズ番号が無効です: {args[1]}", file=sys.stderr)
            return 1
        return cmd_delegation(phase)

    elif cmd == "all":
        return cmd_all()

    else:
        print(f"[ERROR] 不明なコマンド: {cmd}", file=sys.stderr)
        print("使用法: python phase_gate.py {pre|post|delegation|all} [N]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
