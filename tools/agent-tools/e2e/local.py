#!/usr/bin/env python3
"""ローカル PC の実機 E2E — install.sh で入れた**実バイナリ**を実プロセスで走らせる。

`run.py`（mock シナリオ）が各エンジンの unittest を呼ぶのに対し、こちらは
一時 prefix へ `tools/agent-tools/install.sh` を実行し、そこで出来た zipapp を
PATH の先頭に置いて叩く。したがって zipapp の組み立て・CLI 定義の配置・
エンジン間の受け渡し（flow バス → audit の収集）まで含めて、この PC の実物で検証する。

状態は環境変数で隔離する（実 `~/.agents` を汚さない・実 control.json の
workloads 上書きで stub が本物のモデルに化けるのも防ぐ）。

  python3 tools/agent-tools/e2e/local.py                 # fast のみ（モデル不要・数十秒）
  python3 tools/agent-tools/e2e/local.py --tier all      # ローカル ollama も使う
  python3 tools/agent-tools/e2e/local.py --scenario flow-run --keep
  python3 tools/agent-tools/e2e/local.py --json > local-e2e.json
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CLIS = ["agent-project", "agent-flow", "agent-amigos", "agent-audit",
        "agent-herd", "agent-ollama", "agent-aider", "agent-loop", "agent-send"]


class Ctx:
    """1 回の実行で共有する砂場（temp prefix への install 済み・環境隔離済み）。"""

    def __init__(self, root: Path, model: str):
        self.root, self.model = root, model
        self.bin = root / "bin"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "AGENT_PROJECT_AGENTS_HOME": str(root / ".agents"),
            "AGENT_CONTROL_DIR": str(root / "control"),
            "AGENT_BUDGET_DIR": str(root / "budget"),
            "AGENT_AMIGOS_TURNS_DIR": str(root / "turns"),
            "AGENT_LOOP_RUN_DIR": str(root / "runs"),          # harness の run ログ
            "AGENT_OLLAMA_LOG_DIR": str(root / "logs/ollama"),  # 実モデル呼び出しの記録
            "AGENT_AMIGOS_STUB_COST": "0.01",
            "AGENT_FLOW_STUB_SLEEP_MAX": "0",
            "AGENT_LOOP_STUB_DELAY": "0",
        }

    def workdir(self, name: str) -> Path:
        d = self.root / "work" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run(self, *argv, cwd=None, timeout=300, check=True, stdin=None):
        proc = subprocess.run([str(a) for a in argv], cwd=str(cwd or self.root), env=self.env,
                              input=stdin, capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise AssertionError(
                f"exit={proc.returncode}: {' '.join(str(a) for a in argv)}\n"
                f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}")
        return proc


def install(root: Path) -> subprocess.CompletedProcess:
    """一時 prefix へ 4 エンジン + agent-herd を入れる。全シナリオ共通の前提。"""
    (root / "bin").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["bash", str(ROOT / "tools/agent-tools/install.sh"), "--prefix", str(root / "bin")],
        env={**os.environ, "AGENT_PROJECT_AGENTS_HOME": str(root / ".agents")},
        capture_output=True, text=True, timeout=600)


def local_models() -> "list[str] | None":
    """ollama が持っているモデル名。到達できなければ None（= tier:model は測れない）。"""
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = "http://" + host
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=3) as res:
            return [m["name"] for m in json.load(res).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# シナリオ（AssertionError を投げたら失敗。返り値は使わない）
# --------------------------------------------------------------------------- #
def s_install_parity(ctx: Ctx) -> None:
    """install.sh が出したものが全部そろい、実バイナリとして起動できる。"""
    for name in CLIS:
        path = ctx.bin / name
        assert path.exists() and os.access(path, os.X_OK), f"{name} が入っていない: {path}"
        ctx.run(path, "--help", timeout=60)
    defs = sorted(p.name for p in (ctx.root / ".agents/agents").glob("*.json"))
    for required in ("aider.json", "ollama.json"):
        assert required in defs, f"CLI 定義が配られていない: {required}（{defs}）"


def s_installed_drift(ctx: Ctx) -> None:
    """この PC の `~/.local/bin` が repo より古くないか（「直したのに直らない」の正体）。"""
    installed = {name: shutil.which(name) for name in CLIS}
    installed = {k: v for k, v in installed.items() if v and not v.startswith(str(ctx.bin))}
    if not installed:
        raise Skip("agent-* が PATH に入っていない（この PC では未インストール）")
    sources = [p for glob in ("tools/agent-*/agent_*/**/*.py",
                              "tools/agent-tools/agentcore/agentcore/**/*.py",
                              "tools/agent-*/agent-*.py")
               for p in ROOT.glob(glob) if "__pycache__" not in p.parts]
    newest = max(p.stat().st_mtime for p in sources)   # 配布に入るものだけ（tests/eval は無関係）
    stale = [k for k, v in installed.items() if Path(v).stat().st_mtime < newest]
    assert not stale, ("配布物が repo のソースより古い: " + ", ".join(sorted(stale)) +
                       "\n  bash tools/agent-tools/install.sh")


def s_flow_run(ctx: Ctx) -> None:
    """agent-flow: 単発 run が計画→fan-out→gate→統合まで回り、result が done を返す。"""
    d = ctx.workdir("flow")
    ctx.run(ctx.bin / "agent-flow", "--bus", d / "bus", "run", "E2E: ローカル実機の疎通確認",
            "--workers", "1", "--planner", "stub", "--executor", "stub", "--poll", "0.2")
    runs = sorted((d / "bus/runs").iterdir())
    assert len(runs) == 1, f"run が 1 本ではない: {runs}"
    assert (runs[0] / "final.json").exists(), "final.json が書かれていない"
    out = ctx.run(ctx.bin / "agent-flow", "--bus", d / "bus", "--run-id", runs[0].name,
                  "result", "--json").stdout
    result = json.loads(out)
    assert result["status"] == "done" and result["done"] is True, result
    assert result["final_nodes"], "final_nodes が空"


def s_project_drain(ctx: Ctx) -> None:
    """agent-project: backlog 1 件が実行→verify→done→archive まで抜ける。"""
    d = ctx.workdir("project")
    (d / "backlog").mkdir(exist_ok=True)
    (d / "backlog/T1.md").write_text(
        "## T1: ローカル実機 E2E\n- status: ready\n- verify: `true`\n", encoding="utf-8")
    proc = ctx.run(ctx.bin / "agent-project", "run", "--no-delivery-review", "--workdir", d,
                   "--backlog", d / "backlog", "--policy", d / "policy.md",
                   "--decisions", d / "decisions", "--journal", d / "journal.md",
                   "--needs", d / "needs", "--bus", d / "bus", "--planner", "none",
                   "--executor", "stub", "--flow-planner", "stub", "--max-cycles", "5", cwd=d)
    assert "done=1" in proc.stdout and "drained" in proc.stdout, proc.stdout
    assert (d / "archive/T1.md").exists(), "archive へ移っていない"
    assert not list((d / "backlog").glob("*.md")), "backlog に残っている"
    assert list((d / "bus/runs").iterdir()), "agent-flow バスに run が無い（委譲していない）"
    assert (d / "DELIVERY.md").exists(), "納品書が出ていない"


def s_amigos_cycle(ctx: Ctx) -> None:
    """agent-amigos: 公示→自己補充→各ロール実行→統合→受入まで 1 ノードで回る。"""
    d = ctx.workdir("amigos")
    (d / "design.md").write_text("# design\n受入基準: 成果物が揃うこと。\n", encoding="utf-8")
    (d / "roles.json").write_text(json.dumps({
        "mission": {"title": "ローカル実機 E2E", "goal": "成果物を揃える", "staffing_timeout": 0,
                    "convergence": {"done_when": "reviewer-approved", "quiescence_turns": 5},
                    "budget": {"execution_minutes": 10}},
        "roles": [{"id": "architect", "mission": "設計", "deliverables": ["architecture.md"]},
                  {"id": "impl", "mission": "実装", "deliverables": ["src/main.py"],
                   "collaborates_with": ["architect"]},
                  {"id": "reviewer", "mission": "レビュー", "approver": True}],
    }, ensure_ascii=False), encoding="utf-8")
    ctx.run(ctx.bin / "agent-amigos", "post", "--bus", d / "bus", "--node-id", "e2e-owner",
            "--design", d / "design.md", "--roles", d / "roles.json", "--mission-id", "am-e2e",
            "--drive", "--agent-cli", "stub", "--interval", "0", "--cycles", "40", cwd=d)
    deliverable = d / "bus/missions/am-e2e/deliverable"
    for rel in ("architect/architecture.md", "impl/src/main.py"):
        assert (deliverable / rel).exists(), f"deliverable が無い: {rel}"
    ctx.run(ctx.bin / "agent-amigos", "accept", "--bus", d / "bus", "--node-id", "e2e-owner",
            "--home", d, "am-e2e", cwd=d)
    assert list((d / "deliveries").rglob("*")), "納品棚が空（accept が搬出していない）"


def s_audit_collect(ctx: Ctx) -> None:
    """agent-audit: 先行シナリオが実際に残したバスから証跡を収集する（エンジン間の受け渡し）。"""
    d = ctx.workdir("audit")
    config = {"audit_dir": str(d / "store"), "budget_dir": str(d / "budget"),
              "sources": ["flow-bus", "amigos-bus", "project-root"],
              "flow_buses": [str(ctx.workdir("flow") / "bus")],
              "amigos_buses": [str(ctx.workdir("amigos") / "bus")],
              "project_roots": [str(ctx.workdir("project"))]}
    (d / "agent-audit.json").write_text(json.dumps(config), encoding="utf-8")
    ctx.run(ctx.bin / "agent-audit", "--config", d / "agent-audit.json", "collect", cwd=d)
    records = [line for f in (d / "store/records").glob("*.jsonl")
               for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records, "レコードが 1 件も収集されていない"
    kinds = {json.loads(line).get("source") for line in records}
    assert "flow-bus" in kinds, f"flow バスを読めていない: {sorted(k for k in kinds if k)}"


# 小さいモデルはまれに空応答を返す（実測 gemma4:e2b で 1/3）。配線の E2E で測りたいのは
# そこではないので、**この失敗様式に限って** 1 回だけ引き直す。ほかの失敗はそのまま落とす。
_TRANSIENT = "空の応答"


def _model_run(ctx: Ctx, *argv, cwd=None, timeout=900):
    proc = ctx.run(*argv, cwd=cwd, timeout=timeout, check=False)
    if proc.returncode != 0 and _TRANSIENT in (proc.stdout + proc.stderr):
        proc = ctx.run(*argv, cwd=cwd, timeout=timeout, check=False)
    assert proc.returncode == 0, (f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout[-2000:]}"
                                 f"\n--- stderr ---\n{proc.stderr[-2000:]}")
    return proc


def s_ollama_oneshot(ctx: Ctx) -> None:
    """agent-ollama: ローカルモデルへ 1 回投げ、本文と usage を契約どおり分けて返す。"""
    proc = ctx.run(ctx.bin / "agent-ollama", ctx.model, timeout=600,
                   stdin="次の文字列だけを 1 行で返してください: agent-e2e-ok\n")
    assert "agent-e2e-ok" in proc.stdout, f"本文が stdout に無い: {proc.stdout[:400]!r}"
    assert "@agent-usage" in proc.stderr, "usage が stderr に無い"


def s_herd_harness(ctx: Ctx) -> None:
    """agent-herd harness run: ローカルモデルが実際にファイルを書き、機械が受入を検証する。"""
    d = ctx.workdir("herd")
    proc = _model_run(ctx, ctx.bin / "agent-herd", "harness", "run",
                      "hello.txt というファイルを作り、中身を agent-e2e-ok の 1 行だけにする。",
                      "--acceptance", "`hello.txt` があり agent-e2e-ok を含む",
                      "--deliverable", "hello.txt", "--agent-cli", "ollama",
                      "--model", ctx.model, "--dir", d, cwd=d)
    line = [x for x in proc.stdout.splitlines() if x.startswith("RESULT ")]
    assert line, f"RESULT 行が無い: {proc.stdout[-1000:]}"
    result = json.loads(line[-1][len("RESULT "):])
    assert result["ok"] is True, result
    assert (d / "hello.txt").exists(), "成果物が書かれていない"
    assert "agent-e2e-ok" in (d / "hello.txt").read_text(encoding="utf-8"), "中身が違う"
    assert result["verified"] is True, f"機械検証が通っていない: {result}"


def s_loop_harness(ctx: Ctx) -> None:
    """agent-loop run: 同じハーネスをもう 1 つの入口（配布した agent-loop zipapp）から回す。"""
    d = ctx.workdir("loop")
    proc = _model_run(ctx, ctx.bin / "agent-loop", "run",
                      "hello.txt というファイルを作り、中身を agent-e2e-ok の 1 行だけにする。",
                      "--acceptance", "`hello.txt` があり agent-e2e-ok を含む",
                      "--agent-cli", "ollama", "--model", ctx.model, "--dir", d, cwd=d)
    assert (d / "hello.txt").exists(), f"成果物が書かれていない: {proc.stdout[-1000:]}"


def _ollama_calls(ctx: Ctx) -> int:
    """この砂場から出たローカル推論の回数（1 呼び出し = 1 ログ）。配線が本当に通ったかの根拠。"""
    return len(list((ctx.root / "logs/ollama").glob("*.jsonl")))


def s_flow_wired(ctx: Ctx) -> None:
    """agent-flow を stub ではなくローカルモデルで回す（executor=agent / agent-cli=ollama）。

    グラフは work 1 ノードに固定する（plan-file）。既定の fan-out は同じ配線を 6 回通るだけで
    壁時計が 10 倍になり、verify ノードを挟むと**終端がモデルの判定文に左右される**
    （実測: check1 の出力が機械の読める verdict にならず run が failed）。ここで見たいのは
    配線であって、モデルの判定力でも並列数でもない。
    """
    d = ctx.workdir("flow-wired")
    (d / "plan.json").write_text(json.dumps(
        {"name": "e2e-wired",
         "nodes": [{"id": "w1", "goal": "{{request}}", "deps": [], "kind": "work"}]}),
        encoding="utf-8")
    before = _ollama_calls(ctx)
    ctx.run(ctx.bin / "agent-flow", "--bus", d / "bus", "--agent-cli", "ollama",
            "run", "hello.txt に agent-e2e-ok と 1 行だけ書く", "--workers", "1",
            "--plan-file", d / "plan.json", "--executor", "agent",
            "--model", ctx.model, "--poll", "0.5", cwd=d, timeout=900)
    runs = sorted((d / "bus/runs").iterdir())
    result = json.loads(ctx.run(ctx.bin / "agent-flow", "--bus", d / "bus", "--run-id",
                                runs[0].name, "result", "--json").stdout)
    assert result["done"] is True, result
    assert _ollama_calls(ctx) > before, "ローカルモデルが 1 度も呼ばれていない（stub のまま）"
    written = [p for p in d.rglob("hello.txt")]
    assert written, "モデルが成果物を書いていない"
    assert "agent-e2e-ok" in written[0].read_text(encoding="utf-8", errors="replace"), \
        written[0].read_text(encoding="utf-8", errors="replace")[:200]


def s_project_wired(ctx: Ctx) -> None:
    """agent-project → agent-flow → ローカルモデル。委譲の層をまたいで実際に推論が走る。"""
    d = ctx.workdir("project-wired")
    (d / "backlog").mkdir(exist_ok=True)
    (d / "backlog/T1.md").write_text(
        "## T1: ローカルモデルで 1 件消化する\n- status: ready\n- verify: `true`\n", encoding="utf-8")
    before = _ollama_calls(ctx)
    proc = ctx.run(ctx.bin / "agent-project", "run", "--no-delivery-review", "--workdir", d,
                   "--backlog", d / "backlog", "--policy", d / "policy.md",
                   "--decisions", d / "decisions", "--journal", d / "journal.md",
                   "--needs", d / "needs", "--bus", d / "bus", "--planner", "none",
                   "--executor", "agent", "--agent-cli", "ollama", "--model", ctx.model,
                   "--flow-planner", "stub", "--max-cycles", "3", cwd=d, timeout=1200)
    assert "done=1" in proc.stdout and "drained" in proc.stdout, proc.stdout
    assert (d / "archive/T1.md").exists(), "archive へ移っていない"
    assert _ollama_calls(ctx) > before, "ローカルモデルが 1 度も呼ばれていない（stub のまま）"


class Skip(Exception):
    """前提が無いので測れない（失敗ではない）。"""


# --------------------------------------------------------------------------- #
# agent-loop（スケジュール実行）と agent-dashboard
# --------------------------------------------------------------------------- #
def s_loop_schedule(ctx: Ctx) -> None:
    """agent-loop: デーモンが定期プロンプトを tmux ペインのエージェントへ送り、完了まで見届ける。

    `loop-state` と `slots` は HOME 直下でしか移せない（`agent_home_subdir("", …)`）ので、
    このシナリオだけ HOME ごと砂場へ差し替える。tmux も専用ソケットへ隔離し、実機で
    動いている agent-loop のセッション・スロットには触れない。
    """
    tmux = shutil.which("tmux")
    if not tmux:
        raise Skip("tmux が無い")
    d, shim = ctx.workdir("loop"), ctx.root / "shim"
    shim.mkdir(exist_ok=True)
    socket = f"agent-e2e-{os.getpid()}"
    stub = ROOT / "tools/agent-loop/stub/kiro-cli-stub.py"
    (shim / "kiro-cli").write_text(f'#!/bin/sh\nexec {sys.executable} {stub} "$@"\n',
                                   encoding="utf-8")
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {tmux} -L {socket} "$@"\n', encoding="utf-8")
    for name in ("kiro-cli", "tmux"):
        (shim / name).chmod(0o755)
    (d / "agent-loop.json").write_text(json.dumps({
        "max_concurrent": 1, "kiro_options": {"trust_all_tools": True},
        "prompts": [{"name": "e2e-schedule", "prompt": "agent-e2e-scheduled-ok と一度だけ答えてください",
                     "interval_minutes": 1, "run_immediately_on_startup": True, "enabled": True}],
    }, ensure_ascii=False), encoding="utf-8")
    env = {**ctx.env, "HOME": str(ctx.root), "AGENT_LOOP_STUB_DELAY": "1",
           "PATH": f"{shim}{os.pathsep}{ctx.env['PATH']}"}
    log = ctx.root / ".agents/agent-loop.log"
    proc = subprocess.Popen([str(ctx.bin / "agent-loop"), "--no-auto-attach"], cwd=str(d), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        deadline, text = time.monotonic() + 180, ""
        while time.monotonic() < deadline:
            time.sleep(2)
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            if "state=DONE" in text or proc.poll() is not None:
                break
        assert "event=request_accepted" in text and "source=schedule" in text, \
            f"スケジュール起点の要求が受理されていない\n{text[-2000:]}"
        assert "event=dispatch_sent" in text, f"ペインへ送信されていない\n{text[-2000:]}"
        assert "state=DONE" in text, f"実行が終端していない\n{text[-2000:]}"
    finally:
        with contextlib.suppress(Exception):    # 通常終了（デーモンは stdin のコマンドで畳む）
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            proc.wait(timeout=30)
        if proc.poll() is None:                 # 子だけでなくプロセスグループごと落とす
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)
        subprocess.run([tmux, "-L", socket, "kill-server"], capture_output=True)
        with contextlib.suppress(OSError):      # kill-server はソケットの器を残す
            (Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}" / socket).unlink()


_JS_ARGV = """
const fs = require('fs'), path = require('path');
const agentCli = require(process.argv[2]);
const out = {};
for (const f of fs.readdirSync(process.argv[3]).filter((x) => x.endsWith('.json')).sort()) {
  const name = path.parse(f).name;
  const spec = agentCli.loadCli(name, null, { useCache: false });
  const mask = (b) => b.argv.map((t) => (t === b.outputFile ? '<out>' : t));
  out[name] = { write: mask(agentCli.headlessCmd(spec, 'M', 'P')),
                readonly: mask(agentCli.headlessCmd(spec, 'M', 'P', { readonly: true })) };
  try { out[name].interactive = agentCli.interactiveCmd(spec, 'M'); }
  catch { out[name].interactive = null; }
}
process.stdout.write(JSON.stringify(out));
"""

_PY_ARGV = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
from agentcore import agentcli
out = {}
for f in sorted(x for x in os.listdir(sys.argv[2]) if x.endswith(".json")):
    spec = agentcli.load_cli(f[:-5], use_cache=False)
    mask = lambda b: [("<out>" if t == b.get("output_file") else t) for t in b["argv"]]
    out[f[:-5]] = {"write": mask(agentcli.headless_cmd(spec, "M", "P")),
                   "readonly": mask(agentcli.headless_cmd(spec, "M", "P", readonly=True))}
    try:
        out[f[:-5]]["interactive"] = agentcli.interactive_cmd(spec, "M")
    except Exception:
        out[f[:-5]]["interactive"] = None
print(json.dumps(out))
"""

_JS_TASKS = """
const project = require(process.argv[2]);
process.stdout.write(JSON.stringify(project.listTasks(process.argv[3])));
"""

DASHBOARD = ROOT / "tools/agent-dashboard/src/features/agent-project/main"


def _node(ctx: Ctx, script: str, *args, env=None) -> str:
    if not shutil.which("node"):
        raise Skip("node が無い（agent-dashboard は Node 実装）")
    path = ctx.workdir("dashboard") / f"probe-{abs(hash(script)) % 10**8}.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path), *[str(a) for a in args]],
                          cwd=str(DASHBOARD), env=env or ctx.env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"node 失敗: {proc.stderr[-2000:]}"
    return proc.stdout


def s_dashboard_cli_parity(ctx: Ctx) -> None:
    """agent-dashboard: 同じ定義から dashboard(JS) と agentcore(Python) が同じ argv を出す。

    ローダが 2 実装ある以上、**入れたばかりの定義**で両者が割れると、道具によって CLI の
    起動が変わる。ユニットの golden は固定値、こちらは install.sh が実際に配ったものを見る。
    """
    defs = ctx.root / ".agents/agents"
    env = {**ctx.env, "KIRO_AGENTS_DIR": str(defs)}      # 両実装とも最優先で見る口
    js = json.loads(_node(ctx, _JS_ARGV, DASHBOARD / "agentCli.js", defs, env=env))
    py = json.loads(subprocess.run(
        [sys.executable, "-c", _PY_ARGV, str(ROOT / "tools/agent-tools/agentcore"), str(defs)],
        env=env, capture_output=True, text=True, timeout=120, check=True).stdout)
    assert sorted(js) == sorted(py), f"読めた定義が違う: js={sorted(js)} py={sorted(py)}"
    assert js, "定義が 1 つも無い"
    drift = [(name, mode) for name in py for mode in ("write", "readonly", "interactive")
             if js[name][mode] != py[name][mode]]
    assert not drift, "argv が割れている: " + ", ".join(
        f"{n}.{m}\n  js={js[n][m]}\n  py={py[n][m]}" for n, m in drift)


def s_dashboard_reads_project(ctx: Ctx) -> None:
    """agent-dashboard: エンジンが実際に書いた成果（archive のタスク）を dashboard が読める。"""
    archive = ctx.workdir("project") / "archive"
    if not archive.exists():
        raise Skip("project-drain が先に走っていない")
    tasks = json.loads(_node(ctx, _JS_TASKS, DASHBOARD / "project.js", archive))
    assert len(tasks) == 1, f"archive のタスクを読めていない: {tasks}"
    assert tasks[0]["id"] == "T1" and tasks[0]["status"] == "done", tasks[0]


SCENARIOS = [
    {"id": "install-parity", "engine": "agent-tools", "tier": "fast", "fn": s_install_parity,
     "covers": ["install", "zipapp", "cli-definitions", "exit-0"]},
    {"id": "installed-drift", "engine": "agent-tools", "tier": "fast", "fn": s_installed_drift,
     "covers": ["host-state", "stale-distribution"]},
    {"id": "flow-run", "engine": "agent-flow", "tier": "fast", "fn": s_flow_run,
     "covers": ["real-process", "fan-out", "review-gate", "result-json"]},
    {"id": "project-drain", "engine": "agent-project", "tier": "fast", "fn": s_project_drain,
     "covers": ["real-process", "verify", "archive", "delegation"]},
    {"id": "amigos-cycle", "engine": "agent-amigos", "tier": "fast", "fn": s_amigos_cycle,
     "covers": ["real-process", "roles", "integration", "acceptance", "delivery"]},
    {"id": "audit-collect", "engine": "agent-audit", "tier": "fast", "fn": s_audit_collect,
     "needs": ["flow-run", "project-drain", "amigos-cycle"],
     "covers": ["cross-engine", "collection", "normalization"]},
    {"id": "loop-schedule", "engine": "agent-loop", "tier": "daemon", "fn": s_loop_schedule,
     "covers": ["daemon", "schedule", "tmux-pane", "dispatch", "completion"]},
    {"id": "dashboard-cli-parity", "engine": "agent-dashboard", "tier": "fast",
     "fn": s_dashboard_cli_parity, "covers": ["cross-implementation", "installed-definitions"]},
    {"id": "dashboard-reads-project", "engine": "agent-dashboard", "tier": "fast",
     "fn": s_dashboard_reads_project, "needs": ["project-drain"],
     "covers": ["cross-tool", "engine-output", "task-parse"]},
    {"id": "flow-wired", "engine": "agent-flow", "tier": "wired", "fn": s_flow_wired,
     "covers": ["local-model", "executor-agent", "artifact", "no-stub"]},
    {"id": "project-wired", "engine": "agent-project", "tier": "wired", "fn": s_project_wired,
     "covers": ["local-model", "delegation", "no-stub"]},
    {"id": "ollama-oneshot", "engine": "agent-ollama", "tier": "model", "fn": s_ollama_oneshot,
     "covers": ["local-model", "stdout-stderr-split", "usage"]},
    {"id": "herd-harness", "engine": "agent-herd", "tier": "model", "fn": s_herd_harness,
     "covers": ["local-model", "tool-loop", "deliverable", "machine-verify"]},
    {"id": "loop-harness", "engine": "agent-loop", "tier": "model", "fn": s_loop_harness,
     "covers": ["local-model", "tool-loop", "second-entrypoint"]},
]


def select(args) -> list[dict]:
    by_id = {s["id"]: s for s in SCENARIOS}
    unknown = set(args.scenario) - set(by_id)
    if unknown:
        raise SystemExit(f"unknown scenario: {', '.join(sorted(unknown))}")
    chosen = {s["id"] for s in SCENARIOS
              if (args.tier in ("all", s["tier"]))
              and (args.engine in ("all", s["engine"]))
              and (not args.scenario or s["id"] in args.scenario)}
    for sid in list(chosen):                      # 前提シナリオ（audit は先行の成果物を読む）
        chosen.update(by_id[sid].get("needs", []))
    return [s for s in SCENARIOS if s["id"] in chosen]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="agent-tools local-machine E2E (real installed CLIs)")
    p.add_argument("--tier", choices=["fast", "model", "daemon", "wired", "all"], default="fast",
                   help="fast=モデル不要 / model=ローカル ollama を使う / "
                        "daemon=常駐と tmux を使う / wired=エンジンをローカルモデルで回す"
                        "（数分） / all=全部")
    p.add_argument("--engine", default="all")
    p.add_argument("--scenario", action="append", default=[])
    # 既定は e4b。e2b は同じ配線で 5/15 落ちた（空応答と「作らずに完了と言う」の 2 様式）。
    # ここで測りたいのはモデルの実力ではなく配線なので、素で通るモデルを既定にする。
    p.add_argument("--model", default=os.environ.get("AGENT_E2E_MODEL", "gemma4:e4b"))
    p.add_argument("--list", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--keep", action="store_true", help="砂場を消さない（デバッグ用）")
    args = p.parse_args(argv)
    if args.list:
        for s in select(args):     # 絞り込みは実行時と同じ規則で効かせる
            print(f"{s['id']:<18} {s['tier']:<6} {s['engine']:<14} {', '.join(s['covers'])}")
        return 0

    selected = select(args)
    root = Path(tempfile.mkdtemp(prefix="agent-e2e-local-"))
    results: list[dict] = []
    try:
        started = time.monotonic()
        installed = install(root)
        setup = {"id": "install", "engine": "agent-tools", "tier": "fast",
                 "status": "passed" if installed.returncode == 0 else "failed",
                 "seconds": round(time.monotonic() - started, 3), "covers": ["install.sh"],
                 "stdout": installed.stdout, "stderr": installed.stderr}
        results.append(setup)
        if setup["status"] == "passed":
            ctx = Ctx(root, args.model)
            models = local_models()
            for s in selected:
                started = time.monotonic()
                status, detail = "passed", ""
                try:
                    if s["tier"] in ("model", "wired"):
                        if models is None:
                            raise Skip("ollama に接続できない")
                        if args.model not in models:
                            raise Skip(f"モデルが無い: {args.model}（ollama pull {args.model}）")
                    s["fn"](ctx)
                except Skip as exc:
                    status, detail = "skipped", str(exc)
                except subprocess.TimeoutExpired as exc:
                    status, detail = "timeout", str(exc)
                except AssertionError as exc:
                    status, detail = "failed", str(exc)
                results.append({"id": s["id"], "engine": s["engine"], "tier": s["tier"],
                                "status": status, "detail": detail, "covers": s["covers"],
                                "seconds": round(time.monotonic() - started, 3)})
    finally:
        if args.keep:
            print(f"sandbox: {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)

    ok = all(r["status"] in ("passed", "skipped") for r in results)
    report = {"mode": "local", "root": str(root), "model": args.model, "ok": ok,
              "results": results}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"[{r['status'].upper():7}] {r['id']} ({r['seconds']:.1f}s)"
                  + (f" — {r.get('detail', '').splitlines()[0]}" if r.get("detail") else ""))
            if r["status"] not in ("passed", "skipped"):
                print(r.get("detail") or (r.get("stdout", "") + r.get("stderr", ""))[-4000:],
                      file=sys.stderr)
        passed = sum(r["status"] == "passed" for r in results)
        print(f"{passed}/{len(results)} passed"
              f" ({sum(r['status'] == 'skipped' for r in results)} skipped)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
