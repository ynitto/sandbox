"""ケース一覧を生成し、点検とテスト実行を順に行う。"""
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    lint = os.environ.get("TESTCASE_LINT", "testcase-lint")
    root = Path(__file__).resolve().parent
    commands = (
        ("-f", "json", "-o", "results/cases.json"),
        (),
        ("run",),
    )
    for args in commands:
        try:
            # codd-agent から渡された実行識別子を含め、親の環境を引き継ぐ。
            result = subprocess.run([lint, *args], cwd=root, check=False)
        except OSError as error:
            print(f"testcase-lint を起動できません: {error}", file=sys.stderr)
            return 1
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
