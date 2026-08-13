#!/usr/bin/env python3
"""agent-tools の処理単位を同じ条件で測り、結果を run ごとに束ねる。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from eval_io import new_run_dir, write_json

HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "results"
UNITS = ("coverage", "worker", "judge", "retrieval")


def command_for(unit: str, args: argparse.Namespace, out: Path) -> tuple[list[str], dict[str, str]]:
    env: dict[str, str] = {}
    if unit == "coverage":
        cmd = [sys.executable, str(HERE / "coverage_eval.py"),
               "--output", str(out / "coverage.json")]
    elif unit == "worker":
        env["WORKER_EVAL_DIR"] = str(out)
        cmd = [sys.executable, str(HERE / "worker_eval.py"), "--model", args.model,
               "--cli", args.cli, "--repeat", str(args.repeat), "--wall", str(args.wall)]
    elif unit == "judge":
        env["JUDGE_EVAL_DIR"] = str(out)
        base_cli = "ollama" if args.cli == "agent-ollama" else args.cli
        cmd = [sys.executable, str(HERE / "judge_eval.py"), "--model", args.model,
               "--base-cli", base_cli, "--repeat", str(args.repeat),
               "--wall", str(args.wall)]
    else:
        cmd = [sys.executable, str(HERE / "retrieval_eval.py"), "--model", args.embedding_model,
               "--output", str(out / "metrics.json")]
        if args.tfidf_only:
            cmd.append("--tfidf-only")
    return cmd, env


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="生成モデル（比較軸なので必ず明示する）")
    ap.add_argument("--units", default=",".join(UNITS),
                    help=f"実行単位のカンマ区切り: {','.join(UNITS)}")
    ap.add_argument("--cli", choices=("agent-ollama", "aider"), default="agent-ollama")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--wall", type=float, default=600)
    ap.add_argument("--embedding-model", default="bge-m3")
    ap.add_argument("--tfidf-only", action="store_true")
    ap.add_argument("--label", default="", help="run 名へ加えるメモ（例: baseline）")
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--dry-run", action="store_true", help="コマンドと manifest だけ作る")
    args = ap.parse_args()
    units = [x.strip() for x in args.units.split(",") if x.strip()]
    unknown = sorted(set(units) - set(UNITS))
    if unknown:
        ap.error(f"未知の単位: {', '.join(unknown)}")

    run = new_run_dir(args.results_dir, args.model, args.label)
    manifest = {
        "schema_version": 1, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": args.model, "cli": args.cli, "repeat": args.repeat, "wall": args.wall,
        "embedding_model": args.embedding_model, "units": units, "status": "running",
    }
    write_json(run / "manifest.json", manifest)
    failed = False
    for unit in units:
        out = run / unit
        out.mkdir()
        cmd, extra_env = command_for(unit, args, out)
        rendered = shlex.join(cmd)
        (out / "command.txt").write_text(rendered + "\n", encoding="utf-8")
        print(f"[{unit}] {rendered}", flush=True)
        if args.dry_run:
            continue
        with (out / "console.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(cmd, env={**os.environ, **extra_env}, stdout=log,
                                    stderr=subprocess.STDOUT, text=True)
        write_json(out / "status.json", {"returncode": result.returncode})
        failed |= result.returncode != 0
    manifest["status"] = "dry-run" if args.dry_run else "failed" if failed else "complete"
    manifest["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(run / "manifest.json", manifest)
    print(f"results: {run}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
