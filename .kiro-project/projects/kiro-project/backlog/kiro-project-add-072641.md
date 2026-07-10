## kiro-project-add-072641: kiro-project: バックログへのタスク追加コマンド（add）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-project/main.py add --project /tmp/kp-test --title 'タスク1' --description '説明1' && python -c "import json; bl=json.load(open('/tmp/kp-test/backlog.json')); assert any(t['title']=='タスク1' for t in bl['tasks'])" && echo PASS`
- retries: 0
- workspace: sandbox
