## test--f-kiro-project-jso-142029: 受入条件を満たす: > test -f .kiro/project.json && python -c "import json,sys; d=json.load(open('.kiro/project.json')); sys.exit(
- status: proposed
- source: acceptance
- priority: 0
- verify: `> test -f .kiro/project.json && python -c "import json,sys; d=json.load(open('.kiro/project.json')); sys.exit(0 if d.get('name') else 1)"`
- retries: 0
