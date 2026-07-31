# S9 詳細設計: エージェント CLI 差分吸収レイヤ

ステータス: 実装済み（詳細設計 + 実装で確定した差分を反映）
入力: [`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §3 S9（C11）
前提: [`2026-07-26-s1-config-two-layer-detailed-design.md`](2026-07-26-s1-config-two-layer-detailed-design.md) / [`2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md`](2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md)（agentcore を共有実装の置き場とする前例）
実装フェーズ: Phase 1'（S9-1〜3）。S9-4（対話診断）は Phase 4 で、このレイヤの最初の利用者になる

**この設計の受入条件**（仕様書 S9-2 より）: **エージェント CLI の挙動・作法が変わったとき、修正が `agents/<name>.json` 1 ファイルで完結すること。**
以下の設計判断はすべてこの 1 点から導いている。

---

## 1. 現状（調査結果）

### 1.1 CLI 固有知識の散らばり

「この CLI をどう起動するか」の知識が **8 か所・4 実装**に分かれている。

| 知識 | 置き場 | 対象 |
|---|---|---|
| ヘッドレス argv | `agent-project/prioritize.py:131-172`（`_agent_cmd`） | kiro / claude / copilot / codex |
| ヘッドレス argv | `agent-flow/agent.py:~701`（`load_agent_plugin` 前の分岐） | 同上 |
| ヘッドレス argv | `agent-amigos/agentcli.py:150-230`（`_plugin_cmd` / `run_agent`） | 同上 |
| ヘッドレス argv | `agent-dashboard/features/agent-project/main/agent.js:179-214`（`buildCommand`） | 同上 + cursor / ollama |
| 読み取り専用フラグ | 同 `agent.js:364-417`（`buildDoctorCommand`） | kiro / claude / copilot |
| **対話 argv** | 同 `agent.js:218-247`（`buildInteractiveCommand`） | kiro / claude / copilot / codex / cursor / ollama |
| **入力受付の検出** | `cowork/main/loopProvider.js:388-403`（capture-pane を grep する正規表現） | 全 CLI 共通の 1 本 |
| **既定の対話コマンド** | `cowork/config.js:15` / `cowork.js:580`（`'kiro-cli chat --trust-all-tools'` の文字列） | kiro 固定 |
| プラグイン定義ローダ | 上記のうち 4 ファイル（Python 3 実装 + JS 1 実装） | — |

同じ CLI の argv が 4 か所に書かれているだけでなく、**同じ CLI でも用途によって argv が違う**:

| CLI | 書き込みあり（act / charter 補完） | 助言のみ（Doctor） |
|---|---|---|
| claude | `-p --output-format text --dangerously-skip-permissions`（agent-project） / 同フラグ無し（dashboard） | `-p --output-format text --permission-mode plan --tools '' --no-session-persistence` |
| kiro | `chat --no-interactive --trust-all-tools` | `chat --no-interactive --trust-tools=`（spill 使用時は `--trust-tools=fs_read`） |
| copilot | `-s --allow-all-tools --allow-all-paths`（agent-project） / `--allow-all-paths` 無し（dashboard） | `-s --allow-all-tools --available-tools= --disable-builtin-mcps --no-custom-instructions` |

つまり必要な軸は「CLI × モード（ヘッドレス / 対話）× 権限（書き込み可 / 読み取り専用）」の 3 つで、現行スキーマ（`command` 1 本）はそのうち 1 マスしか表現できない。

### 1.2 現行のプラグイン契約の空白

`schemas/agent-cli.schema.json` は**ヘッドレス片道実行の書き込み可モードだけ**を契約化している。
`command` / `prompt_via` / `output` / `errors` は揃っているが、対話 argv・読み取り専用フラグ・入力受付検出・プロンプト注入方法は持たない。
組み込み名（kiro/claude/copilot/codex）はローダが明示的に弾いており（`agent.js:66` の `AGENT_CLIS.has(nm)` / `prioritize.py` の `_agent_cmd` 分岐が先）、**定義ファイルで上書きできない**。

### 1.3 kiro の固有事情（データ化が必要な唯一の癖）

kiro-cli は positional プロンプトを渡すと stdin を読まない。そのため dashboard は長大なスナップショットを一時ファイルへ退避（spill）し、「まず `fs_read` でそのファイルを読め」という**日本語の指示文**を短い argv で渡している（`agent.js:706-741`）。
この指示文は `fs_read` という **kiro のツール名**に依存しており、CLI 固有知識そのもの。JSON へ移す対象に含める。

---

## 2. スキーマ拡張（S9-1）

`agent-cli.schema.json` に次を **追加**する（既存フィールドは変更しない＝既存の `cursor.json` / `ollama.json` はそのまま有効）。

```jsonc
{
  "name": "kiro",
  "command": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"],
  "prompt_via": "argv",
  "model_flag": "--model",

  // ▼ 追加（ヘッドレス・対話の両方に効く共通の権限フラグ）
  "readonly_args": ["--trust-tools="],
  "readonly": "best-effort",
  "no_session_args": [],

  // ▼ 追加（長大プロンプトの一時ファイル退避。prompt_via では表せない CLI 固有の癖）
  "spill": {
    "args": ["--trust-tools=fs_read"],
    "instruction": "指示の全文は一時ファイル {file} にあります。まず fs_read でこのファイル全体を読み込み、その内容だけに従ってください。"
  },

  // ▼ 追加（対話モード）
  "interactive": {
    "command": ["kiro-cli", "chat", "--trust-all-tools"],
    "readonly_args": ["--trust-tools="],
    "no_session_args": [],
    "ready_pattern": "^[[:space:]]*[>?❯›][[:space:]]*$|!>|│[[:space:]]*[>❯›]|ask a question|describe a task",
    "ready_timeout_sec": 60,
    "prompt_inject": "send-keys"
  }
}
```

### 2.1 仕様書からの意図的な差分（3 点）

仕様書 §3 S9-1 の草案から次の 3 点を変えた。理由も含めて契約に固定する。

| 仕様書の草案 | 本設計 | 理由 |
|---|---|---|
| `interactive.prompt_via: "send-keys" \| "file"` | **`interactive.prompt_inject`** に改名 | トップレベルに `prompt_via: stdin \| argv` が既にある。同名で enum だけ違うキーを入れ子に置くと、読み手が「どちらの層の話か」を毎回確かめることになる。誤設定は静かに効かないだけなので気付けない |
| `readonly_args` / `no_session_args` は `interactive` の中 | **トップレベルにも置き、`interactive` 側は任意の上書き**（無ければトップレベルを継承） | 読み取り専用はヘッドレスの Doctor・構造化 Assist でも必要（`buildDoctorCommand` がまさにそれ）。対話の中だけに置くと同じ知識をもう一度書く羽目になる |
| （無し） | **`spill` ブロック**を追加 | kiro の「positional を渡すと stdin を読まない」癖と、その回避に要る `fs_read` 指示文は CLI 固有知識。JS 側にハードコードしたままだと受入条件（JSON 1 ファイルで完結）を満たせない |

`readonly` は未決事項 7 の決着（§6）で足したフィールド。

### 2.2 フィールドの意味

| フィールド | 意味 | 既定 |
|---|---|---|
| `readonly_args` | 助言のみ（ファイルを書かない・コマンドを実行しない）にする追加フラグ | `[]` |
| `readonly` | 上のフラグの**強制力**。`enforced`=CLI が保証する / `best-effort`=CLI 依存で保証しない | `best-effort` |
| `no_session_args` | セッション永続化を切るフラグ（使い捨て診断用） | `[]` |
| `spill.args` | プロンプトを一時ファイルへ退避したとき **追加で**付けるフラグ | `[]` |
| `spill.instruction` | 退避したときに argv で渡す短い指示文。`{file}` が退避先パスに置換される。空なら退避しない | `""` |
| `interactive.command` | 対話起動 argv（`{model}` 使用可） | 無ければ対話起動を提供しない |
| `interactive.ready_pattern` | `tmux capture-pane` 出力から入力受付を検出する **ERE**（`grep -E` にそのまま渡す） | 組み込み既定パターン |
| `interactive.ready_timeout_sec` | 上の検出を諦めるまでの秒数 | 60 |
| `interactive.prompt_inject` | 初回プロンプトの注入方法。`send-keys`=1 行に畳んで送る / `file`=一時ファイルへ書き「このファイルを読め」を送る | `send-keys` |

`ready_pattern` を**正規表現の文字列**にするのは現行実装（`grep -qiE`）にそのまま載るため。パターンを持たない定義は組み込み既定（現行の 1 本）を使う——これは「知らない CLI でも従来どおりは動く」ための退避で、既定に依存し続ける CLI は定義を書けば直せる。

---

## 3. 組み込み CLI の JSON 化とローダ 1 本化（S9-2）

### 3.1 同梱定義の置き場と探索順

リポジトリ直下の `agents/` に `kiro.json` / `claude.json` / `copilot.json` / `codex.json` を追加する（既存の `cursor.json` / `ollama.json` と同じ場所）。

探索順は現行契約のまま（`$KIRO_AGENTS_DIR` → `<プロジェクト>/agents/` → `~/.agents/agents/` → `~/.kiro/agents/`、first-wins）で、**組み込み名の予約を解除する**。上位に `claude.json` を置けば同梱定義を上書きできる——受入条件「JSON 1 ファイルの修正で完結」は、これが無いと成り立たない。

**同梱定義が見つからなかったときの扱い**: フォールバックの組み込みテーブルは**持たない**。持てば「JSON を直したのに古い挙動のまま」という、いま消そうとしている二重管理が別の形で戻る。代わりに、ローダは定義を解決できないとき**インストールの破損として明示エラー**にする:

```
未知の agent_cli です: 'claude'（agents/claude.json が見つかりません）
探索順: $KIRO_AGENTS_DIR → <root>/agents → ~/.agents/agents → ~/.kiro/agents
インストールが壊れている可能性があります: bash install.sh を再実行してください
```

黙って別の CLI へ倒すよりは止まった方がよい（未知 `agent_cli` が黙って kiro へ落ちていた過去の罠を、`prioritize.py` は既に明示エラーに直している——同じ方針を組み込み名にも広げるだけ）。

### 3.2 Python ローダを agentcore へ

`agentcore/agentcli.py`（新規）に 1 実装を置き、agent-project / agent-flow / agent-amigos の 3 実装がこれを使う。

```python
load_cli(name, dirs=None) -> dict          # 探索・正規化・キャッシュ。壊れた定義は例外
headless_cmd(spec, model, prompt, *, readonly=False, no_session=False)
        -> (argv, stdin, output_file, cleanup)   # ヘッドレス 1 回分（実行はしない）
interactive_cmd(spec, model, *, readonly=False, no_session=False) -> argv
classify_error(spec, blob) -> (cls, hint) | None   # errors[] の評価（組み込みパターンより先）
```

スキーマの説明文にある「結合はこのデータ契約のみで、各ツールが自前の小さなローダで解釈する（ツール間のコード依存は作らない）」は、**Python 側については取り下げる**。S3 で同じ判断を一度している: URL 正規化を 3 者が自前実装した結果、吸収規則が微妙に食い違い「同じ 2 つの URL が経路によって一致したりしなかったりする」状態になった。CLI 定義の解釈も同型で、`empty_output_is_error` の扱いや `{model}` 省略規則が実装ごとにずれれば、同じ定義ファイルがツールによって別の argv になる。スキーマの説明文もこの機に改訂する。

**dashboard（JS）は自前ローダのまま**にする。Python を起動すると CLI チャットの起動が毎回プロセス起動待ちになる（S3-4 で `nodeRepos.js` を JS で書いたのと同じ理由）。JS 側は `agent.js` の既存 `normalizeAgentPlugin` を拡張して 1 本にまとめ、Python 実装とはテストで揃える（§5 の 8）。

### 3.3 用途 → フラグの対応（移行後）

| 呼び出し元 | モード | readonly | no_session |
|---|---|---|---|
| agent-project の act / plan / verify 等（`_run_agent_cli`） | headless | ✗ | ✗ |
| agent-flow の worker | headless | ✗ | ✗ |
| agent-amigos | headless | ✗ | ✗ |
| dashboard の charter 補完（`runAgent`） | headless | ✗ | ✗ |
| dashboard の Doctor / 構造化 Assist（`buildDoctorCommand`） | headless | ✓ | ✓ |
| dashboard の CLI チャット（`openInteractiveChat`） | interactive | ✗ | ✗ |
| cowork の tmux 実行 | interactive | ✗ | ✗ |
| S9-4 対話診断（Phase 4） | interactive | ✓ | ✓ |

現状 agent-project の claude だけが `--dangerously-skip-permissions` を持ち dashboard の claude は持たない、という差は**この表に集約して消す**（書き込みが要る用途は同じ argv、助言だけの用途は `readonly_args` を足す）。dashboard の charter 補完は現在フラグ無しで動いているが、`command` は書き込み可の骨とし、charter 補完は書き込みを要求しないので実挙動は変わらない。

### 3.4 適用範囲（S9-3）

tmux 経由のエージェント起動を全部このレイヤに通す。現在レイヤの外にあるのは:

1. **cowork の tmux 実行** — `cowork/config.js:15` の `chatCommand: 'kiro-cli chat --trust-all-tools'` という**文字列固定**。定常業務は `agent_cli` 設定を無視して常に kiro を起動している。`resolveAgent` → `interactive_cmd` 経由に変え、`chatCommand` は「明示上書き（空なら解決結果を使う）」へ降格する。
2. **入力受付の検出** — `loopProvider.js:388-403` のパターンを `ready_pattern` から受け取る。`chatWindowScript` の引数に `readyPattern` / `readyTimeoutSec` / `promptInject` を足す（既定は現行値なので、渡さない呼び出し元の挙動は変わらない）。
3. **kiro-loop の chat 起動** — 同じ `runChatWindow` を通るため 1・2 の変更で自動的に載る。

`prompt_inject: "file"` は現行に実装が無いので新規に足す（一時ファイルへ書き、`spill.instruction` の `{file}` を置換した 1 行を send-keys する）。S9-4 の対話診断がスナップショットを渡すために要る経路で、先に足しておく。

---

## 4. 実装単位

| # | 対象 | 内容 |
|---|---|---|
| S9-a | `schemas/agent-cli.schema.json` | `readonly_args` / `readonly` / `no_session_args` / `spill` / `interactive` を追加。説明文の「各ツールが自前のローダ」を改訂 |
| S9-b | `agents/{kiro,claude,copilot,codex}.json` + `agents/README.md` | 同梱定義。既存 `cursor.json` / `ollama.json` にも `interactive` を追記 |
| S9-c | `agentcore/agentcli.py` + tests | Python ローダ 1 本（load / headless / interactive / classify） |
| S9-d | agent-project | `_agent_cmd` の組み込み分岐を撤去し agentcore へ委譲。`load_agent_plugin` / `_normalize_agent_plugin` を削除 |
| S9-e | agent-flow | 同上（`agent.py:159-210` / `:701` 周辺） |
| S9-f | agent-amigos | 同上（`agentcli.py` は agentcore の薄い再輸出に縮める） |
| S9-g | agent-dashboard | `buildCommand` / `buildInteractiveCommand` / `buildDoctorCommand` の CLI 分岐を撤去し、拡張した `normalizeAgentPlugin` + 定義ファイルへ集約 |
| S9-h | agent-dashboard | `loopProvider.chatWindowScript` に `readyPattern` / `readyTimeoutSec` / `promptInject` を導入。cowork の `chatCommand` を解決結果ベースへ |
| — | ドキュメント | 各ツール README の agent_cli 節 / `agents/README.md` / CHANGELOG |

`AGENT_CLIS`（dashboard）と `_AGENT_CLI_BINARIES`（agent-project の doctor が PATH 確認に使う）は、定義ファイルの `command[0]` から導出する形に置き換える。

---

## 5. テスト計画

1. `load_cli`: 探索順（`$KIRO_AGENTS_DIR` → プロジェクト → `~/.agents` → `~/.kiro`）で first-wins。**上位に置いた `claude.json` が同梱定義を上書きする**
2. `load_cli`: 定義が 1 つも見つからない組み込み名は明示エラー（黙って別 CLI に落ちない）
3. `load_cli`: 壊れた定義（`command` 非配列 / `output=file` なのに `{output_file}` 無し / `errors.match` が不正な正規表現）は例外
4. `headless_cmd`: `{model}` 省略・`model_flag` の付与・`prompt_via` stdin/argv・`output=file` の一時ファイル
5. `headless_cmd(readonly=True)`: `readonly_args` が付く。`no_session=True` で `no_session_args` が付く
6. `headless_cmd`: `spill.instruction` があるとき、長大プロンプトが退避され argv には `{file}` 置換済みの短い指示文だけが載る（+ `spill.args` が付く）
7. `interactive_cmd`: `interactive.command` の `{model}` 展開。`interactive.readonly_args` 未指定ならトップレベルを継承する
8. **同一定義ファイルに対して Python と JS が同じ argv を返す**（組み込み 4 + cursor + ollama をゴールデンとして固定。実装が 2 つある以上、ずれを検出する仕掛けが要る）
9. 回帰: 移行後の kiro/claude/copilot/codex のヘッドレス argv が移行前と一致する（agent-project / agent-flow / agent-amigos / dashboard の 4 経路それぞれ）
10. 回帰: `buildDoctorCommand` の読み取り専用 argv が移行前と一致する（kiro の spill 有無の両方）
11. `chatWindowScript`: `readyPattern` を渡すとスクリプト中の `grep -qiE` がそのパターンになる。渡さなければ従来のパターン
12. `chatWindowScript`: `promptInject: "file"` で一時ファイル書き出し + 指示 1 行の send-keys になる
13. cowork の tmux 実行が `agent_cli` 設定に従う（kiro 以外を設定すると起動 argv が変わる）
14. `readonly: "best-effort"` の CLI に読み取り専用を要求したとき、返り値に警告フラグが立つ（§6-1）

---

## 6. 未決事項の決着（仕様書 §5-7）

### 6-1. `readonly_args` の強制力は CLI 実装依存（フラグを無視する CLI への防御を持たない）

**決着: 防御は持たない。ただし「保証できない」ことを宣言可能にし、人に見せる。**

レイヤができるのは宣言どおりのフラグを渡すことだけで、CLI がそれを守るかは CLI 側の問題。ここで無理に防御（サンドボックス化・ファイル監視）を作ると、レイヤの責務が「argv の組み立て」から「実行の隔離」へ膨らみ、CLI ごとに別の実装が要る——いま畳もうとしている散らばりが別の場所に再発する。

代わりに定義へ `readonly: "enforced" | "best-effort"`（既定 `best-effort`）を持たせ、読み取り専用を要求する呼び出し（Doctor・S9-4 の対話診断）が `best-effort` の CLI を使うときは、**画面に「このCLIでは助言のみを保証できません」と出す**。判断材料を人に渡すのが、この層でできる正直な対処。

同梱定義の初期値: kiro=`best-effort`（`--trust-tools=` は信頼するツールを絞るだけで実行自体は止めない） / claude=`enforced`（`--permission-mode plan`） / copilot=`best-effort` / codex=`enforced`（`--sandbox read-only`） / cursor・ollama=`best-effort`。

### 6-2. 対話セッションの副作用は人が見ている前提で許容するか

**決着: 許容する。ただし「使い捨て」と「常駐」を分ける。**

対話セッションは人が画面を見ながら操作するもので、副作用は人の責任範囲にある（CLI チャットも定常業務も、そもそも作業させるために開いている）。ここに制限をかけると、いま動いている用途が壊れる。

分けるのは**診断だけ**: S9-4 の対話診断は `readonly_args + no_session_args` で開き、セッション名も `agent-doctor-<digest>` と別系統にして、作業用セッションと混ざらないようにする。診断で開いた窓が作業セッションに合流すると、読み取り専用のつもりの窓から書き込みができてしまう。

---

## 7. 実装で確定した差分

| 項目 | 実装 |
|---|---|
| **`write_args` の追加** | 設計では「`readonly_args` を足す / 足さない」で足りると考えていたが、kiro は既定が `--trust-all-tools`・読み取り専用が `--trust-tools=` で、**追加ではなく排他**だった（並べると後勝ちに賭けることになる）。既定モードのフラグを `write_args` として分離し、両モードが対になる形にした |
| **`command_suffix` の追加** | codex の `-`（プロンプトを stdin から読む位置引数）は必ず末尾でなければならない。`command` に書くとモード別フラグとモデル指定がその後ろへ回る |
| **`interactive.write_args`** | 対話の既定でも kiro は `--trust-all-tools` が要る一方、トップレベルの `write_args`（claude の `--dangerously-skip-permissions` 等）を対話へ持ち込むのは危険。継承せず、対話で要るものは明示する形にした |
| **`spill.instruction` は「置き換え」ではなく「付け足し」** | 当初は本文の代わりに instruction を渡す設計にしたが、Doctor は**役割・出力書式を argv 側**に載せていて本文（スナップショット）だけをファイルへ逃がす。置き換えると役割ごと消える。呼び出し側の短い指示の末尾へ足す形にした（`spill.args` の方は設計どおり権限フラグを置き換える） |
| **dashboard のヘッドレスは全て読み取り専用へ** | 移行前は charter 補完だけが権限フラグ無しで、Doctor だけが読み取り専用だった。`agent.js` 冒頭が謳う「書き込みはビュアー側が行う」という護りに argv を合わせ、3 経路（charter 補完・Doctor・構造化 Assist）を同じモードに揃えた。結果 `buildCommand` と `buildDoctorCommand` はほぼ同じものになった |
| **同梱定義のパッケージ同梱** | `agents/` はアプリのソースツリーの外にあるので `build.files` では入らない。フォールバックの組み込みテーブルを持たない設計なので、入っていないと `~/.agents/agents/` が無い端末で AI 機能が全滅する。`extraResources` で同梱し、packaging テストで固定した |
| **`cowork.chatCommand` の降格** | 設計では「cowork も `resolveAgent` 経由にする」とだけ書いたが、既存設定を無視すると上書きの口が消える。空なら解決結果・値があれば明示上書き、という降格にした |
| **失敗トリアージの副産物修正** | `classify_agent_failure` がヒントを**クラス一致**で引いていたため、kiro 定義に quota 規則を足した途端 codex の usage limit に kiro の月間上限の案内が付いた。実際に一致した規則からヒントを採るよう直した（既存の潜在バグ） |

**実績**: agentcore 72 件 / agent-project 953 件 / agent-flow 564 件 / agent-amigos 176 件 / agent-dashboard 全スイート green。

## 8. 積み残し（この設計に含めないもの）

1. **S9-4（対話診断）** — Phase 4。本設計のレイヤが前提なので順序は S9-1〜3 → S9-4 で固定。
2. **dashboard の YAML 定義読み取り** — 定義ファイルは JSON のみ（このアプリは YAML パーサを持たない。S3-4 の `nodeRepos.js` と同じ制約）。
3. **`errors[]` の分類を dashboard でも使う** — 現在 dashboard は失敗トリアージを持たず、`commandResultText` が kiro の月間上限だけを特別扱いしている。定義の `errors[]` を読めば汎用化できるが、S9 の受入条件には含まれないので別途。
