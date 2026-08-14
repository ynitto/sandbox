"""決定的検査（check）の契約テスト。

遷移の材料を「モデルが書いたテキスト」から「ハーネスが測った終了コード」へ移す口。
見るのは 4 点——宣言の正規化、結果のコンテキスト契約、落ちたときの再投入と上限、
そして**壊れた宣言・検査の無いステートでの分岐を投入前に落とすこと**（検知の欠落は
最も気付けない事故なので、黙って素通りさせない）。
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.engine import (  # noqa: E402
    CHECK_CONTEXT_KEYS, StateMachineEngine, check_context, load_workflow,
    normalize_check, run_check, validate_workflow,
)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "next_state.py"


def write_workflow(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "workflow.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ── 宣言の正規化 ────────────────────────────────────────────

def test_string_check_is_split_without_a_shell():
    assert normalize_check("python3 -m pytest tests/ -q") == {
        "command": "python3", "args": ["-m", "pytest", "tests/", "-q"], "timeout_sec": 120}


def test_quoted_argument_survives_splitting():
    assert normalize_check("echo 'a b'")["args"] == ["a b"]


def test_list_and_mapping_forms_agree():
    listed = normalize_check(["pytest", "-q"])
    mapped = normalize_check({"command": "pytest", "args": ["-q"]})
    assert listed == mapped


def test_timeout_can_be_raised_and_is_floored():
    assert normalize_check({"command": "x", "timeout_sec": 300})["timeout_sec"] == 300
    assert normalize_check({"command": "x", "timeout_sec": 0})["timeout_sec"] == 1


def test_absent_declaration_is_none():
    assert normalize_check(None) is None
    assert normalize_check("") is None


@pytest.mark.parametrize("shell", ["pytest | tee log", "a && b", "cat x > y", "echo `id`"])
def test_shell_metacharacters_are_rejected_not_silently_literal(shell):
    # argv 直接実行では解釈されない。黙って別物を実行するより宣言の時点で落とす。
    with pytest.raises(ValueError, match="シェル記号"):
        normalize_check(shell)


def test_mapping_without_command_is_rejected():
    with pytest.raises(ValueError, match="command"):
        normalize_check({"args": ["-q"]})


# ── 結果のコンテキスト契約 ──────────────────────────────────

def test_success_context():
    assert check_context(0, "all good\n") == {
        "check_status": "0", "check_ok": "true", "check_output": "all good"}


def test_failure_keeps_status_and_first_diagnostic_line():
    ctx = check_context(1, "", "E   assert 1 == 2\nmore\n")
    assert ctx == {"check_status": "1", "check_ok": "false",
                   "check_output": "E   assert 1 == 2"}


def test_unrunnable_check_is_not_a_pass():
    # コマンドが無い・タイムアウトした = 「測れなかった」。0 でない側へ倒す。
    ctx = check_context(None, error="検査コマンドを実行できません")
    assert ctx["check_status"] == "error"
    assert ctx["check_ok"] == "false"


def test_context_keys_are_exactly_the_declared_contract():
    assert set(check_context(0)) == set(CHECK_CONTEXT_KEYS)


def test_run_check_reports_exit_status(tmp_path):
    assert run_check(normalize_check([sys.executable, "-c", "raise SystemExit(0)"]),
                     cwd=tmp_path)["ok"] is True
    failed = run_check(normalize_check([sys.executable, "-c", "raise SystemExit(3)"]),
                       cwd=tmp_path)
    assert failed["ok"] is False
    assert failed["context"]["check_status"] == "3"


def test_missing_command_does_not_raise(tmp_path):
    result = run_check(normalize_check(["definitely-not-a-real-command-xyz"]), cwd=tmp_path)
    assert result["ok"] is False
    assert result["context"]["check_status"] == "error"


# ── 検証（投入前に落とす） ──────────────────────────────────

BASE = """
name: gated
initial_state: work
states:
  work:
    action: "作業する"
    {extra}
  done:
    terminal: true
transitions:
  - from: work
    to: done
    condition_rule: "{rule}"
"""


def test_broken_declaration_fails_validation(tmp_path):
    wf = load_workflow(write_workflow(tmp_path, BASE.format(
        extra='check: "pytest | tee log"', rule="equals:check_ok:true")))
    assert any("check が不正です" in e for e in validate_workflow(wf))


def test_unknown_on_exhausted_fails_validation(tmp_path):
    wf = load_workflow(write_workflow(tmp_path, BASE.format(
        extra='check: "pytest"\n    check_on_exhausted: retry',
        rule="equals:check_ok:true")))
    assert any("check_on_exhausted" in e for e in validate_workflow(wf))


def test_branching_on_check_without_a_check_fails_validation(tmp_path):
    # ここを通すと、決定的に見ているつもりの遷移が黙って LLM 評価へ落ちる。
    wf = load_workflow(write_workflow(tmp_path, BASE.format(
        extra="", rule="equals:check_ok:true")))
    errors = validate_workflow(wf)
    assert any("check がありません" in e for e in errors), errors


def test_gated_workflow_validates_clean(tmp_path):
    wf = load_workflow(write_workflow(tmp_path, BASE.format(
        extra='check: "pytest -q"', rule="equals:check_ok:true")))
    assert validate_workflow(wf) == []


def test_check_retries_defaults_to_max_retries(tmp_path):
    wf = load_workflow(write_workflow(tmp_path, BASE.format(
        extra='check: "pytest"\n    max_retries: 2', rule="equals:check_ok:true")))
    assert wf.states["work"].check_retries == 2


def test_check_retries_can_be_set_apart_from_max_retries(tmp_path):
    wf = load_workflow(write_workflow(tmp_path, BASE.format(
        extra='check: "pytest"\n    max_retries: 2\n    check_retries: 0',
        rule="equals:check_ok:true")))
    assert wf.states["work"].check_retries == 0


# ── --state-check（外部ハーネス向けの照会口） ────────────────

def run_script(*args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args],
                            capture_output=True, text=True)
    return result


def test_state_check_returns_the_normalized_declaration(tmp_path):
    path = write_workflow(tmp_path, BASE.format(
        extra='check: "pytest -q"\n    check_retries: 2', rule="equals:check_ok:true"))
    result = run_script(str(path), "--state", "work", "--state-check")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["check"] == {"command": "pytest", "args": ["-q"], "timeout_sec": 120}
    assert payload["check_retries"] == 2
    assert payload["check_on_exhausted"] == "escalate"
    assert payload["check_feedback"] is True


def test_state_check_returns_null_for_ungated_state(tmp_path):
    path = write_workflow(tmp_path, BASE.format(extra="", rule="startswith:last_output:OK"))
    result = run_script(str(path), "--state", "work", "--state-check")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["check"] is None


def test_state_check_rejects_a_broken_declaration(tmp_path):
    path = write_workflow(tmp_path, BASE.format(
        extra='check: "pytest | tee log"', rule="equals:check_ok:true"))
    result = run_script(str(path), "--state", "work", "--state-check")

    assert result.returncode == 1
    assert "check" in result.stderr


def test_check_result_feeds_condition_rule_through_context(tmp_path):
    # ハーネスが測った値を --context で渡せば、遷移は LLM 抜きで確定する。
    path = write_workflow(tmp_path, BASE.format(
        extra='check: "pytest -q"', rule="equals:check_ok:true"))
    result = run_script(str(path), "--state", "work", "--auto-eval",
                        "--context", json.dumps(check_context(0, "ok")))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["resolved"] == "done"


def test_failing_check_does_not_resolve_the_success_route(tmp_path):
    path = write_workflow(tmp_path, BASE.format(
        extra='check: "pytest -q"', rule="equals:check_ok:true"))
    result = run_script(str(path), "--state", "work", "--auto-eval",
                        "--context", json.dumps(check_context(1, "", "failed")))

    assert json.loads(result.stdout)["resolved"] == "NONE"


# ── 実行（再投入と上限） ────────────────────────────────────

GATED = """
name: gated run
initial_state: work
config:
  max_steps: 4
states:
  work:
    action: "{marker} を作る"
    check: {check}
    check_retries: {retries}
    {extra}
  done:
    terminal: true
transitions:
  - from: work
    to: done
    condition_rule: "equals:check_ok:true"
"""


def build(tmp_path: Path, *, retries: int, passes_on: int, extra: str = "") -> Path:
    """`passes_on` 回目の action で初めて通る検査を持つワークフローを作る。"""
    counter = tmp_path / "attempts"
    script = tmp_path / "check.py"
    script.write_text(
        "import pathlib, sys\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        f"sys.exit(0 if n >= {passes_on} else 1)\n", encoding="utf-8")
    return write_workflow(tmp_path, GATED.format(
        marker="out.txt", retries=retries, extra=extra,
        check=json.dumps([sys.executable, str(script)])))


def drive(path: Path, tmp_path: Path):
    """action のたびに attempts を 1 増やす LLM スタブで走らせる。"""
    prompts: list[str] = []
    counter = tmp_path / "attempts"

    async def llm(prompt: str) -> str:
        prompts.append(prompt)
        counter.write_text(str(len(prompts)), encoding="utf-8")
        return "OK"

    result = asyncio.run(StateMachineEngine(llm_fn=llm).run(load_workflow(path)))
    return result, prompts


def test_passing_check_advances_without_extra_calls(tmp_path):
    result, prompts = drive(build(tmp_path, retries=2, passes_on=1), tmp_path)

    assert result.success, result.error
    assert result.final_state == "done"
    assert len(prompts) == 1, "通る課題にゲートは課金しない"


def test_failing_check_resubmits_the_same_state(tmp_path):
    result, prompts = drive(build(tmp_path, retries=2, passes_on=2), tmp_path)

    assert result.success, result.error
    assert len(prompts) == 2, "落ちた 1 回目のあと同じステートをやり直す"


def test_diagnostic_is_fed_back_into_the_retry(tmp_path):
    _result, prompts = drive(build(tmp_path, retries=2, passes_on=2), tmp_path)

    assert "check failed" in prompts[1]
    assert "Do not modify the check itself" in prompts[1]


def test_feedback_can_be_reduced_to_truth_only(tmp_path):
    _result, prompts = drive(
        build(tmp_path, retries=2, passes_on=2, extra="check_feedback: false"), tmp_path)

    assert "check failed" in prompts[1]
    assert "Check command:" not in prompts[1], "真偽だけで動かす選択肢を残す"


def test_exhausted_retries_escalate_rather_than_fail_generically(tmp_path):
    result, prompts = drive(build(tmp_path, retries=1, passes_on=99), tmp_path)

    assert result.success is False
    assert result.escalate is True, "上限到達は段を上げるシグナル（失敗一般と区別する）"
    assert len(prompts) == 2, "上限は宣言どおり（1 回やり直して打ち切る）"
    assert result.to_dict()["escalate"] is True


def test_continue_hands_the_failure_to_the_transitions(tmp_path):
    result, _prompts = drive(
        build(tmp_path, retries=0, passes_on=99, extra="check_on_exhausted: continue"),
        tmp_path)

    # 成功経路（equals:check_ok:true）は成立せず、他に経路が無いので遷移不一致で止まる。
    assert result.escalate is False
    assert "トランジション" in result.error or "on_no_transition" in result.error


def test_error_mode_fails_without_escalating(tmp_path):
    result, _prompts = drive(
        build(tmp_path, retries=0, passes_on=99, extra="check_on_exhausted: error"), tmp_path)

    assert result.success is False
    assert result.escalate is False


def test_model_output_cannot_satisfy_a_failing_check(tmp_path):
    """自己申告は遷移材料から外れている——「OK」と書いても検査が落ちれば進まない。"""
    path = build(tmp_path, retries=0, passes_on=99)

    async def llm(_prompt: str) -> str:
        return "OK\nPASS\n検査は通りました"

    result = asyncio.run(StateMachineEngine(llm_fn=llm).run(load_workflow(path)))

    assert result.success is False
    assert result.final_state == "work"
