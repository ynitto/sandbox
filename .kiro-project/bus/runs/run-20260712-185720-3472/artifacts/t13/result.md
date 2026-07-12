切り口: 再試行中は静かに自己修復し、全候補を棄却した時だけ「コマンド行なし」という根因を既存の空 verify 失敗経路へ添えた。

## 成果／サマリー

- `synth_verify` で `_first_command_line` が `None` を返した場合の不採用理由を「応答に実行可能なコマンド行がなかった」と明確化した。
- 一時的な不採用ではログを出さず再試行を継続し、全試行失敗時のみ、タスク名と最終不採用理由を stderr へ出すようにした。
- 戻り値は従来どおり空文字のため、上位の verify 未定義・人への移譲経路を変更していない。
- 散文のみの応答が続くケースについて、空文字、試行回数、エラーメッセージの原因とタスク名を回帰テストで固定した。

## 検証内容と結果

- 完了条件 `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`: `12 passed, 512 deselected`、終了コード 0。
- `git diff --check`: 終了コード 0。
- 追加確認 `-k 'synth_verify or first_command_line'`: `15 passed, 1 failed, 508 deselected`。失敗は依存成果から継承した ANSI 付き出力の抽出回帰で、ANSI エスケープから始まる行を `_has_command_like_leading_token` が候補化できないもの。本タスクの `None` 失敗処理自体は期待どおり原因ログを出した。
- `codd-gate verify --base main`: 終了コード 1。変更ソースに対する既存設計文書の未更新判定と、既存テスト内サンプルパスの未解決参照による AMBER。

## 採用した前提・未解決事項・範囲外

- 「既存の失敗経路と整合」は、合成不能時の戻り値を空文字のまま維持し、上位の未検証／人への移譲を壊さないことと解釈した。
- `_first_command_line` の `None` は再試行可能な不採用であり、途中の試行ごとに警告すると成功時にも誤解を招くため、試行枯渇時だけ stderr に記録する前提を採用した。
- システム Python には pytest がないため、依存成果と同じ既存 venv を PATH の先頭に置いて指定コマンドを実行した。
- ANSI 付き出力の抽出回帰は依存タスク側の抽出ロジックに属し、本担当の `None` 失敗処理から外れるため変更していない。codd-gate の AMBER 解消も広範な文書・既存テストへ波及するため範囲外とした。
