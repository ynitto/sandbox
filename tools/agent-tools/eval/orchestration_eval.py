#!/usr/bin/env python3
"""Fixed Single/Cascade/Critique benchmark; dry-run unless --execute is supplied.

The evaluator and private probes stay outside each candidate checkout. CLI
permissions still apply: these checkouts are experiment isolation, not an OS sandbox.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
from agentcore import agentcli  # noqa: E402
from eval_io import new_run_dir, write_json  # noqa: E402
from orchestration_report import ARMS, summarize  # noqa: E402

SCOPES = {"T1": ["eval/humansize.py", "eval/test_*.py"],
          "T2": ["eval/billing.py"],
          "T3": ["schemas/node-budget-summary.schema.json", "tools/agent-project/**/test_*.py"]}
IGNORED = {".git", "__pycache__", ".pytest_cache", ".cache", ".venv"}
REVIEW_INSTRUCTION = ('Review the supplied candidate only. Return only a JSON object '
                      '{"findings":[{"id":"F1","path":"relative/path","evidence":"...",'
                      '"suggestion":"..."}]}. Report only concrete defects. '
                      'No tools or repository access are available. Do not use markdown fences.')
CLAUDE_CRITIC_FLAGS = ["--safe-mode", "--tools", "", "--strict-mcp-config",
                       "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence"]


def critic_transport(binding):
    return binding.get("transport", "cli" if binding.get("cli") else "api")


def critic_spec(spec):
    # Rebuild the invocation: user-defined write/resume/fallback flags must not leak into review.
    if spec["name"] != "claude":
        raise ValueError("CLI critic currently supports claude only (tool-less safe mode)")
    return {**spec, "command": [spec["command"][0], "-p", "--output-format", "json"],
            "command_suffix": [], "write_args": [], "readonly_args": list(CLAUDE_CRITIC_FLAGS),
            "model_flag": "--model", "prompt_via": "stdin", "prompt_flag": "",
            "env": {}, "output": "stdout"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def frozen_spec(spec):
    # Normalized CLI specs include compiled regexes; environment values may be secrets.
    keys = ("name", "command", "command_suffix", "model_flag", "write_args", "readonly_args",
            "prompt_via", "prompt_flag", "output", "timeout", "default_model", "headless_autonomy")
    return {**{k: spec.get(k) for k in keys}, "environment_keys": sorted(spec.get("env", {})),
            "definition_sha256": digest(json.dumps(spec, sort_keys=True, default=str).encode())}


def git(*args, cwd=REPO):
    return subprocess.check_output(["git", *args], cwd=cwd)


def finite_number(value, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def validate(config):
    if config.get("version") != 1:
        raise ValueError("manifest version must be 1")
    tasks = config.get("tasks", [])
    if not tasks or len(set(tasks)) != len(tasks) or any(t not in SCOPES for t in tasks):
        raise ValueError("tasks must be unique IDs from T1,T2,T3")
    if type(config.get("repeat")) is not int or config["repeat"] < 1:
        raise ValueError("repeat must be a positive integer")
    if type(config.get("seed")) is not int:
        raise ValueError("seed must be an integer")
    for name in ("wall_seconds", "verify_seconds", "cascade_draft_seconds",
                 "critique_draft_seconds", "critic_seconds"):
        if not finite_number(config.get(name), positive=True):
            raise ValueError(f"{name} must be positive and finite")
    if config["verify_seconds"] >= config["wall_seconds"]:
        raise ValueError("verification must fit inside wall_seconds")
    for role in ("baseline", "starter", "critic"):
        binding = config.get(role, {})
        for key in ("model", "family"):
            if not isinstance(binding.get(key), str) or not binding[key].strip() or binding[key].startswith("REPLACE"):
                raise ValueError(f"{role}.{key} must be explicitly pinned")
        if role != "critic" and not binding.get("cli"):
            raise ValueError(f"{role}.cli is required")
    if config["baseline"]["family"].lower() == config["critic"]["family"].lower():
        raise ValueError("critic must use a different model family")
    critic = config["critic"]
    transport = critic_transport(critic)
    if transport not in ("cli", "api"):
        raise ValueError("critic.transport must be cli or api")
    if transport == "cli":
        if critic.get("cli") != "claude":
            raise ValueError("CLI critic currently supports claude only")
        return config
    if not critic.get("endpoint", "").startswith("https://"):
        raise ValueError("critic.endpoint must be an HTTPS chat/completions endpoint")
    if not critic.get("api_key_env"):
        raise ValueError("critic.api_key_env is required; never store keys in the manifest")
    if type(critic.get("max_tokens")) is not int or critic["max_tokens"] < 1:
        raise ValueError("critic.max_tokens must be positive")
    prices = critic.get("prices_per_million")
    if prices is not None and (set(prices) != {"input", "cached_input", "output"}
                               or not all(finite_number(x) for x in prices.values())):
        raise ValueError("critic prices require nonnegative input,cached_input,output rates")
    return config


def run_process(argv, cwd, timeout, stdin=None, env=None):
    """Kill the whole POSIX group even if its leader exits before its children.

    No retry. Captured output survives a timeout. Non-POSIX runs are rejected at preflight.
    """
    started = time.monotonic()
    if timeout <= 0:
        return {"status": "timeout", "stdout": "", "stderr": "", "wall": 0, "returncode": None}
    proc = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    status = "ok"
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        status = "timeout"
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate(timeout=5)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise
    finally:
        # A CLI may leave children behind after returning successfully.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if status == "ok" and proc.returncode:
        status = "execution_error"
    return {"status": status, "stdout": out, "stderr": err,
            "returncode": proc.returncode, "wall": time.monotonic() - started}


def snapshot(root):
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED]
        for name in [*files, *[d for d in dirs if (Path(parent) / d).is_symlink()]]:
            p = Path(parent) / name
            rel = p.relative_to(root).as_posix()
            result[rel] = ("symlink:" + os.readlink(p)) if p.is_symlink() else digest(p.read_bytes())
    return result


def changes(before, after):
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def scope_ok(task, before, root):
    after = snapshot(root)
    changed = changes(before, after)
    invalid = [p for p in changed if not any(fnmatch.fnmatchcase(p, pat) for pat in SCOPES[task])
               or after.get(p, "").startswith("symlink:")]
    return not invalid, invalid, changed


def prepare(root, archive, task):
    import worker_eval
    root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(root, filter="data")
    # Do not put evaluation code, expected answers, or result ledgers in candidate context.
    shutil.rmtree(root / "tools/agent-tools/eval", ignore_errors=True)
    git("init", "-q", cwd=root)
    git("add", ".", cwd=root)
    git("-c", "user.name=Benchmark", "-c", "user.email=benchmark@invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Frozen fixture", cwd=root)
    worker_eval.TASKS[task]["seed"](root)
    return snapshot(root)


def check_worker(task, root):
    """Private grading entry point, invoked only after all solver calls finish."""
    import worker_eval
    ok, note = worker_eval.TASKS[task]["check"](root)
    # T2's existing checker equals its public tests; add private boundary probes.
    if ok and task == "T2":
        probe = ("from billing import prorate, invoice_total; "
                 "assert prorate(100,0,30)==0; assert prorate(100,1,30)==4; "
                 "assert invoice_total([],0)==0")
        r = subprocess.run([sys.executable, "-c", probe], cwd=root / "eval", capture_output=True)
        ok, note = r.returncode == 0, "private boundary probes" if r.returncode == 0 else "private boundary failure"
    print(json.dumps({"passed": bool(ok), "diagnostic": note}))


def parse_claude(output):
    """Claude result JSON contains complete per-session USD, including cache/model legs."""
    try:
        data = json.loads(output)
    except ValueError:
        return None, output, None
    if not isinstance(data, dict) or data.get("type") != "result":
        return None, output, None
    cost = data.get("total_cost_usd")
    usage = {"source": "claude_result", "session_id": data.get("session_id"),
             "usage": data.get("usage"), "model_usage": data.get("modelUsage"),
             "cost_kind": "provider_estimate"}
    return cost if finite_number(cost) else None, str(data.get("result", "")), usage


def critic_worker(request_path):
    request = json.loads(Path(request_path).read_text())
    binding = request["binding"]
    payload = {"model": binding["model"], "max_tokens": binding["max_tokens"],
               "messages": [{"role": "system", "content": REVIEW_INSTRUCTION},
                            {"role": "user", "content": request["packet"]}]}
    req = urllib.request.Request(binding["endpoint"], json.dumps(payload).encode(),
                                 {"Content-Type": "application/json",
                                  "Authorization": "Bearer " + os.environ[binding["api_key_env"]]})
    with urllib.request.urlopen(req, timeout=request["timeout"]) as response:
        data = json.load(response)
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    prices = binding.get("prices_per_million")
    actual = data.get("model")
    allowed = [binding["model"], *binding.get("response_model_aliases", [])]
    cost = None
    if prices and actual in allowed:
        inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        if all(finite_number(x) for x in (inputs, outputs, cached)) and cached <= inputs:
            cost = ((inputs - cached) * prices["input"] + cached * prices["cached_input"]
                    + outputs * prices["output"]) / 1_000_000
    print(json.dumps({"text": text, "usage": usage, "actual_model": actual,
                      "cost_usd": cost, "model_matches": actual in allowed}))


class Runtime:
    def __init__(self, config, specs, output):
        self.config, self.specs, self.output = config, specs, output

    def solve(self, binding_name, prompt, root, timeout, leg_dir):
        binding = self.config[binding_name]
        spec = self.specs[binding_name]
        review = binding_name == "critic"
        if review:
            spec = critic_spec(spec)
        call = agentcli.headless_cmd(spec, prompt=prompt, model=binding["model"], readonly=review)
        argv = call["argv"]
        if spec["name"] == "claude" and "--output-format" in argv:
            argv[argv.index("--output-format") + 1] = "json"
        env = {**os.environ, **call["env"]}
        write_json(leg_dir / "invocation.json", {"argv": argv, "stdin": call["stdin"],
                                                "environment_keys": sorted(call["env"])})
        r = run_process(argv, root, timeout, call["stdin"], env)
        cost, text, usage = parse_claude(r["stdout"]) if spec["name"] == "claude" else (None, r["stdout"], None)
        if call.get("output_file"):
            p = Path(call["output_file"])
            if p.exists():
                text = p.read_text()
                p.unlink()
        if r["status"] == "ok" and not text.strip():
            r["status"] = "empty_output"
        if spec["name"] == "claude":
            try:
                result = json.loads(r["stdout"])
                if result.get("is_error"):
                    r["status"] = "execution_error"
            except (ValueError, AttributeError):
                pass
        if r["status"] == "execution_error":
            message = (r["stdout"] + r["stderr"]).lower()
            if any(x in message for x in ("quota", "rate limit", "usage limit")):
                r["status"] = "quota_error"
            elif any(x in message for x in ("unauthorized", "authentication", "not logged in")):
                r["status"] = "auth_error"
        r.update(text=text, cost_usd=cost, usage=usage, requested_model=binding["model"])
        return r

    def critique(self, packet, root, timeout, leg_dir):
        if critic_transport(self.config["critic"]) == "cli":
            # Start outside the candidate repository with no files or API credential requirement.
            with tempfile.TemporaryDirectory(prefix="orchestration-critic-") as temp:
                r = self.solve("critic", REVIEW_INSTRUCTION + "\n\n" + packet,
                               Path(temp), timeout, leg_dir)
            r["transport"] = "cli"
            return r
        request_path = leg_dir / "request.json"
        write_json(request_path, {"binding": self.config["critic"], "packet": packet, "timeout": timeout})
        r = run_process([sys.executable, str(Path(__file__).resolve()), "--critic-request", str(request_path)],
                        leg_dir, timeout)
        r.update(cost_usd=None, text="")
        if r["status"] == "ok":
            try:
                data = json.loads(r["stdout"])
                r.update(data)
                if not data["model_matches"]:
                    r["status"] = "model_mismatch"
            except (ValueError, KeyError, TypeError):
                r["status"] = "invalid_critic_response"
        return r

    def grade(self, task, root, timeout):
        r = run_process([sys.executable, str(Path(__file__).resolve()), "--check", task, str(root)],
                        HERE, timeout)
        if r["status"] != "ok":
            return {"passed": False, "diagnostic": r["status"], "status": r["status"]}
        try:
            return {**json.loads(r["stdout"]), "status": "ok"}
        except ValueError:
            return {"passed": False, "diagnostic": "invalid checker output", "status": "checker_error"}


def public_gate(task, root, timeout):
    commands = {"T1": [sys.executable, "-m", "pytest", "-q", "eval"],
                "T2": [sys.executable, "-m", "pytest", "-q", "eval/test_billing.py"],
                "T3": [sys.executable, "-m", "json.tool", "schemas/node-budget-summary.schema.json"]}
    result = run_process(commands[task], root, timeout)
    return result["status"] == "ok", (result["stdout"] + result["stderr"])[-12000:], result["status"]


def review_packet(prompt, root, before):
    after = snapshot(root)
    paths = changes(before, after)
    context = {}
    for name in paths:
        p = root / name
        if p.is_file() and not p.is_symlink():
            context[name] = p.read_text(errors="replace")
        else:
            context[name] = "[deleted]"
    # Include the immutable public tests when present, never private checker output.
    for name in ("eval/test_billing.py",):
        p = root / name
        if p.exists():
            context[name] = p.read_text()
    packet = json.dumps({"request": prompt, "candidate_files": context,
                         "diff": git("diff", "HEAD", cwd=root).decode(errors="replace")}, ensure_ascii=False)
    if len(packet.encode()) > 180_000:
        raise ValueError("review packet exceeds 180KB; no silent truncation")
    return packet


def findings_from(text):
    obj = json.loads(text)
    findings = obj["findings"]
    if not isinstance(findings, list) or len(findings) > 50:
        raise ValueError("invalid findings")
    ids = set()
    for f in findings:
        if not isinstance(f, dict) or any(not isinstance(f.get(k), str) or not f[k].strip()
                                          for k in ("id", "path", "evidence", "suggestion")):
            raise ValueError("invalid finding")
        if f["id"] in ids or Path(f["path"]).is_absolute() or ".." in Path(f["path"]).parts:
            raise ValueError("invalid finding id/path")
        ids.add(f["id"])
        f["adoption_confirmed"] = None
    return findings


def run_arm(task, repeat, arm, root, before, runtime):
    import worker_eval
    config = runtime.config
    run_id = f"{task}-{repeat}-{arm}"
    output = runtime.output / run_id
    output.mkdir()
    start = time.monotonic()
    deadline = start + config["wall_seconds"]
    solver_deadline = deadline - config["verify_seconds"]
    prompt = worker_eval.TASKS[task]["goal"] + "\nAllowed changes: " + ", ".join(SCOPES[task])
    row = {"run_id": run_id, "task": task, "repeat": repeat, "arm": arm, "passed": False,
           "escalated": False, "findings": [], "legs": [], "status": "ok"}
    draft_copy = None

    def leg(role, kind, text, limit):
        directory = output / f"{len(row['legs']) + 1}-{kind}"
        directory.mkdir()
        budget = min(limit, solver_deadline - time.monotonic())
        if budget <= 0:
            row["status"] = "timeout"
            return {"status": "timeout", "text": ""}
        if kind == "escalate":
            row["escalated"] = True
        try:
            r = (runtime.critique(text, root, budget, directory) if kind == "critic"
                 else runtime.solve(role, text, root, budget, directory))
        except KeyboardInterrupt:
            r = {"status": "cancelled", "text": "", "cost_usd": None}
        except (OSError, subprocess.SubprocessError) as exc:
            r = {"status": "execution_error", "text": "", "cost_usd": None,
                 "diagnostic": str(exc)}
        write_json(directory / "result.json", r)
        row["legs"].append({"kind": kind, **{k: v for k, v in r.items()
                                             if k not in ("stdout", "stderr", "text")}})
        row["status"] = r["status"]
        return r

    try:
        role = "starter" if arm == "cascade" else "baseline"
        limit = config.get(f"{arm}_draft_seconds", config["wall_seconds"])
        draft = leg(role, "draft", prompt, limit)
        if draft["status"] == "ok" and arm == "cascade":
            ok, diagnostic, status = public_gate(task, root, min(30, solver_deadline - time.monotonic()))
            write_json(output / "gate.json", {"passed": ok, "diagnostic": diagnostic, "status": status})
            row["gate_passed"] = ok
            if status == "timeout":
                row["status"] = "timeout"
            elif not ok:
                leg("baseline", "escalate", prompt + "\nPublic gate failed:\n" + diagnostic,
                    config["wall_seconds"])
        elif draft["status"] == "ok" and arm == "critique":
            draft_copy = output / "draft"
            shutil.copytree(root, draft_copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            # Retain Git metadata for the existing T3 new-test check.
            shutil.copytree(root / ".git", draft_copy / ".git")
            packet = review_packet(prompt, root, before)
            critic = leg("critic", "critic", packet, config["critic_seconds"])
            if critic["status"] == "ok":
                row["findings"] = findings_from(critic["text"])
                revision_before = snapshot(root)
                leg("baseline", "revision", prompt + "\nReview findings:\n" + critic["text"]
                    + "\nRevise once. Report each finding ID as accepted/rejected with reason and changed path."
                    + " If no changes are needed, state that explicitly.", config["wall_seconds"])
                row["revision_changed_paths"] = changes(revision_before, snapshot(root))
        valid, invalid, changed = scope_ok(task, before, root)
        row.update(changed_paths=changed, scope_violations=invalid)
        # Save candidate evidence before grading runs tests that may write caches.
        write_json(output / "candidate_hashes.json", snapshot(root))
        (output / "candidate.diff").write_bytes(git("diff", "HEAD", cwd=root))
        evidence = output / "changed-files"
        for name in changed:
            p = root / name
            if p.is_file() and not p.is_symlink():
                dest = evidence / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest)
        if row["status"] == "ok" and valid:
            grade = runtime.grade(task, root, max(0, deadline - time.monotonic()))
            row.update(passed=grade["passed"], diagnostic=grade["diagnostic"], status=grade["status"])
        elif not valid:
            row["status"] = "scope_violation"
    except KeyboardInterrupt:
        row["status"] = "cancelled"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        row["status"] = "harness_error"
        row["diagnostic"] = str(exc)
    finally:
        row["wall"] = time.monotonic() - start
        if row["wall"] > config["wall_seconds"]:
            row.update(passed=False, status="timeout")
        costs = [r.get("cost_usd") for r in row["legs"]]
        row["cost_usd"] = sum(costs) if costs and all(c is not None for c in costs) else None
        # Diagnostic grading occurs after the scored workflow, never reaches solver/critic.
        if draft_copy is not None and row["status"] != "cancelled":
            diagnostic_start = time.monotonic()
            try:
                valid, _, _ = scope_ok(task, before, draft_copy)
                grade = runtime.grade(task, draft_copy, config["verify_seconds"]) if valid else {"passed": False}
                row["draft_passed"] = grade["passed"] if grade.get("status", "ok") == "ok" else None
            except Exception:
                row["draft_passed"] = None
            row["diagnostic_wall"] = time.monotonic() - diagnostic_start
            shutil.rmtree(draft_copy)
        write_json(output / "run.json", row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--stop-after-usd", type=float, help="Stop between runs; not a per-call hard spending cap")
    parser.add_argument("--check", nargs=2, metavar=("TASK", "ROOT"), help=argparse.SUPPRESS)
    parser.add_argument("--critic-request", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check:
        check_worker(args.check[0], Path(args.check[1]))
        return
    if args.critic_request:
        critic_worker(args.critic_request)
        return
    if not args.manifest:
        parser.error("--manifest required")
    try:
        config = validate(json.loads(args.manifest.read_text()))
        if os.name != "posix":
            raise ValueError("POSIX process-group cancellation is required")
        if args.stop_after_usd is not None and not finite_number(args.stop_after_usd, positive=True):
            raise ValueError("--stop-after-usd must be positive and finite")
        specs = {role: agentcli.load_cli(config[role]["cli"], REPO)
                 for role in ("baseline", "starter")}
        cli_critic = critic_transport(config["critic"]) == "cli"
        if cli_critic:
            specs["critic"] = critic_spec(agentcli.load_cli(config["critic"]["cli"], REPO))
        commit = git("rev-parse", config.get("commit", "HEAD") + "^{commit}").decode().strip()
        schedule = []
        rng = random.Random(config["seed"])
        for task in config["tasks"]:
            for repeat in range(1, config["repeat"] + 1):
                arms = list(ARMS)
                rng.shuffle(arms)
                schedule.extend({"task": task, "repeat": repeat, "arm": arm} for arm in arms)
        frozen = {**config, "commit": commit, "schedule": schedule,
                  "resolved_cli_specs": {role: frozen_spec(spec) for role, spec in specs.items()},
                  "source_hashes": {p.name: digest(p.read_bytes()) for p in
                                    (Path(__file__), HERE / "worker_eval.py", HERE / "orchestration_report.py",
                                     HERE / "eval_io.py", HERE / "engine.py", Path(agentcli.__file__))},
                  "python": sys.version, "concurrency": 1,
                  "stop_after_usd": args.stop_after_usd}
        if not args.execute:
            print(json.dumps(frozen, ensure_ascii=False, indent=2))
            return
        if not cli_critic and not os.environ.get(config["critic"]["api_key_env"]):
            raise ValueError("critic API key environment variable is missing")
        for role, spec in specs.items():
            executable = agentcli.headless_cmd(spec, prompt="preflight", model=config[role]["model"])["argv"][0]
            if not shutil.which(executable):
                raise ValueError(f"CLI executable unavailable: {executable}")
            if role == "critic":
                help_result = run_process([executable, "--help"], HERE, 10)
                required = ("--safe-mode", "--tools", "--strict-mcp-config", "--no-session-persistence")
                if help_result["status"] != "ok" or any(flag not in help_result["stdout"] for flag in required):
                    raise ValueError("Update Claude CLI: tool-less critic flags are unavailable")
    except (ValueError, OSError, agentcli.AgentCliError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    output = new_run_dir(args.output, "orchestration", "three-arm")
    write_json(output / "manifest.json", frozen)
    archive = git("archive", commit)
    runtime = Runtime(config, specs, output)
    rows = []
    stop_reason = None
    try:
        for item in schedule:
            with tempfile.TemporaryDirectory(prefix="orchestration-") as temp:
                root = Path(temp) / "candidate"
                before = prepare(root, archive, item["task"])
                row = run_arm(**item, root=root, before=before, runtime=runtime)
            rows.append(row)
            with (output / "ledger.jsonl").open("a") as ledger:
                ledger.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            write_json(output / "report.json", summarize(rows))
            print(f"{row['run_id']}: {'PASS' if row['passed'] else 'FAIL'} ({row['status']})", flush=True)
            if row["status"] == "cancelled":
                stop_reason = "cancelled"
                break
            if args.stop_after_usd is not None:
                if any(r["cost_usd"] is None for r in rows):
                    stop_reason = "cost_unknown"
                    break
                if sum(r["cost_usd"] for r in rows) >= args.stop_after_usd:
                    stop_reason = "spending_threshold"
                    break
    finally:
        write_json(output / "completion.json", {"completed": len(rows), "planned": len(schedule),
                                                "stop_reason": stop_reason,
                                                "complete": len(rows) == len(schedule)})
        print(f"Results: {output}")


if __name__ == "__main__":
    main()
