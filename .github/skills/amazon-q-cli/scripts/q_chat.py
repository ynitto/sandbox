#!/usr/bin/env python3
"""
Amazon Q CLI chat wrapper (cross-platform: Windows / macOS / Linux)

Usage:
  python q_chat.py "prompt text"
  python q_chat.py --file path/to/prompt.txt
  python q_chat.py --agent my-agent "prompt text"
  python q_chat.py --agent my-agent --file path/to/prompt.txt
"""

import argparse
import shutil
import subprocess
import sys


def find_q() -> str | None:
    """Return the path to the q executable, or None if not found."""
    return shutil.which("q")


def build_cmd(agent: str | None, prompt: str) -> list[list[str]]:
    """
    Return a list of candidate command lists to try in order.
    First with --no-interactive, then fallback without it.
    """
    base = ["q", "chat", "--no-interactive", "--trust-all-tools"]
    if agent:
        base += ["--agent", agent]

    fallback = ["q", "chat", "--no-interactive", "--trust-all-tools"]

    return [base + [prompt], fallback + [prompt]]


def run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def main() -> None:
    parser = argparse.ArgumentParser(description="Amazon Q CLI chat wrapper")
    parser.add_argument("prompt", nargs="?", default="", help="Prompt text")
    parser.add_argument("--file", help="Read prompt from file")
    parser.add_argument("--agent", help="Amazon Q agent name")
    args = parser.parse_args()

    # Resolve prompt
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                prompt = f.read()
        except FileNotFoundError:
            print(f"ERROR: Prompt file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
    elif args.prompt:
        prompt = args.prompt
    else:
        print("ERROR: No prompt provided. Pass prompt text or use --file.", file=sys.stderr)
        sys.exit(1)

    # Check q is installed
    if not find_q():
        print("ERROR: Amazon Q CLI (q) is not installed.", file=sys.stderr)
        print("Install:", file=sys.stderr)
        print("  Windows : winget install Amazon.AmazonQ", file=sys.stderr)
        print("  macOS   : brew install amazon-q", file=sys.stderr)
        print("  Linux   : https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-getting-started-installing.html", file=sys.stderr)
        sys.exit(1)

    # Candidate command lists
    candidates = build_cmd(args.agent, prompt)

    last_code = 0
    last_out = ""
    last_err = ""

    for cmd in candidates:
        code, out, err = run(cmd)
        if code == 0:
            print(out, end="")
            sys.exit(0)

        combined = out + err
        last_code, last_out, last_err = code, out, err

        # Auth error — no point retrying
        if any(kw in combined.lower() for kw in ("auth", "login", "credential", "sign in")):
            print("ERROR: Amazon Q authentication required. Run: q login", file=sys.stderr)
            sys.exit(2)

        # If --agent caused the error, next iteration drops it
        if "--agent" not in cmd:
            break

    # All candidates failed
    print(f"ERROR: Amazon Q CLI call failed (exit code: {last_code}).", file=sys.stderr)
    print("Try running manually: q chat", file=sys.stderr)
    if last_out:
        print(last_out, file=sys.stderr)
    if last_err:
        print(last_err, file=sys.stderr)
    sys.exit(last_code or 1)


if __name__ == "__main__":
    main()
