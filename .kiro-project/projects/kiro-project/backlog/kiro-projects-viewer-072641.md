## kiro-projects-viewer-072641: kiro-projects-viewer: 複数プロジェクトのディレクトリ走査と一覧表示を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-projects-viewer/main.py list --dir /tmp | grep -q 'kp-test' && echo PASS`
- retries: 0
- workspace: sandbox
