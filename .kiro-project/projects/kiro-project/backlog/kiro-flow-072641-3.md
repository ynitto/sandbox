## kiro-flow-072641-3: kiro-flow: タスク実行エンジン（順次・並列）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-flow/main.py run tests/fixtures/sample_workflow.yaml && grep -q 'completed' tests/fixtures/sample_workflow_output.json && echo PASS`
- retries: 0
- workspace: sandbox
