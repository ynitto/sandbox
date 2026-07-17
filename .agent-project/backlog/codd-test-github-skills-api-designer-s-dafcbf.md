## codd-test-github-skills-api-designer-s-dafcbf: .github/skills/api-designer/scripts/validate_openapi.py のテストを追加する（repo src）
- status: proposed
- source: enqueue
- priority: 0
- verify: `codd-gate check --repo-dir src=. --covered .github/skills/api-designer/scripts/validate_openapi.py --need test`
- retries: 0
- note: 接続マップ上でどのテストからも参照されていない
- paths: .github/skills/api-designer/scripts/validate_openapi.py
- assess: c=1 r=1 a=1
