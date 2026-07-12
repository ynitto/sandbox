# t23 verify report — first_command_line loop-until-done

- completion condition: `python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
- final result: **22 passed, 523 deselected, exit 0**

## What was fixed

`tools/kiro-project/kiro-project.py`

- Fence path now normalizes candidate lines (`$` / `PS>` / `>` prompt and emoji decorators).
- Fence path now rejects obvious non-command lines (e.g., `出力例:` and separator lines).
- Fence path now requires command-like leading tokens instead of accepting arbitrary first non-empty line.

`tools/kiro-project/tests/test_kiro_project.py`

- Added 4 regression tests for previously reported failures:
  - prose line inside fence before command
  - emoji-prefixed fenced command
  - PowerShell prompt (`PS>`) in fenced command
  - output label + log separator preceding command in fence

## Independent re-check against t22 cases

All 4 adversarial cases now return:

`python3 -m pytest tools/kiro-project/tests -q -k first_command_line`

for both `_first_command_line(...)` and `synth_verify(...)`.
