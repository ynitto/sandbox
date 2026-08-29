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
| `status [LOG]` | JSONL ログ | 現在の状態を JSON で表示する |
| `follow [LOG]` | JSONL ログ | 状態を追尾表示する |
| `replay [PATH] ...` | JSONL ログ | 記録済みの依頼を再生する |

`aider`、`ollama`、`edit` と観測コマンドの残りの引数は adapter が解釈する。`chat`、`defs`、
`exec`、`harness` は、各節に記載した引数以外を終了コード 2 で拒否する。

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

引数の誤りと未知のハーネス種別は終了コード 2。それ以外はハーネス本体の終了コードを返す。

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
| ツール出力の取り込み | 4,000 字 | 変更不可 |
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

#### 11.5 スキル

スキルは `--skill NAME` または本文先頭のスラッシュ行で指定する。次の順に `SKILL.md` を探す。

```text
~/.agents/skills
$AGENT_OLLAMA_SKILLS_DIR の各ディレクトリ
~/.claude/skills
```

frontmatter を除いた本文を 1 回だけプロンプトへ加える。指定したスキルが無ければ `env` エラー。
`{skill_dir}` を使うスキルは同梱スクリプトの実行を前提とするため、`read` ツールとの組み合わせを
拒否する。スキル一覧を system prompt へ常時加える処理は行わない。

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
ログ書き込みと表示の失敗は推論を停止させない。

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
