## codd-ref-github-skills-agentic-search-4b7a96: .github/skills/agentic-search/SKILL.md の壊れた参照 references/protocol.md を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agentic-search/SKILL.md`
- retries: 0
- note: .github/skills/agentic-search/SKILL.md 行22 の references/protocol.md が実在しない（inline）
- paths: .github/skills/agentic-search/SKILL.md
- expect: changes
- assess: c=1 r=1 a=1
