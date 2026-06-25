---
name: gitlab-efficiency-metrics
description: GitLab のイシュー・MR からエージェント活用による効率（コスト削減）を定量化する。成果物バイト数・エージェント投稿量・差し戻し/破棄回数・推定レビュー時間を収集し、人手のみとエージェント利用のコストを比較して削減額・削減率をレンジで算出する。「エージェントの効率を測って」「どれだけコスト削減できた？」「GitLab の生産性メトリクス/ROI を出して」で発動する。
metadata:
  version: 1.0.0
  tier: experimental
  category: analytics
  tags:
    - gitlab
    - metrics
    - efficiency
    - cost-savings
    - roi
---

# gitlab-efficiency-metrics — GitLab 効率性メトリクス

GitLab のイシュー・MR の活動量を収集し、**エージェントを使ったことで人のコストをどれだけ削減できたか** を推定する。

中核は決定論的な収集・算出スクリプト `scripts/efficiency.py`。生メトリクスの収集とコスト計算はスクリプトが行い、
エージェントは前提値の確認・結果の解釈・レポート提示を担う。

## 前提条件

- Python 3.8+（stdlib のみ）
- GitLab パーソナルアクセストークン（`read_api` で十分）→ `GITLAB_TOKEN` 環境変数 または connections.yaml
- **同じ skills ディレクトリに `gitlab-idd` スキルが必要**（GitLab 接続・認証の `gl.py` を再利用する）
- 集計はリポジトリの `git remote origin` または connections.yaml からプロジェクトを解決する

接続が未設定の場合は gitlab-idd スキルの `configure` で設定する。

## 何を測るか（生メトリクス）

| メトリクス | 定義 | GitLab ソース |
|-----------|------|--------------|
| **成果物バイト数** `deliverable_bytes` | マージ済み MR の差分で追加された行のバイト数 | MR `changes` の diff |
| **エージェント投稿バイト数** `agent_comment_bytes` | イシュー・MR にエージェントが投稿したコメントのバイト数 | issue/MR `notes`（system 除外・author で判定） |
| **差し戻し回数** `rework_count` | イシューの reopen + `status:needs-rework` ラベル付与の回数 | `resource_state_events` / `resource_label_events` |
| **破棄した MR** `discarded_mr_count` | マージされずにクローズされた MR の数 | MR state=closed かつ merged_at なし |
| **推定レビュー時間** `review_minutes_est` | マージ済み MR の変更量から見積もったレビュー時間 | MR の追加バイト数 × レビュー速度 |

「エージェント」は GitLab username の集合で判定する（既定は認証ユーザー。複数ノードが同一アカウントを共有する
gitlab-idd 運用では `--agent-users` で明示する）。

## ワークフロー

### Step 1: 前提を確認する

コスト推定は前提値に強く依存する。実行前に最低限これらをユーザーに確認するか、既定値で進めてよいか合意する:

- **対象期間**（既定: 直近 30 日）
- **エージェントの username**（既定: 認証ユーザー）
- **人件費**（既定: 6,000 JPY/h）と**実装スループット**（LOC/人日）

既定値は概算であり、組織の実績に合わせるほど推定精度が上がる。前提の意味は [references/cost-model.md](references/cost-model.md) を参照する。

### Step 2: 収集・算出を実行する

```bash
# 直近 30 日・既定前提・Markdown レポート
python scripts/efficiency.py --days 30 --format markdown

# 期間とエージェントユーザーを指定
python scripts/efficiency.py --since 2026-05-01 --until 2026-06-01 \
  --agent-users node-a,node-b --format markdown

# 前提を組織値で上書き（JSON）
python scripts/efficiency.py --days 30 --params-file my_params.json --format markdown

# 機械処理用に単一フィールドだけ取り出す
python scripts/efficiency.py --days 30 --get estimate.savings.mid
```

> `python` は環境に応じて `python3` / `py` に読み替える。収集の内訳を見るには `--verbose`（stderr）。

前提を上書きする `my_params.json` の例（必要なキーだけでよい）:

```json
{
  "human_hourly_rate": 8000,
  "write_loc_per_day_mid": 50,
  "agent_cost_override": 12000
}
```

全パラメータと既定値は [references/cost-model.md](references/cost-model.md) を参照する。

### Step 3: 結果を提示・解釈する

`--format markdown` がそのままレポートになる。ユーザーへ提示する際は次を必ず添える:

- **削減はレンジで示す**（楽観／最頻／悲観）。単一値だけを断定しない
- **前提を明記する**（人件費・スループット・レビュー速度）。前提が変われば結論も変わる
- **削減率の母数**は「人手のみで作った場合の総コスト」であること
- 異常値（成果物バイト数が極端に大きい等）があれば原因を指摘する（生成物・ベンダーコード・大量自動生成の混入など → Step 4）

### Step 4: 精度を上げる（任意）

- 自動生成ファイル・vendor・ロックファイルが `deliverable_bytes` を膨らませている場合、その MR を `--agent-users` の対象から外す、または期間を絞って影響を確認する
- 実トークン課金がある場合は `agent_cost_override` に実費を入れて計算コストを正確にする
- スループット・レビュー速度を過去スプリントの実績から逆算して `--params-file` に反映する

## 出力形式（JSON）

`--format json`（既定）は以下の構造で出力する。`raw_metrics` が生メトリクス、`estimate` が推定結果:

```json
{
  "window": { "since": "...", "until": "..." },
  "agent_users": ["..."],
  "raw_metrics": {
    "deliverable_bytes": 0, "deliverable_lines": 0, "merged_mr_count": 0,
    "discarded_mr_count": 0, "rework_count": 0,
    "agent_comment_bytes": 0, "issue_comment_bytes": 0, "mr_comment_bytes": 0,
    "review_minutes_est": 0, "reviewed_mr_count": 0
  },
  "estimate": {
    "currency": "JPY",
    "savings": { "low": 0, "mid": 0, "high": 0 },
    "savings_pct": { "low": 0, "mid": 0, "high": 0 },
    "human_hours_saved": { "low": 0, "mid": 0, "high": 0 },
    "scenarios": { "optimistic": {}, "most_likely": {}, "pessimistic": {} },
    "assumptions": {}
  }
}
```

`savings.low`（楽観）は人の生産性が高い前提＝削減が小さい側、`savings.high`（悲観）は削減が大きい側。

## ガードレール

| 制限 | 内容 |
|------|------|
| 前提依存の明示 | コストは前提値次第。既定値のまま断定せず、前提を併記して「前提を変えれば再計算可能」と伝える |
| レンジで示す | 単一値の削減額だけを提示しない。最頻＋楽観/悲観を出す |
| 読み取り専用 | このスキルはデータ収集のみ。イシュー・MR の変更や投稿は行わない |
| バイト数の解釈 | `deliverable_bytes` は追加行の総バイト数であり「価値」ではない。自動生成物の混入に注意する |
| 因果の断定回避 | 差し戻し・破棄が多い＝悪、と短絡しない。学習コストや探索の結果であり得る点を添える |

## 行動指針

1. **収集はスクリプトに任せる** — 生メトリクスの算出は `efficiency.py` が決定論的に行う。手作業で API を叩き直さない
2. **前提を先に握る** — 実行前に期間・エージェントユーザー・人件費を確認する。合意した前提を結果に明記する
3. **削減の母数を明確に** — 削減率は「人手のみ delivery の総コスト」に対する比率である
4. **レビューは共通費** — 人手シナリオでもエージェントシナリオでも人のレビューは必要。削減は主に「実装工数の置き換え」から生じる（[references/cost-model.md](references/cost-model.md)）
5. **異常値を疑う** — 桁外れの成果物バイト数や削減率は生成物混入や前提のズレを示唆する。鵜呑みにせず原因を添える
