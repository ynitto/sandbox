## E2E-kiro-projects-viewer-072641: E2E: kiro-projects-viewer でサンプルプロジェクトの全タスク done・成果物あり・needs 空を確認する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-projects-viewer/main.py show --project /tmp/e2e-sample | grep -q 'done' && python tools/kiro-projects-viewer/main.py needs --project /tmp/e2e-sample | grep -q 'none\|なし\|0 item' && echo PASS`
- retries: 0
- workspace: sandbox
