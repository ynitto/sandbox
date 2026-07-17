## codd-ref-github-skills-agentic-search-5b7d04: .github/skills/agentic-search/SKILL.md の壊れた参照 agentic-search/scripts を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agentic-search/SKILL.md`
- retries: 0
- note: .github/skills/agentic-search/SKILL.md 行78 の agentic-search/scripts が実在しない（inline）
- paths: .github/skills/agentic-search/SKILL.md
- expect: changes
- assess: c=1 r=1 a=1
