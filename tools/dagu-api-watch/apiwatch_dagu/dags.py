"""Dagu の DAG を書き出す `dags` コマンド。

取得元ごとの DAG（fetch → evaluate → dispatch）・再送の DAG（evaluate → dispatch → reconcile）・整理の DAG（gc）。
Dagu v2.25.2 の dag.schema.json に合う書式（type: graph + depends、action: exec の argv）で書く。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from apiwatch import VERSION
from apiwatch.config import Config
from apiwatch.util import EXIT_OK, log


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _step(name: str, argv: list[str], *, depends: str | None, timeout: int, retry: dict | None) -> list[str]:
    lines = [f"  - name: {name}"]
    if depends:
        lines.append(f"    depends: [{depends}]")
    lines += ["    action: exec", "    with:", f"      command: {_q(argv[0])}", "      args:"]
    lines += [f"        - {_q(a)}" for a in argv[1:]]
    lines.append(f"    timeout_sec: {timeout}")
    if retry:
        lines.append("    retry_policy:")
        lines.append(f"      limit: {retry['limit']}")
        lines.append(f"      interval_sec: {retry['interval_sec']}")
        if retry.get("backoff"):
            lines.append("      backoff: true")
            lines.append(f"      max_interval_sec: {retry['max_interval_sec']}")
        if retry.get("exit_code"):
            lines.append(f"      exit_code: {json.dumps(retry['exit_code'])}")
    return lines


def _header(cfg: Config, desc: str, schedule: str, opts) -> list[str]:
    lines = [f"# dagu-api-watch {VERSION} が生成（api_watch.py dags）。手で直さず、設定を変えて作り直してください。",
             f"# 設定: {cfg.path}",
             f"description: {_q(desc)}",
             f"schedule: {_q(schedule)}",
             "type: graph",
             f"hist_retention_days: {opts.hist_retention_days}"]
    if opts.dotenv:
        lines.append("dotenv:")
        lines += [f"  - {_q(d)}" for d in opts.dotenv]
    lines.append("steps:")
    return lines


def generate(cfg: Config, out_dir: Path, python: str, script: Path, opts) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = [python, str(script), "--config", str(cfg.path)]
    if cfg.connections.path is not None:
        base += ["--connections", str(cfg.connections.path)]
    prefix = opts.prefix
    written = []

    def write(name: str, lines: list[str]) -> None:
        path = out_dir / f"{prefix}-{name}.yaml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)

    for name, src in cfg.sources.items():
        fetch_timeout = int(max(300, src.timeout_sec * src.retry_attempts * 4 + src.max_wait_sec + 120))
        lines = _header(cfg, f"{name} を取得し、ルールで判定して受け渡す", src.schedule, opts)
        lines += _step("fetch", base + ["fetch", "--source", name], depends=None, timeout=fetch_timeout,
                       retry={"limit": 3, "interval_sec": 60, "backoff": True, "max_interval_sec": 600,
                              "exit_code": [3]})
        lines += _step("evaluate", base + ["evaluate", "--source", name], depends="fetch", timeout=600,
                       retry={"limit": 2, "interval_sec": 10})
        lines += _step("dispatch", base + ["dispatch", "--source", name], depends="evaluate", timeout=600, retry=None)
        write(name, lines)
    lines = _header(cfg, "未判定の取得結果の判定と、渡せていない変更の再送・後続処理の完了の確認",
                    opts.dispatch_schedule, opts)
    lines += _step("evaluate", base + ["evaluate"], depends=None, timeout=600, retry=None)
    lines += _step("dispatch", base + ["dispatch"], depends="evaluate", timeout=600, retry=None)
    lines += _step("reconcile", base + ["reconcile"], depends="dispatch", timeout=300, retry=None)
    write("dispatch", lines)
    lines = _header(cfg, "保持期間を過ぎた取得結果・イベントを整理する", opts.maintenance_schedule, opts)
    lines += _step("gc", base + ["gc"], depends=None, timeout=1800, retry=None)
    write("maintenance", lines)
    return written


def cmd_dags(cfg: Config, store, a) -> int:
    import apiwatch
    default = Path(apiwatch.__file__).resolve().parent.parent / "api_watch.py"
    script = Path(a.script).resolve() if a.script else default
    for p in generate(cfg, Path(a.out).expanduser().resolve(), a.python or sys.executable, script, a):
        log(f"書きました: {p}")
    return EXIT_OK


def register(add) -> None:
    sp = add("dags", cmd_dags, "Dagu の DAG を書き出す（取得元ごとの取得・再送・整理）")
    sp.add_argument("--out", required=True)
    sp.add_argument("--python", help="DAG が使う Python（既定: いま動いている Python）")
    sp.add_argument("--script", help="DAG が呼ぶ api_watch.py（既定: apiwatch と同じ場所の api_watch.py）")
    sp.add_argument("--prefix", default="api-watch")
    sp.add_argument("--dotenv", action="append", default=[], help="DAG に読み込ませる環境変数のファイル（繰り返し可）")
    sp.add_argument("--dispatch-schedule", default="* * * * *", help="再送の DAG の起動間隔（既定: 毎分）")
    sp.add_argument("--maintenance-schedule", default="17 3 * * *", help="整理の DAG の起動間隔（既定: 毎日 3:17）")
    sp.add_argument("--hist-retention-days", type=int, default=14, help="Dagu の実行履歴を残す日数")
