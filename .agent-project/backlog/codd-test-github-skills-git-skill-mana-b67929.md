## codd-test-github-skills-git-skill-mana-b67929: .github/skills/git-skill-manager/scripts/promotion_policy.py のテストを追加する（repo src）
- status: proposed
- source: enqueue
- priority: 0
- verify: `codd-gate check --repo-dir src=. --covered .github/skills/git-skill-manager/scripts/promotion_policy.py --need test`
- retries: 0
- note: 接続マップ上でどのテストからも参照されていない
- paths: .github/skills/git-skill-manager/scripts/promotion_policy.py
- assess: c=1 r=1 a=2
