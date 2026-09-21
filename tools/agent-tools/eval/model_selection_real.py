"""Opt-in serial real-run adapter; existing Agent CLI and verification contracts.

No router, scheduler, pricing model, or production writes. Workspaces and every
receipt remain under the eval run directory. Archive seeds hide solution history.
"""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tarfile
import time
from unittest.mock import patch

import engine
from eval_io import safe_name, write_json
from model_selection_eval import ms, vc, validate, number


KIRO_PROFILE = {
    "name": "selector-qualification",
    "description": "Qualification run confined to the current task workspace",
    "prompt": "Work only in the current working directory. Resolve relative task paths against this directory. Do not read or edit the source repository outside it. Use only the built-in read/write/shell tools.",
    "tools": ["read", "write", "shell"], "allowedTools": ["read", "write", "shell"],
    "mcpServers": {}, "resources": [], "includeMcpJson": False,
}


def containment_profile(workspace, protected_roots):
    """macOS repository guard inherited by child processes; no remote MCP tools.

    The Python environment remains readable. Writes outside the workspace are
    limited to CLI runtime records. Installed skills are unreadable. This is a
    filesystem guard, not a general malicious-code sandbox or an egress policy.
    """
    workspace = str(Path(workspace).resolve())
    venv = str(Path(sys.prefix).resolve())
    rules = ["(version 1)", "(allow default)"]
    home = Path.home()
    runtime_dirs = [home / ".kiro" / name for name in ("cache", "logs", "sessions", "session-index", "snapshots")]
    runtime_files = [home / ".kiro/.cli_bash_history"]
    storage = home / "Library/Application Support/kiro-cli"
    runtime_dirs += [storage / "history", storage / "run-receipts"]
    runtime_files += [storage / name for name in ("data.sqlite3", "data.sqlite3-wal", "data.sqlite3-shm", ".refresh.lock")]
    allowed = [f"(subpath {json.dumps(workspace)})"]
    allowed += [f"(subpath {json.dumps(str(p.resolve()))})" for p in runtime_dirs]
    allowed += [f"(literal {json.dumps(str(p.resolve()))})" for p in runtime_files]
    allowed += ['(literal "/dev/null")', '(literal "/dev/tty")']
    rules.append("(deny file-write* (require-not (require-any " + " ".join(allowed) + ")))")
    for directory in (home / ".kiro/skills", home / ".agents/skills", home / ".codex/skills"):
        rules.append(f"(deny file-read* (subpath {json.dumps(str(directory.resolve()))}))")
    for root in sorted({str(Path(p).resolve()) for p in protected_roots}):
        quoted = json.dumps(root)
        rules.append(f"(deny file-write* (require-all (subpath {quoted}) (require-not (subpath {json.dumps(workspace)}))))")
        rules.append(f"(deny file-read* (require-all (subpath {quoted}) (require-not (require-any (subpath {json.dumps(workspace)}) (subpath {json.dumps(venv)})))))")
    return "\n".join(rules) + "\n"


def contained_command(argv, candidate, workspace, run, protected_roots):
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        raise RuntimeError("real-run requires macOS sandbox-exec repository containment; import external receipts on other hosts")
    argv = list(argv)
    if candidate["agent_cli"] == "kiro":
        argv[2:2] = ["--agent", KIRO_PROFILE["name"]]
    profile = run / "candidate.sb"
    profile.write_text(containment_profile(workspace, protected_roots))
    return ["/usr/bin/sandbox-exec", "-f", str(profile.resolve()), *argv]


def git(*args, cwd=None, binary=False):
    p = engine.run_process(["git", *args], cwd=cwd or engine.REPO, capture_output=True,
                           text=not binary, timeout=120)
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, ["git", *args], p.stdout, p.stderr)
    return p.stdout if binary else p.stdout.strip()


def safe_path(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or path.is_symlink():
        raise ValueError(f"unsafe fixture path: {relative}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"fixture path escapes workspace: {relative}")
    return path


def restore_checks(f, workspace):
    task = f["real_task"]
    for relative in task.get("test_files", []):
        body = git("show", f"{task['verification_revision']}:{relative}", binary=True)
        path = safe_path(workspace, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    for relative, text in task.get("verification_files", {}).items():
        path = safe_path(workspace, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def snapshot(workspace, message):
    git("add", "-A", cwd=workspace)
    git("-c", "user.name=Selector Eval", "-c", "user.email=selector-eval@localhost",
        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
        "commit", "--allow-empty", "-qm", message, cwd=workspace)
    return git("rev-parse", "HEAD", cwd=workspace)


def verify(f, workspace, rev, timeout):
    plan = f["verification_plan"]
    if plan["version"] != 1 or plan.get("criteria"):
        raise ValueError("real adapter supports command-only v1 plans; import other canonical receipts offline")
    started = time.monotonic()
    commands = [vc.run_plan_command(c["command"], str(workspace), timeout,
                                   env=test_environment(),
                                   confirm=plan.get("policy", {}).get("confirm", 1))
                for c in plan["commands"]]
    return vc.build_receipt(plan, result_rev=rev, commands=commands,
                            verified_by="selector-qualification/verifycontract"), time.monotonic() - started


def test_environment():
    return {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}


def capture_selector(f):
    """Observe one live .9 decision; replay <= .9 needs no extra paid calls.

    Capture usage at the transport boundary before core defaults missing values
    to zero. Failed requests and partial token fields remain unknown.
    """
    observations = {}
    raw_usage = {"jev": [], "judge": []}

    def transport(stage, original):
        def call(*args, **kwargs):
            try:
                data = original(*args, **kwargs)
            except Exception:
                raw_usage[stage].append(None)
                raise
            fields = (data.get("usage") or {}) if stage == "jev" else data
            keys = ("input_tokens", "output_tokens") if stage == "jev" else ("prompt_eval_count", "eval_count")
            values = [fields.get(k) for k in keys]
            raw_usage[stage].append(sum(values) if all(number(v) for v in values) else None)
            return data
        return call

    def capture(stage, original):
        def call(*args, **kwargs):
            start = time.monotonic()
            try:
                answer = original(*args, **kwargs)
                observations[stage] = {"status": "answer", "answer": copy.deepcopy(answer),
                                       "tokens": None}
                return answer
            except ms.SelectError as exc:
                observations[stage] = {"status": "error", "detail": str(exc), "tokens": None}
                raise
            finally:
                if stage in observations:
                    observations[stage]["wall_seconds"] = time.monotonic() - start
                    values = raw_usage[stage]
                    observations[stage]["tokens"] = sum(values) if values and all(number(v) for v in values) else None
                    observations[stage]["request_tokens"] = list(values)
        return call
    with patch.object(ms, "post_jev", transport("jev", ms.post_jev)), \
            patch.object(ms.judge, "post_chat", transport("judge", ms.judge.post_chat)), \
            patch.object(ms, "ask_jev", capture("jev", ms.ask_jev)), \
            patch.object(ms, "ask_judge", capture("judge", ms.ask_judge)):
        result = ms.select(f["prompt"], f["candidates"], purpose=f["purpose"],
                           ratings=f.get("ratings", []), quotas=f["quotas"],
                           budget=f.get("budget"), min_confidence=.9)
    for attempt in result["attempts"]:
        if attempt["stage"] in ("jev", "judge") and attempt["stage"] not in observations:
            observations[attempt["stage"]] = {"status": attempt["outcome"], "tokens": 0, "wall_seconds": 0}
    return observations, result


def collect(fixtures, candidates, out, timeout, *, resume=False, containment=False, protected_roots=()):
    if containment and (sys.platform != "darwin" or not shutil.which("sandbox-exec")):
        raise RuntimeError("real-run requires macOS sandbox-exec repository containment; import external receipts on other hosts")
    # Explicit models prevent defaults changing between candidate runs.
    normalized = ms.normalize_candidates(candidates)
    if len(normalized) < 2 or any(not c["model"] for c in normalized):
        raise ValueError("real-run needs at least two explicit agent/model candidates")
    results = []
    unavailable = {}
    for original in fixtures:
        f = copy.deepcopy(original)
        if not resume:
            f.update(provenance="measured", outcomes={}, selector_observations={})
            f["quotas"] = ms.quota_observations()
            f["budget"] = ms.budget_summary(f["workload"])
            f["candidates"] = [ms.describe_candidate(c, quotas=f["quotas"], ratings=f.get("ratings", []),
                                                      purpose=f["purpose"]) for c in normalized]
        elif {ms.candidate_id(c) for c in normalized} != {ms.candidate_id(c) for c in f["candidates"]}:
            raise ValueError("resume candidate set must match recorded fixture")
        if f.get("resolver"):
            raise ValueError("real candidate override requires a direct-selector fixture; resolver snapshots are offline inputs")
        validate(f)
        task = f["real_task"]
        base = git("rev-parse", task["base_revision"] + "^{commit}")
        target = git("rev-parse", task["verification_revision"] + "^{commit}")
        f["real_task"].update(base_revision=base, verification_revision=target)
        root = out / safe_name(f["id"])
        root.mkdir(exist_ok=resume)
        # Capture decisions before learning any outcomes (no oracle leakage).
        if not resume:
            f["selector_observations"], f["live_selector_result"] = capture_selector(f)
        f["containment"] = {"enabled": containment, "kiro_external_mcp": False if containment else "default",
                            "policy": "workspace-writes-v2" if containment else None,
                            "protected_repositories": [str(p) for p in (engine.REPO, *protected_roots)]}
        write_json(root / "fixture.json", f)
        write_json(root / "selector.json", {"observations": f["selector_observations"], "result": f["live_selector_result"]})
        print(f"{f['id']}: selector observations captured (threshold=0.9)", flush=True)
        live = f["live_selector_result"]
        eligible = {c["id"] for c in live.get("state", {}).get("candidates", f["candidates"])}
        dropped = {c["id"] for c in live.get("dropped", [])}
        if {c["id"] for c in f["candidates"]} <= dropped:
            eligible = set()
        for index, candidate in enumerate(f["candidates"]):
            cid = ms.candidate_id(candidate)
            if cid in f["outcomes"]:
                print(f"{f['id']} {cid}: retained recorded outcome", flush=True)
                continue
            if cid in unavailable:
                f["outcomes"][cid] = {"status": "api-unavailable", "reason": unavailable[cid]}
                write_json(root / "outcomes.json", f["outcomes"])
                print(f"{f['id']} {cid}: api-unavailable (not invoked)", flush=True)
                continue
            if cid not in eligible:
                f["outcomes"][cid] = {"status": "no-eligible-candidate"}
                print(f"{f['id']} {cid}: no-eligible-candidate", flush=True)
                continue
            run = root / f"{index + 1}-{safe_name(cid)}"
            workspace = run / "workspace"
            workspace.mkdir(parents=True)
            archive = git("archive", "--format=tar", base, binary=True)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
                tf.extractall(workspace, filter="data")
            git("init", "-q", cwd=workspace)
            restore_checks(f, workspace)
            if containment and candidate["agent_cli"] == "kiro":
                write_json(workspace / ".kiro/agents/selector-qualification.json", KIRO_PROFILE)
            preparation = [vc.run_plan_command(command, str(workspace), timeout, env=test_environment())
                           for command in task.get("setup_commands", []) + task.get("environment_checks", [])]
            write_json(run / "environment.json", preparation)
            if any(p.get("exit_code") != 0 or p.get("inconclusive") for p in preparation):
                f["outcomes"][cid] = {"status": "environment-error"}
                write_json(root / "outcomes.json", f["outcomes"])
                print(f"{f['id']} {cid}: environment-error", flush=True)
                continue
            seed_rev = snapshot(workspace, "fixture seed and fixed acceptance checks")
            before, _ = verify(f, workspace, seed_rev, timeout)
            write_json(run / "seed-receipt.json", before)
            if vc.receipt_overall(before) != "fail":
                f["outcomes"][cid] = {"status": "invalid-seed" if vc.receipt_overall(before) == "pass" else "environment-error"}
                write_json(root / "outcomes.json", f["outcomes"])
                print(f"{f['id']} {cid}: {f['outcomes'][cid]['status']}", flush=True)
                continue
            print(f"{f['id']} {cid}: running (timeout={timeout:g}s)", flush=True)
            start = time.monotonic()
            try:
                built = engine.headless_cmd(candidate["agent_cli"], candidate["model"], f["prompt"], readonly=False, no_session=True)
                if containment:
                    built["argv"] = contained_command(built["argv"], candidate, workspace, run,
                                                       (engine.REPO, *protected_roots))
                    scratch = workspace / ".selector-tmp"
                    scratch.mkdir()
                    with (workspace / ".git/info/exclude").open("a") as excluded:
                        excluded.write("\n.selector-tmp/\n")
                    built["env"] = {**(built.get("env") or {}), "TMPDIR": str(scratch.resolve()),
                                    "TMP": str(scratch.resolve()), "TEMP": str(scratch.resolve())}
                write_json(run / "invocation.json", {"agent_cli": candidate["agent_cli"], "model": candidate["model"],
                                                      "argv": built["argv"], "base_revision": base, "timeout": timeout})
                p = engine.run_process(built["argv"], input=built.get("stdin"), cwd=workspace,
                                       capture_output=True, text=True, timeout=timeout,
                                       env={**os.environ, **test_environment(), **engine.load_env(candidate["agent_cli"]), **(built.get("env") or {})})
                stdout, stderr = p.stdout or "", p.stderr or ""
                status = "ok" if p.returncode == 0 else "cli-error"
            except subprocess.TimeoutExpired as exc:
                stdout = exc.output or ""
                stderr = exc.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", "replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", "replace")
                stderr += "\nTIMEOUT"
                status = "timeout"
            except (OSError, RuntimeError, ValueError) as exc:
                stdout, stderr, status = "", str(exc), "environment-error"
            wall = time.monotonic() - start
            (run / "stdout.txt").write_text(stdout)
            (run / "stderr.txt").write_text(stderr)
            if status == "cli-error" and candidate["agent_cli"] == "kiro" and "Monthly request limit reached" in stderr:
                # A provider's explicit hard limit is not a task failure. Do not
                # make more paid attempts for this candidate in this collection.
                unavailable[cid] = "Kiro monthly request limit reached"
            # Restore pinned tests after the agent; deleting/editing checks is not success.
            restore_checks(f, workspace)
            rev = snapshot(workspace, "candidate result with fixed acceptance checks")
            receipt, verify_wall = verify(f, workspace, rev, timeout)
            write_json(run / "receipt.json", receipt)
            # Shared eval adapter marker parser retains None when absent.
            from worker_eval import _agent_markers
            usage = _agent_markers(stderr)
            tokens = (usage["tokens_in"] + usage["tokens_out"]
                      if usage["tokens_in"] is not None and usage["tokens_out"] is not None else None)
            f["outcomes"][cid] = {"status": status, "receipt": receipt, "result_rev": rev,
                                   "tokens": tokens, "cost": None, "currency": None,
                                   "wall_seconds": wall + verify_wall, "agent_wall_seconds": wall,
                                   "verification_wall_seconds": verify_wall,
                                   "usage": usage, "harness": candidate["agent_cli"],
                                   "containment": dict(f["containment"])}
            write_json(run / "outcome.json", f["outcomes"][cid])
            write_json(root / "outcomes.json", f["outcomes"])
            print(f"{f['id']} {cid}: {status}, receipt={receipt['verdict']}, wall={wall + verify_wall:.1f}s, tokens={tokens}", flush=True)
        results.append(f)
        write_json(out / "measured-fixtures.json", results)
    return results
