# GitLab 週次レポート

複数の GitLab プロジェクトからマージ済み MR、クローズした issue、パイプラインを集め、週次レポートとリリースノート下書きを Markdown で生成する。投稿を有効にすると、2 つの文書をハブ issue のコメント 1 件にまとめる。Outlook への通知を起点とする配信手順は [Power Automate レシピ](power-automate/README.md) を参照。

## 前提と設定

Python 3.10 以降と GitLab 17.0 以降を使う。Python は標準ライブラリのみ。GitLab トークンには対象プロジェクトの読み取り権限が必要（例 `read_api`）。実投稿時はハブ issue を閲覧してコメントできる `api` スコープのトークンを使う。

[config.example.json](config.example.json) をコピーして値を設定する。

| キー | 設定内容 |
| --- | --- |
| `gitlab_url` | GitLab のベース URL |
| `token_env` | トークンを格納する環境変数名。例は `GITLAB_TOKEN` |
| `projects` | 数値 ID または `group/subgroup/project` の配列。同一プロジェクトは一つの表記で指定する |
| `hub_issue_url` | 同じ GitLab 上にあるハブ issue のブラウザ URL または API URL |
| `period_days` | 集約日数。既定は 7 |
| `include_open` | `true` で取得時点のオープン MR・issue 件数を追加する。既定は `false` |
| `post_enabled` | `true` で投稿を有効にする。既定は `false` |

## 実行

トークンを設定したシェルで、リポジトリのルートから実行する。

```sh
python3 tools/weekly-report/weekly_report.py --config ~/.config/weekly-report/config.json --dry-run
python3 tools/weekly-report/weekly_report.py --config ~/.config/weekly-report/config.json --days 14 --end-date 2026-11-16 --dry-run
```

終了日は既定で今日の UTC 日付。`--end-date` で指定できる。開始日の UTC 00:00 から終了日の UTC 00:00 の直前までを対象にする。同じ日に同じ日数で再実行すると期間は一致する。日をまたいだ再実行では元の終了日を指定する。

dry-run でも GitLab API から集約し、2 つの Markdown を標準出力に表示する。投稿の有効化は `WEEKLY_REPORT_POST=1` または `post_enabled: true` で行う。環境変数の `0` / `1` は JSON に優先し、`--dry-run` は両方に優先する。

実投稿時は先頭行の `[weekly-report: YYYY-MM-DD/YYYY-MM-DD]` をハブ issue の既存コメントと照合する。同じ識別行があればスキップする。期間ごとに 1 件なので、異なるプロジェクト構成のレポートを同一期間に配信する場合はハブ issue を分ける。照合と投稿は別の API 操作なので、同一ハブの実投稿は逐次実行する。取得・生成・投稿でエラーが起きると終了コードは 2。生成、投稿、重複スキップの成功時は 0。

[dags/weekly-report.example.yaml](dags/weekly-report.example.yaml) を Dagu の DAG ディレクトリへコピーし、設定、ツール、dotenv のパスを変更する。例は Dagu のタイムゾーンで月曜朝 9 時に起動する。dotenv にトークンを設定し、初回は dry-run の出力を確認した上で投稿を有効化する。

## 集約と分類

- MR は `merged_at`、issue は `closed_at`、パイプラインは `created_at` で期間を判定する。issue は取得時点でクローズしているものを集約するため、一度閉じて再オープンした履歴の集計には使えない。
- パイプライン成功率は、取得時点で `success` の件数を対象パイプラインの総数で割る。実行中・キャンセルも分母に含まれる。子パイプラインも取得し、全体の成功率は各プロジェクトの件数を合計して算出する。総数 0 件は「対象なし」と表示する。
- 関連 issue は MR の `closes_issues` と、タイトル・説明にある `#123`、`group/project#123`、GitLab 形式の issue URL を拾う。文章中の参照も含むため、すべてがクローズ対象とは限らない。

リリースノートはタイトルとラベルを大文字・小文字を区別せず照合し、下表の上から最初に一致した分類を使う。英語は単語境界、日本語は部分一致で判定する。箇条書きでは対象の Conventional Commits 形式の接頭辞を外す。

| 分類の優先順 | キーワード |
| --- | --- |
| 修正 | `fix`、`fixed`、`fixes`、`bug`、`bugfix`、`hotfix`、修正、不具合 |
| 新機能 | `feat`、`feature`、`enhancement`、新機能、機能追加 |
| 改善 | `improve`、`improvement`、`perf`、`performance`、`refactor`、`doc`、`docs`、改善、最適化 |
| その他 | どのキーワードにも一致しない |

箇条書きの説明は MR タイトルに依存する。利用者への影響、内部作業の取捨選択、公開に適した表現は下書きのレビューで確認する。ハブ issue と通知先の閲覧範囲は、集約するプロジェクトの情報を共有できる範囲に設定する。

## テスト

```sh
python3 -S -m unittest discover -s tools/weekly-report/tests -v
```

HTTP 呼び出しをモックする。GitLab への接続や localhost のソケットは不要。
