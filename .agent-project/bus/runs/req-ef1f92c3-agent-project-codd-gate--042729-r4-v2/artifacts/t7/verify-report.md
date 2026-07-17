# t7 verify report

- task_id: agent-project-codd-gate--042729
- target_command: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml`
- result: pass
- matched_line: `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'`

```json
{"ok": true, "issues": []}
```

```json
{"constraints": ["完了条件の判定対象はこのworktree直下の`agent-project.yaml`であり、参照用リポジトリ（read-only）の同名設定とは混同しないこと。"]}
```
