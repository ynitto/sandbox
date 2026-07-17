## codd-ref-github-skills-agent-project-e01703: .github/skills/agent-project/SKILL.md の壊れた参照 ~/.agent/… を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agent-project/SKILL.md`
- retries: 0
- note: .github/skills/agent-project/SKILL.md 行287 の ~/.agent/… が実在しない（inline）
- paths: .github/skills/agent-project/SKILL.md
- expect: changes
- assess: c=1 r=1 a=1
