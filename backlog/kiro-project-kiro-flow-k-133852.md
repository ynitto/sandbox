## kiro-project-kiro-flow-k-133852: kiro-project × kiro-flow 統合: バックログタスクをkiro-flowワークフローとして自律実行する連携層
- status: proposed
- source: charter
- priority: 0
- verify: `kiro-project backlog list 2>/dev/null | head -1 > /dev/null && kiro-flow status 2>/dev/null > /dev/null; echo $?`
- retries: 0
- workspace: kiro-project
- refs: kiro-flow
