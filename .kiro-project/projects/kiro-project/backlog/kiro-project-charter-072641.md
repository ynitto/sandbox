## kiro-project-charter-072641: kiro-project: プロジェクトの charter 確認コマンドを実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-project/main.py charter --project /tmp/kp-test | grep -q 'name\|goal\|goal' && echo PASS`
- retries: 0
- workspace: sandbox
