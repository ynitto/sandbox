## codd-ref-github-skills-agent-flow-SKI-90d612: .github/skills/agent-flow/SKILL.md の壊れた参照 .agent/agent-flow.yaml を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agent-flow/SKILL.md`
- retries: 0
- note: .github/skills/agent-flow/SKILL.md 行101 の .agent/agent-flow.yaml が実在しない（inline）
- paths: .github/skills/agent-flow/SKILL.md
- expect: changes
- assess: c=1 r=2 a=1
