# `_first_command_line` 前置き＋フェンス回帰テスト案

切り口: 広いパラメータ化ではなく、障害を起こした LLM 応答形状を一つの因果が明瞭な最小回帰テストとして固定する。

## 前提と完了判定

- この担当は実装本体の修正ではなく、テスト観点「LLM が前置き散文を付けてもフェンス内のコマンドを拾える」のケース設計を行う。
- `t7` の確定仕様どおり、フェンス内候補はフェンス外の前置き散文より優先する。
- 後続実装後、テスト名を `first_command_line` で絞り込め、指定コマンドが終了コード 0 になることを受入条件とする。

## 成果: 追加する最小回帰ケース

追加先: `tools/kiro-project/tests/test_kiro_project.py` の既存 `_first_command_line` テスト群。

````python
def test_first_command_line_prefers_command_in_fence_after_prose(self):
    out = """以下のコマンドで検証できます:
```bash
python3 -m pytest tools/kiro-project/tests -q
````
"""
    self.assertEqual(
        km._first_command_line(out),
        "python3 -m pytest tools/kiro-project/tests -q",
    )
```

このケースは次を同時に固定する。

1. 日本語の前置き散文をコマンドとして返さない。
2. `bash` タグ付きコードフェンスを認識する。
3. フェンス記号を飛ばし、フェンス内の最初のコマンド行を返す。
4. コマンド文字列を改変せず返す。

複数フェンス、タグなしフェンス、非 shell フェンスなどは `t7` の別受入ケースであり、この障害再現ケースには混ぜない。失敗時にどの規則が壊れたかを特定しやすくするためである。

## 検証

- 現在の専用 worktree で既存テストを実行:
  `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
- 結果: `5 passed, 512 deselected, 5 subtests passed`、終了コード 0。
- システムの標準 `python3` には pytest がないため、依存成果 `t7` と同じく既存 `.venv` を `PATH` の先頭に指定した。
- 提案ケースは現実装では前置き行を返すため、実装修正と同時に追加して Red→Green を確認すること。

## 未解決事項・範囲外

- 現実装の修正とテストファイルへの追加は、この generate 担当の範囲外として行っていない。
- `codd-gate` の導入・設定変更も行っていない。今回の成果は単一ユニットテスト案であり、既存 pytest による検証が直接的である。
- 範囲外の追加問題は確認していない。
