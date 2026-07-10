## E2E-kiro-flow-run-comple-072641: E2E: kiro-flow でサンプルワークフローを run → 全タスク completed を確認する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-flow/main.py run tests/e2e/sample_workflow.yaml --run-id e2e-run && python -c "import json; s=json.load(open('.kiro-flow/runs/e2e-run/state.json')); assert s['status']=='completed', s['status']" && echo PASS`
- retries: 0
- workspace: sandbox
