"""GitLab MR の差分を決定的なルールで検査する change-watch プラグイン。"""

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import sys
from bisect import bisect_right
from pathlib import Path, PurePosixPath

_spec = importlib.util.spec_from_file_location("gitlab_mr", Path(__file__).with_name("gitlab_mr.py"))
_mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mr)

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")
ARM_SCHEMA = re.compile(r'"\$schema"\s*:\s*"[^"\n]*deploymentTemplate', re.I)
CFN_VERSION = re.compile(r'^\s*["\']?AWSTemplateFormatVersion["\']?\s*:', re.M)
COMMENT = re.compile(r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(?:\#|//).*""")
DEFAULT_LABEL = "needs-extra-approval"

# 配列と YAML のブロックリストは、リテラルだけを読む。
LITERAL = r"""(?:"[^"\n]*"|'[^'\n]*'|[^\s,\[\]{}"']+)"""


def property_pattern(keys):
    return re.compile(
        rf"""(?<![\w'".-])["']?(?P<key>{keys})["']?\s*[:=][ \t]*"""
        rf"""(?P<value>\[\s*{LITERAL}(?:[,\s]+{LITERAL})*[,\s]*\]|(?:\n[ \t]*-[ \t]+{LITERAL})+|{LITERAL})""",
        re.I,
    )


CIDR = property_pattern(
    "cidr_blocks|ipv6_cidr_blocks|CidrIp|CidrIpv6|cidr_ipv[46]|"
    "sourceAddressPrefix(?:es)?|source_address_prefix(?:es)?"
)
ACCESS = property_pattern("access")
PUBLIC = property_pattern(
    "allowBlobPublicAccess|allow_blob_public_access|allow_nested_items_to_be_public|"
    "allow_public_access|public_access_enabled|publicAccess|public_access|"
    "container_access_type|access_control|acl|"
    "BlockPublicAcls|BlockPublicPolicy|IgnorePublicAcls|RestrictPublicBuckets|"
    "block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets"
)
IAM = property_pattern("actions?|resources?|dataActions")
ADMIN = re.compile(
    r"""(?i)(?<![\w-])(?:arn:[\w-]+:iam::aws:policy/AdministratorAccess|"""
    r"""AdministratorAccess|8e3af657-a8ff-443c-a75c-2fe8c4bcb635)(?![\w-])"""
)
NETWORK = re.compile(
    r"""(?ix)
    \bresource\s+(?:"(?P<tf>(?:aws|azurerm|google)_[\w]+)"|
        [\w]+\s+'(?P<bicep>Microsoft\.Network/[^'\s]+)')|
    ["']?\btype["']?\s*:\s*["']?(?P<template>AWS::(?:EC2|NetworkFirewall|WAFv2)::[\w]+|
        Microsoft\.Network/[\w/]+)
    """
)
NETWORK_NAME = re.compile(
    r"security_?group|network_?acl|firewall|vpc|vnet|virtual_?network|subnet|route_?table|network_?security", re.I
)
DIRECTION = re.compile(r"""(?ix)
    (?<![\w])(?P<block>ingress|egress)\s*\{|
    ["']?\b(?:type|direction)["']?\s*[:=]\s*["']?(?P<value>ingress|egress|inbound|outbound)\b|
    \b(?P<resource>SecurityGroupIngress|SecurityGroupEgress|aws_vpc_security_group_ingress_rule|
        aws_vpc_security_group_egress_rule)\b
    """
)
RULES = {
    "open-ingress": ("high", "全 IP を対象にする受信 CIDR"),
    "public-storage": ("high", "ストレージの公開を許可する設定"),
    "iam-wildcard": ("high", "IAM の Action / Resource にワイルドカード"),
    "iam-admin": ("high", "管理者権限に相当する識別子"),
    "network-change": ("low", "ネットワークリソースの変更"),
}


def hunks(diff):
    """ヘッダーと、新側の (行番号, 追加か, テキスト) を hunk ごとに返す。"""
    rows = []
    header = ""
    number = None
    for line in diff.splitlines():
        match = HUNK.match(line)
        if match:
            if rows:
                yield header, rows
            number, header = int(match[1]), match[2]
            rows = []
        elif number is not None and line.startswith(("+", " ")):
            rows.append((number, line[0] == "+", line[1:]))
            number += 1
    if rows:
        yield header, rows


def file_kind(path, source=""):
    path = PurePosixPath(path.lower())
    if path.suffix == ".tf":
        return "terraform"
    if path.suffix == ".bicep":
        return "bicep"
    if path.suffix == ".json" and (
        ARM_SCHEMA.search(source) or re.fullmatch(r"(?:azuredeploy|arm[-_.]template|deployment[-_.]template)(?:[-_.][\w.-]+)?\.json", path.name)
    ):
        return "arm"
    if path.suffix in (".yaml", ".yml") and (
        CFN_VERSION.search(source) or {"cloudformation", "cfn"}.intersection(path.parts[:-1])
        or re.fullmatch(r"(?:cloudformation|cfn)(?:[-_.][\w.-]+)?\.ya?ml", path.name)
    ):
        return "cloudformation"
    return None


def public_value(key, value):
    key = key.lower().replace("_", "")
    value = value.lower()
    if key.startswith(("blockpublic", "ignorepublic", "restrictpublic")):
        return value == "false"
    return value in {"true", "blob", "container", "public-read", "public-read-write", "publicread", "publicreadwrite"}


def nearby_match(matches, position):
    return next((match for match in reversed(matches) if match.start() <= position), matches[0] if matches else None)


def findings_for_hunk(path, kind, header, rows):
    added = {offset: number for offset, (number, is_added, _) in enumerate(rows) if is_added}
    if not added:
        return []
    lines = [COMMENT.sub(lambda match: match[1] or "", row[2]) for row in rows]
    source = "\n".join(lines)
    findings = {}

    def record(rule, offset, evidence):
        if offset in added:
            findings.setdefault((offset, rule), {
                "path": path, "line": added[offset], "format": kind, "rule": rule,
                "severity": RULES[rule][0], "evidence": evidence,
            })

    context = header + "\n" + source
    directions = list(DIRECTION.finditer(context))
    access_modes = list(ACCESS.finditer(context)) if kind in {"bicep", "arm"} else []
    strings = list(COMMENT.finditer(source))
    string_starts = [string.start() for string in strings]
    for pattern in (CIDR, PUBLIC, IAM):
        for match in pattern.finditer(source):
            index = bisect_right(string_starts, match.start("key")) - 1
            if index >= 0:
                string = strings[index]
                if match.start("key") < string.end() and (string.start(), string.end()) != (
                    match.start("key") - 1, match.end("key") + 1
                ):
                    continue
            key = match["key"]
            if pattern is CIDR:
                position = len(header) + 1 + match.start()
                direction = nearby_match(directions, position)
                access = nearby_match(access_modes, position)
                if direction and re.search(r"egress|outbound", direction[0], re.I):
                    continue
                if access and access["value"].strip('"\'').lower() == "deny":
                    continue
            for token in re.finditer(LITERAL, match["value"]):
                value = token[0].strip('"\'')
                if pattern is CIDR and value in {"0.0.0.0/0", "::/0"}:
                    rule = "open-ingress"
                elif pattern is PUBLIC and public_value(key, value):
                    rule = "public-storage"
                elif pattern is IAM and "*" in value and not value.startswith(("/", "[", "${")):
                    rule = "iam-wildcard"
                else:
                    continue
                value_line = source.count("\n", 0, match.start("value") + token.start())
                record(rule, value_line, f"{key} = {value}" if pattern is not IAM else key)
    for match in ADMIN.finditer(source):
        record("iam-admin", source.count("\n", 0, match.start()), match[0])

    # hunk ヘッダーには Git が直前の宣言を載せることがある。
    network = next((name for match in NETWORK.finditer(context)
                    for name in match.groups() if name and NETWORK_NAME.search(name)), None)
    if network and not findings:
        record("network-change", next(iter(added)), network)
    return [finding for _, finding in sorted(findings.items())]


def inspect(diffs, read_file):
    findings, targets, skipped = [], [], []
    for diff in sorted(diffs, key=lambda item: item.get("new_path") or item.get("old_path") or ""):
        path = diff.get("new_path") or diff.get("old_path")
        if not path or diff.get("deleted_file"):
            continue
        body = diff.get("diff") or ""
        sections = list(hunks(body))
        kind = file_kind(path, "\n".join(row[2] for _, rows in sections for row in rows))
        unavailable = diff.get("collapsed") or diff.get("too_large")
        if not kind and PurePosixPath(path.lower()).suffix in {".json", ".yaml", ".yml"} and (
            unavailable or any(added for _, rows in sections for _, added, _ in rows)
        ):
            kind = file_kind(path, read_file(path))
        if not kind:
            continue
        targets.append(diff)
        if unavailable and not sections:
            skipped.append(path)
        for header, rows in sections:
            findings.extend(findings_for_hunk(path, kind, header, rows))
    identity = sorted({(finding["path"], finding["rule"]) for finding in findings})
    # 欠落したファイルは指紋の材料にせず、読めた差分とルールを比較する。
    readable = [diff for diff in targets if (diff.get("diff") or "").strip()]
    data = [_mr.diff_fingerprint(readable, ""), identity, skipped]
    fingerprint = hashlib.sha256(json.dumps(data).encode()).hexdigest()
    return {"fingerprint": fingerprint, "findings": findings, "skipped_files": skipped}


def marker(result):
    return f'<!-- gitlab-iac:v1:{result["fingerprint"]} -->'


def markdown(result):
    def safe(value):
        return html.escape(str(value)).replace("\n", " ").replace("\r", " ").replace("`", "&#96;")

    lines = [f'### IaC リスク検査 · MR !{result["iid"]}', ""]
    for finding in result["findings"]:
        _, message = RULES[finding["rule"]]
        lines.append(f'- **{finding["severity"]}** `{safe(finding["path"])}:{finding["line"]}`'
                     f' — {message}（`{safe(finding["evidence"])}`）')
    if not result["findings"]:
        lines.append("確認できた差分に該当する変更はありません。")
    for path in result["skipped_files"]:
        lines.append(f"- 差分を確認できませんでした（`{safe(path)}`）")
    return "\n".join([*lines, "", marker(result)])


def post(gl, result, body, label, enabled):
    if not result["findings"]:
        return "no-findings"
    if not enabled:
        return "dry-run"
    if any(marker(result) in (note.get("body") or "").splitlines() for note in gl.notes(result["iid"])):
        return "duplicate"
    endpoint = f'/merge_requests/{result["iid"]}'
    # ラベル追加を先に済ませ、コメント送信失敗後も同じコマンドを再試行できるようにする。
    gl.call("PUT", endpoint, body={"add_labels": label})
    gl.call("POST", endpoint + "/notes", body={"body": body})
    return "posted"


def run(args):
    url, token = os.environ.get("GITLAB_URL"), os.environ.get("GITLAB_TOKEN")
    if not all((url, token, args.project)):
        print("環境変数 GITLAB_URL・GITLAB_TOKEN・GITLAB_PROJECT を設定してください", file=sys.stderr)
        return _mr.USAGE
    label = os.environ.get("MR_REVIEW_IAC_LABEL", DEFAULT_LABEL).strip()
    if args.cmd == "post" and (not label or "," in label or not label.isprintable()):
        print("MR_REVIEW_IAC_LABEL に単一のラベル名を指定してください", file=sys.stderr)
        return _mr.USAGE
    gl = _mr.GitLab(url, token, args.project)
    head = None

    def read_file(path):
        nonlocal head
        if head is None:
            mr = gl.mr(args.iid)
            head = (mr.get("diff_refs") or {}).get("head_sha") or mr.get("sha")
            if not head:
                raise ValueError("MR の head SHA を取得できませんでした")
        return gl.file(path, head)

    try:
        result = inspect(gl.mr_diffs(args.iid), read_file)
        result["iid"] = int(args.iid)
        body = markdown(result)
        if args.cmd == "post":
            enabled = os.environ.get("MR_REVIEW_IAC_POST") == "1"
            result["post_status"] = post(gl, result, body, label, enabled)
            result["label"] = label
            print(f'{result["post_status"]} (label: {label})', file=sys.stderr)
        output = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else body
        if args.output:
            Path(args.output).write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0
    except (OSError, ValueError, _mr.GitLabError) as error:
        print(str(error), file=sys.stderr)
        return _mr.FAILED


def _add_arguments(parser):
    commands = parser.add_subparsers(dest="cmd", required=True)
    for command, default in (("check", "json"), ("post", "markdown")):
        sub = commands.add_parser(command)
        sub.add_argument("iid", type=_mr._iid)
        sub.add_argument("--project", default=os.environ.get("GITLAB_PROJECT", ""))
        sub.add_argument("--format", choices=("json", "markdown"), default=default)
        sub.add_argument("--output", type=Path, help="出力先ファイル（既定は標準出力）")


def register(api):
    _add_arguments(api.add_command("iac", lambda _cfg, _store, args: run(args),
                                   "GitLab の MR の IaC リスクを検査する", needs_config=False))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="GitLab の MR の IaC リスクを検査する")
    _add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
