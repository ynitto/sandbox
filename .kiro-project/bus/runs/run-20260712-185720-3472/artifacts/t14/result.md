切り口: LLM の日本語前置きに惑わされず、後続の `bash` フェンスを優先する実障害直結の回帰ケース。

## 成果

`test_first_command_line_returns_command_from_bash_fence_after_prose` を追加した。

- 入力文字列: `確認コマンドはこちらです。\n```bash\npython3 -m pytest tools/kiro-project/tests -q\n````
- 期待コマンド: `python3 -m pytest tools/kiro-project/tests -q`

このケースを通すため、`_first_command_line` が ` ```bash ` の内側を先に走査し、閉じフェンスまでの最初の非空・非コメント行を返す最小変更を加えた。変更対象は `tools/kiro-project/kiro-project.py` と `tools/kiro-project/tests/test_kiro_project.py` のみ。

## 検証

`PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`

結果: 終了コード 0、`4 passed, 512 deselected`。

## 前提・未解決事項

- 前提: 本タスクの完了には、テスト設計だけでなく追加した回帰テストが指定コマンドで成功する最小実装も必要と解釈した。
- 前提: 依存仕様の全入力形状ではなく、担当観点である「日本語前置き + `bash` フェンス」だけを固定した。
- 未解決: タグなし、`sh`/`zsh`/`console`、複数フェンス、非 shell フェンスなど依存仕様の他ケースは本タスクの範囲外として変更していない。
- 環境: 標準 `python3` には pytest がなかったため、既存 `.venv` を PATH に追加した。機密情報は成果物に含めていない。
