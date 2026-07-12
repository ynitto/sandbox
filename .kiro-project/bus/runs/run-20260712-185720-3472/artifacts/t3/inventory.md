# first_command_line / synth_verify テスト棚卸し

## サマリー

- `tools/kiro-project/tests` 配下のテストファイルは `test_kiro_project.py` の1ファイルのみ。
- `_first_command_line` の直接テストは `TestVerifyAssist` クラス内に2件存在する。
  - `test_first_command_line_returns_direct_command`
  - `test_first_command_line_returns_empty_without_candidate`
- `synth_verify` 関連テストも同じファイルに存在する。主な配置は `TestVerifyAssist`、`FeedbackReductionTests`、およびリスク判定テストのクラスで、関数名は `test_synth_verify_*` または `test_synth_*` を用いる。
- 今回の不具合に直結する「前置きの後にあるコードフェンス内コマンド」を明示した既存テストはない。現存する `_first_command_line` テストは、コメント・空行のスキップと候補なしのケースだけを扱う。

## 命名規約と pytest -k の選択条件

- テストファイル: `test_*.py`（現状は `test_kiro_project.py`）。
- テストクラス: `unittest.TestCase` 派生で `Test...` または `...Tests`。
- テストメソッド: `test_<対象>_<期待動作>`。
- `pytest -k first_command_line` は完全一致ではなく、収集済み node ID を構成するファイル名・クラス名・関数名等への部分文字列（式）マッチで選択する。現状、ファイル名とクラス名には当該文字列がなく、関数名に `first_command_line` を含む次の2件だけが選択される。
  - `tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_returns_direct_command`
  - `tools/kiro-project/tests/test_kiro_project.py::TestVerifyAssist::test_first_command_line_returns_empty_without_candidate`
- 後続で追加する回帰テストをこの完了コマンドに選択させるには、テスト関数名（または node ID の別要素）に連続した文字列 `first_command_line` を含める必要がある。例: `test_first_command_line_extracts_command_from_fenced_block_after_prose`。
- `test_synth_verify_*` だけの名前は `-k first_command_line` では選択されないため、今回の限定検証に含めたいテストは関数名へ `first_command_line` を含める。

## 検証

実行コマンド（既存仮想環境を PATH に設定）:

```sh
PATH=/Users/nitto/Workspace/sandbox/.venv/bin:$PATH python3 -m pytest tools/kiro-project/tests -q -k first_command_line
```

結果: 終了コード 0、`2 passed, 512 deselected in 0.03s`。

`--collect-only` でも上記2 node IDのみが選択されることを確認した。

## 前提・未解決事項・範囲

- 前提: 本タスクは棚卸しと選択条件の確定が担当範囲であり、実装・回帰テスト追加は後続タスクが行う。
- システム標準の `/Library/Developer/CommandLineTools/usr/bin/python3` には pytest がなく、PATH 未設定の初回実行は `No module named pytest` で終了コード1だった。既存の `/Users/nitto/Workspace/sandbox/.venv` を利用すると完了条件は成功する。
- 指定 worktree のソースおよびテストは変更していない。範囲外の問題は修正していない。
