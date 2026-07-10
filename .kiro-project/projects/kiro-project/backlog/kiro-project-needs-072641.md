## kiro-project-needs-072641: kiro-project: needs（人の判断待ち）の登録・確認・解決コマンドを実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-project/main.py needs add --project /tmp/kp-test --message '方針確認が必要' && python tools/kiro-project/main.py needs list --project /tmp/kp-test | grep -q '方針確認が必要' && echo PASS`
- retries: 0
- workspace: sandbox
