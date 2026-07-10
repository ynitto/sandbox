## kiro-projects-viewer-nee-072641: kiro-projects-viewer: needs（判断待ち）一覧の表示を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-projects-viewer/main.py needs --project /tmp/kp-test | grep -q '方針確認が必要' && echo PASS`
- retries: 0
- workspace: sandbox
