# agent-dots — 目標を預けて、裏で進めてもらう

目標を 1 つ預けると、GitHub Copilot のデスクトップアプリが裏で少しずつ進め、報告と質問と承認の依頼だけを
GitLab のハブの issue に書きます。返事はハブの issue にコメントすれば届きます。個人で使う道具です。

```text
Dagu（15 分おき）
  tick     起こす理由（始め・返事・決めた時刻）が無ければ、ここで終わる（アプリは動かない）
  ask      Copilot アプリに依頼を渡す（copilot-app-jobs の受け箱）
             └ アプリは目標のフォルダで作業し、外へ出したいことを outbox/ に「提案」として書く
  act      提案を policy.json と照らして片付ける（許していないものは捨てて記録する）
             └ 聞いてからやるものは承認を頼み、承認されたら動かす
  comment  報告と質問をハブの issue にコメントする → GitLab の通知メール

Dagu（毎分）
  ハブの issue の「【名前】…」のコメント → その名前の目標の inbox/ に置く → 次の tick で起きる
```

起こすかどうかと、外へ書くかどうかは、このフォルダのスクリプトが決めます。エージェント（Copilot アプリ）は
提案を書くだけで、ハブの issue へは直接書きません。

## 用意

前提は Python 3.9 以上（標準ライブラリだけで動きます）、GitHub Copilot のデスクトップアプリ、Dagu です。

1. [copilot-app-jobs](../copilot-app-jobs/README.md) を置きます（例: `C:\tools\copilot-app-jobs\`）。
2. [gitlab-issue-hub-agent](../change-watch/agents/gitlab-issue-hub-agent/README.md) を入れて、ハブの issue を決めます
   （その README の「入れ方」の 1〜5）。ハブの issue の通知をオンにしておくと、報告と質問がメールで届きます。
3. このフォルダの `dot.py` を置きます（例: `C:\tools\agent-dots\`）。

## 目標を作る

1. 目標のフォルダを作ります。名前は、ハブの issue で呼びかけるときに使います（空白と【】は使えません）。

   ```powershell
   python C:\tools\agent-dots\dot.py init --dir C:\work\dots\estimate --name 見積り
   ```

2. `goal.md` に、目標と**完了の条件**を書きます。
3. Copilot アプリで、このフォルダをプロジェクトとして開き、自動実行を 1 つ作ります。貼るプロンプトは
   copilot-app-jobs が出します（間隔は 1 時間、`--stay 50` で居座らせると、依頼を置いてすぐ拾われます）。

   ```powershell
   python C:\tools\copilot-app-jobs\copilot_app_job.py prompt --dir C:\work\dots\estimate --stay 50
   ```

4. [`dags/agent-dots-tick.example.yaml`](./dags/agent-dots-tick.example.yaml) を Dagu のワークフローの置き場にコピーし、
   名前・`DOT`・パスを直します。目標ごとに 1 つ置きます。
5. [`dags/agent-dots-listen.example.yaml`](./dags/agent-dots-listen.example.yaml) を 1 つだけ置き、`DOTS`（目標のフォルダを並べた
   親フォルダ）とパスを直します。目標がいくつあっても 1 つで足ります。
6. 一度ハブの issue の今のコメントを読み飛ばします（今あるコメントを覚えるだけで、何もしません）。

   ```powershell
   change-watch --plugin gitlab_hub --config <設定> --connections <接続設定> get --source gitlab-hub-notes --consumer dots --no-jitter
   ```

最初の tick で目標が動き出し、計画やメモを作って、報告か質問をハブの issue に書きます。

## 話しかける

ハブの issue に、1 行目を `【名前】` で始めたコメントを書きます。

```text
【見積り】 前提は B で進めてください
```

次の tick で目標が起き、このコメントを読んで続きを進めます。目標が書くコメントは `【名前より】` で始まります。

| 書き方 | 意味 |
|---|---|
| `【見積り】承認 a3` | 承認待ちの a3 を動かしてよい。次の片付けで動き、結果がハブの issue に書かれる |
| `【見積り】却下 a3 まだ早い` | a3 は動かさない。理由は目標に伝わる |
| `【見積り】q2 B で` | 質問 q2 への返事 |
| `【見積り】…` | それ以外はふつうの話しかけ |

承認待ちは、決めた日数（既定 7 日）返事が無いと取り下げます。

## 目標が書ける提案

エージェントは作業を終える前に、目標のフォルダの `outbox/` に提案を 1 件 1 ファイルの JSON で書きます。

| 提案 | 例 | どうなるか |
|---|---|---|
| メモ | `{"kind": "note", "body": "…"}` | `notes/` の日ごとのファイルに 1 行足す。次に起きたときの依頼に載る |
| 報告 | `{"kind": "act", "action": "hub.comment", "body": "…"}` | ハブの issue にコメントする |
| コマンドを動かす | `{"kind": "act", "action": "run", "name": "publish", "input": "…", "why": "…"}` | policy.json に登録したコマンドを、決めた段（すぐ動かす・承認を頼む）で動かす。`input` は標準入力に渡る。結果は次に起きたときに届く |
| 質問 | `{"kind": "ask", "question": "…", "options": ["A", "B"]}` | ハブの issue に番号（q1 …）と選択肢つきで書く |
| 次に起きる時刻 | `{"kind": "wait", "until": "2026-11-25T09:00"}` | その時刻を過ぎた最初の tick で起きる（タイムゾーンを書かなければこの PC の時刻） |
| 完了 | `{"kind": "done", "evidence": ["…"]}` | 完了の検査があれば動かし、通れば終わる。無ければ「確かめてください」と書いて止まる |

時刻を決めなければ、次は返事が来るまで起きません。提案を 1 件も書かずに終わった回は、届いていた返事を戻して
やり直します（2 回まで。それでも無ければ捨てて記録します）。

## やってよいことを決める（policy.json）

```json
{
  "actions": {"hub.comment": "auto"},
  "commands": {
    "publish": {"run": ["python", "C:/tools/publish.py"], "level": "ask", "description": "下書きを共有フォルダに置く", "timeout": 600}
  },
  "approvers": [],
  "approval_expire_days": 7,
  "quiet_hours": "22:00-07:00",
  "wake_per_day": 24,
  "done_check": ["python", "check.py"]
}
```

- `actions`：ハブへの報告（`hub.comment`）を `auto`（聞かずに書く）・`ask`（承認されてから書く）・`deny`（書かない）の
  どれにするか。ここに書いても、知らない作用は通しません。
- `commands`：目標に動かしてよいコマンドを名前で登録します。`run` はコマンドと引数のリスト（シェルは通しません）、
  `level` は `auto`・`ask`（既定）・`deny`、`description` は承認を頼むときと依頼文に出す説明、`timeout` は秒です。
  コマンドは目標のフォルダで動き、提案の `input` を標準入力（UTF-8）で受け取ります。登録していないコマンドは動きません。
- `approvers`：承認・却下できる GitLab のユーザー名。空なら、人が書いたコメントならだれでも。
- `approval_expire_days`：承認待ちを取り下げるまでの日数（0 なら取り下げない）。
- `quiet_hours`：コメントを書かない時間帯（日をまたいでよい）。この間の報告・質問・承認の依頼は溜めておき、明けてから
  まとめて書きます。空ならいつでも書きます。
- `wake_per_day`：1 日に起きてよい回数。使い切ると、その日は起きません（0 なら上限なし）。
- `done_check`：完了の検査。目標のフォルダで動かし、終了コード 0 で完了にします。空なら、完了は人が確かめます。

## 様子を見る

```powershell
python C:\tools\agent-dots\dot.py status --dir C:\work\dots\estimate
```

やったこと・捨てた提案・起きた理由は `journal.jsonl` に 1 行ずつ残ります。

| ファイル | 中身 |
|---|---|
| `goal.md` | 目標と完了の条件（人が書く） |
| `policy.json` | やってよいこと・起きる回数・完了の検査 |
| `state.json` | いまの状態・承認待ち・質問（スクリプトだけが書く。手で直さない） |
| `inbox/` | 届いた返事 |
| `outbox/` | エージェントの提案（片付けると `.dot/outbox-done/` へ移る） |
| `notes/` | エージェントの自分へのメモ |
| `journal.jsonl` | 記録 |

## 気をつけること

- **エージェントはフォルダの中では何でもできます。** 外へ書かないように依頼文で伝えていますが、Copilot アプリの
  ツールを止めてはいません。目標のフォルダに、触られて困るものを置かないでください。ハブの issue へ書けるトークンを
  アプリから見える場所に置かないでください。
- 自動実行は、PC が起きていてアプリが開いているときだけ動きます。
- 完了の条件を検査にできるなら `done_check` を書きます。書かなければ、終わったかどうかは人が決めます。
- `commands` に登録するのは、入力を受けても困らないコマンドだけにします。目標は `input` の中身を自由に書けます。
- 承認されたコマンドが動いている途中で止まった（PC が落ちたなど）ときは、やり直さずに「途中で止まった」と書きます。
  二重に動くと困るためです。結果は自分で確かめてください。

## テスト

```bash
python -m unittest discover -s tools/agent-dots/test
```

アプリ役をテストが演じます。アプリ本体・GitLab・Dagu は呼びません。
