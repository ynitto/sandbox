## codd-ref-github-skills-agent-loop-mes-f72a9c: .github/skills/agent-loop-messaging/SKILL.md の壊れた参照 ~/.kiro/agents を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agent-loop-messaging/SKILL.md`
- retries: 0
- note: .github/skills/agent-loop-messaging/SKILL.md 行259 の ~/.kiro/agents が実在しない（inline）
- paths: .github/skills/agent-loop-messaging/SKILL.md
- expect: changes
- assess: c=1 r=1 a=1
