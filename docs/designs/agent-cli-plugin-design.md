# エージェント CLI プラグインと失敗トリアージ — 設計書

> 最終更新: 2026-07-13 ／ 関連: `schemas/agent-cli.schema.json`（契約の正典）, `agents/`（同梱定義）,
> `tools/kiro-project/kiro_project/prioritize.py`, `tools/kiro-flow/kiro-flow.py`

kiro-project / kiro-flow の LLM 呼び出し（エージェント CLI）を、**リポジトリ内で共通化した
データ契約**で差し替え可能にし、あわせて失敗を**決定的にトリアージ**（誰が直すか分類）する仕組み。

## 1. 動機

- agent_cli は kiro / claude / copilot / codex のハードコード 4 択で、cursor / ollama / hermes
  などの CLI を足すには両ツールのコードを触る必要があった。未知の値は**黙って kiro-cli に
  落ちる**罠もあった（設定ミスに気づけない）。
- 失敗の扱いが「なんでもリトライ・なんでも人へ」だった。認証切れ・利用上限のような
  **環境要因**は、どのタスクをリトライしても同じ理由で落ちる（実際 codex の利用上限で
  26 ノードがリトライを焼き尽くし「理由不明の全滅」になった）。逆に一時的エラーまで
  人へ回すと判断待ちが濫造される。

## 2. プラグイン契約（データのみ・コード共有なし）

- **正典**: [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json)。
  1 CLI = 1 ファイル `agents/<name>.json`。`agent_cli: <name>`（グローバル / `agents:` の
  役割毎上書き）で使う。
- **探索順**: `$KIRO_AGENTS_DIR` → `<プロジェクトルート>/agents/`（= 実行時 cwd）→
  `~/.kiro/agents/`。同名は先勝ち。組み込み名は上書き不可。
- **各ツールが自前の小さなローダを持つ**（`load_agent_plugin` / `_plugin_agent_cmd`）。
  kiro-project と kiro-flow の結合は task.schema / repos.schema と同じく**データ契約のみ**。
- 定義できること: argv（`{model}` / `{output_file}` プレースホルダ）・プロンプトの渡し方
  （stdin / argv ＋自動スピル）・モデルフラグと既定モデル・応答の取り出し（stdout / ファイル）・
  追加環境変数・タイムアウト・空応答の扱い・**エラー分類規則（errors）**。
- 未知の agent_cli で定義も無ければ**明示エラー**（黙るフォールバックは廃止）。
- 同梱定義: `agents/ollama.json`（ローカル LLM）, `agents/cursor.json`。追加手順は
  [`agents/README.md`](../../agents/README.md)。

## 3. 失敗トリアージ（決定的・LLM 不使用）

エラー本文（プラグインの `errors` → 汎用パターンの順）から「誰が直すか」を分類し、
メッセージ先頭の機械可読タグ **`[agent-error:<class>]`** で全層に運ぶ。

| class | 意味 | 誰が直すか | 各層の動き |
|---|---|---|---|
| `quota` | 利用上限 | 時間（またはプラン見直し） | 下記「環境要因」の扱い |
| `auth` | 認証切れ | 人（再ログイン） | 同上 |
| `env` | 実行環境（CLI 不在・モデル不正） | 人（環境修復） | 同上 |
| `transient` | 一時的（タイムアウト・接続断） | 誰も（自動で解ける） | 通常リトライ |
| （タグ無し） | 内容の問題 | タスク単位の判断 | 従来どおり retry → 裁定 → 人 |

**環境要因（quota/auth/env）の扱い** — 3 層が同じタグを読む:

1. **kiro-flow**（`_continue` → `_env_failure_reason`）: 環境要因の失敗ノードが 1 つでもあれば
   **再計画せず run を即 failed で終端**（`meta.failure_reason` にタグ付き理由）。全ノードで
   リトライを焼き尽くす無駄を止める。done ノードは温存＝再開で続きから。
2. **kiro-project**（`_settle_failure`）: vmsg と `last_run` の meta/final からタグを読み、
   **リトライを消費せず・裁定（これも LLM＝同じ理由で失敗する）も呼ばず**、原因と直し方を
   明記して needs へ。環境を直して approve すれば同じ run の続きから再開する。
3. **viewer**（`runAdvice`）: `failureReason` のタグを読み、タスク状態より先に
   「🔑 認証切れ — 再ログイン後、要対応タブで承認すると続きから再開」等を言い切る。

## 4. 不変条件

- 分類は**決定的**（正規表現のみ・LLM 不使用）。判定に迷うものはタグ無し＝「内容の問題」
  に倒し、従来のタスク単位フロー（retry → 裁定 → 人）に委ねる。
- トリアージは「止める・人へ知らせる」方向にのみ働く。done を作らない・予算を破らない
  （kiro-project 設計書 §1 の不変条件に従属）。
- プラグインは stdlib（json/re）だけで読める。PyYAML 等の依存を増やさない。

## 5. viewer の executor 連動（付随）

run の `meta.executor` を orchestrator が記録（`note_executor`）し、viewer は
`run.gitlabish`（executor==='gitlab'、旧 run は証跡から推定）で GitLab 連携 UI
（⟳ 最新化・関連イシュー・自動突き合わせ）を表示切替する。gitlab executor を
使っていない run に無意味なボタンを並べない。
