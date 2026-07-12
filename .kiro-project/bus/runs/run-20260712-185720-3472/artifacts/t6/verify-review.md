verify=fail

1. **t1/t5 の完了条件判定が現 HEAD と矛盾している（重大）**
   - どこで:  
     - `artifacts/t1/first_command_line-current.md` の「`512 deselected` / exit 5」記載  
     - `artifacts/t5/charter-scope-and-codd-gate.md` の「`first_command_line` テスト未存在」記載
   - 何が問題か: 対象ブランチ HEAD は `967f7e73...` で、`tools/kiro-project/tests/test_kiro_project.py` に `test_first_command_line_*` が2件存在し、実行結果は `2 passed, 512 deselected`（exit 0）。調査結果が現行と不整合。
   - 実コード根拠: `test_kiro_project.py:4924-4928`。
   - 差し戻し内容: t1/t5 は同一ブランチ HEAD で再検証し、実行コマンド・exit code・選択 node id を更新すること。

2. **t4 の「基準ユニットテストを1件追加」がコミット実体と一致しない（重大）**
   - どこで: `artifacts/t4/first-command-line-current-behavior.md` の検証節。
   - 何が問題か: ブランチ上の追加コミットは `967f7e7` のみで、差分は `_first_command_line` テスト2件追加（6行）だけ。t4 主張の「1件追加」「1 passed」は実体裏付けがない。
   - 実コード根拠: `git show --stat 967f7e73...` で `test_kiro_project.py` に `6 insertions` のみ。
   - 差し戻し内容: t4 は「追加したテスト名」「対応コミット」「pytest 実ログ」を整合する形で再提出すること（追加なしなら“調査のみ”と明記）。

3. **中核入力形状（前置き＋コードフェンス）を検証で固定できていない（重大）**
   - どこで: t1〜t5 全体。`inventory.md` でも回帰テスト未存在と記載。
   - 何が問題か: 問題の主対象「LLM前置き＋ fenced command」について、現行がどこで落ちるかを実行可能テストで固定できていない。  
     現実装は `_first_command_line` で先頭非空行を返すため、`"Here is the command:\n```bash\npytest -q\n```"` で `"Here is the command:"` を返しフェンス内コマンドに到達しない。
   - 実コード根拠: `kiro-project.py:2885-2888`（先頭非空行即 return）。
   - 差し戻し内容: 少なくとも以下を追加して再提出すること。  
     - `-k first_command_line` に拾われる名前のテストで、前置き＋フェンス入力の期待値を固定  
     - 可能なら `synth_verify` 経由でも同形状を再現し、`task.verify` 未設定化までを検証

完了条件確認:
- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
- 結果: `2 passed, 512 deselected`（exit 0）

{"ok": false, "issues": ["t1/t5の完了条件判定が現HEADと矛盾", "t4のテスト追加主張がコミット実体と不一致", "前置き+コードフェンス入力形状の回帰テスト欠落"]}
