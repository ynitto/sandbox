# 夜間点検の結果集約

夜間に分かれて走る点検の結果を朝 7:30 に集め、GitLab のハブ issue に Markdown のコメントを 1 件追加する。失敗と未確認を分けて表示し、失敗の要点と結果ファイルの場所を示す。

`tools/nightly-check-digest/` に独立ツールとして置く。`change-watch/install.py` は `change-watch.yaml.example` を持つディレクトリを agent として認識する。このツールが扱うのは複数の点検ツールが残したローカル結果で、設定を持って単独で動く点は `tools/gitlab-repo-sync/` などと同じ。投稿には既設の `gitlab-issue-hub-agent` の設定とプラグインを使う。

## 点検と結果の参照先

| 点検時刻 | 点検 | 設定する参照先と判定 |
| --- | --- | --- |
| 01:30 | doc-drift-detector | DAG の状態ディレクトリにある `doc-drift.json`。設定例では `summary.drifted` または `summary.by_severity.critical` が 1 以上なら失敗と判定する。[定期実行の設定](#doc-drift-detector-の定期実行)を参照。 |
| 02:00 | codd-lint | DAG の `working_dir` にある `.codd/lint.md`。実装の `run_lint` は正常時にも見出しと `- なし` を書く。設定例ではこれらを外して指摘を拾う。 |
| 02:30 | testcase-lint | 点検設定の `evidence`（例 `results/testcase-lint.json`）。`src/evidence.js` の version 1 の `items` を読み、`kind: behavior` で `status: failed` の項目を拾う。合否には生成側の `fail-on` と静的解析の未確定箇所が反映される。 |
| 02:30 | gitlab-repo-sync | 実行設定の `state_dir` にある `last-report.json`。`runner.py` の `write_report` が書く `finished_at` と `pairs` を持つ結果で、各ペアの失敗・分岐・衝突を拾う。`status: unchanged` でも持ち越した分岐・衝突を拾う。追加の実行ログは同期側の `log_file` で確認できる。 |
| 03:17 | change-watch gc | **仮置き**。結果ファイルは作らない。現行の `cmd_gc` は `util.log` 経由で整理件数を標準エラーに出す。保守 DAG 側で標準出力と標準エラーを専用ログへ追記し、終了コードも記録する。 |

testcase-lint の `src/report.js` が作る `--format json` の出力には `summary` と `findings` がある。こちらを読む場合は、たとえば汎用条件の `summary.bySeverity.error` を使う。

## 設定と手元での実行

Python 3.10 以降で、標準ライブラリだけで JSON 設定を利用できる。PyYAML を import できる環境では `.yaml` または `.yml` の設定も使える。`config.example.json` または `config.example.yaml` をコピーし、点検の数とパスを環境に合わせる。設定内の相対パスは設定ファイルのあるディレクトリを基準に解決する。`~` はホームに展開する。

```sh
python3 tools/nightly-check-digest/nightly_check_digest.py --config ~/.config/nightly-check-digest/config.json
```

`checks` に `name`（表示名）と `type` を点検ごとに書く。ファイルを読む種別では `path` と、必要に応じて `max_age_hours` を指定する。集約時刻とファイルの mtime の差を使い、既定は 26 時間、設定例は 12 時間。ファイルの欠落、期限切れ、読み取りエラーは「❓ 未確認（結果なし）」になる。codd-lint で正常時に結果ファイルを残さない運用の場合は、成功を確認した実行側で新しい空ファイルを残すと正常と判定できる。

- `findings_file` は空行と Markdown の ATX 見出しを除く。`ignore_patterns` に除外したい行の正規表現を配列で指定できる。新しいファイルで残りの行があれば失敗、空なら正常。
- `json_report` は `format` に `testcase-evidence` または `repo-sync-report` を指定できる。`failure_conditions` は単独でも `format` に追加しても使え、1 条件でも成立すれば失敗。配列要素は `items.0.status` のように 0 始まりで指定する。比較は `eq`（等しい）、`ne`（異なる）、`gt`、`ge`、`lt`、`le`。指定パスが存在しない結果や比較できない結果は未確認になる。
- `log_file` は `failure_patterns` に一致する行があれば失敗。`run_start_pattern` を指定すると最後の開始行以降を読み、`run_end_pattern` を指定すると完了行までを読む。指定した目印がそろわないときは未確認になる。
- `command` は `argv` にコマンドと引数の配列を指定する。シェルを介さず集約時に実行するため鮮度判定は不要。作業ディレクトリは `cwd`（既定は設定ファイルのあるディレクトリ）、制限時間は `timeout_seconds`（既定 10 秒）。終了コード 0 なら正常、非 0 なら失敗として終了コードと標準出力の先頭数行を載せる。

汎用 JSON 条件の例。

```json
{
  "name": "テスト点検の JSON レポート",
  "type": "json_report",
  "path": "results/report.json",
  "failure_conditions": [
    {"path": "summary.bySeverity.error", "op": "ne", "value": 0}
  ]
}
```

本文は標準出力に出る。`--output ファイル` でファイルに保存でき、`--date YYYY-MM-DD` でタイトルの日付を変更できる（既定は実行環境の今日）。集約できた場合、点検が失敗・未確認でも終了コードは 0。設定や出力先のエラーなどで集約できない場合は 2。

### gc の保守 DAG からログへ追記する

利用環境の保守 DAG の gc ステップで、今の gc コマンドを次の形で実行する。設定例は `gc start` と `gc exit` に囲まれた最新回だけを読み、古い失敗が毎朝載るのを防ぐ。ログのパスは集約設定とそろえ、この gc ステップ専用にする。Dagu にも gc の終了コードが返る。

```sh
log=/home/you/.local/state/change-watch/hub-gc.log
mkdir -p "$(dirname "$log")" || exit 1
{
  printf 'gc start %s\n' "$(date -Iseconds)" || exit 1
  rc=0
  /home/you/.local/bin/change-watch --plugin gitlab_hub \
    --config /home/you/.config/change-watch/gitlab-issue-hub-agent/change-watch.yaml \
    --connections /home/you/.github/connections.yaml gc || rc=$?
  printf '\ngc exit %s\n' "$rc" || exit 1
  exit "$rc"
} >> "$log" 2>&1
```

### 朝の投稿 DAG を置く

1. [gitlab-issue-hub-agent の手順](../change-watch/agents/gitlab-issue-hub-agent/README.md)に沿って hub agent をインストールし、ハブ issue、dotenv（`GITLAB_HUB_ISSUE_URL` など）、接続設定を用意する。投稿用トークンの持ち主がコメントの書き手になる。GitLab は自分のコメントを自分にメールしないため、通知メールの受信者（Power Automate が監視するメールボックスの持ち主を含む）は別の人にし、issue の通知をオンにする。
2. `dags/nightly-check-digest.example.yaml` を `~/.config/dagu/dags/nightly-check-digest.yaml` にコピーし、プログラム、設定、change-watch、dotenv、hub agent の設定、接続設定のパスを書き換える。予定は Dagu のタイムゾーンで毎朝 7:30。
3. `compose` が `change-watch --plugin gitlab_hub note -- python3 ...` で本文をコメント用 JSON にする。`comment` は本文があるときに `change-watch call POST` でハブ issue に投稿し、一時的な失敗（終了コード 3）を再試行する。通知メールは GitLab が配信し、その後の処理は Power Automate が担う。

## doc-drift-detector の定期実行

`dags/doc-drift-nightly.example.yaml` を Dagu の DAG ディレクトリにコピーし、作業コピーと状態ディレクトリのパス、点検範囲、エージェント CLI を環境に合わせる。例では毎晩 01:30 にエージェントが読み取り専用で点検し、作業コピーに `.drift-work/drift-report.md` を書く。続くステップでアダプタが結果 JSON を状態ディレクトリに書く。点検の開始時に前回のレポートと JSON を削除するため、今回のレポートが欠落しているかサマリ表を読み取れない場合は朝の集約で「未確認」になる。

[変換スクリプトの冒頭](../../.github/skills/doc-drift-detector/scripts/drift_report_to_json.py#L1) に JSON 様式の定義がある。version 1 のフィールドは以下のとおり。

| フィールド | 内容 |
| --- | --- |
| `version` / `source` / `generated_at` | `1` / `doc-drift-detector` / UTC の ISO 8601 日時 |
| `summary` | `documents`、`claims`、`verified`、`drifted`、`unverifiable` の件数。指摘件数は `by_class`（`DOC-STALE`、`CODE-DRIFT`、`UNKNOWN`）と `by_severity`（`critical`、`major`、`minor`）にも記録する。該当なしは 0。 |
| `findings` | 指摘一覧の掲載順に `id`、`class`、`severity`、`title` を並べた配列。要確認リストへの再掲は集計から除く。 |

集約設定の `checks` に次の項目を追加し、`path` を DAG の出力先にそろえる。例ではドリフトした主張が 1 件でもあるか、重大度 `critical` の指摘が 1 件でもあると失敗になる。重大度だけで判定する場合は `summary.by_severity.critical` の条件だけを残す。

```json
{
  "name": "ドキュメント乖離点検",
  "type": "json_report",
  "path": "/home/you/.local/state/nightly-check-digest/doc-drift.json",
  "max_age_hours": 12,
  "failure_conditions": [
    {"path": "summary.drifted", "op": "ne", "value": 0},
    {"path": "summary.by_severity.critical", "op": "gt", "value": 0}
  ]
}
```

## テスト

リポジトリのルートで実行する。`-S` を付けると通常のサイトパッケージの読み込みを無効にし、PyYAML なしの JSON 経路を検証できる。

```sh
python3 -S -m unittest discover -s tools/nightly-check-digest/tests -v
```
