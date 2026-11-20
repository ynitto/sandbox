# gitlab-proof-agent（独立検証 `proof`）設計

gitlab-mr-review-agent のレビュー担当のエージェントが見つけた指摘を、**別セッションの検証エージェント**に確かめさせてから投稿する。
レビュー担当の自己評価（確信度・推論・作業ログ）だけで「検証済み」にしない、という GitHub Blog
「Build once, run on any agent: A practical guide to agent skills」の Proof Agent の考え方を、change-watch のプラグインとして組み込む。
実装は `tools/change-watch/agents/gitlab-mr-review-agent/gitlab_proof.py` にある。
前提になる仕組みは [gitlab-mr-review-agent 設計](./gitlab-mr-review-agent-design.md) と、同エージェントの README の「指摘と証拠の規約」（#1046）。

要件:

1. 検証エージェントには「元の依頼」（MR のタイトル・説明・URL・差分）と「検証する指摘」（場所と主張）だけを渡す。
   レビュー担当の自己評価・推論・作業ログ・スコアは渡さない（**独立性。最重要**）
2. 検証エージェントは外部コマンドとして起動し、制限時間を必ず掛ける。起動できない・時間切れ・出力が読めないときは
   全指摘を「判断不能」にし、ワークフローは止めない
3. 結果は指摘ごとの判定（裏付けあり / 反証あり / 判断不能）と証拠で、既存の証拠規約（`evidence="verified"` / `"hypothesis"`）にそのまま繋がる

## 1. 位置づけと分担

| 受け持ち | どこ | 何を |
|---|---|---|
| どの MR をいつレビューするか・再試行・時間の上限 | Dagu と change-watch（今のまま） | `get` → `review` → `commit` |
| 指摘を見つける・GitLab に書く | レビュー担当（kiro-cli）と `gitlab_mr.py` の `mr`（今のまま） | `context` / `show` / `verify` / `comment` / `resolve` / `summary` |
| **指摘を第三者に確かめさせる** | `gitlab_proof.py`（プラグインが足すコマンド `proof`） | 検証の入力を組み立てる → 検証エージェントを起動する → 判定を読んで決まった形の JSON で返す |
| 指摘を確かめる | 検証エージェント（別セッションのエージェント CLI。Copilot / Kiro CLI など） | 作業フォルダでコードを読み・実行し、指摘ごとに判定と証拠を書く |

`proof` は GitLab に**書かない**。投稿は今までどおり `mr comment` が行い、`proof` はその手前で「証拠つきで投稿する / 仮説として投稿する / 投稿しない」
を分けて返すだけにする。印の付け方・重複の防止は `gitlab_mr.py` の 1 か所のままにできる。

### 1.1 `verify` と `proof` の違い

| | `mr verify`（#1046） | `proof`（この設計） |
|---|---|---|
| 誰が確かめるか | レビュー担当が自分で（自分の指摘を自分で検証） | 別セッションの検証エージェント（第三者の検証） |
| 何を実行するか | 許可リストにある 1 つのコマンド | エージェント CLI を 1 回（中で何を実行するかは検証エージェントが決める） |
| 出すもの | コマンド・終了コード・出力の末尾（機械が作る証拠） | 指摘ごとの判定と、検証エージェントが書いた証拠（Markdown） |
| 判定 | しない（出力をどう読むかはレビュー担当） | する（裏付けあり / 反証あり / 判断不能） |
| 失敗したとき | 0 以外で終わる。指摘は仮説になる | 0 で終わる。全指摘が判断不能＝仮説になる |
| 印 | 証拠を `comment` に渡せば `verified` | 同じ（裏付けありの証拠を `comment` に渡せば `verified`） |

- どちらも**印の意味を変えない**。`evidence="verified"` は今までどおり「再現手順か実行結果が添付された」ことだけを表す。
  誰が検証したかは印ではなく、証拠の先頭の 1 行（§6.2）で読めるようにする（印の属性を増やすと `gitlab_mr.py` と過去の印の読み方を変えることになる）
- 併用できる。`verify` の証拠を持つ指摘も `proof` に掛けてよいが、**`verify` の出力は `proof` の入力に入れない**（自己評価の側。§3）。
  `proof` が判断不能でも、レビュー担当が `verify` の証拠を持っていれば、それを `comment --evidence-file` に渡してよい（今までの規約のまま）
- `proof` が反証ありとした指摘は、`verify` の証拠があっても投稿しない（第三者の反証を優先する。§6）

## 2. 配置

**`agents/gitlab-mr-review-agent/gitlab_proof.py` に置く**（第一候補のとおり）。コマンドは `proof`。

```
change-watch --plugin gitlab_proof proof <MR> --findings <ファイル> [--evidence-dir <フォルダ>]
```

| 確かめたこと | 根拠 |
|---|---|
| `--plugin gitlab_proof` で名前から見つかる | `changewatch/plugins/__init__.py` の `resolve` は `agents/<エージェント>/<名前>.py` を探す。`gitlab_proof.py` は 1 つだけなので衝突しない |
| `install --agent gitlab-mr-review-agent` でいっしょに入る | `install.py` の `agent_files` はエージェントのフォルダ直下の `*.py` をすべてプログラムとして写す。インストーラは変えなくてよい |
| 既存のインストールの試験が通る | `test_install.py` は `agent_files` が返すものを比べるだけで、ファイル名を決め打ちしていない |
| `mr` と同時に読み込める | `--plugin gitlab_mr --plugin gitlab_proof` と並べられる（`load` は一覧を受ける）。コマンド名 `mr` と `proof` は重ならない |

`gitlab_mr.py` と同じく `register(api)`（`add_command("proof", …, needs_config=False)`。change-watch の設定は読まない）と、
`python3 gitlab_proof.py …` で直接呼ぶ `main` を持つ。

MR の取得（`GitLab`）・手元のリポジトリ（`LocalRepo`）・作業フォルダが今のコミットかの確認は `gitlab_mr.py` のものを使う。
`gitlab_proof.py` は隣の `gitlab_mr.py` を `Path(__file__).with_name("gitlab_mr.py")` から `importlib` で読み込む
（プラグインは `sys.path` に載らないので `import gitlab_mr` とは書けない。インストール後も 2 つは必ず同じフォルダにある）。

採らなかった置き場所:

- **`gitlab_mr.py` の中に `mr proof` として足す**: コマンド名が `proof` にならない。入力の組み立てを 1 か所に閉じたことを
  試験で確かめたい（§3）ので、自己評価に触れないファイルとして分けておくほうが見通しがよい。`gitlab_mr.py` は既に 1000 行ある
- **新しいエージェント `agents/gitlab-proof-agent/`**: インストーラは `change-watch.yaml.example` を持つフォルダをエージェントと数えるので、
  取得元を持たないのに設定の雛形が要る。`.env`・作業フォルダは gitlab-mr-review-agent のものを使うので、別に入れる意味がない
- **本体の `changewatch/plugins/` に同梱する**: GitLab の MR に固有で、本体は GitLab を知らない

## 3. 入力の分離（独立性の強制）

守ることは 1 つ: **検証エージェントに届くものは `build_verifier_input()` が返す文字列だけ**にする。

### 3.1 型で分ける

```
指摘のファイル ──read_claims()──▶ list[Claim] ─┐
                （許可した項目だけを拾う）        ├─▶ build_verifier_input(request, claims, reply_path) ─▶ 検証の入力（文字列）
GitLab と手元の git ──fetch_request()──▶ Request ─┘
```

| 名前 | 中身 | 決まり |
|---|---|---|
| `Claim` | `id`・`path`・`line`・`claim` の 4 つだけを持つ、変えられないデータ | 自己評価を入れる場所が**型に無い** |
| `Request` | MR の `title`・`description`・`web_url`・ブランチ・`base` / `head` のコミット・`diff` | `proof` が GitLab と手元の git から**自分で**取る。レビュー担当が書いたファイルからは取らない |
| `read_claims(raw)` | 指摘のファイルから `Claim` を作る | **許可した項目だけを拾う**（`id`・`target.path`・`target.line`・`claim`）。ほかの項目は `self_assessment` も、知らない名前の項目も、読んだ時点で捨てる。「自己評価を除く」のではなく「主張だけを拾う」ので、項目名を変えて紛れ込ませることもできない |
| `build_verifier_input(request, claims, reply_path)` | 検証の入力の文字列 | 引数はこの 3 つだけ。環境変数・ファイル・`args` を読まない。入力を作るのはこの関数だけ |

`read_claims` を出たあと、プログラムのどこにも自己評価は残らない（出力の JSON にも載せない。呼び出し側は `id` で自分の持っているものと突き合わせる）。

### 3.2 関数の外から漏れる道をふさぐ

入力の文字列が正しくても、検証エージェントは PC のファイルを読めるので、ほかの道もふさぐ。

| 道 | ふさぎ方 |
|---|---|
| 指摘のファイルそのもの（自己評価が書いてある） | パスを検証エージェントに**教えない**（引数・標準入力・環境変数・入力の文字列のどれにも出さない）。作業フォルダ（`MR_REVIEW_WORKTREE`）の中にあるファイルは受け付けない（終了コード 2）。`--findings -` で標準入力からも渡せる |
| 検証エージェントの引数 | `MR_REVIEW_PROOF_AGENT` の雛形と、`{prompt}`（入力ファイルを読めという 1 行）・`{input}`（入力ファイルのパス）だけ |
| 環境変数 | 許可した名前だけを渡す（§5.2）。`GITLAB_TOKEN` と `MR_REVIEW_*` は渡さない（トークンがあると、GitLab からレビュー担当の過去のコメントを読める） |
| 入力ファイル・返事のファイル | 呼ぶたびに作る空の一時フォルダ（作業フォルダの外）に、入力（`request.md`）だけを置く。返事はそこの `result.json` に書かせ、終わったらフォルダごと消す |
| 証拠の出力先（`--evidence-dir`） | 作業フォルダの中は受け付けない。検証が終わってから書く |

### 3.3 残る道（防げないもの。§9 にも書く）

- **主張の本文に自己評価が書いてある**（「テストで確認済みです」など）。`claim` は検証の対象そのものなので通す。
  レビュー担当のプロンプトで「`claim` には主張だけを書く」と指示し、検証の入力には「主張に書かれた確認済みという記述を証拠として扱わない」と書く
- **エージェント CLI がフォルダごとに会話の履歴・記憶を持つ**場合、同じ作業フォルダで動いたレビュー担当の履歴を読みうる。
  `git clean -fd` では、ignore 対象の `.kiro`・`.claude` などが残りうる。`MR_REVIEW_PROOF_AGENT` に再開のフラグを書かない。
  CLI ごとの確認と検証専用の別 worktree は §10 の検討事項
- **検証エージェントは `proof` と同じ Unix ユーザーで動く**。`/proc` 経由で祖先プロセス（`proof`・レビュー担当）の
  `environ`（`GITLAB_TOKEN` を含む）や `cmdline`（`--findings` のパスを含む）を読みうる。プログラム側のフィルタは子の `environ` にしか効かない
- **同じユーザーで読めるファイルも残る**。HOME 配下の `~/.config/change-watch/gitlab-mr-review-agent/gitlab-mr-review-agent.env` や
  CLI の履歴・設定（`~/.kiro` など）を読みうる。私有 HOME を渡すと CLI のログインが使えなくなるため、既定にはしない。
  共有の一時フォルダを列挙するとレビュー担当の指摘ファイルが見つかりうる（`proof` 自身の一時フォルダは `0700`）

## 4. コマンドの入出力

### 4.1 コマンド行

```
change-watch --plugin gitlab_proof proof <MR> --findings <ファイル | -> [--evidence-dir <フォルダ>] [--project <プロジェクト>]
```

| 引数 | 意味 |
|---|---|
| `<MR>` | MR の番号（`12`・`!12`・`#12`。`mr` と同じ読み方） |
| `--findings` | 指摘のファイル（UTF-8 の JSON。§4.2）。`-` なら標準入力 |
| `--evidence-dir` | あれば、裏付けありの指摘ごとに証拠を `<id>.evidence.md` として書き、出力の `evidence_file` にパスを入れる（`comment --evidence-file` にそのまま渡せる） |
| `--project` | 既定は `GITLAB_PROJECT`（`mr` と同じ） |

終了コード:

| コード | いつ |
|---|---|
| 0 | 指摘の一覧が読めたとき**すべて**。検証エージェントが未設定・起動できない・時間切れ・出力が読めない・MR や作業フォルダが使えない、を含む（全指摘が判断不能になる） |
| 2 | 呼び方の誤り: 引数の誤り、`--findings` が読めない・JSON でない・`findings` が配列でない、指摘のファイルや `--evidence-dir` が作業フォルダの中 |

2 は「何を検証するかが分からない」ときだけで、判定を返す相手の一覧が無いので 0 にはできない。標準エラーに理由を 1 行出す。
標準出力は結果の JSON だけにし（§4.3）、経過は標準エラーに出す。

### 4.2 指摘のファイル（入力）

```json
{
  "version": 1,
  "findings": [
    {
      "id": "f1",
      "target": {"path": "app.py", "line": 2},
      "claim": "x が None のとき `x * 2` で TypeError になります。",
      "self_assessment": {
        "confidence": 0.9,
        "reasoning": "呼び出し元の handler.py で None が渡る経路がある…",
        "evidence": "mr verify の出力…",
        "log": "…"
      }
    }
  ]
}
```

| 項目 | 必須 | 使い方 |
|---|---|---|
| `id` | ○ | 結果との突き合わせ。ファイルの中で重ならない文字列（英数字・`-`・`_`、64 字まで。`--evidence-dir` のファイル名にもなる） |
| `target.path` / `target.line` | ○ | 指摘の場所（新しい側のパスと行番号。`mr comment` の `--path` / `--line` と同じ） |
| `claim` | ○ | 主張の本文（Markdown）。`mr comment` の本文にするものと同じ文。**主張だけ**を書く |
| `self_assessment` | — | レビュー担当の自己評価（確信度・推論・自分で取った証拠・ログ。形は自由）。**`proof` は読まない** |
| そのほかの項目 | — | 読まない（捨てる） |

- `claim` と `self_assessment` を別の項目にしておくことで、「検証エージェントに渡すもの」と「渡さないもの」がデータの形で分かれる。
  レビュー担当が自分用の覚え書きを 1 つのファイルに持てるように `self_assessment` を許しているが、書かなくてよい
- 必須の項目が欠けた・型が違う・`id` が重なる指摘は、検証に回さず判断不能（`reason: invalid_finding`）として結果に載せる（ほかの指摘は検証する）
- 一度に検証するのは `MR_REVIEW_PROOF_MAX_FINDINGS` 件（既定 20）まで。超えた分は判断不能（`reason: not_checked`）
- `findings` が空なら、検証エージェントを起動せずに空の結果を返す（0）

### 4.3 結果（標準出力の JSON）

```json
{
  "version": 1,
  "mr": {"iid": "12", "head": "aaaaaaaa…"},
  "verifier": {"status": "ok", "detail": "", "elapsed_sec": 212, "exit_code": 0},
  "counts": {"supported": 1, "refuted": 1, "inconclusive": 1},
  "post": [
    {"id": "f1", "path": "app.py", "line": 2, "claim": "x が None のとき…",
     "verdict": "supported", "evidence": "独立検証（proof）: 裏付けあり\n\n- 手順: …\n- 結果: …",
     "evidence_file": "/tmp/mr-12/f1.evidence.md", "detail": "", "reason": ""},
    {"id": "f3", "path": "util.py", "line": 40, "claim": "…",
     "verdict": "inconclusive", "evidence": "", "detail": "再現に必要なサービスが起動できなかった", "reason": "verifier"}
  ],
  "drop": [
    {"id": "f2", "path": "app.py", "line": 9, "claim": "…",
     "verdict": "refuted", "evidence": "", "detail": "呼び出し元は 3 か所とも None を弾いている（handler.py:12 …）", "reason": ""}
  ]
}
```

| 項目 | 中身 |
|---|---|
| `post` | 投稿してよい指摘（裏付けあり・判断不能）。入力の順 |
| `drop` | 投稿すべきでない指摘（反証あり）。呼び出し側はこの配列を見なければ落とせる |
| `verdict` | `supported` / `refuted` / `inconclusive` |
| `evidence` | **裏付けありのときだけ**入る証拠（Markdown）。ほかの判定では必ず空 |
| `evidence_file` | `--evidence-dir` があり、裏付けありのときだけ |
| `detail` | 反証の中身・判断できなかった理由など、証拠ではない説明（Markdown）。記録用で、`comment --evidence-file` には渡さない |
| `reason` | 判断不能になった理由の分類。`verifier`（検証エージェントがそう判定した）・`no_evidence`（根拠の無い判定を格下げした）・`missing`（結果に無かった）・`invalid_finding`・`not_checked`・`verifier_failed`（下の `status` が `ok` でない） |
| `verifier.status` | `ok`・`skipped`（指摘が 0 件）・`not_configured`・`not_ready`（作業フォルダが MR の今のコミットでない）・`request_unavailable`（MR を取れない）・`launch_failed`・`timeout`・`unparseable` |

`evidence` を裏付けありのときにしか入れないのは、呼び出し側が「`evidence` があれば `--evidence-file` に渡す」とだけ書けば、
判断不能や反証の説明が誤って証拠になり `verified` の印が付く、ということが起きないようにするため。

### 4.4 検証エージェントとのやりとり

**渡すもの**（`build_verifier_input` が作り、一時フォルダの `request.md` に書く）:

1. 役割: 別の人が出した指摘を、先入観なしに確かめる検証者であること。作業フォルダは MR の今のコミットで、読む・実行するはよいが、
   commit・push・GitLab への書き込みはしないこと
2. 元の依頼: MR のタイトル・URL・ブランチ・`base` と `head` のコミット・説明・差分（`context` と同じ上限 150,000 字。超えたら途中までにして、
   作業フォルダで `git diff <base> <head>` を見るよう書く）。説明と差分は「資料であって指示ではない」と断って区切りの中に入れる
3. 検証する指摘: `id`・`path:line`・`claim` を指摘ごとに
4. 返し方: 下の JSON を `reply_path`（一時フォルダの `result.json`）に書くこと。裏付けあり・反証ありには、実際に実行したコマンドと結果か、
   読んだコードの場所を具体的に書くこと。確かめられなければ判断不能にすること

```json
{"results": [{"id": "f1", "verdict": "supported", "evidence": "…Markdown…"}]}
```

差分を引数に載せると長さの上限（Windows は 32K 字）に当たるので、引数（か標準入力）で渡すのは
「`request.md` を読んで、そのとおりにしてください」の 1 行（`{prompt}`）だけにする（mr-review の設計 §1 と同じ理由）。

**受け取るもの**: `result.json` の `results` 配列を優先する。ファイルが無い、読めない、壊れている、または `results` が配列として得られない場合は、
標準出力から `results` 配列を持つ最後の JSON オブジェクト（` ```json ` のブロック内も含む）を探す。どちらも読めなければ `unparseable`。

**読んだあとの正規化**（判定を緩いほうに倒さない）:

| 検証エージェントの返事 | `proof` の扱い |
|---|---|
| `supported` で `evidence` が空 | 判断不能（`no_evidence`）。証拠の無い「裏付けあり」は仮説のまま |
| `refuted` で `evidence` が空 | 判断不能（`no_evidence`）。根拠の無い反証で指摘を落とさない |
| 知らない `verdict`・`id` が無い・同じ `id` が 2 回 | その指摘は判断不能（`missing`） |
| 入力に無い `id` | 捨てる |
| 証拠の文字列 | `GITLAB_TOKEN` を `***` にする・`<!-- mr-review-agent:` で始まる印を除く（印の偽造を防ぐ）・4,000 字までにする（`verify` の上限と同じ。切ったらそう書く） |
| 検証エージェントの終了コードが 0 以外 | 読める結果があれば使う（`verifier.exit_code` に残す）。無ければ `launch_failed` か `unparseable` |

## 5. 検証エージェントの呼び出し

### 5.1 環境変数

`gitlab-mr-review-agent.env` に書く（Dagu が `dotenv` で渡す）。名前は既存の `MR_REVIEW_*` に合わせる。

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `MR_REVIEW_PROOF_CLI` | （なし） | レビュー担当のプロンプトに埋める `proof` の呼び方。例: `/home/you/.local/bin/change-watch --plugin gitlab_proof proof`（`MR_REVIEW_CLI` と同じ流儀。`proof` 自身は読まない） |
| `MR_REVIEW_PROOF_AGENT` | （なし＝検証しない） | 検証エージェントの起動コマンド。引数の JSON の配列（`MR_REVIEW_VERIFY_ALLOW` と同じく JSON。シェルを介さずに起動するので、空白や Windows のパスで割れない）。`{prompt}` は 1 行の依頼に、`{input}` は入力ファイルのパスに置き換える。どちらも無ければ依頼を標準入力に渡す |
| `MR_REVIEW_PROOF_TIMEOUT_SEC` | 900 | 検証エージェント 1 回の制限時間（秒。1〜3600 に収める。数でなければ既定） |
| `MR_REVIEW_PROOF_MAX_FINDINGS` | 20 | 1 回に検証する指摘の数の上限（1〜100） |
| `MR_REVIEW_PROOF_ENV_PASS` | （なし） | 検証エージェントに追加で渡す環境変数の名前（`,` 区切り）。CLI の認証に要るものを書く |
| `MR_REVIEW_REPO_DIR` / `MR_REVIEW_WORKTREE` | 既存 | 作業フォルダ。検証エージェントの作業フォルダは `MR_REVIEW_WORKTREE` |
| `GITLAB_URL` / `GITLAB_TOKEN` / `GITLAB_PROJECT` | 既存 | `proof` が MR のタイトル・説明・URL を取るのに使う |

`MR_REVIEW_PROOF_AGENT` の例（フラグは CLI の版で確かめる。§10）:

```
MR_REVIEW_PROOF_AGENT=["kiro-cli","chat","--no-interactive","--trust-all-tools","{prompt}"]
MR_REVIEW_PROOF_AGENT=["copilot","-p","{prompt}","--allow-all-tools"]
```

既定を「なし」にするのは `MR_REVIEW_VERIFY_ALLOW` と同じ考え方で、入れただけでは何も起動しない。未設定なら
`verifier.status: not_configured` で全指摘が判断不能になり、今までどおり仮説として投稿される。
レビュー担当と違う CLI を検証に当てると（レビューは Kiro、検証は Copilot など）、同じモデルの同じ癖で見逃す分が減る。

### 5.2 起動のしかた

| 項目 | 決まり |
|---|---|
| 起動の前の確認 | `verify` と同じく、作業フォルダの HEAD が手元に記録した MR のコミット（`refs/mr-review/mr/<iid>`）と同じで、GitLab の MR の今のコミットとも同じときだけ起動する。違えば `not_ready`（先に `context` が要る） |
| 起動 | シェルを介さない（`shell=False`）。作業フォルダは `MR_REVIEW_WORKTREE`。標準入力は閉じる（依頼を標準入力で渡すとき以外） |
| 環境変数 | `verify` が渡すもの（`PATH`・`HOME`・`LANG` など）に、プロキシ（`HTTP_PROXY`・`HTTPS_PROXY`・`NO_PROXY` と小文字）・証明書（`SSL_CERT_FILE`・`NODE_EXTRA_CA_CERTS` など）・Windows の利用者フォルダ（`USERPROFILE`・`APPDATA`・`LOCALAPPDATA`）・`XDG_*`・`MR_REVIEW_PROOF_ENV_PASS` の名前を足す。`GITLAB_TOKEN` は `MR_REVIEW_PROOF_ENV_PASS` に書いてあっても渡さない |
| 制限時間 | `MR_REVIEW_PROOF_TIMEOUT_SEC`。超えたら、検証エージェントが起こした子プロセスごと止める（新しいプロセスグループで起動し、グループを止める。Windows は `taskkill /T /F`）。途中までの出力・書きかけの `result.json` は使わない（`verify` の時間切れと同じ） |
| 回数 | 1 回の `proof` で検証エージェントを 1 回だけ起動し、全指摘をまとめて渡す（指摘ごとに起動すると、件数 × 制限時間になる） |
| 後始末 | 一時フォルダを消す。作業フォルダに検証エージェントが残したファイルは、次の `context` が `checkout --force` と `clean` で消す |

社内の制約との対応: エージェント CLI は**この PC で**動かす（外のサービスに差分を送る口を `proof` は持たない）。プロキシは環境変数で
検証エージェントに引き継ぎ、`proof` 自身の GitLab への問い合わせは `gitlab_mr.py` の `GitLab`（`urllib`。環境変数のプロキシに従う）を使う。
結果は標準出力と GitLab のコメントだけで、メールは送らない。標準ライブラリだけで書く。

### 5.3 失敗したとき

| 起きたこと | `verifier.status` | 指摘 | 終了コード |
|---|---|---|---|
| `MR_REVIEW_PROOF_AGENT` が無い・JSON の配列でない | `not_configured` | 全部 判断不能 | 0 |
| 作業フォルダが MR の今のコミットでない・`MR_REVIEW_REPO_DIR` が無い | `not_ready` | 全部 判断不能 | 0 |
| MR を取れない（GitLab に届かない・環境変数が無い・MR が開いていない） | `request_unavailable` | 全部 判断不能 | 0 |
| コマンドが見つからない・起動できない | `launch_failed` | 全部 判断不能 | 0 |
| 制限時間を超えた | `timeout` | 全部 判断不能 | 0 |
| 返事が JSON として読めない・`results` が無い | `unparseable` | 全部 判断不能 | 0 |
| 返事はあるが、一部の指摘が無い | `ok` | 無い指摘だけ 判断不能 | 0 |

どの行も `reason: verifier_failed`（最後の行は `missing`）で、`evidence` は空。呼び出し側から見ると「証拠が無い＝仮説として投稿する」になり、
`proof` を入れる前と同じ結果に戻る。**`proof` が壊れても、レビューは今までより悪くならない**。

## 6. 判定と証拠規約の対応

### 6.1 対応表

| `proof` の判定 | 結果の置き場所 | 呼び出し側がすること | 印 | `context` での表示 |
|---|---|---|---|---|
| 裏付けあり（`supported`。証拠あり） | `post`・`evidence` と `evidence_file` あり | `mr comment <MR> --path … --line … --body-file <主張> --evidence-file <evidence_file>` | `evidence="verified"` | 〔検証済み〕 |
| 判断不能（`inconclusive`） | `post`・`evidence` は空 | `mr comment …` を証拠なしで（`verify` で自分で取った証拠があれば、それを渡してよい。§1.1） | `evidence="hypothesis"`（自分の証拠を渡したら `verified`） | 〔仮説（未検証）〕 |
| 検証の失敗（§5.3） | `post`・全部 判断不能 | 同上 | 同上 | 同上 |
| 反証あり（`refuted`。根拠あり） | `drop` | 投稿しない。まとめに件数だけ書く | — | — |

- `gitlab_mr.py` は変えない。`comment` は「証拠があれば `verified`、無ければ `hypothesis`」のままで、`proof` はその証拠を作る 1 つの手段になる
- 指摘の `key`（パス + 本文のハッシュ）は `claim` から決まり、証拠は混ぜないので、同じ指摘を `proof` の有無で二重に書くことはない
- 反証ありで落とした指摘は GitLab に残らないので、次のコミットでレビュー担当が同じ指摘をまた見つけ、また検証に回りうる。
  重複ではなく費用の問題なので、まずは受け入れる（§10）

### 6.2 証拠の先頭の 1 行

裏付けありの `evidence` は、先頭に `独立検証（proof）: 裏付けあり` の 1 行を `proof` が足してから、検証エージェントの書いた証拠を続ける。
コメントの「証拠」節を読んだ人が、レビュー担当自身の検証（`verify` の「コマンド / 終了コード / 出力」）と見分けられる。

## 7. Dagu のワークフローの例

**別ファイルの例にし、`agents/gitlab-mr-review-agent/examples/change-watch-mr-review-proof.yaml.example` に置く。**
既定の `change-watch-mr-review.yaml.example` は変えない。

| 案 | 判断 | 理由 |
|---|---|---|
| 既定の例のプロンプトを差し替える | 採らない | `proof` は検証エージェントの設定（`MR_REVIEW_PROOF_AGENT`）が要る任意の機能で、既定にすると未設定の PC でも毎回 `proof` を呼んで空振りする。エージェントの起動が 1 件あたり 2 回になり、費用と時間が増えることを利用者が選べない。既にあるワークフローはインストーラが上書きしないので、既定を変えても既存の PC には届かず、新旧で動きが分かれる。既存の `FlowTest.test_default_example` もプロンプトを見ている |
| エージェントのフォルダ直下に 2 つ目の `change-watch-*.yaml.example` を置く | 採らない | `install.py` は直下の `change-watch-*.yaml.example` を**すべて** DAG として `~/.config/dagu/dags/` に置く。同じ MR を同じ受け手（`mr-review`）で毎分取り合うワークフローが 2 つ動き、片方が `commit` した MR をもう片方が見ない・同じ MR を 2 回レビューする、が起きる |
| **`examples/` の下に置く** | 採る | `agent_files` は直下しか見ないので、インストーラは置かない。gitlab-issue-hub-agent の `examples/change-watch-issue-hub-route.yaml.example` と同じ作法（使う人がコピーして `/home/you` を直す） |

使うときは、例を `~/.config/dagu/dags/change-watch-mr-review.yaml` として**置き換える**（並べて置かない。README にそう書く）。

### 7.1 例の形

ステップは既定の例と同じ `get` → `review` → `commit` のままにし、`review` のプロンプトで `proof` を使わせる
（`review` は `harness.run` で kiro-cli を動かし、プロンプトでコマンドを使わせる、という今の形を変えない）。違うところ:

| 箇所 | 既定の例 | `proof` の例 |
|---|---|---|
| `review` のプロンプト | 見つけた問題を `comment` で指摘する | 見つけた問題をまず指摘のファイルにまとめ、`proof` に掛け、結果のとおりに `comment` する |
| `review` の `timeout_sec` | 1800 | 3600（`MR_REVIEW_PROOF_TIMEOUT_SEC` の分を足す。検証はレビューの制限時間の内側で動く） |
| `.env` | — | `MR_REVIEW_PROOF_CLI`・`MR_REVIEW_PROOF_AGENT`（・`MR_REVIEW_PROOF_TIMEOUT_SEC`） |

プロンプトに足す指示（既定のプロンプトの「新しく見つけた問題だけを comment で指摘し」のところを置き換える）:

1. 新しく見つけた問題は、すぐには `comment` せず、一時フォルダ（作業フォルダの外）の JSON にまとめる。指摘ごとに `id`・`target`（`path`・`line`）・
   `claim`（指摘の本文。主張だけを書き、確信度や確認した経緯は書かない）。自分で確かめた内容を残すなら `self_assessment` に書く
2. `${MR_REVIEW_PROOF_CLI} ${CHANGE.record.iid} --findings <そのファイル> --evidence-dir <一時フォルダ>` を 1 回実行する
3. 出力の `post` の指摘を `comment` で指摘する。本文は `claim` のまま変えず、`evidence_file` があれば `--evidence-file` に渡す。
   無ければ証拠なしで指摘する（自分の `verify` の証拠があれば渡してよい）
4. 出力の `drop` の指摘は投稿しない
5. `proof` が 0 以外で終わったら、指摘はすべて証拠なしで `comment` する
6. `summary` には、検証済み・仮説・独立検証で取り下げた指摘の件数を分けて書く

`resolve`（直った指摘を閉じる）は `proof` に掛けない。閉じる判断は今のコミットのファイルを `show` で見て行うもので、
検証の対象は「新しく投稿する主張」に絞る（§10）。

### 7.2 この形の弱いところと、次の段

この形では、**`drop` を投稿しないことはレビュー担当が指示に従うことに依っている**（検証の入力の独立性は `proof` が守るが、
結果を守らせる仕組みは無い）。また、レビュー担当が `proof` の終わりを待つあいだ、エージェント CLI が 2 つ動いている
（mr-review の「同時に 1 セッション」は、動いて要求を出しているのが 1 つ、という意味では保たれるが、プロセスは 2 つになる）。

`proof` はファイルを読んで JSON を出すだけのコマンドなので、ワークフローを `review`（指摘をファイルに書くだけ）→ `proof`（Dagu の `exec`）→
`publish`（結果のとおりに投稿する）に分ける形にも、そのまま使える。この形なら投稿をレビュー担当から切り離せ、エージェントは 1 つずつ順に動く。
ただし、ステップをまたぐファイルの受け渡し・古いファイルの掃除・まとめの書き手の分担を決めることになり、mr-review の設計が
「エージェントに JSON を出させ、決まった処理が書き込む」形を採らなかったのと同じ重さがある。**まずプロンプトの形で入れ、
レビュー担当が `drop` を投稿してしまう・入れ子の起動が CLI で動かない、のどちらかが実機で見つかったら分ける**（§10）。

## 8. テスト計画

`agents/gitlab-mr-review-agent/test/test_gitlab_proof.py` を足す（`python3 -m unittest discover -s test` で既存の試験といっしょに回る）。
GitLab は `test_gitlab_mr.py` の偽の GitLab、作業フォルダは同じく手元の bare リポジトリから作るものを使う（共通の部品は試験の中で取り込む。
`test_gitlab_mr.py` の中身は変えない）。標準ライブラリだけ（例の DAG を読む試験だけ PyYAML）。

**偽の検証エージェント**: 試験が一時フォルダに書く小さな Python スクリプト。`MR_REVIEW_PROOF_AGENT=[sys.executable, "<fake.py>", "{prompt}"]` で差し替える。
することは 2 つ: (a) 自分に**見えたもの全部**（引数・標準入力・環境変数・`{prompt}` が指す入力ファイルの中身・その一時フォルダのファイル一覧・
作業フォルダのファイル一覧）を記録ファイルに書く、(b) 試験が決めた動き（決めた JSON を `result.json` に書く / 標準出力に出す / 壊れた文字列を出す /
眠る / 0 以外で終わる）をする。

| 分類 | 試験 | 確かめること |
|---|---|---|
| 入力の分離 | 目印の不混入 | `self_assessment` の中（文字列・入れ子のオブジェクト・配列）と、知らない名前の項目（`reasoning`・`score` など指摘の直下）に目印文字列を仕込む。偽エージェントの記録（引数・標準入力・環境変数・入力ファイル）のどこにも目印が無い。`claim`・タイトル・説明・差分に入れた別の目印は入力にある（渡すべきものは渡っている） |
| | パスの不混入 | 指摘のファイルのパスが、記録のどこにも無い。`GITLAB_TOKEN` の値と `MR_REVIEW_*` の名前が環境変数に無い |
| | 型と関数 | `Claim` の項目が `id`・`path`・`line`・`claim` だけ。`build_verifier_input` の引数が `request`・`claims`・`reply_path` だけ（`inspect.signature`）。同じ引数なら、環境変数や作業フォルダを変えても同じ文字列を返す |
| | 構造 | `gitlab_proof.py` を `ast` で読み、説明の文字列（docstring・ヘルプ）のほかに `self_assessment` を参照する箇所が無い |
| | 置き場所 | 指摘のファイル・`--evidence-dir` が作業フォルダの中なら 2 で終わり、検証エージェントを起動しない。`--findings -`（標準入力）で同じ結果になる |
| 判定の解釈 | 3 つの判定 | `supported` / `refuted` / `inconclusive` が `post` / `drop` に分かれ、`counts` が合う。順は入力の順 |
| | 格下げ | 証拠が空の `supported`・`refuted`、知らない `verdict`、返事に無い `id`、重なった `id` が判断不能になる。入力に無い `id` は捨てる |
| | 証拠の扱い | `evidence` が入るのは `supported` だけ。先頭の 1 行が付く。`GITLAB_TOKEN` が `***` になる。`<!-- mr-review-agent:finding … -->` が除かれる。長い証拠が切られる |
| | 返事の読み方 | `result.json` を優先する。有効な `results` 配列を得られなければ標準出力の JSON（前後に雑談・` ```json ` のブロック）から読む。0 以外で終わっても読める結果は使う |
| | 入力の検査 | 必須の項目が無い指摘は `invalid_finding`、上限を超えた分は `not_checked`。`findings` が空なら起動しない。JSON でないファイルは 2 |
| 失敗 | 未設定 | `MR_REVIEW_PROOF_AGENT` が無い・配列でない → `not_configured`・全部 判断不能・0 |
| | 起動の失敗 | 無いコマンド → `launch_failed`・0 |
| | 時間切れ | `MR_REVIEW_PROOF_TIMEOUT_SEC=1` で眠る偽エージェント（書きかけの `result.json` を先に置く）→ `timeout`・全部 判断不能・0。書きかけは使わない。子プロセスが残らない |
| | 読めない返事 | 壊れた JSON・`results` が無い → `unparseable`・0 |
| | 準備ができていない | 作業フォルダが別のコミット・`MR_REVIEW_REPO_DIR` が無い → `not_ready`。GitLab に届かない → `request_unavailable`。どちらも 0 で、起動しない |
| | 後始末 | どの場合も一時フォルダが残らない |
| 証拠規約への接続 | 裏付けあり | `proof` の `evidence_file` を `mr comment --evidence-file` に渡すと、スレッドの印が `evidence="verified"`、本文に「証拠」節と先頭の 1 行があり、`context` に〔検証済み〕と出る |
| | 判断不能・失敗 | `post` の証拠なしの指摘を `mr comment` すると `evidence="hypothesis"` と注意書きが付き、〔仮説（未検証）〕と出る。検証の失敗（時間切れ）でも同じ |
| | 反証あり | `drop` の指摘を投稿しなければ、スレッドは増えない。`drop` に `evidence` / `evidence_file` が無い |
| | `key` | 同じ `claim` を `proof` あり・なしで 2 回 `comment` しても、2 回目は「同じ指摘が既にあります」になる |
| 入り口 | change-watch 経由 | `change_watch.py --plugin <gitlab_proof.py> proof …` と `python3 gitlab_proof.py …` が同じ結果を返す。`--plugin gitlab_mr --plugin gitlab_proof` を並べても両方のコマンドが使える |
| Dagu の例 | 例の流れ | `examples/change-watch-mr-review-proof.yaml.example` を `dagrun` で `get` → `review` → `commit` と回す。`review` の代わりの関数が、プロンプトから `proof` の呼び方を取り出し、指摘のファイルを書いて `proof`（偽の検証エージェント）を呼び、結果のとおりに `comment` する。裏付けありは `verified`、判断不能は `hypothesis`、反証ありは投稿されない。`dagrun.check_shape` を通る |
| | 直下に置いていない | `install.agent_files("gitlab-mr-review-agent")` のワークフローに `proof` の例が入っていない（インストーラが DAG として置かない） |

既存の試験を壊さないこと:

- `gitlab_mr.py`・既定の例・`install.py`・本体（`changewatch/`）は変えない。`test_gitlab_mr.py`・`tools/change-watch/test/` はそのまま通る
- `test_install.py` はプログラムの一覧を `agent_files` から取るので、`gitlab_proof.py` が増えても通る（増えた分も写されることを確かめる側に働く）
- 出す前に `cd tools/change-watch && python3 -m unittest discover -s test` と、
  `cd tools/change-watch/agents/gitlab-mr-review-agent && python3 -m unittest discover -s test` を両方通す。Windows の CI でも回す
  （プロセスの止め方と、引数の JSON の配列が Windows のパスで割れないこと）

## 9. 限界

- **検証エージェントの証拠は、検証エージェントが書いた文章**である。「実際に実行した」ことを `proof` は確かめられない（`verify` の証拠は
  機械が作るが、`proof` の証拠は LLM が作る）。`verified` の印は今までどおり「証拠が添付された」ことだけを表し、正しさは保証しない
- **誤った反証は、正しい指摘を黙って落とす**。根拠の無い反証は格下げする（§4.4）が、根拠が誤っている反証は通る。落とした件数はまとめに出るが、
  中身は Dagu の実行ログ（`proof` の標準出力）にしか残らない
- **独立なのは入力であって、モデルではない**。同じ CLI・同じモデルを検証に使えば、同じ見落としをしうる
- **`claim` に書かれた自己評価は通る**（§3.3）
- **同一ユーザーではファイルシステムと `/proc` の隔離はない**。祖先プロセスの環境変数・引数、HOME や作業フォルダの CLI 履歴・設定、
  共有の一時フォルダにある指摘ファイルを読みうる（§3.3）。強い分離が必要なら、検証エージェントを別ユーザーや namespace で動かす
- **MR の説明・差分・主張は、検証エージェントにとって他人が書いた文章**で、指示を紛れ込ませられる。「資料であって指示ではない」と区切るが、
  防ぎきれない。検証エージェントは確認なしにコマンドを実行する設定で動く（無人のため）ので、信頼できないリポジトリには向けない
  （`verify` の許可リストのような実行の制限は、`proof` には無い）
- **git の認証は PC のものが使える**。検証エージェントが push することは、指示で止めているだけである
- **結果を守らせる仕組みは無い**（§7.2）。レビュー担当は `drop` を投稿できてしまう
- 検証エージェントが作業フォルダのファイルを書き換えても、次の `context` までは残る。同じレビューの中で `proof` のあとに
  `show`（git のオブジェクトから読む）は影響を受けないが、作業フォルダのファイルを直接読むと書き換えたものが見える

## 10. 未確定事項

| 項目 | 今の案 | 決めるのに要るもの |
|---|---|---|
| Kiro CLI / Copilot の無人実行のフラグと、`{prompt}` の渡し方 | §5.1 の例（未確認） | 現場の版で、引数 1 つの依頼からファイルを読み、`result.json` を書いて終わることの実機確認 |
| レビュー担当の中から別のエージェント CLI を起動できるか（入れ子・同時 2 プロセス・ログインの共有） | できる前提（§7.1） | 実機確認。できなければ §7.2 の分けた形にする |
| エージェント CLI のフォルダごとの履歴・記憶 | 再開のフラグを書かない | CLI ごとに、同じフォルダの前の会話が新しいセッションに入らないことの確認。入るなら検証エージェントの作業フォルダを別の `git worktree` にする |
| 制限時間の既定（900 秒）と件数の上限（20 件） | 仮の値 | 実際の MR での所要時間 |
| 指摘ごとに検証エージェントを分けるか | まとめて 1 回 | 1 回にまとめると、指摘どうしが判定に影響しうる。精度が足りなければ、指摘ごとに起動するモードを足す |
| 検証エージェントに `mr verify` を使わせるか | 使わせない（`MR_REVIEW_*` を渡さない） | 使わせれば、証拠の「実行結果」の部分を機械が作れる。許可リストと作業フォルダの環境変数だけを渡す形を、次の段で検討する |
| 反証ありで落とした指摘の記録 | 残さない（次のコミットでまた検証に回りうる） | 費用が問題になったら、落とした `key` を覚えるか、まとめに 1 行ずつ書く |
| `resolve`（直ったという判断）の検証 | 対象外 | 誤って閉じる例が見つかったら、同じ形（主張＝「直った」）で `proof` に掛ける |
| 投稿をレビュー担当から切り離す（§7.2） | しない | レビュー担当が `drop` を投稿する例が出るか、入れ子の起動が動かないとき |

実装のときに、この文書のほかに直すもの（この設計の変更には含めていない）:
gitlab-mr-review-agent の README（「指摘と証拠の規約」に `proof` の節、コマンドの表、例の使い方）、
`gitlab-mr-review-agent.env.example`（`MR_REVIEW_PROOF_*` をコメントで足す）、`docs/designs/README.md` の索引。

## 11. 採らなかった案

- **検証エージェントに指摘のファイルをそのまま渡し、「自己評価は読まないで」と指示する**: 独立性が指示に依る。渡さないことをプログラムで決める
- **自己評価の項目を名前で除く（`self_assessment` を消してから渡す）**: 別の名前の項目に書かれた推論が通る。許可した項目だけを拾う
- **元の依頼もレビュー担当に書かせる**（`context` の出力をファイルにして渡す）: `context` の出力には前回までの指摘（レビュー担当の過去の主張と返信）が
  入っている。レビュー担当が要約・加工もできてしまう。`proof` が GitLab と git から自分で取る
- **`proof` が `comment` まで行う**: 印・重複の防止・位置の決め方が 2 か所になる。`proof` は GitLab に書かず、分けて返すだけにする
- **印に検証者の属性を足す**（`evidence="proof"` など）: `gitlab_mr.py` の読み方と `context` の表示を変えることになり、#1046 の
  「証拠があれば `verified`、無ければ `hypothesis`」の二分が崩れる。見分けは証拠の先頭の 1 行で足りる
- **失敗を 0 以外の終了コードで知らせる**: Dagu のステップやレビュー担当の手順が止まり、検証が壊れただけでレビューが出なくなる。
  失敗は JSON の `verifier.status` で知らせ、指摘は仮説に倒す
- **判断不能・反証の説明も `evidence` に入れる**: 呼び出し側がそのまま `--evidence-file` に渡すと、反証された指摘に `verified` が付く
- **検証エージェントの返事を標準出力だけから読む**: エージェント CLI の標準出力には経過・色の制御文字が混ざる。ファイルに書かせ、標準出力は戻り道にする
