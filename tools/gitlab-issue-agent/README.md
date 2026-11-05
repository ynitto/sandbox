# gitlab-issue-agent

GitLab の issue を定期的に見に行き、変化があった issue をエージェント CLI（Claude Code / Codex / Kiro / Copilot など）に
1 件ずつ渡す。**同時に動くエージェント CLI は常に 1 つ**で、同じ変化を 2 回流さない。

issue の取得・変化の検知・実行待ち・再試行は [api-watch](../api-watch/README.md) が、起動とエージェント CLI の実行は
[Dagu](https://github.com/dagu-org/dagu)（v2.25 以降。`harness.run` を使う）が受け持つ。このフォルダには、GitLab の issue に固有の部分を
足す api-watch のプラグイン（`gitlab_issue.py`）と、設定の例がある。

```
 Dagu: api-watch-issue-agent（毎分起動。1 回の起動で 1 件を処理する）
   fetch      毎時 0 分の予定のあと、0〜15 分ずらして issue を全ページ取得（予定の時刻のあとに取得済みなら問い合わせない）
   evaluate   前回と比べて変化した issue を拾う
   claim      流す issue を 1 件取る。issue を取り直し、処理し終えた状態と同じ・閉じた・別のセッションが実行中なら流さない
   render     プロンプトを組む（前回処理し終えたときからの変化の箇条書きと、issue の内容）
   handle     エージェント CLI を動かす（Dagu の harness.run）
   finish     handle の終了コードを見て、issue を取り直して処理し終えた状態にする（失敗ならあとでやり直す）

 流す issue が無ければ claim のあとは動かない。流すものが複数あれば、次の起動（1 分後）が次の 1 件を取る
```

## 入れ方

1. プラグインとプロンプトのテンプレートを置く。

   ```bash
   mkdir -p ~/.local/share/gitlab-issue-agent
   ```

   ```bash
   cp tools/gitlab-issue-agent/gitlab_issue.py ~/.local/share/gitlab-issue-agent/
   ```

   ```bash
   cp tools/gitlab-issue-agent/prompt.md.example ~/.config/dagu/gitlab-issue-agent.prompt.md
   ```

2. GitLab の URL・プロジェクト・トークンを書く。`gitlab-issue-agent.env.example` を `~/.config/dagu/gitlab-issue-agent.env` にコピーして埋める。
   トークンには `read_api` が要る（エージェントが issue にコメントするなら、そのエージェント側の権限も別に要る）。

   ```bash
   cp tools/gitlab-issue-agent/gitlab-issue-agent.env.example ~/.config/dagu/gitlab-issue-agent.env
   ```

3. api-watch を入れる。

   ```bash
   python3 tools/api-watch/install.py install
   ```

4. api-watch の設定を置く。対象の issue の絞り込みは、設定の `query`（取得するもの）とルールの `filter`（実行の直前にもう一度確かめるもの）に書く。

   ```bash
   mkdir -p ~/.config/api-watch
   ```

   ```bash
   cp tools/gitlab-issue-agent/api-watch.yaml.example ~/.config/api-watch/api-watch.yaml
   ```

5. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-issue-agent.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

6. エージェント CLI の動かし方を書く。`handle-step.yaml.example` を `~/.config/dagu/gitlab-issue-agent.handle.yaml` にコピーし、
   `provider`（`claude` / `codex` / `kiro` / `copilot` など）・フラグ・作業フォルダ（`working_dir`）を直す。
   CLI はこの PC に入れて、ログインしておく。

   ```bash
   cp tools/gitlab-issue-agent/handle-step.yaml.example ~/.config/dagu/gitlab-issue-agent.handle.yaml
   ```

7. 環境変数を読み込んでから、Dagu のワークフローの雛形を書き出す。`api-watch-issue-agent.yaml`（取得から処理まで。
   6 のステップが handle に入る）と `api-watch-maintenance.yaml`（毎日の整理）ができる。
   作ったあとは自由に直してよい（既にあるファイルは上書きしない）。Dagu の設定（`config.yaml`）に足すものは無い。

   ```bash
   set -a; . ~/.config/dagu/gitlab-issue-agent.env; set +a
   ```

   ```bash
   api-watch --plugin dagu --plugin ~/.local/share/gitlab-issue-agent/gitlab_issue.py --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags --dotenv ~/.config/dagu/gitlab-issue-agent.env --handle-step issue-agent.run=~/.config/dagu/gitlab-issue-agent.handle.yaml
   ```

8. 一度手で取得して基準を取る（今ある issue を覚えるだけで、エージェントは起こさない）。そのあと Dagu のスケジューラを動かす
   （`dagu start-all` など）。以後は issue にコメント・ラベル変更・本文の更新などがあると、次の取得（1 時間に 1 回）でエージェントが起きる。

   ```bash
   api-watch --plugin ~/.local/share/gitlab-issue-agent/gitlab_issue.py --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml run --rule issue-agent --no-jitter
   ```

## GitLab への問い合わせの回数

- issue の一覧の取得は **1 時間に 1 回**。予定の時刻（毎時 0 分）から 0〜15 分のあいだでランダムにずらして問い合わせる。
  複数の PC やプロジェクトで動かしても、問い合わせが同じ時刻に重ならない。1 回の取得は 100 件ごとに 1 リクエスト
- ワークフローは毎分起動するが、予定の時刻のあとに取得し終えていれば一覧は取得しない。毎分の起動で進むのは、失敗したもののやり直しだけ
- エージェントを 1 回流すたびに 4 リクエスト（実行の前と後に issue を取り直す 2 回と、実行中のほかの人の動きを見る `/user`・コメントの 2 回）。
  エージェント自身のコメントで出た変化など、前回処理し終えた状態と同じものは取り直しもせずに飛ばす
- 取得が失敗したら 5 分（続けて失敗するほど倍、最大 1 時間）は問い合わせない

予定は api-watch の設定の取得元の `schedule`、ずらす幅は `jitter_sec` で変えられる。予定を変えるときは api-watch の設定だけを直せばよく、
ワークフローは書き出し直さなくてよい。

## 何を「変化」とみなすか

次のどれかが前回から変わったときだけ流す。`updated_at` だけが動いた更新（MR からの言及など）では起こさない。

- 新しい issue / タイトル / 状態（開いた・閉じた）/ ラベル / 担当 / マイルストーン / 期日 / コメント数 / 本文

見る項目は api-watch の設定のルールの `watch` に書いてある。対象は `query` とルールの `filter` で絞る。`filter` は実行の直前に取り直した issue にも
当てはめるので、実行を待つ間に絞り込みから外れた issue（閉じた・ラベルが外れた）は流さない。

## 重複させない仕組み

- 実行の直前に issue を取り直し、最新の内容でエージェントに渡す。プロンプトの差分は前回処理し終えたときからの累積
- 流し終えたら、その時点の issue を取り直して「処理し終えた状態」にする。**エージェント自身のコメントやラベル変更で
  もう一度起きることはない**（その変化も拾うが、処理し終えた状態と同じなので流さない）
- 実行を待つ間に同じ issue が何度か変わっても、先に動いた実行が最新の issue でまとめて流し、後ろは飛ばす
- 実行中に自分以外の人がコメントしたときだけ、処理し終えたのは実行前の状態までにして、次の取得でもう一度流す
  （自分はトークンの持ち主で判定する。api-watch の設定の `handled_before_if` の `options.self_username` で明示もできる）

## 1 セッションだけにする仕組み

- ワークフローは 1 回の起動で 1 件だけ処理する。同じワークフローが重ねて起動されることもない
- api-watch の設定の `lock`（例は `agent-session`）が同じ受け渡し先どうしは、プロジェクトをまたいでも同時に 1 つだけ実行する。
  空いていなければ「あとで」として試行に数えずに戻し、次の起動でもう一度試す
- エージェント CLI の時間切れ・失敗では、`finish` が終了コードを見て失敗として記録する
- ワークフローごと止めた・Dagu ごと落ちたなどで `finish` まで進まなかったときは、`timeout_sec` + 2 分たつと次の起動が取り直す
  （それまでその issue と `lock` は空かない）

## 失敗したとき

エージェント CLI が 0 以外で終わる・時間切れ・止められると失敗になり、api-watch が受け渡し先の `retry_backoff_sec` 秒（1 回ごとに倍）
待ってから同じ変化を流し直す。`max_attempts` 回失敗したら打ち切り、その issue が次に変化したときにもう一度拾う。設定例では 3 回・300 秒。

| したいこと | コマンド |
|---|---|
| 失敗している・打ち切った変化を見る | `api-watch --config ~/.config/api-watch/api-watch.yaml events --rule issue-agent --status send_failed,dead` |
| 打ち切った変化をすぐ流し直す | `api-watch --config ~/.config/api-watch/api-watch.yaml retry <受け渡しの ID>` |
| 処理し終えた issue をもう一度流させる | `api-watch --config ~/.config/api-watch/api-watch.yaml forget-handled --rule issue-agent 123`（次に変化を受け取ったとき流す） |

## エージェント CLI とプロンプト

- エージェント CLI は Dagu の `harness.run` で動かす（`gitlab-issue-agent.handle.yaml`）。`provider` に `claude` / `codex` / `kiro` / `copilot` / `gemini` などを書き、
  `with` のほかの項目は CLI のフラグになる（`dangerously_skip_permissions: true` → `--dangerously-skip-permissions`）。権限の確認で止まらないよう、
  無人で動かせるフラグを付けること
- プロンプトは `~/.config/dagu/gitlab-issue-agent.prompt.md`（api-watch の設定の `input_template`）。api-watch のテンプレートで書き、
  `{{ event.record.iid }}`・`{{ event.record.title }}`・`{{ event.record.web_url }}`・`{{ event.record | json: 2 }}`・`{{ delivery.attempt }}`（何回目か）と、
  プラグインの `{{ event | gitlab_issue_changes }}`（変化の箇条書き）・`{{ event | gitlab_issue_kind }}`（新規 / 更新）が使える
- 組んだプロンプトはエージェント CLI の標準入力に渡る（`stdin: "${INPUT}"`）

## プラグイン（gitlab_issue.py）が足すもの

| 名前 | 種類 | すること |
|---|---|---|
| `gitlab_issue_changes` | フィルタ | 前回処理し終えたとき（無ければ前回の取得）からの変化の箇条書き |
| `gitlab_issue_kind` | フィルタ | 新規 / 更新 |
| `gitlab.others_acted` | チェック | 実行を始めてから、自分以外の人が issue にコメント・操作したか（`handled_before_if` に書く） |
| `gitlab_idd_role` | フィルタ | gitlab-idd のロール（`status:review-ready` ならレビュアー、`status:open` などならワーカー）。下の「gitlab-idd スキルと繋ぐ」 |
| `gitlab_idd.role_changed` | チェック | 実行のあいだに gitlab-idd のロールが変わったか（`handled_before_if` に書くと、次のロールでもう一度流れる） |

GitLab への問い合わせは、api-watch の取得元の接続で送り、要求の間隔・失敗の待ちを取得と分け合う。

## gitlab-idd スキルと繋ぐ

[gitlab-idd](../../.github/skills/gitlab-idd/SKILL.md) スキルは、GitLab のイシューを `status:*` のラベルで回すイシュー駆動開発のスキルで、
「イシューを拾って」（ワーカー）や「イシューをレビューして」（レビュアー）と頼まれたときに 1 回ずつ動く。これを gitlab-issue-agent に繋ぐと、
ラベルやコメントが変わったイシューを、そのラベルに合ったロールで自動で処理させられる。設定の例は `examples/gitlab-idd/` にある。

| イシューのラベル | エージェントに頼むこと |
|---|---|
| `status:open` / `status:needs-rework` / `status:needs-clarification` | gitlab-idd のワーカーロールで、そのイシューを実装してブランチ・MR・コメントで報告する |
| `status:review-ready` | gitlab-idd のレビュアーロールで、そのイシューの受け入れ条件を評価して承認か差し戻しをする |
| それ以外（`status:in-progress` / `status:approved` / `status:done` など） | 流さない（マージは人が行う） |

```
 Dagu: api-watch-idd（毎分起動。1 回の起動で 1 件）
   fetch → evaluate → claim → render（ラベルからロールを選んだプロンプト）→ handle（harness.run）→ finish

 イシューの進み方: status:open ─ワーカー→ status:review-ready ─レビュアー→ status:approved（人がマージ）
                                              └─差し戻し→ status:needs-rework ─ワーカー→ …
```

- ワーカーが `status:review-ready` にしたり、レビュアーが差し戻したりして**ロールが変わったときは、エージェント自身の書き込みでも次の取得でもう一度流す**
  （チェック `gitlab_idd.role_changed`）。ロールが変わらない自分の書き込み（質問のコメント・`status:needs-clarification` への変更など）では流さない
- 依頼者が質問に答える（コメントする）と、ワーカーがもう一度呼ばれる
- エージェントには「このイシューだけを処理して」と頼む。gitlab-idd が候補の一覧から別のイシューを選ばないように、プロンプトで止めている

### 入れ方

上の「入れ方」の 1〜8 のうち、次のファイルを `examples/gitlab-idd/` のものに替える。

| 置く場所 | 例 | 替えるもの |
|---|---|---|
| `~/.config/api-watch/api-watch.yaml` | `api-watch.yaml.example` | 4 の設定（ルール `idd`） |
| `~/.config/api-watch/idd.prompt.md` | `idd.prompt.md.example` | プロンプト（設定と同じフォルダに置く） |
| `~/.github/connections.yaml` | `connections.yaml.example` | 5 の接続設定（`default` は gitlab-idd 用のプロジェクトの URL、`watch` は api-watch 用の GitLab の URL） |
| `~/.config/dagu/gitlab-issue-agent.handle.yaml` | `handle-step.yaml.example` | 6 のステップ |
| エージェントのホームの `skill-registry.json`（Claude Code なら `~/.claude/skill-registry.json`） | `skill-registry.json.example` | 足す（既にあれば `skill_configs.gitlab-idd` だけを足す） |

`gitlab-issue-agent.env` には `SKILL_CONNECTIONS_FILE`（上の connections.yaml のパス。gitlab-idd にも同じ接続設定を読ませる）と
`GITLAB_NODE_ID` も書く（`gitlab-issue-agent.env.example` の該当の行）。エージェント CLI から gitlab-idd スキルが使えるようにしておく
（Claude Code なら `~/.claude/skills/gitlab-idd`）。

既にある作業コピーを使うなら、`gitlab-issue-agent.env` の `GITLAB_IDD_LOCAL_REPO` にそのフォルダ（handle のステップの `working_dir` と同じでよい）を書く。
gitlab-idd のワーカーは、イシューごとにリポジトリをクローンする代わりに、そのリポジトリへ `git worktree` を足して作業し、終わったら外す
（取ってくるのは対象ブランチの新しいコミットだけ）。作業コピーのブランチ・書きかけの変更には触らない。作業コピーの `origin` は
gitlab-idd 用のプロジェクトを指していること。書かなければ、従来どおりイシューごとに一時フォルダへクローンする。

7 のワークフローは、ルール名が `idd` なので `--handle-step idd.run=~/.config/dagu/gitlab-issue-agent.handle.yaml` を付けて書き出す。
`api-watch-idd.yaml` ができる（中身は `examples/gitlab-idd/api-watch-idd.yaml.example` と同じで、パスが自分の PC のものになる）。

```bash
api-watch --plugin dagu --plugin ~/.local/share/gitlab-issue-agent/gitlab_issue.py --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags --dotenv ~/.config/dagu/gitlab-issue-agent.env --handle-step idd.run=~/.config/dagu/gitlab-issue-agent.handle.yaml
```

```bash
api-watch --plugin ~/.local/share/gitlab-issue-agent/gitlab_issue.py --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml run --rule idd --no-jitter
```

### 気をつけること

- gitlab-idd は複数のノード（PC・人）で分担する前提で、自分が出したイシューを 1 時間着手しない（self-defer）・自分が実装したイシューを
  24 時間レビューしない（self-review ロック）。このワークフローは 1 つのエージェントがワーカーとレビュアーを兼ねるので、
  `skill-registry.json` でどちらも 0 にする（`skill-registry.json.example`）。0 にしないと、そのあいだに着手しなかったイシューは、
  次に変化があるまで流れない
- 自分で実装したものを自分でレビューすることになる。人の目を入れたいなら、レビュアーの承認（`status:approved`）のあとのマージを人が行う
  （gitlab-idd もマージはしない）
- gitlab-idd の「24 時間たったら推測で着手する」（質問や方針の提案に答えがなかったとき）は、イシューに変化が無いので起きない。
  答えるか、ラベルを `status:open` に戻すと流れる
- 1 回の実装は長いことがあるので、`timeout_sec` を長めにしている（例は 4200 秒）

## Windows で使うとき

- api-watch は `python install.py install` で入れる（`dags` はその Python と `api-watch.pyz` のフルパスでワークフローを書く）
- Dagu の設定フォルダは `C:\Users\<名前>\.config\dagu\` で、DAG は `dags\` に置く
- `gitlab-issue-agent.handle.yaml` の `working_dir` は `C:/Users/you/work/project` のように書く（`GITLAB_IDD_LOCAL_REPO` も同じ書き方）
- gitlab-idd のワーカーの作業用フォルダは、一時フォルダ（`%TEMP%`）の下に作る。git（Git for Windows 2.31 以降）が要る

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/dagu/gitlab-issue-agent.env` | GitLab の URL・プロジェクト・トークン |
| `~/.config/api-watch/api-watch.yaml` | 取得の予定・ずらし・監視する項目・絞り込み・再試行・実行の上限・`lock` |
| `~/.config/dagu/gitlab-issue-agent.prompt.md` | プロンプト |
| `~/.config/dagu/gitlab-issue-agent.handle.yaml` | エージェント CLI の種類・フラグ・作業フォルダ |

## テスト

```bash
cd tools/gitlab-issue-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-issue-agent-design.md`](../../docs/designs/gitlab-issue-agent-design.md)
