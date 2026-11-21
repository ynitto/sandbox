# エージェント活用の月次効率レポート

定常業務マップ F4 の月次レポートを作り、改善施策の優先付けに使う観測を共有する。
`tools/weekly-report` は週次の活動サマリとリリースノートの下書きを扱う。このツールは
月次の効率・効果測定を扱う。`efficiency.py collect` が集計し、このツールが集計済み JSON を Markdown に整形する。

## 実行

Python 標準ライブラリで動作する。リポジトリルートから、対象リポジトリと UTC の対象期間を指定して集計する。
`collect` の終了境界は包含なので、例では対象月の最終日の末尾を指定する。

```sh
python3 .github/skills/gitlab-efficiency-metrics/scripts/efficiency.py collect \
  --projects group/project --since 2026-10-01 --until 2026-10-31T23:59:59.999999Z \
  --format json > october.json
python3 tools/efficiency-report/efficiency_report.py --input october.json --month 2026-10
```

`--month` の既定は UTC で前月。指定値は Markdown の対象月と投稿識別子に使う。
集計範囲は入力の `window` を表示するので、指定月と入力の期間をそろえる。
`totals.raw_metrics` は必須。その他の表示項目は、欠落時に「未確認」、件数が 0 で平均を出せない場合や空の内訳は「対象なし」と表示する。
閉じた Issue 数は `time.ai_actual` と `time.human_actual` の計測件数を合計する。
正味時間の集計対象は着手時刻を検出できた Issue。ユーザー別の削減推定には差し戻し分が含まれない。

## 投稿と定期実行

[設定例](config.example.json) をコピーし、`--config` で指定する。パスは実行時の作業ディレクトリを基準とする。
`input`、`audit_input`、`hub_issue_url` は CLI 引数で上書きできる。
ハブ URL の優先順は `--hub-issue-url`、設定の `hub_issue_url`、環境変数 `GITLAB_HUB_ISSUE_URL`。
URL は GitLab の HTTPS ブラウザ URL（`https://gitlab.example.com/group/project/-/issues/42`）を指定する。

既定は dry-run で Markdown を標準出力に表示する。`--post` または `post_enabled: true` で投稿を有効にでき、
`--dry-run` を併記した場合は dry-run が優先する。投稿時はコメント権限のある `GITLAB_TOKEN` を環境変数に設定する。
プロキシが必要な環境では起動環境の `HTTPS_PROXY`、`NO_PROXY` などを設定する。
GitLab ハブ issue のコメントをページごとに確認し、先頭行が `[efficiency-report: YYYY-MM]` と一致すればスキップする。
照合と投稿は別の API 操作なので、同一ハブへの実投稿は逐次実行する。正常終了と重複スキップは終了コード 0、入力や通信のエラーは 2。

[Dagu の例](dags/efficiency-report.example.yaml) は UTC の毎月 1 日朝 9 時に前月分を収集してレポートを生成する。
Linux の GNU `date` を使う。Dagu のタイムゾーン、対象プロジェクト、スクリプト・設定・dotenv のパスを環境に合わせる。
初回は dry-run の出力で対象月・対象リポジトリ・前提を確認してから、設定で投稿を有効にする。

## 任意の agent-audit 集計

`--audit-input`（設定では `audit_input`）を省略した場合、利用量と実行品質は「未集計」と表示する。
次のコマンドで利用量と実行品質を JSON に保存できる。CLI 別の実測入力、実測出力、推定トークン、未計測実行数を別々に表示する。

```sh
agent-audit usage --period month --by agent_cli --json > usage.json
agent-audit stats --period month --json > stats.json
```

`usage` と `stats` の `period: "month"` は実行時点の UTC 当月を表し、出力に年月は含まれない。
`usage.json` を直接指定すると集計対象月は「未確認」、失敗クラスは「未集計」と表示する。
前月レポートに添える際は、対象月内に保存した集計を使い、次の組み立て方で実際の集計月を明記する。
月末に保存した集計は保存時点までの値となる。下の例で作成したファイルは `--audit-input audit.json` で指定する。`usage` には usage 出力全体、任意の `stats` には
同じ月の stats 出力全体を入れる。外側のオブジェクトはこのツールの入力形式。
`stats.tools[].error_class` からツール別の失敗クラス件数を表示する。`report` の通常出力は Markdown。

```sh
python3 -c 'import json, sys; from pathlib import Path; print(json.dumps({
    "month": sys.argv[1],
    "usage": json.loads(Path("usage.json").read_text(encoding="utf-8")),
    "stats": json.loads(Path("stats.json").read_text(encoding="utf-8")),
}, ensure_ascii=False))' 2026-10 > audit.json
```

## 数値の読み方

削減時間・コストは人の生産性を変えた `low / mid / high` の 3 シナリオを併記する。
人の生産性が高い前提ほど推定削減は小さくなる。時間削減は人の実働日見積もりと AI の実時間を比べた概算。
人件費・実装スループット・レビュー速度は入力の `params` があれば転記する。現行の `collect` は
`params` を出力しないため、通常は「未確認」。前提を変えた推定は収集側の `--params-file` で行う。

AI 判定は収集時の本文マーカーと設定されたユーザーに依存する。マーカーの欠落や共有アカウントによって
分類がずれる場合がある。成果物のバイト数には自動生成物や vendor の追加が混入し得る。
差し戻しや破棄 MR の件数から候補を挙げ、処理量や個別の理由を見て優先度を判断する。
前提の詳細は [gitlab-efficiency-metrics](../../.github/skills/gitlab-efficiency-metrics/SKILL.md) を参照。

## テスト

```sh
python3 -m unittest discover -s tools/efficiency-report/tests -v
```

HTTP はモックで検証する。
