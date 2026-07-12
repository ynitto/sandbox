切り口: フェンス抽出後の候補選別を独立ヘルパに集約し、シェル言語タグ残骸の誤採用を防いだ。

## 成果／サマリー

- `tools/kiro-project/kiro-project.py` に `_first_executable_line(lines)` を追加した。
- 候補行を順番に正規化し、空行、先頭が `#` のコメント、`bash` / `console` / `sh` / `shell` / `zsh` の言語タグ残骸、シェルコマンドとして不正な行を除外して、最初の実行可能行を返す。
- `_first_command_line` のフェンス内走査と既存フォールバックを同じ選別ロジックへ統合した。
- 言語タグがフェンス内の独立行として残るケースの回帰テストを追加した。

## 検証内容と結果

- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`: `9 passed, 512 deselected`、終了コード 0。
- `git diff --check`: 終了コード 0。
- `codd-gate verify --base main`: 終了コード 1。今回触れたコードに対する既存設計文書の未更新判定、および既存テスト内のサンプルパスを未解決参照とする AMBER が検出された。タスク範囲外の文書・既存テスト記述は変更していない。

## 採用した前提・未解決事項・範囲外

- 「言語タグ残骸」は、Markdown パーサや LLM 出力の揺れによりフェンス内容の独立行として残った代表的なシェル系タグと解釈した。大文字小文字は区別しない。
- フェンス内に有効な候補がなければ、従来どおり全文から候補を探す挙動を維持した。
- システム Python には pytest がないため、既存 venv を PATH の先頭に置き、指定されたコマンド文字列をそのまま実行した。
- codd-gate の AMBER 解消は設計文書や多数の既存テスト記述へ波及し、本タスクの最小変更範囲を超えるため未解決事項として報告する。
