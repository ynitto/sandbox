# agent-audit — 実行証跡・セッションログの収集と知見蒸留

agent-project / agent-flow / agent-amigos / agent-loop の実行証跡、エージェント CLI
自身のセッションログ、対応CLIのquotaを収集・正規化し、

- **トークン使用量の集計**（実測と推定を別掲）
- **実行品質の集計**（失敗クラス・リトライ・verify）
- **知見・スキル改善点の蒸留**（LLM は extract / distill の 2 段だけ）

を行う独立 CLI。どのツールにも依存せず、エージェント CLI 単独利用の環境でも
セッションログの集計だけで完結する。設計の「なぜ」は
[`docs/designs/agent-audit-design.md`](../../docs/designs/agent-audit-design.md)、
契約・設定キー・上限は
[`docs/specs/agent-audit-spec.md`](../../docs/specs/agent-audit-spec.md)、
導入から定期実行までの手順は
[`docs/guides/agent-audit-setup.md`](../../docs/guides/agent-audit-setup.md)。

## インストール

```bash
bash tools/agent-audit/install.sh            # 実体は tools/agent-tools/install.sh
# または
bash tools/agent-tools/install.sh --only agent-audit
```

前提は python3.11+ のみ（YAML 設定を使うときだけ PyYAML）。

## 使い方

```bash
agent-audit collect                  # 源泉 + 対応CLI quotaの増分収集（LLM不使用）
agent-audit usage --period month --by agent_cli
agent-audit stats                    # 実行品質 + LLM判断と決定的ルールの一致率
agent-audit report                   # Markdown レポート（usage + quality + knowledge + insights）
agent-audit report --kind knowledge [--json]
                                     # 記憶 3 層 + 共有路の健全性（publish 待ち・忘却リスク・
                                     # outbox 滞留・queries ヒット率。LLM 不使用）

agent-audit extract                  # レコード → 観測（LLM map・弱モデル可）
agent-audit distill                  # 観測クラスタ → 洞察（LLM reduce）
agent-audit tasks                    # 洞察 → 改善タスク（JSON を stdout へ）
agent-audit tune [--apply]           # 型付き調整候補 → 宣言へ昇格、悪化・期限で自動退役

agent-audit calibrate [--write]      # rates 較正の提案（--write で budget config へ）
agent-audit ratings --period month [--methods] # 仕事種別×モデル（任意で手法セット軸）の格付け
agent-audit trials --period month    # 2 variant trial の PASS 率・平均消費・差分判定
agent-audit gc [--dry-run]           # 保持期限での掃除（通常は collect が定期実行）
agent-audit reclean [--agent-cli N]  # clean ルール改訂後に既存 transcript を再生成
agent-audit sessions --cli N [--since T --until T --cwd-contains S] [--messages ID]
                                     # CLI ネイティブセッションの検索・本文取得（JSON。
                                     # dashboard の「ノードの会話を見る」導線が使う読み取り口）
agent-audit doctor                   # 源泉の到達性・clean 宣言の点検
```

`usage --by agent_cli` は利用量に加え、node-budget で宣言した CLI 別トークン上限と、
quota 観測から得た使用率・復帰時刻も表示する。Claude は `/usage`、Codex は
app-server、Copilot は `/usage`、Kiro は ACP の組み込み usage から取得する（LLM 不使用）。
同じ値が続く場合もnode-budget台帳には収集成功ごとにsnapshotを追記し、Resource Controllerが
観測の鮮度を判断できるようにする。監査ストア側の同一snapshotは従来どおり重複排除する。

定期実行は同梱 `audit-calibrate-hook.py` が collect → calibrate → extract → distill --review →
tune --apply を順に実行する。
extract / distill には間隔・蓄積ゲートがあるので、**高頻度で駆動しても LLM 消費は
設定したリズムを超えない**（`--force` はゲートだけを飛ばす。上限と予算は飛ばせない）。

## 設定

雛形は [`agent-audit.yaml.example`](./agent-audit.yaml.example)。優先順位は
**CLI 引数 > 設定ファイル > 組み込み既定**で、agent-audit 固有の環境変数は無い。
探索順は `--config` → `<cwd>/agent-audit.*` → `<cwd>/.agents/` → `~/.agents/`。

LLM の段別モデル選択（トークン削減の要）:

```yaml
agents:
  extract: {agent_cli: ollama, model: qwen3}   # 局所要約は弱モデルで
  distill: {agent_cli: claude, model: sonnet}  # 一般化は中〜強モデルで
```

## どこを読むか

| 源泉 | 場所（既定） | 取るもの |
|---|---|---|
| node-budget 台帳 | `~/.agents/budget/ledger/*.jsonl` | 消費の一次事実（秒・トークン）と適用手法・trial |
| CLI セッション | `agents/<name>.json` の `session_log` 宣言 | 実測トークン・turn 数・transcript（`session_log.clean` でノイズ除去。§4.4） |
| CLI quota | Claude `/usage` / Codex app-server / Copilot `/usage` / Kiro ACP | 使用率・復帰時刻。到達状態はnode-budget台帳へ観測として共有 |
| agent-flow バス | 設定 `flow_buses` / `project_roots` | run の結果・失敗クラス・verify・読込割付・依存digest削減量・モデル昇格・planner判断照合・適用手法・trial |
| agent-project | 設定 `project_roots` の `run-log.jsonl` | run 単位の実績・コスト |
| agent-amigos バス | 設定 `amigos_buses` | ターン数・実行秒 |
| agent-loop ログ | 設定 `loop_logs` | エラー行 |
| 記憶 3 層 + 共有路 | 設定 `memory_stores`（ltm / wiki / persona / moltbook） | frontmatter・件数・mtime・索引・ログの**メタデータだけ**（記憶の本文は読まない。persona は件数と滞留日数のみ） |

記憶ストアに対しても **agent-audit は読み手に徹する**——整理（consolidate / batch-update /
索引再構築 / lint）の実行はスキル自身のスクリプトが担い、audit は測って整理候補を出すまで。

書くのは audit ディレクトリ（既定 `~/.agents/audit/`）と、quota観測を共有する
node-budgetの追記専用台帳だけ。transcript 本文は
ローカルに留まり、report / tasks の出力は資格情報の伏せ字化とパスのホーム相対化を
必ず通る。

## セッションログのクリーニング

CLI ネイティブのセッションログにはシステムリマインダの注入・サイドチェーン・
コマンドエコーなど会話の実体以外のノイズが混ざる。`agents/<name>.json` の
`session_log.clean` で、CLI ごと・バージョンごとに閉じたルール（`drop-line` /
`drop-message` / `strip-tag` / `strip-regex` / `truncate-message`）を宣言すると、
collect が transcript・turns・extract ダイジェストから決定的に取り除く（LLM 不使用。
設計 §4.4）。実装は `agent_audit/cleaning.py` に 1 つで、ルール改訂は JSON 追記だけで
完結する。過去に保存済みの transcript へ改訂を反映するには `agent-audit reclean` を使う
（records・処理済み管理には触れない）。

## テスト

```bash
python3 -m unittest discover -s tools/agent-audit/tests
```
