## kiro-project-todo-in_pro-072641: kiro-project: タスクのステータス遷移（todo→in_progress→done）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `TASK_ID=$(python tools/kiro-project/main.py add --project /tmp/kp-test --title 'ステータス確認' --json | python -c "import sys,json; print(json.load(sys.stdin)['id'])") && python tools/kiro-project/main.py start --project /tmp/kp-test --id $TASK_ID && python tools/kiro-project/main.py done --project /tmp/kp-test --id $TASK_ID && python -c "import json; bl=json.load(open('/tmp/kp-test/backlog.json')); t=next(t for t in bl['tasks'] if t['id']=='$TASK_ID'); assert t['status']=='done'" && echo PASS`
- retries: 0
- workspace: sandbox
