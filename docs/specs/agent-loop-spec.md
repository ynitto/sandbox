# agent-loop 仕様書

> 最終更新: 2026-08-21（実装と全件照合。CLI の層と適応遷移の表、グローバル指示、対話コンソールを追記）
> 対象: `tools/agent-loop/`（`agent_loop` パッケージ・27 モジュール・約 12,700 行）
>
> 本書は「**何ができて、何を設定でき、どんな規約と制約があるか**」を書きます。
> なぜそう決めたかは[設計書](../designs/agent-loop-design.md)、クラス構成と処理フローは
> `tools/agent-loop/DESIGN.md`、導入手順と操作の手引きは `tools/agent-loop/README.md` にあります。
>
> 実装と食い違いを見つけたら、実装が正です。見つけた人が本書を直してください。

agent-loop は、YAML に書いたプロンプトを tmux 上のエージェント CLI へ定期送信するデーモンです。定期発火のほかに、フックの判断・外部システムの HTTP POST・他エージェントのメッセージ・CLI からの単発送信も同じ配送経路へ流れます。

動作要件は tmux（必須）と Python 3（標準ライブラリのみ。CI は 3.11 で検証）です。PyYAML は YAML 設定と `prompt-add` / `prompt-remove` を使う場合に要ります。JSON 設定だけなら無くても動きます。エージェント CLI（kiro-cli / claude / codex / aider など）は別途 PATH 上に必要です。

---

## 1. できること

### 1.1 送信のきっかけ

| 経路 | 何が送信を起こすか | 要る設定 | 送信までの遅れ |
|---|---|---|---|
| スケジュール | `interval_minutes` または `cron` の到来 | エントリに `prompt` か `slash` | 最短 1 秒（tick） |
| イベントフック | スケジュール発火時に `check()` が文面を返したとき | `hooks` | 同上（`check()` は 30 秒まで） |
| Webhook | 外部システムの `POST /hooks/<name>` | グローバル `webhook.enabled` とエントリ名の一致 | 202 を返した後、1 tick 以内にドレイン |
| メッセージング | 他エージェントが inbox へ JSON を投函 | グローバル `agent_name` | `inbox_poll_seconds`（既定 5 秒）＋ 1 tick |
| CLI send | `agent-loop send` の実行 | daemon 稼働（不在時は直接送信へ) | 1 tick（`--wait` で完了待ちも可） |

どの経路も、最後は同じ判定列（lifecycle → preflight → セッション準備 → スロット → ready）を通ります。busy やスロット上限で送れなければ要求は捨てずに保留し、次の tick で再試行します。同じエントリのスケジュール発火は最大 1 件へまとめます。

### 1.2 実行のかたち

| かたち | 設定 | pane の扱い | 使いどころ |
|---|---|---|---|
| 対話キープ（既定） | 何も書かない | 起動したまま保つ | 会話文脈を積み上げたい定常業務 |
| oneshot | `oneshot: true` | 実行ごとに作って壊す | 前の文脈を持ち込みたくない点検 |
| clean session | `clean_session: N` | N 回成功ごとに建て直す | 長時間動かして文脈が膨らむ業務 |
| Ralph | `mode: ralph` + `max_iterations` | 同じ pane へ N 回送り、最終回に要約指示 | 有界反復の改善作業 |
| headless | `session: per-run` | pane を使わず subprocess を都度起こす | 対話ペインを持たない CLI（aider 等） |
| external pane | `target: <external_panes の name>` | agent-loop は起動も cleanup もしない | 人が開いている既存セッションへ送る |
| 単発実行 | `agent-loop run` | daemon 不要。その場で 1 回 | アドホック実行、dashboard からの呼び出し |
| ステートマシン | `agent-loop statemachine` | daemon 不要。ワークフローを完走 | `statemachine-use` の定型業務 |

どのかたちを選べるかは、CLI 定義が申告する `headless_autonomy` で決まります。

| 層 | 定義の申告 | 例 | agent-loop の扱い |
|---|---|---|---|
| 層 2 | `tool-loop` | claude / codex / copilot / cursor / kiro / opencode / ollama | ヘッドレス argv を 1 回実行し、終了コードで完了を検知する |
| 層 3 | `single-shot` | aider / ollama-json / ollama-list | 限定ツール契約（§3.4）でツール実行を供給しながら完遂させる |

tmux を使うかどうかはこの層とは独立です。tmux は送る手段と見る手段で、headless でも見せ方は変わりません。`interactive` 節を持たない定義は対話キープを保てないので、`keep` 指定でも per-run へ倒して警告します。

### 1.3 操作コマンド

| コマンド | 何をする | daemon |
|---|---|:---:|
| `agent-loop` | デーモンを起動する（tmux 外なら専用セッションを作って自動アタッチ） | — |
| `ls` | 管理下の tmux セッションとペインを一覧する | 任意 |
| `send PROMPT` | 常駐セッションへ送る。`--wait` `--priority` `--model` `--sandbox` `--force` `--ralph` | 推奨 |
| `run PROMPT` | その場で 1 回実行する。`--agent-cli` `--model` `--acceptance` `--judge` `--dir` | 不要 |
| `statemachine --workflow PATH` | ワークフローを headless CLI で完走させる | 不要 |
| `msg --to AGENT [BODY]` | 相手の inbox へメッセージを投函する | 受信側に必要 |
| `agents` | 登録済みエージェントと inbox の状態を出す | 不要 |
| `pause` / `resume` | local pause を掛ける・外す（`resume` は agent-control / budget の pause を外さない） | 必要 |
| `cancel TARGET` | 管理下の entry / pane を止めてスロットを解放する（external pane は拒否） | 必要 |
| `drain` | 新規受付を止め、実行中が終わったら終了する | 必要 |
| `reload` | 設定を検証してから次 tick で一括交換する | 必要 |
| `doctor [--json] [--fix]` | 設定・スロット・send-request を診断する（`--fix` も非破壊） | 任意 |
| `methods list\|enable\|disable\|add` | 手法パックを操作する | 不要 |
| `update` | zipapp インストールを更新する | 停止中のみ |

`send` は「常駐セッションへ送る」、`run` は「今ここで実行して結果を返す」、`msg` は「相手の inbox へ置いて非同期に処理させる」です。宛先も完了の分かり方も違うので、用途で選んでください。

デーモンの stdin は**対話コンソール**になっていて、`status` / `ls` / `send <target> <text>` / `prompt-add` / `prompt-list` / `prompt-remove` / `help` / `quit` を受け付けます。`prompt-add` と `prompt-remove` は設定ファイルを直接書き換えるので、PyYAML が要ります（サブコマンドではなくこのコンソールの機能です）。

`slot-release` と `hook-event` は CLI 定義側の hook が呼ぶ内部コマンドで、`--help` には出ません。

### 1.4 完了の見分け方

送った処理が終わったかの判定は、実行のかたちで変わります。

| 実行 | 完了検知 |
|---|---|
| 対話 pane（`interactive.turn_completion` を宣言した CLI） | CLI 自身のターン完了イベント。取れなければ画面監視へ自動で戻る（§3.6） |
| 対話 pane（宣言なし） | 画面監視（`busy_pattern` / `ready_pattern` / `idle_quiet_sec`） |
| headless（`session: per-run`、`run`、`statemachine`） | subprocess の終了コード |

どの経路でも、完了時にスロットを解放して次の dispatch を通すのはスケジューラ側の 1 か所です。

### 1.5 失敗トリアージと quota の観測

**ターンの終わりに、ペインの画面を定義の `errors[]` で分類します。** 分類の実装は
ヘッドレスと同じ 1 つ（`agentcore.agentcli.classify_error`）で、`interactive` を持つ
どの CLI でも同じ規則が効きます。以前この経路には分類が無く、効くのは
`interactive.failure_pattern`（しかも `send --wait` のときだけ）でした。

| 分類 | このターンの扱い |
|---|---|
| `quota` / `auth` / `env` | **失敗**（完了として返さない）。失敗の理由にはその分類名が残る |
| `transient` | 完了のまま。再投入で解けるものなので、上位の判断に任せる |
| 一致なし・定義が `errors[]` を持たない | 何も変わらない |

**`quota` は node-budget の台帳へ観測行が入ります**（`event: quota` と `quota_kind`。
画面から復帰時刻が読めれば `reset_at` も）。これが無いと、ペインで quota が枯れても
管理面の段判定が知らず、degrade が効きません。

分類は 1 ターンに 1 回だけ走ります（監視のポーリングは 2 秒おきなので、素で回すと
同じ画面から同じ観測行を何本も生みます）。分類器が落ちても監視スレッドは止めません
——止まるとスロットが解放されず、ペインが上限を食ったまま誰も進めなくなるためです。

---

## 2. 設定

### 2.1 ファイルの場所と優先順位

| 役割 | 場所 | 備考 |
|---|---|---|
| 共通設定 | `~/.agents/agent-loop.yaml` / `.yml` / `.json` | 見つかった最初の 1 つだけを読む |
| プロジェクト設定 | `<cwd>/.agents/agent-loop.yaml` / `.yml` / `.json` | 旧 `.agent/` も読み取り互換 |
| 互換入力 | `<cwd>/.vscode/settings.json` の `agentExecutor.periodicPrompts` | 共通設定に `prompts` が無いときだけ |

`prompts` はプロジェクト設定が勝ちます。プロジェクト側に `prompts` があれば、共通設定と VS Code 由来の予定は使いません。`prompt-add` / `prompt-remove` は既存の YAML、無ければ `<cwd>/.agents/agent-loop.yml` へ書きます。設定ファイルは JSONC（`//` と `/* */`）も読めます。

### 2.2 グローバル設定

| キー | 型 | 既定 | 意味 |
|---|---|---|---|
| `agent_name` | str | なし | このデーモンの名前。設定すると inbox 監視を始める（未設定なら送信のみ） |
| `inbox_poll_seconds` | int | 5 | inbox のポーリング間隔 |
| `agent_cli` | str | なし（kiro-cli） | 駆動する CLI 名。`agents/<name>.json` 契約で解決する |
| `agent_cli_options.model` | str | 定義の既定 | 起動時に渡すモデル |
| `agent_cli_options.readonly` | bool | false | 読み取り専用フラグで起動する |
| `agent_cli_options.extra_args` | list | `[]` | argv 末尾へ足すフラグ |
| `kiro_options` | dict | — | `agent_cli` 未指定のときだけ効く kiro-cli 起動オプション（`trust_all_tools` / `resume` / `agent` / `model` / `extra_args`） |
| `startup_timeout` | int 秒 | 60 | ペインの起動待ち上限 |
| `max_concurrent` | int | 0 | 同時実行スロット数。0 は無制限 |
| `slot_timeout_seconds` | int 秒 | 7200 | スロットを強制解放するまでの猶予 |
| `cooldown_seconds` | int 秒 | 0 | 解放後に同じペインが再取得できない時間。0 は無効 |
| `response_timeout` | int 秒 | 600 | `send --wait` の既定待ち時間 |
| `split_direction` | str | horizontal | tmux の分割方向。不正値は警告して horizontal |
| `acceptance_judge` | bool | false | 受入条件の判定層を全エントリで有効にする（エントリ側で上書き可能・§3.4） |
| `headless_window` | bool | false | headless 実行のログを `tail -F` で追う tmux ウィンドウを開く（エントリごとに 1 枚を使い回す） |
| `health.check_interval_seconds` | int 秒 | 10 | ヘルス監視の間隔 |
| `health.freeze_timeout_seconds` | int 秒 | 0 | busy 中に画面が変わらない時間の上限。0 は無効 |
| `health.max_pane_rss_mb` | int MB | 0 | ready なペインの RSS 上限。0 は無効 |
| `health.min_free_memory_mb` | int MB | 0 | 空きメモリ下限。下回ると local pause（2 回連続 OK で自動 resume） |
| `health.input_recovery` | bool | false | ready かつ入力が残っているとき 1 回だけ再送する |
| `webhook.enabled` | bool | false | HTTP サーバを起こすか |
| `webhook.host` | str | 127.0.0.1 | bind 先 |
| `webhook.port` | int | なし | `enabled` なら明示必須（0 以下だと起動しない） |
| `webhook.path_prefix` | str | `/hooks` | 受け口のパス接頭辞 |
| `webhook.secret` | str | `""` | 共有シークレット。空なら検証せず起動時 WARNING |
| `webhook.secret_header` | str | `X-Gitlab-Token` | シークレットを照合するヘッダ名 |
| `webhook.max_body_bytes` | int | 1048576 | ボディ上限。超過は 413 |
| `environment_handoff.prompt` | bool | false | root プロンプト先頭へ `[ENV]…[/ENV]` を付ける（Ralph child には付けない） |
| `environment_handoff.skill_home` | str | null | スキルのホーム |
| `environment_handoff.token_env_names` | list[str] | `[]` | 存在の有無だけを渡す環境変数名。値は渡さない |
| `external_panes[].name` | str | 必須 | エントリの `target` から引く名前。重複は不可 |
| `external_panes[].tmux_target` | str | 必須 | `session:window.pane` 形式の宛先 |
| `external_panes[].agent_cli` | str | なし | その pane の ready / busy 判定にだけ使う。起動 CLI は変えない |
| `mapping` | dict[str, dict] | なし | 設定内の文字列から `{{lookup <ラベル> <キー>}}` で引ける値の辞書 |
| `prompts` | list | `[]` | 定期プロンプトのエントリ（次節） |

`mapping` は読み込み時に展開します。`mapping` 自身を除く全キーの文字列（ネストした dict と list の中も含む）が対象で、置き場所は `prompt` でも `cwd` でも構いません。存在しないラベルやキーを参照すると設定エラーになり、その設定は読み込めません。

`agent-tuning`（`$AGENT_TUNING_DIR` の `tuning.json`）と手法パックは agent-loop の設定ファイルの外にあります。エントリ側は `tuning_profile` で選ぶだけです。

### 2.3 プロンプトエントリ

| キー | 型 | 既定 | 意味 |
|---|---|---|---|
| `name` | str | 本文の先頭 40 文字 | 表示名。webhook のルート名にもなる |
| `id` | str | `uuid5(index, name)` | 省略しても**決定的**に採番するので、位置と名前が変わらない限り reload をまたいで同一視される（毎回 uuid4 にすると、起動直後の reload が `run_immediately_on_startup` の予定時刻を上書きしてしまう） |
| `enabled` | bool | true | false のエントリは読み込み時に落とす |
| `prompt` | str | — | 送る本文。`hooks` か `slash` があれば省略可 |
| `slash` | str \| list[str] | なし | 本文の前に独立送信する CLI コマンド |
| `interval_minutes` | int | — | 送信間隔。1 未満は無効（`webhook` 付きなら push 専用として許容） |
| `cron` | str | なし | 5 フィールドの cron 式。指定すると `interval_minutes` より優先 |
| `run_immediately_on_startup` | bool | false | 起動 30 秒後に 1 回目を送る（別名 `run_immediately`） |
| `cwd` | str | デーモンの起動位置 | このエントリの作業ディレクトリ |
| `fresh_context` | bool | false | 送信前にコンテキスト破棄コマンドを送る |
| `fresh_context_interval_minutes` | int | なし | 破棄を間引く間隔。1 未満は未指定と同じ |
| `exclude_from_concurrency` | bool | false | `max_concurrent` の対象から外す |
| `hooks` | str \| list[str] | なし | `check()` を呼ぶフックスクリプト。パスでも名前でもよい。文字列配列以外は起動エラー |
| `event_hook_fallback` | bool | false | 更新が無いときの代替送信をフックへ許可する |
| `hook_config` | dict | なし | `check(config)` へ渡す個別設定。dict 以外は起動エラー |
| `webhook.hook` | path | なし | `handle(ctx)` を呼ぶフック。省略すると受信 JSON をそのまま注入（パススルー） |
| `webhook.secret` / `webhook.secret_header` | str | グローバル値 | ルートごとの上書き |
| `adaptive.enabled` | bool | false | 動的インターバルを使う。`cron` エントリには効かない |
| `adaptive.min_interval_seconds` | float | 60 | 最短間隔 |
| `adaptive.max_interval_seconds` | float | 1800 | 最長間隔 |
| `adaptive.backoff_factor` | float | 1.5 | 無風時の伸ばし率（1.0 未満は 1.0 に丸める） |
| `adaptive.idle_threshold` | int | 3 | 連続 idle が何回続いたら伸ばすか |
| `adaptive.jitter` | float | 0.2 | ばらつき幅（0〜1 に丸める） |
| `preflight` | path | なし | `should_dispatch(request, cfg) -> bool` を呼ぶ |
| `tuning_profile` | str | `default` | 注入プロファイル。外向き成果物は `external-facing` |
| `mode` | `normal` \| `ralph` | normal | それ以外は起動エラー |
| `max_iterations` | int | — | `mode: ralph` で必須。1〜100 |
| `oneshot` | bool | false | true / false 以外（0 / 1 を除く）は起動エラー |
| `clean_session` | int | なし | 正整数のみ |
| `target` | str | なし | `external_panes[].name` |
| `agent_cli` | str | グローバル値 | このエントリだけ CLI を替える |
| `model` | str | グローバル値 | このエントリだけモデルを替える |
| `session` | `keep` \| `per-run` | keep | それ以外は起動エラー |
| `acceptance` | str \| list[str] | `[]` | 受入条件。文字列 1 本は 1 項目として扱う |
| `acceptance_judge` | bool | 未指定 | パスを含まない受入条件を検証エージェントに判定させる（§3.4）。トップレベルの既定を上書きする |
| `statemachine` | str | なし | 実行するステートマシン。`.statemachine/<名前>/workflow.yaml` の名前か、作業ディレクトリからの相対パス（§2.3.1） |
| `input` | dict | なし | ステートマシンの実行条件。値はスカラのみ。dict 以外・入れ子・空値は起動エラー |

#### 2.3.1 ステートマシン実行（`statemachine`）

`statemachine` を書いたエントリは、対話ペインへ本文を送るのではなく、**ハーネスのステートマシン実行**（`agent-loop statemachine` と同じ実体）へ回ります。`session` は `per-run` に固定され、CLI とモデルの差し替えは実行のたびに効きます。

値の正規化は次のとおりです。区切りを含まない名前は規約の場所へ展開し、パスは末尾が `.yaml` / `.yml` でなければ `workflow.yaml` を足します。絶対パス・`..`・`~` は**読み込みで断ります**（ハーネスも作業ディレクトリの外は読みませんが、設定を読んだ時点のほうが直しやすいため）。

| 書いた値 | 実行するファイル |
|---|---|
| `digest` | `.statemachine/digest/workflow.yaml` |
| `.statemachine/digest` | `.statemachine/digest/workflow.yaml` |
| `flows/digest/machine.yml` | `flows/digest/machine.yml` |

**実行条件の書き方は 2 つで、正典は `input:` です。**

| 書き方 | 何になるか | いつ使うか |
|---|---|---|
| `input:` のマップ | 宣言したキーがそのまま実行パラメータになる | **名前のある条件はすべてこちら（推奨）** |
| `prompt` の自由文 | ワークフローの `input` パラメータ 1 個ぶんになる | 名前の無い自由文だけ |

`input:` を正典に置く理由は、ワークフローが自分のパラメータ面（`{{topic}}` と `context:`）を宣言しているからです。マップはその面と 1:1 に対応するので、キーの過不足を**実行前に**——設定の読み込み時、および agent-dashboard の入力欄——突き合わせられます。自由文が確実に届く先は `input` の 1 スロットだけで、2 つ以上の条件を自由文で書くと割り付けはモデルの推測になり、外した実行は `check:` まで進んでから落ちます（1 回ぶんの課金と時間を捨てます）。実行ログにも `--param topic=llm` の形でそのまま残るので、後から同じ条件で引き直せます。

両方を書くことはできます（自由文 + 名前つき条件）。衝突するのは `input` キーだけで、`prompt` と `input.input` を同時に書いた設定は**片方を勝たせず落とします**。フックが本文を返した実行では、`prompt` ではなく**届いた本文**が `input` になります。

同じエントリは、tmux もデーモンも無しに次のどちらでも回せます（宣言の読み方は 1 実装を共有します）。

```
agent-loop statemachine --entry "日次ダイジェスト"
agent-herd  harness statemachine --entry "日次ダイジェスト"
```

`--param` / `--input` をその場で打った値は、エントリの宣言より**優先**します（後から来た判断を勝たせる）。CLI とモデルの解決順は `--agent-cli` / `--model`、エントリの `agent_cli` / `model`、control.json の `selection_policy`（version 2 以上で宣言があるときだけ）、既定（`aider`）の順です。

`cron` は「分 時 日 月 曜日」の 5 フィールドで、判定はデーモンが動いている端末のローカル時刻です。曜日の `7` は日曜として扱い、日と曜日を両方指定したときは Vixie cron と同じ OR になります（どちらかに当たれば発火）。

CLI とモデルの解決順は、control.json の `workloads.routine`（予算枯渇時の `degraded` 差し替えを含む）、エントリ、グローバル設定、既定の順です。エントリは管理面より上には行きません。

### 2.4 エントリが採用される条件

読み込み時に次を満たさないエントリは、そのエントリだけが落ちます。

- `enabled: false` ではない
- `prompt` / `hooks` / `slash` / `statemachine` のうち少なくとも 1 つがある（`statemachine` は `input:` だけで実行条件が足りるので、本文が無くても採用します）
- `cron` が妥当（不正な式は WARNING を出してスキップ）、または `interval_minutes >= 1`
- `interval_minutes` が無い場合は `webhook` ブロックがある（push 駆動専用として残り、自動発火はしない）

一方、次の組合せは**エントリを落とさず、読み込み全体を失敗させます**。起動時なら起動が止まり、`reload` 時なら現行設定を維持します。

| 組合せ | 理由 |
|---|---|
| `mode: ralph` と `oneshot` | 反復と使い捨ての前提が噛み合わない |
| `oneshot` と `clean_session` | pane の寿命の決め方が二重になる |
| `target` と `oneshot` / `clean_session` | 外部 pane の生死は agent-loop が持たない |
| `mode: ralph` で `max_iterations` 未指定、または 1〜100 の外 | 反復の上限が決まらない |
| `mode` / `session` / `oneshot` / `clean_session` / `acceptance` / `acceptance_judge` / `hooks` / `hook_config` の型違反 | 静かに既定へ倒すと意図しない挙動になる |
| `statemachine` と `mode: ralph` | 反復はワークフローの遷移で書く |
| `statemachine` と `oneshot` / `clean_session` / `target` / `session: keep` | ハーネスは対話ペインを持たない |
| `statemachine` と `slash` | スキルの呼び出しはワークフローの `action` に書く |
| `statemachine` と `acceptance` / `acceptance_judge` | 受入はワークフローの `check:` で宣言する（同じ検証を 2 か所に置かない） |
| `statemachine` の値が絶対パス・`..`・`~`、または `input` が dict でない・入れ子・空値 | 作業ディレクトリの外は読まない。実行条件は文字列としてテンプレートへ展開される |
| `prompt` と `input.input` の併記 | どちらも `input` パラメータを指している |

`statemachine` を宣言したエントリは、起動時に**ワークフローの実在**も確かめます。見つからなければ起動を止めます（dispatch のたびに CLI を 1 回起こしてから落ちるのを避けるため）。

headless（`session: per-run`）では、Ralph 多段と external target を組み合わせた時点で起動を明示エラーで断ります。

### 2.5 動的インターバルの遷移

`adaptive.enabled: true` のエントリだけが対象です（`cron` エントリには効きません）。発火の結果から次回時刻を決めます。

| 結果 | インターバルの更新 | idle 回数 |
|---|---|---|
| activity（送信した） | `min_interval_seconds` へ即リセット | 0 に戻す |
| idle（送らなかった） | `idle_threshold` 回続いたら `× backoff_factor` | +1 |
| error | `min × backoff_factor` の短時間 retry | 増やさない |

更新値は `max_interval_seconds` で頭打ちにし、`jitter` でばらつかせます。状態は `~/.agents/loop-adaptive/<entry-id>.json` へ原子的に書き、再起動をまたいで続きます。

**error は「壊れている」であって「無風」ではありません。** フックが 1 つも仕事を返さなかったとき、その理由が正常な無風（`check()` が `None` を返した）なら idle、次のどれかなら error です。

| error になる | idle になる |
|---|---|
| `check()` が例外を投げた | `check()` が `None` / 空文字を返した |
| `check()` が timeout して隔離された（隔離中のスキップも含む） | |
| フックファイルが読めない・`check()` が定義されていない | |
| 戻り値が契約違反（prompt が無い・`cwd` が実在しない・`vars` の format 失敗・型違い） | |

分けているのは、壊れたフックを無風と同じ経路で後退させると `max_interval_seconds` まで滑っていき、症状が「なんだか静かだ」としか見えなくなるからです。error は最小間隔付近を保って叩き続け、`~/.agents/loop-adaptive/<entry-id>.json` の `last_outcome` に残ります。ログにも `event=hook_check_error` が出ます。

複数フックのうち一部だけが壊れていて、他が仕事を返した場合は activity です（仕事があるほうを優先する）。

**入れていないもの**: フックが次回間隔を明示指定する口と、EWMA などによる予測。

### 2.6 CLI とモデルの差し替えが効く境界

| セッション設定 | 境界 | 差し替えが効くタイミング |
|---|---|---|
| `oneshot` / `session: per-run`（headless） | 毎回 | 次の実行 |
| `clean_session: N` | N 回成功ごと | 次の建て直し |
| 無限キープ（既定・`clean_session` 無し） | デーモン再起動のみ | 再起動後 |

既存ペインと要求内容が食い違っても実行は捨てません。判定は `launch_fingerprint`（CLI 名 + argv + cwd）で行い、食い違いは警告（セッションごとに 1 回）と status の `restart_required` で境界待ちとして伝えます。`revision_applied` は実際に解決へ使った revision を報告します（ファイルの最新値ではありません）。

---

## 3. 外部との契約

### 3.1 イベントフック（pull）

```python
def check(config=None) -> str | dict | None:
    """str=送る本文 / dict={"prompt", "cwd"?, "vars"?} / None=今回は送らない"""

def ack() -> None:
    """任意。tmux への送信が成功したときだけ呼ばれる"""
```

引数なしの `check()` も有効です。1 引数を受ける場合は、エントリ名・ID・fallback 可否・`hook_config`・cwd・workspace が渡ります。dict の `cwd` は実在するディレクトリだけを受理し、`vars` は `prompt.format_map` へ渡ります。フックには環境変数 `AGENT_LOOP_EVENT_HOOK_FALLBACK`（`1` / `0`）と `AGENT_LOOP_PROMPT_NAME` が渡ります。

イベントを既読にするのは `ack()` です。`check()` が返した時点ではまだ確定しません。

**スクリプトの指定**: `hooks` にはパスも名前も書けます。ディレクトリ区切りを含まない名前（`gitlab-issue-hook`）なら、実行ファイルと同じ prefix の `hooks/`、次に agent-loop 本体と同じ階層の `hooks/` を探し、拡張子 `.py` を補います。区切りを含む指定と絶対パスは、そのまま解決します。読めなければ WARNING を出してスキップします（デーモンは止まりません）。

**複数指定**: 配列で書くと、発火のたびに全部の `check()` を呼びます。プロンプトを返したフックの数だけ dispatch request ができ、`ack()` はそれぞれのフックへ返ります。1 件も返さなければそのエントリは idle 扱いです。重複排除はフック単位なので、別のフックが同じ本文を返しても消し合いません。

```yaml
prompts:
  - name: "GitLab ワーカー"
    hooks: [gitlab-issue-hook, gitlab-mr-hook]   # 文字列 1 本でも可
    hook_config: { labels: ["status:open"] }
    event_hook_fallback: false
    interval_minutes: 5
```

### 3.2 Webhook フック（push）

```python
def handle(ctx) -> dict | None:
    """dict=テンプレートへ注入する key-value / None=無視して 200 を返す"""
```

`ctx` は `name`（ルート名）、`method`、`headers`（キーは小文字）、`query`、`raw`（生ボディ）、`payload`（JSON パース結果。非 JSON なら `{}`）を持ちます。イベント種別の判定も署名検証もフックの仕事で、`ctx.event` のような provider 固有の属性はありません。`ThreadingHTTPServer` なので同時に複数スレッドから呼ばれます。

HTTP の応答は次のとおりです。

| 状況 | 応答 |
|---|---|
| 受理してキューへ積んだ | 202（tmux への送信完了は待たない） |
| フックが `None` を返した / フックが例外 / `handle` が無い | 200 |
| ルート名に一致するエントリが無い | 404 |
| `secret_header` の値がシークレットと違う | 401 |
| ボディがサイズ上限を超えた | 413 |
| `GET`（`<path_prefix>/_health` 以外） | 405 |

フックの例外を 500 ではなく 200 で握るのは、送信元が 5xx にリトライを重ねて同じ例外を繰り返すのを避けるためです。

### 3.3 inbox メッセージ

置き場は `~/.kiro/agents/<agent_name>/inbox/`、処理済みは同じ階層の `.processed/` です。

| フィールド | 型 | 必須 | 意味 |
|---|---|:---:|---|
| `id` | str | ✅ | メッセージ ID（UUIDv4 hex） |
| `from` | str | ✅ | 送信元エージェント名 |
| `to` | str | ✅ | 宛先エージェント名 |
| `created_at` | float | ✅ | Unix timestamp |
| `body` | str | ✅ | 本文（プロンプトのベース） |
| `subject` | str | — | 件名 |
| `reply_to` | str | — | 返信元の**メッセージ ID**。エージェント名は入れない |
| `correlation_id` | str | — | 会話スレッドの追跡 ID |
| `cwd` | str | — | 送信元の作業ディレクトリ |

`reply_to` にエージェント名を入れないでください。返信先の相手は `from` から引きます。`msg` の `BODY` は、同名のファイルが実在すればその中身を本文に使います。

### 3.4 限定ツール契約と受入条件

ツールループを内蔵しない CLI（定義の `headless_autonomy: single-shot`）には、次の 4 つだけを供給します。

| ツール | 引数 | 制約 |
|---|---|---|
| `read_files` | `paths` | 作業フォルダ配下のみ |
| `write_files` | `files` | 同上。`..` とシンボリックリンクによる逸脱は拒否 |
| `run` | `command`, `args`, `timeout_sec` | シェル文字列は不可。実行ファイルは PATH 上かロード済みスキル配下。`sh` / `bash` / `powershell` などのシェル自体も不可 |
| `final` | 結果 | 完了の宣言 |

ツールへ渡す制御応答（次の一手を書いた JSON）は、定義が用途別の変種（`variants.planner`）を申告していればその起動形へ振り替えます。編集そのものは元の CLI のままです。

受入条件（`acceptance` / `run --acceptance`）は自然文で書きます。機械が照合するのは、**バッククォートで囲まれた、パスの形をしたプロジェクト内の表記**だけです。パスの形とは、区切りの `/` か `\` を含むか、末尾が拡張子（`.md` など 1〜10 文字）であること。空白を含む断片はコマンド行とみなして外します。だから `` `agent-audit` `` のようなコマンド名は照合対象になりません（拾ってしまうと、永久に満たせない条件になります）。

抽出されたパスは、実在するか・この実行で触れたか・実際に変わったかの 3 つを全部満たしたときだけ通ります。1 つでも欠ければ fail です。URL とハイフン始まりの表記、作業フォルダ外の参照も外します。

```yaml
acceptance:
  - "`reports/audit-digest.md` が今回の実行で更新されている"   # 機械層が照合する
  - 直近 24 時間のエラーが発生元ごとに件数付きで列挙されている   # 判定層（opt-in）へ回る
```

パスの形をした表記を 1 つも含まない受入条件しか無いエントリは、判定層を有効にしていなければ「検証なし」として記録されます。条件が書いてあることと、機械で照合できることは別です。

**この機械層は対話ペインでも同じように回ります。** dispatch の直前に指紋を取り、ターンの
完了時に照合します。判定は**ヘッドレスと同じ 1 実装**（`toolloop.acceptance_outcome`）で、
`verifiedBy` も同じ語彙（`machine` / `judge` / `machine+judge`）で記録します。以前ペインは
**画面が idle に戻っただけで完了**として返しており、`acceptance` を宣言していても誰も
見ていませんでした——経路によって done の意味が違っていたことになります。

ペインもヘッドレスの層2（`headless_autonomy: tool-loop`）も、エージェントが触ったファイルを
外から観測できません。だからどちらも「受入条件が名指ししたファイルの指紋が変わったか」で
見ます——**この 2 つは元から同じ精度**です。宣言外のファイルを勝手に触ったことは、どちらの
経路でも検知できません（git 差分を足す案が別途あります）。

**判定層（次項）は対話ペインではまだ走りません。** 判定にはエージェントの報告本文が要り、
`capture-pane` の画面は装飾込みで壊れやすいためです（正典は `session_log` に置きます）。
黙って無視はせず、ペイン経路のエントリが `acceptance_judge` を宣言していたら起動時に
警告します。`verifiedBy` に `judge` が出ないので、機械層だけで通したことは後から
区別できます。

#### 判定層（`acceptance_judge`。既定 off）

機械層が触れない基準——パスの形をした表記を含まない自然文——を、**読み取り専用の検証エージェント**に判定させます。

| 設定 | 場所 | 既定 |
|---|---|---|
| `acceptance_judge` | トップレベル | `false` |
| `acceptance_judge` | エントリ（トップレベルより優先。未指定なら継承） | 未指定 |
| `--judge` | `agent-loop run` | off |

既定を off にしてあるのは、判定のために CLI をもう 1 回起こすからです。「ファイルが更新されたか」だけの定期プロンプトに毎回もう 1 回分のトークンを払う理由はありません。

**判定役は定義側が決めます。** `agents/<name>.json` の `variants` に `verify` の申告があればそちらへ振り替え、変種自身の既定モデルを使います（呼び出し元が明示指定していない場合）。申告が無ければ作業した CLI がそのまま判定します——これは**最も弱い構成**です。自分の仕事を自分で採点することになるので、判定を本気で使うなら定義に `verify` 変種を置いてください。

**fail-closed です。** 次はすべて「満たしていない」に倒します。判定を頼まれて判定できなかったことを pass として記録すると、機械層を入れる前より悪くなるからです。

- 判定役を起動できない（定義が壊れている・CLI が無い）
- 判定の実行が非ゼロ終了 / timeout した
- 出力から JSON を読めない
- 基準の一部について判定が返ってこない

判定に渡すのは、基準・エージェントの報告本文（先頭 4,000 字）・この実行で変わったファイルの一覧だけです。判定役は読み取り専用で起動するので、必要ならファイルを自分で読みます。プロンプトには「報告だけでは証拠にならない」「確かめられないものは fail」と明記します。

**限界**: 判定は実行が終わってから 1 回だけ走ります。ラウンドの途中では判定しないので、判定層が落とした基準をエージェントがその場で直す機会はありません（次の実行で作り直します）。ラウンドごとに判定させるとコストが基準の数だけ増えるため、この形にしています。

結果の `verifiedBy` で、どの層が検証したかを区別します。

| `verifiedBy` | 意味 |
|---|---|
| `machine` | ファイル指紋で照合した |
| `judge` | 検証エージェントが判定した |
| `machine+judge` | 両方 |
| `""`（空） | 誰も検証していない（`verified: false`） |

### 3.5 結果契約（`run` / `statemachine`）

どちらも終了時に `RESULT {json}` を 1 行出力します。呼び出し側（dashboard など）はこの行を読みます。`statemachine` が返すのは `ok` / `stdout` / `finalState` / `logFile` / `files` です。

`statemachine` は `--workflow` か `--entry` のどちらか一方を取ります（両方・どちらも無しは終了コード 2）。`--entry` はエントリ名で、ワークフローの位置と実行条件を `agent-loop.yaml` から引きます（§2.3.1）。エントリが `cwd` を宣言していて `--dir` が無ければ、そちらを作業ディレクトリにします。

**配布の契約検査（起動時）。** 実行の最初に、解決した `statemachine-use` の `next_state.py` が現行契約かを `--help` で確かめます。ハーネスは `--auto-eval` を**値の無いフラグ**として `--context` の後ろに渡すので、古い配布（旧 `--list-conditions`）や `--auto-eval` が値を取る変種だと噛み合いません。噛み合わないときは **LLM を 1 回も呼ばずに** 終了コード 1 で落とし、使用中の実体のパス・探索順・再配布コマンドを返します（argparse の生エラーだけが残ると、複数ある探索先のどれが使われたのか人が特定できないため）。

**決定的検査に達したときの追加フィールド。** ステートが `check`（`statemachine-use` の決定的検査コマンド）を宣言していて、再投入を使い切っても通らなかった場合、結果に `escalate: true` と `check`（`state` / `attempts` / `argv` / `check_status` / `check_output`）が加わり、**終了コードは 3** になります。これは「失敗した」ではなく「**この実行レベルでは解けない**」の宣告で、呼び出し側は上位の段（より能力の高いモデル）へ回す判断に使えます。

意味の切り分けは次のとおりです。

| 終了コード | 意味 | 呼び出し側の打ち手 |
|---|---|---|
| 0 | 完走した | — |
| 1 | 失敗した（出力契約違反・遷移不一致・実行エラー） | 定義か環境を直す |
| 3 | 検査が再投入上限まで通らなかった | 段を上げて投入し直す |

`escalate` と `check` は加算的なフィールドです。`RESULT` を読むだけの既存の呼び出し側、および非 0 を一律に失敗として扱う呼び出し側は、そのままで壊れません。

### 3.6 ターン完了 hook（内部契約）

エージェント CLI 定義が `interactive.turn_completion` を宣言すると、**agent-loop が起動した pane に限って** CLI 自身のターン完了イベントを完了検知に使います。YAML には出てきません。設定するのは `agents/<name>.json` の側だけです。宣言できる値と注入方法は次のとおりです。

| 値 | 注入方法 | 正常終了 | 失敗の扱い |
|---|---|---|---|
| `kiro` | private な `KIRO_HOME` へ設定と資源をホワイトリスト複製し、`--agent` を差し替え | `stop` | pane の死亡と timeout |
| `claude` | `--plugin-dir` で hook だけの plugin を追加 | `Stop` | `StopFailure` |
| `codex` | 一度きりの `--config notify=…`（既存 notify は多重化） | `agent-turn-complete` | pane の死亡と timeout |
| `copilot` | `--plugin-dir` | `agentStop` | `errorOccurred(recoverable=false)` を hint として記録 |
| `opencode` | plugin だけを置いた `OPENCODE_CONFIG_DIR` | `session.idle` | `session.error` を hint として記録 |
| `ollama` | **資産なし**（env だけ）。前面が我々の TUI なので、ターンの終わりに自分で `hook-event` を呼ぶ | `turn_end` | 例外と中断（Ctrl-C）を `failure` として通知 |

**`ollama` だけは資産が要りません。** ほかの adapter は相手の CLI のプラグイン機構へ
hook を差し込む必要がありますが、`ollama` の対話面（`agent-herd ollama --tui`）は
**我々の実装**なので、ターンの終わりに同じコマンドを直接叩けます。管理下の pane で
なければ（env が無ければ）何もせず、通知に失敗しても対話は続きます——知らせられ
なかったときは画面監視（`busy_pattern`）が拾うので、そこで落ちる理由がありません。
人が Ctrl-C で止めたターンは `failure` として通知します（成果の無い実行を完了として
記帳しないため）。

hook は `agent-loop hook-event` を呼び、`~/.agents/loop-hooks/<instance-id>/` の mailbox（`active/<pane-id>.json` と `events/<dispatch-id>.json`、ディレクトリ `0700` / ファイル `0600`）へ書きます。SlotMonitor が画面判定より先にこれを claim し、既存の完了・失敗コールバックへ渡します。hook 自身はセマフォを解放しません。

`hook-event` が状態を書き換えるのは、次を**すべて**満たしたときだけです。満たさない通知は黙って無視し、終了コード 0 を返します（CLI 側の停止処理を邪魔しないため）。

1. `AGENT_LOOP_INSTANCE_ID` に対応する runtime がある
2. `$TMUX_PANE` に対応する active レコードがある
3. `AGENT_LOOP_HOOK_TOKEN` が active レコードと一致する
4. adapter が active レコードの `agent_cli` と一致する
5. instance ID・pane ID・dispatch ID・generation が有効

次のいずれかでは画面監視へ戻ります。起動を止めることはありません。

- 定義が `interactive.turn_completion` を宣言していない、または未知の値（定義の読み込み自体はエラー）
- hook 資産が見つからない、runtime の準備に失敗した
- Kiro v3 や、安全に複製できない agent 形式

`doctor` は adapter の資産と runtime ディレクトリを点検します。利用者の global / project 設定、手動で起動した CLI、外部 pane には触りません。

### 3.7 グローバル指示（agent-instructions）

管理面（agent-dashboard）が原子的に書き換える `$AGENT_INSTRUCTIONS_DIR`（既定 `~/.agents/instructions/`）の `instructions.json` を pull 型で読み、**送信プロンプトの先頭へ前置**します。正典は `schemas/agent-instructions.schema.json` で、agent-loop 側に設定キーはありません。

- 前置するのは、**そのペインへまだ注入していないか、`revision` が変わったとき**だけです。同じ revision を同じペインへ二度は入れません
- レンダリングは決定的で、dashboard（JS）・agent-flow・agent-loop が同一出力になります
- 長さは既定 2,000 文字で丸め、宣言できる上限は 8,000 文字です（0 以下は既定へ）
- 実際に適用した revision は agent-control の status へ `instructions_revision_applied` として載せます
- **フェイルセーフ**: 不在・破損・`enabled: false`・本文が空・既にマーカーが混入済みは、すべて no-op です。注入の失敗で送信を止めません

---

## 4. 規約

**`slash` の書き方**: 各要素は `<name> [args]` で、名前は `^[a-z0-9][a-z0-9._-]*$`。先頭の `/` は書きません（付いていれば警告して剥がします）。規約外の要素はその要素だけ捨て、エントリは生かします。送信順は、fresh context の破棄コマンド、`slash` を宣言順に 1 件ずつ、`prompt` 本文です。失敗した時点で後続を止めます。

**webhook のルート名**: エントリ名の英数字・`_`・`-` 以外を `-` に置き換え、前後の `-_` を落として小文字化したものが URL のパス名です。ルート表は持たず毎リクエスト引き直すので、reload 後も古い宛先は残りません。

**テンプレートの波括弧**: フックが返した辞書は `{key}` へ注入します。未定義のキーは `{key}` のまま残るので、誤記があってもクラッシュしません。波括弧そのものを書きたいときは `{{ }}` でエスケープします。

**tmux セッション名**: `agent-loop-<ラベル>-<パスの sha1 先頭 8 桁>-<インスタンス ID>`。ラベルは起動ディレクトリ名を 24 文字まで安全化したものです。同じ cwd なら同じ名前になります。

**環境変数名**: `environment_handoff.token_env_names` は `^[A-Z_][A-Z0-9_]*$` に一致する名前だけを受理し、渡すのは `SET` か `UNSET` だけです。値そのものはプロンプトにも起動環境にも入れません。

**パス**: エントリの `cwd` は実在するディレクトリのみ。ツール契約と `statemachine --workflow` は作業フォルダの内側に限り、正規化して外へ出る指定を拒みます。

---

## 5. 制約

### 5.1 上限と待ち時間

| 対象 | 値 | 変えられるか |
|---|---|---|
| `check()` の待機 | 30 秒で打ち切り、そのフックは完了か reload まで隔離 | 不可 |
| `preflight` | 15 秒で打ち切り | 不可 |
| `send` の重複排除 | 同じエントリと本文が 3 秒以内なら破棄（成功扱い） | 不可 |
| webhook のボディ | 既定 1MB | `webhook.max_body_bytes` |
| webhook のキュー | ルートごと 100 件。超過は古いものから捨てて警告 | 不可 |
| ツールループの往復 | 8 往復 | 不可 |
| ツール `run` の timeout | 既定 60 秒、上限 300 秒 | 呼び出しの `timeout_sec`（上限まで） |
| ツール結果の自動読み戻し | 32KB まで | 不可 |
| headless CLI の 1 回の実行 | 既定 600 秒 | CLI 定義の `timeout` |
| 受入条件の判定（`acceptance_judge`） | 既定 600 秒（本体と同じ fallback） | CLI 定義の `timeout`（変種側） |
| スロットの強制解放 | 7200 秒 | `slot_timeout_seconds` |
| 処理開始待ち（SlotMonitor） | 60 秒 | 不可 |
| ペインの起動待ち | 60 秒 | `startup_timeout` |
| `send --wait` | 600 秒 | `--response-timeout` |
| ステートマシンの遷移回数 | 50 ステップ | ワークフローの `config.max_steps` |
| ポーリング間隔 | scheduler 1 秒 / SlotMonitor 2 秒 / inbox 5 秒 / session-monitor 10 秒 | inbox のみ `inbox_poll_seconds`、session-monitor は `health.check_interval_seconds` |

`send --wait` の終了コードは、完了が 0、失敗（ペインやプロセスの終了、`failure_pattern` 一致）が 1、タイムアウトが 2 です。

### 5.2 配送保証

保証は **at-most-once** です。daemon が要求をメモリキューへ受理した後にクラッシュしても、再送はしません。webhook のキューはインメモリなので、再起動で未処理分は消えます。送信元は 202 を受けた時点で再送しません。

取りこぼせないイベントは、`hooks` のポーリングを併用して冪等に取りに行ってください。イベントフックは送信成功後の `ack()`、inbox は `.processed/` への移動が確定点なので、受理前に落ちても失われません。

### 5.3 失敗したときにどうなるか

| 事象 | 挙動 |
|---|---|
| `check()` が例外・戻り値が不正・`check` が無い | ログを残してこのサイクルをスキップ。デーモンは動き続ける |
| `check()` が 30 秒を超えた | 待機を打ち切り、そのフックを隔離（thread は増やさない） |
| `preflight` が例外・timeout | fail-open（送る） |
| webhook のフックが例外・`handle` が無い | 200 を返して無視 |
| webhook の bind に失敗 | WARNING を出して本体は継続（HTTP だけ立たない） |
| `cron` 式が不正 | そのエントリだけスキップ |
| `slash` の要素が規約外 | その要素だけ捨てる |
| エントリの型・組合せ違反 | 起動は失敗、`reload` は現行設定と pane を維持 |
| `agent_cli` の定義が未知・壊れている | デーモンは起動エラー。`send` などの補助コマンドは WARNING を出して従来判定で続行 |
| ペインが死んだ | session-monitor が再起動する |
| ペインの画面が `errors[]` の `quota` / `auth` / `env` に一致した | そのターンを失敗として扱い、理由に分類名を残す。`quota` は台帳へ観測行（§1.5） |
| busy・スロット上限・cooldown | 要求を保留して次 tick で再試行（スケジュールは 1 件へ coalesce） |
| セマフォのファイル I/O エラー | 安全側（実行許可）へ倒す |
| 受入条件のファイルが無い・触っていない・変わっていない | 機械層は fail。done の根拠にしない（**対話ペインでも同じ**。理由は `acceptance_failed`） |

### 5.4 効かない組合せと未実装

headless 経路では次が変わります。黙って劣化させず、警告か明示エラーになります。

- `fresh_context` のコンテキスト破棄と `slash` は WARNING を出してスキップ（送る相手の対話ペインが無いため）
- Ralph 多段と external target は起動時に明示エラー
- ensure_session・ready 判定・SlotMonitor を通らない。スロットは合成キー（`headless:<root_id>`）で取り、解放時にノード予算へ記帳する
- semaphore・cooldown・lifecycle は対話経路と同じ契約で効く

そのほかの制約と未実装は次のとおりです。

- ターン完了 hook を注入するのは agent-loop が起動した対話 pane だけ。headless、external pane、手動起動の CLI、Cursor、Kiro v3 は対象外で、画面監視か終了コードで判定する
- CLI とモデルの差し替えはセッション境界でだけ効く。無限キープのペインはデーモンを再起動するまで替わらない（`status` の `restart_required` で境界待ちが分かる）
- headless の実行ログは `~/.agents/runs/headless/` の JSONL に出る。追う tmux ウィンドウは `headless_window: true` のときだけ自動で開く（既定 false）
- 受入条件の判定層（`acceptance_judge`）は実行が終わってから 1 回だけ走る。ラウンドの途中では判定しないので、判定で落ちた基準をその実行のうちに直す機会は無い
- Ralph は daemon 再起動をまたいで途中再開しない。dirty な sandbox は自動削除しない
- `update` は zipapp インストール専用。source / pip / symlink は理由付きで非 0 を返し、daemon 稼働中は update lock で断る
- `statemachine` が受理するのは `statemachine-use` の 1 経路だけ。OS レベルの副作用隔離は持たず、境界は argv・cwd・実行ファイル・パスの検証と監査ログ

---

## 付録: ファイルとディレクトリ

| パス | 中身 |
|---|---|
| `~/.agents/agent-loop.yaml` | 共通設定 |
| `~/.agents/agent-loop.log` | ローテートログ（7 世代） |
| `~/.agents/loop-state/<pid>.json` | デーモンの状態（`ls` / `send` が読む） |
| `~/.agents/loop-commands/<pid>/` | `pause` / `cancel` / `drain` / `reload` の受け口 |
| `~/.agents/loop-control/` | workspace 単位の永続 local pause |
| `~/.agents/loop-adaptive/<entry-id>.json` | 動的インターバルの状態 |
| `~/.agents/loop-hooks/<instance-id>/` | ターン完了 hook の mailbox（`active/` と `events/`。`0700` / `0600`） |
| `~/.agents/send-requests/` | CLI send の受付キュー |
| `~/.agents/send-responses/` | `send --wait` が読む request 単位の完了状態 |
| `~/.agents/runs/headless/` | headless 実行の JSONL ログ |
| `~/.agents/tuning/tuning.json` | agent-tuning の注入プロファイル（`$AGENT_TUNING_DIR` で変更可） |
| `~/.kiro/slots/` | 同時実行スロットとクールダウン（`.lock` は fcntl のミューテックス） |
| `~/.kiro/agents/<name>/inbox/` | エージェント間メッセージ（`.processed/` は処理済み） |
| `<project>/.agents/agent-loop.yml` | プロジェクトの定期プロンプト |
| `~/.agents/instructions/instructions.json` | グローバル指示（`$AGENT_INSTRUCTIONS_DIR` で変更可。§3.7） |
| `<install prefix>/hooks/` | `hooks` から名前で引ける同梱スクリプト（`install.sh` が配置） |
| `<install prefix>/agent-hooks/` | ターン完了 hook の CLI 別資産（同上） |

`~/.kiro/` 配下の 2 つ（スロットとエージェント inbox）は旧 kiro-loop 系統と共有していた置き場です。旧系統は退役済みですが、稼働中の inbox とスロットを移設する実利が無いのでパスはそのままにしています。

## 付録: テスト

`tools/agent-loop/test/` に 51 ファイル・523 件。tmux とエージェント CLI はスタブへ差し替えるので、どちらも無い環境で全件が通ります（webhook だけは実 HTTP の E2E です）。

```bash
python3 -m unittest discover -s tools/agent-loop/test
```
