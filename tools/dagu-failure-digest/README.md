# Dagu の実行失敗集約

Dagu の実行履歴からステップ失敗や起動失敗を拾い、GitLab ハブ issue にまとめてコメントする。nightly-check-digest は各ツールが残した結果ファイルを集約する。dagu-failure-digest は Dagu の実行履歴を読むので、結果ファイルが書かれる前に落ちた実行も履歴に残っていれば拾える。両方の報告は同じハブ issue に載り、GitLab メール → Power Automate → Teams の既存ブリッジで届く。

## 履歴取得

Dagu v2 の `dagu history <DAG名>` は指定した DAG の履歴を返す。`--format json` は JSON 出力、`--limit` は最大件数を指定する。このため設定例では DAG ごとに実行し、最大 100 件を読む。[Dagu Operations](https://docs.dagu.sh/server-admin/operations)・[CLI リファレンス](https://docs.dagu.sh/getting-started/cli#history)

既定の実行履歴は `~/.local/share/dagu/data/dag-runs/` 配下に保存される。環境に応じて、履歴ファイルを読んで全 DAG 分の JSON を標準出力に出すラッパーを用意し、`{dag}` を含まない `history_command` として設定できる。[Dagu 設定リファレンス](https://docs.dagu.sh/server-admin/reference)

**実 Dagu での動作は未検証。** `dagu history --format json` の正確なフィールド名は実機で確認する。パーサは配列または `runs` / `history` / `dagRuns` / `items` に配列を持つオブジェクトを読み、DAG 名、実行 ID、日時、ステップ配列のフィールド名の揺れを吸収する。実行とステップは、文字列のステータスが `failed`、`error`、`partially_succeeded`、`partial_success` のいずれかなら失敗と判定する。実行全体が `succeeded` でも、失敗ステップが 1 件以上あれば失敗として扱う。大文字小文字は区別しない。

## 設定と実行

Python 3.10 以降の標準ライブラリで動く。`config.example.json` をコピーし、対象 DAG とパスを環境に合わせる。履歴コマンドはシェルを介さず設定ファイルのディレクトリで実行し、相対パスもそこを基準にする。

| 項目 | 内容 |
| --- | --- |
| `history_command` | 必須。コマンドと引数の配列。`{dag}` があれば DAG 名で置換して DAG ごとに実行し、なければ全体の履歴取得として 1 回実行する。 |
| `history_limit` | 履歴の上限を判定する正の整数。既定は 100。`history_command` の `--limit` と揃えること。 |
| `dags` | `{dag}` を使う場合に必須。対象 DAG 名の配列。 |
| `state_file` | 状態ファイル。既定は `~/.local/state/dagu-failure-digest/state.json`。 |
| `dry_run` | 既定は `true`。本文の確認だけを行う。 |
| `timeout_seconds` | 履歴取得コマンド 1 回と GitLab API リクエストの制限時間（秒）。既定は 60。 |
| `lookback_hours` | source の状態がないときに遡る時間数。既定は 26。 |

```sh
python3 tools/dagu-failure-digest/dagu_failure_digest.py --config ~/.config/dagu-failure-digest/config.json --dry-run
```

処理した本文は標準出力に出る。`--date YYYY-MM-DD` でタイトルの日付を変更できる。既定は実行環境の今日。

`dry_run` を `false` にすると実投稿を有効にできる。環境変数 `DAGU_FAILURE_DIGEST_DRY_RUN` の `0` は実投稿、`1` は dry-run として設定値より優先する。`--dry-run` はどちらよりも優先する。実投稿には `GITLAB_HUB_ISSUE_URL`（HTTPS のハブ issue URL）と `GITLAB_TOKEN` が必要。

## 状態と終了コード

状態は version 1 の JSON で、`sources` に DAG 名ごとの状態を持つ。全体を 1 回で取得する場合の source 名は `__all__`。各 source の `watermark` は ISO 8601 の日時、`reported_ids` は新しく報告した順に最大 1000 件の実行 ID を保持する。状態がない場合や状態が壊れている場合は、現在時刻から `lookback_hours` 時間前を境界とする。壊れた状態は標準エラーに通知する。

実行の基準時刻には終了時刻を使い、終了時刻がなければ更新時刻（`updatedAt`）、次いで開始時刻を使う。タイムゾーンのない時刻は UTC として扱う。基準時刻が watermark 以降で、実行 ID が未報告の失敗を載せる。  
終了時刻が記録されていない失敗は、開始時刻が watermark より古くても実行 ID が未報告なら載せる。

通常は取得できた実行の最新の基準時刻を watermark にする。source ごとの取得件数が `history_limit` 以上なら、取得できた実行の最古の基準時刻を watermark にし、履歴の上限に関する注意を載せる。注意だけでも投稿する。取得失敗や空の履歴では該当 source の状態を維持する。履歴取得に失敗し続ける間は、nightly-check-digest と同様に、毎日「未確認」を投稿し続ける仕様とする。

投稿に成功した場合か、投稿対象がなかった場合に状態を保存する。投稿と状態更新は `<state_file>.lock` の flock で直列化する。dry-run では状態を保存しないため、確認した失敗は次回の実投稿でも対象になる。

| 終了コード | 意味 |
| --- | --- |
| 0 | 少なくとも 1 source の履歴取得に成功し、処理を完了した。 |
| 2 | 設定、投稿、状態の読み書きなどのエラーで処理を完了できなかった。 |
| 3 | 全 source の履歴取得に失敗した。本文に未確認の理由を載せ、実投稿が有効なら投稿してから終了する。 |

## 朝の投稿 DAG を置く

1. [gitlab-issue-hub-agent の手順](../change-watch/agents/gitlab-issue-hub-agent/README.md)に沿って、ハブ issue と接続情報を用意する。
2. `dags/dagu-failure-digest.example.yaml` を `~/.config/dagu/dags/dagu-failure-digest.yaml` にコピーし、ツール、設定、hub agent の dotenv のパスを直す。予定は Dagu のタイムゾーンで毎朝 7:35。
3. dry-run で本文を確認し、実投稿を有効にする。

## テスト

リポジトリルートで実行する。履歴取得にはフェイクのコマンドを使い、GitLab API はモックする。

```sh
python3 -m unittest discover -s tools/dagu-failure-digest/tests
```
