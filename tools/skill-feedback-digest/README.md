# skill-feedback-digest

`git-skill-manager` が記録するフィードバックを読み取り専用で集計し、改良提案の Markdown 下書きを作ります。設定で有効にすると、既存の GitLab ハブ issue にコメントします。Python 3.10 以降の標準ライブラリを使います。

## レジストリ

`<AGENT_HOME>/skill-registry.json` の `installed_skills[].feedback_history` を読みます。履歴には `timestamp`、`verdict`、`note`、改良済みを示す `refined` が入ります。記録・改良・レジストリの管理は git-skill-manager が担当します。このツールはレジストリを直接読み、マイグレーションや更新を行いません。ファイルの不在・読み取り失敗・不正なデータは `未確認（理由）` を出力し、投稿せず終了コード 0 で終了します。

レジストリパスは `--registry`、環境変数 `SKILL_FEEDBACK_REGISTRY_PATH`、JSON の `registry_path` の順に優先します。指定がなければこのチェックアウトの git-skill-manager の `registry.py` にある `_registry_path()` を呼びます。リポジトリから実行する場合は通常 `~/.copilot/skill-registry.json` で、`USERPROFILE` があればその配下を使います。Codex など別のエージェントのレジストリを集計する場合はパスを明示してください。

## 設定と実行

リポジトリルートから実行します。設定ファイルを省略した場合も dry-run で Markdown を標準出力に表示します。

```sh
python3 tools/skill-feedback-digest/skill_feedback_digest.py --registry ~/.codex/skill-registry.json
cp tools/skill-feedback-digest/config.example.json /tmp/skill-feedback-digest.json
python3 tools/skill-feedback-digest/skill_feedback_digest.py --config /tmp/skill-feedback-digest.json
```

| JSON キー | 動作 |
| --- | --- |
| `registry_path` | レジストリのパス。既定の `null` はパス解決に従う |
| `hub_issue_url` | GitLab の issue URL（例 `https://gitlab.example.com/org/hub/-/issues/42`）。省略時は `GITLAB_HUB_ISSUE_URL` |
| `dry_run` | 既定は `true`。`false` を明示した場合のみ投稿する。`--dry-run` で一時的に上書きできる |

実投稿には、ハブと同じ GitLab の HTTPS ベース URL を `GITLAB_URL` に、コメントの閲覧・作成権限がある API トークンを `GITLAB_TOKEN` に設定します。Markdown には note の抜粋が含まれるので、投稿前に dry-run で内容と issue の公開範囲を確認してください。

実投稿前に GitLab のコメントをページ単位で照会し、先頭行に同じ UTC 集計日の識別子がある場合は投稿を省略します。照会に失敗した場合はエラー終了します。同じハブへの並行実行は照会と投稿の間で競合しうるため、定期実行元を一つにしてください。また、複数レジストリを同じハブに投稿すると同日分は最初の投稿で代表されます。

週次実行には `dags/skill-feedback-digest.example.yaml` を Dagu の DAG ディレクトリへコピーし、スクリプトと JSON 設定ファイルのパスを合わせます。例は毎週月曜 09:00（Dagu のスケジュール時刻）に実行します。

## 集計の観点と限界

- 無効化されたスキルも含め、全登録スキルの履歴から verdict ごとの総件数と最終フィードバック日時を表示します。日時は ISO 8601 として比較し、タイムゾーンの記載がない場合は UTC とみなします。同時刻の記録は配列順に扱います。週次実行でも集計対象は全履歴です。
- `broken` が一度でもあったスキルには最新の `broken` の note を添えます。改良済みの履歴も含みます。
- `refined` が付いていない `needs-improvement` と `broken` が未改良問題です。しきい値は `refine_threshold` を優先し、未設定なら workspace は 1 件、それ以外は 3 件です。しきい値到達か `pending_refinement` が立っているスキルを一覧にします。
- 時系列の末尾で `needs-improvement` が 2 件以上続くスキルを一覧にします。`ok` または `broken` が入ると連続は途切れます。連続判定には改良済みの記録も含みます。
- 履歴が空のスキルは未使用候補として登録日時（`installed_at`）を添えます。記録されなかった使用は集計できません。
- 最初の三つの問題観点に該当するスキルには、スキルごとに一つの提案を生成します。note の空白を整えて先頭 120 文字まで抜粋し、未改良問題と最新の `broken` を優先して、`refine` または `skill-evaluator` を定型文で提案します。LLM は使用しません。

## テスト

```sh
python3 -B -m unittest discover -s tools/skill-feedback-digest/tests -v
```
