# t5 verify gate: regression/intake と repos.json 解決の敵対的検証

## 判定

**verify=fail**

`.agent/agent-project.yaml` の完了条件 grep は成立するが、設定された `regression_cmd` / `intake_cmd` の `--repos` 相対パス解決が実行 cwd と不整合で、実行時に失敗する。

## 独立再検算の結果

1. 完了条件コマンドは成立（exit 0）
   - `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`
2. 設定値は以下
   - `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'`
   - `intake_cmd: 'codd-gate tasks --debt --repos .agent-project/repos.json'`
3. 実行ディレクトリ `/Users/nitto/Workspace/sandbox-agent-state/.agent-project` での実測
   - `.agent-project/repos.json` は **存在しない**
   - `repos.json` は **存在する**
   - `codd-gate verify --base HEAD --repos .agent-project/repos.json` → exit 2（repos レジストリ未検出）
   - `codd-gate verify --base HEAD --repos repos.json` → exit 2（`--repo-dir` 不足）
   - `codd-gate verify --base HEAD --repos repos.json --repo-dir src=.` → レジストリ解決成功（差分検出で exit 1）

## issues（再作業指示）

1. `.agent/agent-project.yaml:30` の `regression_cmd` が誤り。  
   **何が問題か**: `--repos .agent-project/repos.json` は実行 cwd 基準で二重パスになり不達。加えて repo 解決に必要な `--repo-dir` がない。  
   **どう直すか**: `codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.` に修正する（少なくとも `--repos repos.json` へ修正し、`--repo-dir` を併記）。
2. `.agent/agent-project.yaml:31` の `intake_cmd` が誤り。  
   **何が問題か**: 同様に `--repos .agent-project/repos.json` が不達。  
   **どう直すか**: `codd-gate tasks --debt --repos repos.json --repo-dir src=.` に修正する（`tasks` 側でも repo 解決引数を統一）。
3. t2/t3 タスク成果が「正しい」とした結論は実測と矛盾。  
   **どこで**: `artifacts/t2/report.md` と `artifacts/t3/report.md`。  
   **どう直すか**: 両タスクを作り直し、README の文面一致ではなく「実行 cwd での実行可能性（repos 解決成功）」を完了条件に入れて再検証すること。

## スコープ外変更の混入

本 t5 では `.agent/agent-project.yaml` 本体は未編集。成果物は本レポートのみ。
