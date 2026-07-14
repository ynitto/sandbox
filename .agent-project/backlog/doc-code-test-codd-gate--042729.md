## doc-code-test-codd-gate--042729: doc/code/test一貫性を取りcodd-gate verify --strictが通る状態にする
- status: proposed
- source: charter
- priority: 0
- verify: `python3 tools/codd-gate/codd-gate.py verify --base "$KIRO_BASE_REV" --strict`
- retries: 0
- workspace: sandbox
- charter: v1
- after: verify-codd-gate-042729, agent-project-codd-gate--042729, agent-dashboard-codd-gat-042729
