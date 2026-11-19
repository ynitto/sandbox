#!/usr/bin/env python3
"""夜間点検の結果を集め、ハブの issue に載せる Markdown を作る。"""

import argparse
from dataclasses import dataclass
from datetime import date
import json
import math
import operator
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


LIMIT = 5
COMPARISONS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt,
               "ge": operator.ge, "lt": operator.lt, "le": operator.le}
SOURCE_TYPES = {"findings_file", "json_report", "log_file", "command"}
JSON_FORMATS = {"testcase-evidence", "repo-sync-report"}


@dataclass
class Result:
    name: str
    source: str
    status: str
    details: list


def positive_number(value, key):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} は正の数で指定してください")
    return value


def patterns(check, key):
    values = check.get(key, [])
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError(f"{key} は正規表現の配列で指定してください")
    return [re.compile(value) for value in values]


def load_checks(path):
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError:
            raise ValueError("YAML を読むには PyYAML が必要です。JSON 設定も使えます") from None
        try:
            config = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML を読めません: {exc}") from exc
    else:
        config = json.loads(content)

    checks = config.get("checks") if isinstance(config, dict) else None
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks に点検を 1 件以上指定してください")
    for index, check in enumerate(checks, 1):
        try:
            validate_check(check, path.parent)
        except (ValueError, TypeError, re.error) as exc:
            raise ValueError(f"checks の {index} 件目: {exc}") from exc
    return checks


def validate_check(check, base):
    if not isinstance(check, dict) or not isinstance(check.get("name"), str) or not check["name"].strip():
        raise ValueError("点検の name を指定してください")
    kind = check.get("type")
    if kind not in SOURCE_TYPES:
        raise ValueError("点検の type を確認してください")
    if kind == "command":
        args = check.get("argv")
        if not isinstance(args, list) or not args or any(
            not isinstance(arg, str) or "\0" in arg for arg in args
        ) or not args[0]:
            raise ValueError("argv に実行ファイルと引数を配列で指定してください")
        positive_number(check.get("timeout_seconds", 10), "timeout_seconds")
        check["cwd"] = base / Path(check.get("cwd", ".")).expanduser()
        return

    path = check.get("path")
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise ValueError("結果の path を指定してください")
    check["path"] = base / Path(path).expanduser()
    check["max_age_hours"] = positive_number(check.get("max_age_hours", 26), "max_age_hours")
    if kind == "findings_file":
        check["ignore_patterns"] = patterns(check, "ignore_patterns")
    if kind == "log_file":
        check["failure_patterns"] = patterns(check, "failure_patterns")
        if not check["failure_patterns"]:
            raise ValueError("failure_patterns に正規表現を 1 件以上指定してください")
        for key in ("run_start_pattern", "run_end_pattern"):
            if key in check:
                if not isinstance(check[key], str):
                    raise ValueError(f"{key} は正規表現で指定してください")
                check[key] = re.compile(check[key])
    if kind == "json_report":
        fmt = check.get("format")
        if fmt is not None and fmt not in JSON_FORMATS:
            raise ValueError("JSON レポートの format を確認してください")
        conditions = check.get("failure_conditions", [])
        if not isinstance(conditions, list) or (fmt is None and not conditions):
            raise ValueError("format または failure_conditions を指定してください")
        for condition in conditions:
            if (not isinstance(condition, dict) or not isinstance(condition.get("path"), str)
                    or not all(condition["path"].split("."))
                    or condition.get("op") not in COMPARISONS or "value" not in condition):
                raise ValueError("failure_conditions の各条件に path、op、value を指定してください")


def summarize_failures(details):
    summary = []
    omitted = 0
    for detail in details:
        if len(summary) < LIMIT:
            summary.append(detail)
        else:
            omitted += 1
    if omitted:
        summary.append(f"ほか {omitted} 件")
    return summary


def testcase_failures(report):
    items = report["items"]
    if report["version"] != 1 or not isinstance(items, list):
        raise ValueError("version 1 の evidence が必要です")
    for item in items:
        if item["kind"] == "behavior":
            if item["status"] not in ("passed", "failed"):
                raise ValueError("evidence の status が不明です")
            if item["status"] == "failed":
                yield f"{item.get('path') or item.get('file') or item['id']} — {item.get('error', '点検に失敗')}"


def repo_sync_failures(report):
    pairs = report["pairs"]
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("同期ペアの結果がありません")
    for pair in pairs:
        name = pair["pair"]
        status = pair["status"]
        if status not in ("ok", "unchanged", "failed", "diverged"):
            raise ValueError("同期ペアの status が不明です")
        issues = []
        for key, label in (("diverged", "分岐"), ("conflicts", "衝突"), ("failed", "失敗")):
            refs = pair[key]
            if not isinstance(refs, list):
                raise ValueError(f"{key} は配列である必要があります")
            if refs:
                issues.append(f"{label}（{len(refs)} 件） {', '.join(summarize_failures(refs))}")
        if pair.get("error"):
            issues.append(pair["error"])
        if status == "failed" or status == "diverged" or issues:
            yield f"{name} — {' / '.join(issues) or status}"


def json_failures(report, check):
    # evidence の合否は生成側の fail-on を反映した behavior に従う。
    fmt = check.get("format")
    if fmt == "testcase-evidence":
        yield from testcase_failures(report)
    elif fmt == "repo-sync-report":
        yield from repo_sync_failures(report)
    for condition in check.get("failure_conditions", []):
        path, op, expected = (condition[k] for k in ("path", "op", "value"))
        actual = report
        for part in path.split("."):
            actual = actual[int(part)] if isinstance(actual, list) and part.isdecimal() else actual[part]
        if COMPARISONS[op](actual, expected):
            yield f"{path} = {actual!r}（{op} {expected!r}）"


def log_failures(stream, check):
    start = check.get("run_start_pattern")
    end = check.get("run_end_pattern")
    started, finished = start is None, end is None
    failures = []
    omitted = 0
    for raw_line in stream:
        line = raw_line.strip()
        if start and start.search(line):
            started, finished = True, end is None
            failures.clear()
            omitted = 0
        if started and (not finished or end is None):
            if any(p.search(line) for p in check["failure_patterns"]):
                if len(failures) < LIMIT:
                    failures.append(line)
                else:
                    omitted += 1
            if end and end.search(line):
                finished = True
    if not started or not finished:
        raise ValueError("ログに実行の開始または完了の記録がありません")
    if omitted:
        failures.append(f"ほか {omitted} 件")
    return failures


def file_failures(check, now):
    with check["path"].open(encoding="utf-8-sig") as stream:
        # 読んでいるファイルの更新時刻を見る。置き換えと競合しても別の版を判定しない。
        age = now - os.fstat(stream.fileno()).st_mtime
        if age > check["max_age_hours"] * 3600:
            raise ValueError(f"結果が古い（{age / 3600:.1f} 時間前）")
        if check["type"] == "json_report":
            return summarize_failures(json_failures(json.load(stream), check))
        if check["type"] == "log_file":
            return log_failures(stream, check)
        lines = (line.strip() for line in stream)
        return summarize_failures(line for line in lines if line
                                  and not re.match(r"^#{1,6}(\s|$)", line)
                                  and not any(p.search(line) for p in check["ignore_patterns"]))


def collect(check, now):
    command = check["type"] == "command"
    source = shlex.join(check["argv"]) if command else str(check["path"])
    try:
        if command:
            proc = subprocess.run(check["argv"], cwd=check["cwd"], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, encoding="utf-8", errors="replace",
                                  timeout=check.get("timeout_seconds", 10))
            details = []
            if proc.returncode:
                details = [f"終了コード {proc.returncode}"]
                details.extend(summarize_failures(line for line in proc.stdout.splitlines() if line.strip()))
        else:
            details = file_failures(check, now)
    except FileNotFoundError:
        return Result(check["name"], source, "unknown",
                      ["コマンドまたは作業ディレクトリがありません" if command else "結果ファイルがありません"])
    except subprocess.TimeoutExpired:
        return Result(check["name"], source, "failed", ["コマンドが制限時間を超えました"])
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        return Result(check["name"], source, "unknown", [str(exc)])
    return Result(check["name"], source, "failed" if details else "ok", details)


def markdown(text):
    # 実行結果をコメントの本文に埋めても、見出しや HTML として解釈させない。
    line = " ".join(str(text).splitlines()).strip()
    if len(line) > 400:
        line = line[:397] + "..."
    return re.sub(r"([\\`*_\[\]<>#])", r"\\\1", line)


def render(results, digest_date):
    lines = [f"## 夜間点検サマリ（{digest_date}）", ""]
    healthy = "、".join(markdown(r.name) for r in results if r.status == "ok")
    if healthy:
        label = "すべて正常" if all(r.status == "ok" for r in results) else "正常"
        lines += [f"✅ {label}：{healthy}", ""]
    for result in results:
        if result.status == "ok":
            continue
        label = "❌ 失敗" if result.status == "failed" else "❓ 未確認（結果なし）"
        lines += [f"### {label}：{markdown(result.name)}", ""]
        lines.extend(f"- {markdown(detail)}" for detail in result.details)
        lines += [f"- 参照先：{result.source}", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="集約の設定ファイル")
    parser.add_argument("--output", type=Path, help="コメント本文の出力先（既定は標準出力）")
    parser.add_argument("--date", type=date.fromisoformat, help="タイトルの日付（YYYY-MM-DD）")
    args = parser.parse_args(argv)
    try:
        checks = load_checks(args.config.resolve())
        now = time.time()
        body = render([collect(check, now) for check in checks], args.date or date.today())
        if args.output:
            args.output.write_text(body, encoding="utf-8")
        else:
            sys.stdout.write(body)
    except (OSError, ValueError, OverflowError, RuntimeError) as exc:
        print(f"集約できません: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
