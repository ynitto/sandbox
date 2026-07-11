## python--m-pytest-Users-n-142029: 受入条件を満たす: > python -m pytest /Users/nitto/Workspace/sandbox -x -q 2>/dev/null || (cd /Users/nitto/Workspace/sandbox && p
- status: doing
- source: acceptance
- priority: 0
- verify: `> python -m pytest /Users/nitto/Workspace/sandbox -x -q 2>/dev/null || (cd /Users/nitto/Workspace/sandbox && python -c "import sys; sys.exit(0 if import('os').path.isdir('.kiro') or import('os').path.isfile('kiro-project.yaml') or import('os').path.isfile('.github/skills/kiro-project/SKILL.md') or import('glob').glob('**/*.kiro*', recursive=True) else 1)")`
- retries: 0
