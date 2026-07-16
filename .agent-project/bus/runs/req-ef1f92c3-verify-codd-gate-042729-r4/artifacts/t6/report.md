# verify-codd-gate-042729 / t6 敵対的検証レポート

- 判定: **pass**
- 対象 worktree: `/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-flow-ws-36484-ksgwq1dw/sandbox`
- 依存成果参照: `artifacts/t5/report.md`

## 独立再導出の実施内容

1. 完了条件 assert をそのまま実行し、exit 0 を確認。
2. 追加エッジケースを独立実行して照合。
   - ラベル同一行: `検証コマンド: codd-gate verify ...`
   - 散文＋ラベル同一行混在: `以下を実行してください。検証コマンド: codd-gate verify ...`
   - ラベル別行: `検証コマンド:\ncodd-gate verify ...`
   - 散文のみ（負例）: `None`
3. pytest（対象サブセット）を実行:
   - `pytest -q tools/agent-project/tests/test_agent_project.py -k "first_command_line or synth_verify or join_continuations or is_windows_shell_command"`
   - 結果: **33 passed, 645 deselected**
4. 実装とテストの代表抜き取り:
   - 実装: `tools/agent-project/agent_project/verify.py` の `_strip_leading_command_label` (L347), `_first_executable_line` (L412), `_first_command_line` (L431)
   - テスト: `tools/agent-project/tests/test_agent_project.py` の
     `test_first_command_line_strips_japanese_label_on_command_line` (L5899),
     `test_first_command_line_strips_doubled_japanese_label` (L5921),
     `test_first_command_line_strips_japanese_label_after_prose_preamble` (L5929),
     `test_first_command_line_returns_none_for_prose_only` (L5940)
5. スコープ外差分の混入確認:
   - `git status --short` は **CLEAN**（変更なし）

## 検証観点に対する判定

- (1) 完了条件: 充足
- (2) 集計値整合: pytest 33 件通過（t5 と同数）
- (3) 抜け漏れ・重複: 主要ケース（同一行ラベル・散文混在・別行ラベル・散文のみ負例）を確認
- (4) 要素妥当性抜き取り: 実装関数と対応テストを突合
- (5) スコープ外変更: なし

## 結論

`verify=pass`

```json
{"ok": true, "issues": []}
```
