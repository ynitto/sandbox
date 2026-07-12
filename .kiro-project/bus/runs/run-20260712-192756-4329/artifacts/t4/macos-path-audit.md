# macOS 固有パス差分の棚卸し

## 前提

- この観点タスクの完了は、macOS 上で `tempfile`、`TMPDIR`、`os.path.realpath`、大文字小文字の扱いを実測し、指定された全テストが終了コード 0 になることと解釈した。
- 既存コードがすでに green なら、不要な修正や追加リファクタリングは行わない。

## 実測結果

実行環境は macOS、Python 3.9。Python で得た値は次のとおり。

```text
tempfile.gettempdir() = /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T
TMPDIR = /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/
realpath(tempdir) = /private/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T
TemporaryDirectory = /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/tmp1hcq7_td
realpath(TemporaryDirectory) = /private/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/tmp1hcq7_td
/var realpath = /private/var
```

したがって、`tempfile` や `$TMPDIR` 由来の `/var/...` と、Git・OS・`realpath` が返す `/private/var/...` は同じ実体でも文字列比較では不一致になる。パス同一性を検証する箇所は、期待値と実値の両辺を `os.path.realpath` で正規化するのが妥当。

大文字小文字の実測:

```text
lower exists = True
upper exists = True
samefile(lower, upper) = True
filesystem case-insensitive = True
```

小文字名で作ったファイルを大文字名でも参照でき、`os.path.samefile` も同一と判定した。このボリュームでは、パス文字列の大小文字差をファイルの別物判定に使えない。既存パス同士なら `os.path.samefile`、未作成パスを含む比較なら用途に応じて `realpath` と大小文字正規化を検討する。ただし、大小文字を区別する macOS ボリュームもあり得るため、製品コードで無条件に `casefold` するのは避ける。

## 検証

```text
python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q
900 passed in 120.25s (0:02:00)
exit code: 0
```

完了条件を満たした。指定 worktree の `git status --short` は空で、ソースコード・テストへの変更はない。

## 未解決事項・範囲外

- 初回実行時はシステム Python に pytest がなく失敗したため、ユーザー環境へ pytest 8.4.2 をインストールして再実行した。リポジトリ内容は変更していない。
- この観点では再現するテスト失敗はなかった。他タスクの修正状況や別 macOS ボリューム形式での挙動は範囲外。
