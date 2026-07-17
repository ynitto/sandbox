## codd-test-github-skills-dependency-aud-6c00cf: .github/skills/dependency-auditor/scripts/audit_deps.py のテストを追加する（repo src）
- status: proposed
- source: enqueue
- priority: 0
- verify: `codd-gate check --repo-dir src=. --covered .github/skills/dependency-auditor/scripts/audit_deps.py --need test`
- retries: 0
- note: 接続マップ上でどのテストからも参照されていない
- paths: .github/skills/dependency-auditor/scripts/audit_deps.py
- assess: c=1 r=1 a=1
