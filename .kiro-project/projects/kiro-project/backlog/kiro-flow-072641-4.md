## kiro-flow-072641-4: 標準ライブラリのみ使用していることを全ツールで検証する（対象: kiro-flow）
- status: ready
- source: charter
- priority: 0
- verify: `python -c "
import ast, sys, pathlib
stdlib = sys.stdlib_module_names
errors = []
for p in pathlib.Path('tools/kiro-flow').rglob('*.py'):
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (node.names[0].name if isinstance(node, ast.Import) else node.module or '').split('.')[0]
            if mod and mod not in stdlib and mod not in ('', '__future__'):
                errors.append(f'{p}:{node.lineno}: {mod}')
if errors: print('\n'.join(errors)); sys.exit(1)
" && echo PASS`
- retries: 0
- review: human
- cohort: cohort
- cohort_role: pilot
