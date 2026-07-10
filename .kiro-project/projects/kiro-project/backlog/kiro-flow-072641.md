## kiro-flow-072641: kiro-flow の骨格を作成する（ディレクトリ・エントリーポイント・ヘルプ表示）
- status: doing
- source: charter
- priority: 0
- verify: `python tools/kiro-flow/main.py --help | grep -q 'usage\|Usage\|kiro-flow' && echo PASS`
- retries: 0
- workspace: sandbox
