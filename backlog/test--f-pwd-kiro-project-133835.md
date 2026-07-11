## test--f-pwd-kiro-project-133835: 受入条件を満たす: > test -f "$(pwd)/.kiro/project.yaml" || test -f "$(pwd)/.kiro/project.yml"
- status: proposed
- source: acceptance
- priority: 0
- verify: `> test -f "$(pwd)/.kiro/project.yaml" || test -f "$(pwd)/.kiro/project.yml"`
- retries: 0
