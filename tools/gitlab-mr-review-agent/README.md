# gitlab-mr-review-agent

決めた GitLab のリポジトリのマージリクエスト（MR）を定期的に見に行き、**新しいコミットが積まれた MR** を kiro-cli に 1 件ずつレビューさせる。
kiro-cli は差分の行に指摘のスレッドを立て、次のコミットで直っていればそのスレッドに返信して**解決にする**。
**コミットが変わっていない MR は二度レビューしない**（コメント・返信・ラベルの変更では起こさない）。

MR の取得・変化の検知・実行待ち・再試行は [change-watch](../change-watch/README.md) が、起動と kiro-cli の実行は
[Dagu](https://github.com/dagu-org/dagu)（v2.25 以降。`harness.run` を使う）が受け持つ。[gitlab-issue-agent](../gitlab-issue-agent/README.md) と
同じ作りで、このフォルダには、MR に固有の部分を足す `gitlab_mr.py`（change-watch のプラグインで、kiro-cli が GitLab に読み書きする
コマンドも兼ねる）と、設定の例がある。

```
 Dagu: change-watch-mr-review（毎分起動。1 回の起動で 1 件をレビューする。GitLab への問い合わせは 15 分ごと）
   fetch      開いている MR を全ページ取得
   evaluate   前回と比べて、head のコミット・ドラフト・対象ブランチが変わった MR を拾う
   claim      MR を 1 件取り直す。レビューし終えたコミットと同じ・差分の中身が前にレビューしたときと同じ（リベースなど）・
              ドラフト・マージ済みなら流さない（kiro-cli を起こさない）
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
   `MR_REVIEW_CLI` は 1 で置いた `gitlab_mr.py` のフルパスにする。`MR_REVIEW_REPO_DIR` は手元のリポジトリで、**既にある作業コピーを指定してよい**
   （その作業ツリー・ブランチ・書きかけの変更には触らない。無ければ 6 で 1 回だけ取ってくる）。`MR_REVIEW_WORKTREE` はレビュー用の作業フォルダで、
   そのリポジトリに `git worktree` で足す（レビューのたびに MR のコミットに切り替え、ほかのファイルは消すので、ほかの用途に使わない）。

   ```bash
   cp tools/gitlab-mr-review-agent/gitlab-mr-review-agent.env.example ~/.config/dagu/gitlab-mr-review-agent.env
   ```

3. change-watch を入れる。

   ```bash
   python3 tools/change-watch/install.py install
   ```

4. change-watch の設定を置く。レビューする MR を絞るなら、設定の取得元の `query`（例: `target_branch: main`）とルールの `filter` に書く。
   gitlab-issue-agent と同じ PC で動かすなら、両方の `sources` と `rules` を 1 つの設定ファイルにまとめる。

   ```bash
   mkdir -p ~/.config/change-watch
   ```

   ```bash
   cp tools/gitlab-mr-review-agent/change-watch.yaml.example ~/.config/change-watch/change-watch.yaml
   ```

5. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-mr-review-agent.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

6. Dagu のワークフローを置く。`change-watch-mr-review.yaml.example` を `~/.config/dagu/dags/change-watch-mr-review.yaml` にコピーし、
   `handle` の作業フォルダ（`working_dir`）を 2 の `MR_REVIEW_WORKTREE` と同じにして、`/home/you` を自分のものに直す。
   kiro-cli はこの PC に入れて、ログインしておく。毎日の整理のワークフローは change-watch の例をそのまま置く。
   Dagu の設定（`config.yaml`）に足すものは無い。

   ```bash
   cp tools/gitlab-mr-review-agent/change-watch-mr-review.yaml.example ~/.config/dagu/dags/change-watch-mr-review.yaml
   ```

   ```bash
   cp tools/change-watch/dags/change-watch-maintenance.example.yaml ~/.config/dagu/dags/change-watch-maintenance.yaml
   ```

7. 環境変数を読み込んで、`prepare` でレビュー用の作業フォルダを作る（リポジトリが無ければ、ここで 1 回だけ取ってくる。ファイルの中身は
   要るときに取るので、履歴の大きなリポジトリでも軽い）。git（2.31 以降）が要る。

   ```bash
   set -a; . ~/.config/dagu/gitlab-mr-review-agent.env; set +a
   ```

   ```bash
   python3 ~/.local/share/gitlab-mr-review-agent/gitlab_mr.py prepare
   ```

8. Dagu のスケジューラを動かす（`dagu start-all` など）。設定の例は `initial: emit` なので、**最初の取得で、いま開いている MR もレビューする**
   （1 分に 1 件ずつ）。これから変わる MR だけにしたいなら、動かす前に `initial: baseline` にする。

## いつレビューするか

| MR の変化 | レビューするか |
|---|---|
| 新しく開かれた（ドラフトでない） | する |
| コミットが積まれた・中身の変わる force push | する（前回レビューしたコミットからの差分のうち、この MR が触っているファイルを見る） |
| リベースだけ・中身の同じ force push | しない（head は変わるが、足した行・消した行が前にレビューしたときと同じ） |
| ドラフトを外した | する（ドラフトのあいだはしない） |
| 対象ブランチを変えた | する（差分の中身が変わったとき） |
| 閉じて開き直した | しない（中身が前にレビューしたときと同じなら） |
| コメント・返信・ラベル・説明・担当の変更 | しない |
| kiro-cli 自身の指摘・解決 | しない |
| マージ・クローズされた | しない（待つあいだにマージされたものも取り直して飛ばす） |

- レビューし終えたコミットは change-watch が覚える。同じコミットのあいだは、何度取得しても流さない
- 「レビューし終えたか」は、MR の差分の中身（ファイルの名前と、足した行・消した行）でも見る。中身が同じなら、head のコミットが
  変わっていても（リベース・マージトレイン）、kiro-cli を起こさずに飛ばす。GitLab が中身を返さないほど大きなファイルがある MR は、
  中身を比べられないので、リベースでもレビューする
- コミットを続けて積んでも、レビューは取得（15 分に 1 回）ごとに最新のコミットで 1 回にまとまる
- レビューのあいだにコミットが積まれたら、レビューし終えたのは実行前のコミットまでにし、次の取得で新しいコミットをレビューする
- 失敗したときのやり直しでも、指摘は重ならない（同じ指摘・同じコミットのまとめは書かない）。今のコミットのまとめが既にあれば、
  kiro-cli は何もせずに終える

## 指摘の閉じ方

- kiro-cli は、自分が立てた未解決のスレッドを、新しいコミットで 1 件ずつ確かめる。直っていれば、何がどう直ったかを返信して解決にする。
  直っていなければそのまま残し、同じ指摘を書き直さない。未解決の指摘がある行には、言い直した指摘も書けない
  （`comment` が止める）
- 返信で「意図どおり」などと説明されていて納得できれば、その旨を書いて閉じることもある
- 解決済みの指摘（人が閉じたものも）は `context` が一覧にし、kiro-cli に同じ内容をもう一度指摘させない
- 閉じるのは自分が立てたスレッドだけ。人のスレッドには触らない
- 差分に無い行への指摘は、場所（`パス:行`）を本文に書いた MR 全体のスレッドになる。閉じ方は同じ
- MR の承認・マージ・ラベルの変更はしない

## GitLab への問い合わせの回数

- MR の一覧は **15 分に 1 回**（予定の時刻から 0〜5 分ずらす）。100 件ごとに 1 リクエスト
- 1 件を流す前に、差分の中身をレビューし終えているかを確かめる（差分とノートを読む。100 件ごとに 1 リクエスト）
- 1 件をレビューするたびに、change-watch が実行の前後に MR を取り直す 2 回と、レビュー中のコミットの追加を確かめる 1 回。
  ほかに kiro-cli が使うコマンドが、MR・スレッド・ノートを読み、指摘・返信・まとめを書く
- 差分・前回のコミットからの差分・ファイルの中身は、作業フォルダから git で読み、API には問い合わせない。MR の差分の一覧（API）は
  1 回のレビューで 1 回だけ取り、`comment`・`summary` は取ったものを使い回す
- git のリモートへは、MR の今のコミットを前に取ってきていないときだけ、その MR と対象ブランチを `git fetch` する（足りない分だけ）。
  リポジトリを丸ごと取り直すことはない。同じコミットのやり直しでは fetch もしない。取ってきたものは `refs/mr-review/` の下にだけ置き、
  リポジトリのブランチ・追跡ブランチは動かさない
- 作業フォルダが使えない（`MR_REVIEW_REPO_DIR` が空・git が無い・取ってこられない）ときは、差分もファイルも API から読む
- 取得が失敗したら 5 分（続けて失敗するほど倍、最大 1 時間）は問い合わせない

予定は change-watch の設定の取得元の `schedule`、ずらす幅は `jitter_sec` で変えられる。

## 1 セッションだけにする仕組み

kiro-cli は同時に 1 つだけ動く。change-watch の設定の `lock`（例は `agent-session`）が同じ受け渡し先どうしは、gitlab-issue-agent のワークフローと
またいでも同時に 1 つだけ実行し、空いていなければ次の起動でもう一度試す。1 件の上限は `timeout_sec`（例は 1800 秒）。

## 失敗したとき

kiro-cli が 0 以外で終わる・時間切れ・止められると失敗になり、5 分（1 回ごとに倍）たってから同じコミットでやり直す。3 回失敗したら打ち切り、
その MR に次のコミットが積まれたときにもう一度拾う。

| したいこと | コマンド |
|---|---|
| 失敗している・打ち切ったレビューを見る | `change-watch --config ~/.config/change-watch/change-watch.yaml events --rule mr-review --status send_failed,dead` |
| 打ち切ったレビューをすぐやり直す | `change-watch --config ~/.config/change-watch/change-watch.yaml retry <受け渡しの ID>` |
| レビューし終えた MR をもう一度レビューさせる | `change-watch --config ~/.config/change-watch/change-watch.yaml forget-handled --rule mr-review 12` と、MR のまとめのコメントを消す |

## kiro-cli とプロンプト

- kiro-cli は Dagu の `harness.run` で動かす（ワークフローの `handle`）。プロンプトは引数で渡し（`prompt: "${INPUT}"`）、
  `trust_all_tools: true`（`--trust-all-tools`）で確認なしにコマンドを実行させる。Dagu の `harness.run` が使えない版なら、
  `handle` のコメントにある `action: exec` で kiro-cli を直接呼ぶ
- プロンプトは `~/.config/dagu/gitlab-mr-review-agent.prompt.md`（change-watch の設定の `input_template`）。何を指摘するか・まとめに何を書くかは
  ここで変える。`{{ event.record.iid }}`・`{{ event.record.title }}`・`{{ event.record.web_url }}`・`{{ delivery.attempt }}`（何回目か）と、
  プラグインの `{{ event | gitlab_mr_since }}`（前回レビューしたコミット）・`{{ event | gitlab_mr_kind }}`（初めて / 再レビュー）が使える
- GitLab への読み書きは、プロンプトに書いた `gitlab_mr.py` のコマンドだけで行わせる

## kiro-cli が使うコマンド（gitlab_mr.py）

GitLab の URL・トークン・リポジトリは環境変数 `GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_PROJECT` から、手元のリポジトリとレビュー用の作業フォルダは
`MR_REVIEW_REPO_DIR`・`MR_REVIEW_WORKTREE` から読む（Dagu が `.env` から渡す）。git で取ってくる先は、既定でリポジトリのリモート `origin`
（その PC の git の認証のまま）。HTTPS で GitLab と同じホストのときだけ、トークンをその git にだけ渡す（リポジトリの設定ファイルには書かず、
ほかのホストには渡さない）。

| コマンド | すること |
|---|---|
| `prepare` | レビュー用の作業フォルダを作る。リポジトリが無ければ 1 回だけ取ってくる（入れるとき） |
| `context <MR> [--since <コミット>]` | 作業フォルダを MR の今のコミットにし、MR の概要・自分が立てた未解決のスレッド（返信つき）と解決済みの指摘・差分を出す。`--since` があればそこからの差分（この MR が触っているファイルだけ）。差分の中身をレビューし終えていればそう出す |
| `show <MR> <パス> [--from N --to M]` | 今のコミットのファイルを行番号つきで出す（作業フォルダから） |
| `comment <MR> --path <パス> --line <行> --body-file <ファイル>` | 差分の行に指摘のスレッドを立てる。同じ指摘か、同じ行に未解決の指摘が既にあれば書かない |
| `resolve <MR> <スレッド> --body <文>` | 自分が立てたスレッドに返信して解決にする |
| `summary <MR> --body-file <ファイル>` | 今の差分のレビューのまとめを 1 回だけ書く（差分の中身の指紋を印に残す） |

`--body-file` の代わりに `--body <文>` でもよい。手で試すときは、環境変数を読み込んでから `python3 gitlab_mr.py context 12` のように呼ぶ。

## プラグイン（gitlab_mr.py）が足すもの

| 名前 | 種類 | すること |
|---|---|---|
| `gitlab_mr_since` | フィルタ | 前回レビューし終えたときの head のコミット（初めてなら空） |
| `gitlab_mr_kind` | フィルタ | 初めてのレビュー / 再レビュー |
| `gitlab_mr.changed_during_review` | チェック | レビューのあいだにコミット・ドラフト・対象ブランチが変わったか（`handled_before_if` に書く） |
| `gitlab_mr.already_reviewed` | チェック | 今の差分の中身を既にレビューし終えているか（`skip_if` に書く。真なら kiro-cli を起こさない） |

## Windows で使うとき

- `gitlab-mr-review-agent.env` の `MR_REVIEW_CLI` は `python C:/Users/<名前>/.local/share/gitlab-mr-review-agent/gitlab_mr.py` のように書く
- change-watch は `python install.py install` で入れる。ワークフローの `command` はその Python（`python`）にし、`args` の先頭を `change-watch.pyz` のフルパスにする
- `MR_REVIEW_REPO_DIR`・`MR_REVIEW_WORKTREE` と、ワークフローの `handle` の `working_dir` は `C:/Users/you/work/project` のように書く
- git は Git for Windows（2.31 以降）。長いパス（260 字を超える）と日本語のファイル名は、このツールが git に設定を渡して扱う。
  HTTPS のリモートは、その PC の資格情報マネージャーか、GitLab と同じホストならトークンで取る

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/dagu/gitlab-mr-review-agent.env` | GitLab の URL・リポジトリ・トークン・`gitlab_mr.py` の呼び方・手元のリポジトリ・レビュー用の作業フォルダ |
| `~/.github/connections.yaml` | GitLab の接続（値は上の環境変数から取る） |
| `~/.config/change-watch/change-watch.yaml` | 取得の予定・ずらし・対象の MR の絞り込み・再試行・実行の上限・`lock` |
| `~/.config/dagu/gitlab-mr-review-agent.prompt.md` | プロンプト（レビューの観点） |
| `~/.config/dagu/dags/change-watch-mr-review.yaml` | Dagu のワークフロー（kiro-cli のフラグ・作業フォルダは `handle`） |

## テスト

```bash
cd tools/gitlab-mr-review-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-mr-review-agent-design.md`](../../docs/designs/gitlab-mr-review-agent-design.md)
