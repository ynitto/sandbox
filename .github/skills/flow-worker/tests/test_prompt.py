"""flow-worker prompt.py のテスト。

出力契約（agent-flow のパーサが前提とする文言）が壊れていないことを最優先で守る。
実行: python -m pytest .github/skills/flow-worker/tests/ -q
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "prompt.py")

spec = importlib.util.spec_from_file_location("fw_prompt", SCRIPT)
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)


def test_worker_prompt_contains_goal_and_discipline():
    p = fw.build({"role": "worker", "kind": "work", "goal": "READMEに節を追加"})
    assert "READMEに節を追加" in p
    assert "タスク(work)" in p
    assert "三つの約束" in p
    assert "範囲を守る" in p
    assert "報告契約" in p


def test_worker_prompt_includes_interface_info():
    p = fw.build({
        "role": "worker", "kind": "work", "goal": "g",
        "request": "全体要求テキスト",
        "repo_instruction": "【ワークスペース】/tmp/clone で作業",
        "artifact_note": "【中間成果物プロトコル】出力先 /tmp/art",
        "deps": {"t1": {"output": "依存成果A", "data": {"k": 1}}},
    })
    assert "全体要求テキスト" in p
    assert "【ワークスペース】/tmp/clone で作業" in p
    assert "【中間成果物プロトコル】出力先 /tmp/art" in p
    assert "[t1] 依存成果A" in p
    assert '"k": 1' in p


def test_verify_prompt_keeps_output_contract():
    p = fw.build({"role": "worker", "kind": "verify", "goal": "検証する"})
    # agent-flow の _normalize_verify が前提とする文言
    assert "verify=pass" in p
    assert "verify=fail" in p
    assert '{"ok": true|false, "issues": ["..."]}' in p
    assert "再導出" in p
    assert "(minor)" in p


def test_split_prompt_keeps_array_contract():
    p = fw.build({"role": "worker", "kind": "split", "goal": "分割"})
    assert "JSON 配列のみ" in p
    assert "説明文は付けず配列だけを返す" in p


def test_classify_and_reduce_contracts():
    p = fw.build({"role": "worker", "kind": "classify", "goal": "分類"})
    assert "class=<ラベル>" in p
    p = fw.build({"role": "worker", "kind": "reduce", "goal": "集約"})
    assert "count" in p


def test_judge_and_filter_structured_tail():
    p = fw.build({"role": "worker", "kind": "judge", "goal": "選ぶ"})
    assert '"winner"' in p
    p = fw.build({"role": "worker", "kind": "filter", "goal": "選別"})
    assert '"kept"' in p


def test_map_prompt_keeps_single_item_rule():
    p = fw.build({"role": "worker", "kind": "map", "goal": "各要素を処理"})
    assert "与えられた1要素だけに適用" in p


def test_unknown_kind_falls_back_to_work():
    p = fw.build({"role": "worker", "kind": "mystery", "goal": "g"})
    assert "ワーカー" in p


def test_git_rules_injected_for_exec_and_verify():
    for kind in ("work", "generate", "map", "verify"):
        p = fw.build({"role": "worker", "kind": kind, "goal": "g"})
        assert "git 利用規約" in p, kind
        assert "git_worktree.py" in p, kind        # スクリプトの絶対パスが注入される
        assert "provision" in p and "release" in p, kind


def test_git_rules_not_injected_for_aggregation_kinds():
    for kind in ("classify", "synthesize", "filter", "judge", "reduce", "split"):
        p = fw.build({"role": "worker", "kind": kind, "goal": "g"})
        assert "git 利用規約" not in p, kind


def test_evaluator_prompt_keeps_decision_contract():
    p = fw.build({"role": "evaluator", "request": "req", "results_summary": "- t1: done",
                  "max_retries": 5, "patterns_catalog": "- fan-out: ..."})
    assert '"decision":"done"|"replan"' in p
    assert '"new_tasks"' in p
    assert '"replaces"' in p
    assert "最大 5 回" in p
    assert "- fan-out: ..." in p
    assert "- t1: done" in p
    assert "受け入れ" in p
    assert "膨張禁止" in p


def test_evaluator_prompt_includes_human_feedback():
    p = fw.build({"role": "evaluator", "request": "req", "results_summary": "s",
                  "human_feedback": "APIはv2を使うこと"})
    assert "APIはv2を使うこと" in p
    assert "最優先" in p


def test_request_is_trimmed():
    p = fw.build({"role": "worker", "kind": "work", "goal": "g", "request": "あ" * 1000})
    assert "あ" * 400 + "…" in p
    assert "あ" * 401 not in p


def test_worker_prompt_prepends_global_instructions():
    block = "<!-- agent-instructions rev:4 -->\n## 共通指示（agent-dashboard 管理・全ノード共通）\n回答は日本語。"
    p = fw.build({"role": "worker", "kind": "work", "goal": "g", "instructions": block})
    # ブロックは先頭に置かれ、タスク本文より前に来る
    assert p.startswith(block)
    assert p.index(block) < p.index("タスク(work)")
    # 最弱の層であることを明示する文言
    assert "個別タスクの指示" in p and "それらを優先" in p


def test_worker_prompt_without_instructions_unchanged():
    p = fw.build({"role": "worker", "kind": "work", "goal": "g"})
    assert "agent-instructions" not in p
    assert p.startswith("あなたは分散 Dynamic Workflow")


def test_cli_stdin_roundtrip():
    payload = {"role": "worker", "kind": "work", "goal": "CLIテスト"}
    proc = subprocess.run([sys.executable, SCRIPT], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert "CLIテスト" in proc.stdout


def test_cli_bad_input_fails_nonzero():
    proc = subprocess.run([sys.executable, SCRIPT], input="not json",
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1


def test_deterministic():
    payload = {"role": "worker", "kind": "verify", "goal": "g",
               "deps": {"a": {"output": "x", "data": [1, 2]}}}
    assert fw.build(payload) == fw.build(payload)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
