## kiro-project-codd-gate-171537: kiro-project に codd-gate 自動検出を実装し、差分ゲート・受入判定・負債取り込みへ結線する
- status: doing
- source: charter
- priority: 0
- verify: `python3 -m pytest tools/kiro-project/tests -q -k codd && codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict`
- retries: 4
- workspace: sandbox
- refs: sandbox
- charter: v0.1
- assess: c=2 r=2 a=2
