#!/usr/bin/env python3
"""
evaluator.py — タスク実行後の自己評価チェックポイント。

Hermes Agent の仕様に基づき:
  - 15 ツールコールごと、または 5+ 回を要した複雑タスク完了後に評価
  - 評価結果をもとに再利用価値の高い手順を ltm-use に procedural 記憶として保存

使い方:
  # タスク完了後に呼び出す
  python evaluator.py evaluate \
    --task-title "PR のコードレビュー" \
    --tool-calls 8 \
    --success true \
    --steps '["gl.py list-mrs", "git diff", "agent-reviewer", "gl.py add-mr-comment"]'

  # チェックポイント判定のみ
  python evaluator.py should-evaluate --tool-calls 15 --completed true
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHECKPOINT_EVERY_N_CALLS = 15
COMPLEX_TASK_THRESHOLD = 5

# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class Trace:
    task_title: str
    tool_calls: int
    steps: list[str]
    success: bool
    duration_sec: float = 0.0

@dataclass
class EvaluationResult:
    save_as_skill: bool
    efficiency_score: int        # 1-5
    reusability_score: int       # 1-5
    improvement_suggestion: str
    skill_name: str | None
    skill_category: str | None
    reasons: list[str] = field(default_factory=list)

# ---------------------------------------------------------------------------
# SelfEvaluator
# ---------------------------------------------------------------------------

class SelfEvaluator:

    def should_evaluate(self, tool_call_count: int, task_completed: bool) -> bool:
        """評価チェックポイントを発動するか判定。"""
        return (
            tool_call_count % CHECKPOINT_EVERY_N_CALLS == 0
            or (task_completed and tool_call_count >= COMPLEX_TASK_THRESHOLD)
        )

    def evaluate_trace(self, trace: Trace) -> EvaluationResult:
        """実行トレースを評価してスキル保存の判断と改善案を返す。"""
        reasons = []

        # 効率性スコア（ツールコール数から推定）
        if trace.tool_calls <= 5:
            efficiency = 5
        elif trace.tool_calls <= 10:
            efficiency = 4
        elif trace.tool_calls <= 15:
            efficiency = 3
        elif trace.tool_calls <= 20:
            efficiency = 2
        else:
            efficiency = 1

        # 再利用性スコア（ステップの汎用性から推定）
        generic_keywords = ["git", "gl.py", "pytest", "review", "deploy", "test", "build"]
        generic_count = sum(
            1 for step in trace.steps
            if any(kw in step.lower() for kw in generic_keywords)
        )
        reusability = min(5, max(1, generic_count + 1))

        # 保存判断
        save_as_skill = (
            trace.success
            and reusability >= 3
            and trace.tool_calls >= COMPLEX_TASK_THRESHOLD
        )

        if not trace.success:
            reasons.append("タスクが失敗したため保存しない")
        if reusability < 3:
            reasons.append("汎用性が低いため保存しない")
        if trace.tool_calls < COMPLEX_TASK_THRESHOLD:
            reasons.append("単純タスク（ツールコール数が少ない）のため保存しない")
        if save_as_skill:
            reasons.append(f"再利用性スコア {reusability}/5、効率性スコア {efficiency}/5 で保存価値あり")

        # 改善提案
        if efficiency <= 2:
            improvement = "ツールコール数が多い。中間ステップを統合できないか検討する。"
        elif not trace.success:
            improvement = "失敗原因を特定し、前提条件チェックを手順の冒頭に追加する。"
        else:
            improvement = "現在の手順は効率的です。"

        # スキル名・カテゴリの推定
        skill_name = None
        skill_category = None
        if save_as_skill:
            title_lower = trace.task_title.lower()
            category_map = {
                "code_review": ["レビュー", "review", "diff"],
                "testing":     ["テスト", "test", "pytest", "coverage"],
                "deployment":  ["デプロイ", "deploy", "build", "release"],
                "refactoring": ["リファクタ", "refactor"],
                "research":    ["調査", "research", "調べ"],
            }
            for cat, kws in category_map.items():
                if any(kw in title_lower for kw in kws):
                    skill_category = cat
                    break
            skill_category = skill_category or "general"
            # タイトルからスネークケースのスキル名を生成
            import re
            slug = re.sub(r"[^\w\s]", "", trace.task_title.lower())
            slug = re.sub(r"\s+", "_", slug.strip())[:40]
            skill_name = slug or "unnamed_skill"

        return EvaluationResult(
            save_as_skill=save_as_skill,
            efficiency_score=efficiency,
            reusability_score=reusability,
            improvement_suggestion=improvement,
            skill_name=skill_name,
            skill_category=skill_category,
            reasons=reasons,
        )

    def generate_skill_from_trace(self, trace: Trace, evaluation: EvaluationResult) -> str | None:
        """評価結果をもとに ltm-use に procedural 記憶として保存する。
        保存した記憶の ID を返す。保存しない場合は None。"""
        if not evaluation.save_as_skill:
            return None

        ltm_save = _find_ltm_save()
        steps_md = "\n".join(f"{i+1}. {s}" for i, s in enumerate(trace.steps))
        content = f"""## 概要

{trace.task_title}

## 手順

{steps_md}

## 評価

- ツールコール数: {trace.tool_calls}
- 効率性スコア: {evaluation.efficiency_score}/5
- 再利用性スコア: {evaluation.reusability_score}/5
- 改善提案: {evaluation.improvement_suggestion}
"""
        r = subprocess.run(
            [
                sys.executable, ltm_save,
                "--no-dedup", "--no-auto-tags",
                "--scope", "home",
                "--category", evaluation.skill_category or "general",
                "--title", f"{trace.task_title}（procedural）",
                "--summary", f"{trace.task_title} の手順。{trace.tool_calls} ツールコール、成功率: {'成功' if trace.success else '失敗'}。",
                "--content", content,
                "--tags", f"procedural,{evaluation.skill_category or 'general'},auto-generated",
            ],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"[evaluator] WARN: ltm-use 保存に失敗: {r.stderr[:200]}", file=sys.stderr)
            return None

        print(r.stdout.strip())
        import re
        m = re.search(r"mem-\d{8}-\d+", r.stdout)
        return m.group(0) if m else None


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _find_ltm_save() -> str:
    candidates = [Path.home() / ".kiro/skills/ltm-use/scripts/save_memory.py"]
    for c in candidates:
        if c.exists():
            return str(c)
    return "save_memory.py"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="タスク実行後の自己評価チェックポイント")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("should-evaluate", help="評価チェックポイントを発動するか判定")
    p.add_argument("--tool-calls", type=int, required=True)
    p.add_argument("--completed", type=lambda x: x.lower() == "true", default=False)

    p = sub.add_parser("evaluate", help="実行トレースを評価してスキル保存を判断")
    p.add_argument("--task-title", required=True)
    p.add_argument("--tool-calls", type=int, required=True)
    p.add_argument("--success", type=lambda x: x.lower() == "true", default=True)
    p.add_argument("--steps", default="[]", help="JSON 配列形式のステップリスト")
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--save", action="store_true", help="評価後に ltm-use へ自動保存")
    p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()
    evaluator = SelfEvaluator()

    if args.cmd == "should-evaluate":
        result = evaluator.should_evaluate(args.tool_calls, args.completed)
        print(json.dumps({"should_evaluate": result}))

    elif args.cmd == "evaluate":
        steps = json.loads(args.steps)
        trace = Trace(
            task_title=args.task_title,
            tool_calls=args.tool_calls,
            steps=steps,
            success=args.success,
            duration_sec=args.duration,
        )
        ev = evaluator.evaluate_trace(trace)

        result = {
            "save_as_skill": ev.save_as_skill,
            "efficiency_score": ev.efficiency_score,
            "reusability_score": ev.reusability_score,
            "improvement_suggestion": ev.improvement_suggestion,
            "skill_name": ev.skill_name,
            "skill_category": ev.skill_category,
            "reasons": ev.reasons,
        }

        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"save_as_skill      : {ev.save_as_skill}")
            print(f"efficiency_score   : {ev.efficiency_score}/5")
            print(f"reusability_score  : {ev.reusability_score}/5")
            print(f"improvement        : {ev.improvement_suggestion}")
            for r in ev.reasons:
                print(f"  - {r}")

        if args.save and ev.save_as_skill:
            mem_id = evaluator.generate_skill_from_trace(trace, ev)
            if mem_id:
                print(f"\n[evaluator] ltm-use に保存しました: {mem_id}")


if __name__ == "__main__":
    main()
