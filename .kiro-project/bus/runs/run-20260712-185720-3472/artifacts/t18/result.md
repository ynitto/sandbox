切り口: fenced 応答だけでなく、LLM が実際に返しやすい「日本語の前置き＋裸のコマンド」という回帰境界を直接固定する。

## 成果／サマリー

- `test_first_command_line_skips_unfenced_prose_before_command` を追加した。
- 入力 `検証コマンドは次のとおりです。\npython3 -m pytest tools/kiro-project/tests -q` から、2 行目のコマンドだけを返すことを検証する。
- テストを成立させる最小変更として、`_first_command_line` がコメント・空行に加え、`_looks_like_shell_command` で散文と判定された行も読み飛ばすようにした。フェンス内優先と既存の行順は変更していない。

## 検証内容と結果

- 完了条件: `PATH="/Users/nitto/Workspace/sandbox/.venv/bin:$PATH" python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
  - 結果: 終了コード 0、`8 passed, 512 deselected in 0.20s`
- `git diff --check`
  - 結果: 終了コード 0、警告なし。
- システム標準の `python3 -m pytest ...` は pytest 未導入で実行できなかったため、依存成果 t7 と同じく既存 `.venv` を PATH の先頭に置いた。コマンド本体と選択条件は完了条件どおり。

## 前提・未解決事項・範囲外

- 前提: 「フェンスなし・前置き散文＋コマンド行」は、改行で分離された日本語説明行の直後に単一シェルコマンドが続く LLM 応答を指す。
- 前提: 新規テストを追加するだけでは現実装で失敗するため、完了条件を満たす範囲の最小実装修正も本タスクに含めた。
- 変更対象は `tools/kiro-project/kiro-project.py` と `tools/kiro-project/tests/test_kiro_project.py` のみ。依存追加、リファクタリング、commit/push は行っていない。
- 未解決事項・範囲外で見つけた問題: なし。
