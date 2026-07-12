# 切り口: 混在位置と先頭選択を同時に固定する境界値テスト

## 成果／サマリー

`tools/kiro-project/tests/test_kiro_project.py` に
`test_first_command_line_skips_blank_and_comment_lines_inside_fence` を追加した。
`bash` フェンス内の先頭空行、通常コメント、インデント付きコメント、コマンド直前の空行を
読み飛ばし、後続コマンドではなく最初の実コマンド行を返すことを検証する。

## 検証内容と結果

- `git diff --check`: 成功（出力なし）
- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`:
  `5 passed, 512 deselected in 0.10s`、終了コード 0
- システム標準の `python3` には pytest がないため、依存成果 t7 と同じ既存 `.venv` を
  `PATH` に指定した。テストコマンド本体は完了条件どおりである。

## 前提・未解決事項・範囲外

- 完了条件は、指定 pytest の終了コード 0 と、対象観点を直接固定するユニットテストの追加と解釈した。
- 依存仕様どおり、先頭が空白の `#` コメントも `strip()` 後にコメントとして扱う前提を採用した。
- 変更対象はテストファイル1件のみ。実装本体、依存関係、他のテストは変更していない。
- 未解決事項および範囲外で新たに見つけた問題はない。
