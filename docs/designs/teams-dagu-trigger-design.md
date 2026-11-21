# Teams の投稿から Dagu を動かす設計（案）

Teams のメッセージを起点に、手元の Windows PC の Dagu のワークフローを動かし、結果を同じ Teams のスレッドに返す。
MS365 と手元の PC は、ネットワークの上ではどちらからも相手へ届かない。そこで逆向き（GitLab → Teams）と同じく、両方の世界に
もともとある「メール」を橋にする。ただし今回は、MS365 から GitLab へ書き込む口が無いので、**手元の PC がメールを取りに行く（pull）**。

状態: 案（2026-11-21）。実装はまだ無い。決めたことは §1、決めていないことは §9。

## 1. 前提（2026-11-21、利用者と確かめた）

| 項目 | 状況 | 効くところ |
|---|---|---|
| Power Automate のプレミアム（HTTP のアクション） | 使えない | MS365 から外へ HTTP を送れない。標準コネクタ（Teams・Office 365 Outlook）だけで組む |
| MS365 から GitLab が見えるか | 見えない | Power Automate からハブの issue へ直接書けない |
| GitLab の受信メール（メールで返信） | 使えない | メールを送って issue のコメントにする道も無い |
| OneDrive の同期クライアント | 使えない | Power Automate が置いたファイルを手元に下ろす道も無い |
| Dagu の置き場所 | Outlook と同じ Windows PC | 手元の Outlook を COM で読める |
| Outlook の版 | クラシック版 | COM（Outlook のオブジェクトモデル）が使える。新しい Outlook では使えない |

この条件で MS365 から手元へ届くのは、**手元の Outlook が受け取ったメール**だけになる。手元から GitLab へは今までどおり書ける。

## 2. 考えた経路と選んだもの

| 経路 | 判断 |
|---|---|
| Teams の送信 Webhook・ボット | 外から社内の口へ入る必要がある。使えない |
| Power Automate の HTTP で GitLab の API を叩く | プレミアムが無く、GitLab も見えない。使えない |
| メールを GitLab の受信アドレスへ送り、issue のコメントにする | GitLab の受信メールが無い。使えない |
| OneDrive にファイルを書き、同期クライアントで手元へ下ろす | 同期クライアントが無い。使えない |
| Playwright で Teams・Outlook の Web 画面を読む | 動くが、画面の変化で壊れやすく、社内ポリシーにも触れやすい。新しい Outlook に移らされたときの予備にする |
| **Power Automate が自分宛てにメールを送り、手元の Outlook を COM で読む** | **選ぶ**。標準コネクタだけで済み、手元から外への通信も増えない |

## 3. 全体像

```
 Teams: メッセージの「…」→ ワークフロー「Dagu を動かす」（フォームで操作と DAG を選ぶ）
   │ Power Automate（標準コネクタだけ）
   ▼
 Outlook: 自分宛てのメール   件名 [dagu-req] <受付番号>   本文 JSON（§5）
   │ Outlook の仕分けルールで「dagu-req」フォルダへ
   ▼
 Dagu: teams-dagu-request（毎分起動。1 回の起動で 1 件）              ← 手元の Windows PC
   export   change-watch --plugin outlook_mail export     「dagu-req」の未読を JSON のファイルにして、メールを「処理済み」へ移す
   get      change-watch get --source teams-requests --consumer teams-dagu --one    フォルダの取得元。新しいファイルを 1 件受け取る
   check    change-watch --plugin outlook_mail check-run --change=${CHANGE}     許可リストと照らし合わせ、何を動かすかを出す
   ack      受け付けた旨を返す（下の「返す」と同じ道）
   run      dagu start <DAG> -- <引数>  /  dagu retry <DAG>  /  dagu status <DAG>
   reply    結果を返す
   commit   change-watch commit --source teams-requests --consumer teams-dagu ${CHANGE.key}
   │ 返す: 知らせを新しい issue にする例（change-watch-issue-hub-notice）を呼ぶ
   ▼
 GitLab: 知らせ用プロジェクトに issue を立てる → 「新しい issue」の通知メール
   ▼
 Power Automate（gitlab-mail-teams と同じ作り）: 本文の受付番号と返信先を読み、Teams の元のスレッドに返信する
```

戻りの道（手元 → GitLab → メール → Teams）は、gitlab-issue-hub-agent の知らせの例と gitlab-mail-teams のフローをそのまま使う。
作り足すのは、行きの道の「Teams → メール」（Power Automate）と「Outlook → ファイル」（手元の COM）と、照合のコマンドだけ。

## 4. 分担

| 受け持ち | どこ | 何を |
|---|---|---|
| Teams の操作を受けてメールにする | Power Automate のフロー「Dagu を動かす」 | トリガー「選択したメッセージに対して」（Teams、標準）。フォームで操作と DAG を選ばせ、§5 の JSON を本文にして「メールの送信 (V2)」で自分宛てに送る。送る前に、押した人が許可した人かを見る |
| メールを仕分ける | Outlook の仕分けルール | 差出人が自分で、件名が `[dagu-req]` で始まるものを「dagu-req」フォルダへ移す。受信トレイを汚さない |
| メールをファイルにする | `outlook_mail.py`（change-watch のプラグイン）の `export` | COM で「dagu-req」の未読を読み、1 通を 1 つの JSON ファイルにする。書き終えてから名前を付け替える（途中のファイルを読ませない）。既読にして「処理済み」フォルダへ移す。中身の判断はしない |
| 前回からの変化を返す・二重に動かさない | change-watch の `folder` 取得元 | ファイル 1 つが 1 件。`get --consumer` / `commit` で、同じ依頼を 2 回返さない |
| 何を動かしてよいかを決める | `outlook_mail.py` の `check-run` | 許可リスト（§6）と照らし合わせ、`route`（`start`・`retry`・`status`・`reject`）と、Dagu に渡す DAG の名前・引数を JSON で出す |
| 起動・分岐・再試行・履歴 | Dagu のワークフロー `teams-dagu-request` | §3 のステップ。条件は `preconditions` に書く |
| 結果を Teams に返す | 既存の `change-watch-issue-hub-notice` と、Teams に流す Power Automate のフロー | 知らせの issue の説明に受付番号と返信先を書く。フローがそれを読んで元のスレッドに返信する |

`export` を change-watch のプラグインにするのは、ほかのエージェントと同じく「ワークフローは change-watch を入り口にする」ためで、
change-watch の本体には手を入れない。Outlook を読むのは `export` だけにし、照合・起動・返信には COM を持ち込まない。

## 5. 依頼のメール

件名は `[dagu-req] <受付番号>`。受付番号は Teams のメッセージ ID から作る（同じメッセージで 2 回押されたら同じ番号になる。
押し直しを別の依頼にしたいなら、押した時刻を足す。§9）。本文は JSON 1 つだけにする。

```json
{
  "v": 1,
  "id": "T1732150000000",
  "op": "start",
  "dag": "nightly-report",
  "params": {"DATE": "2026-11-20"},
  "by": {"name": "鈴木 一郎", "email": "ichiro.suzuki@example.co.jp"},
  "reply": {"team": "<チームの ID>", "channel": "<チャネルの ID>", "message": "<元のメッセージの ID>"},
  "text": "元のメッセージの本文（記録用。照合にも実行にも使わない）"
}
```

- Outlook は本文を HTML にすることがある。`export` は本文から最初の `{` から対応する `}` までを JSON として読み、読めなければ
  そのメールを `invalid` としてファイルにする（`check-run` が `reject` にし、差し戻しを返す）
- 本文の文字数の上限を決めておく（§9）。上限を超えたものは読まずに `invalid` にする

`export` が書くファイル（`<受信フォルダ>/<受付番号>.json`）には、メールの側の事実を足す。

```json
{
  "request": { …本文の JSON… },
  "mail": {"entry_id": "…", "received": "2026-11-21T10:15:02+09:00", "sender": "me@example.co.jp", "subject": "[dagu-req] T1732150000000"},
  "error": null
}
```

## 6. 何を動かしてよいか（許可リスト）

Teams の本文はそのまま実行しない。動かせるのは、許可リストに書いた DAG と操作と引数だけにする。

```json
{
  "users": ["ichiro.suzuki@example.co.jp", "hanako.sato@example.co.jp"],
  "dags": {
    "nightly-report": {
      "label": "夜間レポート",
      "ops": ["start", "retry", "status"],
      "params": {"DATE": "^\\d{4}-\\d{2}-\\d{2}$"},
      "users": null
    },
    "cache-refresh": {"label": "キャッシュの作り直し", "ops": ["start", "status"], "params": {}}
  }
}
```

`check-run` は次をすべて満たすときだけ `start`・`retry`・`status` を出し、1 つでも外れれば `reject` と理由を出す。

1. メールの差出人が自分（`export` の設定で決めた自分のアドレス）
2. `by.email` が `users`（DAG ごとの `users` があればそちら）に入っている
3. `dag` が `dags` にあり、`op` がその `ops` に入っている
4. `params` の名前がすべて許可リストにあり、値が正規表現に当たる（許可リストに無い引数は渡さない）
5. `v` が分かる版

Dagu へは `dagu start <DAG> -- 名前=値` の形で、引数の配列として渡す（シェルを通さない）。

最初に用意する操作は 3 つ。

| 操作 | すること | 返すもの |
|---|---|---|
| `start` | DAG を動かす | 受け付けたこと → 終わったら成否・所要時間・失敗したステップ |
| `retry` | 直近で失敗した回をやり直す | `start` と同じ |
| `status` | 直近の回の結果を見る | 成否・終わった時刻・失敗したステップ |

Teams のフォームの DAG の選択肢は、許可リストの `label` と同じものを Power Automate に書く。二重に持つことになるので、
`check-run` の側を正とし、フォームに無い DAG・許可リストに無い DAG はどちらも `reject` になる。

## 7. 決まりごと

- **二重に動かさない**: 同じ受付番号のファイルは同じ名前になり、change-watch の consumer が commit したものは返さない。
  `export` は既にあるファイルを上書きしない
- **commit は必ず進める**: 照合で外れても、動かした DAG が失敗しても、返信のあとで commit する（`continue_on.failure`）。
  commit しないと毎分やり直し続ける
- **同時には 1 件**: `teams-dagu-request` は 1 回の起動で 1 件。動かした DAG が終わるまで次の起動は待つ（Dagu の既定の
  `maxActiveRuns: 1` で、重なった起動は飛ばされる）。長くかかる DAG があると、あとの依頼が待たされる（§9）
- **受け付けたことを先に返す**: `run` の前に `ack` を返す。終わるまで Teams に何も出ないと、押した人が押し直す
- **返す文に内部の綴りを出さない**: 返信は DAG の `label` と、成否・時刻・失敗したステップの名前で書く
- **行頭の `/`**: 返す文は GitLab の issue を通るので、行頭の `/` は `\/` にする（gitlab-issue-hub-agent と同じ）

## 8. 手元の PC の条件と、失敗したとき

| 条件・失敗 | 扱い |
|---|---|
| Dagu はログインしているユーザーとして動かす | Windows のサービスとして別のアカウントで動かすと COM で Outlook に入れない。作業スケジューラなら「ユーザーがログオンしているときのみ実行」にする |
| Outlook を起動しておく | 閉じていると COM が Outlook を起動しようとし、遅い・止まることがある。`export` は Outlook が動いていなければ一時的な失敗（終了コード 3）で終わり、メールは残る |
| 「プログラムがアクセスしようとしています」の警告 | 社内の設定（ウイルス対策の状態・グループポリシー）で出る。出るなら情シスに確認する。出たまま自動では回せない |
| 「dagu-req」フォルダが無い | `export` は終了コード 2 で止め、作り方を案内する |
| PC が寝ている・電源が切れている | メールは Outlook に溜まり、起きたあと古い順に 1 件ずつ処理される。古すぎる依頼を捨てる期限を決める（§9） |
| 新しい Outlook に切り替えられた | COM が使えなくなる。`export` だけを Playwright で Outlook の Web 画面を読むものに替える。§5 のファイルの形を変えなければ、ほかはそのまま使える |

## 9. 決めていないこと

| 項目 | 案 |
|---|---|
| 押した人を Power Automate で取れるか | 「選択したメッセージに対して」のトリガーが押した人を返すかを実機で確かめる。取れなければ、メッセージの投稿者で代える（その場合「投稿した人」が許可リストに入っている必要がある） |
| 押し直しを別の依頼にするか | 既定は同じ依頼（同じ受付番号）として捨てる。やり直したいときは `retry` を選ぶ |
| 古い依頼を捨てる期限 | 受信から 60 分を過ぎたものは動かさず、期限切れとして返す |
| 本文の上限 | 16 KB |
| 長くかかる DAG | 最初は待つ（§7）。困ったら、`run` を `dagu enqueue` にして終わりを別のワークフローで見に行く形へ分ける |
| 返信の宛先 | 元のスレッドに返す。返せない（ID が無い）ときは決めたチャネルに投稿する |
| 名前 | プラグインを `outlook_mail`、エージェントを `outlook-mail-agent`（change-watch の `agents/` の下）とする |

## 10. 作る順

1. `outlook_mail.py` の `check-run` と許可リストの読み込み。COM に触れないので、Linux のテストで固められる
2. `export`。COM の呼び出しを薄い層に閉じ込め、テストでは偽物の Outlook を差し込む。実物では手元の PC で 1 通流して確かめる
3. Dagu のワークフローの例 `teams-dagu-request.yaml.example` と、change-watch の取得元 `teams-requests`（`folder`、`content: json`）の設定の例
4. Power Automate のレシピ 2 本: 行き（Teams → メール）と、戻り（知らせの issue のメール → Teams の元のスレッドへの返信）
5. 利用者向けの README（入れ方・Outlook の仕分けルールの作り方・許可リストの書き方・止まったとき）
