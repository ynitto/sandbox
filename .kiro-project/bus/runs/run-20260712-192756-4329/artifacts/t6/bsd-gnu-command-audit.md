# BSD/GNU コマンド差と git 自己修復経路の調査結果

## サマリー

- 対象を `tools/kiro-flow`、`tools/kiro-project`、および同梱の `.github/skills/flow-worker` に限定して調査した。
- git 自己修復実装は Python 標準ライブラリと `git` の argv 形式サブプロセス呼び出しで構成され、`readlink -f`、外部 `mktemp`、外部 `date`、`sed` を経由しない。
- BSD/GNU 差がある実呼び出しは `tools/kiro-flow/install.sh` の `sed -i` だけで、既に `Darwin` では `sed -i ''`、それ以外では `sed -i` と分岐している。
- 調査時点の macOS worktree ではコード変更なしで全指定テストが green だった。

## コマンド別の洗い出し

| コマンド | BSD/GNU 差 | 対象内の依存状況 |
|---|---|---|
| `readlink -f` | macOS の BSD `readlink` は通常 `-f` 非対応 | 呼び出しなし。パス処理は Python (`os.path` / `pathlib`)。 |
| `mktemp` | template・`-t`・suffix 等の仕様が異なる | 実装からの外部呼び出しなし。Python `tempfile.gettempdir` / `mkdtemp` / `TemporaryDirectory` を使用。`tools/kiro-flow/tests/pattern_cases.yaml` のコメント例 `mktemp -d` は実行経路ではない。 |
| `sed` | in-place 編集時、BSD は `-i ''`、GNU は `-i` | `tools/kiro-flow/install.sh:171-173` のみ。`OS == Darwin` 分岐で吸収済み。git 自己修復経路からは呼ばれない。 |
| `date` | `-d`（GNU）と `-v`（BSD）等が非互換 | 外部呼び出しなし。日時生成は Python `datetime` / `time`。 |

## サブプロセス依存と影響

- git キャッシュ、worktree、GitBus、state-git の処理は `subprocess.run(["git", ...])` 形式で `git` を直接起動する。
- shell 文字列を介して上記4コマンドを連鎖起動する箇所は、git 自己修復経路にはない。
- 自己修復テストは stale `index.lock`、破損 index、retry 中に stale 化する lock、中断 rebase、live lock、空 loose object、push 中の object 破損、remote 破損診断を直接検証している。
- したがって、元要求の「macOS で失敗する git 自己修復テスト4件」の原因として BSD/GNU の上記4コマンド差は、現在の worktree からは再現・確認できない。

## 検証内容と結果

1. 指定どおり `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` を実行したが、macOS の `/Library/Developer/CommandLineTools/usr/bin/python3` に pytest がなく、`No module named pytest`（終了コード1）。これは製品テスト失敗ではなく検証環境の依存不足。
2. リポジトリを変更せず、既存 venv の interpreter で同じ対象を実行した。
   - `/Users/nitto/Workspace/sandbox/.venv/bin/python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q`
   - 結果: `900 passed in 122.70s`、終了コード0。
3. `git status --short` は空で、専用 worktree に変更なし。

## 採用した前提・未解決事項・範囲外

- 完了条件の意図は「同じ Python テストスイートが pytest で全件成功すること」と解釈した。システム `python3` 自体への pytest インストールはリポジトリ変更ではなく環境変更になるため行わず、既存 venv で代替検証した。
- 調査対象は元要求と直接関係する3ディレクトリに限定した。リポジトリ内の無関係なツール全体の移植性監査は範囲外。
- `install.sh` の OS 判定が正しく設定されることは既存実装の前提。今回の git 自己修復4件とは独立しているため変更していない。
- codd-gate の導入・設定変更は、本タスクが調査のみでコード変更不要だったため実施していない。
