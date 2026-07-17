## codd-ref-github-instructions-common-i-30a833: .github/instructions/common.instructions.md の壊れた参照 ~/.codex/skill-registry.json を修正する（repo src）
- status: proposed
- source: enqueue
- priority: 1
- verify: `codd-gate check --repo-dir src=. --refs .github/instructions/common.instructions.md`
- retries: 0
- note: .github/instructions/common.instructions.md 行56 の ~/.codex/skill-registry.json が実在しない（inline）
- paths: .github/instructions/common.instructions.md
- expect: changes
- assess: c=1 r=1 a=1
