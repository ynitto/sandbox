# gitlab-mr-review-agent

決めた GitLab のリポジトリのマージリクエスト（MR）を定期的に見に行き、**新しいコミットが積まれた MR** を kiro-cli に 1 件ずつレビューさせる。
kiro-cli は差分の行に指摘のスレッドを立て、次のコミットで直っていればそのスレッドに返信して**解決にする**。
**コミットが変わっていない MR は二度レビューしない**（コメント・返信・ラベルの変更では起こさない）。

MR の取得・変化の検知・実行待ち・再試行は [api-watch](../api-watch/README.md) が、起動と kiro-cli の実行は
[Dagu](https://github.com/dagu-org/dagu)（v2.25 以降。`harness.run` を使う）が受け持つ。[gitlab-issue-agent](../gitlab-issue-agent/README.md) と
同じ作りで、このフォルダには、MR に固有の部分を足す `gitlab_mr.py`（api-watch のプラグインで、kiro-cli が GitLab に読み書きする
コマンドも兼ねる）と、設定の例がある。

```
 Dagu: api-watch-mr-review（毎分起動。1 回の起動で 1 件をレビューする。GitLab への問い合わせは 15 分ごと）
   fetch      開いている MR を全ページ取得
   evaluate   前回と比べて、head のコミット・ドラフト・対象ブランチが変わった MR を拾う
   claim      MR を 1 件取り直す。レビューし終えたコミットと同じ・ドラフト・マージ済みなら流さない
   render     プロンプトを組む（MR の番号・前回レビューしたコミット・使えるコマンド）
   handle     kiro-cli がレビューする（Dagu の harness.run）
                context で前回の指摘と差分を読む → 直った指摘を resolve → 新しい問題を comment → summary
   finish     レビューし終えたコミットを記録する（失敗ならあとでやり直す）
```

## 入れ方

1. プラグインとプロンプトのテンプレートを置く。

   ```bash
   mkdir -p ~/.local/share/gitlab-mr-review-agent ~/.config/dagu
   ```

   ```bash
   cp tools/gitlab-mr-review-agent/gitlab_mr.py ~/.local/share/gitlab-mr-review-agent/
   ```

   ```bash
   cp tools/gitlab-mr-review-agent/prompt.md.example ~/.config/dagu/gitlab-mr-review-agent.prompt.md
   ```

2. GitLab の URL・リポジトリ・トークンを書く。`gitlab-mr-review-agent.env.example` を `~/.config/dagu/gitlab-mr-review-agent.env` にコピーして埋める。
   トークンには `api` スコープが要り、**このトークンの持ち主が指摘の書き手**になる。スレッドを解決にするには、そのリポジトリで Developer 以上の権限が要る。
   `MR_REVIEW_CLI` は 1 で置いた `gitlab_mr.py` のフルパスにする。

   ```bash
   cp tools/gitlab-mr-review-agent/gitlab-mr-review-agent.env.example ~/.config/dagu/gitlab-mr-review-agent.env
   ```

3. api-watch を入れる。

   ```bash
   python3 tools/api-watch/install.py install
   ```

4. api-watch の設定を置く。レビューする MR を絞るなら、設定の取得元の `query`（例: `target_branch: main`）とルールの `filter` に書く。
   gitlab-issue-agent と同じ PC で動かすなら、両方の `sources` と `rules` を 1 つの設定ファイルにまとめる。

   ```bash
   mkdir -p ~/.config/api-watch
   ```

   ```bash
   cp tools/gitlab-mr-review-agent/api-watch.yaml.example ~/.config/api-watch/api-watch.yaml
   ```

5. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-mr-review-agent.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

6. kiro-cli の動かし方を書く。`handle-step.yaml.example` を `~/.config/dagu/gitlab-mr-review-agent.handle.yaml` にコピーし、作業フォルダ（`working_dir`）を直す。
   kiro-cli はこの PC に入れて、ログインしておく。作業フォルダにレビューするリポジトリの作業コピーを置くと、kiro-cli が差分の周りのコードも読める。

   ```bash
   cp tools/gitlab-mr-review-agent/handle-step.yaml.example ~/.config/dagu/gitlab-mr-review-agent.handle.yaml
   ```

7. 環境変数を読み込んでから、Dagu のワークフローの雛形を書き出す。`api-watch-mr-review.yaml`（取得からレビューまで。6 のステップが handle に入る。
   中身は `api-watch-mr-review.yaml.example` と同じで、パスが自分の PC のものになる）と `api-watch-maintenance.yaml`（毎日の整理）ができる。

   ```bash
   set -a; . ~/.config/dagu/gitlab-mr-review-agent.env; set +a
   ```

   ```bash
   api-watch --plugin dagu --plugin ~/.local/share/gitlab-mr-review-agent/gitlab_mr.py --config ~/.config/api-watch/api-watch.yaml --connections ~/.github/connections.yaml dags --out ~/.config/dagu/dags --dotenv ~/.config/dagu/gitlab-mr-review-agent.env --handle-step mr-review.run=~/.config/dagu/gitlab-mr-review-agent.handle.yaml
   ```

8. Dagu のスケジューラを動かす（`dagu start-all` など）。設定の例は `initial: emit` なので、**最初の取得で、いま開いている MR もレビューする**
   （1 分に 1 件ずつ）。これから変わる MR だけにしたいなら、動かす前に `initial: baseline` にする。

## いつレビューするか

| MR の変化 | レビューするか |
|---|---|
| 新しく開かれた（ドラフトでない） | する |
| コミットが積まれた・force push された | する（前回レビューしたコミットからの差分を見る） |
| ドラフトを外した | する（ドラフトのあいだはしない） |
| 対象ブランチを変えた | する |
| コメント・返信・ラベル・説明・担当の変更 | しない |
| kiro-cli 自身の指摘・解決 | しない |
| マージ・クローズされた | しない（待つあいだにマージされたものも取り直して飛ばす） |

- レビューし終えたコミットは api-watch が覚える。同じコミットのあいだは、何度取得しても流さない
- レビューのあいだにコミットが積まれたら、レビューし終えたのは実行前のコミットまでにし、次の取得で新しいコミットをレビューする
- 失敗したときのやり直しでも、指摘は重ならない（同じ指摘・同じコミットのまとめは書かない）。今のコミットのまとめが既にあれば、
  kiro-cli は何もせずに終える

## 指摘の閉じ方

- kiro-cli は、自分が立てた未解決のスレッドを、新しいコミットで 1 件ずつ確かめる。直っていれば、何がどう直ったかを返信して解決にする。
  直っていなければそのまま残し、同じ指摘を書き直さない
- 返信で「意図どおり」などと説明されていて納得できれば、その旨を書いて閉じることもある
- 閉じるのは自分が立てたスレッドだけ。人のスレッドには触らない
- 差分に無い行への指摘は、場所（`パス:行`）を本文に書いた MR 全体のスレッドになる。閉じ方は同じ
- MR の承認・マージ・ラベルの変更はしない

## GitLab への問い合わせの回数

- MR の一覧は **15 分に 1 回**（予定の時刻から 0〜5 分ずらす）。100 件ごとに 1 リクエスト
- 1 件をレビューするたびに、api-watch が実行の前後に MR を取り直す 2 回と、レビュー中のコミットの追加を確かめる 1 回。
  ほかに kiro-cli が使うコマンドが、MR・差分・スレッド・ノートを読み、指摘・返信・まとめを書く
- 取得が失敗したら 5 分（続けて失敗するほど倍、最大 1 時間）は問い合わせない

予定は api-watch の設定の取得元の `schedule`、ずらす幅は `jitter_sec` で変えられる。

## 1 セッションだけにする仕組み

kiro-cli は同時に 1 つだけ動く。api-watch の設定の `lock`（例は `agent-session`）が同じ受け渡し先どうしは、gitlab-issue-agent のワークフローと
またいでも同時に 1 つだけ実行し、空いていなければ次の起動でもう一度試す。1 件の上限は `timeout_sec`（例は 1800 秒）。

## 失敗したとき

kiro-cli が 0 以外で終わる・時間切れ・止められると失敗になり、5 分（1 回ごとに倍）たってから同じコミットでやり直す。3 回失敗したら打ち切り、
その MR に次のコミットが積まれたときにもう一度拾う。

| したいこと | コマンド |
|---|---|
| 失敗している・打ち切ったレビューを見る | `api-watch --config ~/.config/api-watch/api-watch.yaml events --rule mr-review --status send_failed,dead` |
| 打ち切ったレビューをすぐやり直す | `api-watch --config ~/.config/api-watch/api-watch.yaml retry <受け渡しの ID>` |
| レビューし終えた MR をもう一度レビューさせる | `api-watch --config ~/.config/api-watch/api-watch.yaml forget-handled --rule mr-review 12` と、MR のまとめのコメントを消す |

## kiro-cli とプロンプト

- kiro-cli は Dagu の `harness.run` で動かす（`gitlab-mr-review-agent.handle.yaml`）。プロンプトは引数で渡し（`prompt: "${INPUT}"`）、
  `trust_all_tools: true`（`--trust-all-tools`）で確認なしにコマンドを実行させる。Dagu の `harness.run` が使えない版なら、
  ファイルのコメントにある `action: exec` で kiro-cli を直接呼ぶ
- プロンプトは `~/.config/dagu/gitlab-mr-review-agent.prompt.md`（api-watch の設定の `input_template`）。何を指摘するか・まとめに何を書くかは
  ここで変える。`{{ event.record.iid }}`・`{{ event.record.title }}`・`{{ event.record.web_url }}`・`{{ delivery.attempt }}`（何回目か）と、
  プラグインの `{{ event | gitlab_mr_since }}`（前回レビューしたコミット）・`{{ event | gitlab_mr_kind }}`（初めて / 再レビュー）が使える
- GitLab への読み書きは、プロンプトに書いた `gitlab_mr.py` のコマンドだけで行わせる

## kiro-cli が使うコマンド（gitlab_mr.py）

GitLab の URL・トークン・リポジトリは環境変数 `GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_PROJECT` から読む（Dagu が `.env` から渡す）。

| コマンド | すること |
|---|---|
| `context <MR> [--since <コミット>]` | MR の概要・自分が立てた未解決のスレッド（返信つき）・差分を出す。`--since` があればそこからの差分。今のコミットをレビューし終えていればそう出す |
| `show <MR> <パス> [--from N --to M]` | 今のコミットのファイルを行番号つきで出す |
| `comment <MR> --path <パス> --line <行> --body-file <ファイル>` | 差分の行に指摘のスレッドを立てる。同じ指摘が既にあれば書かない |
| `resolve <MR> <スレッド> --body <文>` | 自分が立てたスレッドに返信して解決にする |
| `summary <MR> --body-file <ファイル>` | 今のコミットのレビューのまとめを 1 回だけ書く |

`--body-file` の代わりに `--body <文>` でもよい。手で試すときは、環境変数を読み込んでから `python3 gitlab_mr.py context 12` のように呼ぶ。

## プラグイン（gitlab_mr.py）が足すもの

| 名前 | 種類 | すること |
|---|---|---|
| `gitlab_mr_since` | フィルタ | 前回レビューし終えたときの head のコミット（初めてなら空） |
| `gitlab_mr_kind` | フィルタ | 初めてのレビュー / 再レビュー |
| `gitlab_mr.changed_during_review` | チェック | レビューのあいだにコミット・ドラフト・対象ブランチが変わったか（`handled_before_if` に書く） |

## Windows で使うとき

- api-watch は `python install.py install` で入れる
- `gitlab-mr-review-agent.env` の `MR_REVIEW_CLI` は `python C:/Users/<名前>/.local/share/gitlab-mr-review-agent/gitlab_mr.py` のように書く
- `gitlab-mr-review-agent.handle.yaml` の `working_dir` は `C:/Users/you/work/project` のように書く

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/dagu/gitlab-mr-review-agent.env` | GitLab の URL・リポジトリ・トークン・`gitlab_mr.py` の呼び方 |
| `~/.github/connections.yaml` | GitLab の接続（値は上の環境変数から取る） |
| `~/.config/api-watch/api-watch.yaml` | 取得の予定・ずらし・対象の MR の絞り込み・再試行・実行の上限・`lock` |
| `~/.config/dagu/gitlab-mr-review-agent.prompt.md` | プロンプト（レビューの観点） |
| `~/.config/dagu/gitlab-mr-review-agent.handle.yaml` | kiro-cli のフラグ・作業フォルダ |

## テスト

```bash
cd tools/gitlab-mr-review-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-mr-review-agent-design.md`](../../docs/designs/gitlab-mr-review-agent-design.md)
