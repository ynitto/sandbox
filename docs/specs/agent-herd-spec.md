# agent-herd 仕様書 — 入口の綴り・契約・終了状態

> 作成 2026-08-25
> 対象実装: `tools/agent-tools/agentcore/agentcore/herdcli.py` / `hostenv.py` /
> `aider_adapter.py` / `ollama_adapter.py` / `opencode_adapter.py` /
> `tools/agent-tools/install.sh`
> 設計（なぜそうするか）: [agent-herd 統合入口の設計](../plans/2026-08-25-agent-herd-unified-entry-design.md)
> 位置づけ: **本書が綴りの正典**。設計書は判断の理由を、本書は「打つと何が起きるか」を固定する。
> 実装と食い違ったら、どちらかが間違っているので直すまで作業を止める。

---

## 1. 何であるか

`agent-herd` は **LAN 上の ollama を動かす実行系の唯一の入口**である。実体は agentcore を
同梱した zipapp 1 ファイルで、`agent-aider` / `agent-ollama` / `agent-opencode` は同じ
ファイルへのハードリンクとして置かれる。

**クラウド CLI（claude / codex / kiro / copilot / cursor）はこの入口を通らない。**
それらの `agents/*.json` の `command` は素の CLI を指したままである（設計 §9.3）。

## 2. ディスパッチ

```
basename(argv[0])       解決されるサブコマンド        残りの引数
  agent-aider       →   aider                        argv 全部
  agent-ollama      →   ollama                       argv 全部
  agent-opencode    →   opencode                     argv 全部
  agent-herd        →   argv[0] をサブコマンドとして  argv[1:]
  それ以外           →   argv[0] をサブコマンドとして  argv[1:]
```

分岐はこの 1 回だけである。`agent-aider X` と `agent-herd aider X` は**同じ関数に同じ引数で**
届く（テスト: `test_herdcli.Argv0DispatchTests`）。

「それ以外」があるのは、開発木から `python3 -m agentcore.herdcli defs` のように叩くため。

## 3. サブコマンド

**サブコマンドは adapter の名前であって定義の名前ではない。** これが名前空間の規約である。

| 綴り | 実体 | 引数の扱い |
|---|---|---|
| `aider …` | `aider_adapter.main` | adapter へ素通し |
| `ollama …` | `ollama_adapter.main` | adapter へ素通し |
| `opencode …` | `opencode_adapter.main` | adapter へ素通し |
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

- **adapter サブコマンド**（`aider` / `ollama` / `opencode` / 観測別名）は引数を 1 つも解釈
  せず adapter へ渡す。adapter 側の `--help` がその面の正典。
- **入口が持つサブコマンド**（`chat` / `defs` / `exec`）は引数面が閉じている。未知のオプションは
  終了コード 2 で拒否する（黙って下へ流さない——流すと「効いたつもり」が起きる）。

### 3.2 入口自身のオプション

`agent-herd` は自分のフラグを持たない。受けるのは次の 3 つだけ:

| 綴り | 動作 | 終了コード |
|---|---|---|
| `--help` / `-h` / `help` | サブコマンド一覧を stdout へ | 0 |
| `--version` / `version` | `agent-herd <agentcore.__version__>` を stdout へ | 0 |
| 引数なし | 一覧を stdout へ（使い方の誤りなので 0 では返さない） | 2 |

各 adapter の詳細は `agent-herd ollama --help`（= `agent-ollama --help` と同一本文）。

## 4. 定義経由の 2 系統

| | adapter サブコマンド | `exec` |
|---|---|---|
| 綴り | `agent-herd ollama --format json qwen3` | `agent-herd exec ollama-json --model qwen3` |
| argv を決めるもの | 打った人 | `agentcore.agentcli`（定義） |
| 定義を読むか | 読まない | 読む（`variants` / `readonly` が効く） |
| 名前の空間 | `aider` / `ollama` / `opencode` | `agents/*.json` の全定義 |

### 4.1 `defs`

```
agent-herd defs                          # 解決できる定義名を列挙（探索順の和集合・重複排除）
agent-herd defs <名前>                    # 1 件の実効 argv（write / readonly / chat）と宣言
agent-herd defs <名前> --json             # 同じものを 1 行 JSON で
agent-herd defs <名前> --model M          # モデルを差し替えて argv を見る
agent-herd defs <名前> --purpose <用途>    # variant 解決を通してから見る
```

JSON のキー: `name` / `path` / `requested` / `resolved_via_variant` / `headless_autonomy` /
`readonly` / `relative_cost` / `default_model` / `model` / `prompt_via` / `variants` /
`argv_write` / `argv_readonly` / `argv_interactive` / `timeout`。

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

- 解決した argv の先頭が**自分自身の名前**（`agent-herd` / `agent-aider` / `agent-ollama` /
  `agent-opencode`）なら **in-process** で入る。定義は配布名で書いてあるので、開発木のように
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
agent-herd harness statemachine --workflow PATH [--agent-cli NAME] [--model M]
                                [--param KEY=VALUE]… [--input TEXT] [--dir DIR]
agent-herd harness run PROMPT… [--agent-cli NAME] [--model M]
                               [--acceptance TEXT]… [--judge] [--dir DIR]
```

フラグの綴りは `agent-loop statemachine` / `agent-loop run` と**同じ**にしてある。同じ
ハーネスの 2 つの入口なので、片方だけ違う名前を人に覚えさせない。終了時に `RESULT {json}`
を 1 行出すのも同じで、それが呼び出し側との結果契約になる。

実体は `agentcore.harness`（`agent_loop` からの移植）。**tmux もデーモンも設定ファイルも
要らない**——tmux はコマンドを走らせて様子を見せる手段であって実行契約の一部ではない、
という元の設計注記がそのまま効く。

移植先の既定は agent-loop と 2 点だけ違う（§6 と同じく「黙って書かない」を既定にした）:

| | agent-loop 経由 | `agent-herd harness` |
|---|---|---|
| 台帳への記帳 | 自分の ledger へ追記 | **しない**（`harness.set_hooks` で差し込める） |
| `selection_policy` の解決 | control.json v2 を読む | **しない**（None = 従来の pin / 既定候補で走る） |

終了コード: 移植元が `sys.exit` で表すものをそのまま返す / 2 = 引数の誤り・未知の種別。

## 5. 未知のサブコマンド

黙って別解釈しない。2 通りに分ける:

| 打たれたもの | 応答 | 終了コード |
|---|---|---|
| 解決できる**定義名**（`ollama-json` 等） | `exec` を案内する: `agent-herd exec <名前> [--model …]` | 2 |
| それ以外 | 使えるサブコマンド一覧を出す | 2 |

エラーは `[agent-error:env] agent-herd: …` の形で **stderr** へ出す（定義の `errors` 分類に
乗る綴り）。

## 6. 環境の補完（`agentcore.hostenv`）

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

## 8. 配布

`bash tools/agent-tools/install.sh` が作るもの:

```
~/.local/bin/agent-herd        zipapp（agentcore 同梱・rich は --with-rich のとき）
~/.local/bin/agent-aider   ─┐
~/.local/bin/agent-ollama   ├─ agent-herd へのハードリンク（同一 inode）
~/.local/bin/agent-opencode ┘
```

- ハードリンクが張れない FS ではコピーへ落とし、`[WARN]` を出す。インストーラは常に 4 つ
  全部を書き直すので、コピーでも版がずれることはない
- `--only agent-herd` でエンジンを 1 本も入れずに実行系だけ置ける（推論だけ担当する PC 向け）
- インストール後、`agent-herd --help` と `agent-ollama --help` の両方を実行して
  argv[0] 分岐まで踏む（「入った」と言い切る前に shebang / Python の取り違えを潰す）
- `tools/opencode/install.sh` は agent-tools 一式とは独立に走るので、同じ実装を**自前の
  zipapp**として組む（agentcore 同梱）。どちらを走らせても同じ実装が入る

## 9. 本実装で入っていないもの

| 項目 | 状態 | いまの経路 |
|---|---|---|
| `agent_loop` の断片を消して `agentcore.harness` への委譲へ | **未着手（P2 段2）** | 移植先と元の 2 つが並存（AST パリティテストが一致を縛る） |

`agents/*.json` のローカル 8 定義は `["agent-herd", "<sub>", …]` へ正典化済み（P3）。
クラウド 5 件は §1 のとおり素の CLI を指したまま。

`harness` は **P2 段1（ポーティング）で実装済**。`agent_loop` 側の断片は消していないので、
同じハーネスに 2 つの入口がある状態である（一致は AST パリティテストが縛る）。
段2（agent_loop を委譲へ寄せる）は未着手。

## 10. テスト

| 何を縛るか | どこ |
|---|---|
| argv[0] 分岐・別名と明示形の同一性・観測別名 | `agentcore/tests/test_herdcli.py::Argv0DispatchTests` |
| サブコマンド名の空間（定義名を adapter に化けさせない） | 同 `SubcommandNamespaceTests` |
| `defs` が `agentcli` と同じ argv を出すこと・variant 解決 | 同 `DefsTests` |
| `chat` の既定・policy の同一経路・対話面が無い定義の拒否・in-process 起動 | 同 `ChatTests` |
| `exec` の argv 組み立て・引数面が閉じていること・tty を読まないこと | 同 `ExecTests` |
| `harness` が所在を答えること | 同 `HarnessTests` |
| 環境補完が 1 実装であること（`is` で同一性） | `agentcore/tests/test_hostenv.py` |
| 環境補完の振る舞い（相互補完・プロキシ迂回・両表記の一致） | 同 `CompleteOllamaEnvTests` |

テストルートは 2 つある（`agentcore/tests/` と `agentcore/agentcore/tests/`）。CI は両方を
明示して回す:

```bash
cd tools/agent-tools/agentcore \
  && python3 -m unittest discover -s tests \
  && python3 -m unittest discover -s agentcore/tests
```
