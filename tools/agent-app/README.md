# agent-app

GitHub Copilot App 風のデスクトップ。ローカルリポジトリを登録し、`agents/*.json` に定義した
エージェント CLI（copilot / claude / codex / kiro / cursor …）と会話形式で作業する Electron アプリ。
GitHub との連携は持たない。見に行くのは登録したフォルダだけで、CLI はこの PC に入っているものを
そのまま呼ぶ（このアプリのために何かを入れる必要はない）。

- **左**: 登録したリポジトリと、そのリポジトリの会話一覧。応答中の会話には印が付き、別の会話を
  開いて並行して進められる
- **中央**: チャット。上でエージェント・モデル・モード（Agent / Ask）を選び、下に依頼を書く。
  応答中は CLI の出力を「ログ」に流す
- **右**: 作業ツリーの変更（`git status` と `git diff`）。ターンが終わるたびに更新する

## 起動

```bash
cd tools/agent-app
npm install
npm start
```

テストは Electron を起動せずに通る:

```bash
npm test
```

## CLI の呼び方

argv の組み立ては agent-tools ファミリーの契約（[`agents/README.md`](../../agents/README.md)、
正典は `schemas/agent-cli.schema.json`）に従う。会話に要る分だけを `src/main/agentCli.js` に持ち、
定義の探索順も同じ（`$KIRO_AGENTS_DIR` → リポジトリの `agents/` → `~/.agents/agents` →
`~/.kiro/agents` → このリポジトリ直下の `agents/`）。

```
command + (write_args | readonly_args) + model_flag model + command_suffix + プロンプト
```

Agent モードは `write_args`、Ask モードは `readonly_args`。`readonly: best-effort` の CLI で Ask を
選ぶと、保証できない旨を画面に出す（copilot と kiro がそれ）。

1 ターン = CLI をヘッドレスで 1 回起動し、終わったら次のターンでセッションを再開する。
再開の作法は CLI ごとに違うので、定義ファイルに昇格するまでは `agentCli.js` の `SESSION` 表に置く:

| CLI | 初回 | 2 回目以降 |
|---|---|---|
| claude / copilot | こちらで UUID を発行して `--session-id` | `--resume <UUID>` |
| codex | `--json` を足し、出力の `thread_id` を拾う | `codex exec resume <id>` |
| kiro | 何も足さず、終了後に `--list-sessions --format json` の最新を拾う | `--resume-id <id>` |
| cursor | `continue_args`（`--continue`。同じ CLI を並行して使うと混線する） | 同左 |
| それ以外（aider / ollama / vscode-copilot） | 会話履歴をプロンプトへ再送 | 同左 |

停止はプロセスグループごと（CLI が起こした孫まで止める）。

## 保存先

Electron の userData（macOS は `~/Library/Application Support/agent-app`）にだけ書く。

```
config.json          登録したリポジトリ、最後に選んだリポジトリ・エージェント・モデル・モード
sessions/<id>.json   会話 1 つ。リポジトリ・CLI・メッセージ列・CLI 側のセッション ID
```

リポジトリ側には何も置かない。CLI 自身のセッションログ（`~/.claude/projects` など）は CLI の管轄。

## 持たないもの

- GitHub 連携（PR・Issue・クラウドセッション）
- ツール呼び出しの逐次承認。権限は Agent / Ask の 2 択で、細かい許可は CLI 側の設定に任せる
- 差分の適用・取り消し。変更ビューは読むだけで、git への書き込みは持たない
