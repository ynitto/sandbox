verify=pass

- 完了条件コマンドを対象 worktree で独立再実行し、終了コード 0 を確認。
- `docs/designs/README.md` 実体を確認し、4ファイル名（`agent-project-design.md` / `agent-flow-design.md` / `codd-gate-design.md` / `agent-tools-rename-design.md`）の記載を確認。
- 作業ツリー差分（`git status --short` / `git diff --name-only`）は空で、スコープ外の変更混入なし。
- (minor) `docs/designs/*.md` 実在件数は 26（README 含む）で、README の「24件」表記と不一致。今回の完了条件には非該当のため pass 判定。

{"ok": true, "issues": ["(minor) docs/designs/README.md の『設計書24件』表記が現状の実在件数（README含む26件）と不一致。`agent-dashboard-project-ux-improvements.md` 追加分を反映して件数・索引を更新すること。"]}
