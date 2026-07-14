# docs/designs 設計書 索引

`docs/designs/` 配下の設計書 23 件をカテゴリ別に一意分類し、読む順序を定めた索引。分散ワークフロー（t1〜t4）による棚卸しと gate タスクの突合せ結果に基づく。

## まず読むもの — 主要 4 設計

エンジン（agent-project / agent-flow）、ドキュメント一貫性ゲート（codd-gate）、名称移行方針（agent-tools-rename）の4件が全体の骨格。ここから読み始めるとコードベース全体の見取り図が掴める。

1. [`agent-project-design.md`](./agent-project-design.md) — 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層の設計正典。3層2ループ構成（project 上位ループ／run 正準ループ／agent-flow 実行層）を地図として示す。
2. [`agent-flow-design.md`](./agent-flow-design.md) — git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。
3. [`codd-gate-design.md`](./codd-gate-design.md) — ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツールの設計正典。agent-project に依存しない独立ツールで、結合点は `schemas/` の共通データ契約のみ。
4. [`agent-tools-rename-design.md`](./agent-tools-rename-design.md) — 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard の移行は完了、`kiro-loop → agent-loop` の移行のみ未了で、現行の指針であり続けている（詳細は次節「ループ拡張」の注記）。

> **補足**: agent-dashboard（フロントエンド）の設計書は本ディレクトリには無く、`docs/plans/2026-07-14-agent-dashboard-*-design.md` 等に分散している（棚卸し中に判明。本 README のスコープ外のためリンクは追加していない）。

---

## カテゴリ別索引（全 23 件）

### 1. 主要 4 設計

| ファイル | 要旨 |
|---|---|
| [`agent-project-design.md`](./agent-project-design.md) | 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層の唯一の設計正典。 |
| [`agent-flow-design.md`](./agent-flow-design.md) | git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。 |
| [`codd-gate-design.md`](./codd-gate-design.md) | ドキュメント・コード・テストの一貫性を維持する決定的ツールの設計正典。結合点は `schemas/` の共通データ契約のみ。 |
| [`agent-tools-rename-design.md`](./agent-tools-rename-design.md) | 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針。project/flow/dashboard は移行完了、`kiro-loop` のみ未了で残置。 |

### 2. ループ拡張（kiro-loop / agent-loop）

> **kiro-loop 系 / agent-loop 系の重複について**
> adaptive-interval・agent-messaging・event-hook・gitlab-webhook の 4 件で同名の設計が並存する。`agent-loop-*` は各ファイル冒頭で「`kiro-loop-*` を置換せずクローンし改称した」と自己申告しており、[`agent-tools-rename-design.md`](./agent-tools-rename-design.md) 自身も本文中で kiro-loop の移行・削除を非目標と明記している。実装側も `tools/kiro-loop/kiro-loop.py` が数ヶ月かけて有機的に開発された一方、`tools/agent-loop/` は単一コミットでの機械的クローンであることが裏付けとなる。**現行は `kiro-loop-*-design.md`**、`agent-loop-*-design.md` は将来 agent-loop へ本移行する際の未統合クローンとして参考掲載する。

| ファイル | 要旨 |
|---|---|
| [`kiro-loop-adaptive-interval-design.md`](./kiro-loop-adaptive-interval-design.md) | kiro-loop の固定インターバル方式が抱える「活発時の反応遅延」「無風時の API 浪費」を解消する動的インターバル方式の設計案。 |
| [`agent-loop-adaptive-interval-design.md`](./agent-loop-adaptive-interval-design.md)（clone, 未統合） | 上記の複製。用語置換のみで本文は実質同一。 |
| [`kiro-loop-agent-messaging-design.md`](./kiro-loop-agent-messaging-design.md) | kiro-loop を使ったエージェント間非同期メッセージング設計。エージェントごとの inbox に他エージェントがメッセージを投函し、kiro-cli へのプロンプトとして処理する仕組み。 |
| [`agent-loop-agent-messaging-design.md`](./agent-loop-agent-messaging-design.md)（clone, 未統合） | 上記の複製。用語置換のみで本文は実質同一。 |
| [`kiro-loop-event-hook-design.md`](./kiro-loop-event-hook-design.md) | kiro-loop のイベントフック拡張（`check()` フック）設計案。フォールバック機能・同梱フック例（GitLab issue/MR hook）を実装メモとして追記済み。 |
| [`agent-loop-event-hook-design.md`](./agent-loop-event-hook-design.md)（clone, 未統合） | 上記の複製。環境変数名が `AGENT_LOOP_*` に置換されている以外は同一内容。 |
| [`kiro-loop-gitlab-webhook-design.md`](./kiro-loop-gitlab-webhook-design.md) | kiro-loop 向け汎用 inbound Webhook 設計案（具体例 GitLab）。`WebhookServer` 追加や `PeriodicScheduler` 拡張など実装済み確定事項を記載。 |
| [`agent-loop-gitlab-webhook-design.md`](./agent-loop-gitlab-webhook-design.md)（clone, 未統合） | 上記の複製。用語置換のみで本文は実質同一。 |

### 3. 実装・運用設計（外部連携・インフラ・実行基盤）

| ファイル | 要旨 |
|---|---|
| [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md) | agent-project/agent-flow の LLM 実行 CLI をプラグイン化しデータ契約のみで拡張可能にし、失敗を quota/auth/env/transient で決定的にトリアージする設計。 |
| [`agent-flow-retry-inheritance-design.md`](./agent-flow-retry-inheritance-design.md) | agent-flow のリトライ時に先行 run の結果・成果物・作業ブランチを再利用し先行 run を安全削除する設計。 |
| [`git-gitlab-circuit-breaker-pattern.md`](./git-gitlab-circuit-breaker-pattern.md) | git/GitLab へアクセスする任意ツール向けの汎用サーキットブレーカー＋監視パターン。 |
| [`git-worktree-cache-pattern.md`](./git-worktree-cache-pattern.md) | 同一 remote を繰り返し clone するツール向けに共有 bare ミラー＋使い捨て worktree へ置換する汎用パターン。 |
| [`gitlab-agent-sns-design.md`](./gitlab-agent-sns-design.md) | GitLab Issue＋Moltbook リポジトリでエージェント向け SNS を構築する moltbook-use の確定版設計。 |
| [`node-federation-design.md`](./node-federation-design.md) | ノードのローカル改善を中央へ集約する pull/push 設計。自ら実装済み・仕様リファレンスと明記。 |
| [`plan-a-local-gitlab-design.md`](./plan-a-local-gitlab-design.md) | ローカル GitLab CE 作業インスタンス（案A）の設計・運用正典。 |

### 4. 歴史的・比較検討

| ファイル | 要旨 |
|---|---|
| [`ltm-use-v4-design.md`](./ltm-use-v4-design.md) | 類似記憶検知・ハイブリッド recall・自動タグ付けの提案設計（Draft のまま）。後継 `ltm-use-v5-brain-design.md` に前提として引き継がれた。 |
| [`ltm-use-v5-brain-design.md`](./ltm-use-v5-brain-design.md) | 脳構造になぞらえた記憶固定化・context-aware recall の提案設計（Draft のまま、前提: ltm-use v4.0.0）。 |
| [`selfhost-forge-comparison.md`](./selfhost-forge-comparison.md) | セルフホスト構成 5 案（A〜E）を比較した資料。主推奨は案C（コードのみローカル、issues/MR は上流のまま）、issues/MR を必ずローカルに置く要件がある場合の代替は案A。実採用は案A（`plan-a-local-gitlab-design.md`）で、本書はその意思決定に至った経緯記録。 |
| [`gitea-gitlab-sync-design.md`](./gitea-gitlab-sync-design.md) | LAN 内 Gitea で Issue/MR 管理、コードは GitLab と双方向同期する設計正典（`selfhost-forge-comparison.md` の案Bに相当、実装は未着手と本文に明記）。比較の結果、実採用は案A（plan-a 側）で本案は不採用。 |

---

## 突合せで判明した矛盾の解消（記録）

分散ワークフロー各タスク（t1〜t4）の棚卸し結果を突合せた際、以下の不一致を確認し解消した。

- **`codd-gate-design.md`**: t1（エンジン中核）・t2（ループ拡張）の双方に計上されていた。gate 裁定により「主要4設計」に一本化した。
- **`agent-tools-rename-design.md`**: t1「現行」・t4「歴史的（経緯記録）」で判定が割れた。移行対象4系統のうち `kiro-loop → agent-loop` が本文の明記どおり未完了であり、本書は移行方針として引き続き参照される現行文書と判断し、t1・gate 裁定（主要4設計＝現行）を採用した。
- **`gitea-gitlab-sync-design.md`**: t3「現行」・t4「歴史的（不採用）」で判定が割れた。本文冒頭に「実装は未着手」と明記があり、`selfhost-forge-comparison.md`（案B相当）が最終的に不採用・案Cを推奨した経緯を実読で確認したため、t4・gate 裁定（歴史的・比較検討）を採用した。
- **`selfhost-forge-comparison.md`**: t3 は「案A/B/C の3案比較・案A推奨」と要約したが、実ファイルを確認したところ実際は**5案（A〜E）の比較**で**主推奨は案C**（issues/MR を必ずローカルに置く要件がある場合のみ案A）だった。t3 の要約誤りを本 README 作成時に訂正し、t4 の分類（歴史的・比較検討）と合わせて反映した。
- **`ltm-use-v4-design.md`**: t3・t4 とも「歴史的」で一致（後継 `ltm-use-v5-brain-design.md` に引き継ぎ済み）。
- **kiro-loop-\* / agent-loop-\* の4組**: 上記「ループ拡張」節の注記のとおり、両系統を並記したうえで現行は `kiro-loop-*` と明記した。

## 前提・スコープ外の事項

- 本 README は `docs/designs/` 配下の実ファイル一覧（23件、gate 突合せで実在確認・漏れ/幽霊ファイルなしを確認済み）を基準に作成した。
- agent-dashboard の主要設計書は本ディレクトリに存在しないため未掲載（`docs/plans/` 側に分散、t1 の申し送り事項）。本 README のスコープ外のため、リンクの追加は行っていない。
