# t3 verify: 集約前チェック（t1/t2 整合性・完了条件・回帰両立）

## 判定
- verify=pass

## 独立検算の実施内容
1. 依存成果の整合確認  
   - t1/t2 ともに「完了条件は現行実装で成功」「コード変更なし」で一致。  
   - 既知の穴（`検証コマンド: $ ...` 同一行）が両者で同じ内容として報告され、矛盾なし。

2. 実装・経路の再導出  
   - `_first_command_line` 定義: `tools/agent-project/agent_project/verify.py:431`  
   - 呼び出し元は `synth_verify` のみ: `verify.py:477`  
   - 波及経路は `synth_verify -> ensure_verify -> mr.py:552` と `project.py:86` を確認。

3. 完了条件の再実行（指定コマンドそのもの）  
   - 成功（exit 0）:
   - `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'`

4. 既存回帰との両立確認  
   - `-k 'first_command_line or synth_verify or join_continuations or is_windows_shell_command'`  
   - 結果: `33 passed, 645 deselected`

5. 抜き取り検査（ラベル系代表ケース）  
   - `検証コマンド:\n...` / 同一行ラベル / 全角コロン / 二重ラベル / 別行 `$` は期待どおり抽出。  
   - 既知の穴 `検証コマンド: $ ...` は `None`（t1/t2 報告どおり、今回の完了条件スコープ外）。

6. スコープ外差分混入確認  
   - worktree 変更なし（`git status --short` 出力なし）。

## チェック観点の結論
- (1) 目標・完了条件: 満たす  
- (2) 集計値整合: t2 主張の回帰 33 件を再実行で一致  
- (3) 抜け漏れ・重複: 依存報告間に矛盾なし  
- (4) 妥当性抜き取り: 代表ケースを実測で確認  
- (5) スコープ外変更: なし

## issues
- (minor) `検証コマンド: $ codd-gate ...`（ラベルと `$` が同一行）は未対応の既知穴。今回の完了条件（ラベル行とコマンド行が別行）および既存回帰 33 件の範囲外のため fail にはしない。
