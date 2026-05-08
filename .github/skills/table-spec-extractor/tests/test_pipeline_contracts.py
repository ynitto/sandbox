#!/usr/bin/env python3
"""スキル間パイプライン契約テスト。

各スキルの meta.yaml に定義された io_contract に基づいて、
上流スキルの出力が下流スキルの入力として有効かどうかを検証する。

使い方:
    python test_pipeline_contracts.py
    python test_pipeline_contracts.py --pipeline requirements-to-api
    python test_pipeline_contracts.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / ".github" / "skills"

# ──────────────────────────────────────────────
# meta.yaml パーサー
# ──────────────────────────────────────────────

def parse_io_contract(skill_name: str) -> dict:
    """meta.yaml の io_contract セクションを解析する。"""
    meta_yaml = SKILLS_DIR / skill_name / "meta.yaml"
    if not meta_yaml.exists():
        return {}

    content = meta_yaml.read_text(encoding="utf-8")
    io_contract: dict = {"input": [], "output": []}
    in_io_contract = False
    current_section = None
    current_item: dict = {}

    for line in content.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if not stripped or stripped.startswith("#"):
            continue

        # io_contract: の開始（indent=0）
        if indent == 0 and stripped.startswith("io_contract:"):
            in_io_contract = True
            continue

        # io_contract 以外のトップレベルキーで終了
        if indent == 0 and in_io_contract and ":" in stripped:
            in_io_contract = False
            continue

        if not in_io_contract:
            continue

        # input: / output: セクション（indent=2）
        if indent == 2 and stripped.startswith("input:"):
            if current_item and current_section:
                io_contract[current_section].append(current_item)
                current_item = {}
            current_section = "input"
            continue
        if indent == 2 and stripped.startswith("output:"):
            if current_item and current_section:
                io_contract[current_section].append(current_item)
                current_item = {}
            current_section = "output"
            continue

        # リストアイテム開始（indent=4, "- "）
        if indent == 4 and stripped.startswith("- ") and current_section:
            if current_item:
                io_contract[current_section].append(current_item)
            current_item = {}
            key_val = stripped[2:].strip()
            if ":" in key_val:
                k, _, v = key_val.partition(":")
                current_item[k.strip()] = v.strip().strip('"')
            continue

        # アイテム内フィールド（indent=6）
        if indent == 6 and ":" in stripped and current_section:
            k, _, v = stripped.partition(":")
            current_item[k.strip()] = v.strip().strip('"')

    if current_item and current_section:
        io_contract[current_section].append(current_item)

    return io_contract


# ──────────────────────────────────────────────
# パイプライン定義
# ──────────────────────────────────────────────

PIPELINES: dict[str, list[str]] = {
    "requirements-to-api": ["requirements-definer", "api-designer"],
    "api-to-frontend": ["api-designer", "react-frontend-coder"],
    "brainstorming-to-requirements": ["brainstorming", "requirements-definer"],
    "requirements-to-domain": ["requirements-definer", "domain-modeler"],
    "research-to-brainstorming": ["deep-research", "brainstorming"],
}

# 上流出力→下流入力の互換マッピング
FORMAT_COMPATIBILITY: dict[tuple[str, str], bool] = {
    ("json", "json"): True,
    ("yaml", "yaml"): True,
    ("markdown", "markdown"): True,
    ("markdown", "free-text"): True,
    ("free-text", "free-text"): True,
    ("json", "free-text"): True,
    ("yaml", "free-text"): True,
    ("file-reference", "file-reference"): True,
}


# ──────────────────────────────────────────────
# バリデーション関数
# ──────────────────────────────────────────────

def validate_io_contract_defined(skill_name: str) -> list[str]:
    """スキルに io_contract が定義されているか確認する。"""
    errors = []
    contract = parse_io_contract(skill_name)
    if not contract.get("input") and not contract.get("output"):
        errors.append(f"  [WARN] {skill_name}: meta.yaml に io_contract が未定義")
    elif not contract.get("output"):
        errors.append(f"  [WARN] {skill_name}: io_contract.output が未定義")
    return errors


def validate_pipeline_compatibility(pipeline_name: str, skills: list[str]) -> list[str]:
    """パイプライン内のスキル間 I/O 互換性を検証する。"""
    errors = []
    for i in range(len(skills) - 1):
        upstream = skills[i]
        downstream = skills[i + 1]

        up_contract = parse_io_contract(upstream)
        down_contract = parse_io_contract(downstream)

        if not up_contract.get("output"):
            errors.append(
                f"  [WARN] {pipeline_name}: {upstream} の output が未定義（互換性検証スキップ）"
            )
            continue

        if not down_contract.get("input"):
            errors.append(
                f"  [WARN] {pipeline_name}: {downstream} の input が未定義（互換性検証スキップ）"
            )
            continue

        required_inputs = [f for f in down_contract["input"] if f.get("required") == "true"]
        available_formats = {o["format"] for o in up_contract["output"] if "format" in o}

        for req in required_inputs:
            req_format = req.get("format", "")
            satisfied = any(
                FORMAT_COMPATIBILITY.get((avail, req_format), False)
                for avail in available_formats
            )
            if not satisfied and available_formats:
                errors.append(
                    f"  [WARN] {pipeline_name}: {upstream}→{downstream}: "
                    f"必須入力 '{req.get('name', '?')}' (format={req_format}) が "
                    f"上流出力 {available_formats} で満たせない可能性"
                )

    return errors


def validate_required_fields(skill_name: str) -> list[str]:
    """io_contract の各アイテムに必須フィールドが揃っているか確認する。"""
    errors = []
    contract = parse_io_contract(skill_name)
    required_keys = {"name", "format"}

    for direction in ("input", "output"):
        for item in contract.get(direction, []):
            missing = required_keys - set(item.keys())
            if missing:
                errors.append(
                    f"  [WARN] {skill_name}: io_contract.{direction} アイテムに "
                    f"必須キー {missing} が不足"
                )

    return errors


# ──────────────────────────────────────────────
# テストスイート
# ──────────────────────────────────────────────

class PipelineTestSuite:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.passed = 0
        self.warned = 0
        self.failed = 0

    def run_test(self, name: str, func, *args) -> None:
        issues = func(*args)
        warns = [i for i in issues if "[WARN]" in i]
        fails = [i for i in issues if "[FAIL]" in i]

        if fails:
            print(f"  FAIL  {name}")
            for f in fails:
                print(f"    {f}")
            self.failed += 1
        elif warns:
            print(f"  WARN  {name}")
            if self.verbose:
                for w in warns:
                    print(f"    {w}")
            self.warned += 1
        else:
            if self.verbose:
                print(f"  PASS  {name}")
            self.passed += 1

    def report(self) -> bool:
        total = self.passed + self.warned + self.failed
        print(f"\n{'─' * 50}")
        print(f"結果: {self.passed} PASS / {self.warned} WARN / {self.failed} FAIL  (合計 {total})")
        return self.failed == 0


def run_all_tests(pipeline_filter: str | None = None, verbose: bool = False) -> bool:
    suite = PipelineTestSuite(verbose=verbose)

    print("=== スキル間パイプライン契約テスト ===\n")

    pipeline_skills: set[str] = set()
    for skills in PIPELINES.values():
        pipeline_skills.update(skills)

    print("[ io_contract 定義チェック (meta.yaml) ]")
    for skill in sorted(pipeline_skills):
        suite.run_test(
            f"{skill}: io_contract 定義",
            validate_io_contract_defined,
            skill,
        )
        suite.run_test(
            f"{skill}: io_contract フィールド",
            validate_required_fields,
            skill,
        )

    print("\n[ パイプライン互換性チェック ]")
    for pipeline_name, skills in PIPELINES.items():
        if pipeline_filter and pipeline_filter not in pipeline_name:
            continue
        suite.run_test(
            f"pipeline: {pipeline_name}",
            validate_pipeline_compatibility,
            pipeline_name,
            skills,
        )

    return suite.report()


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="スキル間パイプライン契約テスト")
    parser.add_argument("--pipeline", help="特定パイプラインのみテスト（部分一致）")
    parser.add_argument("--verbose", "-v", action="store_true", help="PASS も表示")
    args = parser.parse_args()

    success = run_all_tests(pipeline_filter=args.pipeline, verbose=args.verbose)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
