---
name: gitlab-efficiency-metrics
description: GitLab のイシュー・MR からエージェント活用による効率（コスト削減）を定量化する。成果物バイト数・投稿量・差し戻し/破棄回数・推定レビュー時間を収集し、人手とエージェントのコストを比較して削減額・削減率を算出する。リポジトリ別・ユーザー別・AI/人別の内訳を出せる。「エージェントの効率を測って」「どれだけコスト削減できた？」「GitLab の生産性メトリクス/ROI を出して」で発動する。
metadata:
  version: 1.1.0
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
**リポジトリ別・ユーザー別・AI/人別** の軸で内訳を出し、**状態ファイル**で増分集計・差分・再解析ができる。

中核は決定論的な収集・算出スクリプト `scripts/efficiency.py`。収集と計算はスクリプトが行い、
エージェントは前提値の確認・結果の解釈・レポート提示を担う。

## 前提条件

- Python 3.8+（stdlib のみ）
- GitLab パーソナルアクセストークン（`read_api` で十分）→ `GITLAB_TOKEN` 環境変数 または connections.yaml
- **同じ skills ディレクトリに `gitlab-idd` スキルが必要**（GitLab 接続・認証の `gl.py` を再利用する）
- 対象は git remote origin / connections.yaml から解決する。複数リポジトリも指定可能

接続が未設定の場合は gitlab-idd スキルの `configure` で設定する。

## 何を測るか（生メトリクス）

| メトリクス | 定義 | GitLab ソース |
|-----------|------|--------------|
| **成果物バイト数** `deliverable_bytes` | マージ済み MR の差分で追加された行のバイト数 | MR `changes` の diff |
| **投稿バイト数** `agent_comment_bytes` | イシュー・MR に投稿されたコメントのバイト数 | issue/MR `notes`（system 除外） |
| **差し戻し回数** `rework_count` | イシューの reopen + `status:needs-rework` ラベル付与の回数 | `resource_state_events` / `resource_label_events` |
| **破棄した MR** `discarded_mr_count` | マージされずにクローズされた MR の数 | MR state=closed かつ merged_at なし |
| **推定レビュー時間** `review_minutes_est` | マージ済み MR の変更量から見積もったレビュー時間 | MR の追加バイト数 × レビュー速度 |

## 3 つの軸と AI/人 の判定

各メトリクスは **(リポジトリ, ユーザー, 種別=ai/human)** のセルに割り当てられ、以下に集計される:

- **`totals`**: 全体。`by_kind`（ai / human）の内訳付き。コスト削減推定は **ai 種別** に対して計算する
- **`by_repo`**: リポジトリ別（`--axis` に `repo`）
- **`by_user`**: ユーザー別（`--axis` に `user`）。差し戻しはユーザー帰属しないため `by_user` の推定には含めない

**AI / 人の判定**は投稿・MR ごとに行う（アカウントを共有していても判別できる）:

1. 本文に **AI マーカー** が含まれれば ai（既定: gitlab-idd の `worker-node-id` 等のマーカー、`Co-Authored-By: Claude`、`🤖 Generated with` など）
2. または作成者が **`--agent-users`** 集合（既定は認証ユーザー）に含まれれば ai
3. それ以外は human

マーカーは `--ai-markers`（`||` 区切りの正規表現）で置換、対象ユーザーは `--agent-users`（カンマ区切り、空文字でマーカー判定のみ）で変更する。

## ワークフロー

### Step 1: 前提を確認する

コスト推定は前提値に強く依存する。実行前に最低限これらをユーザーに確認するか、既定値で進めてよいか合意する:

- **対象期間**（既定: 直近 30 日。状態ファイルがあれば前回の続きから増分）
- **エージェント判定**（AI マーカー＋`--agent-users`。アカウント共有なら特に重要）
- **対象リポジトリ**（単一 / `--projects` / `--conn-labels`）
- **人件費**（既定 6,000 JPY/h）と**実装スループット**（LOC/人日）

前提の意味は [references/cost-model.md](references/cost-model.md) を参照する。

### Step 2: 収集・算出を実行する

```bash
# 直近 30 日・既定前提・Markdown レポート（サブコマンド collect は省略可）
python scripts/efficiency.py collect --days 30 --format markdown

# 複数リポジトリ（同一接続のホスト上）／軸を限定
python scripts/efficiency.py collect --projects ns/a,ns/b --axis repo,user

# 複数インスタンス（connections.yaml のラベル）
python scripts/efficiency.py collect --conn-labels default,work --format markdown

# AI 判定ユーザーを明示（アカウント共有時）
python scripts/efficiency.py collect --days 30 --agent-users bot-node-a,bot-node-b

# 前提を組織値で上書き
python scripts/efficiency.py collect --days 30 --params-file my_params.json

# 機械処理用に単一フィールドだけ取り出す
python scripts/efficiency.py collect --days 30 --get totals.estimate.savings.mid
```

> `python` は環境に応じて `python3` / `py` に読み替える。収集の内訳は `--verbose`（stderr）。

### Step 3: 状態を記録して増分・差分・再解析する

`--state-file` でリポジトリ毎の処理済み時刻（カーソル）とラン履歴を記録する。呼び出し側が状態を持つことで、
範囲を柔軟に指定して**増分集計・差分・再解析**ができる。

```bash
# 増分: --since 未指定かつカーソルがあれば前回の続きから集計し、状態を更新
python scripts/efficiency.py collect --state-file s.json --save-state --format markdown

# ラン履歴を一覧
python scripts/efficiency.py history --state-file s.json

# 直近 2 ランの差分（AI メトリクス・削減額の delta）
python scripts/efficiency.py diff --state-file s.json
# 任意のランを比較
python scripts/efficiency.py diff --state-file s.json --a 0 --b -1

# 再解析: 範囲を明示すればカーソルを無視して任意期間を再計算（--save-state を付けなければ状態は不変）
python scripts/efficiency.py collect --since 2026-05-01 --until 2026-06-01 --state-file s.json
```

`--since` を明示した場合はカーソルより優先される（＝再解析）。`--save-state` を付けたときだけ状態を更新する。

### Step 4: 結果を提示・解釈する

`--format markdown` がそのままレポートになる（全体サマリ＋AI/人の生メトリクス＋リポジトリ別＋ユーザー別）。提示時は必ず:

- **削減はレンジで示す**（楽観／最頻／悲観）。単一値を断定しない
- **前提を明記する**（人件費・スループット・レビュー速度・AI 判定方法）
- **削減率の母数**は「人手のみで作った場合の総コスト」、推定は **ai 種別** に対するもの
- 異常値（成果物バイト数が極端に大きい等）は原因を指摘する（生成物・vendor・大量自動生成の混入など）

## 出力形式（JSON, collect）

```json
{
  "window": { "since": "...", "until": "..." },
  "repos": ["ns/a", "ns/b"],
  "classification": { "agent_users": ["..."], "ai_markers": ["..."] },
  "totals": {
    "raw_metrics": { "deliverable_bytes": 0, "...": 0 },
    "by_kind": { "ai": { "...": 0 }, "human": { "...": 0 } },
    "estimate": {
      "currency": "JPY",
      "savings": { "low": 0, "mid": 0, "high": 0 },
      "savings_pct": { "low": 0, "mid": 0, "high": 0 },
      "human_hours_saved": { "low": 0, "mid": 0, "high": 0 },
      "scenarios": { "optimistic": {}, "most_likely": {}, "pessimistic": {} }
    }
  },
  "by_repo": { "ns/a": { "raw_metrics": {}, "by_kind": {}, "estimate": {} } },
  "by_user": { "alice": { "kinds": ["ai"], "raw_metrics": {}, "by_kind": {}, "estimate": {} } }
}
```

`savings.low`（楽観）は人の生産性が高い前提＝削減が小さい側、`savings.high`（悲観）は削減が大きい側。

## ガードレール

| 制限 | 内容 |
|------|------|
| 前提依存の明示 | コストは前提値次第。既定値のまま断定せず、前提を併記して「前提を変えれば再計算可能」と伝える |
| レンジで示す | 単一値の削減額だけを提示しない。最頻＋楽観/悲観を出す |
| 読み取り専用 | このスキルはデータ収集のみ。イシュー・MR の変更や投稿は行わない（状態ファイルへの書き込みは除く） |
| バイト数の解釈 | `deliverable_bytes` は追加行の総バイト数であり「価値」ではない。自動生成物の混入に注意する |
| AI 判定の限界 | マーカーが無い AI 投稿は username 集合に頼る。判定方法を結果に明記する |
| 因果の断定回避 | 差し戻し・破棄が多い＝悪、と短絡しない。学習・探索の結果であり得る点を添える |

## 行動指針

1. **収集はスクリプトに任せる** — 生メトリクスの算出は `efficiency.py` が決定論的に行う。手作業で API を叩き直さない
2. **前提を先に握る** — 期間・AI 判定・対象リポジトリ・人件費を確認し、合意した前提を結果に明記する
3. **削減の母数を明確に** — 削減率は「人手のみ delivery の総コスト」に対する比率で、推定は ai 種別が対象
4. **レビューは共通費** — 人手シナリオでもエージェントシナリオでも人のレビューは必要。削減は主に「実装工数の置き換え」から生じる（[references/cost-model.md](references/cost-model.md)）
5. **状態で増分・再解析** — 継続運用では `--state-file --save-state` で増分集計し、`diff` で期間比較する。再解析は `--since/--until` を明示する
6. **異常値を疑う** — 桁外れの成果物バイト数や削減率は生成物混入や前提のズレを示唆する。鵜呑みにせず原因を添える
