# agents/ — エージェント CLI プラグイン定義

agent-project / agent-flow（および旧系統 kiro-project / kiro-flow）の `agent_cli` に、組み込み（`kiro` / `claude` / `copilot` / `codex`）
以外の CLI を**コードを触らずに**追加する置き場。1 CLI = 1 ファイル（`<name>.json`）で、
`agent_cli: <name>`（または `agents:` の役割毎上書き）と書けばそのまま使える。

契約の正典は [`schemas/agent-cli.schema.json`](../schemas/agent-cli.schema.json)。
ツール間の結合はこのデータ契約のみ（agent-project / agent-flow がそれぞれ自前の小さな
ローダで解釈する。互いにコードは共有しない）。

## 探索順

1. `$KIRO_AGENTS_DIR`（環境変数）
2. `<プロジェクトルート>/agents/`（= agent-project 実行時の cwd。プロジェクト固有の定義）
3. `~/.kiro/agents/`（ユーザー共通）

同名は先勝ち。組み込み名（kiro/claude/copilot/codex/stub）は上書きできない。

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
- `errors` に CLI 固有の失敗パターンを書くと、**失敗トリアージ**（quota=時間をおけば回復 /
  auth・env=人が環境を直す / transient=自動リトライ）に反映され、agent-project は
  リトライを無駄に焼かず・viewer は「誰が何を直せばよいか」を表示できる。

## 同梱の定義

| ファイル | CLI | 備考 |
|---|---|---|
| `ollama.json` | `ollama run <model>` | ローカル LLM。`default_model` を環境に合わせて変更 |
| `cursor.json` | `cursor-agent` | Cursor CLI（要ログイン） |

hermes（tools/hermes-kiro-acp）のような自作ブリッジも、stdin でプロンプトを受けて
stdout に本文だけを返す薄い CLI を用意すれば同じ契約で差し込める。
