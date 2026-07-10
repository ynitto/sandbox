## kiro-project-072641: kiro-project: 成果物（納品書）の登録・確認コマンドを実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-project/main.py deliver --project /tmp/kp-test --file /tmp/kp-test/charter.yaml --description 'チャーター' && python tools/kiro-project/main.py deliverables --project /tmp/kp-test | grep -q 'charter.yaml' && echo PASS`
- retries: 0
- workspace: sandbox
