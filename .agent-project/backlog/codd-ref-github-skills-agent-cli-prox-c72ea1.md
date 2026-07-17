## codd-ref-github-skills-agent-cli-prox-c72ea1: .github/skills/agent-cli-proxy/SKILL.md の壊れた参照 references/windows-setup.md を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/skills/agent-cli-proxy/SKILL.md`
- retries: 0
- note: .github/skills/agent-cli-proxy/SKILL.md 行32 の references/windows-setup.md が実在しない（link）
- paths: .github/skills/agent-cli-proxy/SKILL.md
- expect: changes
- assess: c=1 r=1 a=1
