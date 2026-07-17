## codd-test-github-skills-code-to-specs-b5261c: .github/skills/code-to-specs/scripts/coverage_check.py のテストを追加する（repo src）
- status: proposed
- source: enqueue
- priority: 0
- verify: `codd-gate check --repo-dir src=. --covered .github/skills/code-to-specs/scripts/coverage_check.py --need test`
- retries: 0
- note: 接続マップ上でどのテストからも参照されていない
- paths: .github/skills/code-to-specs/scripts/coverage_check.py
