# agents/ — エージェント CLI プラグイン定義

`agent_cli` に指定できる CLI を**コードを触らずに**定義する置き場。1 CLI = 1 ファイル
（`<name>.json`）で、`agent_cli: <name>`（または `agents:` の役割毎上書き）と書けば使える。

**組み込み CLI（kiro / claude / copilot / codex）もここにある**（S9）。ツールのコード側に
CLI 分岐は無く、`agent-cli.schema.json` の定義がすべて。**CLI の挙動・作法が変わったときの
修正が JSON 1 ファイルで完結すること**がこの契約の受入条件。

契約の正典は [`schemas/agent-cli.schema.json`](../schemas/agent-cli.schema.json)。

## 探索順

1. `$KIRO_AGENTS_DIR`（環境変数）
2. `<プロジェクトルート>/agents/`（= agent-project 実行時の cwd。プロジェクト固有の定義）
3. `~/.agents/agents/`（ユーザー共通）
4. `~/.kiro/agents/`（旧ユーザー共通）

`tools/agent-tools/install.sh` が 3 を配布先として、このディレクトリの `*.json` を配る
（`$AGENT_PROJECT_AGENTS_HOME` を設定していればその下）。zipapp はリポジトリの `agents/` を
持ち出せないので、配らないと配布インストールでは組み込み CLI すら「未知の agent_cli」になる。
3 は同梱定義の更新で上書きするので、独自定義は 1 か 2 に置く。

同名は先勝ち（first-wins）。**上位に置けば同梱定義を上書きできる**——組み込み名の予約は
S9 で解除した。定義を 1 つも解決できない `agent_cli` は明示エラーになる（黙って別の CLI へ
倒さない。インストール破損を静かに握り潰さないため）。

## モードと権限

同じ CLI でも「何をさせるか」で必要なフラグが違うので、定義は 1 本の argv ではなく
**モード（ヘッドレス / 対話）× 権限（書き込み可 / 読み取り専用）** で組み立てる。

```
argv = command + (write_args | readonly_args) + no_session_args? + spill.args?
       + model_flag + model + command_suffix + argv 渡しのプロンプト
```

| フィールド | いつ付くか |
|---|---|
| `relative_cost` | 「より安い候補」を決める無次元値（ローカル=0、通常クラウド=1）。**定義単位なのでモデル単位の差は表せない**——`opencode` のようにプロバイダを `--model provider/model` で切り替える CLI では、その定義でふだん使う経路の値を書く（この repo ではローカル ollama 向けなので 0）。モデル別の実効単価が要るようになったら、実測（agent-audit の格付け）を根拠に別途足す |
| `write_args` | 既定モード（act・plan・charter 生成など書き込みを伴う実行） |
| `readonly_args` | 読み取り専用モード（Doctor・構造化 Assist・対話診断） |
| `no_session_args` | 使い捨て実行（診断）。セッション永続化を切る |
| `spill.args` / `spill.instruction` | 長大プロンプトを一時ファイルへ退避したとき |
| `command_suffix` | 位置引数を末尾に固定したい CLI（codex の `-`） |
| `interactive.*` | tmux で人が直接操作する対話起動 |

argv とは別に、**セッションへ送るテキストの作法**も CLI で違う。`skill_command_prefix` は
スキル起動コマンドの行頭記号で、既定は `/`（`/skill-name`）。codex は `$skill-name` でしか
スキルが起動しないので `"$"` を宣言する。セッション開始コマンド（agent-session-commands）の
chat モードのように、人が `/skill-name` と書いたテキストを送る経路がこの宣言を見て行頭の `/`
を差し替える（対象は行頭の `/` + 英数字トークンだけ。`/home/…` のようなパスは変えない）。

`slash_native` は**その行を CLI へ渡すか、ランチャが消費するか**の宣言。`true` はネイティブの
スラッシュコマンドを持つので残して渡し（記号は上の `skill_command_prefix`）、`false` は持たない
のでランチャがスキルとして解決して材料へ載せる。未宣言なら `headless_autonomy` から導く
（`tool-loop` なら `true`）——以前この判定はその代理で書かれていたので、宣言しない定義は
今までと同じに振る舞う。ただし 2 つは別の性質で（「自分でツールを回せるか」と「スラッシュを
自分で解釈するか」）、片方だけ真の CLI はありうるので、同梱定義は自分で宣言している。

`readonly` は**強制力の宣言**（`enforced` / `best-effort`）。このレイヤは宣言どおりの argv を
組み立てるだけで、フラグを無視する CLI への防御は持たない。`best-effort` の CLI に読み取り
専用を要求した呼び出しには警告が返り、画面は「助言のみを保証できません」と出す。

## 書き方（最小）

```json
{
  "command": ["my-cli", "chat"],
  "prompt_via": "stdin",
  "model_flag": "--model"
}
```

- `command` の `{model}` はモデル名に置換（未指定ならそのトークンごと省く。必須の CLI は
  `default_model` を書く）。`{output_file}` は `output: "file"` のとき最終応答を書かせる
  一時ファイルに置換（stdout がイベントログで汚れる CLI 向け）。
- `errors` に CLI 固有の失敗パターンを書くと、**失敗トリアージ**に反映される。
  quota は `quota_kind` で、当面戻らない `exhausted` と、メッセージから復帰時刻を
  読み取れる `rate_limit` を区別する。分類した quota はエンジンが node-budget の台帳へ
  観測行（`quota_kind` / `reset_at`・消費 0）として追記し、管理面の段判定がそれを読んで
  候補から外す。auth・env=人が環境を直す、transient=自動リトライ。agent-project は
  リトライを無駄に焼かず・viewer は「誰が何を直せばよいか」を表示できる。
- `interactive` が無い定義は対話起動（CLIチャット・定常業務の tmux 実行）を提供しない。
- `timeout` は**その CLI だけ既定と違う上限が要るとき**にだけ書く。省略すると呼び出し側の
  fallback（agent-loop は 600 秒、agent-audit は `--agent-timeout`）に従う。既定で足りるのに
  書くと、呼び出し側が fallback を変えてもその定義だけ古い上限のまま取り残される。

## 待機判定（interactive の検出フィールド）

対話 CLI を tmux で自動運転する側（agent-loop の送信可否判定・スロット解放、対話診断の
起動待ち）は、**ペイン画面から「待機中か処理中か」を判定する**。この判定方法は CLI ごとに
違うので、定義側が宣言する。

| フィールド | 役割 |
|---|---|
| `ready_pattern` | 「入力を受け付けている」を検出する ERE（末尾行に適用）。省略時は組み込みの既定（素のプロンプト記号） |
| `busy_pattern` | 「処理中」を正に検出する ERE（可視画面全体に適用・大文字小文字無視）。**入力欄を出したまま処理する TUI**（claude の `(esc to interrupt)` 等）では ready の消失が起きないため、これが判定の正になる |
| `failure_pattern` | `agent-loop send --wait`が明示的失敗とみなすERE。省略時はpane/process終了以外を推測しない |
| `idle_quiet_sec` | どちらのパターンでも判定できない CLI 向けの静穏判定。画面が N 秒変化しなければ待機とみなす（0 = 無効） |
| `clear_command` | コンテキスト破棄コマンド（既定 `/clear`、codex は `/new`）。空文字は「クリア手段なし」の宣言 |
| `turn_completion` | agent-loop が管理する対話paneだけにCLI固有のsession-local完了hookを注入する。既知値は `kiro` / `claude` / `codex` / `copilot` / `ollama`。未指定時は画面監視のみ |

判定の優先順位: `busy_pattern` マッチ → 処理中 ＞ `ready_pattern` マッチ → 待機 ＞
`idle_quiet_sec` 静穏 → 待機 ＞ それ以外 → 処理中。

## 同梱の定義（13 件）

| ファイル | CLI | 読み取り専用の強制力 |
|---|---|---|
| `kiro.json` | `kiro-cli chat` | best-effort（`--trust-tools=` は信頼するツールを絞るだけ） |
| `claude.json` | `claude` | enforced（`--permission-mode plan`） |
| `copilot.json` | `copilot` | best-effort |
| `codex.json` | `codex exec` | enforced（`--sandbox read-only`）。スキル起動は `$name` |
| `cursor.json` | `cursor-agent` | best-effort（`--mode ask`） |
| `ollama.json` | `agent-ollama <model>` | enforced（readonly はツールを持たない） |
| `ollama-json.json` | 同上 + `--format json` | enforced（道具なし。JSON 契約の役割用） |
| `ollama-list.json` | 同上 + `--format array` | enforced（道具なし。配列契約＝split 用） |
| `ollama-list-thinking.json` | 同上 + `--think on`（`--format` なし） | enforced（道具なし。gemma4:e4b の split 用——文法制約を外して Thinking を使い、`temperature=0` で意味的な完全被覆を安定させる） |
| `ollama-read.json` | 同上 + `--tools read` | enforced（write でも読み取り専用コマンドだけ） |
| `ollama-verify.json` | 同上 + `--format json --stall-timeout 180` | enforced（道具なし。テキスト検証役。既定 gemma4:12b——負けは全部タイムアウトなので stall + transient 分類の再投入で受け、コード worker の候補には入れない） |
| `aider.json` | `agent-aider`（aider + ollama_chat） | enforced（`--dry-run`）。single-shot——渡されたファイルを編集するだけでツールループを持たない |
| `vscode-copilot.json` | `vscode-copilot-chat`（VS Code の Language Model API へ橋渡し） | enforced（モデルを呼ぶだけでファイルもコマンドも触らない）。single-shot——ツールを持たないので呼び出し側がループを供給する |

**ローカルが 2 つある理由（結論: 選ばなくてよい）。** `aider` と `ollama` は、15 用途
（verify / judge / extract / retrieve / split …）についてはどちらを base にしても同じ
`ollama` profile へ振り替わる。分かれるのは**編集・実装を誰がやるか**の 1 点だけで、
そこは実測が `aider`（T2/T4 9/9）を選んでいる。よって **base は `aider` に置けばよく、
用途別の振り替えは宣言が全部やる**。`agent-herd defs` がこの分担を 1 行で出す。

opencode の定義は同梱を外した（このハードでは成立しないことが実測済み。設計:
docs/plans/2026-08-27 設計 §6）。使う人は `agents/opencode.json` を自分で置けば
従来どおり定義経由で呼べる（探索順 1・2 がユーザー定義を先勝ちにする）。

`vscode-copilot.json` も本体を直接呼ばない。VS Code の Language Model API は
**編集中の VS Code プロセスの中にしか無い**ので、拡張が localhost に立てた認証付きの口へ
CLI から問い合わせる（`tools/vscode-copilot-chat`）。Copilot の枠でモデルと会話できるが、
VS Code の Agent mode が持つ built-in tools・ファイル編集・ターミナル実行は**含まない**
——`headless_autonomy: single-shot` はそのことの申告でもある。

hermes（tools/hermes-kiro-acp）のような自作ブリッジも、stdin でプロンプトを受けて
stdout に本文だけを返す薄い CLI を用意すれば同じ契約で差し込める。

## ローダ

- **Python**: `agentcore.agentcli` の 1 実装を agent-project / agent-flow / agent-amigos / agent-loop が共有する
- **agent-dashboard**: UI の応答性のため JS の自前ローダ（Python プロセスを起こさない）。
  Python 実装とはゴールデンテストで揃える（同じ定義から同じ argv が出ることを固定する）

## 関連文書

- **契約の正典**: [`schemas/agent-cli.schema.json`](../schemas/agent-cli.schema.json)
- **仕様書**（探索順・全フィールド・ローダ API・エラークラス・同梱定義の一覧）:
  [`docs/specs/agent-cli-spec.md`](../docs/specs/agent-cli-spec.md)
- **設計書**（なぜこの形にしたか）:
  [`docs/designs/agent-cli-plugin-design.md`](../docs/designs/agent-cli-plugin-design.md)
- **ローカル実行系（`aider` / `ollama` の profile）**:
  [`docs/specs/agent-herd-spec.md`](../docs/specs/agent-herd-spec.md) /
  [`docs/designs/agent-herd-design.md`](../docs/designs/agent-herd-design.md)
