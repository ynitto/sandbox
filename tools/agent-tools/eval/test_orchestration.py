from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import orchestration_eval as oe
from orchestration_report import summarize


@pytest.fixture
def config():
    data = json.loads((oe.HERE / "orchestration.example.json").read_text())
    data["baseline"].update(model="solver", family="family-a")
    data["starter"].update(model="cheap", family="family-a")
    data["critic"].update(model="reviewer", family="family-b")
    return data


class FakeRuntime:
    def __init__(self, config, output, draft_status="ok", critic_text=None):
        self.config, self.output = config, output
        self.calls, self.grades = [], []
        self.draft_status = draft_status
        self.critic_text = critic_text or '{"findings":[]}'

    def solve(self, role, prompt, root, timeout, leg_dir):
        self.calls.append((role, prompt))
        (root / "eval/humansize.py").write_text("value = " + str(len(self.calls)))
        return dict(status=self.draft_status if len(self.calls) == 1 else "ok",
                    text="done", cost_usd=1, wall=.01)

    def critique(self, packet, root, timeout, leg_dir):
        self.calls.append(("critic", packet))
        return dict(status="ok", text=self.critic_text, cost_usd=.2, wall=.01)

    def grade(self, task, root, timeout):
        self.grades.append((len(self.calls), root))
        return dict(passed=True, diagnostic="private-secret", status="ok")


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "eval").mkdir()
    (root / "eval/humansize.py").write_text("value = 0")
    oe.git("init", "-q", cwd=root)
    oe.git("add", ".", cwd=root)
    oe.git("-c", "user.name=Test", "-c", "user.email=test@invalid", "-c", "commit.gpgsign=false",
           "commit", "-qm", "fixture", cwd=root)
    return root


def run(config, tmp_path, checkout, arm, **kwargs):
    output = tmp_path / "results"
    output.mkdir()
    runtime = FakeRuntime(config, output, **kwargs)
    row = oe.run_arm("T1", 1, arm, checkout, oe.snapshot(checkout), runtime)
    return row, runtime


@pytest.mark.parametrize("gate_pass,expected_calls", [(True, 1), (False, 2)])
def test_cascade_bounded(config, tmp_path, checkout, monkeypatch, gate_pass, expected_calls):
    monkeypatch.setattr(oe, "public_gate", lambda *a: (gate_pass, "public-diagnostic", "ok" if gate_pass else "execution_error"))
    row, runtime = run(config, tmp_path, checkout, "cascade")
    assert len(runtime.calls) == expected_calls
    assert row["escalated"] is (not gate_pass)
    if not gate_pass:
        assert "public-diagnostic" in runtime.calls[1][1]
    assert row["passed"]
    assert row["cost_usd"] == expected_calls


@pytest.mark.parametrize("status", ["quota_error", "auth_error", "timeout", "execution_error"])
def test_no_escalation_on_runtime_failure(config, tmp_path, checkout, status):
    row, runtime = run(config, tmp_path, checkout, "cascade", draft_status=status)
    assert len(runtime.calls) == 1
    assert not row["escalated"] and not row["passed"]
    assert row["cost_usd"] == 1


def test_critique_exactly_one_revision_no_grade_leak(config, tmp_path, checkout):
    finding = {"id": "F1", "path": "eval/humansize.py", "evidence": "bad unit", "suggestion": "fix unit"}
    row, runtime = run(config, tmp_path, checkout, "critique", critic_text=json.dumps({"findings": [finding]}))
    assert [r[0] for r in runtime.calls] == ["baseline", "critic", "baseline"]
    assert all(n == 3 for n, _ in runtime.grades)
    assert not any("private-secret" in prompt for _, prompt in runtime.calls)
    assert row["findings"][0]["adoption_confirmed"] is None
    assert row["cost_usd"] == 2.2
    assert row["draft_passed"] and row["passed"]
    assert "value = 1" in runtime.calls[1][1]


def test_invalid_critic_still_accounted(config, tmp_path, checkout):
    row, runtime = run(config, tmp_path, checkout, "critique", critic_text="not json")
    assert len(runtime.calls) == 2
    assert not row["passed"] and row["cost_usd"] == 1.2


def test_single(config, tmp_path, checkout):
    row, runtime = run(config, tmp_path, checkout, "single")
    assert len(runtime.calls) == 1 and len(runtime.grades) == 1
    assert row["passed"] and row["cost_usd"] == 1


def test_scope_and_symlink_rejected(checkout):
    before = oe.snapshot(checkout)
    (checkout / "eval/humansize.py").unlink()
    (checkout / "eval/humansize.py").symlink_to("/tmp/outside")
    assert not oe.scope_ok("T1", before, checkout)[0]


def test_missing_cost_does_not_become_zero():
    rows = [dict(task="T1", repeat=1, arm="single", passed=True, status="ok", wall=1, cost_usd=None),
            dict(task="T2", repeat=1, arm="single", passed=False, status="timeout", wall=2, cost_usd=2)]
    report = summarize(rows)["arms"]["single"]
    assert report["pass_rate"] == .5
    assert report["cost_usd"] is None and report["cost_per_pass"] is None
    assert report["unmeasured_runs"] == 1


def test_cost_per_pass_includes_failure_cost():
    rows = [dict(task="T1", repeat=1, arm="single", passed=True, status="ok", wall=1, cost_usd=1),
            dict(task="T2", repeat=1, arm="single", passed=False, status="timeout", wall=2, cost_usd=3)]
    assert summarize(rows)["arms"]["single"]["cost_per_pass"] == 4
    rows[0]["passed"] = False
    assert summarize(rows)["arms"]["single"]["cost_per_pass"] == "infinity"


def test_task_cluster_counts_repeats_once():
    rows = [dict(task=task, repeat=i, arm=arm, passed=arm == "cascade", status="ok", wall=1, cost_usd=1)
            for task in ("T1", "T2") for i in range(1, 4) for arm in ("single", "cascade")]
    pair = summarize(rows)["paired_vs_single"]["cascade"]
    assert pair["paired_tasks"] == 2
    assert pair["task_bootstrap_95"] == [1, 1]
    assert pair["decision"] == "insufficient"


@pytest.mark.parametrize("key,value", [("wall_seconds", float("nan")), ("repeat", 0), ("tasks", ["T1", "T1"])])
def test_manifest_rejects_bad_limits(config, key, value):
    config[key] = value
    with pytest.raises(ValueError):
        oe.validate(config)


def test_family_must_differ(config):
    config["critic"]["family"] = config["baseline"]["family"]
    with pytest.raises(ValueError, match="different"):
        oe.validate(config)


def test_claude_usage():
    cost, text, usage = oe.parse_claude(json.dumps({"type": "result", "total_cost_usd": .5,
                                                  "result": "answer", "session_id": "s1"}))
    assert cost == .5 and text == "answer" and usage["session_id"] == "s1"
    assert oe.parse_claude("answer")[0] is None


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX groups")
def test_timeout_kills_grandchild(tmp_path):
    marker = tmp_path / "leaked"
    child = f"import time; from pathlib import Path; time.sleep(.7); Path({str(marker)!r}).touch()"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    result = oe.run_process([sys.executable, "-c", parent], tmp_path, .2)
    assert result["status"] == "timeout" and result["wall"] < 2
    time.sleep(.8)
    assert not marker.exists()


def test_critic_payload_has_no_tools(config, tmp_path, monkeypatch, capsys):
    config["critic"] = {**json.loads((oe.HERE / "orchestration.api.example.json").read_text())["critic"],
                        "model": "reviewer", "family": "family-b"}
    captured = {}
    class Response:
        def __enter__(self):
            import io
            return io.StringIO(json.dumps({"choices": [{"message": {"content": '{"findings":[]}'}}],
                                          "model": "reviewer", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}))
        def __exit__(self, *a):
            pass
    def open_request(req, timeout):
        captured.update(json.loads(req.data))
        return Response()
    monkeypatch.setattr(oe.urllib.request, "urlopen", open_request)
    monkeypatch.setenv("ORCHESTRATION_CRITIC_API_KEY", "test-not-real")
    path = tmp_path / "request.json"
    oe.write_json(path, {"binding": config["critic"], "packet": "candidate", "timeout": 1})
    oe.critic_worker(path)
    assert "tools" not in captured and "tool_choice" not in captured
    assert captured["model"] == "reviewer"
    result = json.loads(capsys.readouterr().out)
    assert result["cost_usd"] is None


def test_dry_run_no_cli_or_network(config, tmp_path):
    path = tmp_path / "config.json"
    oe.write_json(path, config)
    result = subprocess.run([sys.executable, str(oe.HERE / "orchestration_eval.py"), "--manifest", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    frozen = json.loads(result.stdout)
    assert len(frozen["schedule"]) == 9
    assert len(frozen["commit"]) == 40
    assert frozen["concurrency"] == 1


def test_prepare_excludes_evaluation_and_uses_fresh_git(tmp_path):
    import io
    import tarfile
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for path in ("README.md", "tools/agent-tools/eval/secret.txt"):
            data = b"fixture"
            member = tarfile.TarInfo(path)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    root = tmp_path / "fixture"
    before = oe.prepare(root, archive.getvalue(), "T2")
    assert not (root / "tools/agent-tools/eval").exists()
    assert "eval/billing.py" in before
    assert oe.git("rev-list", "--count", "HEAD", cwd=root).strip() == b"1"


def test_real_private_checker_rejects_then_accepts(config, tmp_path):
    import worker_eval
    root = tmp_path / "fixture"
    root.mkdir()
    worker_eval.seed_t2(root)
    runtime = oe.Runtime(config, {}, tmp_path)
    assert not runtime.grade("T2", root, 10)["passed"]
    (root / "eval/billing.py").write_text(
        "def prorate(monthly_fee, days_used, days_in_month):\n"
        "    return (monthly_fee * days_used + days_in_month - 1) // days_in_month\n"
        "def invoice_total(items, discount_pct=0):\n"
        "    total = sum(i['unit_price'] * i['qty'] for i in items)\n"
        "    return total - total * discount_pct // 100\n")
    assert runtime.grade("T2", root, 10)["passed"]


def test_cancelled_revision_is_unknown_cost(config, tmp_path, checkout, monkeypatch):
    output = tmp_path / "results"
    output.mkdir()
    runtime = FakeRuntime(config, output)
    original = runtime.solve
    def solve(*args):
        if runtime.calls:
            raise KeyboardInterrupt
        return original(*args)
    monkeypatch.setattr(runtime, "solve", solve)
    row = oe.run_arm("T1", 1, "critique", checkout, oe.snapshot(checkout), runtime)
    assert row["status"] == "cancelled" and not row["passed"]
    assert row["cost_usd"] is None
    assert row["legs"][-1]["kind"] == "revision"


def test_adoption_needs_revision_evidence():
    from orchestration_report import apply_adoptions
    rows = [dict(run_id="r", findings=[{"id": "F1", "path": "a.py"}],
                 changed_paths=["a.py"], revision_changed_paths=[])]
    decisions = [dict(run_id="r", finding_id="F1", adopted=True, evidence="checked diff")]
    with pytest.raises(ValueError):
        apply_adoptions(rows, decisions)
    rows[0]["revision_changed_paths"] = ["a.py"]
    apply_adoptions(rows, decisions)
    assert rows[0]["findings"][0]["adoption_confirmed"] is True


def test_cli_critic_uses_no_api_and_no_candidate_access(config, tmp_path, monkeypatch):
    spec = oe.agentcli.load_cli("claude", oe.REPO)
    spec = {**spec, "write_args": ["--dangerously-skip-permissions"],
            "command_suffix": ["--resume", "old-session"]}
    runtime = oe.Runtime(config, {"critic": spec}, tmp_path)
    captured = {}
    def process(argv, cwd, timeout, stdin=None, env=None):
        captured.update(argv=argv, cwd=cwd, stdin=stdin)
        assert list(cwd.iterdir()) == []
        return {"status": "ok", "stdout": json.dumps({"type": "result", "result": '{"findings":[]}',
                                                        "total_cost_usd": .01}), "stderr": "", "wall": .01}
    monkeypatch.setattr(oe, "run_process", process)
    monkeypatch.delenv("ORCHESTRATION_CRITIC_API_KEY", raising=False)
    monkeypatch.setattr(oe.urllib.request, "urlopen", lambda *a, **k: pytest.fail("API must not be called"))
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    result = runtime.critique("candidate-packet", candidate, 10, tmp_path)
    argv = captured["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--safe-mode" in argv and "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert "--dangerously-skip-permissions" not in argv and "--resume" not in argv
    assert captured["cwd"] != candidate and "candidate-packet" in captured["stdin"]
    assert result["cost_usd"] == .01 and result["transport"] == "cli"
    assert result["text"] == '{"findings":[]}'


def test_transport_validation_and_legacy_api(config):
    assert oe.validate(config) == config
    config["critic"]["cli"] = "copilot"
    with pytest.raises(ValueError, match="claude only"):
        oe.validate(config)
    config["critic"] = {**json.loads((oe.HERE / "orchestration.api.example.json").read_text())["critic"],
                        "model": "reviewer", "family": "family-b"}
    assert oe.critic_transport(config["critic"]) == "api"
    assert oe.validate(config) == config


def test_cli_execute_preflight_without_api_key(config, tmp_path, monkeypatch, capsys):
    path = tmp_path / "manifest.json"
    oe.write_json(path, config)
    monkeypatch.setattr(sys, "argv", ["orchestration_eval", "--manifest", str(path), "--execute",
                                     "--output", str(tmp_path / "output")])
    monkeypatch.delenv("ORCHESTRATION_CRITIC_API_KEY", raising=False)
    monkeypatch.setattr(oe.shutil, "which", lambda x: x)
    monkeypatch.setattr(oe, "run_process", lambda *a, **k: {"status": "ok", "stdout": " ".join(oe.CLAUDE_CRITIC_FLAGS)})
    monkeypatch.setattr(oe, "prepare", lambda *a: {})
    def arm(**kwargs):
        return {"run_id": f"{kwargs['task']}-{kwargs['repeat']}-{kwargs['arm']}",
                "task": kwargs["task"], "repeat": kwargs["repeat"], "arm": kwargs["arm"],
                "status": "ok", "passed": True, "wall": 1, "cost_usd": None}
    monkeypatch.setattr(oe, "run_arm", arm)
    oe.main()
    completion = next((tmp_path / "output").glob("*/completion.json"))
    assert json.loads(completion.read_text())["completed"] == 9
