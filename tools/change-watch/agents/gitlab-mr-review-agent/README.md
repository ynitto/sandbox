# gitlab-mr-review-agent

決めた GitLab のリポジトリのマージリクエスト（MR）を定期的に見に行き、**新しいコミットが積まれた MR** を kiro-cli に 1 件ずつレビューさせる。
kiro-cli は差分の行に指摘のスレッドを立て、次のコミットで直っていればそのスレッドに返信して**解決にする**。
指摘には証拠を添え、証拠の無い指摘は仮説として扱う。
**コミットが変わっていない MR は二度レビューしない**（コメント・返信・ラベルの変更では起こさない）。

起動・条件の分岐・kiro-cli の実行・再試行は [Dagu](https://github.com/dagu-org/dagu)（v2.25 以降。`harness.run` を使う）のワークフローが、
GitLab への問い合わせ（いつ取得するか・まとめる・失敗の待ち）と「前回レビューしてから新しいコミットが積まれた MR」を返すことは
[change-watch](../../README.md) が受け持つ（change-watch の README の「条件をワークフローで判定する」）。どの MR をレビューするかの条件は、
ワークフローに書いてある。このフォルダには、kiro-cli が GitLab に読み書きするコマンド `gitlab_mr.py` と、設定の例がある。

```
 Dagu: change-watch-mr-review（毎分起動。1 回の起動で 1 件をレビューする。GitLab への問い合わせは 15 分ごと）
   get      head のコミット・ドラフト・対象ブランチが前回から変わった MR を 1 件受け取る
   review   ドラフトでなければ → kiro-cli がレビューする（Dagu の harness.run）          ← 条件（Dagu の preconditions）
              context で前回の指摘と差分を読む（中身が前にレビューしたときと同じなら、そこで終える）
              → 直った指摘を resolve → 新しい問題を comment → summary
   commit   レビューし終えたと記録する（次は、この姿からの変化だけを受け取る）
```

## 入れ方

1. インストーラで入れる。change-watch と、kiro-cli が使うコマンド（`gitlab_mr.py`）・設定の雛形・Dagu のワークフローを、
   どのエージェントも同じ決まりの場所に置く（下の表。決まりは [change-watch の README](../../README.md) の「エージェントを入れる」）。
   ワークフローと設定のパスは、この PC のものにして置く。既にある設定とワークフローは上書きしないので、入れ直すとプログラムだけが新しくなる。

   ```bash
   python3 tools/change-watch/install.py install --agent gitlab-mr-review-agent
   ```

   | 置かれるもの | 場所 |
   |---|---|
   | プログラム | `~/.local/share/change-watch/agents/gitlab-mr-review-agent/`（Windows は `%LOCALAPPDATA%\Programs\change-watch\agents\gitlab-mr-review-agent\`） |
   | 設定（`change-watch.yaml`・`gitlab-mr-review-agent.env`） | `~/.config/change-watch/gitlab-mr-review-agent/` |
   | Dagu のワークフロー（`change-watch-mr-review.yaml`・IaC 検査の `change-watch-mr-iac.yaml`・整理の `change-watch-gitlab-mr-review-agent-maintenance.yaml`） | `~/.config/dagu/dags/` |
   | 状態 | `~/.local/state/change-watch/gitlab-mr-review-agent/` |

2. GitLab の URL・リポジトリ・トークンと手元のリポジトリを `~/.config/change-watch/gitlab-mr-review-agent/gitlab-mr-review-agent.env` に書く。
   トークンには `api` スコープが要り、**このトークンの持ち主が指摘の書き手**になる。スレッドを解決にするには、そのリポジトリで Developer 以上の権限が要る。
   `MR_REVIEW_REPO_DIR` は手元のリポジトリで、**既にある作業コピーを指定してよい**（その作業ツリー・ブランチ・書きかけの変更には触らない。
   無ければ 5 で 1 回だけ取ってくる）。`MR_REVIEW_WORKTREE` はレビュー用の作業フォルダで、そのリポジトリに `git worktree` で足す
   （レビューのたびに MR のコミットに切り替え、ほかのファイルは消すので、ほかの用途に使わない）。`MR_REVIEW_CLI` はインストーラが埋めてある。

3. `~/.config/dagu/dags/change-watch-mr-review.yaml` の `review` の作業フォルダ（`working_dir`）を、2 の `MR_REVIEW_WORKTREE` と同じにする。
   レビューする MR を絞るなら、`~/.config/change-watch/gitlab-mr-review-agent/change-watch.yaml` の取得元の `query`（例: `target_branch: main`）か、
   ワークフローの `review` の `preconditions`（下の「条件で分ける」）に書く。
   kiro-cli はこの PC に入れて、ログインしておく。

4. 接続設定（例: `~/.github/connections.yaml`）に GitLab を書く。値は `gitlab-mr-review-agent.env` から取るので、トークンを 2 か所に書かなくてよい。

   ```yaml
   gitlab:
     - label: default
       url: ${GITLAB_URL}
       token: ${GITLAB_TOKEN}
   ```

5. 環境変数を読み込んで、`prepare` でレビュー用の作業フォルダを作る（リポジトリが無ければ、ここで 1 回だけ取ってくる。ファイルの中身は
   要るときに取るので、履歴の大きなリポジトリでも軽い）。git（2.31 以降）が要る。

   ```bash
   set -a; . ~/.config/change-watch/gitlab-mr-review-agent/gitlab-mr-review-agent.env; set +a
   ```

   ```bash
   change-watch --plugin gitlab_mr mr prepare
   ```

6. Dagu のスケジューラを動かす（`dagu start-all` など）。Dagu の設定（`config.yaml`）に足すものは無い。ワークフローの `get` は `--initial emit` なので、
   **最初の取得で、いま開いている MR もレビューする**（1 分に 1 件ずつ）。これから変わる MR だけにしたいなら、動かす前に `--initial baseline` にする。

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
| マージ・クローズされた | しない（開いている MR の一覧から消え、条件に合わない） |

- レビューし終えた MR の姿（head のコミットなど）は change-watch がこのワークフロー（`--consumer mr-review`）ごとに覚える。
  ワークフローの `get` の `--watch`（`sha`・`draft`・`target_branch`）が変わらないあいだは、何度取得しても流さない
- 「レビューし終えたか」は、MR の差分の中身（ファイルの名前と、足した行・消した行）でも見る。中身が同じなら、head のコミットが
  変わっていても（リベース・マージトレイン）、`context` が「レビューし終えています」と出し、kiro-cli は何もせずに終える。
  GitLab が中身を返さないほど大きなファイルがある MR は、中身を比べられないので、リベースでもレビューする
- コミットを続けて積んでも、レビューは取得（15 分に 1 回）ごとに最新のコミットで 1 回にまとまる
- レビューのあいだにコミットが積まれたら、次の取得でその MR を新しい変化として受け取り、もう一度レビューする
- 失敗したときのやり直しでも、指摘は重ならない（同じ指摘・同じコミットのまとめは書かない）。今のコミットのまとめが既にあれば、
  kiro-cli は何もせずに終える

## 条件で分ける

ワークフローの `review` は、`get` が出した変化（`CHANGE`）を Dagu の `preconditions` で見て、合ったときだけ動く。
例の条件は「新しく現れたか変わった MR（`${CHANGE.kind}` が `appeared` か `changed`）で、ドラフトでない（`${CHANGE.record.draft}` が `false`）」。
対象ブランチで絞るなら `condition: "${CHANGE.record.target_branch}"`・`expected: "main"` を足す。条件に合わなかった変化も `commit` する
（`continue_on.skipped`）ので、同じ変化が何度も来ることはない。ドラフトのあいだに積まれたコミットは、ドラフトを外したときに
前回レビューしたコミット（`${CHANGE.before.sha}`。ドラフトのあいだの姿）からの差分としてレビューする。

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
- 1 件をレビューするたびに、kiro-cli が使うコマンドが、MR・スレッド・ノートを読み、指摘・返信・まとめを書く
- 差分・前回のコミットからの差分・ファイルの中身は、作業フォルダから git で読み、API には問い合わせない。MR の差分の一覧（API）は
  1 回のレビューで 1 回だけ取り、`comment`・`summary` は取ったものを使い回す
- git のリモートへは、MR の今のコミットを前に取ってきていないときだけ、その MR と対象ブランチを `git fetch` する（足りない分だけ）。
  リポジトリを丸ごと取り直すことはない。同じコミットのやり直しでは fetch もしない。取ってきたものは `refs/mr-review/` の下にだけ置き、
  リポジトリのブランチ・追跡ブランチは動かさない
- 作業フォルダが使えない（`MR_REVIEW_REPO_DIR` が空・git が無い・取ってこられない）ときは、差分もファイルも API から読む
- 取得が失敗したら 5 分（続けて失敗するほど倍、最大 1 時間）は問い合わせない。そのあいだは前回までの取得結果で答える

予定は change-watch の設定の取得元の `schedule`、ずらす幅は `jitter_sec` で変えられる。

## 1 セッションだけにする仕組み

kiro-cli は同時に 1 つだけ動く。ワークフローは 1 回の起動で 1 件だけ処理し、同じワークフローが重ねて起動されることはない（Dagu の既定）。
gitlab-issue-agent のワークフローとも同時に動かさないなら、Dagu のキュー（`queue`）で同じキューに入れ、キューの並列数を 1 にする。
1 件の上限は `review` の `timeout_sec`（例は 1800 秒）。

## 失敗したとき

kiro-cli が 0 以外で終わる・時間切れになると、Dagu が `retry_policy` のとおり 300 秒おいて 2 回までやり直す。尽きたら失敗として Dagu の
実行履歴に残り、`commit` はする（同じコミットではやり直さない。その MR に次のコミットが積まれたときにもう一度拾う）。
すぐやり直したいときは、Dagu の画面から `review` のステップを再実行する。

## kiro-cli とプロンプト

- kiro-cli は Dagu の `harness.run` で動かす（ワークフローの `review`）。プロンプトは引数で渡し、
  `trust_all_tools: true`（`--trust-all-tools`）で確認なしにコマンドを実行させる
- プロンプトはワークフローの `review` の `prompt`。何を指摘するか・まとめに何を書くかはここで変える。`${CHANGE.record.iid}`・`${CHANGE.record.title}`・
  `${CHANGE.record.web_url}` と、前回レビューしたコミット `${CHANGE.before.sha}`（初めてなら空）が使える
- GitLab への読み書きは、プロンプトに書いたコマンド（`.env` の `MR_REVIEW_CLI`）だけで行わせる
- 指摘の検証と証拠の添付は「指摘と証拠の規約」に従い、まとめには検証済みと仮説の件数を分けて書かせる

## kiro-cli が使うコマンド

コマンドは change-watch から `change-watch --plugin gitlab_mr mr <コマンド> ...` で呼ぶ（`.env` の `MR_REVIEW_CLI` がこの形。
change-watch の設定は読まないので、`--config`・`--connections` は付けなくてよい）。

GitLab の URL・トークン・リポジトリは環境変数 `GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_PROJECT` から、手元のリポジトリとレビュー用の作業フォルダは
`MR_REVIEW_REPO_DIR`・`MR_REVIEW_WORKTREE` から読む（Dagu が `.env` から渡す）。git で取ってくる先は、既定でリポジトリのリモート `origin`
（その PC の git の認証のまま）。HTTPS で GitLab と同じホストのときだけ、トークンをその git にだけ渡す（リポジトリの設定ファイルには書かず、
ほかのホストには渡さない）。

| コマンド | すること |
|---|---|
| `prepare` | レビュー用の作業フォルダを作る。リポジトリが無ければ 1 回だけ取ってくる（入れるとき） |
| `context <MR> [--since <コミット>]` | 作業フォルダを MR の今のコミットにし、MR の概要・自分が立てた未解決のスレッド（返信つき）と解決済みの指摘・差分を出す。`--since` があればそこからの差分（この MR が触っているファイルだけ）。差分の中身をレビューし終えていればそう出す |
| `show <MR> <パス> [--from N --to M]` | 今のコミットのファイルを行番号つきで出す（作業フォルダから） |
| `verify <MR> -- <コマンド...>` | 作業フォルダで許可された検証コマンドを実行し、コマンド・終了コード・出力の末尾を証拠用の Markdown で出す |
| `change-watch --plugin gitlab_proof proof <MR> --findings <ファイルまたは -> [--evidence-dir <フォルダ>]` | 新しい指摘を別セッションで検証し、投稿する指摘と取り下げる指摘を JSON で返す |
| `comment <MR> --path <パス> --line <行> --body-file <ファイル> [--evidence-file <ファイル>]` | 差分の行に指摘のスレッドを立てる。同じ指摘か、同じ行に未解決の指摘が既にあれば書かない |
| `resolve <MR> <スレッド> --body <文>` | 自分が立てたスレッドに返信して解決にする |
| `summary <MR> --body-file <ファイル>` | 今の差分のレビューのまとめを 1 回だけ書く（差分の中身の指紋を印に残す） |

`--body-file` の代わりに `--body <文>` でもよい。手で試すときは、環境変数を読み込んでから `change-watch --plugin gitlab_mr mr context 12` のように呼ぶ。
以前の `.env` の `MR_REVIEW_CLI=python3 <gitlab_mr.py>`（直接呼ぶ形）も、そのまま動く。

## 指摘と証拠の規約

検証済みの指摘には具体的な再現手順や実行結果を添える。再現手順を添えるときは本文に具体的に書き、証拠は
`comment --evidence <文>` または `--evidence-file <ファイル>` で渡す。実行結果は `verify` の標準出力をファイルに保存し、
`--evidence-file` に渡せる。証拠はコメントの「証拠」節に入り、HTML コメントの印に `evidence="verified"` が付く。
この印は再現手順または実行結果が添付されたことを表す。手作業で確認した証拠にも同じ印が付く。
証拠が無い指摘には注意書きと `evidence="hypothesis"` の印が付き、仮説として扱う。`context` の未解決スレッドの見出しには
それぞれ「〔検証済み〕」「〔仮説（未検証）〕」が付く（以前の印に証拠の属性が無い場合も仮説）。

`verify` を使うには `MR_REVIEW_VERIFY_ALLOW` に許可するコマンドの引数の接頭辞を JSON の配列で設定する。
許可リストは接頭辞一致で、接頭辞の後ろには引数を足せる。これは実行の入口を制限するもので、サンドボックスではない。
信頼できるコマンドだけを許可リストに書く。
`context` で取り出した作業フォルダの HEAD が、手元に記録した MR のコミットと一致するときだけ、シェルを介さずに実行し、
最小限の環境変数だけを引き継ぐ。`GITLAB_TOKEN` などのトークン類は検証コマンドに渡さない。制限時間は `MR_REVIEW_VERIFY_TIMEOUT_SEC` で指定する（既定 120 秒、1〜600 秒）。
制限時間内にコマンドが終われば、終了コードが 0 以外でも結果を証拠に出す。
時間切れは未検証（仮説）として扱い、部分出力は証拠に出さない。
設定が無い、許可リストに無いなどで検証できないときは、無理に実行せず、本文に理由を添えて仮説として投稿する。

### 独立検証（proof）

`mr verify` ではレビュー担当が許可されたコマンドを実行する。`proof` では別セッションの検証エージェントに MR の説明・差分と指摘の場所・主張を渡し、判定と証拠を受け取る。投稿は `mr comment` が受け持つ。

先に `mr context` を実行し、作業フォルダを MR の今のコミットにする。`--findings` に渡す UTF-8 の JSON は次の形にする。ファイルと `--evidence-dir` は作業フォルダの外に置く。標準入力から読む場合は `--findings -` を使う。

```json
{"version": 1, "findings": [{"id": "f1", "target": {"path": "app.py", "line": 2}, "claim": "x が None のとき TypeError になります。"}]}
```

指摘に `self_assessment` を書けば、レビュー担当自身の推論や証拠を手元に残せる。検証セッションには `id`・場所・主張だけが届く。

- `post` の指摘は `claim` を本文として投稿する。裏付けありの `evidence_file` を `comment --evidence-file` に渡すと、証拠節に「独立検証（proof）: 裏付けあり」が入り、`context` に「〔検証済み〕」と出る。
- 判断不能や検証の失敗は、証拠なしで投稿すると仮説になる。`mr verify` で取った証拠があれば使える。
- `drop` の指摘は投稿せず、まとめに取り下げた件数を書く。

Dagu で使うには、環境ファイルの `MR_REVIEW_PROOF_CLI` と `MR_REVIEW_PROOF_AGENT` を設定する。使用する CLI の無人実行フラグは導入先で確認する。
[独立検証の例](examples/change-watch-mr-review-proof.yaml.example) の `/home/you` などを自分の環境に合わせ、既存の
`~/.config/dagu/dags/change-watch-mr-review.yaml` を**置き換える**。既定の例と並べると、同じ受け手 `mr-review` で同じ MR を取り合う。
レビューの制限時間は 3600 秒で、その内側で検証の制限時間（既定 900 秒）がかかる。

### IaC リスク検査（iac）

`iac` は GitLab API から MR 全体の差分を読み、LLM を使わずに追加行を検査する。既存の
`GITLAB_URL`・`GITLAB_TOKEN`・`GITLAB_PROJECT` を使う（`--project` でプロジェクトを上書きできる）。

```sh
change-watch --plugin gitlab_iac iac check 123 --format json --output iac.json
change-watch --plugin gitlab_iac iac check 123 --format markdown
change-watch --plugin gitlab_iac iac post 123
```

`check` の既定出力は JSON、`post` は Markdown。どちらも `--format json|markdown` で選び、
`--output` を省略すると標準出力に書く。指摘にはファイル、MR 新側の行番号、ルール、重大度、判定に使ったリテラルやプロパティ名が入る。

| 形式 | 対象ファイル |
|---|---|
| Terraform | `.tf` |
| Bicep | `.bicep` |
| ARM | `.json` のうち `$schema` の値に `deploymentTemplate` を含むもの、または `azuredeploy.json`、`arm-template.json`、`deployment-template.json` 系 |
| CloudFormation | `.yaml` / `.yml` のうち `AWSTemplateFormatVersion` キーを含むもの、または `cloudformation` / `cfn` ディレクトリ配下や同名のファイル（例 `cfn-prod.yml`） |

ファイル名・パスは大文字小文字を区別しない。ARM のファイル名では単語間の区切りに `-`・`_`・`.` を使え、
環境名などの接尾辞も指定できる。名前で分類できず差分から識別子を確認できない JSON / YAML は、MR の head のファイルを参照する。

| ルール | 重大度 | 判定 |
|---|---|---|
| `open-ingress` | high | `cidr_blocks`、`CidrIp`、`sourceAddressPrefix` などに `0.0.0.0/0` または `::/0` が追加された場合。近くに送信方向の指定や Bicep / ARM の `Deny` が見える場合は除外 |
| `public-storage` | high | `allowBlobPublicAccess: true`、公開 ACL、パブリックアクセスのブロックを無効にする `BlockPublicPolicy: false` など |
| `iam-wildcard` | high | `Action` / `Resource` などの値にワイルドカードがある場合。文字列、インライン配列、YAML ブロックリストを確認 |
| `iam-admin` | high | AWS の `AdministratorAccess` 名・ARN、Azure の Owner ロール ID |
| `network-change` | low | 差分の前後行や hunk のヘッダーにセキュリティグループ、NSG、ファイアウォール、VPC/VNet、サブネットなどのリソース宣言があり、その hunk に追加行がある場合。high の指摘がある hunk では省略 |

決定的な正規表現と簡易走査なので、変数や補間、ポリシーの Effect、テンプレートの展開、実効権限などの意味解析は行わない。
hunk の外にある設定によっては検出漏れや過検出が起こる。削除行だけの変更は検査対象外で、GitLab が差分を省略した対象ファイルは
`skipped_files` に出す。

`post` は既定で dry-run。検出結果を表示し、`MR_REVIEW_IAC_POST=1` のときだけ、
検出がある MR に `MR_REVIEW_IAC_LABEL`（既定 `needs-extra-approval`）を追加してから警告コメントを投稿する。
同じ対象差分とルールの指紋が既存コメントの識別行にあれば両方をスキップする。同時実行では、両方がコメントを投稿する可能性がある。
コメント投稿とラベル付けまでをこのツールが担当する。**マージをブロックするには、GitLab 側でそのラベルを条件とする承認ルールを設定する。**
ラベルの取り外しは運用側で行う。

インストーラが置く [独立 Dagu ワークフロー](change-watch-mr-iac.yaml.example) は、
`dagu start change-watch-mr-iac -- IID=123` で実行できる。実投稿する場合はワークフローが読む環境ファイルで
`MR_REVIEW_IAC_POST=1` を設定する。

## Windows で使うとき

- インストーラは `python tools\change-watch\install.py install --agent gitlab-mr-review-agent` で、Dagu から使う Python で動かす。
  ワークフローの `command`（その Python と `change-watch.pyz`）と `.env` の `MR_REVIEW_CLI` は、インストーラが Windows の形にして置く
- `MR_REVIEW_REPO_DIR`・`MR_REVIEW_WORKTREE` と、ワークフローの `review` の `working_dir` は `C:/Users/you/work/project` のように書く
- git は Git for Windows（2.31 以降）。長いパス（260 字を超える）と日本語のファイル名は、このツールが git に設定を渡して扱う。
  HTTPS のリモートは、その PC の資格情報マネージャーか、GitLab と同じホストならトークンで取る

## 設定の置き場所

| ファイル | 書くこと |
|---|---|
| `~/.config/change-watch/gitlab-mr-review-agent/gitlab-mr-review-agent.env` | GitLab の URL・リポジトリ・トークン・`gitlab_mr.py` の呼び方・手元のリポジトリ・レビュー用の作業フォルダ |
| `~/.github/connections.yaml` | GitLab の接続（値は上の環境変数から取る） |
| `~/.config/change-watch/gitlab-mr-review-agent/change-watch.yaml` | 取得する MR の絞り込み（`query`）・取得の予定・ずらし・問い合わせの間隔 |
| `~/.config/dagu/dags/change-watch-mr-review.yaml` | 見る項目（`--watch`）・条件（`preconditions`）・プロンプト（レビューの観点）・kiro-cli のフラグ・作業フォルダ・再試行 |

## テスト

```bash
cd tools/change-watch/agents/gitlab-mr-review-agent && python3 -m unittest discover -s test
```

設計: [`docs/designs/gitlab-mr-review-agent-design.md`](../../../../docs/designs/gitlab-mr-review-agent-design.md)
