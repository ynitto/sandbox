# docs/designs 設計書 索引

`docs/designs/` 配下の設計書 25 件をカテゴリ別に整理し、読む順序を示す索引。

## まず読むもの — 主要 4 設計

エンジン（agent-project / agent-flow）、ドキュメント一貫性ゲート（codd-gate）、名称移行方針（agent-tools-rename）の4件が全体の骨格。ここから読み始めるとコードベース全体の見取り図が掴める。基本の読む順序は 1 → 2 → 3（制御層 → 実行層 → 品質ゲートの順に責務が積み上がる）。`kiro-*` と `agent-*` の併存に迷ったら、先に 4 を読むと名称の由来と移行状況が先にわかる。

1. [`agent-project-design.md`](./agent-project-design.md) — 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層の設計正典。3層2ループ構成（project 上位ループ／run 正準ループ／agent-flow 実行層）を地図として示す。
2. [`agent-flow-design.md`](./agent-flow-design.md) — git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。
3. [`codd-gate-design.md`](./codd-gate-design.md) — ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツールの設計正典。agent-project 本体は無改造のまま、`schemas/` の共通データ契約と agent-project 側の汎用フック契約（E1〜E3）の2点で連携する独立ツール。
4. [`agent-tools-rename-design.md`](./agent-tools-rename-design.md) — 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard の移行は完了、`kiro-loop → agent-loop` の移行のみ未了で、現行の指針であり続けている（詳細は次節「ループ拡張」の注記）。

> **補足**: agent-dashboard の画面設計は主に `docs/plans/2026-07-14-agent-dashboard-*-design.md` 等に分散。本ディレクトリには制御面分離の正典 [`agent-dashboard-feature-split-design.md`](./agent-dashboard-feature-split-design.md) を置く。

---

## カテゴリ別索引（全 25 件）

### 1. 主要 4 設計

詳細な要旨は前掲「まず読むもの」を参照。[`agent-project-design.md`](./agent-project-design.md) ・ [`agent-flow-design.md`](./agent-flow-design.md) ・ [`codd-gate-design.md`](./codd-gate-design.md) ・ [`agent-tools-rename-design.md`](./agent-tools-rename-design.md)

### 2. ループ拡張（kiro-loop / agent-loop）

> **kiro-loop 系 / agent-loop 系の重複について**
> adaptive-interval・agent-messaging・event-hook・gitlab-webhook の 4 件は、kiro-loop 系と agent-loop 系で同名の設計が並存する。`agent-loop-*` は各ファイル冒頭で「`kiro-loop-*` をクローンし改称した」と自己申告しており、`kiro-loop → agent-loop` の移行が未完了であることは [`agent-tools-rename-design.md`](./agent-tools-rename-design.md) 本文でも明記されている。**現行は `kiro-loop-*-design.md`**、`agent-loop-*-design.md` は移行完了後に本統合される未統合クローンとして参考掲載する。

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
| [`agent-amigos-design.md`](./agent-amigos-design.md) | 役割ミッション表と design doc で公示したミッションに分散ノードがロールを claim して参加し、オーナーが指示した収束条件・予算（実質実行時間）の範囲で型付きメッセージで相互協働しながら 1 つの成果物をオーナーへ納品する協働基盤の設計（Draft、実装未着手）。1 ノードでも自己補充で完結し、中央は専用バスリポジトリ（ミッション別ブランチ、state_git の同期規律を流用）または任意の hub（転送のみ）。定時シャットダウンには away プロトコルとターン原子性で耐える。 |
| [`agent-dashboard-feature-split-design.md`](./agent-dashboard-feature-split-design.md) | agent-dashboard を base / agent-project / kiro-loop にソース分離し、フルプラグインなしで他グループが kiro-loop 制御面を差し込めるようにする設計。 |
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

## 前提・スコープ外の事項

本 README は `docs/designs/` 配下の実ファイル一覧（25件、実在確認・漏れ/幽霊ファイルなしを確認済み）を基準に作成した。
