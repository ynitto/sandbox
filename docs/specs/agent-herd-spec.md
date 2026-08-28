# agent-herd 仕様書 — 入口の綴り・実行系の契約・終了状態

> 設計の「なぜ」は [`docs/designs/agent-herd-design.md`](../designs/agent-herd-design.md)、
> CLI 定義の共通契約は [`docs/specs/agent-cli-spec.md`](./agent-cli-spec.md)、
> 利用手順は `tools/agent-tools/README.md`。
> 対象実装: `tools/agent-tools/agentcore/agentcore/` の `herdcli.py` / `hostenv.py` /
> `aider_adapter.py` / `ollama_adapter.py` /
> `ollama_{loop,context,events,skills,tui,replay}.py` / `harness/` /
> `tools/agent-tools/install.sh`。
> 位置づけ: **本書が綴りの正典**。設計書は判断の理由を、本書は「打つと何が起きるか」を固定する。
> 実装と食い違ったら、どちらかが間違っているので直すまで作業を止める。

---

## 1. 何であるか

`agent-herd` は **LAN 上の ollama を動かす実行系の唯一の入口**である。実体は agentcore を
同梱した zipapp 1 ファイルで、`agent-aider` / `agent-ollama` は同じ
ファイルへのハードリンクとして置かれる。
opencode は同梱を外した（このハードで成立しないことが実測済み。設計 2026-08-27 §6。
使う人は `agents/opencode.json` を自分で置けば定義経由で呼べるが、adapter が持っていた
usage 実測と preflight は付かない）。

**クラウド CLI（claude / codex / kiro / copilot / cursor）はこの入口を通らない。**
それらの `agents/*.json` の `command` は素の CLI を指したままである（設計 §3.8）。

---

## 2. ディスパッチ

```
basename(argv[0])       解決されるサブコマンド        残りの引数
  agent-aider       →   aider                        argv 全部
  agent-ollama      →   ollama                       argv 全部
  agent-herd        →   argv[0] をサブコマンドとして  argv[1:]
  それ以外           →   argv[0] をサブコマンドとして  argv[1:]
```

分岐はこの 1 回だけである。`agent-aider X` と `agent-herd aider X` は**同じ関数に同じ引数で**
届く（テスト: `test_herdcli.Argv0DispatchTests`）。

「それ以外」があるのは、開発木から `python3 -m agentcore.herdcli defs` のように叩くため。

**別名（argv[0]）はフラグより先に決まる。** `agent-ollama --tui` の `--tui` は adapter の
ものであって入口のものではない——ここが逆転すると、adapter だけが知っているフラグを
入口が「受け取りません」で落とす。入口自身のフラグ（§3.3）が効くのは
`agent-herd` として呼ばれたときだけである。

---

## 3. サブコマンド

**サブコマンドは adapter の名前であって定義の名前ではない。** これが名前空間の規約である。

| 綴り | 実体 | 引数の扱い |
|---|---|---|
| `aider …` | `aider_adapter.main` | adapter へ素通し |
| `ollama …` | `ollama_adapter.main` | adapter へ素通し |
| `chat [<cli>] [--model M]` | `herdcli.cmd_chat` | 閉じている（下記以外は拒否） |
| `defs [<名前>] [--json] [--model M] [--purpose P]` | `herdcli.cmd_defs` | 同上 |
| `exec <cli> [オプション]` | `herdcli.cmd_exec` | 同上 |
| `harness statemachine --workflow PATH` | `agentcore.harness.statemachine.cmd_statemachine` | 閉じている |
| `harness run PROMPT…` | `agentcore.harness.toolloop.cmd_run` | 閉じている |
| `status [LOG]` | `ollama --status` の別名 | 素通し |
| `follow [LOG]` | `ollama --follow` の別名 | 素通し |
| `replay [PATH] …` | `ollama --replay` の別名 | 素通し |

`status` / `follow` / `replay` は**別名であって第 2 実装ではない**。`agent-ollama --status`
の綴りも残るので、既存の手順書は書き換え不要。

### 3.1 引数面が「素通し」か「閉じている」か

- **adapter サブコマンド**（`aider` / `ollama` / 観測別名）は引数を 1 つも解釈
  せず adapter へ渡す。adapter 側の `--help` がその面の正典。
- **入口が持つサブコマンド**（`chat` / `defs` / `exec`）は引数面が閉じている。未知のオプションは
  終了コード 2 で拒否する（黙って下へ流さない——流すと「効いたつもり」が起きる）。

### 3.2 入口自身のオプション

| 綴り | 動作 | 終了コード |
|---|---|---|
| `--help` / `-h` / `help` | 一覧を stdout へ | 0 |
| `--version` / `version` | `agent-herd <agentcore.__version__>` を stdout へ | 0 |

各 adapter の詳細は `agent-herd ollama --help`（= `agent-ollama --help` と同一本文）。

### 3.3 トップレベルのフラグ（クラウド CLI と同型の入口）

設計: [2026-08-27 クラウド CLI を正とした入口の再構成](../plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md) §3.1。

**引数なしなら対話、`-p` なら非対話 1 回。** claude / codex と同じ形である。先頭がフラグ
（または引数なし）のとき、`agent-herd` は自分への指定として読む。

| 綴り | 意味 | 落ちる先 |
|---|---|---|
| （引数なし） | 対話（TUI）で開く | `interactive_cmd` — `chat` と同じ |
| `-p ["…"]` / `--prompt` | 1 回だけ実行する。値を省くと本文は stdin | `headless_cmd` — `exec` と同じ |
| `--agent <名前>` | バックエンド。**`agents/<名前>.json` の定義名**（`ollama-json` のような profile 綴りも解ける） | `load_cli` |
| `--model <モデル>` | モデル | 同上 |
| `--purpose <用途>` | 用途の 1 語。起動形の調停は §13 のルータ | `slashroute.resolve` |
| `--readonly` | 読み取り専用（対話でも効く） | `*_cmd(readonly=True)` |
| `--dir <パス>` / `-d` | 作業ディレクトリ（このプロセスの cwd） | `os.chdir` |

**新しい実行経路は足していない。** ここがやるのは、既に `chat` と `exec` が持っている
当て先へフラグを翻訳することだけである。`--agent` が定義名を取ることで、「adapter 名」
という概念が外から消える。

位置引数は受け取らない（本文は `-p` か stdin）——受け取ると `agent-herd ollama` が
「ollama という本文」と紛れる。未知のフラグは終了コード 2 で拒否する。

**`--continue` / `--resume` はまだ受け取らない。** ローカルの単発実行は毎回新しい
プロセスで、「継続」の実体（材料の再構築か CLI 側のセッション機能か）を定義がどう
宣言するかが未決だから（設計 §4・§11 未決 1）。綴りだけ通して黙って無視すると
「継続したつもりで毎回まっさらに走る」になるので、受け取った時点で明示エラーにする。

---

## 4. 定義経由の 2 系統

| | adapter サブコマンド | `exec` |
|---|---|---|
| 綴り | `agent-herd ollama --format json gemma4:e4b` | `agent-herd exec ollama-json --model gemma4:e4b` |
| argv を決めるもの | 打った人 | `agentcore.agentcli`（定義） |
| 定義を読むか | 読まない | 読む（`variants` / `readonly` が効く） |
| 名前の空間 | `aider` / `ollama` | `agents/*.json` の全定義 |

### 4.0 用途別の起動形は profile であって別エージェントではない

`agents/*.json` は **1 ファイル = 1 エージェント**である。同梱定義は **8 件**
（`aider` / `ollama` / `claude` / `codex` / `kiro` / `copilot` / `cursor` /
`vscode-copilot`）で、用途で使い分ける起動差は定義の中の `profiles` に置く:

```
$ agent-herd defs
解決できる定義:
  aider
  claude
  …
  ollama    profiles: json, list, list-thinking, read, verify
```

`ollama-list` のような従来の綴りはそのまま解決でき（`base=ollama / profile=list`）、
返る spec の `name` は正典の `"ollama"`、起動差は `profile` が持つ。**台帳と格付けへ書く
`agent_cli` は常に正典名**（`agentcli.canonical_name()` を通す）——用途の次元は
`operation_class` / `purpose` の列が持っており、`agent_cli` へ畳み込むと同じ次元の二重表現に
なって 1 実行系の実測が偽の候補へ割れる。

profile は base を継ぐが、**`interactive` と `variants` は継承しない**（継承すると対話面を
持たない役割に base の TUI が生え、agent-dashboard の実行経路が変わる）。`env` は base へ
重ね、他は宣言があれば置き換える（`[]` の宣言も「置き換え」として扱う）。実ファイルが profile
より優先されるので、独立させたくなったら `ollama-list.json` を置けばよい。

#### ollama の起動形の割当

| 起動形 | 従来の綴り | 既定モデル | autonomy | think | write モード | readonly モード | 対話 |
|---|---|---|---|---|---|---|:-:|
| base | `ollama` | `gemma4:e4b` | tool-loop | `off`（対話だけ `on`） | `--tools bash --max-rounds 12 --command-timeout 900` | 道具なし | ✓（`--tui`） |
| `json` | `ollama-json` | `gemma4:e4b` | single-shot | `off` | `--format json`、道具なし | 同左 | — |
| `list` | `ollama-list` | `gemma4:e4b` | single-shot | `off` | `--format array`、道具なし | 同左 | — |
| `list-thinking` | `ollama-list-thinking` | `gemma4:e4b` | single-shot | `on` + `temperature 0` | `--format` なし・道具なし | 同左 | — |
| `read` | `ollama-read` | `gemma4:e4b` | tool-loop | `off` | `--tools read --max-rounds 30 --command-timeout 900` | 道具なし | — |
| `verify` | `ollama-verify` | `gemma4:12b` | single-shot | `off` | `--format json --stall-timeout 180`、道具なし | 同左 | — |

base の `variants` は 12 用途（`planner` `evaluator` `filter` `judge` `reduce` `extract`
`plan` `review` `prioritize` `route` `adjudicate` `assess`）を `ollama-json` へ、`split` を
`ollama-list` へ、`retrieve` を `ollama-read` へ、`verify` を `ollama-verify` へ振り替える。
`json` profile は `split` / `retrieve` を、`verify` profile は `split` を持つ（variants は
継承しないので、必要な profile だけが自分で宣言する）。

**全起動形が `relative_cost: 0` と `readonly: enforced` を宣言し、base の `session_log` を継ぐ。**
`errors` は base と各 profile がそれぞれ宣言する（`read` は「スキルと `--tools` セットが
噛み合いません」、`verify` は gemma4:12b の停止性に固有の縮退基準を持つなど、局面ごとに
hint が違うため）。

think はヘッドレスの 6 起動形中 5 つで `off` である。例外は `list-thinking` だけで、
`--format` の文法制約を外したうえで `--think on` と `AGENT_OLLAMA_OPTIONS={"temperature":0}` を
宣言する（gemma4:e4b の split で意味的な完全被覆を安定させるため）。`--tui` の対話起動も
`--think on` を持つ——人が画面で思考を読める場面である。

`--format json`（ollama の JSON モード）は**トップレベルを必ずオブジェクトにする**ので、配列を
返す契約（agent-flow の split）はプロンプトで何を書いても満たせない。`list` profile は
structured outputs のスキーマ `{"type":"array","items":{"type":"string"}}` を渡してトップレベル
配列を表現する。要素を string に固定するのは、split の要素が下流で map ゴールへ文字列として
埋め込まれるためである。

base の readonly にツールを付けないのは、CLI 契約上の強制力に嘘を入れないためである。
`read` profile は write として呼ばれる役割に読取ツールだけを与える別の起動形で、汎用の base を
安全側へ書き換えない。

### 4.1 `defs`

```
agent-herd defs                          # 解決できる定義名を列挙（探索順の和集合・重複排除）
agent-herd defs <名前>                    # 1 件の実効 argv（write / readonly / chat）と宣言
agent-herd defs <名前> --json             # 同じものを 1 行 JSON で
agent-herd defs <名前> --model M          # モデルを差し替えて argv を見る
agent-herd defs <名前> --purpose <用途>    # variant 解決を通してから見る
```

JSON のキー: `name` / `path` / `requested` / `profile` / `profiles` / `resolved_via_variant` /
`headless_autonomy` / `readonly` / `relative_cost` / `default_model` / `model` / `prompt_via` /
`variants` / `argv_write` / `argv_readonly` / `argv_interactive` / `timeout`。

**`defs` は第 2 実装を持たない。** 出す argv は `agentcli.headless_cmd()` が返すものそのもの
で、エンジンが組むのと同一であることをテストが縛る（`test_herdcli.DefsTests`）。

終了コード: 0 = 出力した / 1 = 定義が解決できない / 2 = オプションの誤り。

### 4.2 `exec`

```
agent-herd exec <cli> [--model M] [--purpose P] [--readonly] [--file PATH]… [--read PATH]…
```

本文は **stdin**。端末が繋がっているとき（人が引数だけ打って Enter したとき）は読まずに
本文なしとして進む——プロンプトも出さずに入力待ちで固まるのが一番わかりにくい失敗だからである。

`--readonly` を指定しても CLI が読み取り専用を保証しない定義（`readonly: best-effort`）では、
`@agent-note` を stderr に出してから実行する（保証できないことを黙らない）。

終了コード: 実体のものをそのまま返す / 1 = 定義が解決できない / 2 = オプションの誤り /
127 = 実行ファイルが見つからない。

**エンジンは `exec` を使わない。** 用途は人のデバッグ（「エンジンから呼ぶと落ちるが手で叩くと
動く」を潰す）に閉じる。

### 4.3 `chat`

```
agent-herd chat                     # 既定は ollama（内蔵 TUI）
agent-herd chat <cli> [--model M]
```

定義の `interactive` ブロックを `agentcli.interactive_cmd()` で解決して起動する。

- 解決した argv の先頭が**自分自身の名前**（`agent-herd` / `agent-aider` /
  `agent-ollama`）なら **in-process** で入る。定義は配布名で書いてあるので、開発木のように
  PATH にそれが無い環境でも動く。
- そうでなければ `os.execvp` で置き換える（余計なプロセスを挟まない）。
- `interactive` を持たない定義は**明示エラーで止める**（終了コード 1）。黙ってヘッドレスへ
  倒さない——倒すと人は「対話に入ったつもり」で 1 往復だけの実行を眺めることになる。
  対話面を足したくなったら定義に書く（エンジン改修は不要）。

`ready_pattern` / `busy_pattern` 等の tmux 向けフィールドは `chat` は使わない（人が直接
向き合う起動なので待機判定が要らない）。それらは `interactive.command` を tmux から叩く
消費者（agent-loop / agent-dashboard）のためにある。

### 4.4 `aider` の対話

`agents/aider.json` は `interactive` ブロックを持つ。ヘッドレスとの差は**引き算だけ**:

| ヘッドレスにあり対話に無い | なぜ落とすか |
|---|---|
| `--message`（`prompt_flag`） | 対話は本文を argv で渡さない |
| `--yes-always` | 人が確認する場で押し切らない |
| `--no-stream` / `--no-pretty` | 対話では出力を殺さない |

**残すもの**: `--agent-policy gemma4-e4b-reliability-v1`・`--model ollama_chat/{model}`・
`--no-git` / `--no-auto-commits` / `--no-check-update` / `--no-show-model-warnings` /
`--no-analytics` / `--no-gitignore` / `--map-tokens 0`。

policy と接続補完（§6）が**ヘッドレスと同じ経路**で仕込まれることをテストが縛る
（`test_herdcli.ChatTests`）。これが崩れると「対話で試したことがヘッドレスで再現しない」に
なる。

**`interactive` を持つことと、ハーネスが要ることは別である。** aider は対話面を持ちながら
`headless_autonomy: single-shot` なので、定型業務は従来どおり限定ツール契約のハーネスで
回る。agent-dashboard はこれを `headlessAutonomy` で弁別する
（`cowork.needsHeadlessHarness`）——`interactive` の有無で代理してはいけない。

### 4.5 `harness`

```
agent-herd harness statemachine (--workflow PATH | --entry NAME [--config PATH])
                                [--agent-cli NAME] [--model M]
                                [--param KEY=VALUE]… [--input TEXT] [--dir DIR]
agent-herd harness run PROMPT… [--agent-cli NAME] [--model M]
                               [--acceptance TEXT]… [--judge] [--dir DIR]
```

`--agent-cli` の既定は `aider`。フラグの綴りは `agent-loop statemachine` / `agent-loop run` と
**同じ**にしてある。同じハーネスの 2 つの入口なので、片方だけ違う名前を人に覚えさせない。
終了時に `RESULT {json}` を 1 行出すのも同じで、それが呼び出し側との結果契約になる。

`statemachine` は `--workflow` か `--entry` の**どちらか一方**を取る（両方・どちらも無しは 2）。
`--entry` は `agent-loop.yaml` の `prompts[]` のエントリ名で、ワークフローの位置と
**実行条件**（`input:` のマップと、自由文としての `prompt`）をその宣言から引く
（正典は agent-loop 仕様 §2.3.1、実装は `agentcore.loopentry` の 1 か所）。設定ファイルの
探索順は agent-loop と同じで、`--config` で直接指すこともできる。

| 打ったもの | 効くもの |
|---|---|
| `--param` / `--input` | エントリの宣言より**優先**（後から来た判断を勝たせる） |
| `--agent-cli` / `--model` | 打たなければエントリの `agent_cli` / `model`、それも無ければ既定 |
| `--dir` | 打たなければエントリの `cwd`、それも無ければカレント |

デーモンを持たない `agent-herd` からでも、常駐している定常業務と**同じ条件**で 1 回だけ
回せる——条件の解釈が入口ごとに違うと、「手で回すと通るのに定期実行だけ落ちる」が起きる。

実体は `agentcore.harness`。**tmux もデーモンも設定ファイルも要らない**——tmux はコマンドを
走らせて様子を見せる手段であって実行契約の一部ではない、という元の設計注記がそのまま効く。

**本文は 1 か所にしかない。** `agentcore/harness/toolloop.py` と
`agentcore/harness/statemachine.py` が正典で、`agent_loop/{toolloop,statemachine}.py` は
そこへ**委譲するだけの層**である（写しも、かつての共有データファイルも無い。経緯と
それを縛るテストは §12）。agent-loop 側は `_tl_*` / `_sm_*` を張り直さないので、
ハーネスの名前を差し替えたいテストは `agentcore.harness.*` へ当てる。

移植先の既定は agent-loop 経由と 2 点だけ違う（§6 と同じく「黙って書かない」を既定にした）:

| | agent-loop 経由 | `agent-herd harness` |
|---|---|---|
| 台帳への記帳 | 自分の ledger へ追記 | **しない**（`harness.set_hooks` で差し込める） |
| `selection_policy` の解決 | control.json v2 を読む | **しない**（None = 従来の pin / 既定候補で走る） |

`run_prompt()` の分岐点は定義の `headless_autonomy` **ただ 1 つ**である。`single-shot` なら
限定 4 ツール契約（read_files / write_files / run / final）を付けて回し、`tool-loop` なら
CLI 内部のツールループへ 1 回素通しする。

終了コード: 移植元が `sys.exit` で表すものをそのまま返す / 2 = 引数の誤り・未知の種別。

---

## 5. 未知のサブコマンド

黙って別解釈しない。2 通りに分ける:

| 打たれたもの | 応答 | 終了コード |
|---|---|---|
| 解決できる**定義名**（`ollama-json` 等） | `exec` を案内する: `agent-herd exec <名前> [--model …]` | 2 |
| それ以外 | 使えるサブコマンド一覧を出す | 2 |

エラーは `[agent-error:env] agent-herd: …` の形で **stderr** へ出す（定義の `errors` 分類に
乗る綴り）。

---

## 6. 環境

### 6.1 補完（`agentcore.hostenv`）

**実装は 1 つだけ。** 3 adapter はこれを import する（再定義しない）。同一オブジェクトである
ことをテストが `is` で縛る（`test_hostenv.HostenvIsTheOnlyImplementationTests`）——写しが
復活すれば必ず落ちる。

起動時に 1 回:

1. `OLLAMA_HOST` / `OLLAMA_API_BASE` / `NO_PROXY`（か `no_proxy`）が全部そろっていれば
   `~/.profile` は読まない（構成済みの環境へ余計な subprocess を足さない）
2. 足りなければ `~/.profile` を `sh` の子プロセスで評価し、`OLLAMA_*` / `AGENT_OLLAMA_*` /
   `NO_PROXY` / `no_proxy` だけを取り込む。**環境に既にある変数が常に勝つ。** 評価の失敗は
   黙って無視する（profile が壊れていても推論を止める理由にはしない）。stdin は閉じる
   （このプロセスの stdin はプロンプト本文なので profile に読ませない）
3. `OLLAMA_HOST` ⇄ `OLLAMA_API_BASE` を相互補完する（前者は ollama 系 CLI、後者は
   aider/litellm が見る。スキームが無ければ `http://` を足す）
4. 推論ホストを `NO_PROXY` と `no_proxy` の**両方**へ追記し、両者を同じ値に揃える
   （urllib は小文字を見る）。この段は profile の有無と無関係に必ず行う

4 を怠ると接続が社内プロキシへ流れ、**504 Gateway Timeout**という「設定はしてあるのに
動かない」形で出る。だから NO_PROXY の取り込みだけに頼らず、常に追記する。

### 6.2 環境変数

| 変数 | 既定 | 効く先 |
|---|---|---|
| `OLLAMA_HOST` / `OLLAMA_API_BASE` | — | 接続先。**片方だけでも相互に補完**する |
| `NO_PROXY` / `no_proxy` | — | ollama のホストを常に両表記へ追記する |
| `OLLAMA_TIMEOUT` | `600` 秒 | HTTP 全体の上限 |
| `AGENT_OLLAMA_CONNECT_TIMEOUT` | `120` 秒 | 応答ヘッダを得るまで。到達時に `/api/version` で生存確認し、サーバが生きていれば順番待ち（`OLLAMA_NUM_PARALLEL` の空き待ち・モデルロード中）として待ち続ける。失敗したときだけ打ち切り |
| `AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT` | `0`（無制限） | prefill |
| `AGENT_OLLAMA_STALL_TIMEOUT` | `180` 秒 | decode の無進捗 |
| `AGENT_OLLAMA_META_TIMEOUT` | `3` 秒 | `/api/ps` `/api/show` の問い合わせ |
| `AGENT_OLLAMA_THINK` | — | `on` / `off` / `prompt` |
| `AGENT_OLLAMA_OPTIONS` | — | API の `options` へ渡す JSON |
| `AGENT_OLLAMA_KEEP_ALIVE` | — | API の `keep_alive` |
| `AGENT_OLLAMA_SYSTEM_PROMPT` | — | system プロンプトの差し替え |
| `AGENT_OLLAMA_LOG_DIR` | `~/.agents/logs/ollama` | JSONL ログの置き場 |
| `AGENT_OLLAMA_SKILLS_DIR` | — | スキル探索の追加先（`:` 区切り） |
| `AGENT_OLLAMA_HISTORY` | — | TUI の履歴ファイル |
| `AGENT_OLLAMA_NO_RICH` / `AGENT_OLLAMA_NO_READLINE` | — | `1` で rich / readline を使わない |

`AGENT_OLLAMA_*` の綴りは入口の名前が変わっても据え置く（外部の手順書と `agents/*.json` の
`env` 宣言を書き換えさせない）。

---

## 7. 全サブコマンド共通の契約

既存 adapter の契約をそのまま入口の契約に昇格させたもの。

- **stdout は本文だけ。** 診断は 1 バイトも混ぜない
- **stderr は診断と計測。** `@agent-usage tokens_in=… tokens_out=…`（その実行で使った累計 =
  台帳向け）と `@agent-context used=… limit=… pct=… source=…`（いま文脈がどれだけ埋まって
  いるか）は**意味が違うので行を分ける**
- **完走しなかったら本文末尾に機械可読の封筒** `{"ok": false, "issues": [...]}`
  （`--format json` のときは足さない）。同じことを `@agent-note` で stderr にも出すが、
  **判定に使うのは封筒のほう**
- **終了コードは実体のものをそのまま返す。** 入口で丸めない
- **ログは `~/.agents/logs/<adapter>/` へ JSONL 追記。** agent-audit がセッションとして
  読める形を変えない

---

## 8. ollama 実行系の契約

### 8.1 実行面と CLI フラグ

| 面 | 入口 | 用途 |
|---|---|---|
| plain | `agent-herd ollama <model>` | text / JSON の単発生成。道具なし |
| bash loop | `--tools bash` | OS ユーザー権限での汎用 work |
| read loop | `--tools read` | 決定的ゲート付きの調査・読取 |
| human / observe | `--tui` / `--status` / `--follow` / `--context` | 対話、進捗追尾、状態・文脈上限の取得 |
| measure | `--replay` | 記録済みプロンプトの再生。**道具を持たない**（§8.6） |

`--status` / `--follow` / `--replay` は `agent-herd status` / `follow` / `replay` としても
打てる（§3 の別名。実体は同じフラグ）。

| フラグ | 既定 | 意味 |
|---|---|---|
| `--model` | 定義の `default_model` | モデル（positional でも渡せる） |
| `--tools` | `bash`（ループ時） | `bash` \| `read`。`edit` は予定名として認識し明示エラー |
| `--format` | — | `json` \| `array` \| `text` |
| `--think` | 定義による | `on` \| `off` \| `prompt` |
| `--max-rounds` | `12` | ツールループのラウンド上限 |
| `--command-timeout` | `300` 秒 | ツール 1 コマンドの上限 |
| `--stall-timeout` | `180` 秒 | decode の無進捗上限 |
| `--first-token-timeout` | `0`（無制限） | prefill の上限 |
| `--context-limit` / `--context-warn-pct` | 自動解決 / `90` % | 文脈上限と警告しきい値 |
| `--skill` / `--no-skills` | — | スキルの明示読み込み |
| `--cwd` | 現在地 | ツールの開始位置（**sandbox ではない**） |
| `--log` / `--no-log` | `~/.agents/logs/ollama` | JSONL ログ |
| `--status` / `--follow` / `--context` | — | 観測 |
| `--replay` / `--replay-limit` / `--replay-out` / `--arm` | — | 再生と腕の指定 |

### 8.2 上限

| 対象 | 既定 | 変えられるか |
|---|---:|---|
| ツールループのラウンド | 12（`read` profile は 30） | `--max-rounds` |
| ツール 1 コマンド | 300 秒 | `--command-timeout` |
| ツール出力の取り込み | 4,000 字（頭尾を残して詰め、何を省いたか明記） | 不可 |
| 1 ラウンドの生成 | 4,096 トークン | 不可 |
| 規約外応答の言い直し | 2 回 | 不可 |
| ツール拒否 | 3 回目で `tool_denied`（連続である必要はない） | 不可 |
| 同一 `(コマンド, 終了コード, 出力)` の連続 | 3 回で `no_progress` | 不可 |
| 文脈の警告 | 実効上限の 90 % で 1 回だけ | `--context-warn-pct` |
| 文脈の reserve | 512 トークン | 不可 |
| heartbeat | 5 秒 | 不可 |

待ちの上限は局面ごとに分ける。**prefill を無制限にしてあるのが要点**で、CPU 推論では
「最初のトークンまで 10 分」が正常なため、ここに上限を置くと正常な実行を殺す。上限の判定は
heartbeat の刻みで行うので、検知は最大 5 秒だけ遅れる。

文脈上限は `--context-limit` → request options の `num_ctx` → `/api/ps` → `/api/show` の順で
解決する。取得不能なら上限 0 として使用量だけを表示し、**知らない上限を根拠に警告・打ち切りは
しない**。

### 8.3 終了状態と出力

ループの終了状態は 6 つ。

| 状態 | 意味 | トリアージ |
|---|---|---|
| `done` | `TASK_COMPLETE` を確認した | — |
| `no_command` | 規約外応答が続いた | — |
| `max_rounds` | ラウンド上限 | — |
| `no_progress` | 同一入出力の連続 3 回。同一入力の再試行では解けない | `env` |
| `context_exhausted` | 最低限のツール結果も入らない | `env` |
| `tool_denied` | 拒否が続いた | `env` |

**`done` 以外はすべて未完了**である。最後の本文を捨てずに stdout へ返したうえで、通常本文の
末尾へ `{"ok": false, "issues": [...]}` を足す。`--format json` は本文全体の契約を壊せないため
封筒を足さず、外側の形式修復・検証へ委ねる。**未完了も rc=0** なので、呼び出し側は本文の
機械可読契約を読んで判定する。

stderr の行は責務を分ける。

| 行 | 内容 |
|---|---|
| `@agent-usage` | 累計 `tokens_in` / `tokens_out`。node-budget・audit の実測値 |
| `@agent-context` | 現在の文脈使用量 / 上限 / 比率 / 出典。**累計消費とは混ぜない** |
| `@agent-note` | 未完了理由の人向け注記 |
| `@agent-log` | JSONL ログのパス |

接続不能・モデル未取得・スキル未配布・ツールセット不整合・`context_exhausted` は `env`、
stall・通信断は `transient` として定義する。

### 8.4 ツール

| セット | 許すもの |
|---|---|
| `bash` | `bash -lc` へそのまま渡す。**OS ユーザー権限の範囲で無制限**であり、`cwd` は開始位置にすぎない（sandbox ではない） |
| `read` | ファイルを変更できないコマンドと git の読取 subcommand だけ。引用外のシェルメタ文字・`find` の書込/実行述語・未知コマンドを拒否し、許可後も**シェルを介さず argv として直接実行**する |
| `edit` | 予定名としてだけ認識し、現時点では明示エラー |

判定できない形は安全側で拒否する。拒否は `tool_exec` を出す前に決めるので、実行していない
コマンドが実行されたようにログへ残ることはない。

### 8.5 スキル

**明示・遅延読み込みだけ**である。`--skill <name>` またはプロンプト先頭の連続 slash 行を検出し、
`~/.agents/skills` → `AGENT_OLLAMA_SKILLS_DIR` の追加先 → `~/.claude/skills` の順に `SKILL.md` を
探す。frontmatter は除き、同じスキルは 1 回だけ注入する。明示指定が見つからなければ env 失敗。
**先頭の slash 行がルート表にも宣言にもスキルにも無ければ明示エラー**で止める（§13）。

`{skill_dir}` を使うスキルは同梱 script の実行を前提にするため、`read` と組み合わせた時点で env
失敗にする（スキルを読めたのに手順だけ実行できない「成功に見える失敗」を作らない）。
利用可能なスキルの全一覧を system prompt へ常時載せる自動選択は、prefill の固定費になるため
行わない。

### 8.6 再生（`--replay` / `agent-herd replay`）

記録済みの JSONL から最初の user メッセージを取り出し、モデル・think・format を変えた腕へ同じ
入力を当てて、腕ごとの空応答率・失敗率・所要時間と、腕をまたいだ一致率を出す。

**再生は道具を持たない。** 道具ありの実行ログも入力源にできるが、記録されたコマンドは
再実行しない——再生は測定であって副作用の再現ではなく、測るたびにワークスペースが変わっては
ならない。この不変条件は腕の指定でも緩めない。正解ラベルとの一致率は出さない
（ラベルは人が付けるもので、この口が引き受けるのは「同じ入力に対する出力」を再現可能な形で
並べるところまで）。1 通りしか出力が無い件は一致率の母数に入れない。

### 8.7 ログ

実行中は run / skill / LLM / message / tool / context / error / end を JSONL へ追記する。
ログ書込みや表示 sink の失敗は推論本体を止めない。`--status` は末尾から
`{state, phase, round, last_progress_at, tokens_per_sec, context_*}` を組み立て、`--follow` と
TUI は同じイベントを表示する。

---

## 9. aider の adapter 契約

素の CLI を直接呼ばず 1 枚噛ませているのは、**素の argv では表せないものがある**からである。
それが無いクラウド CLI に adapter は置かない（§1・設計 §3.8）。

### 9.1 `aider`

Aider を CLI 契約の下に置く薄いラッパ（`agentcore/aider_adapter.py`）。`agents/aider.json`
から `agent-herd aider … --model ollama_chat/{model}` として起動され、既定モデルは
`gemma4:e4b`、`headless_autonomy: single-shot`（渡されたファイルを編集するだけでツールループを
持たない）、`readonly: enforced`（`--dry-run`）。`variants` は 13 用途を宣言し、12 用途を
`ollama-json` へ、`split` を `ollama-list-thinking` へ振り替える。

| ラッパ専用オプション | 意味 |
|---|---|
| `--tui` | 共通 TUI（`ollama_tui`）を aider バックエンドで開く（段 12。§9.2） |
| `--agent-policy <id>` | Aider の system prompt 先頭へ固定の reliability policy を注入する。現在の唯一の ID は `gemma4-e4b-reliability-v1`（対象 model は `ollama_chat/gemma4:e4b`） |
| `--agent-num-ctx <整数>` | model settings の `extra_params.num_ctx` |
| `--agent-num-predict <整数>` | model settings の `extra_params.num_predict` |

これら 3 つは Aider へは渡さず、一時的な `--model-settings-file` を組み立てて先頭に差し込む
（実行後に削除）。policy 適用時は stderr へ `@agent-policy id=<id> sha256=<12桁>` を出すので、
実効 policy を後から観測できる。**未知の ID・対象外 model・外部 `--model-settings-file` との
競合は黙って無効化せず、起動前に `[agent-error:env]` で失敗する**——「policy が効いている
つもりで効いていない」を作らないため。

3 つめは実測 usage で、`--analytics-log` の一時ファイルから累計トークンを読んで
stderr の `@agent-usage tokens_in=... tokens_out=...` へ載せる（共通の usage 契約）。

`~/.profile` からの環境補完（§6）も同じ理由で必要である——aider は接続先を `OLLAMA_API_BASE`
（litellm）で読むので、補完が無いと既定の localhost へ向かうか、接続がプロキシへ流れて
504 になる。

### 9.2 対話面 — 共通 TUI の aider バックエンド（段 12）

`agents/aider.json` の `interactive` は aider 素の TUI ではなく**共通 TUI**
（`agent-herd aider --tui`）を起動する。前面の規約（`> ` プロンプト＝`ready_pattern`・
turn hook・`/sm` `/edit` のハーネス回送）は ollama バックエンドと同一で、
1 入力 = aider 1 回（`--message`）のヘッドレス実行になる。

- 会話は積まない。継続に要る材料は毎回プロンプトへ書く（文脈を太らせない）
- `--message` は adapter がターンごとに付けるので、起動 argv には現れない
- モデル別設定（`--agent-policy` / `--agent-num-*`）があるとき `/model` での切り替えは
  明示エラー——settings の entry は起動時のモデル名で束ねてあり、黙って外れることを許さない
- 未知の `/x` と `/ask` `/find` は明示エラー（このバックエンドに toolset は無い）。
  `/sm` `/edit` は TUI がヘッドレスのハーネスへ回す（§13.2 の表と同じ当て先）

---

## 10. 配布

`bash tools/agent-tools/install.sh` が作るもの:

```
~/.local/bin/agent-herd        zipapp（agentcore 同梱・rich は --with-rich のとき）
~/.local/bin/agent-aider   ─┬─ agent-herd へのハードリンク（同一 inode）
~/.local/bin/agent-ollama   ─┘
```

- ハードリンクが張れない FS ではコピーへ落とし、`[WARN]` を出す。インストーラは常に
  全部を書き直すので、コピーでも版がずれることはない。過去の入れ直しで残った
  `agent-opencode` は古い zipapp を指し続ける罠になるため、インストーラが消す
- `--only agent-herd` でエンジンを 1 本も入れずに実行系だけ置ける（推論だけ担当する PC 向け）
- インストール後、`agent-herd --help` と `agent-ollama --help` の両方を実行して
  argv[0] 分岐まで踏む（「入った」と言い切る前に shebang / Python の取り違えを潰す）
- agent-loop は別 zipapp のまま（デーモンであり契約が別）だが、同梱する agentcore に
  `harness` が含まれる。**同時に入れ直す**運用は不変

実装は Python 標準ライブラリだけで成立する。TUI の rich は任意で、無い環境では ANSI /
readline の行指向表示へ戻る。全画面の alternate screen は使わず、agent-loop の
`capture-pane` と `send-keys` から同じ対話面を駆動できる形を保つ。

---

## 11. 未実装

| 項目 | 状態 |
|---|---|
| `edit` ツールセット | 未実装。段 0〜3 の品質・節約実測が着手条件 |
| `--patch` の決定的 SEARCH / REPLACE 適用 | 未実装。`edit` より小さい必要性が確認できたとき再検討 |
| 走行中の read → edit / bash 昇格（`ToolPolicy`） | 未実装。read の権限不足による人手介入が実測で一定数出たときだけ着手 |
| R3「品質は時間で買う」の実証 | `--replay` が検証の口だが、買えている証拠は `list-thinking` の 1 局面を除いてまだ無い |
| `ollama_loop` と `harness.toolloop` の統合 | しない（設計 §2.3）。台帳で「同じ役割を両経路で流した実測」が並ぶまで判断を保留する |
| `agent-herd stub`（`kiro-cli-stub.py` の同梱） | 未決。配布物に試験具を混ぜる是非を先に決める |

`agents/*.json` のローカル定義（`aider` / `ollama` の 6 起動形）は
`["agent-herd", "<sub>", …]` へ正典化済み。クラウド 5 件は §1 のとおり素の CLI を指したまま。
ハーネスは `agentcore.harness` が唯一の実装で、agent-loop はそこへ委譲する（写しも共有
データファイルも残っていない）。

---

## 12. テスト

| 何を縛るか | どこ |
|---|---|
| argv[0] 分岐・別名と明示形の同一性・観測別名 | `agentcore/tests/test_herdcli.py::Argv0DispatchTests` |
| サブコマンド名の空間（定義名を adapter に化けさせない） | 同 `SubcommandNamespaceTests` |
| `defs` が `agentcli` と同じ argv を出すこと・variant 解決 | 同 `DefsTests` |
| `chat` の既定・policy の同一経路・対話面が無い定義の拒否・in-process 起動 | 同 `ChatTests` |
| `exec` の argv 組み立て・引数面が閉じていること・tty を読まないこと | 同 `ExecTests` |
| `harness` の引数解釈と種別の弁別 | 同 `HarnessTests` |
| トップレベルのフラグ（§3.3）と、別名の引数面が素通しのままであること | 同 `TopLevelFlagsTests` / `Argv0DispatchTests` |
| 環境補完が 1 実装であること（`is` で同一性） | `agentcore/tests/test_hostenv.py` |
| 環境補完の振る舞い（相互補完・プロキシ迂回・両表記の一致） | 同 `CompleteOllamaEnvTests` |
| ハーネスが単独で立つこと・本文が 1 か所であること・継ぎ目の既定 | `agentcore/tests/test_harness_standalone.py` |
| ハーネスの振る舞い（ステートマシン完走・限定ツール契約・一時障害リトライ・timeout fallback） | `agentcore/tests/test_harness_{statemachine,control_retry,agent_timeout}.py` |
| 同梱定義の実効 argv（旧綴りが profile として解けることを含む） | `agentcore/tests/test_agentcli.py::TestBundledGolden` |
| 用途別の起動形が 1 エージェントの profile であること | `tests/test_agentcli_jsonvariant.py` |
| ollama の引数解釈・ループ・文脈・スキル・再生・TUI | `agentcore/tests/test_ollama_*.py` |
| aider の policy 合成と usage 抽出 | `agentcore/tests/test_aider_adapter.py` |
| 共通 TUI の aider バックエンド（前面規約の共有・1 入力 1 回・ハーネス回送） | `agentcore/tests/test_aider_tui.py` |
| agent-loop → ハーネスの委譲（別名を張らない・サブコマンドが落ちる・記帳が台帳へ着く） | agent-loop の `test/test_harness_delegation.py` |
| コマンド面の規約・4 種の表・用途の宣言・未知コマンドの明示エラー（§13） | `agentcore/tests/test_slashroute.py` |
| ランチャが argv を組む前に読むこと（`/sm` の起動・`/edit` の宣言・逃げ道） | `agentcore/tests/test_harness_slash_dispatch.py` |

テストルートは 2 つある（`agentcore/tests/` と `agentcore/agentcore/tests/`）。CI は両方を
明示して回す:

```bash
cd tools/agent-tools/agentcore \
  && python3 -m unittest discover -s tests \
  && python3 -m unittest discover -s agentcore/tests
```

---

## 13. コマンド面（スラッシュ）

設計: [2026-08-27 クラウド CLI を正とした入口の再構成](../plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md) §3.2・§3.3。
実装は `agentcore.slashroute` の 1 か所（人が打つ面も engine が組む面も同じものを引く）。

### 13.1 規約

本文の**先頭から連続する** `/name [args]` の行がコマンド行である。名前は
`^[a-z0-9][a-z0-9._-]*$`。空行でブロックが終わる（空行より後ろは本文）。

**ランチャは argv を組む前にこの行を読む。** 起動形（どのハーネス・どの toolset・どの
profile・どの候補）は argv を組む前に決まらなければならないからで、判定は文字列マッチ
だけ——**LLM は 1 回も呼ばれない**。

読んだあと、その行を CLI へ渡すか消費するかは**定義が宣言する**（`slash_native`。
[agent-cli 仕様書](./agent-cli-spec.md) §2.4）。

### 13.2 4 種類

綴りの見え方は 1 つで、実体だけが違う。

| 種別 | 例 | 実体 | 誰が用意するか |
|---|---|---|---|
| A. セッション操作 | `/model` `/tools` `/think` `/ctx` `/status` `/skills` `/keys` `/help` `/quit` | コード内の関数（面が持つ） | agentcore |
| B. 実行形 | `/ask` `/find` `/edit` `/sm <名前> [--param k=v]` | ハーネスと toolset の切替 | agentcore |
| C. 用途 | `/verify` `/judge` … | 宣言 1 枚（§13.3） | 人・配布物 |
| D. スキル | `/wiki-use` … | `SKILL.md` を材料へ載せる | 既存のスキル配布 |

A と B は**コード内の定数**で、設定ファイルにしない。人が書くのは C だけである。
D は表に載らない——`SKILL.md` の実在がそのまま答えだから。

種別 B が決めるのは道具立てとハーネスである。**ツールセットの選択がモデルの判断から
1 語へ移る**のがこの表の要点で、弱いモデル向けの自由度削減はその副産物にすぎない。

| 綴り | 決めるもの |
|---|---|
| `/ask` | 道具なし（推論だけ） |
| `/find` | `read` セット |
| `/edit [指示]` | 編集ハーネス（`toolloop`）。どの編集適用エンジンかは §13.3 の宣言が決める |
| `/sm <名前> [--param k=v]` | ステートマシン。名前が実在するファイルならワークフロー、そうでなければ entry |

引数はルータが食べず**本文の頭へ戻る**（`/ask 富士山の高さは?` の 1 行で送れる）。
例外は `/sm` で、引数が起動形そのものを名指しするため食べる。

### 13.3 用途の宣言 1 枚（種別 C）

置き場と探索順は**先勝ち**で、`$AGENT_COMMANDS_DIR` →
`<プロジェクト>/.agents/commands/` → `~/.agents/commands/` → 同梱。frontmatter は
**平らな `key: value` だけ**（agentcore は stdlib だけで動く必要があり、PyYAML は前提に
できない）。本文はそのままシステムプロンプトになる。

| キー | 意味 |
|---|---|
| `description` | `/help` と補完に出る 1 行 |
| `agent` | 起動形。宣言した定義の `variants` はさらに引かれる（`agent: ollama` ＋ 用途 verify → `ollama-verify`） |
| `model` | 用途専用の既定。**人の明示と用途別順位表（実測）には負ける**（[agent-cli 仕様書](./agent-cli-spec.md) §4.1 と同じ規則） |
| `tools` | ツールセットを 1 つだけ。`[]`＝道具なし / `[read]` / `[bash]` |
| `output` | 出力契約（`json` 等） |
| `argument-hint` | `/help` の左列に出る引数の型 |

**名前空間はスキル・種別 A / B と 1 つ**である。同名を両方置かない（先勝ちで、もう片方が
黙って効かなくなる）。同梱しているのは `edit.md` の 1 枚だけで、**aider の名前が出るのは
そこだけ**である——編集適用の実装を差し替える変更は将来この 1 行で済む。

### 13.4 知らない名前は止まる

ルート表にも宣言にもスキルにも無い先頭コマンド行は**明示エラー**で止める。黙って本文
として推論へ流さない——打ち間違えた `/verfy` が「なぜか普通の依頼として実行された」に
なるのを防ぐ（層3 でスキルが解決できないときに起動時 fail fast にしているのと同じ方針）。

規約が先頭ブロックしか見ない以上、`/tmp を消して` のような普通の依頼も先頭に来れば
コマンド行に見える。エラー文は**逃げ道まで書く**——本文として送るには先頭に空行を
1 つ入れる。1 回実行（`harness run` / `agent-loop run`）はこの空行を落とさない。
