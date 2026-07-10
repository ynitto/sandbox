## kiro-flow-result-show-072641: kiro-flow: 結果出力コマンド（result / show）を実装する
- status: ready
- source: charter
- priority: 0
- verify: `python tools/kiro-flow/main.py result test-run-001 | grep -q 'status' && echo PASS`
- retries: 0
- workspace: sandbox
