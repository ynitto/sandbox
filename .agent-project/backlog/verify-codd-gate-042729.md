## verify-codd-gate-042729: verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる
- status: proposed
- source: charter
- priority: 0
- verify: `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'`
- retries: 0
- workspace: sandbox
- charter: v1
