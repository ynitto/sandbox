切り口: 単発の散文拒否ではなく、再試行が散文だけを返し続ける場合にも verify が誤合成されないことを統合境界で固定した。

## 成果／サマリー

- `tools/kiro-project/tests/test_kiro_project.py` に `test_first_command_line_prose_only_never_becomes_synth_verify_command` を追加した。
- コマンドを一切含まない日本語散文を2回返す fake `kiro_run` を用い、`synth_verify(..., attempts=2)` が空文字 `""` を返すことを確認する。
- 呼び出し回数も2回と検証し、初回候補を誤採用せず再試行した後も誤った verify コマンドを生成しないことを固定した。
- 変更範囲はテストファイル1件のみで、実装コード・依存関係・設定は変更していない。

## 検証内容と結果

- 完了条件: `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
- 結果: 終了コード 0、`3 passed, 512 deselected in 0.10s`。
- システム標準の `python3` には pytest がないため、既存 `.venv` を `PATH` の先頭に指定した。実行した pytest コマンド本体は指定どおりである。

## 前提・未解決事項・範囲外

- 依存成果 `first-command-line-parse-spec.md` に従い、要求中の「None」は本実装の候補なし表現である空文字 `""` と解釈した。`_first_command_line` と `synth_verify` の既存契約はいずれも空文字を使用している。
- 本タスクはテストケース設計・追加が担当範囲のため、パーサー実装やコードフェンス対応は変更していない。
- 未解決事項および範囲外で新たに見つけた問題はない。
