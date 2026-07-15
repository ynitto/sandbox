# t9 verify report — codd-gate 連携（敵対的再検算）

- task_id: `agent-project-codd-gate--042729`
- verdict: `verify=pass`
- timestamp: `2026-07-16T00:29:09.826+09:00` 受領タスクを再検算

## 完了条件ゲート

- 実行（main worktree）:
  - `cd /Users/nitto/Workspace/sandbox && grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml`
- 結果: **exit 0**

## 検証観点と再検算結果

1. **codd-gate 利用可能時の自動結線 + 冪等**
   - 実測: 一時ディレクトリ（`repos.json` あり、ローカル config 非読込の isolated cwd）で `agent_project.build_config()` を2回実行。
   - 結果: `regression_cmd` に `codd-gate verify --base ... --repos ...`、`intake_cmd` に `codd-gate tasks --debt --repos ...` が自動設定され、2回目も同値（冪等）。
   - 併せて関連テスト通過（後述）。

2. **明示設定の保持**
   - 実測: `regression_cmd='echo custom-reg'` を明示した状態で `build_config()`。
   - 結果: 明示した `regression_cmd` は保持され、未指定の `intake_cmd` のみ自動補完。

3. **未検出・version/schema/capability 不適合で no-op 縮退**
   - 実測（`codd_gate_wiring.detect_wiring` へ依存注入）:
     - 旧版 (`0.9.0`) → `usable=False`、推奨コマンドなし
     - `repos.json` schema 不正 (`[]`) → `usable=False`、推奨コマンドなし
     - capability 部分不足（`tasks --debt` 非対応）→ `usable=True` だが `recommended_intake_cmd=None`
   - 結果: いずれも安全側に縮退。

4. **`codd-gate tasks --debt` の正常レコード enqueue / 不正レコード隔離**
   - 実測: `intake_cmd` に mixed payload（正常1件 + `title` 欠落1件）を与えて `run_intake()` 実行。
   - 結果: 正常1件のみ backlog に投入、不正1件は `journal` に `intake レコード無効:` として個別記録。

5. **install.sh 生成 zipapp 内 import と同等連携**
   - 実測:
     - `tools/agent-project/install.sh --prefix <tmp>` で zipapp 生成。
     - 生成物を `zipfile` で検査し、`codd_gate_detect.py` / `codd_gate_wiring.py` / `codd_gate_regression.py` / `codd_gate_debt.py` 同梱を確認。
     - 生成 zipapp を `sys.path` に載せて `import agent_project` 後、`build_config()`（`repos.json` あり）を実行。
   - 結果: zipapp 経路でも `regression_cmd` / `intake_cmd` の同等自動結線が有効。

6. **関連テスト**
   - 実行:
     - `python3 -m pytest -q tools/agent-project/tests/test_codd_gate_detect.py tools/agent-project/tests/test_codd_gate_wiring.py tools/agent-project/tests/test_codd_gate_regression.py tools/agent-project/tests/test_codd_gate_debt.py tools/agent-project/tests/test_agent_project.py -k 'TestCoddGateAutoWiring or TestIntake'`
   - 結果: **16 passed, 726 deselected**

7. **設計書と実装の一致**
   - `docs/designs/codd-gate-design.md` §4.1 の記述（`build_config` で未設定時のみメモリ上自動配線、明示設定優先、未検出/非互換/能力不足は no-op、`codd_gate_regression.py` は永続化用の明示実行ツール）が、`agent_project/configfile.py`・`codd_gate_wiring.py`・`codd_gate_regression.py`・`agent_project/model.py` の実装と整合。
   - `tools/agent-project/README.md` の同趣旨記載とも一致。

## issues

- 重大な fail 条件に該当する問題なし。

```json
{"ok": true, "issues": []}
```
