# GitLab API — Python スクリプトリファレンス

## 目次

- [セットアップ](#セットアップ)
- [--get オプション](#--get-オプション)
- [認証・ユーザー](#認証ユーザー)
- [イシュー操作](#イシュー操作)
- [ブランチ名生成](#ブランチ名生成)
- [作業用フォルダ](#作業用フォルダ)
- [MR（マージリクエスト）操作](#mrマージリクエスト操作)
- [self-defer チェック](#self-defer-チェック)
- [学習ループ](#学習ループ)
- [トラブルシューティング](#トラブルシューティング)

`scripts/gl.py` を Python で呼び出すコマンド集。`glab` CLI は不要。
Python 3.8+ と stdlib のみで動作し、Windows・macOS・Linux に対応する。

---

## セットアップ

GITLAB_TOKEN 環境変数を設定する（必須）:

- **bash/zsh**: `export GITLAB_TOKEN=glpat-xxxxxxxxxxxx`
- **PowerShell**: `$env:GITLAB_TOKEN = "glpat-xxxxxxxxxxxx"`
- **cmd.exe**: `set GITLAB_TOKEN=glpat-xxxxxxxxxxxx`

動作確認（git remote からホスト・プロジェクトを自動取得）:

```
python scripts/gl.py project-info
```

> **注**: 環境によって `python` を `python3` や `py` に読み替える。

`project-info` の出力例:
```json
{
  "host": "gitlab.com",
  "project": "myteam/myapp",
  "project_encoded": "myteam%2Fmyapp",
  "base_url": "https://gitlab.com/myteam/myapp"
}
```

---

## --get オプション

すべての読み取りコマンドで `--get FIELD` を使うとフィールドだけを出力できる。
ドット記法で配列インデックスとネストに対応する。

```
python scripts/gl.py current-user --get username
python scripts/gl.py get-issue 42 --get title
python scripts/gl.py get-issue 42 --get author.username
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.web_url
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.iid
python scripts/gl.py check-defer 42 --get defer
python scripts/gl.py check-defer 42 --get remaining_minutes
```

---

## 認証・ユーザー

```
python scripts/gl.py current-user
python scripts/gl.py current-user --get username
```

---

## ノードID

ターミナル（ノード）を識別するIDを確認する。`GITLAB_NODE_ID` 環境変数で上書き可能。
同一 GitLab アカウントで複数ターミナルを独立ノードとして動かす場合に設定する。

```
python scripts/gl.py get-node-id
python scripts/gl.py --get node_id get-node-id
# → 12 文字のランダム文字列（~/.config/gitlab-idd/node-id に自動保存）

# ターミナルごとに上書きする場合:
export GITLAB_NODE_ID=my-terminal-1
python scripts/gl.py --get node_id get-node-id
# → "my-terminal-1"
```

> **注**: イシュー作成時（`create-issue`）に creator-node-id が description に自動埋め込まれる。
> ワーカー着手時には `worker-node-id` を着手コメントに含める（worker-role.md ステップ 3-3 参照）。
> これらを `check-defer` / `check-review-defer` が読み取り、ターミナル単位で自己判定を行う。

---

## イシュー操作

### 一覧取得

```
python scripts/gl.py list-issues --state opened
python scripts/gl.py list-issues --label "status:open"
python scripts/gl.py list-issues --label "status:open,assignee:any"
python scripts/gl.py list-issues --label "status:open,priority:high"
python scripts/gl.py list-issues --assignee MY_USER --state opened
python scripts/gl.py list-issues --author  MY_USER --state opened
python scripts/gl.py list-issues --label "status:review-ready" --author MY_USER
python scripts/gl.py list-issues --label "status:needs-rework" --assignee MY_USER
```

### イシュー詳細・コメント

```
python scripts/gl.py get-issue 42
python scripts/gl.py get-comments 42
python scripts/gl.py get-issue 42 --get title
python scripts/gl.py get-issue 42 --get author.username
```

### イシュー作成

複数行のイシュー本文は `--body-file` でファイルから渡す。
まず本文を Markdown ファイル（例: `_body.md`）に書いてからコマンドを実行する:

```
python scripts/gl.py create-issue \
  --title "ログインフォームを実装する" \
  --body-file _body.md \
  --labels "status:open,assignee:any,priority:normal"
```

`_body.md` の内容例:

```markdown
## 目的

{目的を 1〜3 文で記述}

## 実装スコープ

- {変更点 1}

## 受け入れ条件

- [ ] {条件 1}
- [ ] {条件 2}

## 技術制約

特になし
```

短い本文は `--body` に直接渡すことも可能:

```
python scripts/gl.py create-issue --title "タイトル" --body "短い説明" --labels "status:open,assignee:any,priority:normal"
```

### イシュー更新

ラベル・アサイニー・状態の更新:

```
python scripts/gl.py update-issue 42 --add-labels "status:in-progress" --remove-labels "status:open,assignee:any"
python scripts/gl.py update-issue 42 --assignee MY_USER
python scripts/gl.py update-issue 42 --state-event close
python scripts/gl.py update-issue 42 --state-event reopen
python scripts/gl.py update-issue 42 --add-labels "status:needs-rework" --remove-labels "status:review-ready" --state-event reopen
```

イシュー本文（description）の更新:

```
python scripts/gl.py update-issue 42 --body-file _clarified_body.md
python scripts/gl.py update-issue 42 --body "更新後の説明（短い場合）"
```

複数オプションの組み合わせも可能:

```
python scripts/gl.py update-issue 42 \
  --body-file _clarified_body.md \
  --add-labels "status:open,assignee:any" \
  --remove-labels "status:needs-clarification"
```

### コメント投稿

短いコメントは `--body` に直接渡す:

```
python scripts/gl.py add-comment 42 --body "作業開始しました"
```

複数行のコメントは `--body-file` でファイルから渡す:

```
python scripts/gl.py add-comment 42 --body-file _comment.md
```

`_comment.md` の内容例:

```markdown
## ✅ 実装完了

受け入れ条件をすべて満たしました。
```

---

## ブランチ名生成

```
python scripts/gl.py make-branch-name 42
# → "feature/issue-42-add-login-form"
```

## 作業用フォルダ

GitLab の API は呼ばず、git だけを使う（トークンは要らない）。

```
python scripts/gl.py workspace-create 42 --branch feature/issue-42-add-login-form --target main
# → 作業用フォルダのパス（一時フォルダの下の gitlab-idd-42-xxxxxxxx）。TARGET を基点に BRANCH を作ってある

python scripts/gl.py workspace-remove <そのパス>
# → 作業用フォルダを消す（worktree なら手元のブランチも外す）
```

- `GITLAB_IDD_LOCAL_REPO`（または `skill-registry.json` の `local_repo`）があれば、そのリポジトリに `git worktree` を足す。
  クローンせず、`origin` から TARGET だけを fetch する。リポジトリの作業ツリー・ブランチ・書きかけの変更には触らない
- 無ければ、今いるリポジトリの `origin`（`--remote-url` で変えられる）を作業用フォルダにクローンする
- 作る場所は一時フォルダ（`--base-dir` で変えられる）。Windows でも同じ手順で動く
- `workspace-remove` は名前が `gitlab-idd-` で始まらないフォルダを消さない

## パケット ID（リポジトリ横断・人が入力できる長さ）

レビューパケットに付ける**リポジトリ横断で一意・人が手入力できる短い ID**を生成／正規化する。
Crockford base32（紛らわしい I/L/O/U を除外）で、既定は `GK-` + 6 文字（≈30bit）。オフライン動作（トークン不要）。

```
python scripts/gl.py gen-packet-id
# → "GK-7F3KQ9"
python scripts/gl.py gen-packet-id --length 8 --prefix "PKT-"
# → "PKT-QB46CFWJ"

# 人が打ち込んだ ID を照合用に正規化（大文字化・前置詞/空白/ハイフン許容・I/L→1・O→0・U除去）
python scripts/gl.py normalize-packet-id "gk 7f3 kq9"
# → "GK-7F3KQ9"
python scripts/gl.py normalize-packet-id "7F3KQ9"
# → "GK-7F3KQ9"
```

---

## MR（マージリクエスト）操作

```
python scripts/gl.py list-mrs --state opened
python scripts/gl.py list-mrs --source-branch "feature/issue-42-add-login"
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.web_url
python scripts/gl.py list-mrs --source-branch BRANCH --get 0.iid
```

MR 作成（本文をファイルから渡す）:

```
python scripts/gl.py create-mr \
  --title "ログインフォームを実装する" \
  --source-branch "feature/issue-42-add-login-form" \
  --target-branch main \
  --description-file _mr_body.md \
  --draft \
  --remove-source-branch
```

短い説明なら `--description` に直接渡すことも可能:

```
python scripts/gl.py create-mr \
  --title "ログインフォームを実装する" \
  --source-branch "feature/issue-42-add-login-form" \
  --target-branch main \
  --description "Closes #42" \
  --draft \
  --remove-source-branch
```

MR 更新（本文の書き換え・ドラフト解除）:

```
python scripts/gl.py update-mr MR_IID \
  --description-file _mr_body.md \
  --no-draft
```

`--description` に直接渡すことも可能:

```
python scripts/gl.py update-mr MR_IID --description "更新後の説明" --no-draft
```

MR を未マージのままクローズ（却下）／再オープン:

```
python scripts/gl.py update-mr MR_IID --state-event close
python scripts/gl.py update-mr MR_IID --state-event reopen
```

```
python scripts/gl.py merge-mr MR_ID --squash --remove-source-branch
```

MR の差分（変更ファイル一覧と unified diff）を取得（ローカルクローンが無い場合のフォールバック）:

```
python scripts/gl.py get-mr-changes MR_IID
python scripts/gl.py get-mr-changes MR_IID --get 0.new_path
```

ブランチの削除（却下した MR のソースブランチ整理など）:

```
python scripts/gl.py delete-branch "feature/issue-42-add-login-form"
```

CI パイプラインの確認（マージ前に実行）:

```
python scripts/gl.py get-mr-pipeline MR_IID
python scripts/gl.py get-mr-pipeline MR_IID --get status
# → "success" / "running" / "pending" / "failed" / "canceled" / "skipped" / "none"
```

`"none"` は CI が未設定またはまだトリガーされていない場合に返る。

---

## self-defer チェック

猶予／ロック判定コマンドは 3 種類ある。いずれも `defer=true` ならスキップ、`false` なら着手可。

### check-defer（自分発行イシューの猶予）

自分のターミナル（ノード）が作成したイシューを猶予期間中はスキップするためのチェック。
「自分かどうか」はイシュー description に埋め込まれた `creator-node-id` で判定する（フォールバック: author.username）。

```
python scripts/gl.py check-defer 42 --get defer
# → True または False

python scripts/gl.py check-defer 42 --minutes 60 --get defer
python scripts/gl.py check-defer 42 --minutes 60 --get remaining_minutes
```

`check-defer` の判定結果:

| reason | defer | 意味 |
|--------|-------|------|
| `not_my_issue` | false | 他ノードが作成 → 即取得可 |
| `self_created_too_recent` | true | 自ノード作成・猶予中（デフォルト 60 分）→ スキップ |
| `self_created_but_expired` | false | 自ノード作成・猶予切れ → 取得可 |

### check-assigned-defer（他ノード着手済みイシューの疎境期間）

`worker-node-id` が別ノードのイシューが放置されていないか確認する。
ロック期間内は引き継ぎ禁止。期間切れなら取得可。

```
python scripts/gl.py check-assigned-defer 42 --get defer
# → True または False

python scripts/gl.py check-assigned-defer 42 --minutes 1440 --get defer
python scripts/gl.py check-assigned-defer 42 --get remaining_minutes
```

`check-assigned-defer` の判定結果:

| reason | defer | 意味 |
|--------|-------|------|
| `no_worker_node_id` | false | 着手記録なし → 引き受け可 |
| `my_assignment` | false | 自分が着手済み → 継続可 |
| `assigned_active_lock` | true | 別ノードが着手中（デフォルト 1440 分）→ スキップ |
| `assigned_lock_unknown` | true | 着手時刻不明 → スキップ |
| `assigned_lock_expired` | false | 着手から 24h 経過し放置 → 引き受け可 |

### check-review-defer（自分実装イシューのレビューロック）

イシューのコメントに埋め込まれた `worker-node-id` とロック時間で判定する。
`worker-node-id` がないイシューは誰でもレビュー可能（defer=false）。

```
python scripts/gl.py check-review-defer 42
python scripts/gl.py check-review-defer 42 --minutes 1440
python scripts/gl.py check-review-defer 42 --get defer
```

---

## 学習ループ

記録形式と評価の規則は [learning-loop.md](learning-loop.md) を参照する。

書き込み系 4 コマンドの `--dry-run` は投稿予定のコメントを標準出力に表示する。GitLab API と子プロセスは呼び出さないため、`ltm-rate --dry-run` では原因分類、引用と差し戻し本文の照合、対象記憶と計画の突き合わせなど、履歴依存の検証を省く。そのため dry-run が成功しても通常実行では終了コード 2 になり得る。

### 終了コード

| コード | 意味 | 呼び出し側の扱い |
|---|---|---|
| 0 | 成功（`empty`・`unavailable` を含む） | 続行 |
| 1 | 既存どおりの `ERROR:`（API・認証・ファイルが読めない） | 既存どおり |
| 2 | 入力が規則に合わない。何も実行せず、何も投稿しない | 入力を直して再実行 |
| 3 | 学習ステップが失敗し、**失敗をイシューに記録できた** | 続行。完了報告に書く |
| 4 | 学習ステップが失敗し、**記録にも失敗した**（標準エラーに内容を全文出す） | 続行。完了報告に内容を書く |

補助スクリプトは同じスキル置き場にある `ltm-use/scripts` と `wiki-use/scripts` から探す。
`GITLAB_IDD_LTM_SCRIPTS` と `GITLAB_IDD_WIKI_SCRIPTS` でそれぞれ上書きできる。

### ltm-recall

```sh
python scripts/gl.py ltm-recall <issue_id> --query Q [--query Q …] [--context C] [--wiki-query K …] [--limit N] [--force] [--dry-run] [--get FIELD]
```

1. コメントを取得し、cycle と trigger（差し戻しコメントが 1 つ以上あれば `rework`）を決める。
2. 自ノード・同 cycle の `recall` イベントが既にあり `--force` が無ければ、そのイベントから出力を組み立てて終了（投稿しない。冪等）。
3. 各 `--query` で `recall_memory.py "<Q>" --scope all --limit <N> --json --no-track [--context C]` を実行。結果を `id` で束ね、`score` は最大値を採る。上位 N 件（既定 5、範囲 1〜10）。`--query` と `--wiki-query` はそれぞれ最大 3 件。
4. 各 `--wiki-query` で `wiki_query.py search "<K>" --json` を実行。`id` で束ね上位 5 件。本文（`text`）は捨てる。
5. コメントを投稿（見出し `## 📚 参照した知見`、表「記憶 ID / タイトル / 関連度」「wiki ページ / 関連度」、0 件なら「該当なし」、未導入なら「記憶の検索は未導入のため行っていません」の 1 行、末尾に `recall` マーカー）。
6. 標準出力: [ステップ 4-0](worker-role.md#ステップ-4-0-スキル選定skill-selector) の `knowledge_context` と同じ構造の JSON（`score` を `relevance` の名で出す。`rework_causes` は直前の差し戻しマーカーから）。`--get` 対応。

- 全クエリがリトライ後も失敗した場合は `status: error` で recall と `failure` のコメントを投稿し、終了コード 3 を返す。一部だけ失敗した場合は成功したクエリの結果で続行し、終了コード 0 を返す。失敗した検索語は結果 JSON と recall マーカーの `failed_queries` に記録する。
- **wiki の失敗は終了コードに影響させない**。`wiki_status: error` と理由 1 行を recall コメントに書くだけ。wiki 未設定のノードは珍しくなく（`wiki_query.py` は設定が無いと exit 1）、毎回失敗コメントを出すと失敗の表示が埋もれる。
- `--no-track` を付ける理由は [誤発火対策](learning-loop.md#誤発火対策)の規則 6。

### ltm-log

```sh
python scripts/gl.py ltm-log <issue_id> --event usage --data-file F [--dry-run]
python scripts/gl.py ltm-log <issue_id> --event failure --step S --reason R [--dry-run]
```

- `usage`: 自ノード・同 cycle の `recall` イベントの ID と突き合わせる。recall に無い ID が含まれる、`use` が 3 値以外、`applied` なのに `how` が空 → 終了コード 2。書かれなかった ID は `unused` で補う。recall イベントが無い場合は終了コード 2（「先に ltm-recall を実行してください」）。
- 検証を通ったら、`applied` の ID だけを `recall_memory.py --ids a,b --json` で**追跡ありで**読み直す（参照回数の加算はここで行う）。この読み直しの失敗は無視する。
- 表はステップ 5-3 の完了コメントに記載し、ここでは短いコメント `📎 参照した知見の使い方を記録しました。` + `usage` マーカーを投稿する。
- `failure`: `--step` は `recall` / `save` / `retro` / `rate` / `sprint-review` のいずれか。gl.py を通らない手順の失敗を記録する。`## ⚠️ 学習ループの失敗` コメントを投稿。終了コード 0。

### ltm-save

```sh
python scripts/gl.py ltm-save <issue_id> --kind execution|retro [--trigger rework|clarification|non-convergence] --args-file F [--dry-run]
```

- `--args-file` は JSON オブジェクト。値は文字列で渡す。許すキー: `category` `title` `summary` `content` `conclusion` `tags` `context` `memory_type` `importance`。`title` と `summary` は必須。未知のキー、`--kind retro` で `--trigger` なし → 終了コード 2。
- 実行: `save_memory.py --non-interactive --no-dedup --<key> <value> …`（長文を引数ファイルで渡すので、シェルのクォートに依存しない）。
- 成功: 標準出力の `保存しました [..]: <path>` からファイル名を取り、`save` または `retro` イベントを投稿。
  - `execution` のコメント: `🧠 実行知見を保存しました。`
  - `retro` のコメント: 見出し `## 🔁 振り返り`、原因分類、学び（`conclusion`）の 2〜3 行。`causes` は `--trigger rework` のとき対象の差し戻しコメントのマーカーから取る。マーカーが無い場合と他の trigger では `["unknown"]`。`target_cycle` は `trigger: rework` なら「振り返りが済んでいない最も古い差し戻しサイクル」、それ以外は現 cycle。未振り返りの差し戻しがない状態で `--trigger rework` を指定すると終了コード 2。
- 未導入: 終了コード 0、標準出力 `{"status":"unavailable"}`、投稿なし。
- 失敗: `failure` コメントを投稿（`lost` に `title` と `conclusion` を入れ、**表示部分にも書く**。学びそのものを失わないため）。終了コード 3。
- 標準出力は `status`（`ok` / `unavailable` / `failed`）。`ok` と `failed` には `file` と `attempts` も入る。`failed` の `file` は空文字。

### ltm-rate

```sh
python scripts/gl.py ltm-rate <issue_id> --plan-file F [--dry-run]
python scripts/gl.py ltm-rate <issue_id> --outcome approved --auto [--dry-run]
```

- 対象サイクル: `outcome` が `rework` なら「自ノードの `usage` があり、結果が `rework` で、評価・保留・スキップが未確定の対象記憶が残る最も古い cycle」。`approved` なら結果が `approved` の最終 cycle。該当なし → 終了コード 0、`{"status":"nothing-to-rate"}`、投稿なし。
- `--auto`（承認のみ）: 計画を gl.py が作る。対象 = その cycle の `usage` で `applied` のもの全部に `good`。`applied` が 0 件なら `nothing-to-rate` を返し、投稿せず終了コード 0。
- `--plan-file`: JSON の `outcome` に `approved` または `rework`、`items` に評価計画を指定する。[誤発火対策](learning-loop.md#誤発火対策)の規則で検証する。計画に含まれない対象記憶が 1 件でもあれば終了コード 2 を返し、子プロセスの実行とコメント投稿を行わない。correction の上限を超えた項目は理由付きで保留にする。
- 実行: 項目ごとに `rate_memory.py --id <id> --scope all --good`、または `--correction --note "[gitlab-idd #<iid>] <note>"`。
  - 終了コード 1 で標準エラーに「ID が見つかりません」→ `result: not-found`（失敗扱いにしない。別ノードの記憶）。
  - それ以外の失敗 → 1 回リトライし、失敗が続けば `result: error`。スクリプト未導入なら `skipped` に `reason: unavailable` を記録する。
- 初回の評価返しのコメント（全件保留でも投稿する）:

````markdown
## ⚖️ 記憶への評価返し（{承認|差し戻し}・サイクル {n}）

| 記憶 | 評価 | 理由 |
|---|---|---|
| mem-20260303-001 JWT の期限設定 | 要修正 | トークン期限は 30 分。15 分という記述は現行仕様と不一致 |
| mem-20260411-004 | 保留 | 記憶は正しく、適用の仕方を誤った |

評価を返したノード: `{NODE_ID}`。誤りだと思ったら、このコメントに返信してください（取り消し手順は下）。

<details><summary>取り消し方</summary>

上のノードで実行します。
`python {ltm-use}/scripts/rate_memory.py --id mem-20260303-001 --good --note "取り消し: #42 の評価は誤判定"`
</details>
````

- 評価結果に `error` があれば `failure` コメントも投稿して終了コード 3。記録にも失敗した場合は 4。評価処理が完了した場合は 0（`not-found` を含む）。
- 標準出力は `rate` イベントの JSON。`ok` または `not-found` の項目は評価済みとしてスキップする。`error` の項目は再実行できる。処理済みのサイクルで評価済みの項目だけを再処理する場合は、評価と投稿を省略する。

### ltm-trail

```sh
python scripts/gl.py ltm-trail <issue_id> [--retro-check] [--get FIELD]
```

読み取り専用。

```json
{
  "issue_id": 42, "node": "node-abc", "cycle": 2,
  "rework_count": 1, "rework_causes": [["spec-misread"]],
  "via_clarification": false,
  "cycles": [
    {"cycle": 1, "recall": true, "usage": true, "outcome": "rework", "rated": false, "retro": false},
    {"cycle": 2, "recall": false, "usage": false, "outcome": "pending", "rated": false, "retro": false}
  ],
  "pending": {"recall": true, "retro_cycles": [1], "rate": [{"cycle": 1, "outcome": "rework"}]},
  "open_failures": [{"step": "save", "cycle": 1, "at": "2026-11-19T03:00:00Z"}],
  "malformed": 0,
  "retro_check": {"due": false, "reasons": [], "done_count": 17}
}
```

- `recall` / `usage` / `rated` は自ノードのイベントだけを見る。`retro` はノードを問わない。自ノードの `usage` があり、対象記憶の全件について評価・保留・スキップが確定済みなら `rated: true`。`error` が残る対象記憶は未確定として扱う。
- `pending.retro_cycles`: 結果が `rework` で、`target_cycle` がそのサイクルで `trigger: rework` の `retro` イベントが無いもの。
- `open_failures`: `failure` のうち、同じ `step`・`cycle`・`node` の成功イベントが後に無いもの（`sprint-review` は解消されない。記録として残す）。
- `via_clarification`: コメントに `gitlab-idd:clarification-requested` がある。
- `retro_check` は `--retro-check` のときだけ。`done_count` は `GET /projects/:id/issues?state=closed&labels=status:done` の件数（`api_list` で数える）。
- `--get` 対応。終了コードは 0 か 1。

### ltm-pending

```sh
python scripts/gl.py ltm-pending [--max-issues N] [--days D]
```

読み取り専用。

- 対象: 担当者が自分のイシューのうち、(a) オープンでラベルが `status:approved` か `status:needs-rework`、(b) クローズ済みで `updated_after` が D 日以内（既定 30）。更新の新しい順に最大 N 件（既定 20）。各イシューのコメントを取得してトレイルを組む。
- 出力:

```json
{
  "rate_pending": [{"issue_id": 42, "cycle": 1, "outcome": "approved"}],
  "open_failures": [{"issue_id": 40, "step": "save", "cycle": 1}],
  "scanned": 7
}
```

- `rate_pending` には `pending.rate` が空でないものだけ。`closed-unknown` は載せない。
- `--max-issues` と `--days` は 0 以上。終了コードは成功時 0、API などのエラー時 1、不正な引数は 2。

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `Set GITLAB_TOKEN...` | トークン未設定 | GITLAB_TOKEN 環境変数を設定する |
| `Cannot get git remote 'origin'` | git リポジトリ外 or remote なし | 正しいディレクトリで実行 |
| `HTTP 401 Unauthorized` | トークン無効・期限切れ | 新しいトークンを発行して再設定 |
| `HTTP 403 Forbidden` | 権限不足 | GitLab プロジェクトの Developer 以上の権限が必要 |
| `HTTP 404 Not Found` | remote の URL が間違っている | `git remote get-url origin` で確認 |
| `python: command not found` | Python コマンド名が違う | `python3` または `py` を試す |
| MR マージ失敗 | CI パイプラインが失敗中 | GitLab の MR ページでパイプライン状態を確認 |
