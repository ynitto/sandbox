---
name: kiro-flow
description: kiro-flow CLI を呼び出して分散 Dynamic Workflow を実行・監視するスキル。kiro-cli を頭脳に、要求を6パターンから戦略化してタスクグラフへ分解し、git/ローカルのファイルバス上で複数ワーカーに分散実行する。「ワークフローを実行して」「kiro-flow で動かして」「タスクを分散実行して」「要求を投入して」「デーモンを起動して」「run の状態を見て」「古い run を掃除して」などで発動する。Claude Dynamic Workflows 風の動的分解・再計画・データ駆動 fan-out を使いたい場合に選択する。
metadata:
  version: 1.0.0
  tier: experimental
  category: orchestration
  tags:
    - kiro-flow
    - kiro-cli
    - dynamic-workflow
    - multi-agent
    - distributed
    - orchestration
---

# kiro-flow — 分散 Dynamic Workflow CLI スキル

`kiro-flow` CLI（`tools/kiro-flow/kiro-flow.py`）を呼び出して、要求を動的にタスク分解 →
複数ワーカーで分散実行 → 結果を評価して再計画 → 統合する。LLM 実行は kiro-cli が既定。
kiro-cli が無い環境では `--planner stub --executor stub` で挙動を確認できる。

## 前提

- Python 3.9+（標準ライブラリのみ）。git は分散モード（`--git`）で必要。実運用には kiro-cli。
- 未インストールなら `python3 tools/kiro-flow/kiro-flow.py …` で代用。インストール済みなら `kiro-flow …`。

```bash
KF="kiro-flow"   # 未インストールなら KF="python3 tools/kiro-flow/kiro-flow.py"
```

## ロール選択ガイド

| 発動フレーズ | コマンド |
|-------------|---------|
| 「ワークフローを実行して」「kiro-flow で動かして」 | `run` |
| 「中断した run を再開して」 | `run --run-id <id>`（要求は省略可） |
| 「デーモンを起動して」「常駐させて」 | `daemon`（多重起動しない＝冪等） |
| 「要求を投入して」「タスクを依頼して」 | `submit` |
| 「run の状態を見て」「進捗を見せて」 | `status`（`--follow` でライブ） |
| 「古い run を掃除して」 | `gc` |

## よく使う呼び出し

### 単発実行（既存 run-id なら自動で再開）

```bash
$KF run "要件整理; API設計; テスト" --workers 3
$KF run --run-id <run-id>                      # 中断した run を再開
# kiro-cli 無しで動作確認:
$KF run "a; b; c" --planner stub --executor stub
```

要求の書き方でパターン/並列数が変わる:

- `;` 区切り＝並列、`->`＝逐次依存（例 `"setup -> build -> test; docs"`）。
- `xN` / `並列N` で並列数を指定。
- 「分類して振り分け」→ classify-and-act、「最良/tournament」→ tournament、
  「候補/フィルタ」→ generate-and-filter、「検証/レビュー」→ adversarial、
  「繰り返し/通るまで」→ loop-until-done、「それぞれ/各/一覧」→ map-reduce（データ駆動 fan-out）。
- `--review` で統合（synthesize/reduce）前に検証 gate を挟む。
- `--max-fanout N`（既定50）でデータ駆動 fan-out の上限。`--max-iterations N`（既定3）で再計画上限。

### デーモン（オンデマンド起動・推奨）

`daemon` は**同一バスにつき 1 つだけ**起動する（既に稼働中なら何もせず終了＝冪等）。
「デーモンを起動して」と言われたら、まず起動コマンドを実行してよい（重複起動の心配は不要）。

```bash
$KF daemon --max-workers 4 &                   # 常駐（多重起動しない）
$KF &                                          # サブコマンド省略でも daemon
RID=$($KF submit "<要求>")                      # 要求を投入（run-id が返る）
$KF status --run-id "$RID" --follow --until-done
```

### 分散（複数 PC）

各 PC で同じ `--git` を指すだけ。バスはリポジトリ直下、または `--git-subdir` でサブディレクトリ。

```bash
$KF --git <repo-url> [--git-subdir flow] daemon --max-workers 4 &   # PC ごとに（冪等）
$KF --git <repo-url> submit "<要求>"
```

### 状態確認・掃除

```bash
$KF status --run-id <run-id>      # 公式風ダッシュボード（進捗バー/エージェント状態/アクティビティ）
$KF status --follow               # ライブ監視（tmux ペイン向け）
$KF status --list                 # run 一覧
$KF gc --older-than 7 --status done --dry-run   # 古い done を掃除（まず dry-run）
```

## 環境ごとの設定

環境依存値（bus / git / planner / executor / max_workers / poll / lease 等）は設定ファイルに置ける
（優先順位 CLI > 設定ファイル > 既定）。検索順: `--config` → `./.kiro/kiro-flow.{yaml,yml,json}` →
`~/.kiro/kiro-flow.{yaml,yml,json}`。サンプル: `tools/kiro-flow/kiro-flow.yaml.example`。

## 使い分けの指針

- **すぐ結果が欲しい単発タスク** → `run`（フォアグラウンドで待機、完了で自動停止）。
- **継続的に要求を捌く / 複数 PC で回す** → `daemon` を常駐させ `submit` で投入。
- **長時間タスク** → `--lease` を実行時間より十分大きく（実行中はハートビートが自動延長）。
- 動作確認やデモは必ず `--planner stub --executor stub`（kiro-cli を消費しない・即時）。

詳細仕様は `tools/kiro-flow/README.md` と `docs/designs/kiro-flow-design.md` を参照。
