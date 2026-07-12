# `$` / `%` prompt 接頭辞テスト成果

切り口: 正常系だけでなく、空白必須の境界を固定して `$pytest`・`%pytest`・`>` の誤除去も防ぐ。

## 成果・サマリー

- `_first_command_line` が `$ ` と `% ` の後に複数空白を伴うコマンドを素のコマンドへ正規化する subtest を追加した。
- `> pytest -q`、`$pytest -q`、`%pytest -q` は変更しない境界値 subtest を追加した。
- テストを成立させる最小実装として、`$ ` / `% ` の2文字を除去後に残余空白を除去する処理を追加した。
- 関連箇所に存在した競合マーカーは、競合していた既存3ケースをすべて保持する形で作業ツリー内容を解消した。git index は規約に従い操作していない。

## 検証

- 完了条件: `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`
  - 結果: 終了コード 0、`5 passed, 512 deselected, 5 subtests passed`。
- `git diff --check`: 終了コード 0。
- `codd-gate verify --repo-dir sandbox=. --base main --repo sandbox --json`: 終了コード 1。変更コードに対する既存設計書の stale 判定と、巨大テストファイル内の既存 fixture 文字列を broken-ref と判定したため。今回の局所的なテスト観点に文書更新は不要と判断した。

## 前提・未解決事項・範囲外

- 前提: 依存仕様どおり、prompt は空白を伴う `$ ` / `% ` だけを指し、`>` や空白なしの `$` / `%` は除去対象外とした。
- 前提: システム `python3` には pytest がないため、依存成果と同じ既存 `.venv` を PATH へ追加して指定コマンドを実行した。
- 未解決: 入力時点でテストファイルの git index が unmerged (`UU`) だった。作業ツリーの競合マーカーは解消済みだが、git add は禁止規約に従い実行していない。
- 範囲外: コードフェンス・散文判定など `_first_command_line` 全体の再実装は行っていない。
