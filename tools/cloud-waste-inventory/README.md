# cloud-waste-inventory

クラウド CLI からリソース一覧を取得し、削除・縮小候補と月額削減見込みを Markdown で出力します。設定で有効にすると、GitLab ハブ issue にも投稿します。Python 3.10 以上の標準ライブラリで動作します（Linux・macOS）。

## 想定

- 主対象は Azure CLI（`az`）です。事前に対象サブスクリプションの一覧取得権限を持つアカウントで認証してください。VM の電源状態を取得するため `az vm list --show-details --output json`、managed disk には `az disk list --output json` を使います。[VM コマンド](https://learn.microsoft.com/en-us/cli/azure/vm?view=azure-cli-latest#az-vm-list) [ディスクコマンド](https://learn.microsoft.com/en-us/cli/azure/disk?view=azure-cli-latest#az-disk-list)
- プロキシ必須の社内環境を想定しています。CLI サブプロセスには `HTTPS_PROXY`、`NO_PROXY` などを含む現在の環境変数がそのまま引き継がれます。
- クラウドに対しては許可済みの読み取り専用コマンドだけを実行します。削除・停止・縮小は実行しません。
- `commands` は JSON の引数配列です。Azure では設定例のコマンドの末尾に `--subscription` と `--resource-group` をそれぞれ値つきで追加できます。省略時は Azure CLI の現在の設定に従います。他のクラウドを使う場合は、実装の `PROVIDERS` に読み取り専用コマンドの検証関数と VM・ディスクの出力変換関数を追加し、JSON の `provider` と `commands` で選択します。各変換関数は一覧の要素を ID・SKU・状態を持つ `Resource` に変換します。

## 判定と見積り

| 候補 | 条件 | 1 件あたりの月額削減見込み |
| --- | --- | --- |
| 停止中 VM | `powerState` が `deallocated` または `stopped`（`VM ` 接頭辞つきも可） | 削除候補。現在の VM サイズの設定月額 |
| 未接続ディスク | managed disk の `diskState` が `Unattached` | 削除候補。ディスク ID の設定月額 |
| 過大 SKU | VM サイズが `oversized_sizes` のキーにある | 縮小候補。現在サイズと縮小先サイズの設定月額の差（下限 0） |

停止中の判定には取得時点の状態を使い、停止期間や稼働日数は参照しません。過大 SKU は設定したサイズ対応表に基づく候補です。CPU・メモリ使用率は収集しません。停止中かつ過大な VM は両方の理由を表示し、1 件として削除時の見積りだけを合計します。

`vm_monthly_prices` は VM サイズ、`disk_monthly_prices` はディスクの完全なリソース ID をキーに、`currency` で指定した通貨の月額を JSON の数値で設定します。ディスクは同じ SKU でも容量や契約により料金が異なるため、ID 単位で設定します。設定例の単価表は空です。必要な単価がなければ「見積り不可」と表示し、合計には見積り可能分だけを含めます。

単価は対象の状態と契約に応じた月額を入力してください。VM サイズの設定額は同じサイズに共通して適用されます。割り当て解除済み VM のコンピュート課金や、VM に付随するストレージなどの請求は取得しないため、停止中 VM の見積りは設定単価ベースです。

## 実行と投稿

```sh
python3 tools/cloud-waste-inventory/cloud_waste_inventory.py \
  --config tools/cloud-waste-inventory/config.example.json
```

- `dry_run` の既定値は `true` です。標準出力に Markdown、標準エラーに実行状況を出します。`false` を明示した場合は、環境変数 `GITLAB_HUB_ISSUE_URL`（`https://gitlab.example.com/group/project/-/issues/42` 形式）と `GITLAB_TOKEN`（対象 issue のコメントを取得・作成できるトークン）を使います。[GitLab Notes API](https://docs.gitlab.com/api/notes/)
- Markdown の先頭に UTC の棚卸し日と候補件数を入れます。GitLab の既存コメントをページ単位で確認し、同じ日付の識別行があれば、件数が変わっていても投稿をスキップします。同じ OS ユーザーでの並行投稿はロックで防ぎます。複数ホストで運用する場合は投稿を担当する実行元を 1 台にしてください。
- `timeout_seconds` は CLI の各呼び出しと GitLab 通信に適用されます（既定 60 秒）。CLI 不在、認証・権限エラー、コマンド失敗、タイムアウト、JSON 出力の不備があれば標準出力に「未確認（理由）」を出し、コード 3 で終了します。未確認なら投稿処理には進みません。
- 正常終了・dry-run・重複によるスキップはコード 0 です。設定や GitLab 投稿処理のエラーは標準エラーに理由を出し、コード 2 で終了します。

## Dagu の週次実行

1. `config.example.json` と `dags/cloud-waste-inventory.example.yaml` を運用先にコピーし、DAG 内のパスと対象範囲・単価を合わせます。スケジュール例は Dagu の実行環境の時刻で毎週月曜 09:00 です。
2. dry-run で手動実行し、Dagu のログで候補と月額を確認します。dry-run 専用の場合は例の `dotenv` を省略できます。
3. 投稿を有効にする場合は `dotenv` に指定したファイルに GitLab の環境変数を設定し、JSON の `dry_run` を `false` に変更します。プロキシ設定は Dagu の実行環境で用意してください。

## テスト

```sh
python3 -m unittest discover -s tools/cloud-waste-inventory/tests -v
```
