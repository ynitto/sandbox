## kiro-flow-run-072641: kiro-flow: run の状態永続化（状態ファイルへの書き込み・読み込み）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-flow/main.py run tests/fixtures/sample_workflow.yaml --run-id test-run-001 && test -f .kiro-flow/runs/test-run-001/state.json && echo PASS`
- retries: 0
- workspace: sandbox
