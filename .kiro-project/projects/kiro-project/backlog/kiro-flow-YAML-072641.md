## kiro-flow-YAML-072641: kiro-flow: ワークフロー定義ファイル（YAML）のロード機能を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-flow/main.py load tests/fixtures/sample_workflow.yaml && echo PASS`
- retries: 0
- workspace: sandbox
