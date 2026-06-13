# kiro-flow

kiro-cli で **Claude 風の Dynamic Workflow**（動的にタスクを分解 → ワーカーへ委譲 → 結果統合）を
実現する基盤。通信は **ファイルのみ**で行い、バスを git に差し替えれば**複数 PC へ分散**できる設計。

> **現状: M2（git 分散）**
> ローカルディレクトリバス（M1）に加え、`--git` で**共有 git リポジトリをバス**にして
> 複数 PC へ分散実行できる。claim は全転送で衝突しない方式に統一済み。

## できること

- `up` **一発**で orchestrator ×1 ＋ worker ×N を起動して待機。run 完了で自動停止、Ctrl-C で全停止。
- 要求を独立タスクに分解し、複数ワーカーが**競合せず** claim して並列実行。
- **`--git` で複数 PC 分散**：各ノードが共有リポジトリの自分専用クローンで作業し、push/pull で通信。
- LLM は **kiro-cli** がデフォルト。kiro-cli 無しでも動く **stub** モードでプロトコル検証可能。

## 設計の肝 — 衝突しない通信

タスクの状態は**ファイルの存在**から導出するため、ノードが同じファイルを書き換えることがない。

| 状態 | 条件 |
|------|------|
| pending | `tasks/<id>.json` があり、有効な claim も `results/<id>.json` も無い |
| claimed | `claims/<id>/` に lease 内の claim があり、勝者が確定している |
| done / failed | `results/<id>.json` があり `status` がそれ |

**claim — 名前空間付き claim ＋ 決定的タイブレーク**：各ワーカーは自分専用の
`claims/<id>/<who>.json` を書く（ファイル名が衝突しないので git で add/add コンフリクトに
ならない）。勝者は lease 内の全 claim のうち **`(ts, who)` が最小**の 1 件に決定的に定まる。
ローカル転送でも git 転送でも、同じロジックで唯一の勝者が決まる。クラッシュ等で放置された
claim は lease 超過で自動的に無効化され、別ノードが再 claim できる。

```
<bus>/runs/<run-id>/
  meta.json            # 要求・status（planning/running/done）
  graph.json           # タスクグラフ（orchestrator のみ書く）
  tasks/<id>.json      # タスク仕様
  claims/<id>/<who>.json  # 取得マーカー（ノードごとに名前空間化）
  results/<id>.json    # 成果（claim 成功者のみ書く）
  events/<who>.jsonl   # 追記専用ログ（各ノードが自分のファイルだけ）
  final.json           # 統合結果
```

## 使い方

```bash
# kiro-cli 無しでプロトコルを確認（推奨: まずこれ）
python3 kiro-flow.py --bus /tmp/flowbus up \
  "要件を整理する; APIを設計する; テストを書く; READMEを書く" \
  --workers 3 --planner stub --executor stub --poll 0.5

# kiro-cli を使った実運用（既定）
python3 kiro-flow.py up "<要求>" --workers 3

# 複数 PC 分散（共有 git リポジトリをバスにする）
#   各ノードは <bus>/<node-id> に自分専用クローンを作り push/pull で通信する。
#   別 PC でも同じ --git URL を指すワーカーを起動すれば同じ run に参加できる。
python3 kiro-flow.py --git git@example.com:team/flow-bus.git --git-branch main \
  up "<要求>" --workers 3
#   ローカルのベアリポジトリで動作確認:
#     git init --bare -b main /tmp/flowbus.git
#     python3 kiro-flow.py --git /tmp/flowbus.git up "A; B; C" --workers 3 \
#       --planner stub --executor stub

# 別 PC をワーカーとして後から合流（同じ run-id を指定）
python3 kiro-flow.py --git <URL> --run-id <run-id> work --keep-alive

# 状態確認
python3 kiro-flow.py --bus /tmp/flowbus --run-id <run-id> status
```

### サブコマンド

| コマンド | 役割 |
|---------|------|
| `up <要求>` | orchestrator + worker(複数) を一発起動して待機 |
| `orchestrate --request <要求>` | 計画役単体（分解 → 投入 → 完了待ち → 統合） |
| `work` | ワーカー役単体（claim → 実行 → result）。`--keep-alive` で常駐待機 |
| `status` | run の状態表示 |

### 主なオプション

| オプション | 既定 | 意味 |
|-----------|------|------|
| `--bus` | `./.kiro-flow` | ローカルバスのルート / git モードでは各ノードのクローン親 |
| `--git` | （なし） | 共有 git リポジトリ URL/パス。指定で複数 PC 分散モード |
| `--git-branch` | `main` | バスに使う git ブランチ |
| `--lease` | 1800 | claim のリース秒数（超過で他ノードが再 claim 可能） |
| `--workers` | 2 | 起動するワーカー数（`up`） |
| `--planner` / `--executor` | `kiro` | `kiro`（kiro-cli）/ `stub`（オフライン検証） |
| `--poll` | 2.0 | ポーリング間隔（秒） |
| `--keep-alive` | off | run 完了後もワーカーを待機させる（`work`） |

## 依存

- Python 3.9+（標準ライブラリのみ）
- git モードでは `git` コマンド（共有リポジトリは初期化済みであること）
- 実運用では `kiro-cli`（`--planner kiro` / `--executor kiro`）

## ロードマップ

- **M1**: ローカルバス・claim プロトコル・一発起動。✅
- **M2（本実装）**: git バスで複数 PC 分散。名前空間付き claim ＋ 決定的タイブレーク、
  push 競合の rebase リトライ、lease による孤児 claim の自動回収。✅
- **M3**: 結果評価に基づく**再計画ループ**（evaluator-optimizer）・`resume`（中断再開）・
  lease ハートビート（長時間タスクの claim 更新）・負荷分散の改善。
- **M4**: tmux 可視化・`gc`（古い run 掃除）・障害注入テスト。

## 既知の制限（M2 時点）

- **負荷分散は最適ではない**: ウォームなノードが連続して claim しやすい。実 kiro-cli は
  タスク実行に時間がかかり遅延も入るため自然に分散するが、厳密な公平分配は M3 で扱う。
- **長時間タスクと lease**: タスク実行が `--lease` を超えると別ノードが再 claim し二重実行の
  恐れ。既定 1800 秒。M3 でハートビート更新を入れる。

## 既存ツールとの関係

| ツール | 構造 | 決定タイミング |
|--------|------|--------------|
| `kiro-loop` | 定期プロンプト送信 | 静的 |
| `multi-agent-shogun-kiro` | 将軍/家老/足軽の固定階層 | 静的 |
| **`kiro-flow`** | **タスクグラフ** | **実行時に LLM が生成** |

`git-file-sync`（git をハブにした同期）と `gitlab-idd`（キューからの claim→実行→報告）の発想を、
タスクグラフの動的生成に組み合わせたもの。
