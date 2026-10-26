"""単体(Python)とCLIのE2E(Node.js)を同じケースと結果形式でつなぐ実行例。"""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parents[1] / "run.py"
LINT = HERE.parents[2] / "testcase-lint/bin/testcase-lint.js"


def main():
    os.chdir(HERE)
    result = subprocess.run(["node", str(LINT), "-f", "json", "-o", "results/cases.json"])
    if result.returncode:
        return result.returncode
    commands = {
        "unit": [sys.executable, "run_unit.py"],
        "e2e": ["node", "--test", "--test-reporter=junit", "tests/e2e.test.cjs"],
    }
    codes = []
    for suite, command in commands.items():
        result = subprocess.run([sys.executable, str(RUNNER), "--bindings", "test-bindings.json", "--suite", suite,
                                 "--junit", "-", "--output", f"results/{suite}.json", "--", *command],
                                env={**os.environ, "PYTHON": sys.executable})
        codes.append(result.returncode)
    return 1 if any(codes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
