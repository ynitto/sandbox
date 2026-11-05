# gitlab-mr-review-agent 設計

決めた GitLab のリポジトリの MR を、エージェント CLI（kiro-cli）に **同時に 1 セッションだけ**レビューさせる。gitlab-issue-agent と同じく
Dagu の定期実行と api-watch の上に組み、MR に固有の部分だけを足す。実装は `tools/gitlab-mr-review-agent/`（`gitlab_mr.py` と設定の例）、
使い方は同フォルダの README。

要件:

1. 変更の無い MR を重ねてレビューしない
2. 指摘した内容が直ったら、確かめて指摘を閉じる（スレッドを解決にする）

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| 取得・変化の検知・実行待ち・再試行・処理し終えた状態・1 セッションの排他 | api-watch | 取得元 `gitlab-mrs`、ルール `mr-review`（`watch: [sha, draft, target_branch]`）、`external` の受け渡し先（`refresh`・`lock`・`handled_before_if`） |
| 起動・kiro-cli の実行と停止・ログと実行履歴 | Dagu | ワークフロー `api-watch-mr-review`（`claim` → `render` → `harness.run`（provider: kiro）→ `finish`） |
| プロンプトの材料・レビュー中のコミットの追加 | `gitlab_mr.py`（プラグイン） | フィルタ `gitlab_mr_since` / `gitlab_mr_kind`、チェック `gitlab_mr.changed_during_review` |
| GitLab への読み書き（レビューの中身） | `gitlab_mr.py`（コマンド） | `context` / `show` / `comment` / `resolve` / `summary`。kiro-cli がプロンプトの指示で呼ぶ |

kiro-cli はプロンプトを引数で受け取る（`agents/kiro.json` の `prompt_via: argv`）。差分を引数に載せると長さの上限（Windows は 32K 字）に
当たるので、プロンプトは MR の番号と手順だけにし、差分は kiro-cli が `context` で取りに行く。

## 2. 重ねてレビューしない（要件 1）

三段で守る。

| 段 | 仕組み | 防ぐもの |
|---|---|---|
| 検知 | ルールの `watch` を `sha`（head のコミット）・`draft`・`target_branch` だけにする。`user_notes_count`・`labels`・`description`・`updated_at` は見ない | コメント・返信・ラベル変更・kiro-cli 自身の書き込みでのイベント |
| 処理し終えた状態 | `refresh` で実行の前後に MR を取り直し、レビューし終えた監視値（＝コミット）を受け渡し先 × MR ごとに覚える。`claim` は同じ値なら取り直しもせずに飛ばす | 同じコミットへの溜まったイベント・取り直したら同じだったもの |
| 書き込みの印 | 指摘の本文に `<!-- mr-review-agent:finding key=… path=… line=… -->`、まとめに `<!-- mr-review-agent:summary sha=<head> -->` を付ける。`comment` は同じ `key`（パス + 本文のハッシュ）があれば書かず、`summary` は同じ head のまとめがあれば書かない。`context` は今の head のまとめがあれば「何もせずに終えて」と出す | 失敗のやり直し・`handled_before_if` が例外で真になったときの同じコミットの再実行 |

- **レビュー中のコミットの追加**: 実行のあとの取り直しをそのまま「処理し終えた」にすると、レビュー中に積まれたコミット B を見ないまま
  B を処理済みにしてしまう。`gitlab_mr.changed_during_review`（`handled_before_if`）が MR を取り直し、`sha` / `draft` / `target_branch` の
  どれかが実行前と違えば真を返す。api-watch は実行前のコミット A までを処理済みにし、次の取得で B がイベントになる。kiro-cli はコメントしか
  書かないので、これらが変わるのは人の操作に限られ、自分の書き込みで真にはならない
- `filter`（`state: opened` かつ `draft` が偽）は取り直したレコードにも当てる。待つ間にマージ・クローズ・ドラフトに戻した MR は流さない。
  ドラフトを外すと `draft` の変化でイベントになり、処理し終えた状態が無い（または違う）のでレビューする
- `initial: emit`: 入れた時点で開いている MR もレビューする（1 回の起動で 1 件）。`baseline` にすれば今のコミットを覚えるだけ

## 3. 直った指摘を閉じる（要件 2）

- 指摘はすべて解決できるスレッド（discussion）にする。差分の行なら `position`（`diff_refs` の base / start / head と、足した行は `new_line`、
  前後の行は `old_line` も）を付けた行のスレッド、差分に無い行や GitLab が位置を受け付けない（400 / 422）ときは、場所を本文に書いた
  MR 全体のスレッド。どちらも印に `path` と `line` を持つので、`context` が同じ形で一覧にできる
- 新しいコミットのレビューでは、`context --since <前回のコミット>` が自分の未解決のスレッド（最初のノートに印があるもの）を返信つきで出し、
  差分は前回のコミットからの compare（`straight=true`）。前回のコミットが force push で消えて compare が失敗したら MR 全体の差分に倒す
- kiro-cli は各スレッドについて `show`（今の head のファイル）で確かめ、直っていれば `resolve`（返信してから `resolved=true`）。
  直っていなければ残し、同じ指摘を書き直さない（書いても印の `key` が同じなら `comment` が止める）
- `resolve` は印の無いスレッド（人のもの）を拒む。既に解決なら何もしない
- 「どこまで直ったか」は GitLab のスレッドの解決状態だけが持つ。こちらでは指摘の一覧を持たない（人が手で解決・再開しても食い違わない）

## 4. 1 セッションの保証・失敗と再試行

gitlab-issue-agent の §4・§5 と同じ（Dagu の 1 起動 1 件、api-watch の `lock`、期限切れでの取り直し、`retry_backoff_sec` の倍々、`max_attempts` で打ち切り）。
`lock` の名前を gitlab-issue-agent と同じ `agent-session` にすると、issue の処理と MR のレビューが同時に動かない。

## 5. GitLab への負荷

| 呼び出し | 頻度 | リクエスト数 |
|---|---|---|
| MR の一覧（api-watch） | 15 分ごとの予定のあと 1 回。0〜300 秒ずらす | MR 100 件ごとに 1 |
| 同じコミットのイベント | 来るたび | 0（`claim` が取り直さずに飛ばす） |
| 1 件のレビュー（api-watch） | 流すたび | 3（実行の前後の取り直しと、`changed_during_review`） |
| 1 件のレビュー（kiro-cli のコマンド） | 流すたび | `context` で 4〜5（MR・ノート・スレッド・差分）、`comment` 1 件ごとに 3〜4、`resolve` 1 件ごとに 3、`summary` 3 |

kiro-cli のコマンドは api-watch の要求の間隔・失敗の待ちを分け合わない（別プロセスで、トークンは環境変数から読む）。回数は 1 件のレビューの中に
限られ、同時に 1 セッションなので、集中はしない。

## 6. 採らなかった案

- **kiro-cli に指摘を JSON で出させ、決まった処理が書き込む**: 書き込みは確実になるが、Dagu のワークフローの `handle` は 1 ステップで、
  kiro-cli の出力を受けて書き込むステップを api-watch の `dags` の雛形に足すことになる。印で重複を防げば、kiro-cli が直接コマンドを
  呼んでも二重には書かれないので、コマンドで渡した
- **kiro-cli に GitLab の API や `glab` を自由に使わせる**: 行のスレッドの `position` の組み立て・自分のスレッドの見分け・重複の防止を
  毎回 LLM に任せることになる。決まった形のコマンドに閉じた
- **コメント数も監視して、人の返信に答える**: 「変更の無い MR を重ねてレビューしない」と両立しにくい（返信のたびにレビューが走る）。
  返信は次のコミットのレビューで読む
- **全部直ったら MR を承認する**: 頼まれていない。承認・マージは人が行う
- **指摘の一覧をこちらでも持つ**: GitLab のスレッドの解決状態と二重になり、人が手で解決・再開したときにずれる
