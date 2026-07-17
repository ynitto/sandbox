# t5 verify report

- 対象 worktree: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-97881-5cvmzqhb/sandbox`
- 実施: 依存成果を参照しつつ、README と実ファイルを独立再導出で突合

## 1) 完了条件コマンド

以下を実行し `completion_exit=0` を確認:

`test -f docs/designs/README.md && grep -q 'agent-project-design.md' docs/designs/README.md && grep -q 'agent-flow-design.md' docs/designs/README.md && grep -q 'codd-gate-design.md' docs/designs/README.md && grep -q 'agent-tools-rename-design.md' docs/designs/README.md`

## 2) 4件リンクの実在・一致

- `docs/designs/agent-project-design.md` 実在、README 内参照あり
- `docs/designs/agent-flow-design.md` 実在、README 内参照あり
- `docs/designs/codd-gate-design.md` 実在、README 内参照あり
- `docs/designs/agent-tools-rename-design.md` 実在、README 内参照あり

## 3) 導線漏れ（README 未掲載）検査

- 実ファイル数（`docs/designs/*.md`）: 28（README 含む）
- README 参照ユニーク件数（`./*.md` 抽出）: 24
- README に存在せず、`docs/designs/` に実在する設計書:
  - `agent-dashboard-kiro-loop-terminal-design.md`
  - `agent-dashboard-project-ux-improvements.md`
  - `agent-flow-self-healing-retry-design.md`
  - （参考）`README.md` 自身

## 4) 重複・不整合

- 参照先が実在しない幽霊リンク: なし
- 4件主要リンクは「まず読むもの」と「カテゴリ別索引」に再掲されており重複は意図的

## 5) スコープ外差分混入

- `git status --short` は空（差分なし）

## 判定

- 4件リンクの完了条件は満たす
- ただし「導線から漏れた設計書がないか」の観点では未掲載3件があり、集約前チェックとしては fail
