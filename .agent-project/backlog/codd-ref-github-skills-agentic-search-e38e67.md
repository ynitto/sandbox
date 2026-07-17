## codd-ref-github-skills-agentic-search-e38e67: .github/skills/agentic-search/CHANGELOG.md の壊れた参照 scripts/hints.py を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agentic-search/CHANGELOG.md`
- retries: 0
- note: .github/skills/agentic-search/CHANGELOG.md 行14 の scripts/hints.py が実在しない（inline）
- paths: .github/skills/agentic-search/CHANGELOG.md
- expect: changes
- assess: c=1 r=1 a=1
