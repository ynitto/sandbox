# Kiro エクスポート・記憶・change-watch の保全点検

Kiro の定期ログエクスポート、記憶レポート、change-watch の状態 DB の更新停止や、記憶メンテナンスの滞留を検出する。点検結果は JSON に保存し、朝の `nightly-check-digest` で集約できる。

## 設定と実行

Python 3.9 以降の標準ライブラリだけで動く。`config.example.json` をコピーしてパスとしきい値を環境に合わせる。設定内の相対パスは設定ファイルのあるディレクトリを基準に解決し、`~` はホームに展開する。

- `kiro_exports` は点検するエクスポート先の配列。各要素に表示用の `name` と出力先の `dir` を指定する。
- `knowledge.path` は `agent-audit report --kind knowledge --json` の保存先。レポートの鮮度はファイルの更新時刻（mtime）で判定する。
- `change_watch` は点検する change-watch の配列。各要素に表示用の `name` と状態 DB を置くディレクトリ `state_dir` を指定する。`max_age_hours` は状態 DB の更新から判定時刻までに許容する経過時間（時間）。
- 各入力の `max_age_hours` と `knowledge.thresholds` の各しきい値は任意の非負数。鮮度上限を省略した入力は、確認できた更新時刻が判定時刻以前なら `ok`。上限を省略したストア指標は点検項目に含めない。エクスポート先、knowledge レポート、change-watch のうち、少なくとも1対象を設定する。

```sh
python3 tools/upkeep-health-check/upkeep_health_check.py \
  --config ~/.config/upkeep-health-check/config.json \
  --output ~/.local/state/upkeep-health-check/result.json
```

`--now 2026-11-20T03:00:00+09:00` を指定すると、再現用に判定時刻を固定できる。時差の指定を省略すると UTC として扱う。

## 判定

| 対象 | `ok` | `stale` | `unknown` |
| --- | --- | --- | --- |
| Kiro の出力先直下の `.kiro_export_state.json` | 状態ファイルを確認でき、設定した鮮度上限以内 | mtime からの経過時間が上限を超過 | ディレクトリや状態ファイルが存在しない、または状態ファイルを確認できない |
| knowledge レポート | JSON の `layers` を読み取れ、設定した鮮度上限以内 | mtime からの経過時間が上限を超過 | レポートが存在しない、または JSON や `layers` を読み取れない |
| `layers.<層>` と `stores[]` | 設定した各指標が上限以内 | ストアの指標が上限を超過 | `configured: false`（未収集）、ストア情報がない、または設定した指標の数値を取得できない |
| change-watch の `<state_dir>/state.db` と `<state_dir>/state.db-wal` のうち新しい方の更新時刻 | 更新時刻を確認でき、設定した鮮度上限以内 | 更新時刻からの経過時間が上限を超過 | どちらのファイルも存在しない、または確認できない |

Kiro は状態ファイルの mtime を使う。既知のセッションだけで差分がない実行でも状態ファイルは保存され、出力済み `.log` の mtime は変わらない。セッションが一件も見つからなかった実行では状態ファイルは更新されない。

鮮度判定に使う mtime が判定時刻より未来の場合は `unknown` とし、`detail` に時刻逆転を記録する。鮮度上限を省略した場合も同様。

knowledge はレポートの鮮度と各ストアの状態を別の項目として記録する。読める古いレポートの指標は、そのレポート時点の値として併記する。上限と同じ値は `ok`。

| 層 | `layers.<層>.stores[]` の点検指標 |
| --- | --- |
| `ltm` | `dormant_oldest_days`（最古の休眠日数）、`index_drift`（索引との差分件数） |
| `wiki` | `index_drift`（索引との差分件数） |
| `persona` | `pending_oldest_days`（最古の確認待ち日数） |
| `moltbook` | `outbox_pending`（送信待ち件数）、`outbox_oldest_days`（最古の送信待ち日数） |

### change-watch の死活監視

change-watch は状態を WAL モードの単一の SQLite DB（`state.db`）に集約し、取得や `commit` のたびに書き込む。書き込みはまず `state.db-wal` に入るため、確認できた両ファイルのうち新しい方の更新時刻を最後の書き込み時刻とみなす。Dagu は毎分 `get` を呼び出し、`get` は取得元ごとの予定に従って取得を実行する。健全なら新しい依頼がなくても DB は更新され続ける。PC や Dagu が止まって書き込みが途絶え、鮮度上限を超えると `stale` になる。

- この点検で分かるのは状態 DB への書き込みの有無まで。保守用の `gc` も同じ DB に書くため、`max_age_hours` は `gc` の実行間隔（通常は日次）より十分小さくする。目安は取得元の最長取得間隔より少し大きい値で、最長5分間隔なら1時間を設定する。
- 取得元が失敗後の待ち時間（クールダウン）に入っている間や、`get --no-fetch` のように取得を行わない運用では、書き込みが止まり、change-watch が動いていても `stale` になることがある。
- チェックポイントで `state.db-wal` が消えることがある。その場合は `state.db` の更新時刻で判定する。

## 出力

version 1 の JSON を `--output` に書く。`summary` は各状態の項目数。`checked_at` は UTC の ISO 日時。`kind` は `kiro_export`、`knowledge_report`、`knowledge_layer`、`knowledge_metric`、`change_watch` のいずれか。`detail` は短い日本語の説明。`value` と `threshold` は鮮度の場合は時間、ストア指標の場合は元の単位で、未取得・未設定の場合は `null`。

```json
{
  "version": 1,
  "source": "upkeep-health-check",
  "checked_at": "2026-11-19T18:00:00+00:00",
  "summary": {"ok": 0, "stale": 1, "unknown": 0},
  "items": [
    {
      "name": "Kiro 定期エクスポート",
      "kind": "kiro_export",
      "status": "stale",
      "detail": "状態ファイルの経過時間が上限を超えています",
      "value": 30.0,
      "threshold": 26
    }
  ]
}
```

引数の解析後、点検処理の開始時に既存の `--output` ファイルを削除する。点検結果を書けた場合は `stale` や `unknown` を含んでいても終了コードは 0。設定や出力先のエラーで実行できない場合は終了コード 2 となり、新しい結果ファイルは書かない。既存ファイルを削除できない場合も出力先のエラーとして扱う。

## 夜間実行と朝の集約

`dags/upkeep-health-check.example.yaml` を Dagu の DAG ディレクトリにコピーし、実行ファイルと入出力のパスを置き換える。毎晩 02:50（Dagu のタイムゾーン）に Kiro エクスポート、knowledge レポート生成、保全点検の順に実行する。事前に agent-audit の `memory_stores` を設定する。エクスポートやレポート生成に失敗した場合も点検を続ける。前回のレポートは新しいレポートの生成に成功した時点で置き換える。

`nightly-check-digest` の設定の `checks` に次の項目を追加する。`path` は DAG の `--output` とそろえる。

```json
{
  "name": "Kiro・記憶の保全点検",
  "type": "json_report",
  "path": "/home/you/.local/state/upkeep-health-check/result.json",
  "max_age_hours": 12,
  "failure_conditions": [
    {"path": "summary.stale", "op": "gt", "value": 0}
  ]
}
```

この設定では `summary.stale` が 1 件以上なら朝の集約で失敗になる。点検結果が新しく `unknown` のみの場合、この条件では朝の集約は正常扱いになり、未確認の内容は結果 JSON の `summary.unknown` と `items` で確認できる。点検結果のファイル自体が欠落・期限切れ・読み取り不能の場合、朝の集約では未確認になる。

## テスト

リポジトリのルートで実行する。

```sh
python3 -S -m unittest discover -s tools/upkeep-health-check/tests -v
```
