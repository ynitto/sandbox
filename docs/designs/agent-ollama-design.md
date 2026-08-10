# agent-ollama — 設計書

> **対象系統**: `agents/ollama{,-json,-read}.json` / `agentcore/ollama_*.py` /
> `agent-project` / `agent-flow` / `agent-audit` / `agent-loop` / `agent-dashboard`。

> 最終更新: 2026-08-09 ／ 関連: [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md)
> （CLI 定義の共通契約）, `tools/agent-tools/README.md`（利用手順）。
> [`2026-08-08-agent-ollama-expansion-design.md`](../plans/2026-08-08-agent-ollama-expansion-design.md) と
> [`2026-08-07-agent-ollama-tool-disclosure-design.md`](../plans/2026-08-07-agent-ollama-tool-disclosure-design.md) の現行判断を統合した正典。

Ollama のローカル推論を、クラウド CLI の枠が乏しいときのバックアップだけでなく、品質が成立する
役割を恒常的に引き受ける**コスト 0 の節約先**として agent-* ファミリーへ接続する。犠牲にするのは
壁時計時間だけで、品質・完了判定・読み取り専用の強制力はクラウド経路と同じ契約を守る。

## 1. 位置づけと要件

- **R1 作業を止めない**: 遅いローカル推論を短い壁時計 timeout で正常系から外さない。途中で
  打ち切る場合も最後の本文を返すが、未完了状態を機械可読にして done へ混ぜない。
- **R2 遅いと停止を区別する**: トークン生成・ツール実行・ラウンド・文脈使用量を追記ログへ残し、
  人とプログラムが同じ証拠を読む。失敗検知の主役は経過時間でなく無進捗に置く。
- **R3 品質は時間で買う（買えている範囲で）**: think は API フィールドで有効にし、JSON は
  デコード時の文法制約で守る。ただし**買えているかは実測で確かめる**——判断役では効くが、
  ツールループの実行役では 1 ラウンドの長考が品質に変換されないと分かったので off へ戻した。
  速さのためにモデルへ自己採点させて品質を下げず、done は外側の機械検証だけが決める。
- **R4 文脈を黙って失わない**: 実効上限と使用量を観測し、警告後も次のツール結果が入らなければ
  `context_exhausted` で止める。サーバの暗黙切り捨てやクライアント側の自動要約に任せない。

## 2. CLI 定義と実行面

一般の `write_args` / `readonly_args` / `json_variant` / `interactive` / `errors` / `session_log` 契約は
[`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md) を正典とし、本書では Ollama への割当だけを定める。

| 定義 | JSON 契約 | write モード | readonly モード | 対話 |
|---|---|---|---|---|
| `ollama` | `ollama-json` へ振替 | `--tools bash --max-rounds 30 --command-timeout 900` | 道具なし | `--tui`、道具なしで開始 |
| `ollama-json` | 自身 | `--format json`、道具なし | 同左 | なし |
| `ollama-read` | なし | `--tools read --max-rounds 30 --command-timeout 900` | 道具なし | なし |

3 定義は `relative_cost: 0`、`readonly: enforced`、既定モデル `qwen3` を宣言する。think は
`ollama` だけ**役割で分ける**（readonly = `on`、write = `off`）。ツールループは 1 ノードで最大
30 ラウンド回るので、道具を持つ側で 1 ラウンドごとに長考されると終わらない（実測 2026-08-10:
思考 7700 トークン・12 分の末に構文エラーのコード）。`ollama-json` / `ollama-read` は `on` のまま。
`ollama` の readonly にツールを付けないため、CLI 契約上の強制力に嘘が入らない。`ollama-read` は
write として呼ばれる役割に読取ツールだけを与える別定義で、汎用 `ollama` を安全側へ書き換えない。

CLI の実行面は次の 4 つに絞る。

| 面 | 入口 | 用途 |
|---|---|---|
| plain | `agent-ollama <model>` | text / JSON の単発生成。道具なし |
| bash loop | `--tools bash` | OS ユーザー権限での汎用 work |
| read loop | `--tools read` | 決定的ゲート付きの調査・読取 |
| human / observe | `--tui` / `--status` / `--follow` / `--context` | 対話、進捗追尾、状態・文脈上限の取得 |

## 3. 実行核

`ollama_adapter.py` は引数解釈とモード分岐だけを持ち、実行を次の順に組み立てる。

1. 非ログイン subprocess でも接続先を解決できるよう、環境に `OLLAMA_HOST` が無い場合だけ
   `~/.profile` から `OLLAMA_*` / `AGENT_OLLAMA_*` を補完する。呼び出し側の明示環境が常に勝つ。
2. 明示されたスキルを 1 回だけ展開し、イベントログと `ContextTracker` を開始する。
3. 道具なしは `/api/generate`、道具ありは `/api/chat` を `stream: true` で呼ぶ。
4. API の `think` / `format` / `options` / `keep_alive` フィールドへ設定を渡す。思考本文は成果本文と
   分離し、JSON のときは `format: json` でデコード文法を制約する。
5. 本文、実測 token、終了状態、ログパス、文脈 snapshot を同じ結果として返す。

ツールループのプロトコルは短い固定文にする。モデルは次の 1 手を最後の bash fence 1 個で返し、
完了時は `TASK_COMPLETE` を返す。規約外応答には最大 2 回だけ言い直しを促し、ラウンド数・コマンド
時間・ツール出力長はすべて有界にする。長いツール出力は頭尾を残して詰め、何を省いたかを明記する。

実装は Python 標準ライブラリだけで成立し、`agent-ollama` の単一 zipapp へ同梱する。TUI の rich は
任意で、無い環境では ANSI / readline の行指向表示へ戻る。全画面の alternate screen は使わず、
agent-loop の `capture-pane` と `send-keys` から同じ対話面を駆動できる形を保つ。

## 4. ツールとスキルの境界

- **`bash`**: `bash -lc` へそのまま渡す。OS ユーザー権限の範囲で無制限であり、`cwd` は開始位置に
  すぎない。ワークスペース外への移動・変更を防ぐ sandbox ではない。
- **`read`**: ファイルを変更できないコマンドと git の読取 subcommand だけを許可する。
  引用外のシェルメタ文字、`find` の書込・実行述語、未知コマンドを拒否し、許可後もシェルを介さず
  argv として直接実行する。判定できない形は安全側で拒否し、拒否を繰り返したら `tool_denied` で止める。
- **`edit`**: 予定名としてだけ認識し、現時点では明示エラーにする。read の保証を prompt のお願いへ
  戻してまで編集能力を足さない。

スキルは**明示・遅延読み込みだけ**とする。`--skill <name>` またはプロンプト先頭の連続 slash 行を
検出し、`~/.agents/skills` → `AGENT_OLLAMA_SKILLS_DIR` の追加先 → `~/.claude/skills` の順に
`SKILL.md` を探す。frontmatter は除き、同じスキルは 1 回だけ注入する。明示指定が見つからなければ
env 失敗、未知の slash 行は通常文かもしれないため警告して本文へ残す。

`{skill_dir}` を使うスキルは同梱 script の実行を前提にするため、`read` と組み合わせた時点で env
失敗にする。スキルを読めたのに手順だけ実行できない「成功に見える失敗」は作らない。利用可能な
スキルの全一覧を system prompt へ常時載せる自動選択は、prefill の固定費になるため行わない。

## 5. 進捗・文脈・セッションログ

待ちの上限は局面ごとに分ける。

| 局面 | 既定 | 扱い |
|---|---:|---|
| connect | 120 秒 | 応答ヘッダを得られない接続・モデルロード |
| prefill / first token | 0（無制限） | CPU で長時間でも正常。必要な呼び出しだけ明示上限を付ける |
| decode stall | 180 秒 | 最後の生成進捗からの無進捗。transient として自己中断 |

待ちと受信は別スレッドに分け、打ち切り時はブロック中の reader を socket shutdown で解除する。
5 秒ごとの heartbeat は「プロセスが生きている」証拠であり、生成進捗とは数えない。

実行中は run / skill / LLM / message / tool / context / error / end を JSONL へ追記する。ログ書込みや
表示 sink の失敗は推論本体を止めない。`--status` は末尾から `{state, phase, round,
last_progress_at, tokens_per_sec, context_*}` を組み立て、`--follow` と TUI は同じイベントを表示する。
3 定義の `session_log` は `~/.agents/logs/ollama` の JSONL ディレクトリを agent-audit へ宣言する。

文脈上限は `--context-limit` → request options の `num_ctx` → Ollama `/api/ps` → `/api/show` の順で
解決する。取得不能なら上限 0 として使用量だけを表示し、知らない上限を根拠に警告・打ち切りは
しない。上限が分かる場合は既定 90% で 1 回だけ警告し、reserve を残してツール結果を詰める。
最低限の結果も入らなければ自動圧縮せず `context_exhausted` で止め、タスク分割を人・上位層へ返す。

## 6. 完了・出力・エラー契約

ループの終了状態は `done` / `no_command` / `max_rounds` / `context_exhausted` / `tool_denied`。
`TASK_COMPLETE` を確認した `done` 以外は未完了であり、最後の本文を捨てずに stdout へ返した上で、
通常本文の末尾へ `{"ok": false, "issues": [...]}` を足す。`--format json` は本文全体の契約を
壊せないため封筒を足さず、外側の形式修復・検証へ委ねる。未完了も rc=0 なので、呼び出し側は本文の
機械可読契約を読んで判定する。

stderr の行は責務を分ける。

- `@agent-usage`: 累計 tokens_in / tokens_out。node-budget・audit の実測値。
- `@agent-context`: 現在の文脈使用量 / 上限 / 比率 / 出典。累計消費とは混ぜない。
- `@agent-note`: 未完了理由の人向け注記。
- `@agent-log`: JSONL ログのパス。

接続不能・モデル未取得・スキル未配布・ツールセット不整合・`context_exhausted` は env、stall・通信断は
transient として定義する。エンジンは同じトリアージ契約を読み、環境修復が必要な失敗でリトライを
焼かず、一時失敗だけを通常リトライへ送る。

## 7. 適用段階とルーティング

適用は独立した段で進め、前段の品質実測なしに編集権限へ広げない。

| 段 | 内容 | 状態 |
|---:|---|---|
| 0 | 役割別設定でローカルへ opt-in、クラウド CLI は既定のまま | 利用口・設定例を配布済み |
| 1 | `--format json`、think（readonly on / write off）、`json_variant` による JSON 契約 | 実装済み |
| 2 | エンジン側の役割別 readonly 宣言 | 実装済み |
| 3 | `ollama-read` と決定的 read ゲート | 実装済み |
| 4 | edit セットによる安全なファイル編集 | 未実装。段 0〜3 の品質・節約実測が着手条件 |
| 5 | `--patch` の決定的 SEARCH / REPLACE 適用 | 未実装。段 4 より小さい必要性が確認できたとき再検討 |

予算・quota によるローカル退避は agent-profile / node-budget が担う。内容失敗に対しては、設定の
`fallbacks` が宣言した現在より高コストの候補を 1 段だけ再試行できる。agent-flow は retry 深さ、
agent-project はプロセス単位の `agent_escalation_max` で有界にする。これは品質を採点して無条件に
クラウドへ投げ直す仕組みではない。仕事種別 × モデルの品質・消費は agent-audit で測り、設定変更の
根拠として人の昇格経路へ返す。

走行中の read → edit / bash 昇格を決定的に行う `ToolPolicy` も未実装とする。静的な read / bash / JSON
の 3 定義で回る限り足さず、read の権限不足による人手介入が実測で一定数出たときだけ着手する。
実装する場合も権限は単調増加、prompt の変更は追記だけとし、安定 prefix cache を壊さない。

## 8. 不変条件と非目標

- readonly は道具なし、read は実行直前の決定的ゲートで強制する。bash の無制限性は隠さない。
- エンジンは `ollama` という名前で分岐せず、`agents/*.json` の共通契約だけを見る。
- 推論・観測は標準ライブラリで動き、ログや任意 UI の失敗を成果生成へ波及させない。
- `done` をモデルの自己申告だけで確定しない。外側の verify と受入条件を省略しない。
- クライアント側の自動コンパクション、無制限の自動クラウド昇格、ツールカタログの常時注入は作らない。
- `docs/plans/2026-08-07-agent-ollama-tool-disclosure-design.md` と
  `docs/plans/2026-08-08-agent-ollama-expansion-design.md` は統合元の詳細検討記録として残す。
  現行状態・責務境界・未実装範囲の判断は本書を優先する。
