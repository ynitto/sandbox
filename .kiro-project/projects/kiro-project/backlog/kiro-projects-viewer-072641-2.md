## kiro-projects-viewer-072641-2: kiro-projects-viewer: 単一プロジェクトの詳細表示（バックログ・ステータス・成果物）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-projects-viewer/main.py show --project /tmp/kp-test | grep -q 'backlog\|status\|deliverable' && echo PASS`
- retries: 0
- workspace: sandbox
