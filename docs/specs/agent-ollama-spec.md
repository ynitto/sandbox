# agent-ollama 仕様書

> 設計の「なぜ」は [`docs/designs/agent-ollama-design.md`](../designs/agent-ollama-design.md)、
> CLI 定義の共通契約は [`docs/specs/agent-cli-spec.md`](./agent-cli-spec.md)。
> 本書は**契約**（定義の割当・CLI フラグ・環境変数・上限・終了状態・出力）を引く場所です。
> 実装: `agentcore/ollama_{adapter,loop,context,events,skills,tui,replay}.py`（約 3,080 行）

---

## 1. CLI 定義の割当

ollama 系の同梱定義は **6 つ**です。共通契約（`write_args` / `readonly_args` / `variants` /
`interactive` / `errors` / `session_log`）は CLI プラグイン契約が正典で、ここでは割当だけを定めます。

| 定義 | `variants` の申告 | 既定モデル | think | write モード | readonly モード | 対話 |
|---|---|---|---|---|---|:-:|
| `ollama` | 15 用途（下記） | `qwen3` | `off`（対話だけ `on`） | `--tools bash --max-rounds 12 --command-timeout 900` | 道具なし | ✓（`--tui`） |
| `ollama-json` | `split`→`ollama-list` / `retrieve`→`ollama-read` | `qwen3` | `off` | `--format json`、道具なし | 同左 | — |
| `ollama-list` | なし | `qwen3` | `off` | `--format array`、道具なし | 同左 | — |
| `ollama-list-thinking` | なし | `gemma4:e4b` | `on` + `temperature 0` | `--format` なし・道具なし | 同左 | — |
| `ollama-read` | なし | `qwen3` | `off` | `--tools read --max-rounds 30 --command-timeout 900` | 道具なし | — |
| `ollama-verify` | `split`→`ollama-list` | `gemma4:12b` | `off` | `--format json --stall-timeout 180`、道具なし | 同左 | — |

`ollama` の `variants` は 12 用途（`planner` `evaluator` `filter` `judge` `reduce` `extract`
`plan` `review` `prioritize` `route` `adjudicate` `assess`）を `ollama-json` へ、`split` を
`ollama-list` へ、`retrieve` を `ollama-read` へ、`verify` を `ollama-verify` へ振り替えます。

**6 定義すべてが `relative_cost: 0` と `readonly: enforced` を宣言し、`session_log` を持ちます。**
既定モデルは 4 定義が `qwen3` ですが、`ollama-list-thinking` は `gemma4:e4b`、`ollama-verify` は
`gemma4:12b` です（用途別にチューニングした既定を base のモデル指定で上書きさせない）。

think はヘッドレスの 6 定義中 5 つで `off` です。例外は `ollama-list-thinking` だけで、
`--format` の文法制約を外したうえで `--think on` と `AGENT_OLLAMA_OPTIONS={"temperature":0}` を
宣言します（gemma4:e4b の split で意味的な完全被覆を安定させるため）。`--tui` の対話起動も
`--think on` を持ちます——人が画面で思考を読める場面です。

`--format json`（ollama の JSON モード）は**トップレベルを必ずオブジェクトにする**ので、配列を
返す契約（agent-flow の split）はプロンプトで何を書いても満たせません。`ollama-list` は
structured outputs のスキーマ `{"type":"array","items":{"type":"string"}}` を渡してトップレベル
配列を表現します。要素を string に固定するのは、split の要素が下流で map ゴールへ文字列として
埋め込まれるためです。

`ollama` の readonly にツールを付けないのは、CLI 契約上の強制力に嘘を入れないためです。
`ollama-read` は write として呼ばれる役割に読取ツールだけを与える別定義で、汎用 `ollama` を
安全側へ書き換えません。

---

## 2. 実行面と CLI フラグ

| 面 | 入口 | 用途 |
|---|---|---|
| plain | `agent-ollama <model>` | text / JSON の単発生成。道具なし |
| bash loop | `--tools bash` | OS ユーザー権限での汎用 work |
| read loop | `--tools read` | 決定的ゲート付きの調査・読取 |
| human / observe | `--tui` / `--status` / `--follow` / `--context` | 対話、進捗追尾、状態・文脈上限の取得 |
| measure | `--replay` | 記録済みプロンプトの再生。**道具を持たない**（§6） |

| フラグ | 既定 | 意味 |
|---|---|---|
| `--model` | 定義の `default_model` | モデル |
| `--tools` | `bash`（ループ時） | `bash` \| `read`。`edit` は予定名として認識し明示エラー |
| `--format` | — | `json` \| `array` |
| `--think` | 定義による | 思考の有効化 |
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

---

## 3. 環境変数

| 変数 | 既定 | 効く先 |
|---|---|---|
| `OLLAMA_HOST` / `OLLAMA_API_BASE` | — | 接続先。**片方だけでも相互に補完**する |
| `NO_PROXY` / `no_proxy` | — | ollama のホストを常に両表記へ追記する |
| `OLLAMA_TIMEOUT` | `600` 秒 | HTTP 全体の上限 |
| `AGENT_OLLAMA_CONNECT_TIMEOUT` | `120` 秒 | 応答ヘッダを得るまで |
| `AGENT_OLLAMA_FIRST_TOKEN_TIMEOUT` | `0`（無制限） | prefill |
| `AGENT_OLLAMA_STALL_TIMEOUT` | `180` 秒 | decode の無進捗 |
| `AGENT_OLLAMA_META_TIMEOUT` | `3` 秒 | `/api/ps` `/api/show` の問い合わせ |
| `AGENT_OLLAMA_THINK` | — | `on` / `off` / `prompt` |
| `AGENT_OLLAMA_OPTIONS` | — | API の `options` へ渡す JSON |
| `AGENT_OLLAMA_KEEP_ALIVE` | — | API の `keep_alive` |
| `AGENT_OLLAMA_SYSTEM_PROMPT` | — | system プロンプトの差し替え |
| `AGENT_OLLAMA_LOG_DIR` | `~/.agents/logs/ollama` | JSONL ログの置き場 |
| `AGENT_OLLAMA_SKILLS_DIR` | — | スキル探索の追加先 |
| `AGENT_OLLAMA_HISTORY` | — | TUI の履歴ファイル |
| `AGENT_OLLAMA_NO_RICH` / `AGENT_OLLAMA_NO_READLINE` | — | `1` で rich / readline を使わない |

**環境に既にある変数が常に勝ちます。** `OLLAMA_HOST` / `OLLAMA_API_BASE` / `NO_PROXY`（か
`no_proxy`）が全部そろっていなければ、そのときだけ `~/.profile` を読んで `OLLAMA_*` /
`AGENT_OLLAMA_*` / `NO_PROXY` を補完します。profile の評価は sh の子プロセスに閉じ込め、
失敗は黙って無視します（profile が壊れていても推論を止めない）。

この補完ブロックは **3 か所に同一の複製**があります（`ollama_adapter.py` が正典、
`aider_adapter.py` と `tools/opencode/agent-opencode.py` が複製）。単体ファイルで配る 2 つは
agentcore を import できないためで、一致は
`agentcore/agentcore/tests/test_adapter_env_parity.py` が AST 比較で機械的に縛ります。

---

## 4. 上限

| 対象 | 既定 | 変えられるか |
|---|---:|---|
| ツールループのラウンド | 12（read セットは 30） | `--max-rounds` |
| ツール 1 コマンド | 300 秒 | `--command-timeout` |
| ツール出力の取り込み | 4,000 字（頭尾を残して詰め、何を省いたか明記） | 不可 |
| 1 ラウンドの生成 | 4,096 トークン | 不可 |
| 規約外応答の言い直し | 2 回 | 不可 |
| ツール拒否の連続 | 2 回で `tool_denied` | 不可 |
| 同一 `(コマンド, 終了コード, 出力)` の連続 | 3 回で `no_progress` | 不可 |
| 文脈の警告 | 実効上限の 90 % で 1 回だけ | `--context-warn-pct` |
| 文脈の reserve | 512 トークン | 不可 |
| heartbeat | 5 秒 | 不可 |

待ちの上限は局面ごとに分けます。**prefill を無制限にしてあるのが要点**で、CPU 推論では
「最初のトークンまで 10 分」が正常なため、ここに上限を置くと正常な実行を殺します。上限の判定は
heartbeat の刻みで行うので、検知は最大 5 秒だけ遅れます。

文脈上限は `--context-limit` → request options の `num_ctx` → `/api/ps` → `/api/show` の順で
解決します。取得不能なら上限 0 として使用量だけを表示し、**知らない上限を根拠に警告・打ち切りは
しません**。

---

## 5. 終了状態と出力

ループの終了状態は 6 つです。

| 状態 | 意味 | トリアージ |
|---|---|---|
| `done` | `TASK_COMPLETE` を確認した | — |
| `no_command` | 規約外応答が続いた | — |
| `max_rounds` | ラウンド上限 | — |
| `no_progress` | 同一入出力の連続 3 回。同一入力の再試行では解けない | `env` |
| `context_exhausted` | 最低限のツール結果も入らない | `env` |
| `tool_denied` | 拒否が続いた | `env` |

**`done` 以外はすべて未完了**です。最後の本文を捨てずに stdout へ返したうえで、通常本文の末尾へ
`{"ok": false, "issues": [...]}` を足します。`--format json` は本文全体の契約を壊せないため封筒を
足さず、外側の形式修復・検証へ委ねます。**未完了も rc=0** なので、呼び出し側は本文の機械可読
契約を読んで判定します。

stderr の行は責務を分けます。

| 行 | 内容 |
|---|---|
| `@agent-usage` | 累計 `tokens_in` / `tokens_out`。node-budget・audit の実測値 |
| `@agent-context` | 現在の文脈使用量 / 上限 / 比率 / 出典。**累計消費とは混ぜない** |
| `@agent-note` | 未完了理由の人向け注記 |
| `@agent-log` | JSONL ログのパス |

接続不能・モデル未取得・スキル未配布・ツールセット不整合・`context_exhausted` は `env`、
stall・通信断は `transient` として定義します。

---

## 6. ツール・スキル・再生

### 6.1 ツール

| セット | 許すもの |
|---|---|
| `bash` | `bash -lc` へそのまま渡す。**OS ユーザー権限の範囲で無制限**であり、`cwd` は開始位置にすぎない（sandbox ではない） |
| `read` | ファイルを変更できないコマンドと git の読取 subcommand だけ。引用外のシェルメタ文字・`find` の書込/実行述語・未知コマンドを拒否し、許可後も**シェルを介さず argv として直接実行**する |
| `edit` | 予定名としてだけ認識し、現時点では明示エラー |

判定できない形は安全側で拒否します。

### 6.2 スキル

**明示・遅延読み込みだけ**です。`--skill <name>` またはプロンプト先頭の連続 slash 行を検出し、
`~/.agents/skills` → `AGENT_OLLAMA_SKILLS_DIR` の追加先 → `~/.claude/skills` の順に `SKILL.md` を
探します。frontmatter は除き、同じスキルは 1 回だけ注入します。明示指定が見つからなければ env
失敗、未知の slash 行は通常文かもしれないため警告して本文へ残します。

`{skill_dir}` を使うスキルは同梱 script の実行を前提にするため、`read` と組み合わせた時点で env
失敗にします（スキルを読めたのに手順だけ実行できない「成功に見える失敗」を作らない）。
利用可能なスキルの全一覧を system prompt へ常時載せる自動選択は、prefill の固定費になるため
行いません。

### 6.3 再生（`--replay`）

記録済みの JSONL から最初の user メッセージを取り出し、モデル・think・format を変えた腕へ同じ
入力を当てて、腕ごとの空応答率・失敗率・所要時間と、腕をまたいだ一致率を出します。

**再生は道具を持ちません。** 道具ありの実行ログも入力源にできますが、記録されたコマンドは
再実行しません——再生は測定であって副作用の再現ではなく、測るたびにワークスペースが変わっては
なりません。この不変条件は腕の指定でも緩めません。正解ラベルとの一致率は出しません
（ラベルは人が付けるもので、この口が引き受けるのは「同じ入力に対する出力」を再現可能な形で
並べるところまで）。

---

## 7. ログ

実行中は run / skill / LLM / message / tool / context / error / end を JSONL へ追記します。
ログ書込みや表示 sink の失敗は推論本体を止めません。`--status` は末尾から
`{state, phase, round, last_progress_at, tokens_per_sec, context_*}` を組み立て、`--follow` と
TUI は同じイベントを表示します。

---

## 8. 配布

| 成果物 | 作り方 |
|---|---|
| `agent-ollama` | agentcore を同梱した zipapp（`install.sh`。`--with-rich` で rich を同梱） |
| `agent-aider` | `aider_adapter.py` の単体コピー |
| `agent-opencode` | `tools/opencode/agent-opencode.py`（agentcore の installer の対象外・別ツール） |

実装は Python 標準ライブラリだけで成立します。TUI の rich は任意で、無い環境では ANSI /
readline の行指向表示へ戻ります。全画面の alternate screen は使わず、agent-loop の
`capture-pane` と `send-keys` から同じ対話面を駆動できる形を保ちます。

### 8.1 `agent-aider`

Aider を CLI 契約の下に置く薄いラッパ（`agentcore/aider_adapter.py`）です。`agents/aider.json`
から `agent-aider ... --model ollama_chat/{model}` として起動され、既定モデルは `gemma4:e4b`、
`headless_autonomy: single-shot`（渡されたファイルを編集するだけでツールループを持たない）、
`readonly: enforced`（`--dry-run`）。`variants` は 13 用途を宣言し、12 用途を `ollama-json` へ、
`split` を `ollama-list-thinking` へ振り替えます。

素の `aider` を直接呼ばず 1 枚噛ませているのは、素の argv では表せないものが 3 つあるためです。

| ラッパ専用オプション | 意味 |
|---|---|
| `--agent-policy <id>` | Aider の system prompt 先頭へ固定の reliability policy を注入する。現在の唯一の ID は `gemma4-e4b-reliability-v1`（対象 model は `ollama_chat/gemma4:e4b`） |
| `--agent-num-ctx <整数>` | model settings の `extra_params.num_ctx` |
| `--agent-num-predict <整数>` | model settings の `extra_params.num_predict` |

これら 3 つは Aider へは渡さず、一時的な `--model-settings-file` を組み立てて先頭に差し込みます
（実行後に削除）。policy 適用時は stderr へ `@agent-policy id=<id> sha256=<12桁>` を出すので、
実効 policy を後から観測できます。**未知の ID・対象外 model・外部 `--model-settings-file` との
競合は黙って無効化せず、起動前に `[agent-error:env]` で失敗します**——「policy が効いている
つもりで効いていない」を作らないためです。

3 つめは実測 usage で、`--analytics-log` の一時ファイルから累計トークンを読んで
stderr の `@agent-usage tokens_in=... tokens_out=...` へ載せます（共通の usage 契約）。

`~/.profile` からの環境補完（§3）も同じ理由で必要です——aider は接続先を `OLLAMA_API_BASE`
（litellm）で読むので、補完が無いと既定の localhost へ向かうか、接続がプロキシへ流れて
504 になります。

---

## 9. 未実装

| 項目 | 状態 |
|---|---|
| `edit` ツールセット | 未実装。段 0〜3 の品質・節約実測が着手条件 |
| `--patch` の決定的 SEARCH / REPLACE 適用 | 未実装。`edit` より小さい必要性が確認できたとき再検討 |
| 走行中の read → edit / bash 昇格（`ToolPolicy`） | 未実装。read の権限不足による人手介入が実測で一定数出たときだけ着手 |
| R3「品質は時間で買う」の実証 | `--replay` が検証の口だが、買えている証拠はまだ無い |
