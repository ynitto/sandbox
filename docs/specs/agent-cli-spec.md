# エージェント CLI プラグイン契約 仕様書

> 設計の「なぜ」は [`docs/designs/agent-cli-plugin-design.md`](../designs/agent-cli-plugin-design.md)、
> 定義の追加手順は [`agents/README.md`](../../agents/README.md)。
> 本書は**契約**（フィールド・探索順・ローダ API・失敗クラス）を引く場所です。
> 正典スキーマ: [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json)
> Python ローダの 1 実装: `tools/agent-tools/agentcore/agentcore/agentcli.py`

---

## 1. 置き場と探索順

**1 ファイル = 1 エージェント**で、`agents/<name>.json` を `agent_cli: <name>` で選びます。
用途で使い分ける起動差は別ファイルに分けず、定義の中の `profiles` に置きます（§2.3）。

| 順 | 場所 |
|---:|---|
| 1 | `$KIRO_AGENTS_DIR` |
| 2 | `<プロジェクトルート>/agents/`（実行時 cwd） |
| 3 | `~/.agents/agents/` |
| 4 | `~/.kiro/agents/` |
| 5 | 同梱定義（リポジトリ直下 / パッケージ resources の `agents/`） |

**同名は先勝ち**で、組み込み名の予約はありません——上位に `claude.json` を置けば同梱定義を
上書きできます。これが無いと受入条件（JSON 1 ファイルで完結）が成り立ちません。ユーザー共通の
定義は `~/.agents/agents/` だけを読み、旧 `~/.agent` ホームへはフォールバックしません。

実ファイルが見つからなかった名前は、最後に `<base>-<profile>` として解き直します
（`ollama-list` → `base=ollama` / `profile=list`）。**実ファイルが常に優先**なので、
profile を独立したエージェントにしたくなったら `ollama-list.json` を置けば勝ちます。
区切りは `-` で、**長い base から順に**試します——`ollama-list-thinking` は
`base=ollama-list` / `profile=thinking` を先に当たり、それが無いので `base=ollama` /
`profile=list-thinking` に解けます（`ollama-list.json` が実在してそこに `thinking` profile が
あれば、そちらが勝ちます）。

未知の `agent_cli` で定義も profile も無ければ**明示エラー**です（黙るフォールバックは廃止）。例外は
cowork の定常業務 tmux 実行だけで、定義解決に失敗しても `kiro-cli chat --trust-all-tools` へ
落として定常業務を止めません。ただし黙らず `console.warn` で理由を残します。

---

## 2. 定義のフィールド

スキーマは `additionalProperties: false`、必須は `command` だけです。既定値はローダが埋めます。

### 2.1 トップレベル

| キー | 型 | 既定 | 意味 |
|---|---|---|---|
| `command` | array | （必須） | ヘッドレス起動 argv。`{model}` / `{output_file}` を展開 |
| `command_suffix` | array | `[]` | argv 末尾に必ず置くトークン（codex の `-` など、位置が意味を持つもの） |
| `prompt_via` | `stdin` \| `argv` | `stdin` | プロンプトの渡し方 |
| `prompt_flag` | str \| null | `null` | argv 渡しのときのフラグ |
| `model_flag` | str \| null | `null` | モデル指定フラグ（`command` に `{model}` が無いときだけ付く） |
| `default_model` | str \| null | `null` | 呼び出し元が省略したときのモデル |
| `output` | `stdout` \| `file` | `stdout` | 応答の取り出し先 |
| `file_flag` | str \| null | `null` | 編集対象ファイルを渡すフラグ |
| `read_flag` | str \| null | `null` | 読み取り専用で渡すファイルのフラグ |
| `env` | object | — | 追加環境変数 |
| `timeout` | num \| null | `null` | 1 回の実行上限（秒）。`null` / `0` は呼び出し側の fallback（agent-loop は 600 秒、agent-audit は `--agent-timeout`・既定 300 秒）。**既定で足りる CLI は宣言しない**——宣言すると呼び出し側が fallback を変えてもその CLI だけ古い上限のまま残る |
| `empty_output_is_error` | bool | `true` | 空応答を失敗とみなすか |
| `write_args` | array | `[]` | 既定（書き込み可）モードのフラグ |
| `readonly_args` | array | `[]` | 読み取り専用モードのフラグ。`write_args` と**排他**（追加ではない） |
| `no_session_args` | array | `[]` | セッション永続化を切るフラグ |
| `readonly` | `enforced` \| `best-effort` | `best-effort` | 読み取り専用の強制力の申告 |
| `relative_cost` | number | `1` | 同じ仕事 1 回の無次元コスト（ローカル 0 / 通常クラウド 1） |
| `headless_autonomy` | `tool-loop` \| `single-shot` | `single-shot` | ヘッドレス 1 回で自分でツールを回して完遂できるか |
| `slash_native` | bool | `headless_autonomy` から導く | 本文先頭のコマンド行（`/name [args]`）をこの CLI へ**残して渡す**か、ランチャが**消費する**か（§2.4） |
| `variants` | object | — | 用途 → 代わりに使う定義名（§4） |
| `profiles` | object | — | 用途別の起動差。`<name>-<profile>` の綴りと `variants` の値がここへ解決される（§2.3） |
| `spill` | object | — | 長大プロンプトの退避（`instruction` / `args`。§5） |
| `errors` | array | — | 失敗トリアージ規則（§6） |
| `skill_command_prefix` | str | `/` | スキル起動コマンドの行頭記号（codex は `$`。§2.4） |
| `session_log` | object | — | agent-audit が読む transcript の所在（§7） |
| `interactive` | object | — | 対話モード（§2.2） |
| `name` | str | ファイル名 | 表示名 |

### 2.2 `interactive`

| キー | 既定 | 意味 |
|---|---|---|
| `command` | — | 対話起動 argv |
| `write_args` | — | 対話専用。**トップレベルから継承しない**（強い権限フラグを対話へ黙って持ち込まない） |
| `readonly_args` / `no_session_args` | トップレベルを継承 | 対話側で上書き可 |
| `ready_pattern` | 組み込み既定 | 入力受付を検出する正規表現 |
| `ready_timeout_sec` | `60` | ready を待つ上限 |
| `ready_tail_lines` | `3` | ready 判定で見る画面末尾の行数 |
| `busy_pattern` | — | **処理中の正の検出**。入力欄を常時出す TUI ではこれが判定の正になる |
| `idle_quiet_sec` | `0`（無効） | どちらのパターンも持てない CLI 向け。画面が N 秒変化しなければ待機 |
| `failure_pattern` | — | `agent-loop send --wait` の明示的な失敗。未指定なら pane / process 終了以外を推測しない |
| `clear_command` | `/clear` | コンテキスト破棄コマンド（codex は `/new`、無い CLI は空文字） |
| `prompt_inject` | — | 初回プロンプトの注入方法（`send-keys` \| `file`） |
| `turn_completion` | `""` | ターン完了 hook のアダプタ。`kiro` / `claude` / `codex` / `copilot` / `opencode` のいずれか。未知の値は**起動時エラー** |

**待機判定の優先順位**は busy ＞ ready ＞ 静穏 ＞ 既定 busy でコード側に固定してあります。

### 2.3 `profiles`（用途別の起動差）

同じエージェントを用途で使い分けるとき、**別定義ファイルへ分けずここへ置きます。** 分けると
`agent_cli` が用途ごとに増え、台帳と格付けのキー `(agent_cli, model, operation_class)` のうち
用途の次元を二重に持つことになり、1 実行系の実測が偽の候補へ割れます。

```json
"profiles": {
  "list": { "command": ["agent-herd","ollama","--think","off","--format","array","{model}"],
            "write_args": [], "readonly_args": [], "headless_autonomy": "single-shot" }
}
```

| | |
|---|---|
| profile 名 | `[\w.-]+` |
| 必須 | `command` だけ（トップレベルと同じ） |
| 置けるキー | トップレベルのうち起動に関わるもの。`profiles` / `session_log` / `spill` / `name` は**置けません**（スキーマとローダの両方が拒否し、何が置けるかを名指しで返す） |
| 継承 | 宣言があれば置き換え（`[]` の宣言も「置き換え」）。宣言しなければ base をそのまま継ぐ |
| `env` | 例外的に base へ**重ねる**（profile の宣言が勝つ） |
| 継承しないもの | `interactive` / `variants` / `slash_native` の 3 つ。継承すると対話面を持たない役割に base の TUI が生え、消費側（agent-dashboard）の実行経路が変わる。`slash_native` は profile 自身の `headless_autonomy` から導く |

**返る spec の `name` は正典（base の名前）のまま**で、どの profile で組まれたかは
`spec["profile"]`、選べる一覧は `spec["profiles"]` が持ちます。台帳・格付けへ書く `agent_cli` は
`canonical_name()`（§3）を通してください。

### 2.4 `slash_native`（コマンド行を渡すか消費するか）

本文の**先頭から連続する** `/name [args]` の行はコマンド行です（規約は
[agentcore の `slashroute`](./agentcore-spec.md)）。ランチャは argv を組む前にこの行を読み、
起動形を決めます。そのうえで**行を CLI へ渡すか消費するかは定義が宣言します**。

| 宣言 | 意味 | 例 |
|---|---|---|
| `true` | ネイティブのスラッシュコマンドを持つので**残して渡す**。行頭記号は `skill_command_prefix`（codex は `$`） | claude / codex / kiro / copilot / cursor / agent-ollama / opencode |
| `false` | 持たないので**ランチャが消費する**。スキルとして解決して材料へ載せる | aider / vscode-copilot |

**未宣言のときは `headless_autonomy` から導きます**（`tool-loop` なら `true`）。以前この判定は
その代理で書かれていたので、宣言していない定義（利用者が置いたものを含む）は今日と同じに
振る舞います。**ただし 2 つは別の性質です**——`headless_autonomy` は「自分でツールを回せるか」、
`slash_native` は「スラッシュを自分で解釈するか」で、片方だけ真の CLI はありえます。
同梱定義はすべて自分で宣言しています。

---

## 3. ローダ API（`agentcore.agentcli`）

| 関数 | 返すもの |
|---|---|
| `load_cli(name, project_dir=None, *, use_cache=True)` | 正規化済み spec。見つからなければ `AgentCliError` |
| `plugin_dirs(project_dir=None)` | §1 の探索順のディレクトリ列 |
| `normalize(name, raw, path)` | 生 JSON → spec（既定の充当と検証） |
| `clear_cache()` | 定義キャッシュの破棄 |
| `headless_cmd(spec, model, prompt, *, readonly, no_session, spill_path, files, read_files)` | `{argv, stdin, output_file, env, empty_output_is_error, timeout, readonly_warning}` |
| `interactive_cmd(spec, model, *, readonly, no_session)` | 対話起動の argv 一式 |
| `canonical_name(name, project_dir=None)` | 台帳・格付けへ書く正典の `agent_cli` 名。`ollama-list` → `ollama`。**綴りでは判定せず定義に問い合わせる**ので、`ollama-list.json` が実在すればそのまま返す。解決できない名前は素通し |
| `resolve_variant(name, purpose, project_dir=None)` | `{agent_cli, default_model}` or `None`（§4） |
| `costlier_fallback(current, candidates, project_dir=None)` | 現在より高コストの最初の 1 件 or `None`（§4.2） |
| `classify_error(spec, blob, *, detailed=False, now=None)` | `(class, hint)`、`detailed=True` なら quota 細分と `reset_at`（§6） |
| `parse_usage(stderr)` | `(tokens_in, tokens_out)`。`@agent-usage` 行の**最後の一致**だけを読む |
| `spill_prompt(prompt, limit, *, prompt_via, prefix)` | argv 長超過の退避（§5） |
| `spill_instruction(what, *, then)` | 退避時の指示文の枠 |
| `readonly_warning(spec, readonly)` / `ready_pattern` / `ready_timeout_sec` / `busy_pattern` / `idle_quiet_sec` / `clear_command` / `prompt_inject` / `skill_command_prefix` / `rewrite_skill_commands` | 各宣言の取り出しと既定の充当 |

**`load_cli` が返す spec に `session_log` は入りません。** ログの所在は argv ローダの入力では
ないので `normalize` が落とします。読み手（agent-audit）は `plugin_dirs` だけを借りて生 JSON を
直接読みます。

---

## 4. `variants`（用途別の振り替え）

**申告が唯一の許可リストです。** 「この用途にはこの変種を使うか」は定義が申告し、エンジンは
**用途の 1 語を渡すだけ**です。調停（どの変種へ振り替え、どのモデルを使うか）は
`agentcore.slashroute.resolve` の 1 実装が行います。エンジンが CLI 名で分岐する必要も、
用途の許可リストを持つ必要もありません。

以前はエンジンごとに許可リストがありました（agent-flow は 9 用途・agent-project は 6 用途・
agent-audit は 2 用途）。定義が 15 用途を申告しても引かれない用途があり、**「宣言したのに
効かない」が静かに起きていました**。許可リストは削除済みです
（[2026-08-27 設計](../plans/2026-08-27-agent-herd-cloud-cli-parity-slash-dispatch-design.md) §3.3）。

- 指す先が存在しない・自分自身を指す申告は**無視して元の定義で走ります**（設定ミスで実行を殺さない）
- 振り替えは **1 段だけ**で連鎖しません
- **指す先は定義名でも profile の綴りでもかまいません。** `variants: {"split": "ollama-list"}` は
  `ollama` の `list` profile へ解決されます（§1）。返る `agent_cli` の綴りはそのままなので、
  台帳へ書く前に `canonical_name()` を通します——**変種は入口を増やさず、定義を増やすだけ**です
- **セッション操作のコマンド名（`/help` `/model` `/tools` …）は用途ではありません。**
  名前空間が 1 つなので、これらのキーを申告しても振り替えには使われません

### 4.1 振り替え後のモデル

変種は用途専用にチューニングされた `default_model` を持つことが多いので（`ollama-verify` の
`gemma4:12b` など）、**原則としてそちらを使います**。上書きしない層が 2 つあります。

| 層 | 変種の既定で上書きするか | 理由 |
|---|---|---|
| 人が設定へ明示したモデル（`agents:` の用途別 `model`・run 単位の固定） | **しない** | 用途を知ったうえでの明示だから |
| 用途別順位表（`selection_policy.by_purpose`）由来の決定 | **しない** | その用途の実測（operation_class 別の格付け）で選ばれているから。上書きすると、judge で bounded-review の裏付けを持つモデルが選ばれたのに、変種の既定で **blocked と実測されているモデル**へ黙って戻る |
| agent-control / tier の自動割り当て・縮退指定・用途を知らない共通候補列 | **する** | 「その CLI を用途を問わずそのまま使う」という明示ではないので、変種の用途専用チューニングのほうが良い推定 |

**この調停は 1 実装（`agentcore.slashroute.resolve`）で、engine は用途の 1 語と
「どの層がモデルを決めたか」の 2 つを渡すだけです。** 用途別順位表を読む口を持つのは
agent-flow だけで、agent-project / agent-audit / ハーネスは legacy の `agents:` 層しか
見ません——`by_purpose` 由来の決定はそこへ届かないので、「明示でないモデル」として
紛れ込んで変種の既定へ戻ることが起きません。**これらへ `selection_policy` を教えるときは、
同じ呼び出しで `by_purpose` を渡さないと静かに壊れます**（4 経路それぞれのテストが縛ります）。

用途語彙は 15 個で、同梱定義（`ollama` / `aider`）の申告がこれを覆っています。

### 4.2 `fallbacks` は定義ではなくエンジンの設定

`relative_cost` は**定義が申告**しますが、**`fallbacks` は定義のフィールドではありません**
（スキーマにも無い）。エンジン側の役割別設定（agent-flow の `agents:` 上書き、agent-project の
同等キー）が候補列を宣言し、`costlier_fallback` がそこから現在より高コストの最初の 1 件だけを
返します。実行回数の上限は各エンジンが持ちます。

---

## 5. 退避（spill）— 別物が 2 つある

混ぜると壊れるので、層を分けてあります。

| | 定義の `spill` | `agentcli.spill_prompt` |
|---|---|---|
| 見ているもの | CLI の癖（kiro-cli は positional プロンプト併用時に stdin を読まない） | OS の `ARG_MAX` |
| 権限フラグ | `spill.args` で**置き換える**（本文を読ませるためにファイル読み取りだけ許す） | **触らない** |
| 用途 | dashboard の診断など読み取り専用の経路 | ヘッドレス実行全般 |
| 閾値 | — | 設定 `argv_limit`（既定 100,000。ノードごとに上書き可） |

ヘッドレス実行に前者を掛けると、退避したときだけコマンドを 1 つも実行できなくなり、検証が全基準
「検証不能」に倒れます。`spill_prompt` は `prompt_via` も見て、stdin 渡しの CLI では何もしません
（stdin は `ARG_MAX` に当たらない）。退避しても OS の上限が閾値より低い環境では `E2BIG` が残るので、
失敗トリアージはこれを `env` に分類します。

---

## 6. 失敗トリアージ

エラー本文（定義の `errors` → 汎用パターンの順）から分類し、メッセージ先頭の機械可読タグ
**`[agent-error:<class>]`** で全層に運びます。分類は**決定的**（正規表現のみ・LLM 不使用）です。

| class | 意味 | 誰が直すか | 各層の動き |
|---|---|---|---|
| `control` | 管理設定による実行停止 | 人（dashboard で許可） | 環境要因の扱い |
| `quota` | 利用上限 | 時間（またはプラン見直し） | 同上 |
| `auth` | 認証切れ | 人（再ログイン） | 同上 |
| `env` | 実行環境（CLI 不在・モデル不正・argv 長超過） | 人（環境修復） | 同上 |
| `transient` | 一時的（タイムアウト・接続断） | 誰も | 通常リトライ |
| （タグ無し） | 内容の問題 | タスク単位の判断 | retry → 裁定 → 人 |

`errors[]` の 1 件は `{match, class, hint?, quota_kind?}` で、`match` と `class` が必須です。
`quota_kind` は `exhausted`（恒久枯渇）と `rate_limit`（時限）に細分し、後者は本文から
`reset_at` を決定的に抽出して node-budget の観測行へ載せます。抽出は絶対時刻
（`reset at <ISO8601>`）と相対時刻（`retry after N s|m|h`）の 2 形式で、相対時刻のときは
`now` を**必須の引数**にします（現在時刻を隠れた入力にしない）。

ヒントは**実際に一致した規則から**採ります——クラス一致で引くと、ある CLI に足した規則の案内文が
別 CLI の失敗に付きます（実際に踏んで直した潜在バグ）。

**環境要因（control / quota / auth / env）の扱い**は 3 層が同じタグを読みます。

1. **agent-flow**: 環境要因の失敗ノードが 1 つでもあれば再計画せず run を即 failed で終端。done ノードは温存＝再開で続きから
2. **agent-project**: リトライを消費せず・裁定も呼ばず、原因と直し方を明記して needs へ
3. **dashboard**: タスク状態より先に「🔑 認証切れ — 再ログイン後、要対応タブで承認すると続きから再開」等を言い切る

---

## 7. `session_log`

agent-audit が CLI 自身の transcript を収集するための宣言です。契約と format の一覧は
[`docs/specs/agent-audit-spec.md`](./agent-audit-spec.md) §3 にあります。

---

## 8. 用途とフラグの対応

かつて実装ごとに食い違っていた箇所なので、1 枚に集約してあります。

| 呼び出し元 | モード | readonly | no_session |
|---|---|---|---|
| act / plan / verify / worker / amigo の手番 | headless | ✗ | ✗ |
| dashboard の charter 補完・Doctor・構造化 Assist・受入条件補完 | headless | ✓ | ✓ |
| dashboard の CLI チャット・cowork の tmux 実行 | interactive | ✗ | ✗ |
| agent-loop の定期プロンプト（`session: keep`・既定） | interactive | ✗ | ✗ |
| agent-loop の定期プロンプト（`session: per-run` / `interactive` 無しの定義） | headless | ✗ | ✓ |
| 対話診断（失敗診断の既定） | interactive | ✓ | ✓ |

---

## 9. 同梱定義

リポジトリ直下 `agents/` に **9 ファイル**。**ファイル数は実エージェント数と一致します**——
用途別の起動差は `ollama` の `profiles`（5 つ）が持ちます。

| 定義 | cost | readonly | 既定モデル | autonomy | 対話 | session_log | profiles |
|---|---:|---|---|---|:-:|:-:|---|
| `claude` | 1 | enforced | 定義なし | tool-loop | ✓ | ✓ | — |
| `codex` | 1 | enforced | 定義なし | tool-loop | ✓ | ✓ | — |
| `copilot` | 1 | best-effort | 定義なし | tool-loop | ✓ | — | — |
| `cursor` | 1 | best-effort | 定義なし | tool-loop | ✓ | — | — |
| `kiro` | 1 | best-effort | 定義なし | tool-loop | ✓ | ✓ | — |
| `opencode` | 0 | best-effort | 定義なし | tool-loop | ✓ | ✓ | — |
| `aider` | 0 | enforced | `gemma4:e4b` | single-shot | ✓ | — | — |
| `ollama` | 0 | enforced | `gemma4:e4b` | tool-loop | ✓ | ✓ | `json` `list` `list-thinking` `read` `verify` |
| `vscode-copilot` | 1 | enforced | 定義なし | single-shot | ✓ | — | — |

`ollama` の profile は base を継ぐので、断りの無い列は base と同じです。違うところだけ:

| profile | 従来の綴り | 既定モデル | autonomy | 起動差 |
|---|---|---|---|---|
| `json` | `ollama-json` | 継承 | single-shot | `--format json`・道具なし |
| `list` | `ollama-list` | 継承 | single-shot | `--format array`・道具なし |
| `list-thinking` | `ollama-list-thinking` | 継承 | single-shot | `--think on` + `temperature 0`・`--format` なし |
| `read` | `ollama-read` | 継承 | 継承（tool-loop） | `--tools read --max-rounds 30` |
| `verify` | `ollama-verify` | `gemma4:12b` | single-shot | `--format json --stall-timeout 180` |

`relative_cost: 0` はローカル実行（`ollama`（profile 含む）・`aider`・`opencode`）です。
これらは `agent-herd` を入口に持ち、クラウド 6 種は素の CLI を指したままです（`vscode-copilot`
だけは素の CLI ではなく自作ブリッジ `vscode-copilot-chat` を指します——VS Code の
Language Model API は編集中の VS Code プロセスの中にしか無く、argv から直接は呼べないため）。詳細は
[`docs/specs/agent-herd-spec.md`](./agent-herd-spec.md)。

`variants` を申告しているのは `ollama`（15 用途）・`aider`（15 用途）と、`ollama` の
`json` profile（`split` / `retrieve`）・`verify` profile（`split`）です（`variants` は
継承しないので、必要な profile だけが自分で宣言します）。

`aider` と `vscode-copilot` は `interactive` を持ちながら `headless_autonomy: single-shot` です。
2 つの宣言は別物（前者は「対話面を提供するか」、後者は「自分で探索・実行まで回せるか」）なので、
**`interactive` の有無を「ハーネスが要るか」の代理に使ってはいけません**——消費側は
`headless_autonomy` で弁別します。

---

## 10. 不変条件

1. **CLI の挙動・作法の変更は `agents/<name>.json` 1 ファイルで完結する**（受入条件）
2. 分類は決定的。判定に迷うものはタグ無し＝「内容の問題」に倒す
3. トリアージは「止める・人へ知らせる」方向にのみ働く。done を作らない・予算を破らない
4. 定義は stdlib（json / re）だけで読める。PyYAML 等の依存を増やさない
5. **読み取り専用の防御はこの層に持たない**——責務が「argv の組み立て」から「実行の隔離」へ
   膨らむと、いま畳んだ散らばりが別の場所に再発する。保証できないことは宣言して人に見せる
6. コード側に CLI 名の分岐を書かない。フォールバックテーブルも持たない
7. **1 ファイル = 1 エージェント。** 用途別の起動差は `profiles` に置き、`agent_cli` の値空間へ
   畳み込まない（用途の次元は `operation_class` / `purpose` が持っている）
