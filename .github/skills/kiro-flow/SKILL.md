---
name: kiro-flow
description: kiro-flow CLI を呼び出して分散 Dynamic Workflow を実行・監視し、完了後は最終結果を提示するスキル。kiro-cli を頭脳に、要求を6パターンから戦略化してタスクグラフへ分解し、git/ローカルのファイルバス上で複数ワーカーに分散実行する。「ワークフローを実行して」「kiro-flow で動かして」「タスクを分散実行して」「要求を投入して」「デーモンを起動して」「run の状態を見て」「結果を見せて」「最終結果は？」「成果物を出して」「古い run を掃除して」などで発動する。Claude Dynamic Workflows 風の動的分解・再計画・データ駆動 fan-out を使いたい場合に選択する。
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
複数ワーカーで分散実行 → 結果を評価して再計画 → 統合する。LLM 実行は kiro-cli が既定
（設定 `agent_cli` で claude / copilot / codex ヘッドレスへ切替可）。
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
| 「要求を投入して」「タスクを依頼して」 | `submit`（投入前に daemon を確保。git リポジトリ内なら GitHub 共有バスで起動） |
| 「run の状態を見て」「進捗を見せて」 | `status`（`--follow` でライブ） |
| 「結果を見せて」「最終結果は？」「成果物を出して」 | `result`（完了済みなら最終成果を提示） |
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

### submit でタスクを投入する手順（daemon の確保 ＋ git 自動設定）

`submit` は要求を inbox に置くだけで、実際に実行するのは `daemon`。したがって
**submit の前に必ず daemon を確保する**（稼働確認は不要 — `daemon` は冪等で、既に動いていれば
自動でスキップされるので、そのまま起動コマンドを実行すればよい）。さらに
**カレントディレクトリが git リポジトリなら、その origin（GitHub）リモートを共有バスにして起動**し、
複数 PC でそのまま分散できるようにする。

```bash
# 1) バスモードを決定: git リポジトリ内なら GitHub 共有バス、そうでなければローカル
if BUS_REMOTE=$(git remote get-url origin 2>/dev/null); then
  REPO=$(basename -s .git "$(git rev-parse --show-toplevel)")
  # 専用ブランチ kiro-flow-bus を使い、プロジェクトの main/作業ブランチには一切 push しない。
  # クローンはリポジトリ外（~/.kiro/flow-clones/<repo>）に置き、作業中のリポジトリを汚さない。
  BUS="--git $BUS_REMOTE --git-branch kiro-flow-bus --bus $HOME/.kiro/flow-clones/$REPO"
else
  BUS="--bus ./bus"                             # git 外: ローカルバス（既定。kiro-project の <root>/bus と同じ）
fi

# 2) daemon を確保（冪等 — 既に稼働していれば自動スキップ。そのまま実行してよい）
$KF $BUS daemon --max-workers 4 &

# 3) 要求を投入 → run-id 取得 → 監視 → 最終結果
RID=$($KF $BUS submit "<要求>")
$KF $BUS status --run-id "$RID" --follow --until-done
$KF $BUS result --run-id "$RID"
```

**同一バスで揃える**: `daemon`/`submit`/`status`/`result` は必ず**同じ `$BUS` フラグ**で呼ぶ
（別バスを指すと投入した要求が拾われない）。毎回フラグを渡す代わりに、リポジトリの
`./.kiro/kiro-flow.yaml` に `git` / `git_branch` / `bus` を書けば全コマンドが自動で同じバスを使う
（優先順位 CLI > 設定ファイル > 既定）。git モードは初回に `kiro-flow-bus` ブランチを origin へ
作成・push する（main は変更しない）。

### 分散（複数 PC）

上の手順で git リポジトリ内なら自動で GitHub 共有バスになる。**他の PC は同じリポジトリで
同じ手順を実行するだけ**で同一バスに参加する。リポジトリ外から明示的に指定する場合:

```bash
$KF --git <repo-url> --git-branch kiro-flow-bus daemon --max-workers 4 &   # PC ごとに（冪等）
$KF --git <repo-url> --git-branch kiro-flow-bus submit "<要求>"
```

### 状態確認・掃除

```bash
$KF status --run-id <run-id>      # 公式風ダッシュボード（進捗バー/エージェント状態/アクティビティ）
$KF status --follow               # ライブ監視（tmux ペイン向け）
$KF status --list                 # run 一覧
$KF gc --older-than 7 --status done --dry-run   # 古い done を掃除（まず dry-run）
```

### 最終結果の提示

このスキルは**ワークフローの最終結果を提示する役割**も担う。状態確認を求められて
（「結果を見せて」「終わった？」「成果物は？」等）対象 run が**完了している**なら、
進捗ダッシュボードだけでなく**最終結果そのもの**を探し出して提示する。

```bash
$KF result                        # 最新 run の最終結果（run_id 省略で自動選択）
$KF result --run-id <run-id>      # 指定 run の最終結果
$KF result --json                 # 機械可読（final_nodes に sink ノードの全文 output/data）
```

- 「最終結果」＝集約／末端（sink）ノードの**全文出力**を自動特定して返す。
  集約 kind（synthesize / reduce / judge / filter）があればそれを優先し、無ければ
  他から依存されない末端ノードを採る（例: fan-out→`synth`、tournament→`judge`、
  map-reduce→`reduce`）。`status` の `├─ result` 欄にも要約が出るが、**全文は `result`**。
- 未完了の run に対しては「まだ完了していません（X/Y 完了）」と知らせ、`status --follow`
  を案内する。確定済みの成果があれば参考として表示する。

判断指針: 進捗・経過を知りたい → `status`。出来上がった成果が欲しい → `result`。
完了済み run について「結果」を問われたら `result` を使う。

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
