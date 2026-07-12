切り口: フェンスなしの全文走査に先頭トークンのコマンドらしさ判定を加え、句読点のない英語散文の誤採用を防いだ。

## 成果／サマリー

- `tools/kiro-project/kiro-project.py` に、既知コマンド語、パス指定、ハイフン付き CLI 名を判定する `_has_command_like_leading_token` を追加した。
- `_first_command_line` はフェンス内候補を従来どおり優先し、フェンス内に候補がない場合はコマンドらしい先頭トークンを持つ行だけを先頭から `_first_executable_line` で検査するよう変更した。
- 句読点のない英語前置き、相対パスの実行ファイル、ハイフン付き CLI 名の回帰テストを追加した。

## 検証内容と結果

- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`: `11 passed, 512 deselected`、終了コード 0。
- `git diff --check`: 終了コード 0。
- `codd-gate verify --base main`: 終了コード 1。既存設計文書の未更新判定と、既存テスト中のサンプルパスを未解決参照とする AMBER のため。

## 採用した前提・未解決事項・範囲外

- 「既知のコマンド語」は synth verify で一般的なテスト、ビルド、検索、言語ランタイム等の固定集合と解釈した。「実行可能トークン列」は `/`・`./`・`../` で始まるパス、または `custom-check` のようなハイフン付き CLI 名と解釈した。
- フェンス内は LLM が明示的にコードとして提示した領域なので、依存実装のシェル構文判定を維持した。厳格な先頭トークン判定は散文混入が問題になるフェンスなしフォールバックだけに適用した。
- システム Python には pytest がないため、既存 venv を PATH の先頭に置いて指定コマンドを実行した。
- codd-gate の AMBER 解消は設計文書や多数の既存テスト記述へ波及し、本タスクの最小範囲外なので変更していない。
