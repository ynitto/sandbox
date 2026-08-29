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
# worker_eval へ透過する腕の条件（dest, flag）。ここに無い軸で腕を変えると、条件が
# manifest に残らないまま結果だけが results/ に残る——計画 2026-08-22 §4.2 A3。
WORKER_ARM = (("tasks", "--tasks"), ("agent_policy", "--agent-policy"),
              ("num_ctx", "--num-ctx"), ("num_predict", "--num-predict"),
              ("max_rounds", "--max-rounds"),
              ("temperature", "--temperature"), ("top_p", "--top-p"),
              ("top_k", "--top-k"), ("resample", "--resample"))


def worker_arm(args: argparse.Namespace) -> dict:
    """指定された腕の条件だけ（flag → 値）。未指定は含めない（worker_eval の既定に委ねる）。"""
    return {flag: getattr(args, dest) for dest, flag in WORKER_ARM
            if getattr(args, dest, None) is not None}


def command_for(unit: str, args: argparse.Namespace, out: Path) -> tuple[list[str], dict[str, str]]:
    env: dict[str, str] = {}
    if unit == "coverage":
        cmd = [sys.executable, str(HERE / "coverage_eval.py"),
               "--output", str(out / "coverage.json")]
    elif unit == "worker":
        env["WORKER_EVAL_DIR"] = str(out)
        cmd = [sys.executable, str(HERE / "worker_eval.py"), "--model", args.model,
               "--cli", args.cli, "--repeat", str(args.repeat), "--wall", str(args.wall)]
        # 腕の条件は**指定されたものだけ**を透過する。未指定を既定値で埋めて渡すと、
        # worker_eval が区別している「未宣言」と「既定値を明示」が同じ argv になる。
        for flag, value in worker_arm(args).items():
            cmd += [flag, str(value)]
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
    # worker 単位の腕（未指定なら worker_eval の既定のまま。値の検証は worker_eval 側）。
    ap.add_argument("--tasks", help="worker_eval --tasks（例: T1gate,T2gate,T3gate）")
    ap.add_argument("--agent-policy", choices=("off", "gemma4-e4b-reliability-v1"))
    ap.add_argument("--num-ctx", type=int)
    ap.add_argument("--num-predict", type=int)
    ap.add_argument("--max-rounds", type=int,
                    help="ツールループの呼び出し回数上限（agent-ollama 経路のみ）")
    ap.add_argument("--temperature", type=float)
    ap.add_argument("--top-p", type=float)
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--resample", type=int, metavar="N",
                    help="worker_eval --resample（引き直し腕。sampling の宣言が要る）")
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
        # 腕の条件。キーは worker_eval の flag そのまま（command.txt と同じ語で引ける）。
        # 空 dict は「どの軸も宣言していない＝worker_eval の既定」の意味で、null と同じではない。
        "worker_arm": worker_arm(args),
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
