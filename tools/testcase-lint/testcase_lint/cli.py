"""testcase-lint の入口。

    testcase-lint cases.yaml [more.json ...] [--requirements reqs.yaml] [--config testcase-lint.yaml]
                  [--format text|json|markdown] [--output FILE] [--fail-on error|warning|info|never]
                  [--min-severity info|warning|error]
    testcase-lint --list-rules

終了コード: 0 = 落とす重さの指摘なし / 1 = あり / 2 = 使い方の誤り・読めない設定。
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from . import __version__
from .checks import run_checks
from .config import load_config
from .loader import FieldMap, load_file, load_requirements
from .model import RULES, SEVERITY_LABEL, SEVERITY_ORDER
from .report import RENDERERS, summary

EXTS = (".json", ".yaml", ".yml")


def expand(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(EXTS)]
        elif any(ch in p for ch in "*?["):
            out += sorted(glob.glob(p, recursive=True))
        else:
            out.append(p)
    return out


def list_rules() -> str:
    lines = ["コード 分類 既定の重さ  規則"]
    for code, (cat, sev, name) in RULES.items():
        lines.append(f"{code}   {cat} {SEVERITY_LABEL[sev]:<5}  {name}")
    return "\n".join(lines) + "\n"


def build_parser():
    p = argparse.ArgumentParser(
        prog="testcase-lint",
        description="自然文のテストケース（JSON / YAML）の重複・矛盾・漏れ・不備を、LLM を使わずに指摘する。")
    p.add_argument("paths", nargs="*", help="ケースのファイル・フォルダ・glob（.json / .yaml / .yml）")
    p.add_argument("-r", "--requirements", help="要件一覧（JSON / YAML）。ケースのない要件を「漏れ」として出す")
    p.add_argument("-c", "--config", help="設定ファイル（規則の止め方・語の追加など）")
    p.add_argument("-f", "--format", choices=sorted(RENDERERS), default="text")
    p.add_argument("-o", "--output", help="結果を書き出すファイル（既定は標準出力）")
    p.add_argument("--fail-on", choices=("error", "warning", "info", "never"), default="error",
                   help="この重さ以上の指摘があれば終了コード 1（既定 error）")
    p.add_argument("--min-severity", choices=("info", "warning", "error"), default="info",
                   help="この重さ未満の指摘は出さない")
    p.add_argument("--list-rules", action="store_true", help="規則の一覧を出す")
    p.add_argument("--version", action="version", version=f"testcase-lint {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_rules:
        sys.stdout.write(list_rules())
        return 0
    if not args.paths:
        build_parser().print_usage(sys.stderr)
        sys.stderr.write("testcase-lint: 点検するファイルを指定してください\n")
        return 2
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"testcase-lint: 設定を読めません: {e}\n")
        return 2
    fmap = FieldMap(cfg.fields)

    files = expand(args.paths)
    if not files:
        sys.stderr.write("testcase-lint: 点検する .json / .yaml が見つかりません\n")
        return 2
    cases, findings = [], []
    for path in files:
        display = os.path.relpath(path) if not os.path.isabs(path) or path.startswith(os.getcwd()) else path
        display = display.replace(os.sep, "/")
        cs, fs = load_file(path, display, fmap, start_index=len(cases))
        cases += cs
        findings += fs
    for f in findings:
        f.severity = cfg.severity(f.code) or ""
    findings = [f for f in findings if f.severity]

    reqs, req_lines, req_display = None, {}, ""
    if args.requirements:
        try:
            reqs, req_lines = load_requirements(args.requirements, fmap)
        except Exception as e:  # noqa: BLE001 - 何が壊れていても利用者には同じ伝え方をする
            sys.stderr.write(f"testcase-lint: 要件一覧を読めません: {e}\n")
            return 2
        req_display = os.path.relpath(args.requirements).replace(os.sep, "/")

    findings += run_checks(cases, cfg, reqs, req_display, req_lines)
    floor = SEVERITY_ORDER[args.min_severity]
    findings = [f for f in findings if SEVERITY_ORDER[f.severity] >= floor]

    s = summary(findings, len(cases), len(files))
    out = RENDERERS[args.format](findings, s)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        sys.stderr.write(f"testcase-lint: {args.output} に書き出しました\n")
    else:
        sys.stdout.write(out)

    if args.fail_on == "never":
        return 0
    limit = SEVERITY_ORDER[args.fail_on]
    return 1 if any(SEVERITY_ORDER[f.severity] >= limit for f in findings) else 0
