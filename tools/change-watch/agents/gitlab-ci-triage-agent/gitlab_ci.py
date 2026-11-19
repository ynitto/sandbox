"""GitLab の失敗したパイプラインを、決まったパターンで診断する。標準ライブラリのみ。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_JOBS = 20  # 1 回の取得で読むログの数
TRACE_TAIL_BYTES = 1 << 20  # ログごとに保持する末尾
SCAN_LINES = 5000  # パターンを調べる末尾の行数
SCAN_LINE_CHARS = 2000  # 正規表現に渡す 1 行の上限
EXCERPT_MAX_LINES = 30
EXCERPT_MAX_CHARS = 2000
EXCERPT_LINE_CHARS = 300
MAX_GROUPS = 5  # 本文に詳しく出す原因の数
BODY_MAX_CHARS = 12000  # 投稿する本文の上限
DEDUP_SCAN_NOTES = 300  # 印を探す最近のコメント数
WORK_KEEP_DAYS = 7
HTTP_TIMEOUT = 60
TRACE_TIMEOUT = 120

LABELS = {
    "configuration": "設定ミス", "infrastructure": "インフラ・クォータ",
    "dependency": "依存関係", "test_failure": "テスト失敗", "unknown": "不明",
}
ORDER = {category: rank for rank, category in enumerate(LABELS)}
ACTIONS = {
    "configuration": "CI の設定・変数や、ジョブの実行環境を確かめてください。",
    "infrastructure": "ランナーの状態と利用できるリソース・枠を確かめてください。",
    "dependency": "依存の指定・ロックファイル・取得元を確かめてください。",
    "test_failure": "失敗したテストの出力を読み、実装と期待値を確かめてください。",
    "unknown": "ジョブのログを確かめてください。同じ形が繰り返すなら、パターンを足すと次から分類できます。",
}
YAML_PATTERN = "gitlab-ci-yaml-invalid"


def builtin(id, category, priority, title, *, regex=None, reasons=None, action=None, fix=None, context=None):
    match = {"regex": regex} if regex else {"failure_reason": reasons}
    entry = dict(id=id, category=category, priority=priority, title=title, match=match,
                 next_action=action or ACTIONS[category])
    if fix is not None:
        entry["fix"] = fix
    if context is not None:
        entry["context"] = context
    return entry


BUILTIN_PATTERNS = {"version": 1, "mask": [], "patterns": [
    # テスト失敗
    builtin(
        'pytest-failed', 'test_failure', 40, 'pytest のテストが失敗',
        regex=[
            '^=+ FAILURES =+$',
            '^FAILED \\S+::\\S+',
            '^=+ .*\\b\\d+ failed\\b.*=+$',
        ],
    ),
    builtin(
        'python-unittest-failed', 'test_failure', 40, 'unittest のテストが失敗',
        regex=[
            '^FAILED \\((failures|errors)=\\d+',
        ],
    ),
    builtin(
        'jest-failed', 'test_failure', 40, 'Jest のテストが失敗',
        regex=[
            '^Tests:\\s+\\d+ failed',
            '^\\s*● .+ › .+',
        ],
    ),
    builtin(
        'go-test-failed', 'test_failure', 40, 'Go のテストが失敗',
        regex=[
            '^--- FAIL: (?P<test>\\S+)',
            '^FAIL\\s+\\S+\\s+[\\d.]+s$',
        ],
    ),
    builtin(
        'junit-failed', 'test_failure', 40, 'JUnit のテストが失敗',
        regex=[
            'Tests run: \\d+, Failures: [1-9]',
            'Tests run: \\d+, Failures: \\d+, Errors: [1-9]',
            'There are test failures',
        ],
    ),
    builtin(
        'rspec-failed', 'test_failure', 40, 'RSpec のテストが失敗',
        regex=[
            '^\\d+ examples?, [1-9]\\d* failures?',
        ],
    ),
    # 依存関係
    builtin(
        'npm-package-not-found', 'dependency', 50, 'npm パッケージが見つからない',
        regex=[
            'npm (ERR!|error) code (E404|ETARGET)',
            "npm (ERR!|error) 404\\s+'(?P<package>[^']+)'",
        ],
        fix='パッケージ名と版の指定を、レジストリにあるものに直してください。',
    ),
    builtin(
        'npm-resolve-conflict', 'dependency', 50, 'npm の依存が競合',
        regex=[
            'npm (ERR!|error) code ERESOLVE',
        ],
        fix='競合している依存の版の指定をそろえ、ロックファイルを更新してください。',
    ),
    builtin(
        'npm-lockfile-out-of-sync', 'dependency', 50, 'npm のロックファイルが一致しない',
        regex=[
            '`npm ci` can only install packages when your package\\.json and package-lock\\.json',
            'Your lockfile needs to be updated',
        ],
        fix='ロックファイルを作り直してコミットしてください。',
    ),
    builtin(
        'pip-no-matching-distribution', 'dependency', 50, 'pip が指定の版を見つけられない',
        regex=[
            'ERROR: Could not find a version that satisfies the requirement (?P<package>[^\\s(]+)',
            'ERROR: No matching distribution found for (?P<package>\\S+)',
        ],
        fix='{package} の版の指定を、取得元にある版に直してください。',
        context={"before": 3, "after": 2},
        action="requirements の版の指定と、パッケージの取得元（index）に届くかを確かめてください。",
    ),
    builtin(
        'pip-resolution-impossible', 'dependency', 50, 'pip の依存を解決できない',
        regex=[
            'ResolutionImpossible',
            'conflicting dependencies',
        ],
    ),
    builtin(
        'python-module-not-found', 'dependency', 50, 'Python のモジュールが見つからない',
        regex=[
            "ModuleNotFoundError: No module named '(?P<module>[^']+)'",
        ],
        fix='{module} を依存の一覧に足してください。',
    ),
    builtin(
        'maven-artifact-not-found', 'dependency', 50, 'Maven の依存を解決できない',
        regex=[
            'Could not resolve dependencies for project',
            'Could not find artifact (?P<artifact>\\S+)',
        ],
    ),
    builtin(
        'gradle-unresolved', 'dependency', 50, 'Gradle の依存を解決できない',
        regex=[
            'Could not resolve all (files|dependencies) for configuration',
        ],
    ),
    builtin(
        'go-module-missing', 'dependency', 50, 'Go モジュールが見つからない',
        regex=[
            'missing go\\.sum entry',
            'go: .*: unknown revision',
        ],
        fix='`go mod tidy` で依存を整理し、変更をコミットしてください。',
    ),
    builtin(
        'bundler-gem-not-found', 'dependency', 50, 'Bundler の gem が見つからない',
        regex=[
            "Could not find gem '(?P<gem>[^']+)'",
        ],
    ),
    builtin(
        'image-not-found', 'dependency', 50, 'イメージを取得できない',
        regex=[
            'manifest for (?P<image>\\S+) not found',
            'pull access denied for (?P<image>\\S+)',
        ],
    ),
    # インフラ・クォータ
    builtin(
        'gitlab-quota-exceeded', 'infrastructure', 90, 'CI の利用枠を超過',
        reasons=[
            'ci_quota_exceeded',
        ],
    ),
    builtin(
        'gitlab-runner-failure', 'infrastructure', 90, 'GitLab ランナーの障害',
        reasons=[
            'runner_system_failure',
            'scheduler_failure',
            'api_failure',
            'data_integrity_failure',
            'runner_unsupported',
            'no_matching_runner',
        ],
    ),
    builtin(
        'gitlab-job-timeout', 'infrastructure', 90, 'ジョブの制限時間・容量を超過',
        reasons=[
            'stuck_or_timeout_failure',
            'job_execution_timeout',
            'trace_size_exceeded',
        ],
    ),
    builtin(
        'job-timeout-log', 'infrastructure', 70, 'ジョブの制限時間を超過',
        regex=[
            'ERROR: Job failed: execution took longer than',
        ],
    ),
    builtin(
        'runner-system-failure-log', 'infrastructure', 70, 'ランナーの準備に失敗',
        regex=[
            'ERROR: Job failed \\(system failure\\)',
            'ERROR: Preparation failed',
            'Cannot connect to the Docker daemon',
        ],
    ),
    builtin(
        'disk-full', 'infrastructure', 70, 'ディスクの空き容量が不足',
        regex=[
            'No space left on device',
            'ENOSPC',
        ],
    ),
    builtin(
        'out-of-memory', 'infrastructure', 70, 'メモリが不足',
        regex=[
            'OOMKilled',
            'Out of memory',
            'JavaScript heap out of memory',
            'java\\.lang\\.OutOfMemoryError',
            'ERROR: Job failed: exit code 137',
        ],
    ),
    builtin(
        'network-unreachable', 'infrastructure', 70, 'ネットワーク接続に失敗',
        regex=[
            'Could not resolve host',
            'Temporary failure in name resolution',
            'TLS handshake timeout',
            '[Cc]onnection (timed out|reset by peer)',
            '\\b(ETIMEDOUT|ECONNRESET|EAI_AGAIN)\\b',
            '50[234] (Bad Gateway|Service Unavailable|Gateway Time-?out)',
        ],
    ),
    builtin(
        'rate-limited', 'infrastructure', 70, 'レート制限に到達',
        regex=[
            'toomanyrequests',
            '429 Too Many Requests',
            'pull rate limit',
        ],
    ),
    builtin(
        'resource-quota-exceeded', 'infrastructure', 70, 'リソースの利用枠を超過',
        regex=[
            'exceeded quota',
            'Quota exceeded',
        ],
    ),
    # 設定ミス
    builtin(
        'gitlab-ci-yaml-invalid', 'configuration', 95, 'GitLab CI の YAML が無効',
        reasons=[
            'yaml_errors',
        ],
    ),
    builtin(
        'gitlab-job-prerequisite', 'configuration', 90, 'ジョブの前提条件を満たしていない',
        reasons=[
            'missing_dependency_failure',
            'unmet_prerequisites',
            'environment_creation_failure',
            'deployment_rejected',
            'protected_environment_failure',
            'secrets_provider_not_found',
            'invalid_bridge_trigger',
            'insufficient_bridge_permissions',
            'downstream_bridge_project_not_found',
        ],
    ),
    builtin(
        'command-not-found', 'configuration', 60, 'コマンドが見つからない',
        regex=[
            '(?P<command>[\\w./+-]+): (command )?not found$',
            'ERROR: Job failed: exit code 127',
        ],
    ),
    builtin(
        'variable-not-set', 'configuration', 60, '変数が設定されていない',
        regex=[
            '(?P<variable>[A-Za-z_][A-Za-z0-9_]*): unbound variable',
            '(?P<variable>[A-Za-z_][A-Za-z0-9_]*): parameter (null or )?not set',
        ],
    ),
    builtin(
        'auth-failed', 'configuration', 60, '認証または認可に失敗',
        regex=[
            '\\b401 Unauthorized\\b',
            '\\b403 Forbidden\\b',
            'HTTP Basic: Access denied',
            'Permission denied \\(publickey\\)',
            'unauthorized: authentication required',
            'denied: access forbidden',
        ],
    ),
    builtin(
        'script-not-executable', 'configuration', 60, 'スクリプトに実行権限がない',
        regex=[
            '(ba)?sh: .*: Permission denied$',
        ],
    ),
    builtin(
        'git-ref-not-found', 'configuration', 60, 'Git の ref が見つからない',
        regex=[
            "fatal: couldn't find remote ref",
            "pathspec '.*' did not match",
        ],
    ),
    builtin(
        'file-not-found', 'configuration', 20, 'ファイルが見つからない',
        regex=[
            'No such file or directory',
        ],
    ),
]}


class PatternErrors(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, allowed):
    require(isinstance(value, dict), "オブジェクトが必要です")
    extra = value.keys() - set(allowed.split())
    require(not extra, "知らない項目: " + ", ".join(sorted(extra)))


def text_list(value, name, *, nonempty=False):
    require(isinstance(value, list) and (not nonempty or value)
            and all(isinstance(item, str) and item for item in value),
            f"{name}: 文字列の配列が必要です")
    return value


def bounded_int(value, maximum, name):
    require(type(value) is int and 0 <= value <= maximum, f"{name}: 0〜{maximum} の整数が必要です")


def validate_pattern(pattern, seen):
    fields(pattern, "id category title priority match context next_action fix enabled")
    pid = pattern.get("id")
    require(isinstance(pid, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", pid), "id が不正です")
    require(pid not in seen, f"id が重なっています: {pid}")
    seen.add(pid)
    enabled = pattern.get("enabled", True)
    require(type(enabled) is bool, "enabled: 真偽値が必要です")
    if not enabled and pattern.keys() == {"id", "enabled"}:
        return
    category = pattern.get("category")
    require(isinstance(category, str) and category in ORDER and category != "unknown", "category が不正です")
    bounded_int(pattern.get("priority", 50), 100, "priority")
    match = pattern.get("match")
    fields(match, "regex failure_reason job_name stage ignore_case")
    require("regex" in match or "failure_reason" in match, "match.regex か match.failure_reason が必要です")
    require(type(match.get("ignore_case", False)) is bool, "match.ignore_case: 真偽値が必要です")
    groups = set()
    for regex in text_list(match.get("regex", []), "match.regex", nonempty="regex" in match):
        groups.update(re.compile(regex, re.IGNORECASE if match.get("ignore_case") else 0).groupindex)
    if "failure_reason" in match:
        text_list(match["failure_reason"], "match.failure_reason", nonempty=True)
    for key in ("job_name", "stage"):
        if key in match:
            require(isinstance(match[key], str), f"match.{key}: 文字列が必要です")
            re.compile(match[key])
    context = pattern.get("context", {})
    fields(context, "before after")
    for key, count in context.items():
        bounded_int(count, 20, f"context.{key}")
    for key, limit in (("title", 80), ("next_action", 300), ("fix", 600)):
        if key == "fix" and key not in pattern:
            continue
        value = pattern.get(key)
        require(isinstance(value, str) and 0 < len(value) <= limit, f"{key}: {limit} 字までの文字列が必要です")
        if key == "title":
            require(value.splitlines() == [value], "title: 1 行が必要です")
        else:
            for _, field, spec, conversion in string.Formatter().parse(value):
                if field is not None:
                    require(field in groups and not spec and not conversion, f"{key}: {{{field}}} に対応するグループが必要です")


def load_patterns(path=None):
    merged = {p["id"]: p for p in BUILTIN_PATTERNS["patterns"]}
    if not path:
        return {"version": 1, "mask": [], "patterns": list(merged.values())}
    site = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    fields(site, "version mask patterns")
    require(type(site.get("version")) is int and site["version"] == 1, "version: 1 が必要です")
    require(isinstance(site.get("patterns"), list), "patterns: 配列が必要です")
    masks = text_list(site.get("mask", []), "mask")
    errors = []
    for index, regex in enumerate(masks):
        try:
            re.compile(regex)
        except re.error as exc:
            errors.append(f"mask[{index}]: {exc}")
    seen = set()
    for index, pattern in enumerate(site["patterns"]):
        try:
            validate_pattern(pattern, seen)
            merged[pattern["id"]] = pattern
        except (ValueError, re.error) as exc:
            errors.append(f"patterns[{index}]: {exc}")
    if errors:
        raise PatternErrors("\n".join(errors))
    return {"version": 1, "mask": masks, "patterns": list(merged.values())}


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SECTION = re.compile(r"section_(?:start|end):\d+:[\w.-]+")
KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
PREFIX_TOKEN = re.compile(
    r"\bgl(?:pat|dt|rt|cbt|ptt|ft|imt|agent|oas|soat)-[0-9A-Za-z_-]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_[A-Za-z0-9_]{22,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
AUTH_HEADER = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization|private-token|job-token|deploy-token|x-api-key|x-auth-token)\b\s*[:=]\s*).*"
)
URL_AUTH = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
SECRET_ASSIGNMENT = re.compile(
    r"""(?i)(["']?\b[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)[\w.-]*["']?\s*[=:]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S+)"""
)
LONG_TOKEN = re.compile(r"[A-Za-z0-9+_-]{40,}={0,2}")


def mask_lines(lines, token="", masks=(), *, in_key=False):
    def long_token(match):
        value = match[0]
        if re.fullmatch(r"[0-9A-Fa-f]+", value):
            return value
        return "***" if all(re.search(chars, value) for chars in ("[A-Z]", "[a-z]", "[0-9]")) else value

    custom = [re.compile(regex) for regex in masks]
    token_forms = ()
    if len(token) >= 8:
        token_forms = dict.fromkeys((urllib.parse.quote(token, safe=""), urllib.parse.quote(token), token))
    result = ["***"] if in_key else []
    for line in lines:
        for value in token_forms:
            line = line.replace(value, "***")
        if not in_key and KEY_BEGIN.search(line):
            result.append("***")
            in_key = True
        if in_key:
            in_key = not KEY_END.search(line)
            continue
        line = PREFIX_TOKEN.sub("***", line)
        line = AUTH_HEADER.sub(lambda m: m[1] + "***" if m[0][len(m[1]):] != "[MASKED]" else m[0], line)
        line = URL_AUTH.sub("://***:***@", line)
        line = SECRET_ASSIGNMENT.sub(lambda m: m[0] if m[2] == "[MASKED]" else m[1] + "***", line)
        line = LONG_TOKEN.sub(long_token, line)
        for regex in custom:
            line = "[MASKED]".join(regex.sub("***", part) for part in line.split("[MASKED]"))
        result.append(line)
    return result


def clean_trace(data, token="", masks=(), clipped=False, in_key=False):
    if clipped:
        first, _, data = data.partition(b"\n")
        # 欠けた行は出さない。その行で鍵が終わる場合は次の行から出す。
        if KEY_END.search(first.decode("utf-8", errors="replace")):
            in_key = False
    lines = []
    for line in data.decode("utf-8", errors="replace").split("\n"):
        line = line.removesuffix("\r").rsplit("\r", 1)[-1]
        lines.append(SECTION.sub("", ANSI.sub("", line)))
    return "\n".join(mask_lines(lines, token, masks, in_key=in_key))


ERROR_LINE = re.compile(r"\b(error|fatal|failed|exception|traceback)\b", re.IGNORECASE)


def excerpt(lines, hit, context):
    if not lines:
        return "", False
    if hit is None:
        footer = next((i for i in range(len(lines) - 1, -1, -1)
                       if lines[i].startswith("ERROR: Job failed")), len(lines))
        center = next((i for i in range(footer - 1, -1, -1)
                       if ERROR_LINE.search(lines[i])), len(lines) - 1)
        before, after = (19, 0) if center == len(lines) - 1 else (15, 5)
    else:
        center = hit
        before, after = context.get("before", 5), context.get("after", 3)
    start, end = max(0, center - before), min(len(lines), center + after + 1)
    selected = []
    truncated = False
    for i in range(start, end):
        line = lines[i].rstrip()
        if not line and (not selected or not selected[-1][1]) and i != center:
            continue
        if len(line) > EXCERPT_LINE_CHARS:
            truncated = True
            line = line[:EXCERPT_LINE_CHARS - 1] + "…"
        selected.append((i, line))
    while selected and not selected[-1][1] and selected[-1][0] != center:
        selected.pop()
    left = right = False
    while selected:
        parts = (["…（省略）"] if left else []) + [line for _, line in selected] + (["…（省略）"] if right else [])
        result = "\n".join(parts).strip("\n")
        if len(parts) <= EXCERPT_MAX_LINES and len(result) <= EXCERPT_MAX_CHARS:
            return result, truncated or left or right
        if center - selected[0][0] >= selected[-1][0] - center:
            selected.pop(0)
            left = True
        else:
            selected.pop()
            right = True
    return "", truncated


def inline(value):
    value = str(value or "")
    value = " ".join(value.replace("`", "").splitlines())
    return "`" + value + "`" if value else ""


def classify(job, trace, patterns):
    lines = trace.splitlines()
    first = max(0, len(lines) - SCAN_LINES)
    scan = [line[:SCAN_LINE_CHARS] for line in lines[first:]]
    hits = []
    for p in patterns:
        if not p.get("enabled", True):
            continue
        match = p["match"]
        if "failure_reason" in match and job.get("failure_reason") not in match["failure_reason"]:
            continue
        if any(key in match and not re.search(match[key], job.get(field) or "")
               for key, field in (("job_name", "name"), ("stage", "stage"))):
            continue
        index, groups = None, {}
        if "regex" in match:
            flags = re.IGNORECASE if match.get("ignore_case") else 0
            regexes = [re.compile(regex, flags) for regex in match["regex"]]
            found = next(((i, m) for i, line in enumerate(scan, first)
                          for regex in regexes if (m := regex.search(line))), None)
            if found is None:
                continue
            index, m = found
            groups = m.groupdict()
        rank = (-p.get("priority", 50), ORDER[p["category"]], index if index is not None else len(lines), p["id"])
        hits.append((rank, p, index, groups))
    hits.sort(key=lambda hit: hit[0])
    if hits:
        _, p, index, groups = hits[0]
    else:
        p = {"category": "unknown", "title": "どのパターンにも当たりませんでした", "next_action": ACTIONS["unknown"]}
        index, groups = None, {}
    snippet, truncated = excerpt(lines, index, p.get("context", {}))
    values = {name: inline(" ".join(value.replace("`", "").splitlines())[:80]) or "（不明）"
              for name, value in groups.items() if value is not None}
    fields = {key: p[key].format_map(_Captures(values)) for key in ("next_action", "fix") if key in p}
    return dict(category=p["category"], pattern=p.get("id"), title=p["title"],
                job={key: job.get(key) for key in ("id", "name", "stage", "web_url", "failure_reason")},
                same_cause=[], line=None if index is None else index + 1,
                excerpt=snippet, excerpt_truncated=truncated,
                also_matched=[hit[1]["id"] for hit in hits[1:]], **fields)


class _Captures(dict):
    def __missing__(self, key):
        return "（不明）"


def aggregate(pipeline, jobs, traces, patterns):
    groups = {}
    others = []
    counts = Counter()
    fingerprints = []
    source_patterns = patterns
    yaml_error = pipeline.get("yaml_errors")
    if yaml_error:
        jobs = [dict(id=0, name="(pipeline)", failure_reason="yaml_errors")]
        traces = {0: yaml_error}
        # GitLab が返す YAML エラーはジョブとは別の診断材料。
        source_patterns = [next(p for p in BUILTIN_PATTERNS["patterns"] if p["id"] == YAML_PATTERN)]
    priorities = {p["id"]: p.get("priority", 50) for p in source_patterns}
    for job in sorted(jobs, key=lambda j: j["id"]):
        if job.get("allow_failure"):
            continue
        if job.get("skipped_trace"):
            others.append(dict(name=job["name"], category="unknown", skipped_trace=True))
            continue
        result = classify(job, traces.get(job["id"], ""), source_patterns)
        category, pattern = result["category"], result["pattern"]
        counts[category] += 1
        fingerprints.append(f"{job['name']}\t{category}\t{pattern or '-'}")
        key = pattern or job["id"]
        if key in groups:
            groups[key]["same_cause"].append(job["name"])
        else:
            groups[key] = result
    ordered = sorted(groups.values(), key=lambda g: (
        ORDER[g["category"]], -priorities.get(g["pattern"], 0), g["job"]["id"]))
    category = min(counts, key=ORDER.get, default="unknown")
    known = any(g["category"] == category and "fix" in g for g in ordered)
    overflow = [
        dict(name=name, category=g["category"], skipped_trace=False)
        for g in ordered[MAX_GROUPS:] for name in [g["job"]["name"], *g["same_cause"]]
    ]
    return dict(version=1, pipeline={key: pipeline.get(key) for key in
                                    ("id", "sha", "ref", "source", "web_url", "project_id", "tag")},
                category=category, mixed=len(counts) > 1, known=known,
                fp=hashlib.sha256("\n".join(sorted(fingerprints)).encode("utf-8")).hexdigest()[:12],
                counts={key: counts[key] for key in LABELS if key in counts},
                groups=ordered[:MAX_GROUPS], others=overflow + others)


MARK = re.compile(r"^<!--\s*ci-triage-agent:diagnosis\s+pipeline=(\d+)\s+fp=([0-9a-f]{12})\s*-->")
HEADING = "### CI の失敗の診断"


def link(label, url):
    if not url or not re.match(r"https?://", url):
        return label
    label = label.replace("[", r"\[").replace("]", r"\]")
    url = urllib.parse.quote(url, safe=":/%?&=#@+;,~!$'*-._")
    return f"[{label}]({url})"


def describe_job(name, category=None):
    return inline(name) + (f"（{LABELS[category]}）" if category else "")


def action_line(label, value):
    # 複数行の運用設定でも、行頭の / を GitLab のクイックアクションにしない。
    return f"- **{label}**: " + "\n".join(re.sub(r"^(\s*)/", r"\1\\/", line) for line in value.splitlines())


def render_comment(diagnosis, *, rerun=False, newer=False, project=None):
    pipeline = diagnosis["pipeline"]
    pid = pipeline["id"]
    category = LABELS[diagnosis["category"]]
    header = [
        f"<!-- ci-triage-agent:diagnosis pipeline={pid} fp={diagnosis['fp']} -->",
        f"{HEADING}{'（再実行後）' if rerun else ''}: {category}", "",
    ]
    pipeline_link = link(f"#{pid}", pipeline.get("web_url"))
    source = " @ ".join(filter(None, (inline(pipeline.get("ref")), inline((pipeline.get("sha") or "")[:8]))))
    project_label = f"{inline(project)} の" if project else ""
    total = sum(diagnosis["counts"].values()) + sum(item["skipped_trace"] for item in diagnosis["others"])
    summary = f"{project_label}パイプライン {pipeline_link}" + (f"（{source}）" if source else "")
    yaml_error = any(group["pattern"] == YAML_PATTERN for group in diagnosis["groups"])
    if yaml_error:
        summary += "で CI の設定エラーが発生しました。"
    elif total:
        summary += f"で {total} 件のジョブが失敗しました"
        if diagnosis["mixed"]:
            breakdown = "・".join(f"{LABELS[key]} {count}" for key, count in diagnosis["counts"].items())
            summary += f"（{breakdown}）"
        summary += "。"
    else:
        summary += "で失敗したジョブが見つかりません。パイプラインの画面で下流のパイプラインを確かめてください。"
    header.append(summary)
    if newer:
        header.append("この MR には、このパイプラインより新しいコミットがあります。")
    header.append("")
    sections = []
    fallback = []
    patterns = []
    for group in diagnosis["groups"]:
        job = group["job"]
        name = ".gitlab-ci.yml" if job["name"] == "(pipeline)" else job["name"]
        label = LABELS[group["category"]]
        stage = f"（{inline(job['stage'])}）" if job["stage"] else ""
        title = f"#### {link(inline(name), job['web_url'])}{stage}— {label}: {group['title']}"
        snippet = group["excerpt"]
        if snippet:
            fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", snippet)), default=0))
            evidence = f"{fence}text\n{snippet}\n{fence}"
        else:
            evidence = f"（ログはありません。GitLab が付けた理由: {inline(job['failure_reason'] or '不明')}）"
        parts = [title, "", evidence, "", action_line("次のアクション", group["next_action"])]
        if "fix" in group:
            parts.append(action_line("修正案", group["fix"]))
        if group["same_cause"]:
            parts.append("- 同じ原因のジョブ: " + "・".join(map(inline, group["same_cause"])))
        sections.append("\n".join(parts) + "\n\n")
        fallback.append([describe_job(item, group["category"]) for item in [name, *group["same_cause"]]])
        if group["pattern"]:
            patterns.append(inline(group["pattern"]))
    other_names = [describe_job(item["name"], None if item["skipped_trace"] else item["category"])
                   for item in diagnosis["others"]]
    matches = f"（{'・'.join(patterns)}）" if patterns else ""
    footer = f"<sub>ログの決まった形だけで分類しています{matches}。秘匿情報らしい文字列は `***` に置き換えています。</sub>\n"
    top = "\n".join(header) + "\n"
    for keep in range(len(sections), -1, -1):
        main = top + "".join(sections[:keep])
        others = [name for group in fallback[keep:] for name in group] + other_names
        other_line = f"ほかに失敗したジョブ: {'・'.join(others)}\n\n" if others else ""
        if len(main) + len(other_line) + len(footer) <= BODY_MAX_CHARS:
            return main + other_line + footer
    # Even a name-only list can exceed the limit on pipelines with many jobs.
    other_label = "ほかに失敗したジョブ: "
    budget = BODY_MAX_CHARS - len(top) - len(footer) - len(other_label + "…（省略）\n\n")
    if budget < 0:
        top = "\n".join(header[:3]) + f"\nパイプライン #{pid}（概要は省略）\n\n"
        budget = BODY_MAX_CHARS - len(top) - len(footer) - len(other_label + "…（省略）\n\n")
    names = []
    for name in others:
        if len(name) > budget:
            break
        names.append(name)
        budget -= len(name) + 1
    return top + other_label + "・".join(names + ["…（省略）"]) + "\n\n" + footer


class GitLabError(Exception):
    def __init__(self, message, status=None, *, temporary=False):
        super().__init__(message)
        self.status = status
        self.temporary = temporary or status in {408, 425, 429} or (status is not None and status >= 500)


def origin(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if origin(req.full_url) != origin(newurl):
            raise GitLabError("別の接続先へのリダイレクトを拒否しました")
        if req.get_method() != "GET":
            raise GitLabError("投稿のリダイレクトを拒否しました")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


KEY_BOUNDARY = re.compile(rb"-----(BEGIN|END) [A-Z ]*PRIVATE KEY-----")


def read_trace_tail(response):
    tail = bytearray()
    clipped = in_key = False
    overlap = b""
    while chunk := response.read(64 * 1024):
        tail.extend(chunk)
        excess = len(tail) - TRACE_TAIL_BYTES
        if excess > 0:
            clipped = tail[excess - 1] != 10
            # 保持範囲より前から続く秘密鍵も、終端が無い場合まで伏せる。
            discarded = overlap + tail[:excess]
            for marker in KEY_BOUNDARY.finditer(discarded):
                if marker.end() > len(overlap):
                    in_key = marker[1] == b"BEGIN"
            overlap = discarded[-256:]
            del tail[:excess]
    if clipped:
        first = bytes(tail).partition(b"\n")[0]
        for marker in KEY_BOUNDARY.finditer(overlap + first):
            if marker.end() > len(overlap):
                in_key = marker[1] == b"BEGIN"
    return bytes(tail), clipped, in_key


class GitLab:
    def __init__(self, url, token, project):
        self.url = url.rstrip("/")
        self.token = token
        self.project = project
        self.api = f"{self.url}/api/v4/projects/{urllib.parse.quote(project, safe='')}"
        self.opener = urllib.request.build_opener(SameOriginRedirect())

    def call(self, method, path, query=None, body=None, *, trace=False):
        url = path if path.startswith(("http://", "https://")) else self.api + path
        if origin(url) != origin(self.url):
            raise GitLabError("別の接続先への要求を拒否しました")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"PRIVATE-TOKEN": self.token, "Accept": "text/plain" if trace else "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=TRACE_TIMEOUT if trace else HTTP_TIMEOUT) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                if trace:
                    return read_trace_tail(response), headers
                content = response.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            raise GitLabError(f"GitLab の {method} が HTTP {exc.code} で失敗しました", exc.code) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise GitLabError(f"GitLab の {method} の応答を受け取れませんでした", temporary=True) from None
        try:
            return (json.loads(content) if content.strip() else None), headers
        except (ValueError, UnicodeError):
            raise GitLabError(f"GitLab の {method} の応答が JSON ではありません",
                              temporary=method == "POST") from None

    def get(self, path, query=None):
        return self.call("GET", path, query)[0]

    def optional_get(self, path):
        try:
            return self.get(path)
        except GitLabError as exc:
            if exc.status != 404:
                raise
            return None

    def paginate(self, path, query=None, limit=None):
        page = 1
        count = 0
        while limit is None or count < limit:
            items, headers = self.call("GET", path, {**(query or {}), "per_page": 100, "page": page})
            require(isinstance(items, list), "GitLab の一覧の応答が不正です")
            yield from (items if limit is None else items[:limit - count])
            count += len(items)
            if "x-next-page" in headers:
                next_page = headers["x-next-page"].strip()
                if not next_page.isascii() or not next_page.isdecimal() or int(next_page) <= page:
                    return
                page = int(next_page)
            elif len(items) < 100:
                return
            else:
                page += 1

    def trace(self, job):
        try:
            return self.call("GET", f"/jobs/{job}/trace", trace=True)[0]
        except GitLabError as exc:
            if exc.status != 404:
                raise
            return b"", False, False


def work_root():
    if os.environ.get("CI_TRIAGE_WORK_DIR"):
        return Path(os.environ["CI_TRIAGE_WORK_DIR"]).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        state = Path(os.environ["LOCALAPPDATA"])
    else:
        state = Path.home() / ".local/state"
    return state / "change-watch/gitlab-ci-triage-agent/work"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def collect(gl, pid, directory, patterns):
    root = work_root().resolve()
    resolved = directory.resolve()
    require(resolved != root and resolved.is_relative_to(root),
            "作業ルート配下のフォルダを指定してください")
    require(not directory.is_symlink() and not Path.cwd().is_relative_to(resolved),
            "取得結果専用の作業フォルダを指定してください")
    directory = resolved
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, mode=0o700)
    if root.is_dir():
        cutoff = time.time() - WORK_KEEP_DAYS * 86400
        for child in root.iterdir():
            if (child.name.isascii() and child.name.isdecimal() and child.is_dir() and not child.is_symlink()
                    and not directory.is_relative_to(child.resolve()) and child.stat().st_mtime < cutoff):
                shutil.rmtree(child)
    pipeline = gl.get(f"/pipelines/{pid}")
    require(isinstance(pipeline, dict) and pipeline.get("id") == pid, "パイプラインの応答が不正です")
    if pipeline.get("yaml_errors"):
        pipeline["yaml_errors"] = clean_trace(pipeline["yaml_errors"].encode(), gl.token, patterns["mask"])
    write_json(directory / "pipeline.json", pipeline)
    if pipeline["status"] != "failed":
        write_json(directory / "jobs.json", [])
        return dict(pipeline=pid, failed_jobs=0, yaml_errors=bool(pipeline.get("yaml_errors")), status=pipeline["status"])
    jobs = []
    if not pipeline.get("yaml_errors"):
        failed_jobs = [job for job in gl.paginate(f"/pipelines/{pid}/jobs", {"scope[]": "failed"})
                       if not job.get("allow_failure")]
        require(all(type(job.get("id")) is int and job["id"] > 0 for job in failed_jobs),
                "ジョブ ID が不正です")
        for index, src in enumerate(sorted(failed_jobs, key=lambda job: job["id"])):
            job = {key: src.get(key) for key in ("id", "name", "stage", "failure_reason", "web_url")}
            job.update(trace=None, skipped_trace=index >= MAX_JOBS)
            if not job["skipped_trace"]:
                data, clipped, in_key = gl.trace(job["id"])
                cleaned = clean_trace(data, gl.token, patterns["mask"], clipped, in_key)
                if cleaned.strip():
                    job["trace"] = f"trace-{job['id']}.log"
                    (directory / job["trace"]).write_text(cleaned, encoding="utf-8", newline="\n")
            jobs.append(job)
    write_json(directory / "jobs.json", jobs)
    return dict(pipeline=pid, failed_jobs=len(jobs), yaml_errors=bool(pipeline.get("yaml_errors")))


def diagnose(pid, directory, patterns):
    pipeline = read_json(directory / "pipeline.json")
    require(pipeline["id"] == pid, "パイプライン ID が作業フォルダの内容と一致しません")
    jobs = read_json(directory / "jobs.json")
    traces = {}
    for job in jobs:
        if job.get("trace"):
            require(job["trace"] == f"trace-{job['id']}.log", "ログのファイル名が不正です")
            traces[job["id"]] = (directory / job["trace"]).read_text(encoding="utf-8")
    result = aggregate(pipeline, jobs, traces, patterns["patterns"])
    write_json(directory / "diagnosis.json", result)
    (directory / "comment.md").write_text(render_comment(result), encoding="utf-8", newline="\n")
    return dict(pipeline=pid, category=result["category"], known=result["known"], jobs=len(jobs), fp=result["fp"])


ISSUE_URL = re.compile(r"^(https?://[^/?#]+)/(.+?)(?:/-)?/(?:issues|work_items)/(\d+)/?(?:[?#].*)?$")


def gitlab_issue_api(url):
    if "/api/v4/" in url:
        return url.rstrip("/")
    match = ISSUE_URL.fullmatch(url)
    require(match, "GitLab の issue の URL を指定してください")
    base, project, iid = match.groups()
    return f"{base}/api/v4/projects/{urllib.parse.quote(project, safe='')}/issues/{iid}"


def find_target(gl, pipeline):
    ref = pipeline.get("ref") or ""
    sha = pipeline.get("sha") or ""
    mr_ref = re.fullmatch(r"refs/merge-requests/([0-9]+)/(head|merge|train)", ref)
    mr = None
    if mr_ref:
        mr = gl.optional_get(f"/merge_requests/{mr_ref[1]}")
    elif ref and not pipeline.get("tag"):
        project_id = pipeline.get("project_id")
        if project_id is None:
            project_id = int(gl.project) if gl.project.isdecimal() else gl.get("")["id"]
        candidates = (mr for mr in gl.paginate("/merge_requests", {"state": "opened", "source_branch": ref})
                      if str(mr.get("source_project_id")) == str(project_id))
        mr = max(candidates, key=lambda mr: int(mr["iid"]), default=None)
    if mr:
        iid = int(mr["iid"])
        head = mr.get("sha") or (mr.get("diff_refs") or {}).get("head_sha")
        return dict(target="mr", ref=f"!{iid}", url=mr.get("web_url") or "",
                    path=f"/merge_requests/{iid}/notes", field="body", newer=bool(head and sha and head != sha))
    sources = {s.strip() for s in os.environ.get("CI_TRIAGE_COMMIT_SOURCES", "push").split(",") if s.strip()}
    if sha and pipeline.get("source") in sources:
        path = "/repository/commits/" + urllib.parse.quote(sha, safe="")
        commit = gl.optional_get(path)
        if commit is not None:
            url = commit.get("web_url")
            if not url:
                project_url = (pipeline.get("web_url") or "").partition("/-/pipelines/")[0]
                url = f"{project_url}/-/commit/{sha}" if project_url else ""
            return dict(target="commit", ref=sha[:8], url=url, path=path + "/comments", field="note")
    hub = os.environ.get("GITLAB_HUB_ISSUE_URL", "").strip()
    if hub:
        require(origin(hub) == origin(gl.url), "ハブの issue は GitLab と同じ接続先を指定してください")
        api = gitlab_issue_api(hub)
        require(not urllib.parse.urlsplit(api).query and not urllib.parse.urlsplit(api).fragment,
                "ハブの issue の URL にクエリやフラグメントは指定できません")
        return dict(target="issue", ref="#" + api.rsplit("/", 1)[-1], url=hub, path=api + "/notes", field="body")
    return None


def comment_created_at(note):
    value = note.get("created_at") or ""
    try:
        created = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return created if created.tzinfo else created.replace(tzinfo=timezone.utc)


def post(gl, pid, directory, dry_run):
    diagnosis = read_json(directory / "diagnosis.json")
    body = (directory / "comment.md").read_text(encoding="utf-8")
    mark = MARK.match(body)
    require(diagnosis["pipeline"]["id"] == pid and mark and mark.groups() == (str(pid), diagnosis["fp"]),
            "診断とコメントの印が一致しません")
    target = find_target(gl, diagnosis["pipeline"])
    if target is None:
        reason = "GITLAB_HUB_ISSUE_URL が設定されていません"
        if dry_run:
            print(f"[dry-run] 投稿先: なし（{reason}）")
            print(f"[dry-run] 本文（{len(body)} 字）:\n{body}", end="")
        else:
            print(json.dumps(dict(posted=False, reason="no_target", target=None, ref=None, url=None)))
        print(reason, file=sys.stderr)
        return 1
    kind, path, field = target["target"], target["path"], target["field"]
    if kind == "commit":
        notes = sorted(gl.paginate(path), key=comment_created_at, reverse=True)[:DEDUP_SCAN_NOTES]
    else:
        notes = gl.paginate(path, {"sort": "desc", "order_by": "created_at"}, DEDUP_SCAN_NOTES)
    found = set()
    for note in notes:
        mark = MARK.match(note.get(field) or "")
        if mark and mark[1] == str(pid):
            found.add(mark[2])
    duplicate = diagnosis["fp"] in found
    body = render_comment(diagnosis, rerun=bool(found) and not duplicate, newer=target.get("newer"),
                          project=gl.project if kind == "issue" else None)
    if dry_run:
        label = {"mr": "MR", "commit": "コミット", "issue": "ハブの issue"}[kind]
        print(f"[dry-run] 投稿先: {label} {target['ref']}（{target['url']}）")
        if duplicate:
            existing = "あり（dry-run でなければ投稿しません）"
        elif found:
            existing = "別の内容であり（dry-run でなければ再実行後として投稿します）"
        else:
            existing = "なし（dry-run でなければ投稿します）"
        print(f"[dry-run] 既存の診断: {existing}")
        print(f"[dry-run] 本文（{len(body)} 字）:\n{body}", end="")
        return 0
    url = target["url"]
    if not duplicate:
        note, _ = gl.call("POST", path, body={field: body})
        if field == "body" and isinstance(note, dict) and note.get("id") is not None:
            url += f"#note_{note['id']}"
    print(json.dumps(dict(posted=not duplicate, reason="duplicate" if duplicate else "posted",
                          target=kind, ref=target["ref"], url=url)))
    return 0


def pipeline_id(value):
    value = value.removeprefix("#")
    if not re.fullmatch(r"[0-9]+", value) or int(value) <= 0:
        raise argparse.ArgumentTypeError("パイプラインの ID は正の整数です")
    return int(value)


def add_arguments(parser):
    parser.add_argument("--project", default=os.environ.get("GITLAB_PROJECT", ""))
    commands = parser.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "diagnose", "post", "triage", "patterns"):
        command = commands.add_parser(name)
        command.add_argument("--project", default=argparse.SUPPRESS)
        if name != "patterns":
            command.add_argument("--dir", type=Path)
        if name not in ("post",):
            command.add_argument("--patterns", default=os.environ.get("CI_TRIAGE_PATTERNS", ""))
        if name in ("triage", "post"):
            command.add_argument("--dry-run", action="store_true")
        if name == "diagnose":
            inputs = command.add_mutually_exclusive_group(required=True)
            inputs.add_argument("pipeline", type=pipeline_id, nargs="?")
            inputs.add_argument("--trace-file", type=Path)
            command.add_argument("--job-name", default="job")
            command.add_argument("--stage", default="")
            command.add_argument("--failure-reason", default="")
        elif name == "patterns":
            command.add_argument("--check", action="store_true")
        else:
            command.add_argument("pipeline", type=pipeline_id)


def run(args):
    cmd = args.cmd
    token = os.environ.get("GITLAB_TOKEN", "")
    try:
        if cmd in ("collect", "post", "triage"):
            url = os.environ.get("GITLAB_URL", "")
            if not url or not token or not args.project:
                print("環境変数 GITLAB_URL・GITLAB_TOKEN・GITLAB_PROJECT を設定してください", file=sys.stderr)
                return 2
            gl = GitLab(url, token, args.project)
        if cmd != "post":
            patterns = load_patterns(args.patterns)
        if cmd == "patterns":
            if not args.check:
                print(json.dumps(patterns, ensure_ascii=False, indent=2))
            return 0
        if cmd == "diagnose" and args.trace_file:
            job = dict(id=1, name=args.job_name, stage=args.stage, failure_reason=args.failure_reason)
            trace = clean_trace(args.trace_file.read_bytes(), token, patterns["mask"])
            result = aggregate(dict(id=0), [job], {1: trace}, patterns["patterns"])
            print(render_comment(result), end="")
            return 0
        pid = args.pipeline
        directory = args.dir.expanduser() if args.dir else work_root() / str(pid)
        if cmd in ("collect", "triage"):
            summary = collect(gl, pid, directory, patterns)
            if cmd == "collect" or "status" in summary:
                print(json.dumps(summary))
                return 0
        if cmd in ("diagnose", "triage"):
            summary = diagnose(pid, directory, patterns)
            if cmd == "diagnose":
                print(json.dumps(summary))
                return 0
        dry_run = args.dry_run or os.environ.get("CI_TRIAGE_DRY_RUN", "").lower() in {"1", "true", "yes", "on"}
        return post(gl, pid, directory, dry_run)
    except (OSError, ValueError, TypeError, KeyError, GitLabError) as exc:
        lines = str(exc).splitlines() if cmd == "patterns" and args.check and isinstance(exc, PatternErrors) else [
            " ".join(str(exc).splitlines())
        ]
        for line in lines:
            print(line.replace(token, "***") if token else line, file=sys.stderr)
        return 3 if isinstance(exc, GitLabError) and exc.temporary else 1


def register(api):
    add_arguments(api.add_command("ci", lambda _cfg, _store, args: run(args),
                                  "GitLab CI の失敗を診断する", needs_config=False))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="gitlab_ci.py", description="GitLab CI の失敗を診断する")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
