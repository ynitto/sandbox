# kiro-flow

kiro-cli で **Claude 風の Dynamic Workflow**（動的にタスクを分解 → ワーカーへ委譲 → 結果統合）を
実現する基盤。通信は **ファイルのみ**で行い、バスを git に差し替えれば**複数 PC へ分散**できる設計。

> **現状: M1（最小実行）**
> メッセージバスはローカルディレクトリ。`claim` プロトコルと最小ワーカーループの正しさを検証する段階。
> git バス（複数 PC 分散）は同じ `Bus` インターフェースの差し替えで M2 として追加予定。

## できること（M1）

- `up` **一発**で orchestrator ×1 ＋ worker ×N を起動して待機。run 完了で自動停止、Ctrl-C で全停止。
- 要求を独立タスクに分解し、複数ワーカーが**競合せず** claim して並列実行。
- LLM は **kiro-cli** がデフォルト。kiro-cli 無しでも動く **stub** モードでプロトコル検証可能。

## 設計の肝 — 衝突しない通信

タスクの状態は**ファイルの存在**から導出するため、ノードが同じファイルを書き換えることがない。

| 状態 | 条件 |
|------|------|
| pending | `tasks/<id>.json` があり、`claims/<id>.lock` も `results/<id>.json` も無い |
| claimed | `claims/<id>.lock` がある |
| done / failed | `results/<id>.json` があり `status` がそれ |

claim は `claims/<id>.lock` を `O_CREAT\|O_EXCL` で作る＝ファイルシステムの原子操作。最初に作れた
ワーカーだけが勝者。git バスでは push 拒否（non-fast-forward）を同じ mutex として使う。

```
<bus>/runs/<run-id>/
  meta.json          # 要求・status（planning/running/done）
  graph.json         # タスクグラフ（orchestrator のみ書く）
  tasks/<id>.json    # タスク仕様
  claims/<id>.lock   # 取得マーカー（原子操作）
  results/<id>.json  # 成果（claim 成功者のみ書く）
  events/<who>.jsonl # 追記専用ログ（各ノードが自分のファイルだけ）
  final.json         # 統合結果
```

## 使い方

```bash
# kiro-cli 無しでプロトコルを確認（推奨: まずこれ）
python3 kiro-flow.py --bus /tmp/flowbus up \
  "要件を整理する; APIを設計する; テストを書く; READMEを書く" \
  --workers 3 --planner stub --executor stub --poll 0.5

# kiro-cli を使った実運用（既定）
python3 kiro-flow.py up "<要求>" --workers 3

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
| `--bus` | `./.kiro-flow` | メッセージバスのルート |
| `--workers` | 2 | 起動するワーカー数（`up`） |
| `--planner` / `--executor` | `kiro` | `kiro`（kiro-cli）/ `stub`（オフライン検証） |
| `--poll` | 2.0 | ポーリング間隔（秒） |
| `--keep-alive` | off | run 完了後もワーカーを待機させる（`work`） |

## 依存

- Python 3.9+（標準ライブラリのみ）
- 実運用では `kiro-cli`（`--planner kiro` / `--executor kiro`）

## ロードマップ

- **M1（本実装）**: ローカルバス・claim プロトコル・一発起動。✅
- **M2**: git バス（push 拒否を mutex に）で複数 PC 分散。lease 切れ孤児の回収強化。
- **M3**: 結果評価に基づく**再計画ループ**（evaluator-optimizer）。`resume`（中断再開）。
- **M4**: tmux 可視化・`gc`（古い run 掃除）・障害注入テスト。

## 既存ツールとの関係

| ツール | 構造 | 決定タイミング |
|--------|------|--------------|
| `kiro-loop` | 定期プロンプト送信 | 静的 |
| `multi-agent-shogun-kiro` | 将軍/家老/足軽の固定階層 | 静的 |
| **`kiro-flow`** | **タスクグラフ** | **実行時に LLM が生成** |

`git-file-sync`（git をハブにした同期）と `gitlab-idd`（キューからの claim→実行→報告）の発想を、
タスクグラフの動的生成に組み合わせたもの。
