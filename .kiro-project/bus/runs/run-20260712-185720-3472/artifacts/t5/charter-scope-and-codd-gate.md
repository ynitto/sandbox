# synth_verify-_first_comm-172544 調査結果

## 成果／サマリー

- 変更対象は `tools/kiro-project/kiro-project.py` と `tools/kiro-project/tests/test_kiro_project.py` だけで完結できる。対象関数 `_first_command_line` とそのユニットテストはいずれも `tools/kiro-project` 配下にあり、charter の変更範囲制約を満たす。リポジトリへの変更は行っていない。
- `codd-gate` は `/Users/nitto/.local/bin/codd-gate` にインストール済み。今回活用する具体的な結線は、kiro-project 設定の差分ゲートを次の 1 点とする。

  ```yaml
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV"'
  ```

  これによりタスク固有 verify（`python3 -m pytest tools/kiro-project/tests -q -k first_command_line`）の成功後、done 確定前に今回の差分について doc/code/test の一貫性を決定的に検査できる。既存負債を今回差分の失敗として扱わない差分ゲートであり、codd-gate スキルの推奨結線にも一致する。

## 検証内容と結果

- `.codegraph/` は対象 repo に存在しないため、通常の `rg` とファイル参照で所在を確認した。
- `command -v codd-gate`: `/Users/nitto/.local/bin/codd-gate`。
- `codd-gate verify --help`: 終了コード 0。`--base` と `$KIRO_BASE_REV` を用いる差分 verify が利用可能であることを確認した。
- 指定完了コマンドをシステム既定 `python3` で実行: 終了コード 1（`No module named pytest`）。
- 既存の `/Users/nitto/Workspace/sandbox/.venv/bin` を PATH 先頭にして同じコマンドを再実行: 終了コード 5、`512 deselected`。現ブランチには `first_command_line` に一致するテストがまだ存在しないため、完了条件は現時点で未達。
- `git status --short`: 出力なし。指定 worktree は無変更。

## 前提・未解決事項・範囲外

- 前提: 本タスクの担当は charter 制約確認と codd-gate 結線案の具体化だけであり、`_first_command_line` の実装修正やテスト追加は別担当タスクが行う。したがって本タスクではソースを編集しない。
- 前提: codd-gate の活用は既存の公式差し込み点 `regression_cmd` への 1 点の結線を意味する。負債ラチェットや intake 連携は追加しない。
- 未解決: 別担当による `first_command_line` テスト追加がこの worktree に未反映のため、指定 pytest コマンドを終了コード 0 にできない。反映後は pytest を導入済みの Python 環境で再実行が必要。
- 範囲外で見つけた問題: システム既定 `/usr/bin/python3` に pytest がない。リポジトリ外の環境変更は本タスクの書込範囲外なので実施していない。
