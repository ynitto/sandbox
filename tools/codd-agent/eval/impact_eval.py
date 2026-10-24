#!/usr/bin/env python3
"""固定したリポジトリで影響分析と計画検査の手間を測る。LLM・ネットワークは使わない。"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
FIXTURE = HERE / "impact_cases.json"
BASELINE = HERE / "impact_baseline.json"
MODE = "text-and-path"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True, timeout=30).stdout.strip()


def fixture_files() -> dict[str, dict[str, str]]:
    """impact-recall.md の印刷・プレビューを固定。正解は別の JSON に置く。"""
    app = {
        "src/print/PrintButton.tsx": "export function PrintButton(sourceId?: string) { return sourceId; }\n",
        "src/print/usePrint.ts": "import { PrintButton } from './PrintButton';\nexport function usePrint(sourceId?: string) { return PrintButton(sourceId); }\n",
        "src/preview/usePreviewModel.ts": "export function usePreviewModel() { return { ready: true }; }\n",
        # 同じ名前でも別の意味のコード。拾ったらノイズとして数える。
        "src/import/source.ts": "export function importSource(sourceId: string) { return sourceId; }\n",
    }
    app.update({f"src/preview/Preview{i:02}.tsx":
                "import { usePreviewModel } from './usePreviewModel';\n"
                f"export function Preview{i:02}() {{ return usePreviewModel(); }}\n"
                for i in range(1, 26)})
    docs = {
        "docs/design/preview.md": "# Preview\n\n## usePreviewModel\n\n状態を返す。実装は `src/preview/`。\n",
        "docs/design/print.md": "# Print\n\n## sourceId\n\n印刷する文書のID。実装は `src/print/`。\n",
        "docs/design/import.md": "# Import\n\nimportSource の識別子。実装は `src/import/`。\n",
    }
    docs.update({f"docs/specs/screen/screen{i:02}.md":
                 f"# Screen {i}\n\n画面名は usePreviewModel。\n" for i in range(1, 7)})
    return {"app": app, "doc": docs}


def suite_digest() -> str:
    content = {"cases": json.loads(FIXTURE.read_text(encoding="utf-8")),
               "files": fixture_files(), "mode": MODE}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def target_files(codd, item: str, own: str, ref: str) -> set[str]:
    """judge が扱う対象だけを読む。添えられた本文のパスは数えない。"""
    targets = set()
    for target in codd.pending_target(item).split(","):
        target = target.strip().strip("`")
        target = re.sub(r":\d+$", "", target)
        targets.add(target if target.startswith(ref + ":") else f"{own}:{target}")
    return targets


def run_case(case: dict) -> dict:
    """1 試行は別プロセスで呼ぶ。cwd・環境・キャッシュを他の試行と共有しない。"""
    with tempfile.TemporaryDirectory(prefix="codd-impact-eval-") as tmp:
        root = Path(tmp)
        # グローバルの git 設定や利用者のスキルを持ち込まない。
        os.environ.update({"HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
                           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                           "GIT_AUTHOR_NAME": "codd-eval", "GIT_AUTHOR_EMAIL": "eval@example.invalid",
                           "GIT_COMMITTER_NAME": "codd-eval", "GIT_COMMITTER_EMAIL": "eval@example.invalid"})
        init = load_module("codd_eval_init", TOOL / "init.py")
        files = fixture_files()
        log = io.StringIO()
        with redirect_stdout(log), redirect_stderr(log):
            for name, contents in files.items():
                repo = root / name
                repo.mkdir()
                git(repo, "init", "-q", "-b", "main")
                for rel, body in contents.items():
                    path = repo / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(body, encoding="utf-8")
                git(repo, "add", "-A")
                git(repo, "commit", "-qm", "fixture")
            for name, side, ref in (("app", "impl", "doc"), ("doc", "design", "app")):
                repo = root / name
                init.init_repo(repo, side, [f"{ref}=../{ref}"], discover=False, agents=())
                cfg_path = repo / ".statemachine/codd/codd.json"
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg["graphify"] = "off"
                cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
                git(repo, "add", "-A")
                git(repo, "commit", "-qm", "install codd")
        own = case["own"]
        ref = "doc" if own == "app" else "app"
        repo = root / own
        os.chdir(repo)
        codd = load_module("codd_eval_machine", repo / ".statemachine/codd/codd.py")
        # 評価条件を固定する。外部検索・グラフの評価ではなく、文字列・パス探索の基準値。
        codd.find_tool = lambda name: None
        bodies = {heading: "なし" for heading in codd.PLAN_HEADINGS}
        bodies.update({"## やりたいこと": case["request"],
                       "## 自分の変更案": f"- {case['seed']} — {case['change']}",
                       "## テストの変更案": "- 変更不要: 固定サンプルにはテストが無い"})
        plan = repo / ".plans/2026-10-01-0000-eval.md"
        plan.parent.mkdir(exist_ok=True)
        plan.write_text("# 変更の計画\n\n" + "\n\n".join(f"{h}\n\n{b}" for h, b in bodies.items()) + "\n",
                        encoding="utf-8")
        required = set(case["required"])
        universe = {f"{name}:{rel}" for name, contents in files.items() for rel in contents}
        if not required or not required <= universe or f"{own}:{case['seed']}" in required:
            raise ValueError(f"{case['id']}: 正解は初期計画のファイルを除く実在パスで指定してください")
        checks, pending_rows, pending_files = 0, 0, set()
        candidates = set()
        started = time.perf_counter()
        with redirect_stdout(log), redirect_stderr(log):
            def command(*argv):
                # 通常の CLI はコマンドごとに別プロセス。印のメモも同じ寿命にする。
                codd._STAMPS.clear()
                args = codd.build_parser().parse_args(list(argv))
                ctx = codd.Ctx(repo)
                if args.cmd == "verify-plan":
                    codd.mark_run_start(ctx)
                rc = codd.COMMANDS[args.cmd](ctx, args)
                if args.cmd in codd.RUN_START_COMMANDS or args.cmd == "guide":
                    codd.mark_run_start(ctx)
                return rc, ctx

            for argv in (("explore", "--term", case["term"]), ("guide", "--all")):
                if command(*argv)[0] != 0:
                    raise RuntimeError(log.getvalue())
            # 固定した判断役。新しい候補や形式の失敗を勝手に承認せず、評価を失敗させる。
            for _ in range(5):
                checks += 1
                rc, ctx = command("verify-plan")
                candidates.update(f"{key or own}:{rel}" for key, rel in ctx.__dict__.get("_found", set()))
                _, parsed = codd.sections(plan.read_text(encoding="utf-8"), codd.PLAN_HEADINGS)
                pending = codd.pending_items(parsed)
                pending_rows += len(pending)
                if rc == 0:
                    break
                if rc != 1 or not pending:
                    raise RuntimeError(f"{case['id']}: 未判断以外で計画検査が停止\n{log.getvalue()}")
                for heading, item in pending:
                    targets = target_files(codd, item, own, ref)
                    if not targets <= universe:
                        raise RuntimeError(f"{case['id']}: 判断データに無い対象: {targets - universe}")
                    pending_files.update(targets)
                    candidates.update(targets)
                    ident = codd.PENDING_ID.search(item)
                    if not ident:
                        raise RuntimeError(f"番号の無い未判断: {item}")
                    ids = "U" + ident.group(1)
                    if heading == "## 参照先のその他" and targets & required:
                        # 関係する参照先は、各ファイルを根拠に移す。
                        text = plan.read_text(encoding="utf-8")
                        citations = "\n".join(f"- {case['term']} の調査対象（根拠: {p}）" for p in sorted(targets & required))
                        text = text.replace("## 参照先の前提\n", "## 参照先の前提\n\n" + citations + "\n", 1)
                        plan.write_text(text, encoding="utf-8")
                        # この固定サンプルでは関係する参照先とノイズの混在を認めない。
                        if targets - required:
                            raise RuntimeError("関係する参照先とノイズの束が混在しています")
                        judgment = ("judge", ids, "--drop")
                    elif heading == "## 影響範囲" and targets <= required:
                        judgment = ("judge", ids, "--fix", "変更内容を反映する")
                    else:
                        judgment = ("judge", ids, "--skip", "固定サンプルの判断: 呼び出し方・記述の変更は不要")
                    if command(*judgment)[0] != 0:
                        raise RuntimeError(log.getvalue())
            else:
                raise RuntimeError(f"{case['id']}: 5 回で計画検査を通過しませんでした\n{log.getvalue()}")
        elapsed = time.perf_counter() - started
        candidates.discard(f"{own}:{case['seed']}")
        missed = sorted(required - candidates)
        work = json.loads((repo / ".codd/work.json").read_text(encoding="utf-8"))
        if work.get("plan_checks") != checks or work.get("pending", 0) != pending_rows:
            raise RuntimeError("検査の作業記録と評価結果が一致しません")
        return {"id": case["id"], "required_files": sorted(required),
                "found_files": sorted(candidates), "missed_files": missed,
                "noise_files": sorted(candidates - required),
                "recall": (len(required) - len(missed)) / len(required),
                "noise_count": len(candidates - required), "pending_rows": pending_rows,
                "pending_files": len(pending_files), "plan_checks": checks, "elapsed_seconds": elapsed}


def compare(report: dict, baseline: dict, max_time_ratio: float | None = None) -> list[str]:
    """各ケースを比較し、平均で取りこぼしや負担の悪化を隠さない。"""
    for key in ("schema_version", "suite_digest", "mode"):
        if report.get(key) != baseline.get(key):
            raise ValueError(f"基準値の {key} が一致しません。条件を確認して基準値を明示的に更新してください")
    previous = {row["id"]: row for row in baseline["cases"]}
    current = {row["id"] for row in report["cases"]}
    if len(previous) != len(baseline["cases"]) or len(current) != len(report["cases"]) or set(previous) != current:
        raise ValueError("基準値のケース一覧が一致しません")
    regressions = []
    for row in report["cases"]:
        old = previous[row["id"]]
        for metric in ("recall", "noise_count", "pending_rows", "pending_files", "plan_checks"):
            before, after = old[metric], row[metric]
            if not isinstance(before, (int, float)) or not math.isfinite(before) or before < 0 or (metric == "recall" and before > 1):
                raise ValueError(f"不正な基準値: {row['id']} {metric}")
            if (after < before if metric == "recall" else after > before):
                regressions.append(f"{row['id']}: {metric} {before} -> {after}")
        if max_time_ratio is not None:
            before = old["elapsed_seconds"]
            if not isinstance(before, (int, float)) or not math.isfinite(before) or before <= 0:
                raise ValueError(f"不正な時間の基準値: {row['id']}")
            if row["elapsed_seconds"] > before * max_time_ratio:
                regressions.append(f"{row['id']}: elapsed_seconds {before:.3f} -> {row['elapsed_seconds']:.3f}")
    return regressions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3, help="ケースごとの独立試行数（既定 3、時間は中央値）")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path, help="測定結果を JSON に保存")
    parser.add_argument("--write-baseline", action="store_true", help="測定値を基準値に保存（比較しない）")
    parser.add_argument("--max-time-ratio", type=float, help="時間の悪化も検出する倍率（例: 1.5）")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repeat < 1 or (args.max_time_ratio is not None and
                           (not math.isfinite(args.max_time_ratio) or args.max_time_ratio < 1)):
        parser.error("repeat は 1 以上、max-time-ratio は有限の 1 以上を指定してください")
    if args.output and args.output.resolve() == args.baseline.resolve():
        parser.error("output と baseline は別のファイルを指定してください（基準値の保存は --write-baseline）")
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if args.worker:
        case = next(c for c in cases if c["id"] == args.worker)
        print(json.dumps(run_case(case), ensure_ascii=False))
        return 0
    try:
        results = []
        for case in cases:
            runs = []
            for _ in range(args.repeat):
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", case["id"]],
                                        capture_output=True, text=True, encoding="utf-8", timeout=180)
                if result.returncode:
                    raise RuntimeError(f"{case['id']}: 評価を実行できませんでした\n{result.stderr}")
                runs.append(json.loads(result.stdout))
            stable = [{k: v for k, v in row.items() if k != "elapsed_seconds"} for row in runs]
            if any(row != stable[0] for row in stable[1:]):
                raise RuntimeError(f"{case['id']}: 独立試行の測定値が一致しません")
            results.append({**stable[0], "elapsed_seconds": statistics.median(r["elapsed_seconds"] for r in runs)})
        report = {"schema_version": 1, "suite_digest": suite_digest(), "mode": MODE,
                  "source_sha256": hashlib.sha256((TOOL / "machine/codd.py").read_bytes()).hexdigest(),
                  "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                  "git": git(TOOL, "--version")},
                  "repeat": args.repeat, "cases": results}
        failures = [f"{r['id']}: 未検出 {', '.join(r['missed_files'])}" for r in results if r["missed_files"]]
        baseline = None
        if not args.write_baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            failures += compare(report, baseline, args.max_time_ratio)
        report["regressions"] = failures
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.write_baseline:
            if failures:
                raise RuntimeError("取りこぼしがあるため基準値を保存しません: " + "; ".join(failures))
            args.baseline.parent.mkdir(parents=True, exist_ok=True)
            args.baseline.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("case | recall | noise | pending rows/files | checks | seconds (median)")
        for row in results:
            print(f"{row['id']} | {row['recall']:.1%} | {row['noise_count']} | "
                  f"{row['pending_rows']}/{row['pending_files']} | {row['plan_checks']} | {row['elapsed_seconds']:.3f}")
        if baseline:
            for row, old in ((row, next(b for b in baseline['cases'] if b['id'] == row['id'])) for row in results):
                print(f"  {row['id']} delta: recall {row['recall'] - old['recall']:+.1%}, "
                      f"noise {row['noise_count'] - old['noise_count']:+}, "
                      f"pending rows {row['pending_rows'] - old['pending_rows']:+}, "
                      f"checks {row['plan_checks'] - old['plan_checks']:+}, "
                      f"seconds {row['elapsed_seconds'] - old['elapsed_seconds']:+.3f}")
        for failure in failures:
            print("REGRESSION " + failure, file=sys.stderr)
        print("基準値を保存しました" if args.write_baseline else ("FAIL" if failures else "PASS"))
        return 1 if failures else 0
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
