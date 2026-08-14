"""migrate（既存定義の検査と新スキーマ追随）のテスト。

見るのは 4 点——(a)(b) を engine の検証結果として修正案付きで列挙すること、
(c)(d) の提案がコメントを保ったまま行挿入で適用でき冪等であること、
編集対象が一意に決まらないときは write を提案しないこと、
そして同梱 examples が migrate を素通りすること（round-trip 無差分）。
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.engine import load_workflow, validate_workflow  # noqa: E402
from scripts.migrate import (  # noqa: E402
    apply_proposals, collect_targets, lint_file, propose_edits, run,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def write_workflow(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "workflow.yaml"
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL_TAIL = """
  done:
    terminal: true
"""


# ── (a)(b) 検出は engine の検証が正典 ─────────────────────────

def test_check_branch_without_check_is_reported_with_suggestion(tmp_path):
    path = write_workflow(tmp_path, """
name: t
initial_state: impl
states:
  impl:
    action: do it
""" + MINIMAL_TAIL + """
transitions:
  - from: impl
    to: done
    condition_rule: "equals:check_ok:true"
""")
    issues = lint_file(path)
    assert any("check がありません" in error for error, _ in issues)
    assert any("修正案" in suggestion for _, suggestion in issues)


def test_shell_metacharacter_check_is_reported_with_suggestion(tmp_path):
    path = write_workflow(tmp_path, """
name: t
initial_state: impl
states:
  impl:
    action: do it
    check: "pytest -q | tee log"
""" + MINIMAL_TAIL + """
transitions:
  - from: impl
    to: done
""")
    issues = lint_file(path)
    assert any("シェル記号" in error for error, _ in issues)
    assert any("スクリプト" in suggestion for _, suggestion in issues)


# ── (c) write 割付の提案 ────────────────────────────────────

CHECKED_BODY = """
name: t
initial_state: impl
states:
  impl:
    # 編集ステート
    action: |
      `src/app.py` を仕様どおりに実装してください。
    check: "python3 -m pytest tests/test_app.py -q"
    check_on_exhausted: escalate
""" + MINIMAL_TAIL + """
transitions:
  - from: impl
    to: done
    condition_rule: "equals:check_ok:true"
"""


def test_unique_edit_target_proposes_write(tmp_path):
    path = write_workflow(tmp_path, CHECKED_BODY)
    proposals = propose_edits(path, path.read_text(encoding="utf-8"))
    assert [(p.kind, p.state_id) for p in proposals] == [("write", "impl")]
    new_text = apply_proposals(path.read_text(encoding="utf-8"), proposals)
    path.write_text(new_text, encoding="utf-8")
    # 挿入後も engine で読めて、write が宣言として入り、コメントは残る
    assert validate_workflow(load_workflow(path)) == []
    assert yaml.safe_load(new_text)["states"]["impl"]["write"] == "src/app.py"
    assert "# 編集ステート" in new_text
    # 冪等: もう一度回しても提案は出ない
    assert propose_edits(path, new_text) == []


def test_ambiguous_or_check_side_paths_do_not_propose_write(tmp_path):
    # 候補 2 つ（.md 以外が複数）→ 一意に決まらないので提案しない
    path = write_workflow(tmp_path, CHECKED_BODY.replace(
        "`src/app.py` を仕様どおりに実装してください。",
        "`src/app.py` と `src/util.py` を実装してください。"))
    assert propose_edits(path, path.read_text(encoding="utf-8")) == []
    # check の argv に載っているパスは候補にしない
    sub = tmp_path / "sub"
    sub.mkdir()
    path2 = write_workflow(sub, CHECKED_BODY.replace(
        "`src/app.py` を仕様どおりに実装してください。",
        "`tests/test_app.py` が通るようにしてください。"))
    assert propose_edits(path2, path2.read_text(encoding="utf-8")) == []


def test_spec_reference_md_is_not_the_edit_target(tmp_path):
    # 仕様参照（.md）と実装対象が並んでいたら .md を除外して一意に決める
    path = write_workflow(tmp_path, CHECKED_BODY.replace(
        "`src/app.py` を仕様どおりに実装してください。",
        "`src/app.py` を実装する。仕様は `docs/spec.md` にある。"))
    proposals = propose_edits(path, path.read_text(encoding="utf-8"))
    assert [p.line.strip() for p in proposals] == ["write: src/app.py"]


# ── (d) check_on_exhausted の明示化 ─────────────────────────

def test_implicit_check_on_exhausted_is_made_explicit(tmp_path):
    body = CHECKED_BODY.replace("    check_on_exhausted: escalate\n", "")
    path = write_workflow(tmp_path, body)
    proposals = propose_edits(path, path.read_text(encoding="utf-8"))
    kinds = {p.kind for p in proposals}
    assert "check_on_exhausted" in kinds
    new_text = apply_proposals(path.read_text(encoding="utf-8"), proposals)
    path.write_text(new_text, encoding="utf-8")
    assert yaml.safe_load(new_text)["states"]["impl"]["check_on_exhausted"] == "escalate"
    assert validate_workflow(load_workflow(path)) == []


def test_block_sequence_check_gets_insertion_after_continuation(tmp_path):
    path = write_workflow(tmp_path, """
name: t
initial_state: impl
states:
  impl:
    action: do it
    check:
      - python3
      - -m
      - pytest
""" + MINIMAL_TAIL + """
transitions:
  - from: impl
    to: done
""")
    proposals = propose_edits(path, path.read_text(encoding="utf-8"))
    new_text = apply_proposals(path.read_text(encoding="utf-8"), proposals)
    path.write_text(new_text, encoding="utf-8")
    raw = yaml.safe_load(new_text)["states"]["impl"]
    assert raw["check"] == ["python3", "-m", "pytest"]  # 継続行を壊していない
    assert raw["check_on_exhausted"] == "escalate"


# ── 対象収集と CLI ──────────────────────────────────────────

def test_collect_targets_skips_non_workflow_yaml(tmp_path):
    write_workflow(tmp_path, CHECKED_BODY)
    (tmp_path / "unrelated.yaml").write_text("just: data", encoding="utf-8")
    targets = collect_targets([str(tmp_path)])
    assert [p.name for p in targets] == ["workflow.yaml"]


def test_dry_run_does_not_modify_and_apply_does(tmp_path):
    body = CHECKED_BODY.replace("    check_on_exhausted: escalate\n", "")
    path = write_workflow(tmp_path, body)
    assert run([str(path)], apply=False) == 1        # 未適用の提案あり
    assert path.read_text(encoding="utf-8") == body  # dry-run は書かない
    assert run([str(path)], apply=True) == 0
    assert "check_on_exhausted: escalate" in path.read_text(encoding="utf-8")
    assert run([str(path)], apply=False) == 0        # 適用後はきれい


# ── round-trip: 同梱 examples は素通り（無差分） ─────────────

def test_examples_round_trip_with_no_changes():
    examples = sorted(EXAMPLES.glob("*.yaml"))
    assert len(examples) >= 4
    for example in examples:
        text = example.read_text(encoding="utf-8")
        assert lint_file(example) == [], example.name
        assert propose_edits(example, text) == [], example.name
