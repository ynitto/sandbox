# agent-loop 仕様書

> 最終更新: 2026-08-12 ／ 対象: `tools/agent-loop/`（`agent_loop` パッケージ）
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
| イベントフック | スケジュール発火時に `check()` が文面を返したとき | `event_hook` | 同上（`check()` は 30 秒まで） |
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

### 1.3 操作コマンド

| コマンド | 何をする | daemon |
|---|---|:---:|
| `agent-loop` | デーモンを起動する（tmux 外なら専用セッションを作って自動アタッチ） | — |
| `ls` | 管理下の tmux セッションとペインを一覧する | 任意 |
| `send PROMPT` | 常駐セッションへ送る。`--wait` `--priority` `--model` `--sandbox` `--force` `--ralph` | 推奨 |
| `run PROMPT` | その場で 1 回実行する。`--agent-cli` `--model` `--acceptance` `--dir` | 不要 |
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
| `prompts` | list | `[]` | 定期プロンプトのエントリ（次節） |

`agent-tuning`（`$AGENT_TUNING_DIR` の `tuning.json`）と手法パックは agent-loop の設定ファイルの外にあります。エントリ側は `tuning_profile` で選ぶだけです。

### 2.3 プロンプトエントリ

| キー | 型 | 既定 | 意味 |
|---|---|---|---|
| `name` | str | 本文の先頭 40 文字 | 表示名。webhook のルート名にもなる |
| `id` | str | 自動採番（UUID） | 明示すると reload をまたいで同一視される |
| `enabled` | bool | true | false のエントリは読み込み時に落とす |
| `prompt` | str | — | 送る本文。`event_hook` か `slash` があれば省略可 |
| `slash` | str \| list[str] | なし | 本文の前に独立送信する CLI コマンド |
| `interval_minutes` | int | — | 送信間隔。1 未満は無効（`webhook` 付きなら push 専用として許容） |
| `cron` | str | なし | 5 フィールドの cron 式。指定すると `interval_minutes` より優先 |
| `run_immediately_on_startup` | bool | false | 起動 30 秒後に 1 回目を送る（別名 `run_immediately`） |
| `cwd` | str | デーモンの起動位置 | このエントリの作業ディレクトリ |
| `fresh_context` | bool | false | 送信前にコンテキスト破棄コマンドを送る |
| `fresh_context_interval_minutes` | int | なし | 破棄を間引く間隔。1 未満は未指定と同じ |
| `exclude_from_concurrency` | bool | false | `max_concurrent` の対象から外す |
| `event_hook` | path | なし | `check()` を呼ぶフックスクリプト |
| `event_hook_fallback` | bool | false | 更新が無いときの代替送信をフックへ許可する |
| `event_hook_config` | dict | なし | `check(config)` へ渡す個別設定。dict 以外は起動エラー |
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

`cron` は「分 時 日 月 曜日」の 5 フィールドで、判定はデーモンが動いている端末のローカル時刻です。曜日の `7` は日曜として扱い、日と曜日を両方指定したときは Vixie cron と同じ OR になります（どちらかに当たれば発火）。

CLI とモデルの解決順は、control.json の `workloads.routine`（予算枯渇時の `degraded` 差し替えを含む）、エントリ、グローバル設定、既定の順です。エントリは管理面より上には行きません。

### 2.4 エントリが採用される条件

読み込み時に次を満たさないエントリは、そのエントリだけが落ちます。

- `enabled: false` ではない
- `prompt` / `event_hook` / `slash` のうち少なくとも 1 つがある
- `cron` が妥当（不正な式は WARNING を出してスキップ）、または `interval_minutes >= 1`
- `interval_minutes` が無い場合は `webhook` ブロックがある（push 駆動専用として残り、自動発火はしない）

一方、次の組合せは**エントリを落とさず、読み込み全体を失敗させます**。起動時なら起動が止まり、`reload` 時なら現行設定を維持します。

| 組合せ | 理由 |
|---|---|
| `mode: ralph` と `oneshot` | 反復と使い捨ての前提が噛み合わない |
| `oneshot` と `clean_session` | pane の寿命の決め方が二重になる |
| `target` と `oneshot` / `clean_session` | 外部 pane の生死は agent-loop が持たない |
| `mode: ralph` で `max_iterations` 未指定、または 1〜100 の外 | 反復の上限が決まらない |
| `mode` / `session` / `oneshot` / `clean_session` / `acceptance` / `event_hook_config` の型違反 | 静かに既定へ倒すと意図しない挙動になる |

headless（`session: per-run`）では、Ralph 多段と external target を組み合わせた時点で起動を明示エラーで断ります。

---

## 3. 外部との契約

### 3.1 イベントフック（pull）

```python
def check(config=None) -> str | dict | None:
    """str=送る本文 / dict={"prompt", "cwd"?, "vars"?} / None=今回は送らない"""

def ack() -> None:
    """任意。tmux への送信が成功したときだけ呼ばれる"""
```

引数なしの `check()` も有効です。1 引数を受ける場合は、エントリ名・ID・fallback 可否・`event_hook_config`・cwd・workspace が渡ります。dict の `cwd` は実在するディレクトリだけを受理し、`vars` は `prompt.format_map` へ渡ります。フックには環境変数 `AGENT_LOOP_EVENT_HOOK_FALLBACK`（`1` / `0`）と `AGENT_LOOP_PROMPT_NAME` が渡ります。

イベントを既読にするのは `ack()` です。`check()` が返した時点ではまだ確定しません。

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

受入条件（`acceptance` / `run --acceptance`）は自然文で書きます。機械が照合するのは、**バッククォートで囲まれたプロジェクト内のパス**だけです。抽出されたパスは、実在するか・この実行で触れたか・実際に変わったかの 3 つを全部満たしたときだけ通ります（1 つでも欠ければ fail）。URL とハイフン始まりの表記、作業フォルダ外の参照は照合対象から外れます。

```yaml
acceptance:
  - "`reports/audit-digest.md` が今回の実行で更新されている"   # 機械が照合する
  - 直近 24 時間のエラーが発生元ごとに件数付きで列挙されている   # 誰も判定しない（判定層は未実装）
```

バッククォートのパスを 1 つも含まない受入条件しか無いエントリは、「検証なし」として記録されます。条件が書いてあることと、機械で照合できることは別です。

### 3.5 結果契約（`run` / `statemachine`）

どちらも終了時に `RESULT {json}` を 1 行出力します。呼び出し側（dashboard など）はこの行を読みます。`statemachine` が返すのは `ok` / `stdout` / `finalState` / `logFile` / `files` です。

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
| headless CLI の 1 回の実行 | 既定 180 秒 | CLI 定義 |
| スロットの強制解放 | 7200 秒 | `slot_timeout_seconds` |
| 処理開始待ち（SlotMonitor） | 60 秒 | 不可 |
| ペインの起動待ち | 60 秒 | `startup_timeout` |
| `send --wait` | 600 秒 | `--response-timeout` |
| ポーリング間隔 | scheduler 1 秒 / SlotMonitor 2 秒 / inbox 5 秒 / session-monitor 10 秒 | inbox のみ `inbox_poll_seconds` |

`send --wait` の終了コードは、完了が 0、失敗（ペインやプロセスの終了、`failure_pattern` 一致）が 1、タイムアウトが 2 です。

### 5.2 配送保証

保証は **at-most-once** です。daemon が要求をメモリキューへ受理した後にクラッシュしても、再送はしません。webhook のキューはインメモリなので、再起動で未処理分は消えます。送信元は 202 を受けた時点で再送しません。

取りこぼせないイベントは、event hook のポーリングを併用して冪等に取りに行ってください。event hook は送信成功後の `ack()`、inbox は `.processed/` への移動が確定点なので、受理前に落ちても失われません。

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
| busy・スロット上限・cooldown | 要求を保留して次 tick で再試行（スケジュールは 1 件へ coalesce） |
| セマフォのファイル I/O エラー | 安全側（実行許可）へ倒す |
| 受入条件のファイルが無い・触っていない・変わっていない | 機械層は fail。done の根拠にしない |

### 5.4 効かない組合せと未実装

headless 経路では次が変わります。黙って劣化させず、警告か明示エラーになります。

- `fresh_context` のコンテキスト破棄と `slash` は WARNING を出してスキップ（送る相手の対話ペインが無いため）
- Ralph 多段と external target は起動時に明示エラー
- ensure_session・ready 判定・SlotMonitor を通らない。スロットは合成キー（`headless:<root_id>`）で取り、解放時にノード予算へ記帳する
- semaphore・cooldown・lifecycle は対話経路と同じ契約で効く

そのほかの制約と未実装は次のとおりです。

- kiro-cli 以外では slot-release の stop hook を注入しない。スロット解放は SlotMonitor のペイン監視だけで行う
- CLI とモデルの差し替えはセッション境界でだけ効く。無限キープのペインはデーモンを再起動するまで替わらない（`status` の `restart_required` で境界待ちが分かる）
- 動的インターバルの error 遷移は関数だけがあり、scheduler へ接続していない。フックの例外・timeout・`None` はすべて idle として扱う
- 受入条件のうち、パスを含まない自然文を証跡付きで判定する層は未実装
- headless の実行ログは `~/.agents/runs/headless/` の JSONL に出るが、それを追う tmux ウィンドウの自動起動は無い
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
| `~/.agents/send-requests/` | CLI send の受付キュー |
| `~/.agents/send-responses/` | `send --wait` が読む request 単位の完了状態 |
| `~/.agents/runs/headless/` | headless 実行の JSONL ログ |
| `~/.agents/tuning/tuning.json` | agent-tuning の注入プロファイル（`$AGENT_TUNING_DIR` で変更可） |
| `~/.kiro/slots/` | 同時実行スロットとクールダウン（`.lock` は fcntl のミューテックス） |
| `~/.kiro/agents/<name>/inbox/` | エージェント間メッセージ（`.processed/` は処理済み） |
| `<project>/.agents/agent-loop.yml` | プロジェクトの定期プロンプト |
