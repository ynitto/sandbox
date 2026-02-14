#!/usr/bin/env python3
"""プランJSONのバリデーションスクリプト。

scrum-masterが生成したプランJSONの構造を検証する。

使い方:
    python validate_plan.py <plan.json> [--skills-json <skills.json>]

    plan.json:   プランJSONファイル
    skills.json: discover_skills.py の出力（任意。指定時はスキル名の存在チェックも行う）
"""

import argparse
import json
import sys


VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "skipped"}


def validate_plan(plan: dict, known_skills: set[str] | None = None) -> list[str]:
    """プランを検証してエラーリストを返す。"""
    errors = []

    # トップレベル必須フィールド
    if "goal" not in plan:
        errors.append("'goal' フィールドが必須です")
    elif not isinstance(plan["goal"], str) or not plan["goal"].strip():
        errors.append("'goal' は空でない文字列が必要です")

    if "backlog" not in plan:
        errors.append("'backlog' フィールドが必須です")
        return errors

    backlog = plan["backlog"]
    if not isinstance(backlog, list):
        errors.append("'backlog' はリストが必要です")
        return errors

    if len(backlog) == 0:
        errors.append("'backlog' に最低1つのタスクが必要です")
        return errors

    # タスクID収集
    task_ids = set()
    for i, task in enumerate(backlog):
        prefix = f"backlog[{i}]"

        if not isinstance(task, dict):
            errors.append(f"{prefix}: オブジェクトが必要です")
            continue

        # 必須フィールド
        tid = task.get("id")
        if not tid or not isinstance(tid, str):
            errors.append(f"{prefix}: 'id' は空でない文字列が必要です")
        else:
            if tid in task_ids:
                errors.append(f"{prefix}: ID '{tid}' が重複しています")
            task_ids.add(tid)

        if "action" not in task or not isinstance(task.get("action"), str):
            errors.append(f"{prefix}: 'action' は文字列が必要です")

        if "priority" not in task:
            errors.append(f"{prefix}: 'priority' フィールドが必要です")
        elif not isinstance(task["priority"], int) or task["priority"] < 1:
            errors.append(f"{prefix}: 'priority' は1以上の整数が必要です")

        if "done_criteria" not in task or not isinstance(
            task.get("done_criteria"), str
        ):
            errors.append(f"{prefix}: 'done_criteria' は文字列が必要です")

        # skill: 文字列 or null
        skill = task.get("skill")
        if skill is not None:
            if not isinstance(skill, str):
                errors.append(f"{prefix}: 'skill' は文字列またはnullが必要です")
            elif known_skills is not None and skill not in known_skills:
                errors.append(
                    f"{prefix}: スキル '{skill}' は利用可能なスキル一覧に存在しません"
                )

        # status
        status = task.get("status", "pending")
        if status not in VALID_STATUSES:
            errors.append(
                f"{prefix}: 'status' は {VALID_STATUSES} のいずれかが必要です"
            )

        # depends_on
        deps = task.get("depends_on", [])
        if not isinstance(deps, list):
            errors.append(f"{prefix}: 'depends_on' はリストが必要です")

    # depends_on 参照整合性チェック
    for task in backlog:
        if not isinstance(task, dict):
            continue
        deps = task.get("depends_on", [])
        if not isinstance(deps, list):
            continue
        tid = task.get("id", "?")
        for dep in deps:
            if dep not in task_ids:
                errors.append(
                    f"タスク '{tid}' の depends_on '{dep}' は存在しないIDです"
                )

    # 循環依存検出
    cycle_err = _detect_cycle(backlog)
    if cycle_err:
        errors.append(cycle_err)

    # sprints（任意）
    sprints = plan.get("sprints", [])
    if not isinstance(sprints, list):
        errors.append("'sprints' はリストが必要です")
    else:
        for i, s in enumerate(sprints):
            if not isinstance(s, dict):
                errors.append(f"sprints[{i}]: オブジェクトが必要です")
                continue
            if "sprint" not in s or not isinstance(s["sprint"], int):
                errors.append(f"sprints[{i}]: 'sprint' は整数が必要です")
            if "task_ids" not in s or not isinstance(s["task_ids"], list):
                errors.append(f"sprints[{i}]: 'task_ids' はリストが必要です")
            else:
                for tid in s["task_ids"]:
                    if tid not in task_ids:
                        errors.append(
                            f"sprints[{i}]: task_id '{tid}' はbacklogに存在しません"
                        )

    return errors


def _detect_cycle(backlog: list[dict]) -> str | None:
    """バックログのdepends_onグラフで循環依存を検出する。"""
    graph: dict[str, list[str]] = {}
    for task in backlog:
        if not isinstance(task, dict):
            continue
        tid = task.get("id")
        if not tid:
            continue
        graph[tid] = [
            d for d in task.get("depends_on", []) if isinstance(d, str)
        ]

    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str) -> list[str] | None:
        visited.add(node)
        in_stack.add(node)
        for dep in graph.get(node, []):
            if dep in in_stack:
                return [dep, node]
            if dep not in visited:
                result = dfs(dep)
                if result is not None:
                    return result
        in_stack.discard(node)
        return None

    for node in graph:
        if node not in visited:
            cycle = dfs(node)
            if cycle:
                return f"循環依存を検出: {' → '.join(cycle)}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="プランJSONを検証する")
    parser.add_argument("plan_file", help="プランJSONファイル")
    parser.add_argument("--skills-json", help="discover_skills.py の出力ファイル")
    args = parser.parse_args()

    with open(args.plan_file, encoding="utf-8") as f:
        plan = json.load(f)

    known_skills = None
    if args.skills_json:
        with open(args.skills_json, encoding="utf-8") as f:
            skills_list = json.load(f)
        known_skills = {s["name"] for s in skills_list}

    errors = validate_plan(plan, known_skills)

    if errors:
        print("バリデーション失敗:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("バリデーション成功")
        sys.exit(0)


if __name__ == "__main__":
    main()
