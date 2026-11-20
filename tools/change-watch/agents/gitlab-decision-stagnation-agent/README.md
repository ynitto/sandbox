# gitlab-decision-stagnation-agent

複数の GitLab プロジェクトを巡回し、承認・レビューや判断パケットの判断を待っている項目を滞留日数の降順でハブ Issue にまとめる。しきい値を超えた項目には、担当者へのメンション付きで判断を促す。GitLab への書き込みは、実投稿を明示的に有効にしたときのハブ Issue へのコメントに限る。

## 設定と実行

1. `tools/change-watch` で `python3 install.py install --agent gitlab-decision-stagnation-agent` を実行する。Python 3.10 以降が必要。プラグインは標準ライブラリのみで動作する。Dagu 例を使う場合は Dagu v2 と、change-watch の YAML 読み込み用の PyYAML が必要。
2. `~/.config/change-watch/gitlab-decision-stagnation-agent/env` を編集する。トークンには対象プロジェクトの参照権限が必要。実投稿にはハブ Issue を参照・コメントできる `api` スコープのトークンを使う。
3. インストールされた `change-watch-gitlab-decision-stagnation-agent.yaml` を Dagu で起動する。スケジュール例は Dagu のスケジューラ時刻で平日の午前 9 時。

| 環境変数 | 値 |
| --- | --- |
| `GITLAB_URL` / `GITLAB_TOKEN` | GitLab のベース URL / アクセストークン |
| `GITLAB_PROJECTS` | プロジェクトのパスまたは数値 ID。複数指定はカンマ区切り |
| `GITLAB_HUB_ISSUE_URL` | 同じ GitLab のハブ Issue のブラウザ URL または Issue API URL |
| `DECISION_STAGNATION_LABELS` | 判断系ラベル。既定は `decision,needs-decision,needs,approval` |
| `DECISION_STAGNATION_PACKET_LABELS` | 判断パケット関連を示すラベル。既定は `decision-packet,status:review-ready` |
| `DECISION_STAGNATION_THRESHOLD_DAYS` | しきい値。既定は 7 日。日数がしきい値と等しい項目は通常表示 |
| `DECISION_STAGNATION_DRY_RUN` | 既定は `1`。値が正確に `0` の場合だけ投稿 |

ラベルはカンマ区切りの完全一致で、空の値を指定するとそのラベル群を無効にできる。プラグインは上記の環境変数を読み、change-watch の接続設定は使わない。

Dagu 例は `tick → get → report → post → commit` の順に動く。`tick` が UTC 日付をローカルのファイルに保存し、`get` がその内容の変化を受け取る。`report` が GitLab の現在の状態を集約して `report.md` を保存し、`post` が成功した後に `commit` が日次トリガーの処理完了を記録する。項目自体に更新がなくても、次の実行日の滞留日数を再計算する。

環境変数をエクスポートしたシェルでは、日次トリガーを使わずに確認できる。

```sh
change-watch --plugin gitlab_decision_stagnation report --out report.md
change-watch --plugin gitlab_decision_stagnation post report.md
```

dry-run でも GitLab から対象を取得し、生成した Markdown を出力する。出力にはタイトルと担当者名が含まれる。Dagu では dry-run でも日次トリガーを処理済みにするため、同じ日に実投稿へ切り替える場合は保存済みの `report.md` を指定して `post` を実行する。

## 対象と滞留日数

- MR は、下書きを除くオープン MR のうち、GitLab が承認不足と報告しているもの、またはレビューが未着手・進行中・承認取り消しのレビュアーがいるもの。承認状態は `detailed_merge_status` と approvals API、レビュー状態は reviewers API で確認する。API の公開範囲や権限によって確認できる状態は異なる。 [Merge requests API](https://docs.gitlab.com/api/merge_requests/) · [Merge request approvals API](https://docs.gitlab.com/api/merge_request_approvals/)
- Issue は、オープンで判断系ラベルか判断パケット関連ラベルのいずれかが付いたもの。両方に該当する場合も 1 件と数える。gatekeeper のパケット ID は成果物のフロントマターに保存され、GitLab の Issue に共通の識別子は付かないため、設定したラベルで代替する。既定の `status:review-ready` は gatekeeper の既定の準備完了ラベル。`decision-packet` はパケット対象を明示したい場合に運用で付ける。 [gitlab-gatekeeper](../../../../.github/skills/gitlab-gatekeeper/SKILL.md)
- 現在付いている対象ラベルごとに、最新イベントが付与ならその時刻を候補にし、候補の中で最も古い付与時刻を使う。ラベル除去後に再付与した場合は再付与から数える。履歴が取得できない場合、ラベルを切り替えた場合や項目を再オープンした場合など、実際に判断待ちになった時刻とずれることがある。 [Resource label events API](https://docs.gitlab.com/api/resource_label_events/)
- ラベルによる時刻が得られない場合、レビュー待ちだけの MR では待機中のレビュアー全員の割り当て時刻があれば最も古い時刻を使う。それ以外は MR・Issue の作成日時を使う。作成日時を使う場合、下書き期間や承認要件が追加される前の期間を含むことがある。
- 取得開始時刻と基準時刻の差を 24 時間単位で切り捨て、最小値を 0 日とする。既定では 8 日以上で `⚠️ 要判断（滞留）` と担当者への催促を表示する。担当者が未設定の場合はその旨を表示する。

Markdown の先頭には UTC 日付と件数を含む HTML コメントを付ける。0 件のときの表示は「判断待ちはありません」の 1 行。実投稿前にハブ Issue のコメントを検索し、同じ UTC 日付のサマリがあれば件数が変わっていても投稿済みとして扱う。Dagu 例では同時実行を 1 本に制限する。別の実行元から同じハブへ同時に投稿すると、照会と投稿の間に競合する可能性がある。

## テスト

リポジトリのルートで実行する。

```sh
python3 -m unittest discover -s tools/change-watch/agents/gitlab-decision-stagnation-agent/test
```
