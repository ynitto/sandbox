## codd-ref-github-skills-api-designer-S-e02848: .github/skills/api-designer/SKILL.md の壊れた参照 references/rest-design-guide.md を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/api-designer/SKILL.md`
- retries: 0
- note: .github/skills/api-designer/SKILL.md 行91 の references/rest-design-guide.md が実在しない（link）
- paths: .github/skills/api-designer/SKILL.md
- expect: changes
- assess: c=1 r=1 a=1
