# git-stale-branches

ローカルブランチの最終コミットとマージ状況を表示し、掃除候補を一覧にします。Python 3 の標準ライブラリと、PATH にある Git を使います。

## 使い方

対象リポジトリの中で実行します。

```sh
python3 /path/to/tools/git-stale-branches/git_stale_branches.py
python3 /path/to/tools/git-stale-branches/git_stale_branches.py --base release --days 30 --protect staging --json
python3 /path/to/tools/git-stale-branches/git_stale_branches.py --delete-merged
```

## オプション

| オプション | 説明 |
| --- | --- |
| `--base BRANCH` | マージ判定に使うローカルブランチ。既定は `main`。 |
| `--days N` | 古さのしきい値となる非負整数。既定は 90 日。 |
| `--protect BRANCH` | 保護するブランチ名を追加する。繰り返し指定できる。 |
| `--delete-merged` | マージ済みの候補を `git branch -d` で削除する。既定では報告のみ。 |
| `--json` | ブランチごとのオブジェクト配列を標準出力に書く。 |
| `-h`, `--help` | ヘルプを表示する。 |

## 判定と出力

ブランチの先端コミットがベースの履歴に含まれていればマージ済みです。先端コミットのコミッター日時から実行時点までの経過日数を整数日で求めます。マージ済み、または経過日数が `--days` を超えるブランチが候補になります。`main`、`master`、`develop`、指定したベース、現在のブランチ、`--protect` で追加したブランチは保護します。

最終コミットが古い順に表示し、同時刻ならブランチ名順に並べます。表では候補に `*` を付け、末尾に候補名を表示します。JSON の各要素には `name`、`merged`、`last_commit_date`（UTC）、`age_days`、`current`、`protected`、`candidate` が入ります。

表と JSON は削除前の一覧です。削除対象はマージ済みの候補に限ります。削除の失敗理由は標準エラーに出します。JSON モードでは削除したブランチ名も標準エラーに出します。

**注意** `--delete-merged` は経過日数に関係なくマージ済みの候補を削除します。今日マージしたブランチも対象です。

## 既知の制限

squash merge や cherry-pick で変更を取り込んでも、元のコミットがベースの履歴に含まれなければマージ済みとは判定されません。削除時は Git 自身が上流ブランチまたは HEAD へのマージ状況を確認します。別の worktree でチェックアウト中のブランチなど、Git が安全削除を拒否する場合もあります。

## 終了コード

| コード | 意味 |
| --- | --- |
| 0 | 正常終了。候補がある場合も含む。 |
| 2 | 引数エラー、リポジトリ外、ベースなし、Git コマンドや削除の失敗。 |
