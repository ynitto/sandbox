# t9 verify report — codd-gate 連携（敵対的再検算）

- task_id: `agent-project-codd-gate--042729`
- verdict: `verify=pass`

## 完了条件ゲート

- 実行:
  - `cd /Users/nitto/Workspace/sandbox && grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`
- 結果: **exit 0**
- 実測行: `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'`

## 検証観点

1. codd-gate 利用可能時の自動結線・冪等  
   - `build_config()` を `repos.json` ありの一時プロジェクトで2回実行。  
   - `regression_cmd`/`intake_cmd` が自動補完され、2回とも同値（冪等）。
2. 明示設定の保持  
   - `regression_cmd="echo custom-reg"` 明示時、当該値は保持され、`intake_cmd` のみ補完。
3. 未検出・version/schema/capability 不適合時の安全 no-op  
   - 未検出（PATH/同梱とも不在をモック）: `usable=False`、推奨コマンドなし。  
   - version 下限未満（0.9.0）: `usable=False`、推奨コマンドなし。  
   - schema 不適合（`repos.json=[]`）: `usable=False`、推奨コマンドなし。  
   - capability 不足（`tasks --debt` 非対応）: `recommended_intake_cmd=None` へ縮退。
4. `codd-gate tasks --debt` の正常 enqueue / 不正個別隔離  
   - mixed payload（正常1件 + `title`欠落1件）で `run_intake()` 実行。  
   - 正常1件のみ backlog へ投入、不正1件は `journal` に `intake レコード無効:` で隔離記録。
5. install.sh 生成 zipapp 内 import と同等連携  
   - `tools/agent-project/install.sh --prefix <tmp>` で zipapp 生成。  
   - zipapp に `codd_gate_detect.py` / `codd_gate_wiring.py` / `codd_gate_regression.py` / `codd_gate_debt.py` 同梱を確認。  
   - zipapp を `sys.path` 先頭にして `import agent_project` 後、`build_config()` で `regression_cmd`/`intake_cmd` の自動結線を確認。
6. 関連テスト  
   - `python3 -m pytest -q tools/agent-project/tests/test_codd_gate_detect.py tools/agent-project/tests/test_codd_gate_wiring.py tools/agent-project/tests/test_codd_gate_regression.py tools/agent-project/tests/test_codd_gate_debt.py tools/agent-project/tests/test_agent_project.py -k 'TestCoddGateAutoWiring or TestIntake'`  
   - **16 passed, 723 deselected**
7. 設計書整合  
   - `docs/designs/codd-gate-design.md` §4.1 の記載（build_config で未設定時のみメモリ上自動配線、明示設定優先、未検出/不適合時 no-op、`codd_gate_regression.py` は永続化用明示ツール）が  
     `agent_project/configfile.py` / `codd_gate_wiring.py` / `codd_gate_regression.py` / `agent_project/model.py` 実装と一致。

## issues

- 重大な fail 条件に該当する問題なし。

```json
{"ok": true, "issues": []}
```
