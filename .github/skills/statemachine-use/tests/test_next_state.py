"""next_state.py の引数契約テスト。

現行契約（--context / --auto-eval / --eval）と、旧ハーネス互換レイヤー
（--list-conditions / --evals / --last-output / --output KEY=VALUE）の両方が
同じ遷移を出すことを確認する。fork 先の旧ハーネスはまだ旧引数で呼ぶ。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "next_state.py"

WORKFLOW = """
name: triage
initial_state: classify
states:
  classify:
    output_key: kind
  bug:
    terminal: true
  feature:
    terminal: true
  ask:
    terminal: true
transitions:
  - from: classify
    to: bug
    condition_rule: "startswith:last_output:BUG"
    priority: 1
  - from: classify
    to: feature
    condition_rule: "startswith:kind:FEATURE"
    priority: 2
  - from: classify
    to: ask
    condition: "{{last_output}} が判断できない場合"
    priority: 3
"""


@pytest.fixture(scope="module")
def workflow(tmp_path_factory):
    path = tmp_path_factory.mktemp("statemachine") / "workflow.yaml"
    path.write_text(WORKFLOW, encoding="utf-8")
    return str(path)


def run(*args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── 現行契約: --context / --auto-eval / --eval ────────────────────────

def test_auto_eval_resolves_without_llm(workflow):
    listed = json.loads(run(workflow, "--state", "classify", "--auto-eval",
                            "--context", json.dumps({"last_output": "BUG in login"})))
    assert listed["resolved"] == "bug"
    assert listed["conditions"][0]["auto_result"] is True
    assert listed["conditions"][0]["needs_llm_eval"] is False


def test_auto_eval_uses_output_key_from_context(workflow):
    listed = json.loads(run(workflow, "--state", "classify", "--auto-eval",
                            "--context", json.dumps({"last_output": "FEATURE req",
                                                     "kind": "FEATURE req"})))
    assert listed["resolved"] == "feature"


def test_auto_eval_leaves_llm_condition_unresolved(workflow):
    listed = json.loads(run(workflow, "--state", "classify", "--auto-eval",
                            "--context", json.dumps({"last_output": "???",
                                                     "kind": "???"})))
    assert listed["resolved"] is None
    pending = [c for c in listed["conditions"] if c["needs_llm_eval"]]
    assert [c["to"] for c in pending] == ["ask"]


def test_auto_eval_renders_condition_template(workflow):
    listed = json.loads(run(workflow, "--state", "classify", "--auto-eval",
                            "--context", json.dumps({"last_output": "???",
                                                     "kind": "???"})))
    assert listed["conditions"][2]["condition"] == "??? が判断できない場合"


def test_eval_decides_with_context(workflow):
    assert run(workflow, "--state", "classify", "--eval", '{"2": true}',
               "--context", json.dumps({"last_output": "???", "kind": "???"})) == "ask"


def test_eval_is_overridden_by_condition_rule(workflow):
    assert run(workflow, "--state", "classify", "--eval", '{"2": true}',
               "--context", json.dumps({"last_output": "BUG"})) == "bug"


def test_rejects_non_object_context(workflow):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), workflow, "--state", "classify",
         "--auto-eval", "--context", '["last_output"]'],
        capture_output=True, text=True)
    assert result.returncode == 1
    assert "JSON オブジェクト" in result.stderr


# ── 無条件トランジション: auto_advance ────────────────────────────────

CHAIN = """
initial_state: fetch
states:
  fetch: {}
  parse: {}
  done:
    terminal: true
transitions:
  - from: fetch
    to: parse
  - from: parse
    to: done
    condition_rule: "startswith:last_output:OK"
    priority: 1
  - from: parse
    to: fetch
    priority: 2
"""


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    path = tmp_path_factory.mktemp("chain") / "workflow.yaml"
    path.write_text(CHAIN, encoding="utf-8")
    return str(path)


def test_auto_advance_omits_conditions(chain):
    listed = json.loads(run(chain, "--state", "fetch", "--auto-eval",
                            "--context", json.dumps({"last_output": "なんでもよい"})))
    assert listed["auto_advance"] is True
    assert listed["next_state"] == "parse"
    assert "conditions" not in listed


def test_auto_advance_state_decides_without_eval(chain):
    assert run(chain, "--state", "fetch", "--eval", "{}") == "parse"


def test_unconditional_fallback_needs_no_llm(chain):
    """後続の無条件トランジションは前段が偽のときのフォールバックとして解決する。"""
    listed = json.loads(run(chain, "--state", "parse", "--auto-eval",
                            "--context", json.dumps({"last_output": "RETRY"})))
    assert "auto_advance" not in listed
    assert listed["resolved"] == "fetch"
    assert [c["needs_llm_eval"] for c in listed["conditions"]] == [False, False]
    assert run(chain, "--state", "parse", "--eval", "{}",
               "--context", json.dumps({"last_output": "RETRY"})) == "fetch"


def test_earlier_rule_wins_over_unconditional_fallback(chain):
    listed = json.loads(run(chain, "--state", "parse", "--auto-eval",
                            "--context", json.dumps({"last_output": "OK"})))
    assert listed["resolved"] == "done"
    assert run(chain, "--state", "parse", "--eval", "{}",
               "--context", json.dumps({"last_output": "OK"})) == "done"


# ── 組み込み変数 today / now ──────────────────────────────────────────

DATED = """
initial_state: report
states:
  report: {}
  done:
    terminal: true
transitions:
  - from: report
    to: done
    condition: "{{today}} / {{now}} の日報として成立している"
"""


@pytest.fixture(scope="module")
def dated(tmp_path_factory):
    path = tmp_path_factory.mktemp("dated") / "workflow.yaml"
    path.write_text(DATED, encoding="utf-8")
    return str(path)


def test_today_and_now_expand_without_being_passed(dated):
    today = __import__("datetime").date.today().isoformat()
    listed = json.loads(run(dated, "--state", "report", "--auto-eval",
                            "--context", json.dumps({"last_output": "書いた"})))
    condition = listed["conditions"][0]["condition"]
    assert condition.startswith(f"{today} / {today}")
    assert "{{" not in condition


def test_context_can_override_today(dated):
    listed = json.loads(run(dated, "--state", "report", "--auto-eval",
                            "--context", json.dumps({"last_output": "書いた",
                                                     "today": "2000-01-01"})))
    assert listed["conditions"][0]["condition"].startswith("2000-01-01 / ")


# ── 旧ハーネス互換: --list-conditions / --evals / --last-output / --output ──

def test_legacy_list_conditions_still_resolves(workflow):
    listed = json.loads(run(workflow, "--state", "classify", "--list-conditions",
                            "--last-output", "BUG in login"))
    assert listed["resolved"] == "bug"


def test_legacy_output_pair_feeds_condition_rule(workflow):
    assert run(workflow, "--state", "classify", "--evals", "{}",
               "--last-output", "FEATURE req", "--output", "kind=FEATURE req") == "feature"


def test_legacy_evals_decide_llm_condition(workflow):
    assert run(workflow, "--state", "classify", "--evals", '{"2": true}',
               "--last-output", "???", "--output", "kind=???") == "ask"


# ── 統合規則: --context が基準、旧引数は不足値の補完 ──────────────────

def test_context_wins_over_legacy_last_output(workflow):
    assert run(workflow, "--state", "classify", "--eval", "{}",
               "--last-output", "BUG",
               "--context", json.dumps({"last_output": "FEATURE", "kind": "FEATURE"})
               ) == "feature"


def test_legacy_fills_key_missing_from_context(workflow):
    assert run(workflow, "--state", "classify", "--eval", "{}",
               "--output", "kind=FEATURE req",
               "--context", json.dumps({"last_output": "unclear"})) == "feature"
