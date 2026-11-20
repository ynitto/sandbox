# quota-alert

agent-audit が毎時記録するエージェント CLI の quota を読み、消費ペースから枯渇日時を予測します。警告または緊急のエージェントがいるときは、GitLab ハブ issue に投稿できる Markdown を生成します。Python 3.10 以上の標準ライブラリで動作します（Linux・macOS）。

## 入力

`ledger_dir`（既定 `~/.agents/budget/ledger`）内のすべての `*.jsonl` を読みます。対象は `event: "quota_snapshot"` の行です。台帳には前回と同じ使用率も追記されるため、使用率が変わらなかった時間を含めて計算できます。

```json
{"ts":"2026-11-20T12:00:00Z","workload":"audit","tool":"agent-audit","seconds":0,"ref":"quota","purpose":"quota","event":"quota_snapshot","agent_cli":"codex","quota_used_percent":42,"quota_source":"codex-cli","reset_at":"2026-11-22T12:00:00Z","quota_window_minutes":10080}
```

利用制限に到達した行には `quota_kind: "rate_limit"` が入ります。`quota_window_minutes` は任意です。必須の時刻 `ts`、CLI 名 `agent_cli`、使用率 `quota_used_percent` を読めない行と、実行時刻より後の観測は読み飛ばします。空または解釈できない `reset_at` は不明として扱います。タイムゾーンの指定がない日時は UTC として扱います。

## 予測方法と判定

CLI ごとに観測を時刻順に並べます。使用率が前回より 10 パーセントポイント以上下がった観測、または前回の観測時刻から今回の観測時刻までの間に前回の `reset_at` を迎えた観測を、新しいウィンドウの開始とします。

最新ウィンドウの最初と最後の使用率の差を経過日数で割り、日次の消費ペースを求めます。そのペースで残量を使い切るまでの時間を最終観測時刻に加えたものが枯渇予測日時です。観測幅が `min_span_hours`（既定 3 時間）未満、消費ペースが正でない、または最終観測のリセット時刻をすでに迎えた場合は予測できません。

線形近似なので、利用量の急増や小さな使用率低下を伴うリセットによって予測がずれる場合があります。最終観測が古い場合も、残量と利用制限の判定には最後に記録された値を使います。

| 判定 | 条件 |
| --- | --- |
| `critical` | 最新使用率が 100% 以上、`quota_kind` が `rate_limit`、残量が `critical_remaining_percent` 以下（既定 10%）、または枯渇予測が `reset_at` より前 |
| `warning` | 残量が `warn_remaining_percent` 以下（既定 30%）、または `reset_at` が不明で実行時刻から枯渇予測までが `warn_days` 日以内（既定 3 日） |
| `unknown` | 残量や利用制限による通知条件に該当せず、枯渇を予測できない |
| `ok` | 枯渇予測があり、通知条件に該当しない |

複数の条件を満たす場合は `critical` を優先します。

## 実行と通知

```sh
python3 tools/quota-alert/quota_alert.py \
  --config tools/quota-alert/config.example.json \
  --output /tmp/quota-forecast.json
```

`--now 2026-11-20T12:00:00Z` で判定時刻を固定できます。開始時に既存の出力ファイルを消します。正常終了はコード 0、設定や入出力のエラーはコード 2 です。

`dry_run: true`（既定）では、通知対象があれば「dry-run のため投稿しません」と Markdown を標準エラーに出します。予測 JSON は通常どおり書き、標準出力は空のままです。通知を有効にすると、その回に通知する対象がいる場合だけ Markdown を標準出力に出します。

CLI ごとに最後に生成した通知の署名（CLI 名、判定、UTC の予測日。予測なしは `null`）と時刻を `state_file` に記録します。前回通知から `cooldown_hours`（既定 24 時間）未満の場合、判定の深刻度（`ok` < `warning` < `critical`）が前回通知より上がったときだけ再通知します。判定が同等または改善した場合は、予測日が変わっても抑制します。dry-run では状態を更新しません。状態に記録するのは Markdown の生成時刻です。後続の GitLab 投稿が再試行を使い切った場合も、この時刻からクールダウンが適用されます。

## Dagu への導入

1. `config.example.json` を `~/.config/quota-alert/config.json` にコピーし、台帳と状態ファイルのパスを合わせます。
2. `dags/quota-alert.example.yaml` を `~/.config/dagu/dags/quota-alert.yaml` にコピーし、`/home/you` で始まるパスを実環境に合わせます。既存の GitLab ハブ用 change-watch の設定、接続設定、および dotenv の `GITLAB_HUB_ISSUE_URL` を使います。
3. dry-run のまま手動実行し、forecast JSON と Dagu のログに出る通知文を確認します。確認後に `dry_run: false` にすると投稿が有効になります。
4. DAG は毎時 15 分に実行します。agent-audit の収集時刻を変更している場合は、その後に予測が実行されるよう `schedule` を合わせてください。

`compose` が `change-watch --plugin gitlab_hub note -- ...` で標準出力を投稿用 JSON に変換します。`comment` は `NOTE` が `{"body"` で始まる場合だけ GitLab に POST します。

## forecast JSON v1

```json
{
  "version": 1,
  "source": "quota-alert",
  "generated_at": "2026-11-20T12:00:00Z",
  "agents": [
    {
      "agent_cli": "codex",
      "used_percent": 42.0,
      "remaining_percent": 58.0,
      "reset_at": "2026-11-22T12:00:00Z",
      "pace_percent_per_day": 40.0,
      "predicted_exhaustion_at": "2026-11-21T22:48:00Z",
      "status": "critical",
      "reasons": ["リセット前に枯渇する見込みです"]
    }
  ]
}
```

`agents` は CLI 名順で、有効な観測がなければ空配列です。日時は UTC の ISO 8601 です。リセット日時、日次消費ペース、枯渇予測日時が不明な場合は、それぞれ `null` になります。`reasons` は判定の根拠を示す日本語文字列の配列です。

## テスト

```sh
python3 -m unittest discover -s tools/quota-alert/tests -v
```
