#!/usr/bin/env python3
"""任意のランナーを実行し、JUnit XML を個別テストの共通結果に変換する。標準ライブラリのみ。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"リポジトリ内の相対パスを指定してください: {value!r}")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"リポジトリ内の相対パスを指定してください: {value}")
    path = (root / value).resolve()
    path.relative_to(root.resolve())
    return path


def run(args: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    roots = {args.side: root}
    for repo in args.repo:
        name, sep, folder = repo.partition("=")
        if not sep or not name or name in roots:
            raise ValueError("--repo は重複しない 名前=パス です")
        roots[name] = Path(folder).resolve()
    data = json.loads(Path(args.bindings).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("bindings"), list):
        raise ValueError('対応表は {"version": 1, "bindings": [...]} の形です')
    targets = {}
    inputs = {}
    identities = set()
    for binding in data["bindings"]:
        case = binding["case"]
        side = case.get("side", args.side)
        if side not in roots or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("case.side の --repo と、空でない case.id が必要です")
        case_path = local(roots[side], case["file"])
        for target in binding["targets"]:
            if target["suite"] != args.suite:
                continue
            if not isinstance(target.get("selector"), str) or not target["selector"]:
                raise ValueError("target.selector が必要です")
            file = local(root, target["file"])
            key = (target["file"], target["selector"])
            identity = (side, case["file"], case["id"], key)
            if identity in identities:
                raise ValueError(f"対応が重複しています: {identity}")
            identities.add(identity)
            inputs[case_path], inputs[file] = sha(case_path), sha(file)
            targets.setdefault(key, []).append({"side": side, "file": case["file"], "id": case["id"], "sha256": inputs[case_path]})
    if not targets:
        raise ValueError(f"suite {args.suite!r} の対応がありません")
    output = Path(args.output).resolve()
    report = None if args.junit == "-" else Path(args.junit).resolve()
    protected = {*inputs, Path(args.bindings).resolve()}
    if output in protected or report in protected or (report and output == report):
        raise ValueError("出力・JUnitレポートを入力ファイルと同じパスにはできません")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("-- の後にテストコマンドを指定してください")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.unlink(missing_ok=True)
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if report and proc.stdout:
        sys.stdout.write(proc.stdout)
    xml = report.read_text(encoding="utf-8") if report else proc.stdout
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("DTD/ENTITY を含むJUnitレポートには対応していません")
    tree = ET.fromstring(xml)
    records = []
    for node in tree.iter():
        if node.tag.split("}")[-1] != "testcase":
            continue
        name, cls = node.get("name", ""), node.get("classname", "")
        selectors = {name, f"{cls}::{name}"} if cls else {name}
        status = "passed"
        reported_status = node.get("status")
        if reported_status not in (None, "run", "passed", "failed", "skipped", "notrun", "disabled"):
            raise ValueError(f"JUnitのstatusが不正です: {reported_status}")
        children = {child.tag.split("}")[-1] for child in node}
        if children & {"failure", "error"} or reported_status == "failed":
            status = "failed"
        elif "skipped" in children or reported_status in ("skipped", "notrun", "disabled"):
            status = "skipped"
        detail = "\n".join((child.get("message") or child.text or "").strip() for child in node
                           if child.tag.split("}")[-1] in ("failure", "error"))
        records.append((selectors, node.get("file"), status, detail))
    for path, expected in inputs.items():
        if sha(path) != expected:
            raise ValueError(f"テスト実行中に入力が変わりました: {path}")
    items = []
    for (file, selector), cases in targets.items():
        matches = []
        details = []
        for selectors, reported_file, status, detail in records:
            if selector not in selectors:
                continue
            if reported_file:
                if Path(reported_file).is_absolute():
                    same = Path(reported_file).resolve() == local(root, file)
                else:
                    same = local(root, reported_file) == local(root, file)
                if not same:
                    continue
            elif sum(1 for f, s in targets if s in selectors) > 1:
                raise ValueError(f"JUnitにfileが無く、同名テストの対応を特定できません: {selector}")
            matches.append(status)
            if detail:
                details.append(detail)
        if not matches:
            continue
        status = "failed" if "failed" in matches else "skipped" if "skipped" in matches else "passed"
        items.append({"id": selector, "kind": "behavior", "suite": args.suite, "file": file,
                      "status": status, "test_sha256": inputs[local(root, file)], "cases": cases,
                      **({"error": "\n".join(details), "path": file} if details else {})})
    result = {"version": 1, "purpose": "execution", "run_id": os.environ.get("TEST_EVIDENCE_RUN_ID") or uuid.uuid4().hex,
              "inputs_digest": os.environ.get("TEST_EVIDENCE_INPUTS_DIGEST") or hashlib.sha256(
                  json.dumps({str(p): h for p, h in inputs.items()}, sort_keys=True).encode()).hexdigest(),
              "root": os.path.relpath(root, output.parent).replace(os.sep, "/"), "items": items}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing = len(items) != len(targets) or any(item["status"] != "passed" for item in items)
    if missing:
        reported = {(item["file"], item["id"]) for item in items}
        for file, selector in targets.keys() - reported:
            print(f"test-evidence: 対応先の結果がありません: {file} / {selector}", file=sys.stderr)
        for item in items:
            if item["status"] != "passed":
                print(f"test-evidence: {item['status']}: {item['file']} / {item['id']}" +
                      (f"\n{item['error']}" if item.get("error") else ""), file=sys.stderr)
    return proc.returncode if proc.returncode > 0 else 1 if proc.returncode < 0 or missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--junit", required=True, help="ランナーが書くXMLのパス。- は標準出力")
    parser.add_argument("--output", required=True)
    parser.add_argument("--side", default="", help="このリポジトリを表す側の識別子")
    parser.add_argument("--repo", action="append", default=[], help="別リポジトリのケース: 名前=パス")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    try:
        return run(parser.parse_args())
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError, subprocess.TimeoutExpired) as e:
        print(f"test-evidence: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
