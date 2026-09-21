# agent-herd 利用ガイド兼 CLI 仕様

`agent-herd` は、LAN 上の Ollama を対話、単発処理、ファイル編集、定型処理から使うための
コマンドである。前半は利用手順、後半は CLI と出力の契約を扱う。

設計判断は[設計書](../designs/agent-herd-design.md)、CLI 定義ファイルの形式は
[agent-cli 仕様書](./agent-cli-spec.md)に分けてある。実装は
`tools/agent-tools/agentcore/agentcore/` と `tools/agent-tools/install.sh` にある。

## まず動かす

### 前提

- macOS、Linux、WSL のいずれか
- Python 3.11 以上
- 接続できる Ollama サーバーと、使用するモデル
- `aider` バックエンドでファイルを編集する場合は Aider CLI

以下では既定モデルの `gemma4:e4b` を使う。別のモデルを使う場合は、コマンドの
`--model` またはモデル名を置き換える。

### インストール

リポジトリのルートで実行する。

```bash
bash tools/agent-tools/install.sh --only agent-herd
export PATH="$HOME/.local/bin:$PATH"
```

`--only agent-herd` を外すと、agent-project などもまとめて更新する。TUI の色付けが必要なら
`--with-rich` を付ける。このオプションだけはパッケージ取得にネットワークを使う。

インストーラは次の 3 コマンドを置く。

```text
~/.local/bin/agent-herd
~/.local/bin/agent-aider   # agent-herd の別名
~/.local/bin/agent-ollama  # agent-herd の別名
```

### Ollama へ接続する

Ollama が別の PC で動いている場合は、接続先を指定する。

```bash
export OLLAMA_HOST=http://YOUR_OLLAMA_HOST:11434
```

`OLLAMA_API_BASE` は `OLLAMA_HOST` から補完される。プロキシを使う環境では、Ollama の
ホストが `NO_PROXY` と `no_proxy` に自動で追加される。環境変数は `~/.profile` に置いてもよい。

モデルがサーバーに無ければ、そのサーバー側で取得する。

```bash
ollama pull gemma4:e4b
```

### 導入を確認する

```bash
agent-herd --version
agent-herd defs
agent-herd ollama --context gemma4:e4b
```

`defs` に `aider` と `ollama` が出れば定義を読めている。`--context` はモデルの文脈上限を
問い合わせるだけで、推論は実行しない。

最初の依頼は読み取り専用で試す。

```bash
agent-herd --readonly -p '7 * 8 の答えだけ返して'
```

結果は stdout、接続情報やトークン数は stderr に出る。シェルで結果だけをファイルへ渡せるよう、
両者は混ぜない。

> 注意: `--readonly` を外した `agent-herd -p` は定義の書き込みモードを使う。既定の
> `ollama` 定義では、モデルが OS ユーザー権限の bash を実行できる。質問だけなら
> `--readonly` を付ける。

## 作業別の使い方

### 対話する

引数なしで Ollama の TUI を開く。

```bash
agent-herd
```

バックエンドとモデルを変える場合は次のように指定する。

```bash
agent-herd --agent aider --model gemma4:e4b
agent-herd chat ollama --model gemma4:e4b
```

TUI のコマンドは `/help`、キー操作は `/keys` で確認できる。終了は `/quit`。

### 一度だけ質問する

本文が短ければ `-p` に続けて書く。パイプから渡すこともできる。

```bash
agent-herd --readonly -p 'このエラーの原因候補を3つ挙げて: connection reset'
printf '%s\n' '次の文章を要約して' | agent-herd --readonly -p
```

既定は `ollama`。バックエンドや作業ディレクトリを変える場合は `--agent` と `--dir` を使う。

```bash
agent-herd --agent ollama --model gemma4:e4b --readonly --dir ./my-repo -p '質問本文'
```

### リポジトリを変更せずに調べる

`ollama-read` は、読み取りコマンドだけを許可した探索用 profile（用途別の起動設定）である。

```bash
cd /path/to/repository
printf '%s\n' '認証処理の入口と呼び出し経路を調べて' \
  | agent-herd exec ollama-read
```

この profile はファイルの読み取りと git の参照系コマンドを使える。`agent-herd --readonly -p`
はツールを一切与えないため、リポジトリを調べる用途には向かない。

### 指定したファイルを編集する

Aider に編集対象を渡す。`--file` と `--read` は複数回指定できる。

```bash
cd /path/to/repository
printf '%s\n' 'parse_config の例外メッセージを日本語に直し、既存テストを保って' \
  | agent-herd exec aider \
      --file src/config.py \
      --read tests/test_config.py
```

`--file` は編集対象、`--read` は参照だけを許すファイルである。変更せずに Aider の結果を
確認したい場合は `--readonly` を加える。

### JSON や配列で受け取る

出力形式が決まっている処理は profile を指定する。

```bash
printf '%s\n' 'dependencies キーに name と reason の配列を入れて返して' \
  | agent-herd exec ollama-json

printf '%s\n' 'この作業を独立したタスクへ分割して' \
  | agent-herd exec ollama-list
```

`ollama-json` はトップレベルがオブジェクトの JSON、`ollama-list` は文字列の JSON 配列を返す。
purpose（用途名）から profile を選ばせることもできる。

```bash
agent-herd --readonly --purpose verify -p 'この結果は受入条件を満たすか: ...'
```

実際に起動するコマンド列は、実行前に確認できる。

```bash
agent-herd defs ollama-json
agent-herd defs ollama --purpose verify
agent-herd defs aider --model gemma4:e4b --json
```

### 型付きの問いに確率つきで答えさせる

選択肢が決まっている判断（振り分け、可否、段階の採点）は `judge` を使う。文章を生成させず、
選択肢の上の確率分布を返すので、呼び出し側は確度で分岐できる。

```bash
cat > questions.json <<'EOS'
{
  "team":   {"type": "choice",  "instructions": "どのチームが扱うべきか",
             "criteria": {"billing": "請求と返金", "support": "それ以外"}},
  "urgent": {"type": "boolean", "instructions": "至急か"},
  "severity": {"type": "score", "instructions": "深刻さ",
             "criteria": ["low", "medium", "high"]}
}
EOS
cat ticket.txt | agent-herd judge --questions questions.json --min-confidence 0.7
```

stdout は 1 行の JSON で、問いごとに `choice` / `value` / `score` と `probabilities`、
`confidence` が付く。確度が `--min-confidence` に届かない問いは `abstained` に載り、
終了コードは 1 になる。答えを黙って採用させないためで、その問いは人か上位へ返す。

ステートマシンの遷移条件、書込先の振り分け、単一基準の選別、投入時の採点は、agent-herd
自身がこの `judge` を使う。既定ではローカルの定義（`aider` / `ollama`）で回しているときだけ
使い、クラウド CLI で回しているときはそのクラウドに JSON を生成させる。クラウド CLI で作業
しながら**判定だけをローカルに逃がす**には、判定に使うモデルを設定しておく:

```bash
agent-herd config set judge.model gemma4:e4b   # どの実行でも判定はこのモデルの judge へ
agent-herd config                              # いまの設定を見る
agent-herd config set judge.model off          # judge をどの実行でも使わない
agent-herd config unset judge.model            # 既定（ローカルの定義のときだけ）に戻す
```

設定は各 PC の `~/.agents/agent-herd.yaml` に置かれる（§9.3）。agent-app の
「設定 > 実行制御」からも同じ設定を変えられる。

### 依頼に合うエージェント・モデルを選ばせる

候補が複数あって「これはローカルで足りるか、クラウドに出すべきか」を毎回決めたくないときは
`select` を使う。依頼文と候補の特性・残量・利用制限を材料に 1 件を返す。判断は本家 Jev →
`judge` → agent-audit の格付け（決定的）の順で、使える段が無くても必ず決める。

```bash
echo 'README の誤字を直して' | agent-herd select --candidate ollama --candidate claude
agent-audit ratings --period month --json > ratings.json
cat task.md | agent-herd select --purpose worker --ratings ratings.json --workload flow
agent-herd config set select.jev.api_key sk-…     # 本家 Jev を第 1 段に使う
```

stdout の `stage` がどの段で決めたかを示す。確度が `select.min_confidence` に届かない答えは
採らず次の段へ倒す（§5.7）。

### 依頼の扱いを振り分けさせる

「答えるだけでいいのか、会話の中で実行させるのか、手元のタスクやワークフローを回せば済むのか」を
送る前に決めたいときは `route` を使う。候補（タスク・ワークフロー・スキル）は呼び出し側が
JSON で渡し、判断は `select` と同じ本家 Jev → `judge` の順。決定的な段は無く、決めなければ
終了コード 1 で伝えるので、呼び出し側は従来の動き（会話で実行）へ倒す。

```bash
cat > candidates.json <<'EOF'
{"tasks": [{"id": "daily-report", "name": "日報", "description": "前日の commit から日報を書く"}],
 "skills": [{"name": "api-designer", "description": "REST API の設計"}],
 "context": {"repo": "sandbox", "readonly": false}}
EOF
echo '前月分の日報をまとめて' | agent-herd route --candidates candidates.json
# {"handling": {"choice": "task", ...}, "task": {"choice": "daily-report", ...}, "hold": true, ...}
```

`hold` が真なら「会話を送らずにそのタスクを開く」を勧めてよい（`handling` と流用先の確度が
どちらも `route.hold_min_confidence`、既定 0.75 以上）。`skills` は添えると質が上がると
判定したスキル、`routine` は定型化を勧める形かどうか（§5.8）。

### スキルを読み込む

スキルは自動選択されない。名前を明示する。

```bash
printf '%s\n' '依頼本文' \
  | agent-herd ollama --skill SKILL_NAME gemma4:e4b
```

TUI では、本文の先頭に `/SKILL_NAME` を置いてもよい。スキルが見つからなければ実行前に止まる。
探索先とスラッシュ行の規則は「スキル」と「コマンド面」を参照。

### ステートマシンを実行する

ワークフローファイルを直接指定する形と、`agent-loop.yaml` の entry（登録済みの実行設定）を
使う形がある。

```bash
agent-herd harness statemachine \
  --workflow .agents/workflows/review.yaml \
  --param target=docs/README.md

agent-herd harness statemachine --entry nightly-review
```

1 件の依頼だけをハーネスで回す場合は `run` を使う。

```bash
agent-herd harness run 'README のリンク切れを直して' \
  --acceptance 'README 内の相対リンクがすべて存在する'
```

どちらも tmux やデーモンを必要としない。最後に `RESULT {json}` を出すので、呼び出し側は
その行から完了状態を判定できる。

### 前回の続きを実行する

```bash
agent-herd -p '残っているテスト失敗を直して' --continue
agent-herd -p 'このセッションの続きを要約して' --resume SESSION_ID
```

`SESSION_ID` はログのファイル名で、`agent-herd status` や stderr の `@agent-log` で確認する。
Ollama と Aider では直近 6 メッセージを新しい依頼の前に付ける。ネイティブのセッション機能を
持つ CLI では、その CLI の再開オプションを使う。

### 長い実行を監視する

別の端末から次を実行する。

```bash
agent-herd status
agent-herd follow
```

`status` は最新ログの状態を 1 行 JSON で返す。`follow` は同じログを追尾する。CPU 推論では
最初のトークンまで数分かかることがあるため、時間だけで停止と判断しない。`state=running` かつ
`alive=true` なら処理は続いている。

設定変更の前後を比べる場合は、記録済みの依頼を再生する。

```bash
agent-herd replay --replay-limit 20 \
  --arm model=gemma4:e4b,think=off,format=json \
  --arm model=gemma4:e4b,think=on,format=json
```

再生は記録されたコマンドを実行しない。モデルへの入力と出力だけを比較する。

## 権限の選び方

| 実行方法 | モデルが使えるもの | 主な用途 |
|---|---|---|
| `agent-herd --readonly -p ...` | ツールなし | 質問、要約、判断 |
| `agent-herd exec ollama-read` | 読み取りコマンド | リポジトリ調査 |
| `agent-herd exec aider --file ...` | 指定した編集対象 | 既知ファイルの修正 |
| `agent-herd -p ...` | 定義の書き込みモード。既定は制限なしの bash | 自律実行 |
| `agent-herd ollama ...` | ツールなし | Ollama adapter の単発利用 |
| `agent-herd ollama --tools read ...` | 読み取りコマンド | adapter を直接調整した調査 |
| `agent-herd ollama --tools bash ...` | OS ユーザー権限の bash | adapter を直接調整した実行 |

`--cwd` と `--dir` は開始位置を変えるだけで、sandbox にはならない。書き込みを許したくない場合は
`--readonly` または `ollama-read` を選ぶ。

## 結果と失敗の読み方

- stdout には成果本文だけが出る。
- stderr には `@agent-usage`、`@agent-context`、`@agent-log` と診断が出る。
- Ollama のツールループが未完了で止まると、通常の本文末尾に
  `{"ok": false, "issues": [...]}` が付く。終了コードが 0 でも、この封筒があれば未完了である。
- `--format json` と `--format array` では出力形式を壊さないため封筒を付けない。外側の
  ハーネスか呼び出し側で判定する。
- `harness` は最後の `RESULT {json}` を完了契約として使う。

## よくある失敗

| 症状 | 確認すること |
|---|---|
| `connection refused` | Ollama サーバーの起動状態と `OLLAMA_HOST` |
| `model ... not found` | Ollama サーバー側で `ollama pull <model>` を実行したか |
| `504 Gateway Timeout` | `OLLAMA_HOST`、`OLLAMA_API_BASE`、`NO_PROXY`、`no_proxy` |
| `aider command not found` | Aider CLI が PATH にあるか |
| `context_exhausted` | 依頼を分割するか、`AGENT_OLLAMA_OPTIONS` の `num_ctx` を増やす |
| `no_progress` | 同じコマンドを繰り返している。依頼を小さくし、手順を具体化する |
| 長時間出力がない | `agent-herd status` で `state`、`alive`、`phase` を見る |

ここまでが利用手順である。以降は、CLI を呼ぶプログラムと実装者向けの外部仕様を固定する。

---

## CLI リファレンス

### 1. 対象と用語

このリファレンスは、`agent-herd` のコマンド名、引数、環境変数、stdout、stderr、ログ、
終了状態を規定する。設計理由や評価結果は対象外である。

| 用語 | 意味 | 例 |
|---|---|---|
| adapter | 実行プログラムへ直接つなぐ層 | `aider`、`ollama`、`edit` |
| 定義 | `agents/*.json` に書かれた起動方法 | `aider`、`ollama`、`codex` |
| profile | 同じ定義に属する用途別の起動差 | `ollama-json`、`ollama-read` |
| purpose | planner や verify などの用途名 | `--purpose verify` |

`agent-herd` が直接実行する adapter は Aider と Ollama である。Claude、Codex、Kiro、Copilot、
Cursor の定義は、それぞれの CLI を直接起動する。

実装との対応は次のとおり。

- 入口: `agentcore.herdcli`
- 定義の解決: `agentcore.agentcli`
- Ollama: `agentcore.ollama_adapter` と `agentcore.ollama_*`
- Aider: `agentcore.aider_adapter`
- ハーネス: `agentcore.harness`
- 型付きの判断: `agentcore.judge`
- 環境補完: `agentcore.hostenv`

### 2. 起動名とディスパッチ

インストールされる 3 つの名前は同じ zipapp を指す。`basename(argv[0])` による分岐は次の 1 回だけ
行う。

```text
agent-aider  ARGS...        -> aider   ARGS...
agent-ollama ARGS...        -> ollama  ARGS...
agent-herd SUBCOMMAND ...   -> SUBCOMMAND ...
```

`agent-aider X` と `agent-herd aider X`、`agent-ollama X` と `agent-herd ollama X` は同じ関数へ
同じ引数を渡す。別名で起動した場合、`--tui` などのフラグは adapter の引数として扱う。

開発木から `python3 -m agentcore.herdcli defs` のように起動した場合も、先頭の位置引数を
サブコマンドとして扱う。

### 3. コマンド一覧

| コマンド | 入力 | 用途 |
|---|---|---|
| 引数なし | TTY | 既定の `ollama` で対話する |
| `-p`、`--prompt` | 引数または stdin | 定義経由で 1 回実行する |
| `aider ARGS...` | Aider の引数 | Aider adapter を直接使う |
| `ollama ARGS...` | Ollama adapter の引数 | Ollama adapter を直接使う |
| `edit ARGS...` | edit adapter の引数 | SEARCH/REPLACE を適用する |
| `chat [NAME]` | TTY | 定義の対話コマンドを起動する |
| `defs [NAME]` | なし | 定義と実効 argv を表示する |
| `exec NAME` | stdin | 定義経由でヘッドレス実行する |
| `harness statemachine ...` | 引数 | ステートマシンを実行する |
| `harness run ...` | 引数 | 1 件の依頼をハーネスで実行する |
| `decide --decision CONTRACT` | stdin | 候補から事実を抽出し、機械が選別する |
| `judge --questions QUESTIONS` | stdin | 型付きの問いに確率つきで答える |
| `select [--candidate CLI[/MODEL]]...` | stdin | 依頼文に合うエージェント・モデルを候補から 1 件選ぶ |
| `route --candidates CANDIDATES` | stdin | 依頼文の扱い（答える / 会話で実行 / タスクやワークフローの流用 / スキル）を決める |
| `config [set KEY VALUE \| unset KEY]` | 引数 | 各 PC の設定（`~/.agents/agent-herd.yaml`）を表示・変更する |
| `status [LOG]` | JSONL ログ | 現在の状態を JSON で表示する |
| `follow [LOG]` | JSONL ログ | 状態を追尾表示する |
| `replay [PATH] ...` | JSONL ログ | 記録済みの依頼を再生する |

`aider`、`ollama`、`edit` と観測コマンドの残りの引数は adapter が解釈する。`chat`、`defs`、
`exec`、`harness`、`decide`、`judge`、`select`、`route`、`config` は、各節に記載した引数以外を終了コード 2 で拒否する。

トップレベルでは、次の 2 つも受け付ける。

| 引数 | 出力 | 終了コード |
|---|---|---:|
| `--help`、`-h`、`help` | ヘルプを stdout へ出す | 0 |
| `--version`、`version` | `agent-herd VERSION` を stdout へ出す | 0 |

adapter の引数は `agent-herd ollama --help` などで確認する。

### 4. トップレベル実行

#### 4.1 構文

```text
agent-herd [--agent NAME] [--model MODEL] [--purpose PURPOSE]
           [--readonly] [--dir PATH]
           [-p [PROMPT] | --prompt [PROMPT]]
           [--continue | --resume SESSION_ID]
```

引数なしなら対話、`-p` または `--prompt` があれば 1 回実行になる。既定の定義名は `ollama`。

| 引数 | 動作 |
|---|---|
| `-p [TEXT]`、`--prompt [TEXT]` | `TEXT` を実行する。省略時は stdin を読む |
| `--agent NAME` | 定義名または profile 名を選ぶ |
| `--model MODEL` | 定義の既定モデルを上書きする |
| `--purpose PURPOSE` | purpose と variant を解決してから起動する |
| `--readonly` | 定義の読み取り専用 argv を使う |
| `--dir PATH`、`-d PATH` | 起動前にカレントディレクトリを変更する |
| `--continue` | 直前のセッションを続ける |
| `--resume SESSION_ID` | 指定したセッションを続ける |

通常の位置引数は受け付けない。依頼本文は `-p` または stdin、バックエンドは `--agent` で渡す。
`--dir` の対象が存在しない場合と、未知のフラグを受け取った場合は終了コード 2 になる。

`-p` の直後がフラグで始まる場合、依頼本文は stdin から読む。先頭が `-` の本文は stdin で渡す。

#### 4.2 セッション継続

継続方法は定義によって異なる。

| 定義 | 継続方法 |
|---|---|
| `continue_args` または `resume_args` がある | 対象 CLI のネイティブ機能を使う |
| 上記の宣言がない Aider と Ollama | 自分の JSONL ログから直近 6 メッセージを依頼の前に付ける |

Aider と Ollama の継続は `-p` と一緒に使う。対話起動に `--continue` を付けた場合は終了コード 2。
ログまたはネイティブの再開引数が無い定義も終了コード 2 になる。新しいセッションとしての
代替実行は行わない。

ネイティブ CLI の継続用 argv は、サブコマンドの直後、通常オプションの前に挿入する。これにより
`codex exec resume --last` のようなサブコマンド形式を保持する。

### 5. 定義経由のコマンド

#### 5.1 `defs`

```text
agent-herd defs
agent-herd defs NAME [--json] [--model MODEL] [--purpose PURPOSE]
```

名前を省略すると、探索できる定義を重複なしで列挙する。名前を指定すると、定義を解決した後の
write、readonly、interactive の argv と宣言値を表示する。表示する argv は
`agentcli.headless_cmd()` と `agentcli.interactive_cmd()` の結果を使う。

`--json` の出力項目は次のとおり。

```text
name, path, requested, profile, profiles, resolved_via_variant,
headless_autonomy, readonly, relative_cost, default_model, model,
prompt_via, variants, argv_write, argv_readonly, argv_interactive, timeout
```

終了コードは 0 が表示成功、1 が定義の解決失敗、2 が引数の誤り。

#### 5.2 `exec`

```text
agent-herd exec NAME [--model MODEL] [--purpose PURPOSE] [--readonly]
                     [--file PATH]... [--read PATH]...
```

依頼本文は stdin から読む。stdin が TTY の場合は入力待ちせず、空の本文として定義を起動する。
`--file` と `--read` は繰り返し指定できる。`--purpose` が variant を選んだ場合は、解決後の
profile または定義を実行する。

`--readonly` を指定した定義が `readonly: best-effort` の場合は、保証できないことを
`@agent-note` で stderr へ出してから起動する。

終了コードは実行先の値を返す。定義を解決できない場合は 1、引数の誤りは 2、実行ファイルが
無い場合は 127。

#### 5.3 `chat`

```text
agent-herd chat [NAME] [--model MODEL]
```

既定の定義は `ollama`。定義の `interactive.command` を解決して起動する。解決した argv の先頭が
`agent-herd`、`agent-aider`、`agent-ollama` のいずれかなら同じプロセス内で起動し、それ以外は
`os.execvp` で置き換える。

`interactive` が無い定義は終了コード 1。ヘッドレス実行への切り替えは行わない。
`ready_pattern` や `busy_pattern` は tmux から対話面を使う側の宣言であり、`chat` 自体は参照しない。

#### 5.4 `harness`

```text
agent-herd harness statemachine (--workflow PATH | --entry NAME [--config PATH])
                                [--agent-cli NAME] [--model MODEL]
                                [--param KEY=VALUE]... [--input TEXT] [--dir DIR]

agent-herd harness run PROMPT...
                       [--agent-cli NAME] [--model MODEL] [--dir DIR]
                       [--acceptance TEXT]... [--judge]
```

`--agent-cli` の既定は `aider`。`statemachine` は `--workflow` と `--entry` のどちらか一方だけを
受け付ける。`--entry` は `agent-loop.yaml` の `prompts[]` からワークフロー、入力、作業場所、
定義名、モデルを読む。`--config` を省略した場合の探索順は agent-loop と同じ。

コマンドラインの値と entry の値が重なった場合は次の順で決める。

| 項目 | 優先順 |
|---|---|
| パラメータ | `--param`、`--input`、entry |
| 定義とモデル | `--agent-cli`、`--model`、entry、既定 |
| 作業場所 | `--dir`、entry の `cwd`、現在地 |

両コマンドとも tmux とデーモンを使わず、終了時に `RESULT {json}` を 1 行出す。
`agent-herd harness` は既定では台帳へ書かず、selection policy も読まない。呼び出し側は hook で
追加できる。

ハーネスは定義の `headless_autonomy` を見て実行方法を選ぶ。`single-shot` は
`read_files`、`write_files`、`run`、`final` の限定ツール契約を付ける。`tool-loop` は対象 CLI の
ツールループへ 1 回渡す。

遷移条件のうち決定的な規則で決まらないもの（`needs_llm_eval`）は `judge`（§5.5）で判定する。
使う条件は設定 `judge.model`（§9.3）による——既定（`auto`）ではローカルの定義
（`relative_cost` が 0 の `aider` / `ollama`）で回しているときだけで、モデル名を設定して
あればクラウド CLI の実行でも判定だけがそのモデルの judge へ行く。状態はその工程の出力、
モデルは設定のモデル、無ければ `--model` の指定か定義の既定。

問いの形は候補の書き方で決まる。候補すべてに `outcome`（この遷移が成立する結果の短い名前。
statemachine-use の transitions の項目）があれば、「結果はどれか」を **choice 1 問**で訊き、
選ばれた候補だけを真にする（「どれでもない」は明示の選択肢で、選ばれると全候補が偽）。
無ければ条件 1 件を boolean の問い 1 つにする。答えはそのまま `next_state.py` の `--evals`
に載る。judge が使えない（Ollama に届かない、`logprobs` を読めない）か確度が下限に届かない
場合は、従来どおり制御応答（JSON を生成させる経路）で判定し直し、証跡に
`condition_judge_fallback` を残す。

ステートの中でも同じ judge を使う。`judge:` を宣言したステート（判定だけのステート。問いと
選択肢を書き、アクションを書かない）は、judge が使えれば生成なしで選択肢の 1 語を出力にし
（`state_judge_done`）、使えなければ宣言から作った短い生成用プロンプトで回す。`output_validator`
に合わない出力は、再生成の前に決定的に直し（契約の語が第 1 行の途中・後ろの行・大文字小文字違い）、
それでも直らなければ judge に「どの契約の語か」を 1 問訊く（`contract_judge_done`、確度 0.6 以上で
採用）。`check` が落ちたときは、環境の失敗（コマンド不在・モジュール不在・権限・接続）なら
再投入を積まず、judge が使えれば「同じ作業のやり直しで直るか」を 1 問訊いて確度 0.85 以上の
「直らない」だけ止める（`check_triage`）。どの段も judge が無ければ決定的な分だけ効き、生成の
回数は増えない。

引数の誤りと未知のハーネス種別は終了コード 2。それ以外はハーネス本体の終了コードを返す。

#### 5.5 `judge`

**Calibration gate**: `tools/agent-tools/eval/readout_eval.py --calibration`は既存fixtureの
決定的checkerをoracleにし、schema_version=1のJSON台帳・reportを作る。judge APIは変更しない。
method別（logprobs / vote / text）のanswered accuracy・棄権率・threshold sweep・coverageと、
boolean/choiceのBrier・confidence bucket・ECEを出す。textは常時棄権かつBrier/ECE対象外。
問い単位とセル全問採用の集計を分け、低coverage ID・通信失敗・応答失敗・観測usageを残す。
データ不足は`insufficient_data`とし、thresholdや`judge.model`へ自動適用しない。
`--fake-run`と`--replay`も同じreport schemaを使う。手順・分母・Brier定義・archiveは
[eval README](../../tools/agent-tools/eval/README.md#judge-calibration-gatereadout_eval)を参照。
PR #862の段0 attributionは独立して利用できる。段1のjudge自動評価は対象用途のcalibrationを
人が確認してから有効化する運用を推奨する。本gateはUIや有効化設定を変更しない。

```text
agent-herd judge --questions (JSON | PATH) [--state PATH] [--model MODEL]
                 [--min-confidence 0-1] [--samples N] [--think on|off|auto]
```

状態は stdin か `--state` から読む。空なら終了コード 2。`--questions` は
`{名前: 問い}` のオブジェクトで、問いは次の 3 型。

| 型 | 問いの項目 | 答えの項目 |
|---|---|---|
| `choice` | `instructions`、`criteria`（キー → 説明。順序を保つ） | `choice`、`probabilities` |
| `boolean` | `instructions` | `value`、`probability`（yes の確率） |
| `score` | `instructions`、`criteria`（順序つき。キーが全部数ならその値、そうでなければ 0 からの順位） | `score`（確率加重）、`bucket`（最頻）、`probabilities` |

どの型も `other` に説明を書くと「どれでもない」を選択肢として足す（答えの `other` に確率が出る）。
選択肢は 26 個まで。

すべての答えに `confidence`（最大確率）、`coverage`（モデルの分布のうち選択肢に落ちた割合）、
`method` が付く。`method` は確率の出どころで、`logprobs`（1 トークン目の分布を読んだ）、
`vote`（`--samples` 回引いた票数）、`text`（本文の 1 文字を読んだだけ。`coverage` は 0）の
いずれか。`logprobs` 以外は確率を目安として扱う。

`--model` を省いたときのモデルは設定 `judge.model`（§9.3）、それも無ければ `gemma4:e4b`。

judge を組み込みで使う判定（遷移条件、書込先の `route`、単一基準の `filter`、投入時の
`assess`）は、既定ではローカルの定義（`relative_cost` が 0 の `aider` / `ollama`）で回して
いるときだけ judge へ行き、クラウド CLI の実行ではそのクラウドに JSON を生成させる。
設定 `judge.model` の値で切り替える（`agent-herd config set judge.model <値>`）。

| 値 | 判定の行き先 |
|---|---|
| `auto`（未設定） | ローカルの定義の実行だけ judge（モデルは実行の指定か定義の既定）。クラウド CLI の実行は生成経路 |
| モデル名（例 `gemma4:e4b`） | どの定義の実行でも、判定はそのモデルの judge。実行のモデルは持ち越さない |
| `off` | どの定義の実行でも judge を使わず、生成経路 |

判定は選択肢の 1 文字で済むので、クラウド CLI で回している実行ほど、指名して判定を LAN の
Ollama へ逃がす効果が大きい。judge が使えない・確度が足りないときの縮退は値に関係なく同じ
（生成経路へ倒し、証跡に残す）。

問いごとに Ollama の chat API を 1 回、`logprobs` を求めて呼ぶ。生成上限は 4 トークンで、
`--think` の既定は `off`。Ollama が `logprobs` を返さない場合、`--samples` が 2 以上なら
structured outputs でその回数引いて票数を確率にし、1 なら本文を読む。

stdout は `{"answers": {名前: 答え}, "abstained": [名前…]}` の 1 行。stderr に
`@agent-usage` を出す。終了コードは 0 が全問に答えた、1 が `abstained` あり
（`confidence` が `--min-confidence` 未満）または Ollama の失敗、2 が引数の誤り。

#### 5.6 `config`

```text
agent-herd config [--json] [--check judge]
agent-herd config set KEY VALUE
agent-herd config unset KEY
```

各 PC の設定ファイル（§9.3）を読み書きする。引数なしは設定ファイルの場所と各項目を人向けに、
`--json` は `{"path", "default_path", "judge": {"mode", "model", "error"}}` を 1 行で出す
（agent-app が読む形）。`--check judge` は判定がモデル指名（`mode` が `pinned`）で回る設定なら
終了コード 0、それ以外は 1——スキルやスクリプトが「判定を judge に任せてよいか」を確かめる口。

`set` は鍵と値を取り、`unset` は鍵だけを取る。鍵は次のとおり。

| 鍵 | 値 | 意味 |
|---|---|---|
| `judge.model` | `auto` / `off` / モデル名 | §5.5 の表のとおり。`unset` は `auto` と同じ |
| `select.jev.api_key` | API キー / `off` | 本家 Jev（TypeSafe AI）の API キー。`select`（§5.7）の第 1 段を有効にする。無ければ環境変数 `TYPESAFE_API_KEY`。`off` は環境変数があっても使わない。表示（`config` / `--json`）では伏せる |
| `select.jev.endpoint` | URL | Jev の URL。省略時 `https://api.typesafe.ai/v1/systemone`（ゲートウェイ経由なら差し替える） |
| `select.jev.model` | モデル名 | 省略時 `jev-latest` |
| `select.min_confidence` | 0〜1 | `select` で jev / judge の答えを採る確度の下限。省略時 0.6（実測前の置き値） |
| `route.min_confidence` | 0〜1 | `route`（§5.8）で答えを採る確度の下限。省略時は `select.min_confidence` と同じ |
| `route.hold_min_confidence` | 0〜1 | `route` が会話を止めてタスク / ワークフローの流用を勧める（`hold`）確度の下限。省略時 0.75（2026-09-21 の標本 40 件で、誤って止める件数が 0 になる最小の値） |
| `judge.calibration` | JSON object / unset | 人が承認したmodel・method・min_coverage・thresholds。用途filter/route/assess/transition。null・省略した用途、未測定model/method、低coverageは既存fallbackへ。未設定なら従来動作。report生成は書き換えない |


未知の鍵と引数の誤りは終了コード 2、ファイルを書けないときは 1。

#### 5.7 `select`

```text
agent-herd select [--candidate CLI[/MODEL]]... [--purpose PURPOSE] [--ratings PATH]
                  [--workload NAME] [--min-confidence 0-1] [--stages jev,judge,audit] [--json]
```

stdin の依頼文（prompt）を読み、候補（`--candidate`。省略時は解決できる定義すべてと
その既定モデル）のうち、どのエージェント・モデルに任せるかを 1 件選ぶ。判断の材料は
候補の特性（定義の相対コスト・ローカルかクラウドか・自律度、`--ratings` で渡した
`agent-audit ratings --json` の PASS 率と平均消費）、トークン量（依頼文の推定トークン数、
候補の文脈上限、`--workload` の node-budget の残量）、利用制限（node-budget 台帳の
quota 観測: 枯渇・レート制限と復帰時刻）。判断の順は次のとおりで、上の段が使えない・
決めない（確度が下限に届かない、「どれでもない」を選んだ）ときだけ次へ倒す。

| 段 | 何で決めるか | 使う条件 |
|---|---|---|
| `jev` | 本家 Jev（TypeSafe AI の System One API）に状態と choice 1 問を送る | `select.jev.api_key`（§5.6）か環境変数 `TYPESAFE_API_KEY` がある |
| `judge` | §5.5 の judge（LAN の Ollama で 1 トークン目の分布を読む） | `judge.model` が `off` でなく、指名があるか候補にローカル定義がある |
| `audit` | agent-audit の格付け → policy の順位 → 相対コストの低い順（LLM を呼ばない） | いつでも。必ず決める |

LLM に訊く前に決定的に落とせる候補は落とす: quota が枯渇・レート制限中、文脈上限が依頼文に
足りない、node-budget 超過で `on_exhausted: degrade` のときのクラウド候補（ローカル候補が
残る場合）。残りが 1 件なら LLM を呼ばない。全部落ちるときは落とさず判断に回す
（止めるかどうかは呼び出し側の契約）。

stdout は `{"selected": {"agent_cli", "model"}, "stage", "confidence", "reason", "dropped"}` の
1 行。`--json` は状態（Jev / judge に送ったもの）・各段の記録（`attempts`）・使用量も出す。
stderr に `@agent-usage`（jev と judge の合計）。終了コードは 0 が選んだ、1 が選べない
（引数の誤り以外の失敗）、2 が引数の誤り。

Python からは `agentcore.modelselect.select(prompt, candidates, purpose=…)`。エンジンは
`executionresolver.resolve_execution(..., selector=modelselect.resolver_selector(prompt))` で
差し込み、selection_policy の適格候補が複数あるときその中からだけ選ぶ（policy の外へは
出ず、決めなければ順位どおり）。agent-flow はこれを配線済みで、明示指定・run 固定の
呼び出しでは選ばない。決定には `selector`（段・確度・理由）が残り、receipt の
`execution_decision` に写る。

#### 5.8 `route`

```text
agent-herd route --candidates (JSON | PATH) [--min-confidence 0-1] [--hold-min-confidence 0-1]
                 [--stages jev,judge] [--json]
```

stdin の依頼文を読み、モデルに送る前に「どう扱うか」を決める。候補は呼び出し側が
`--candidates` の 1 枚で渡す: `tasks` / `flows`（各 `{id, name, description}`）、`skills`
（`{name, description}`）、`context`（`repo`、`attachments`、`readonly`）。各 25 件まで
（judge の choice は A〜Z で、`other` の分を空ける）。絞るのは呼び出し側で、判断の口は
渡された候補の中から選ぶだけ。

問いは 1 基準 1 問で、同じ状態（依頼の先頭 1200 字 + 候補）を先に置く。

| 問い | 型 | 答え |
|---|---|---|
| `handling` | choice | `answer`（実行せず読み取りだけで答える）/ `converse`（会話の中で実行）/ `task` / `flow`（候補の流用）/ other。`readonly` の依頼では訊かない。候補の無い `task` / `flow` は選択肢に出ない |
| `task` / `flow` | choice | 流用するならどれか。候補 + other。候補が 1 件なら「それと同じ作業か」の boolean で訊き、yes をその候補に写す |
| `skill:<name>` | boolean | そのスキルを添えると質が上がるか。候補ごとに 1 問 |
| `routine` | boolean | 入力だけ替えて繰り返す形か |

判断の順は `select`（§5.7）と同じ `jev` → `judge` で、段の試行は同じ実装を使う。
**決定的な段は無い。** `handling` を確度 `route.min_confidence`（§5.6。省略時は
`select.min_confidence`）以上で決めた段が答えを持ち、確度不足・other・本文読み
（`method` が `text`）は決めたことにしない。`judge` は `judge.model` が `off` でなければ使う
（`auto` は既定モデル。実行の定義が無いので「ローカル候補があるとき」の門は持たない）。

stdout は `{"handling", "task", "flow", "skills", "routine", "hold", "stage", "abstained",
"reason"}` の 1 行。`handling` / `task` / `flow` は `{choice, confidence, probabilities}` か
null、`skills` は yes の確率が下限以上のものを確率順に `[{name, probability}]`、`routine` は
`{value, probability, confidence}` か null。`hold` は「会話を送らずに流用を勧めてよい」で、
`handling` が task / flow を指し、その確度と流用先の確度がどちらも `route.hold_min_confidence`
（省略時 0.75）以上のときだけ真。`abstained` は確度が足りず決めていない問い（決めた上での
other / no は入れない）。`--json` は状態・問いの名前・各段の記録（`attempts`）・使用量も出す。
stderr に `@agent-usage`。終了コードは 0 が扱いを決めた（`stage` あり）、1 が決めず（か失敗）、
2 が引数と候補の誤り。

Python からは `agentcore.route.route(prompt, candidates)`。呼び出し側（agent-app の会話画面）は
`stage` が null なら従来の動き（会話で実行、スキルは文字列の一致）へ倒す。

### 6. 定義と profile

同梱する定義は `aider`、`ollama`、`claude`、`codex`、`kiro`、`copilot`、`cursor`、
`vscode-copilot` の 8 件。ローカル実行系の `aider` と `ollama` は `relative_cost: 0`。

`aider` は対象ファイルが分かっている編集に使う。`ollama` は bash を使う探索と実行に使う。
planner、verify、split など 15 の purpose は、どちらを基準にしても同じ Ollama profile へ解決する。

| profile | 互換名 | 既定モデル | write | readonly | 対話 |
|---|---|---|---|---|---|
| base | `ollama` | `gemma4:e4b` | bash、最大 12 ラウンド | ツールなし | TUI、think on |
| `json` | `ollama-json` | `gemma4:e4b` | JSON オブジェクト | 同左 | なし |
| `list` | `ollama-list` | `gemma4:e4b` | 文字列の JSON 配列 | 同左 | なし |
| `list-thinking` | `ollama-list-thinking` | `gemma4:e4b` | text、think on、temperature 0 | 同左 | なし |
| `read` | `ollama-read` | `gemma4:e4b` | read ツール、最大 30 ラウンド | ツールなし | なし |
| `verify` | `ollama-verify` | `gemma4:12b` | JSON オブジェクト | 同左 | なし |

base の variant は planner、evaluator、filter、judge、reduce、extract、plan、review、prioritize、
route、adjudicate、assess を `ollama-json`、split を `ollama-list`、retrieve を `ollama-read`、
verify を `ollama-verify` へ振り分ける。`aider` も同じ 15 件を宣言する。

`json` profile は split を `ollama-list`、retrieve を `ollama-read` へ振り分ける。
`verify` profile は split を `ollama-list` へ振り分ける。base と `read` profile の
`--command-timeout` は 900 秒、`verify` の `--stall-timeout` は 180 秒。

profile は base の設定を継ぐが、`interactive` と `variants` は継承しない。`env` は base に重ね、
ほかの値は profile に宣言があれば置き換える。空のリストも置き換えとして扱う。

profile と同じ名前の実ファイルがある場合は実ファイルを優先する。たとえば
`agents/ollama-list.json` があれば、`ollama` の `list` profile より先に解決する。

`ollama-list` のような互換名を解決した結果では、定義名は `ollama`、profile は `list` になる。
台帳へ記録する `agent_cli` も `ollama` に正規化する。purpose は台帳の `purpose` または
`operation_class` に記録する。

### 7. 対話ペインからの定型処理

agent-loop と agent-dashboard は、`interactive` を宣言した定義を tmux ペインで起動する。
対話面が無い定義だけをヘッドレスのハーネスへ送る。`headless_autonomy` はこの判定に使わない。

ステートマシンを開始するとき、ペインへ送る先頭行は次のとおり。

| 対話面 | 送る先頭行 |
|---|---|
| agent-herd の共通 TUI | `/sm WORKFLOW [--param KEY=VALUE]` |
| クラウド CLI | `statemachine-use スキルでNAMEステートマシンを実行して` と入力条件 |

この文字列は `agentcore.loopentry.statemachine_command` が組み立てる。共通 TUI の `/sm` は本文の
先頭行に置く。前に共通指示などを追加すると、ルータはステートマシンとして解釈しない。

### 8. コマンド面

#### 8.1 先頭のスラッシュ行

本文の先頭から連続する `/name [args]` の行をコマンドとして読む。名前は
`^[a-z0-9][a-z0-9._-]*$`。空行があれば、そこから後ろは通常の本文になる。起動する profile や
ツールを決めるため、ランチャは argv を組み立てる前にこの部分を解析する。

コマンドは次の 4 種類。

| 種類 | 例 | 処理するもの |
|---|---|---|
| セッション操作 | `/model`、`/tools`、`/status`、`/help`、`/quit` | TUI |
| 実行形 | `/ask`、`/find`、`/edit`、`/sm` | agentcore のルータ |
| purpose | `/verify`、`/judge` | command 宣言 |
| スキル | `/wiki-use` | `SKILL.md` |

実行形の割り当ては次のとおり。

| コマンド | 実行方法 |
|---|---|
| `/ask TEXT` | ツールを使わずに推論する |
| `/find TEXT` | read ツールで調べる |
| `/edit TEXT` | 編集ハーネスを使う |
| `/sm NAME [--param KEY=VALUE]` | ファイルまたは entry のステートマシンを使う |

`/ask`、`/find`、`/edit` の引数は依頼本文の先頭へ戻す。`/sm` の引数はルータが実行条件として
消費する。

#### 8.2 purpose の宣言

purpose の宣言ファイルは次の順に探索し、最初に見つかった同名ファイルを使う。

```text
$AGENT_COMMANDS_DIR
PROJECT/.agents/commands/
~/.agents/commands/
同梱ディレクトリ
```

frontmatter は 1 行の `key: value` だけを受け付ける。

| キー | 意味 |
|---|---|
| `description` | `/help` と補完に出す説明 |
| `agent` | 定義または profile |
| `model` | purpose の既定モデル |
| `tools` | `[]`、`[read]`、`[bash]` のいずれか |
| `output` | `json` などの出力形式 |
| `argument-hint` | `/help` に出す引数の形 |
| `system-template` | system prompt のテンプレート |
| `instance-template` | 最初の user message のテンプレート |
| `observation-template` | ツール出力のテンプレート |
| `format-error-template` | 形式エラー時の再指示テンプレート |

テンプレートのパスは宣言ファイルからの相対パス。置換対象は `{task}`、`{cwd}`、`{toolset}`、
`{done_marker}`、`{exit_code}`、`{output}`、`{read_commands}`、`{read_git_subcommands}`。
ほかの `{...}` はそのまま残す。

スキル、セッション操作、実行形、purpose は同じ名前空間を使う。同名を複数の種類に置かない。
同梱する purpose 宣言は `edit.md`。

先頭のスラッシュ名がどの種類にも無い場合は、推論を始めずにエラーにする。`/tmp を消して` の
ような通常の文を先頭から送りたい場合は、先頭に空行を 1 つ置く。

### 9. 環境

#### 9.1 接続情報の補完

起動時に `agentcore.hostenv` が次の処理を 1 回行う。

1. `OLLAMA_HOST`、`OLLAMA_API_BASE`、`NO_PROXY` または `no_proxy` がそろっていれば
   `~/.profile` を読まない。
2. 足りない場合は `~/.profile` を子プロセスで評価し、`OLLAMA_*`、`AGENT_OLLAMA_*`、
   `NO_PROXY`、`no_proxy` だけを取り込む。現在の環境変数を優先する。
3. `OLLAMA_HOST` と `OLLAMA_API_BASE` を相互に補完する。スキームが無ければ `http://` を付ける。
4. Ollama のホストを `NO_PROXY` と `no_proxy` の両方へ追加し、2 つを同じ値にする。

`~/.profile` の評価に失敗した場合は現在の環境で続行する。評価用プロセスの stdin は閉じる。

#### 9.2 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `OLLAMA_HOST` | なし | Ollama の接続先 |
| `OLLAMA_API_BASE` | なし | Aider と LiteLLM の接続先 |
| `NO_PROXY`、`no_proxy` | なし | プロキシを通さないホスト |
| `OLLAMA_TIMEOUT` | `600` 秒 | HTTP 全体の上限 |
| `AGENT_OLLAMA_CONNECT_TIMEOUT` | `120` 秒 | 応答ヘッダを待つ時間 |
| `AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT` | `0` | 最初のトークンまで。0 は無制限 |
| `AGENT_OLLAMA_STALL_TIMEOUT` | `180` 秒 | decode 中の無進捗時間 |
| `AGENT_OLLAMA_META_TIMEOUT` | `3` 秒 | `/api/ps` と `/api/show` の問い合わせ |
| `AGENT_OLLAMA_THINK` | モデル既定 | `on`、`off`、`prompt` |
| `AGENT_OLLAMA_OPTIONS` | なし | API の `options` に渡す JSON |
| `AGENT_OLLAMA_KEEP_ALIVE` | なし | API の `keep_alive` |
| `AGENT_OLLAMA_SYSTEM_PROMPT` | なし | system prompt の差し替え |
| `AGENT_OLLAMA_LOG_DIR` | `~/.agents/logs/ollama` | JSONL ログのディレクトリ |
| `AGENT_OLLAMA_SKILLS_DIR` | なし | 追加のスキル探索先。`:` 区切り |
| `AGENT_OLLAMA_HISTORY` | 実装既定 | TUI の履歴ファイル |
| `AGENT_OLLAMA_NO_RICH` | なし | `1` で rich を使わない |
| `AGENT_OLLAMA_NO_READLINE` | なし | `1` で readline を使わない |

`AGENT_OLLAMA_*` の名前は `agent-herd`、`agent-aider`、`agent-ollama` のどの起動名でも同じ。

#### 9.3 設定ファイル

環境変数では届かない・残らない設定は、各 PC の `~/.agents/agent-herd.yaml`（`.yml` / `.json`
も可。見つかった最初の 1 つを読む）に置く。`AGENT_PROJECT_AGENTS_HOME` で `~/.agents` を
差し替えられる（定義の探索と同じ変数）。

```yaml
judge:
  model: gemma4:e4b   # auto（省略）/ off / モデル名
select:
  jev:
    api_key: sk-…     # 本家 Jev の API キー（無ければ環境変数 TYPESAFE_API_KEY。off で使わない）
    endpoint: https://api.typesafe.ai/v1/systemone
    model: jev-latest
  min_confidence: 0.6
```

書くのは `agent-herd config`（§5.6）か agent-app の「設定 > 実行制御」。手で書いてもよい
（裸の `off` は YAML では真偽値になるが、同じ意味に読む）。壊れたファイルは「設定なし」として
動き、`agent-herd config` が理由を出す。

### 10. stdout、stderr、終了コード

#### 10.1 共通出力

- stdout には成果本文だけを出す。
- stderr には診断と計測を出す。
- adapter の終了コードは入口で変換しない。
- Ollama のログは `~/.agents/logs/ollama/` に JSONL で追記する。

stderr の機械可読行は次のとおり。

| 接頭辞 | 内容 |
|---|---|
| `@agent-usage` | 実行中に消費した `tokens_in` と `tokens_out` の累計 |
| `@agent-context` | 現在の文脈使用量、上限、比率、算出元 |
| `@agent-note` | 未完了や保証範囲についての注記 |
| `@agent-log` | JSONL ログのパス |
| `@agent-policy` | Aider に適用した policy ID とハッシュ |

エラー分類は `[agent-error:env]` または `[agent-error:transient]` の形で stderr へ出す。
接続不能、モデル未取得、スキル未配布、ツール不整合、文脈不足は `env`。通信断と生成中の
stall は `transient`。

#### 10.2 入口の終了コード

| 終了コード | 意味 |
|---:|---|
| 0 | 表示成功、または実行先が 0 を返した |
| 1 | 定義を解決できない、対話面が無い |
| 2 | 引数またはコマンドの誤り |
| 127 | 実行ファイルが見つからない |

Ollama のツールループでは、未完了でも途中成果を返すため終了コードが 0 になる場合がある。
呼び出し側は「Ollama の終了状態」で定める封筒も確認する。

### 11. Ollama adapter

#### 11.1 構文とモード

```text
agent-herd ollama [OPTIONS] MODEL
agent-ollama [OPTIONS] MODEL
```

依頼本文は stdin から受け取る。

| モード | 指定 | 動作 |
|---|---|---|
| 単発 | 指定なし | ツールを使わずに 1 回生成する |
| bash loop | `--tools` または `--tools bash` | bash を使うツールループ |
| read loop | `--tools read` | 読み取りコマンドだけを使うツールループ |
| TUI | `--tui` | 対話する |
| 状態 | `--status [LOG]` | 1 行 JSON を返す |
| 追尾 | `--follow [LOG]` | JSONL ログを追尾する |
| 文脈照会 | `--context MODEL` | 推論せず文脈上限を調べる |
| 再生 | `--replay [PATH]` | 記録済みの依頼を再生する |

#### 11.2 オプション

| オプション | 既定 | 動作 |
|---|---|---|
| `--model MODEL` | 位置引数 | モデルを指定する |
| `--tools [bash\|read]` | `bash` | ツールループを有効にする |
| `--format json\|array\|text` | `text` | 出力文法を制限する |
| `--think on\|off\|prompt` | 環境またはモデル既定 | 思考モードを選ぶ |
| `--max-rounds N` | `12` | ツールループの最大ラウンド |
| `--command-timeout SEC` | `300` | 1 コマンドの上限 |
| `--stall-timeout SEC` | `180` | decode 中の無進捗上限。0 は無効 |
| `--first-token-timeout SEC` | `0` | 最初のトークンまでの上限。0 は無制限 |
| `--context-limit N` | 自動 | 文脈上限を明示する |
| `--context-warn-pct P` | `90` | 文脈警告の割合。0 は無効 |
| `--skill NAME` | なし | スキルを読み込む。複数指定可 |
| `--no-skills` | 無効 | 先頭スラッシュ行によるスキル展開を止める |
| `--cwd DIR` | 現在地 | ツールの開始位置を変える |
| `--log PATH` | ログディレクトリ | ログの置き場を変える |
| `--no-log` | 無効 | ログを書かない |
| `--arm SPEC` | なし | 再生条件を追加する。複数指定可 |
| `--replay-limit N` | 実装既定 | 再生件数を制限する |
| `--replay-out PATH` | ログディレクトリ | 再生結果の JSONL を書く |

`--format json` はトップレベルをオブジェクトに制限する。配列が必要なら `--format array` を使う。
`--think on` と `--format json` を同時に指定した場合は think を off にする。`--think prompt` は
system prompt の先頭に `<|think|>` を置くため、この強制 off の対象外。

#### 11.3 上限とタイムアウト

| 対象 | 既定 | 変更方法 |
|---|---:|---|
| ツールループ | 12 ラウンド | `--max-rounds` |
| read profile | 30 ラウンド | `--max-rounds` |
| ツール 1 コマンド | 300 秒 | `--command-timeout` |
| ツール出力の取り込み | 4,000 字（超えた分は全文をファイルへ置く。下記） | 変更不可 |
| 1 ラウンドの生成 | 4,096 トークン | 変更不可 |
| 規約外応答の再指示 | 2 回 | 変更不可 |
| ツール拒否 | 3 回目で停止 | 変更不可 |
| 同じコマンド、終了コード、出力 | 3 回連続で停止 | 変更不可 |
| 文脈の予備 | 512 トークン | 変更不可 |
| heartbeat | 5 秒 | 変更不可 |

最初のトークンまでの待機は既定で無制限。生成が始まった後に `stall-timeout` のあいだ進捗が
無ければ停止する。

応答ヘッダを `AGENT_OLLAMA_CONNECT_TIMEOUT` の時間内に受け取れない場合は `/api/version` で
サーバーを確認する。サーバーが生きていれば queue として待ち続け、30 秒ごとに再確認する。
3 回続けて生存確認に失敗した場合だけ停止する。

文脈上限は `--context-limit`、request の `num_ctx`、`/api/ps`、`/api/show` の順で決める。
どれも取得できなければ、使用量だけを表示し、割合による警告と停止は行わない。

#### 11.4 ツール

| セット | 許可範囲 |
|---|---|
| `bash` | `bash -lc` にそのまま渡す。OS ユーザー権限の範囲で制限しない |
| `read` | 読み取りコマンドと git の参照系 subcommand。シェルを介さず argv で実行する |
| `edit` | 未実装。指定するとエラー |

`read` は引用外のシェルメタ文字、書き込みを伴う `find` の述語、未知のコマンドを拒否する。
拒否したコマンドは実行ログの `tool_exec` に記録しない。`--cwd` は開始位置の指定であり、
sandbox ではない。

ツール出力が上限（4,000 字。残り文脈が少ないときはそれより小さい）を超えたときは、全文を
ログと同名のディレクトリ `~/.agents/logs/ollama/<ログ名>.results/round-NNN.txt` に保存し、
モデルには先頭と末尾、全文の文字数と行数、ファイルの所在を渡す。モデルは `grep -n` /
`head -n` / `tail -n +K` で必要な部分だけを読み直せる（`read` セットでも使える）。
`--no-log` のときは OS の一時ディレクトリに置く。上限内の出力は何も保存しない。

#### 11.5 スキル

スキルは `--skill NAME` または本文先頭のスラッシュ行で指定する。次の順に `SKILL.md` を探す。

```text
~/.agents/skills
$AGENT_OLLAMA_SKILLS_DIR の各ディレクトリ
~/.claude/skills
```

frontmatter を除いた本文を 1 回だけプロンプトへ加える。本文の前には「このスキルはユーザーが
明示的に呼び出しました」という 1 行を置く。参考資料として読み流さず、今回の依頼へ適用させる
ためである。指定したスキルが無ければ `env` エラー。`{skill_dir}` を使うスキルは同梱スクリプトの
実行を前提とするため、`read` ツールとの組み合わせを拒否する。スキル一覧を system prompt へ
常時加える処理は行わない。

TUI では、本文が `/skill-name` の 1 行だけ（引数も後続の本文も無い）の入力をモデルへ送らず、
次の依頼へ持ち越す。持ち越したスキル呼び出しは次の依頼の先頭へ連結してから 1 回で実行する。
空の仕事をモデルへ投げないためで、スキルを先に適用してから依頼を書く使い方と、セッション開始
コマンドを別に送る呼び出し側（agent-app の開始スキル）が、状態を持たない aider backend でも
同じ 1 回の実行として成立する。持ち越し中は `次の依頼へ適用するスキル: /name` を表示する。

#### 11.6 終了状態

| 状態 | 完了 | 意味 | 分類 |
|---|:---:|---|---|
| `done` | yes | `TASK_COMPLETE` を確認した | なし |
| `no_command` | no | ツール呼び出し規約に合わない応答が続いた | なし |
| `max_rounds` | no | 最大ラウンドに達した | なし |
| `no_progress` | no | 同じコマンドと結果が 3 回続いた | `env` |
| `context_exhausted` | no | 最低限のツール結果も文脈に入らない | `env` |
| `tool_denied` | no | ツール拒否が 3 回に達した | `env` |

`done` 以外でも最後の本文は stdout へ返す。通常の text 出力では、その末尾に次の封筒を加える。

```json
{"ok": false, "issues": ["未完了の理由"]}
```

未完了でも終了コードは 0。`--format json` と `--format array` では出力契約を壊すため
封筒を加えない。

#### 11.7 ログと再生

実行中は run、skill、LLM、message、tool、context、error、end のイベントを JSONL へ追記する。
ログ書き込みと表示の失敗は推論を停止させない。ツール出力を外へ置いたラウンドの `tool_result`
には、保存先 `spill` と全文の文字数 `output_chars_full` が付く（`output_chars` はモデルに
渡した文字数）。

`status` はログ末尾から `state`、`phase`、`round`、`last_progress_at`、`tokens_per_sec`、
`context_*` を組み立てる。`follow` と TUI も同じイベントを表示する。

`replay` は各ログの最初の user message を使う。`--arm` には `model`、`think`、`format`、`label`、
`repeat` を指定できる。出力は腕ごとの空応答率、失敗率、所要時間と、腕をまたいだ一致率。
1 件ごとの結果は `--replay-out` の JSONL へ書く。

再生時はツールを与えず、記録されたコマンドも実行しない。1 種類の出力しか得られなかった依頼は
一致率の母数に入れない。正解ラベルとの一致率は計算しない。

### 12. Aider adapter

#### 12.1 構文

```text
agent-herd aider [AIDER_ARGS...]
agent-aider [AIDER_ARGS...]
```

adapter 専用のオプションを取り除いた後、残りを Aider CLI へ渡す。

| オプション | 動作 |
|---|---|
| `--tui` | 共通 TUI を Aider バックエンドで開く |
| `--agent-policy ID` | system prompt の先頭に固定 policy を加える |
| `--agent-num-ctx N` | model settings の `num_ctx` を設定する |
| `--agent-num-predict N` | model settings の `num_predict` を設定する |

使用できる policy ID は `gemma4-e4b-reliability-v1`、対象モデルは
`ollama_chat/gemma4:e4b`。未知の ID、対象外モデル、外部の `--model-settings-file` との併用は
起動前に `env` エラーにする。

adapter は一時的な analytics log からトークン数を読み、`@agent-usage` を stderr へ出す。
policy を適用した場合は `@agent-policy id=ID sha256=HASH` も出す。一時ファイルは実行後に削除する。

Aider の共通 TUI は 1 入力ごとに 1 回 `aider --message` を実行する。会話履歴は自動で積まない。
`/sm` と `/edit` はハーネスへ渡す。`/ask`、`/find`、未知のスラッシュ名はエラー。

モデル別の policy または `--agent-num-*` を付けて起動した TUI では、`/model` で別モデルへ
切り替えない。設定対象から外れるためエラーになる。

#### 12.2 `edit` adapter

```text
agent-herd edit --model MODEL --file PATH [--read PATH]...
                [--message TEXT] [--dir DIR] [--readonly] [--think on|off]
```

`edit` は SEARCH/REPLACE ブロックを生成し、完全一致、先頭空白の差、`...` 中略の順で適用する。
ファイル探索、シェル、テスト実行は行わない。`--model` と 1 件以上の `--file` が必須。
`--file` が存在しない場合は新規作成、`--readonly` は dry-run。`--message` を省略した場合は
stdin から依頼本文を読む。

この adapter は比較評価用で、同梱の `agents/*.json` には定義を置かない。定義経由で通常の編集を
行う場合は `aider` を使う。

### 13. 配布

```text
bash tools/agent-tools/install.sh [--only agent-herd]
                                  [--prefix DIR]
                                  [--with-rich]
```

既定の出力は次のとおり。

```text
~/.local/bin/agent-herd
~/.local/bin/agent-aider
~/.local/bin/agent-ollama
```

`agent-aider` と `agent-ollama` は `agent-herd` へのハードリンク。ハードリンクを作れない
ファイルシステムではコピーし、警告を出す。インストーラは毎回 3 つを同じ版へ更新する。
旧版の `agent-opencode` が残っている場合は削除する。

`--only agent-herd` は agent-project などを入れずに実行系だけを置く。`--prefix` は出力先を変える。
`--with-rich` は rich を zipapp に同梱する。rich の取得に失敗した場合は ANSI 表示で続行する。

インストール後は `agent-herd --help` と `agent-ollama --help` を実行し、zipapp と起動名の分岐を
確認する。agent-loop は別の zipapp なので、agentcore の契約を更新した場合は agent-loop も
同時に入れ直す。


### Judge calibrationの暫定運用

2026-09-20の実測（12セル×3回）と用途別設定の根拠・適用状態は
[calibration適用記録](../plans/2026-09-20-judge-calibration-application.md)を参照。
`judge.calibration`はconsumerの採用gateで、standalone judge APIは変更しない。
`agent-herd config --json`のcalibration / calibration_errorで設定・不備を確認できる。

## Selectorのoutcome qualification（eval専用）

`tools/agent-tools/eval/model_selection_eval.py` で、同一prompt/candidateの既知outcomeを使い、selector / audit / 最安 / 高格付け / fixture oracleを比較する。PASSは既存verification receipt正典、completionは固定checkpointの達成率（eval専用）。stage・confidence・horizon別集計と0.5〜0.9のthreshold sweepをJSONへ保存する。runtime・本番config・既定0.6は変更しない。

`--selfcheck` はfake応答とoutcomeだけで検証し、`--real-run` は明示した課題を複数の既存Agent CLIで隔離実行してreceiptを収集する。付属9課題のoutcomeは合成値であり実測ではない。詳細は[評価仕様とreal-run手順](../../tools/agent-tools/eval/MODEL_SELECTION.md)を参照。
