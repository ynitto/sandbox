# gitlab-issue-agent

GitLab の issue を定期的に見に行き、変化があった issue をエージェント CLI（Claude Code / Codex / Kiro / Copilot など）に
1 件ずつ渡す。**同時に動くエージェント CLI は常に 1 つ**で、エージェント自身のコメントでもう一度起きることはない。

起動・条件の分岐・エージェント CLI の実行・再試行は [Dagu](https://github.com/dagu-org/dagu)（v2.25 以降。`harness.run` を使う）のワークフローが、
GitLab への問い合わせ（いつ取得するか・まとめる・失敗の待ち）と「前回処理してから変わった issue」を返すことは [change-watch](../../README.md) が
受け持つ（change-watch の README の「条件をワークフローで判定する」）。どの issue をどう処理するかの条件は、ワークフローに書いてある。

```
 Dagu: change-watch-issue-agent（毎分起動。1 回の起動で 1 件を処理する）
   get      前回処理してから変わった issue を 1 件受け取る（GitLab への問い合わせは毎時 0 分の予定のあと、0〜15 分ずらして 1 回）
   new      新しい issue で、開いていれば → エージェント CLI に着手させる                ← 条件（Dagu の preconditions）
   update   変わった issue で、開いていれば → 変わったところ（before・changes）を見て対応させる
   commit   issue を取り直した姿で、処理し終えたと記録する（エージェント自身のコメントで次に起きない）

 変わった issue が無ければ get のあとは動かない。複数あれば、次の起動（1 分後）が次の 1 件を受け取る
```

## 入れ方

1. インストーラで入れる。change-watch と、このエージェントのプログラム（`gitlab_issue.py`）・設定の雛形・Dagu のワークフローを、
   どのエージェントも同じ決まりの場所に置く（下の表。決まりは [change-watch の README](../../README.md) の「エージェントを入れる」）。
   ワークフローのパスは、この PC のものにして置く。既にある設定とワークフローは上書きしないので、入れ直すとプログラムだけが新しくなる。

   ```bash
   python3 tools/change-watch/install.py install --agent gitlab-issue-agent
   ```

   | 置かれるもの | 場所 |
   |---|---|
   | プログラム | `~/.local/share/change-watch/agents/gitlab-issue-agent/`（Windows は `%LOCALAPPDATA%\Programs\change-watch\agents\gitlab-issue-agent\`） |
   | 設定（`change-watch.yaml`・`gitlab-issue-agent.env`） | `~/.config/change-watch/gitlab-issue-agent/` |
   | Dagu のワークフロー（`change-watch-issue-agent.yaml`・整理の `change-watch-gitlab-issue-agent-maintenance.yaml`） | `~/.config/dagu/dags/` |
   | 状態 | `~/.local/state/change-watch/gitlab-issue-agent/` |

2. GitLab の URL・プロジェクト・トークンを `~/.config/change-watch/gitlab-issue-agent/gitlab-issue-agent.env` に書く。
   トークンには `read_api` が要る（エージェントが issue にコメントするなら、そのエージェント側の権限も別に要る）。
   取得する issue の絞り込みは `~/.config/change-watch/gitlab-issue-agent/change-watch.yaml` の `query` に書く。

3. `~/.config/dagu/dags/change-watch-issue-agent.yaml` の `new`・`update` の `provider`（`claude` / `codex` / `kiro` / `copilot` など）・フラグ・
   作業フォルダ（`working_dir`）・`prompt` を自分のものに直す。どの issue を渡すかを変えるなら、そのステップの `preconditions` を直す
   （下の「条件で分ける」）。CLI はこの PC に入れて、ログインしておく。Dagu の設定（`config.yaml`）に足すものは無い。

4. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-issue-agent.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

5. 環境変数を読み込んで、一度手で取得して基準を取る（今ある issue を覚えるだけで、エージェントは起こさない）。そのあと Dagu のスケジューラを動かす
   （`dagu start-all` など）。以後は issue にコメント・ラベル変更・本文の更新などがあると、次の取得（1 時間に 1 回）でエージェントが起きる。

   ```bash
   set -a; . ~/.config/change-watch/gitlab-issue-agent/gitlab-issue-agent.env; set +a
   ```

   ```bash
   change-watch --config ~/.config/change-watch/gitlab-issue-agent/change-watch.yaml --connections ~/.github/connections.yaml get --source gitlab-issues --consumer issue-agent --no-jitter
   ```

## 条件で分ける

ワークフローの `new`・`update` は、`get` が出した変化（`CHANGE`）を Dagu の `preconditions` で見て、合ったときだけ動く。
`${CHANGE.kind}` は `appeared`（新しい issue）・`changed`（変わった issue）・`removed`（一覧から消えた。例では閉じた issue）、
`${CHANGE.record.*}` は今の issue の項目、`${CHANGE.before.*}` は前回処理したときの issue の項目。

| ステップ | 条件 | エージェントに頼むこと |
|---|---|---|
| `new` | `kind` が `appeared`、`state` が `opened` | 新しい issue に着手して、結果を issue にコメントする |
| `update` | `kind` が `changed`、`state` が `opened` | 変わったところに対応して、結果を issue にコメントする |

分け方を足すなら、同じ形のステップを足して `preconditions` を変え、`commit` の `depends` に足す。例えば、ラベル `bug` が付いた issue だけ
別のプロンプトにするなら `condition: "${CHANGE.record.labels}"`・`expected: 're:"bug"'`。どのステップにも合わなかった変化も `commit` する
（`continue_on.skipped`）ので、同じ変化が何度も来ることはない。

## GitLab への問い合わせの回数

- issue の一覧の取得は **1 時間に 1 回**。予定の時刻（毎時 0 分）から 0〜15 分のあいだでランダムにずらして問い合わせる。
  複数の PC やプロジェクトで動かしても、問い合わせが同じ時刻に重ならない。1 回の取得は 100 件ごとに 1 リクエスト
- ワークフローは毎分起動するが、予定の時刻のあとに取得し終えていれば一覧は取得しない（前回の取得結果から次の 1 件を返す）
- エージェントを 1 回流すたびに 1 リクエスト（処理し終えたときに issue を取り直す `commit --refresh`）
- 取得が失敗したら 5 分（続けて失敗するほど倍、最大 1 時間）は問い合わせない。そのあいだは前回までの取得結果で答える

予定は change-watch の設定の取得元の `schedule`、ずらす幅は `jitter_sec` で変えられる。予定を変えるときは change-watch の設定だけを直せばよく、
ワークフローは直さなくてよい。

## 何を「変化」とみなすか

次のどれかが前回処理し終えたときから変わったときだけ流す。`updated_at` だけが動いた更新（MR からの言及など）では起こさない。

- 新しい issue / タイトル / 状態（開いた・閉じた）/ ラベル / 担当 / マイルストーン / 期日 / コメント数 / 本文

見る項目はワークフローの `get` の `--watch` に書いてある。

## 重複させない仕組み

- 「前回処理し終えた姿」は change-watch がこのワークフロー（`--consumer issue-agent`）ごとに覚えていて、`get` はそこから変わった issue だけを返す。
  `commit` するまで同じ issue を返し続ける
- `commit --refresh` は、処理し終えたときに issue を取り直し、その姿を「処理し終えた姿」にする。**エージェント自身のコメントやラベル変更で
  もう一度起きることはない**
- 処理を待つ間に同じ issue が何度か変わっても、受け取るのは最新の取得結果の姿で、まとめて 1 回流れる
- 処理の途中で自分以外の人が書いたコメントも、取り直した姿に含めて処理し終えたことにする。拾い直させたいときは、もう一度コメントするか
  ラベルを変える

## 1 セッションだけにする仕組み

- ワークフローは 1 回の起動で 1 件だけ処理する。同じワークフローが重ねて起動されることはない（Dagu の既定）
- 別のプロジェクトのワークフローとも同時に動かさないなら、Dagu のキュー（`queue`）で同じキューに入れ、キューの並列数を 1 にする

## 失敗したとき

エージェント CLI が 0 以外で終わる・時間切れになると、Dagu が `retry_policy` のとおり 300 秒おいて 2 回までやり直す。尽きたら
失敗として Dagu の実行履歴に残り、`commit` はする（同じ変化は流し直さない。その issue が次に変化したときにもう一度拾う）。
すぐ流し直したいときは、Dagu の画面からそのステップを再実行する。

GitLab に問い合わせられない（一時的な失敗）ときは、`get` が終了コード 3 で終わり、Dagu が 1 分おいてやり直す。

## エージェント CLI とプロンプト

- エージェント CLI は Dagu の `harness.run` で動かす（ワークフローの `new`・`update` のステップ）。`provider` に `claude` / `codex` / `kiro` / `copilot` / `gemini` などを書き、
  `with` のほかの項目は CLI のフラグになる（`dangerously_skip_permissions: true` → `--dangerously-skip-permissions`）。権限の確認で止まらないよう、
  無人で動かせるフラグを付けること
- プロンプトはワークフローの `prompt` に書く。`${CHANGE.record.iid}`・`${CHANGE.record.title}`・`${CHANGE.record.web_url}` などで issue の項目を埋め込める
- 変化の JSON（`key`・`kind`・`record`・`before`・`changes`）はエージェント CLI の標準入力に渡る（`stdin: "${CHANGE}"`）

## プラグイン（gitlab_issue.py）が足すもの

既定のワークフローでは使わない。下の「ルールで判定する形」で使う。

| 名前 | 種類 | すること |
|---|---|---|
| `gitlab_issue_changes` | フィルタ | 前回処理し終えたとき（無ければ前回の取得）からの変化の箇条書き |
| `gitlab_issue_kind` | フィルタ | 新規 / 更新 |
| `gitlab.others_acted` | チェック | 実行を始めてから、自分以外の人が issue にコメント・操作したか（`handled_before_if` に書く） |
| `gitlab_idd_role` | フィルタ | gitlab-idd のロール（`status:review-ready` ならレビュアー、`status:open` などならワーカー）。ルールで判定する形で gitlab-idd と繋ぐとき |
| `gitlab_idd.role_changed` | チェック | 実行のあいだに gitlab-idd のロールが変わったか（`handled_before_if` に書くと、次のロールでもう一度流れる） |

GitLab への問い合わせは、change-watch の取得元の接続で送り、要求の間隔・失敗の待ちを取得と分け合う。

## gitlab-idd スキルと繋ぐ

[gitlab-idd](../../../../.github/skills/gitlab-idd/SKILL.md) スキルは、GitLab のイシューを `status:*` のラベルで回すイシュー駆動開発のスキルで、
「イシューを拾って」（ワーカー）や「イシューをレビューして」（レビュアー）と頼まれたときに 1 回ずつ動く。これを gitlab-issue-agent に繋ぐと、
ラベルやコメントが変わったイシューを、そのラベルに合ったロールで自動で処理させられる。設定の例は `examples/gitlab-idd/` にある。

| イシューのラベル | エージェントに頼むこと |
|---|---|
| `status:open` / `status:needs-rework` / `status:needs-clarification` | gitlab-idd のワーカーロールで、そのイシューを実装してブランチ・MR・コメントで報告する |
| `status:review-ready` | gitlab-idd のレビュアーロールで、そのイシューの受け入れ条件を評価して承認か差し戻しをする |
| それ以外（`status:in-progress` / `status:approved` / `status:done` など） | 流さない（マージは人が行う） |

ラベルでの絞り込みとロールの分岐は、ワークフローの `preconditions`（Dagu）に書いてある。change-watch の設定には取得元だけを書く。
ロールごとに consumer（`idd-worker`・`idd-reviewer`）を分け、それぞれが「前回そのロールで見てから変わったイシュー」を受け取る。

```
 Dagu: change-watch-idd（毎分起動。1 回の起動でロールごとに 1 件まで。エージェント CLI は 1 つずつ動く）
   get-work      ワーカーの側で変わったイシューを 1 件受け取る（--consumer idd-worker）
   work          ラベルが status:open / needs-rework / needs-clarification なら → ワーカーロールで処理   ← 条件（Dagu）
   commit-work   見終えたと記録する
   absorb-work   work が動いたイシューは、取り直した姿で記録し直す（自分の書き込みでワーカーがもう一度起きない）
   get-review → review（status:review-ready なら → レビュアーロール）→ commit-review → absorb-review   ← 同じ形

 イシューの進み方: status:open ─ワーカー→ status:review-ready ─レビュアー→ status:approved（人がマージ）
                                              └─差し戻し→ status:needs-rework ─ワーカー→ …
```

ワークフローの条件の部分（抜粋）:

```yaml
  - name: work
    depends: [get-work]
    preconditions:              # 条件（Dagu）: ワーカーが受け持つラベルのイシュー
      - condition: "${TO_WORK.kind}"
        expected: "re:^(appeared|changed)$"
      - condition: "${TO_WORK.record.labels}"
        expected: 're:"status:(open|needs-rework|needs-clarification)"'
    continue_on: { skipped: true, failure: true }
    action: harness.run
  - name: review
    depends: [get-review]
    preconditions:              # 条件（Dagu）: レビューを待つイシュー
      - condition: "${TO_REVIEW.kind}"
        expected: "re:^(appeared|changed)$"
      - condition: "${TO_REVIEW.record.labels}"
        expected: 're:"status:(review-ready)"'
    continue_on: { skipped: true, failure: true }
    action: harness.run
```

- ワーカーが `status:review-ready` にしたり、レビュアーが差し戻したりして**ロールが変わったときは、エージェント自身の書き込みでも相手のロールの側に変化として届く**
  （consumer をロールごとに分けているため。同じ起動の中で続けて動くこともある）。ロールが変わらない自分の書き込み（質問のコメント・
  `status:needs-clarification` への変更など）では、`absorb-work` が取り直した姿を基準にするので流さない
- 依頼者が質問に答える（コメントする）と、ワーカーがもう一度呼ばれる
- エージェントには「このイシューだけを処理して」と頼む。gitlab-idd が候補の一覧から別のイシューを選ばないように、プロンプトで止めている

### 入れ方

上の「入れ方」のとおり入れてから、次のファイルを `examples/gitlab-idd/` のものに替える（パスの `/home/you` は自分の場所に直す）。

| 置く場所 | 例 | 替えるもの |
|---|---|---|
| `~/.config/change-watch/gitlab-issue-agent/change-watch.yaml` | `change-watch.yaml.example` | 設定（取得元だけ。`gitlab:watch` の接続を使う） |
| `~/.github/connections.yaml` | `connections.yaml.example` | 4 の接続設定（`default` は gitlab-idd 用のプロジェクトの URL、`watch` は change-watch 用の GitLab の URL） |
| `~/.config/dagu/dags/change-watch-idd.yaml` | `change-watch-idd.yaml.example` | ワークフロー（ラベルでの絞り込みとロールの分岐・プロンプト）。インストーラが置いた `change-watch-issue-agent.yaml` は消す |
| エージェントのホームの `skill-registry.json`（Claude Code なら `~/.claude/skill-registry.json`） | `skill-registry.json.example` | 足す（既にあれば `skill_configs.gitlab-idd` だけを足す） |

`gitlab-issue-agent.env` には `SKILL_CONNECTIONS_FILE`（上の connections.yaml のパス。gitlab-idd にも同じ接続設定を読ませる）と
`GITLAB_NODE_ID` も書く（`gitlab-issue-agent.env.example` の該当の行）。エージェント CLI から gitlab-idd スキルが使えるようにしておく
（Claude Code なら `~/.claude/skills/gitlab-idd`）。

既にある作業コピーを使うなら、`gitlab-issue-agent.env` の `GITLAB_IDD_LOCAL_REPO` にそのフォルダ（`work`・`review` のステップの `working_dir` と同じでよい）を書く。
gitlab-idd のワーカーは、イシューごとにリポジトリをクローンする代わりに、そのリポジトリへ `git worktree` を足して作業し、終わったら外す
（取ってくるのは対象ブランチの新しいコミットだけ）。作業コピーのブランチ・書きかけの変更には触らない。作業コピーの `origin` は
gitlab-idd 用のプロジェクトを指していること。書かなければ、従来どおりイシューごとに一時フォルダへクローンする。

ワークフローは `change-watch-issue-agent.yaml` の代わりに `change-watch-idd.yaml` を置く。5 はロールごとに基準を取る。

```bash
change-watch --config ~/.config/change-watch/gitlab-issue-agent/change-watch.yaml --connections ~/.github/connections.yaml get --source gitlab-issues --consumer idd-worker --no-jitter
```

```bash
change-watch --config ~/.config/change-watch/gitlab-issue-agent/change-watch.yaml --connections ~/.github/connections.yaml get --source gitlab-issues --consumer idd-reviewer --no-fetch
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

## ルールで判定する形（`examples/rules/`）

条件も change-watch の設定（ルール `rules`）に書き、change-watch が issue を選んでプロンプトを組み、実行の前後に issue を取り直す形の例。
入れ方の 3 で、`~/.config/change-watch/gitlab-issue-agent/change-watch.yaml` を `examples/rules/change-watch.yaml.example` に、
`~/.config/dagu/dags/change-watch-issue-agent.yaml` を `examples/rules/change-watch-issue-agent.yaml.example` に替え、
`examples/rules/prompt.md.example` を `~/.config/change-watch/gitlab-issue-agent/prompt.md` に置く（`/home/you` は自分の場所に直す）。
5 は `run --rule issue-agent --no-jitter` で取る（`--plugin ~/.local/share/change-watch/agents/gitlab-issue-agent/gitlab_issue.py` を付ける）。
既定の形との違い:

- 実行の直前にも issue を取り直し、絞り込み（ルールの `filter`）から外れた issue は流さない
- 実行中に自分以外の人がコメントしたときは、処理し終えたのは実行前の状態までにして、次の取得でもう一度流す（チェック `gitlab.others_acted`）
- change-watch の設定の `lock` で、プロジェクトをまたいでも同時に 1 つだけ実行する
- 失敗した変化は change-watch が待ってから流し直し（`retry_backoff_sec`・`max_attempts`）、`events`・`retry` で見たり流し直したりできる

## Windows で使うとき

- インストーラは `python tools\change-watch\install.py install --agent gitlab-issue-agent` で、Dagu から使う Python で動かす。
  ワークフローの `command`（その Python と `change-watch.pyz`）は、インストーラが Windows の形にして置く
- Dagu の設定フォルダは `C:\Users\<名前>\.config\dagu\` で、ワークフローは `dags\` に置かれる（別の場所ならインストーラに `--dagu-dir`）
- ワークフローの `new`・`update` の `working_dir` は `C:/Users/you/work/project` のように書く（`GITLAB_IDD_LOCAL_REPO` も同じ書き方）
- gitlab-idd のワーカーの作業用フォルダは、一時フォルダ（`%TEMP%`）の下に作る。git（Git for Windows 2.31 以降）が要る

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/change-watch/gitlab-issue-agent/gitlab-issue-agent.env` | GitLab の URL・プロジェクト・トークン |
| `~/.config/change-watch/gitlab-issue-agent/change-watch.yaml` | 取得する issue の絞り込み（`query`）・取得の予定・ずらし・問い合わせの間隔 |
| `~/.config/dagu/dags/change-watch-issue-agent.yaml` | 見る項目（`--watch`）・条件（`preconditions`）・プロンプト・エージェント CLI の種類・フラグ・作業フォルダ・再試行 |

## テスト

```bash
cd tools/change-watch/agents/gitlab-issue-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-issue-agent-design.md`](../../../../docs/designs/gitlab-issue-agent-design.md)
