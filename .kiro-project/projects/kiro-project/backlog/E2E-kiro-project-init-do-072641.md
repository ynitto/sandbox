## E2E-kiro-project-init-do-072641: E2E: kiro-project でサンプルプロジェクトを init → タスク追加 → 実行 → done まで一連で通す
- status: ready
- source: charter
- priority: 0
- verify: `bash tests/e2e/run_sample_project.sh && python -c "import json; bl=json.load(open('/tmp/e2e-sample/backlog.json')); assert all(t['status']=='done' for t in bl['tasks']), 'incomplete tasks'" && echo PASS`
- retries: 0
- workspace: sandbox
