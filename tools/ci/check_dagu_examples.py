#!/usr/bin/env python3
"""各ツールの `dags/*.example.yaml`（Dagu のワークフローの例）が、そのまま置ける形かを見る。

利用者はコピーしてパスを直すだけで使うので、Dagu に読ませる前に分かる崩れをここで止める:
YAML として読める・description と 5 項目の schedule がある・ステップ名が重ならない・depends が
前に書いたステップを指す・exec のステップに command がある・exec が展開しない `~` を書いていない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def example_files(root: Path = ROOT) -> "list[Path]":
    return sorted(root.glob("tools/*/dags/*.example.yaml"))


def check_doc(doc: object) -> "list[str]":
    if not isinstance(doc, dict):
        return ["YAML の一番上がマップではない"]
    errors: list[str] = []
    if not str(doc.get("description") or "").strip():
        errors.append("description が無い")
    schedule = doc.get("schedule")
    if not isinstance(schedule, str) or len(schedule.split()) != 5:
        errors.append(f"schedule が 5 項目の cron ではない: {schedule!r}")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["steps が無い"]
    seen: set[str] = set()
    for step in steps:
        name = step.get("name") if isinstance(step, dict) else None
        if not name:
            errors.append(f"name の無いステップ: {step!r}")
            continue
        if name in seen:
            errors.append(f"{name}: ステップ名が重なっている")
        for dep in step.get("depends") or []:
            if dep not in seen:
                errors.append(f"{name}: depends の {dep} が前に無い")
        seen.add(name)
        if step.get("action") == "exec":
            command = (step.get("with") or {}).get("command")
            if not command:
                errors.append(f"{name}: exec に command が無い")
            args = (step.get("with") or {}).get("args") or []
            for value in [command, step.get("working_dir"), *args]:
                if isinstance(value, str) and value.startswith("~"):
                    errors.append(f"{name}: exec は ~ を展開しない: {value}")
    return errors


def main() -> int:
    failed = 0
    files = example_files()
    for path in files:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(f"{path.relative_to(ROOT)}: YAML として読めない: {exc}")
            failed += 1
            continue
        for error in check_doc(doc):
            print(f"{path.relative_to(ROOT)}: {error}")
            failed += 1
    print(f"{len(files)} 本の例を見た。崩れ {failed} 件")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
