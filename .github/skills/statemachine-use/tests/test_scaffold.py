"""scaffold（骨組み生成）のテスト。

見るのは 3 点——生成した定義がそのまま engine の検証を通ること、
新スキーマの口（check / check_on_exhausted / write）がコメントとして骨組みに
含まれること、壊れた指定（重複 ID・不正 ID・既存上書き）を投入前に落とすこと。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.engine import load_workflow, validate_workflow  # noqa: E402
from scripts.scaffold import parse_state_arg, scaffold  # noqa: E402


def test_generated_workflow_passes_validation(tmp_path):
    target = scaffold("demo", ["implement:実装する", "review"], "complete:完了",
                      tmp_path, force=False)
    workflow = load_workflow(target / "workflow.yaml")
    assert validate_workflow(workflow) == []
    assert workflow.initial_state == "implement"
    assert set(workflow.states) == {"implement", "review", "complete"}
    assert workflow.states["complete"].terminal


def test_linear_chain_and_action_stubs(tmp_path):
    target = scaffold("demo", ["a", "b"], "done", tmp_path, force=False)
    workflow = load_workflow(target / "workflow.yaml")
    chain = [(t.from_state, t.to_state) for t in workflow.transitions]
    assert chain == [("a", "b"), ("b", "done")]
    # アクションスタブは非終端ステートの分だけ生成され、末尾の単一指示を含む
    for state_id in ("a", "b"):
        stub = (target / "actions" / f"{state_id}.md").read_text(encoding="utf-8")
        assert "次のステップは別途指示されます" in stub
    assert not (target / "actions" / "done.md").exists()


def test_new_schema_hooks_present_as_comments(tmp_path):
    target = scaffold("demo", ["impl"], "complete", tmp_path, force=False)
    text = (target / "workflow.yaml").read_text(encoding="utf-8")
    for hook in ("# check:", "# check_retries:", "# check_on_exhausted:", "# write:"):
        assert hook in text
    assert 'output_validator: "startswith:OK,FAILED"' in text


def test_state_arg_parsing():
    assert parse_state_arg("impl") == ("impl", "impl")
    assert parse_state_arg("impl:実装する") == ("impl", "実装する")
    with pytest.raises(ValueError):
        parse_state_arg("bad id:説明")
    with pytest.raises(ValueError):
        parse_state_arg("1starts_with_digit")


def test_duplicate_state_id_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="重複"):
        scaffold("demo", ["a", "a"], "complete", tmp_path, force=False)
    with pytest.raises(ValueError, match="重複"):
        scaffold("demo", ["a"], "a", tmp_path, force=False)


def test_existing_workflow_needs_force(tmp_path):
    scaffold("demo", ["a"], "complete", tmp_path, force=False)
    with pytest.raises(ValueError, match="--force"):
        scaffold("demo", ["a"], "complete", tmp_path, force=False)
    scaffold("demo", ["a", "b"], "complete", tmp_path, force=True)  # 上書きは通る
    assert (tmp_path / "demo" / "actions" / "b.md").exists()
