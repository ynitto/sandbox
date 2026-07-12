切り口: 各フェンスに副作用の異なる sentinel コマンドを置き、後続フェンスの誤選択を戻り値だけで明瞭に検出する。

## 成果／サマリー

- `test_first_command_line_returns_command_from_first_of_multiple_fences` を追加した。
- 第1フェンスの `pytest -q tests/first_fence` と、第2フェンスの `rm -rf tests/second_fence` を対比し、第1フェンスのコマンドが返ることを固定した。
- テストで判明した、フェンスマーカーが `` ` `` として返る既存不具合を、`_first_command_line` が ` ``` ` で始まる行を候補外にする最小変更で補正した。

## 検証内容と結果

- 完了条件: `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
- 結果: 終了コード 0、`6 passed, 512 deselected, 5 subtests passed`。
- `git diff --check`: 問題なし。

## 採用した前提・未解決事項・範囲外

- 依存仕様 t7 に従い、「コマンドを含むフェンスが複数なら、第1フェンスの第1コマンドを返す」を期待値とした。
- 指定の system Python には pytest がないため、既存 `/Users/nitto/Workspace/sandbox/.venv` を PATH の先頭に置いて、指定コマンド本体を実行した。
- 変更範囲は `_first_command_line` のフェンスマーカー除外と、その複数フェンステストのみ。前置き優先順位、非 shell タグ、空フェンスなど t7 の他ケースは本タスクの範囲外として変更していない。
- 未解決事項・範囲外で新たに見つけた問題はない。
