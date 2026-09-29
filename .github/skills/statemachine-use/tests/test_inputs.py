"""入力の宣言（inputs）の契約テスト。

利用者が入れる値を workflow.yaml の `inputs:` に書き、エンジンは実行前に同じ宣言で
必須の欠けを断る。agent-app の入力ダイアログも同じ宣言を読むので、ここで固めるのは
正規化の形・壊れた宣言の検出・既定値と任意の扱い・照会口（next_state.py --inputs）。
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.engine import (  # noqa: E402
    StateMachineEngine, load_workflow, normalize_inputs, resolve_inputs, validate_workflow,
)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "next_state.py"

BODY = """
name: 月次レポート
initial_state: write
inputs:
  month:
    label: 対象月
    type: month
  format:
    label: 出力形式
    type: choice
    options: [md, html]
    default: md
  note:
    label: 補足
    required: false
states:
  write:
    action: "{{month}} を {{format}} で。補足: {{note}}"
    terminal: true
transitions: []
"""


def write_workflow(tmp_path: Path, body: str = BODY) -> Path:
    path = tmp_path / "workflow.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_declaration_is_normalized_in_order():
    items, errors = normalize_inputs({
        "month": {"label": "対象月", "type": "month"},
        "note": {"required": False},
    })
    assert errors == []
    assert items == [
        {"key": "month", "label": "対象月", "description": "", "required": True,
         "default": None, "type": "month", "options": []},
        {"key": "note", "label": "note", "description": "", "required": False,
         "default": None, "type": "text", "options": []},
    ]


def test_broken_declarations_are_reported():
    _, errors = normalize_inputs({
        "last_output": {},
        "kind": {"type": "choice"},
        "size": {"type": "huge", "requried": True},
        "flag": {"required": "yes"},
        "pick": {"type": "choice", "options": ["a"], "default": "b"},
    })
    text = "\n".join(errors)
    assert "last_output" in text
    assert "inputs.kind.options" in text
    assert "inputs.size.type" in text and "requried" in text
    assert "inputs.flag.required" in text
    assert "'b' が options にありません" in text


def test_validate_workflow_includes_input_errors(tmp_path):
    wf = load_workflow(write_workflow(tmp_path, BODY.replace("type: month", "type: calendar")))
    assert any("inputs.month.type" in e for e in validate_workflow(wf))


def test_defaults_fill_and_optional_becomes_empty():
    items, _ = normalize_inputs({
        "month": {}, "format": {"default": "md"}, "note": {"required": False},
    })
    values, missing = resolve_inputs(items, {"month": " 2026-09 "})
    assert values == {"month": "2026-09", "format": "md", "note": ""}
    assert missing == []
    _, missing = resolve_inputs(items, {"month": "  "})
    assert missing == ["month"]


def test_declared_input_reads_the_input_text():
    items, _ = normalize_inputs({"input": {"label": "本文"}})
    assert resolve_inputs(items, {}, "本文です") == ({"input": "本文です"}, [])
    assert resolve_inputs(items, {}, "")[1] == ["input"]


def test_engine_refuses_missing_required_before_calling_the_model(tmp_path):
    calls = []

    async def llm(prompt):
        calls.append(prompt)
        return "OK"

    engine = StateMachineEngine(llm_fn=llm)
    result = asyncio.run(engine.run(load_workflow(write_workflow(tmp_path)), context={}))
    assert not result.success
    assert "対象月（month）" in result.error
    assert calls == []

    result = asyncio.run(engine.run(load_workflow(write_workflow(tmp_path)), context={"month": "2026-09"}))
    assert result.success
    assert calls == ["2026-09 を md で。補足: "]


def test_next_state_reports_inputs_and_missing(tmp_path):
    path = write_workflow(tmp_path)
    proc = subprocess.run([sys.executable, str(SCRIPT), str(path), "--inputs"],
                          capture_output=True, text=True)
    assert proc.returncode == 4
    data = json.loads(proc.stdout)
    assert [item["key"] for item in data["inputs"]] == ["month", "format", "note"]
    assert data["missing"] == ["month"]

    proc = subprocess.run([sys.executable, str(SCRIPT), str(path), "--inputs",
                           "--context", json.dumps({"month": "2026-09"})],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["values"] == {"month": "2026-09", "format": "md", "note": ""}
