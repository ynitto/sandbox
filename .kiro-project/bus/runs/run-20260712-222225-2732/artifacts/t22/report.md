# t22 verify report — adversarial check for `_first_command_line`

## Outcome

- **Result**: fail (4 major breakages)
- **Completion condition**: `python3 -m pytest tools/kiro-project/tests -q -k first_command_line` passed (18 passed, exit 0)
- **Workspace diff**: none

## Reproduction

```bash
cd /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/kiro-flow-ws-32415-4ldylxxj/sandbox
python3 - <<'PY'
import importlib.util, sys
from pathlib import Path
from types import SimpleNamespace

p = Path('tools/kiro-project/kiro-project.py')
spec = importlib.util.spec_from_file_location('km', p)
km = importlib.util.module_from_spec(spec); sys.modules['km'] = km; spec.loader.exec_module(km)
cfg = SimpleNamespace(model='dummy')

cases = [
    ("indented-fence-with-leading-prose-inside",
     "説明です\n    ```bash\n以下を実行してください\npython3 -m pytest tools/kiro-project/tests -q -k first_command_line\n```",
     "python3 -m pytest tools/kiro-project/tests -q -k first_command_line"),
    ("emoji-before-command-in-fence",
     "```bash\n✅ python3 -m pytest tools/kiro-project/tests -q -k first_command_line\n```",
     "python3 -m pytest tools/kiro-project/tests -q -k first_command_line"),
    ("powershell-prompt-in-fence",
     "```console\nPS> python3 -m pytest tools/kiro-project/tests -q -k first_command_line\n```",
     "python3 -m pytest tools/kiro-project/tests -q -k first_command_line"),
    ("mixed-output-and-command-in-same-fence",
     "```bash\n出力例:\n================== test session starts ==================\npython3 -m pytest tools/kiro-project/tests -q -k first_command_line\n```",
     "python3 -m pytest tools/kiro-project/tests -q -k first_command_line"),
]

for name, out, expected in cases:
    first = km._first_command_line(out)
    synth = km.synth_verify(cfg, 'T', 'A', kiro_run=lambda p, m, o=out: o, attempts=1)
    print(name, "first=", repr(first), "synth=", repr(synth), "expected=", repr(expected))
PY
```

## Major issues

1. `indented-fence-with-leading-prose-inside`  
   - where: `tools/kiro-project/kiro-project.py` `_first_command_line` (`_first_executable_line(..., require_shell_syntax=False)` via fence path)  
   - what: fence内の散文 `以下を実行してください` をコマンドとして採用  
   - observed: `_first_command_line` / `synth_verify` ともに `"以下を実行してください"` を返す  
   - should fix: フェンス内でも散文行を除外し、実コマンド行を選ぶ（少なくとも command-like 判定を適用）

2. `emoji-before-command-in-fence`  
   - where: same as above  
   - what: 先頭に絵文字付きの行をそのまま採用してしまう  
   - observed: `"✅ python3 -m pytest ..."` を返却  
   - should fix: 絵文字・装飾プレフィックスを正規化してから判定する

3. `powershell-prompt-in-fence`  
   - where: `_strip_leading_shell_prompt` (`$ ` のみ対応)  
   - what: `PS>` プレフィックスが剥がれずそのまま返る  
   - observed: `"PS> python3 -m pytest ..."` を返却  
   - should fix: `PS>` / `>` などの代表的プロンプトを除去対象に追加

4. `mixed-output-and-command-in-same-fence`  
   - where: fence path in `_first_command_line`  
   - what: フェンス内の「出力例:」のような散文を先に拾ってしまい、後続の実コマンドを見ない  
   - observed: `"出力例:"` を返却  
   - should fix: 「出力例」「ログ」「区切り線」など非コマンド行をスキップし、最初の実行可能行を選ぶ

## Spot checks (passed)

- `nested-backticks-command`: pass  
- `heading-before-command-in-fence` (`### ...`): pass（`#` 行としてスキップ）
