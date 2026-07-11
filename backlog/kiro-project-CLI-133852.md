## kiro-project-CLI-133852: kiro-project CLIの基盤実装: プロジェクト初期化・バックログ管理・タスク実行ループ
- status: proposed
- source: charter
- priority: 0
- verify: `kiro-project --help > /dev/null 2>&1 && python -m pytest .kiro/hooks/ -q 2>/dev/null; test -f "$(pwd)/.kiro/project.yaml" || test -f "$(pwd)/.kiro/project.yml"`
- retries: 0
- workspace: kiro-project
