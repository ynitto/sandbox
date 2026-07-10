## kiro-project-init-072641: kiro-project: プロジェクト初期化コマンド（init）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-project/main.py init --name sample-project --dir /tmp/kp-test && test -f /tmp/kp-test/charter.yaml && test -f /tmp/kp-test/backlog.json && echo PASS`
- retries: 0
- workspace: sandbox
