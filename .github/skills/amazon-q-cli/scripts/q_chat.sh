#!/usr/bin/env bash
# Amazon Q CLI chat wrapper
# Usage:
#   q_chat.sh "prompt text"
#   q_chat.sh --file /path/to/prompt.txt
#   q_chat.sh --agent my-agent "prompt text"
#   q_chat.sh --agent my-agent --file /path/to/prompt.txt

set -euo pipefail

AGENT=""
PROMPT_FILE=""
PROMPT_TEXT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      AGENT="$2"
      shift 2
      ;;
    --file)
      PROMPT_FILE="$2"
      shift 2
      ;;
    *)
      PROMPT_TEXT="$1"
      shift
      ;;
  esac
done

# Validate q command exists
if ! command -v q &>/dev/null; then
  echo "ERROR: Amazon Q CLI (q) is not installed." >&2
  echo "Install: https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-getting-started-installing.html" >&2
  echo "  macOS: brew install amazon-q" >&2
  echo "  Linux: See official docs" >&2
  exit 1
fi

# Build prompt
PROMPT=""
if [[ -n "$PROMPT_FILE" ]]; then
  if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "ERROR: Prompt file not found: $PROMPT_FILE" >&2
    exit 1
  fi
  PROMPT="$(cat "$PROMPT_FILE")"
elif [[ -n "$PROMPT_TEXT" ]]; then
  PROMPT="$PROMPT_TEXT"
else
  echo "ERROR: No prompt provided. Pass prompt text or use --file." >&2
  exit 1
fi

# Build base command args
CMD_ARGS=("chat" "--no-interactive" "--trust-all-tools")
if [[ -n "$AGENT" ]]; then
  CMD_ARGS+=("--agent" "$AGENT")
fi

# Try non-interactive mode first
if output=$(q "${CMD_ARGS[@]}" "$PROMPT" 2>&1); then
  echo "$output"
  exit 0
fi

# Check for common errors and give guidance
EXIT_CODE=$?
if echo "$output" | grep -qi "auth\|login\|credential\|sign in"; then
  echo "ERROR: Amazon Q authentication required. Run: q login" >&2
  exit 2
fi

if echo "$output" | grep -qi "agent\|--agent"; then
  # Retry without --agent option
  CMD_ARGS_NO_AGENT=("chat" "--no-interactive" "--trust-all-tools")
  if output2=$(q "${CMD_ARGS_NO_AGENT[@]}" "$PROMPT" 2>&1); then
    echo "$output2"
    exit 0
  fi
fi

# --no-interactive might not be supported; fall back to piping via stdin
# Note: stdin piping support varies by version
if echo "$PROMPT" | q chat 2>&1; then
  exit 0
fi

echo "ERROR: Amazon Q CLI call failed (exit code: $EXIT_CODE)." >&2
echo "Try running manually: q chat" >&2
echo "Output was:" >&2
echo "$output" >&2
exit $EXIT_CODE
