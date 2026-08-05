# tools/opencode — opencode を別 PC の ollama で回す

[opencode](https://opencode.ai) を **agent-tools シリーズ（agent-project / agent-flow /
agent-amigos / agent-audit / agent-dashboard）から `agent_cli: opencode` で呼べる**ところまで
一気に構成する独立インストーラ。推論は手元ではなく**別 PC の ollama**（OpenAI 互換
エンドポイント）に投げる。

```bash
# 推論する PC（GPU 機）側で 1 回
OLLAMA_HOST=0.0.0.0 ollama serve

# 使う PC 側
bash tools/opencode/install.sh --ollama-host http://192.168.1.20:11434
```

## なぜ独立インストーラなのか

`tools/agent-tools/install.sh` は **4 エンジンを 1 パッケージで揃える**ためのもの（契約
バージョンがずれた片肺ノードを作らないのが目的）。opencode はその契約に載る *利用者側の
CLI* で、エンジンとは寿命も更新タイミングも別だし、推論サーバの住所は PC ごとに違う。
同じインストーラに畳むと「エンジンを入れ直すたびに opencode の設定を触る」ことになるので、
分けてある。逆に、agent-tools 側は opencode を知らなくても壊れない——CLI の知識は
`agents/opencode.json` の 1 ファイルに閉じている（契約: `schemas/agent-cli.schema.json`）。

## 入るもの

| 置き場 | 中身 |
|---|---|
| `opencode`（PATH） | opencode 本体（未導入なら公式スクリプト → npm の順で導入） |
| `~/.config/opencode/opencode.json` | ollama プロバイダ（OpenAI 互換）・既定モデル・plan の権限 |
| `~/.local/bin/agent-opencode` | 実測 usage と事前到達性チェックのアダプター |
| `~/.agents/agents/opencode.json` | エージェント CLI 定義（agent-tools 側の探索順 3 番目） |

## agent-opencode（アダプター）が居る理由

`opencode run` をそのまま呼ばないのは、素の argv では表せないものが 2 つあるから。

1. **実測 usage**。`opencode run --format json` は `step_finish` イベントに
   `tokens.{input,output}` を載せる。これを stderr の
   `@agent-usage tokens_in=… tokens_out=…` へ移すと、台帳（`agent-audit usage`）が推定では
   なく実測で埋まる。`agent-ollama` が居るのと同じ理由。
2. **落ちない失敗を落とす**。推論サーバが落ちていると opencode は即座に失敗せず**内部
   リトライで待ち続ける**（実測: 接続不可のまま 120 秒待っても終了しない）。呼び出し側の
   タイムアウトまで枠を焼くだけなので、実行前に 1 回だけ到達性を確かめ、駄目なら
   `env` 分類（＝人が環境を直す。リトライを焼かない）に乗るメッセージで即座に失敗する。

```bash
echo '要件を要約して' | agent-opencode --model ollama/qwen3   # ヘッドレス
agent-opencode --check                                        # 到達性だけ確認
```

環境変数: `OPENCODE_BIN`（既定 `opencode`）/ `AGENT_OPENCODE_PROVIDER`（モデル名に
`provider/` が無いときの既定・既定 `ollama`）/ `AGENT_OPENCODE_SKIP_PREFLIGHT=1` /
`AGENT_OPENCODE_PREFLIGHT_TIMEOUT`（秒・既定 5）。

## agent-tools シリーズからの使い方

設定に `agent_cli: opencode` と書くだけ。モデルは opencode の作法どおり
`<プロバイダ>/<モデル>`（例 `ollama/qwen3`）。

```yaml
# agent-flow.yaml / agent-project.yaml
agent_cli: opencode
model: ollama/qwen3

# agent-audit.yaml — 局所要約だけローカル推論に落とす（既定 CLI はクラウドのまま）
agents:
  extract: {agent_cli: opencode, model: ollama/qwen3}
```

役割ごとの上書き（`agents:` ブロック）や agent-amigos の `roles.yaml` でも同じ名前が使える。
agent-dashboard の CLI 選択にも自動で並ぶ（定義ファイルを読むだけなので UI 側の改修は不要）。

## 読み取り専用は best-effort

`readonly_args` は `--agent plan`（opencode 組み込みの計画エージェント）。**edit は拒むが
bash は拒まない**ので、契約上は `best-effort`（＝助言のみを保証できない）と宣言してある。
実効を上げるため、インストーラは設定側で plan を締める:

```json
{ "agent": { "plan": { "permission": { "edit": "deny", "bash": "deny", "webfetch": "deny" } } } }
```

宣言を `enforced` に上げていないのは、この締めが**こちらが書いた設定にしか無い**から。
他の PC の設定や、`--agent` に存在しない名前を渡したとき（opencode は警告だけ出して
**既定エージェントへフォールバックする**）まで保証できるとは言えない。

## インストーラのオプション

```
--ollama-host <url>     推論エンジンの住所（既定 $OLLAMA_HOST か http://localhost:11434）
--models <a,b,...>      使うモデル（既定: /api/tags から自動取得）
--default-model <name>  既定モデル（既定: --models の先頭）
--small-model <name>    表題生成など軽い用途のモデル（既定: 既定モデルと同じ）
--provider-id <id>      設定に書くプロバイダ ID（既定 ollama）
--context <n>           入力上限の宣言（既定 32768）  --max-output <n>（既定 8192）
--config <path>         opencode 設定（既定 $OPENCODE_CONFIG か ~/.config/opencode/opencode.json）
--prefix <dir>          agent-opencode の置き場（既定 ~/.local/bin）
--agents-dir <dir>      CLI 定義の配布先（既定 ~/.agents/agents）
--method <auto|script|npm|skip>   opencode 本体の入れ方
--skip-agents / --print-config / --doctor
```

既存の設定は**壊さない**。読み込んでマージし、`.bak` に退避してから書く。`model` /
`small_model` は未設定のときだけ書き、`provider.<id>.models` も既存の宣言（手で調整した
`limit` など）を残したまま追記する。opencode の設定は**未知のキーを拒否する**
（`Unrecognized key` で起動ごと落ちる）ので、書くキーは実機で通ることを確かめたものだけに
限ってある。

## 推論する PC（ollama 側）の準備

```bash
OLLAMA_HOST=0.0.0.0 ollama serve       # LAN から届くようにする（既定は 127.0.0.1 のみ）
OLLAMA_CONTEXT_LENGTH=32768 ollama serve   # 文脈長。既定のままだと長いプロンプトが黙って切れる
ollama pull qwen3                      # 使うモデル
```

- ファイアウォールで 11434/tcp を開ける（Windows なら受信規則を 1 本）。
- `--context` の宣言はあくまで **opencode 側の見積り**。実際に効くのはサーバ側の
  `OLLAMA_CONTEXT_LENGTH`（または Modelfile の `num_ctx`）なので、両方を揃える。
- ツール呼び出しに対応したモデルを選ぶ（qwen3 / qwen2.5-coder など）。非対応モデルだと
  opencode はファイルを読み書きできず「助言しか返らない」状態になる。

## 点検

```bash
bash tools/opencode/install.sh --doctor --ollama-host http://192.168.1.20:11434
```

opencode 本体 / `agent-opencode` / 設定 / CLI 定義 / ollama への到達性とモデル一覧を見る。
何も書かない。

## 既知の制約

- **セッションログは agent-audit の収集対象外**。opencode はセッションを SQLite
  (`~/.local/share/opencode/opencode.db`) に持つが、`schemas/agent-cli.schema.json` の
  `session_log.format` は `jsonl-dir` / `kiro-sqlite` しか持たない。パーサを足すまでは
  「未収集」として明示される（黙ってスキップはしない）。トークンの実測は
  `@agent-usage` 経由で台帳に入るので、usage 集計には影響しない。
- **スキル配布（`install.py --agent …`）は未対応**。opencode は Claude Code のスキルを
  読むので `~/.claude/skills` に入れてあるものは効くが、opencode 専用の配布先は持たせて
  いない。
