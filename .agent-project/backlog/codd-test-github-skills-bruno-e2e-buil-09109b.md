## codd-test-github-skills-bruno-e2e-buil-09109b: .github/skills/bruno-e2e-builder/scripts/generate_e2e.py のテストを追加する（repo src）
- status: proposed
- source: enqueue
- priority: 0
- verify: `codd-gate check --repo-dir src=. --covered .github/skills/bruno-e2e-builder/scripts/generate_e2e.py --need test`
- retries: 0
- note: 接続マップ上でどのテストからも参照されていない
- paths: .github/skills/bruno-e2e-builder/scripts/generate_e2e.py
