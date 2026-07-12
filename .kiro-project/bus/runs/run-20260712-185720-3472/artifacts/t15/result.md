切り口: タグなしフェンスの単純な正常系ではなく、フェンス内の空行・コメントを越えて実コマンドを選べる境界を固定した。

## 成果／サマリー

- `tools/kiro-project/tests/test_kiro_project.py` に `test_first_command_line_extracts_command_from_untyped_fence` を追加した。
- 言語タグなしの ` ``` ` フェンス内に空行、コメント、実コマンドを並べ、`_first_command_line` が `python3 -m pytest tools/kiro-project/tests -q` を返すことを検証する。
- 変更対象はテストファイル1つだけで、実装・依存関係・他機能には手を加えていない。

## 検証内容と結果

- 完了条件: `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
  - 終了コード: 0
  - 結果: `7 passed, 512 deselected, 5 subtests passed in 0.10s`
- `git diff --check`
  - 終了コード: 0（空白エラーなし）
- システム標準の `python3` には pytest がなかったため、依存成果 t7 と同じ既存 `.venv` を PATH の先頭に置いて完了条件を実行した。コマンド本体は指定どおりである。

## 前提・未解決事項・範囲外

- 前提: この担当の完了は、テスト観点「言語タグなしフェンス内のコマンドを拾える」を名前と入力形状で明示するユニットテストとして固定し、指定の絞り込みテストが成功すること、と解釈した。
- 前提: 依存仕様に従い、タグなしフェンスは shell フェンスとして受理し、空行と `#` コメントを候補外として最初の実コマンドを返すものとした。
- 未解決事項: なし。
- 範囲外で見つけた問題: システム標準 Python に pytest が未導入。既存 `.venv` で検証可能なため、環境変更は行っていない。
