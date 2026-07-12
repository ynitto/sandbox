切り口: フェンス構文の状態遷移を独立ヘルパに閉じ込め、既存のコマンド選択処理を最小変更で拡張した。

## 成果／サマリー

- `tools/kiro-project/kiro-project.py` に `_code_fence_lines(out)` を追加した。
- 空タグまたは言語タグ付きの開始フェンスを検出し、開始・終了マーカーを除いた内容行を複数ブロックにまたがって出現順に返す。
- 未閉じフェンスは入力末尾までを内容として扱う。
- `_first_command_line` は抽出ヘルパの結果を先に走査し、既存のフェンス外フォールバックを維持する。
- 複数フェンスの順序と未閉じフェンスを固定するユニットテストを追加した。

## 検証内容と結果

- `PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line`: `7 passed, 512 deselected`、終了コード 0。
- `git diff --check`: 終了コード 0。
- システム Python に pytest がないため、既存 `.venv` を PATH に指定して完了条件と同じ Python コマンドを実行した。

## 前提・未解決事項・範囲外

- 「行リスト」は全コードフェンスの内容をブロック出現順に平坦化した `list[str]` と解釈した。
- 開始マーカーは行前後の空白を許容し、````` または `````lang` を受理する。終了マーカーは Markdown の閉じフェンスとして単独の ````` のみを扱う。
- 改行文字そのものは `splitlines()` により戻り値へ含めず、各行の内容と順序は保持する。
- 未解決事項および範囲外で見つけた問題はない。
