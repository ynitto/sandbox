## codd-ref-github-skills-agentic-search-f58bbb: .github/skills/agentic-search/CHANGELOG.md の壊れた参照 references/protocol.md を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agentic-search/CHANGELOG.md`
- retries: 0
- note: .github/skills/agentic-search/CHANGELOG.md 行20 の references/protocol.md が実在しない（inline）
- paths: .github/skills/agentic-search/CHANGELOG.md
- expect: changes
- assess: c=1 r=1 a=1
