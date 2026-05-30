---
name: observability-designer
description: システムの可観測性を設計するスキル。構造化ログ・メトリクス・分散トレースの三本柱、OpenTelemetry計装、相関ID伝播、ダッシュボード・アラート設計を提案する。「ログ設計して」「メトリクスを設計して」「トレースを入れたい」「OpenTelemetryで計装して」「可観測性を上げたい」「監視を設計して」などで発動する。恒久的な観測基盤の設計を扱う。
metadata:
  version: 1.0.0
  tier: experimental
  category: operations
  tags:
    - observability
    - logging
    - metrics
    - tracing
    - opentelemetry
---

# observability-designer

「本番で何が起きているか」を後から問い合わせられる状態＝**可観測性**を設計する。場当たりの `print` ではなく、ログ・メトリクス・トレースを一貫した設計で計装する。

> `performance-profiler` は問題発生時の一回限りの計測。本スキルは平時から継続して観測できる基盤の設計を担う。

## 三本柱（Three Pillars）

| 柱 | 答える問い | 設計対象 |
|----|-----------|----------|
| ログ (Logs) | 「その時、具体的に何が起きたか」 | 構造化ログ・ログレベル・相関ID |
| メトリクス (Metrics) | 「全体の傾向・健全性はどうか」 | カウンタ/ゲージ/ヒストグラム・RED/USE |
| トレース (Traces) | 「リクエストはどこで時間を使ったか」 | スパン・伝播・サンプリング |

詳細パターンは [references/instrumentation.md](references/instrumentation.md)。

## ワークフロー

### Step 1: 現状とゴールを把握する

1. システム構成（モノリス/マイクロサービス・言語・ランタイム）と既存の監視を確認する
2. 何に困っているかを特定: 障害時に原因が追えない / 性能劣化が見えない / アラートが多すぎる/少なすぎる
3. 利用基盤を確認（Datadog / Grafana+Prometheus / CloudWatch / OpenTelemetry Collector 等）。なければベンダー中立な OpenTelemetry を基本に提案する

### Step 2: ログを設計する

- **構造化ログ（JSON）** を基本に。レベル運用（ERROR/WARN/INFO/DEBUG）の基準を定める
- 全ログに **相関ID / trace_id** を載せ、1リクエストを横断追跡できるようにする
- **何を出すか/出さないか**: イベント・判断・外部呼び出しは出す。PII は出さない（`privacy-compliance` と整合）
- ログ量とコスト・保持期間のバランスを設計する

### Step 3: メトリクスを設計する

- リクエスト系は **RED**（Rate・Errors・Duration）、リソース系は **USE**（Utilization・Saturation・Errors）
- メトリクス名・ラベル（次元）設計。カーディナリティ爆発（高基数ラベル）に注意する
- ビジネスメトリクス（注文数・決済成功率等）も技術メトリクスと並べて設計する

### Step 4: トレースを設計する

- サービス境界・外部I/O（DB・API・キュー）にスパンを張る
- **コンテキスト伝播**（W3C Trace Context 等）でサービス間を繋ぐ
- サンプリング戦略（ヘッド/テールベース）とコストのトレードオフを設計する

### Step 5: アラートとダッシュボードを設計する

- アラートは **症状ベース（SLO違反・ユーザー影響）** を優先し、原因ベースの過剰アラートを避ける
- アラート疲れを防ぐ閾値・連続条件・重大度分け。SLO 設計は `slo-designer` と連携する
- ダッシュボードは「概況→ドリルダウン」の階層で構成する

### Step 6: 計装の実装提案

OpenTelemetry を中心に、言語に応じた SDK・自動計装・Collector 構成を [references/instrumentation.md](references/instrumentation.md) のスニペットで示す。

## ガードレール

| 制限 | 内容 |
|------|------|
| PII 非出力 | ログ/スパン属性に個人情報・秘匿情報を載せない（privacy-compliance 参照） |
| コスト意識 | ログ量・高基数ラベル・トレース全量取得のコストを明示し、サンプリング/保持を設計する |
| ベンダー中立 | 特定SaaS前提を押し付けず、OpenTelemetry など移植性ある選択を基本に提案する |
| 過剰アラート防止 | アラートはユーザー影響/SLOベースを優先。ノイズになる原因アラートを乱発しない |
