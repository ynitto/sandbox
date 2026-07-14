## agent-dashboard-codd-gat-042729: agent-dashboardでcodd-gate連携（regression/intake）の有効状態を確認できるようにする
- status: proposed
- source: charter
- priority: 0
- verify: `test -f tools/agent-dashboard/test/codd-gate-status.test.js && cd tools/agent-dashboard && npm test -- test/codd-gate-status.test.js`
- retries: 0
- workspace: sandbox
- charter: v1
- after: agent-project-codd-gate--042729
- assess: c=2 r=1 a=2
