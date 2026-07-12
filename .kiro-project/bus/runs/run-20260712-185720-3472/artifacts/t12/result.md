切り口: 「候補なし」を空文字ではなく Optional の欠如値 `None` に統一し、散文のみを型と回帰テストの両面でコマンドから分離した。

## 成果／サマリー

- `tools/kiro-project/kiro-project.py` の `_first_executable_line` と `_first_command_line` を `Optional[str]` 契約に揃え、すべての抽出規則で候補がない場合は `None` を返すようにした。
- コメントだけの入力と、句読点のない英語散文だけの入力について `None` を明示的に検証する回帰テストを追加した。
- `synth_verify` は従来から `if not cand` で欠如を処理しているため、再試行と最終的な空 verify の外部挙動は維持される。

## 検証内容と結果

- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`: `12 passed, 512 deselected`、終了コード 0。
- `git diff --check`: 終了コード 0。
- `codd-gate verify --base main`: 終了コード 1。変更ソースに対する既存設計文書の未更新判定と、既存テスト内のサンプルパスを未解決参照とする AMBER のみ。

## 採用した前提・未解決事項・範囲外

- 「既存の型契約」は、コマンド取得成功時のみ `str`、取得不能時は `None` を返す `Optional[str]` と解釈した。内部ヘルパーも同じ欠如表現へ統一した。
- 散文だけの応答には、日本語の句読点付き散文だけでなく、`sh -n` がコマンドとして受理し得る句読点なし英語散文も含むと解釈した。
- システム Python には pytest がないため、依存タスクと同じ既存 venv を PATH の先頭に置いて指定コマンドを実行した。PATH 未調整の同コマンドは `No module named pytest` で実行不能だった。
- codd-gate の AMBER 解消は設計文書や多数の既存テスト記述へ波及し、本タスクの最小範囲外なので変更していない。
