# docs/designs 設計書 索引

`docs/designs/` 配下の設計書 22 件をカテゴリ別に整理し、読む順序を示す索引。

## まず読むもの — コンセプトと主要 4 設計

最初に 0 のコンセプト正典を読む。agent-* ファミリー全体が何のための道具か（チーム利用・人介在の最適化・資源効率の三本柱）と、設計をぶれさせないための原則 C1〜C10、このリポジトリでの作業ゲートがここにある。続くエンジン（agent-project / agent-flow）、ドキュメント一貫性ゲート（codd-gate）、名称移行方針（agent-tools-rename）の4件が全体の骨格。基本の読む順序は 0 → 1 → 2 → 3（コンセプト → 制御層 → 実行層 → 品質ゲートの順に責務が積み上がる）。`kiro-*` と `agent-*` の併存に迷ったら、先に 4 を読むと名称の由来と移行状況が先にわかる。

0. [`agent-tools-concept.md`](./agent-tools-concept.md) — ファミリー全体の上位文書。個人単位のクレジットを前提に実作業をチームで分担する（柱1）、人の判断を最少回数・最高品質で使い品質責任を個人に負わせない（柱2）、有限なエージェント資源をローカル LLM 含む適所適材で使い切り作業を止めない（柱3）という三本柱と、原則 C1〜C10、モジュール別の設計方針、作業時の強制ゲート（§8）を定める。対象パスの変更はこのゲートを通す。
1. [`agent-project-design.md`](./agent-project-design.md) — 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層の設計正典。done を verify の終了コードだけで確定する不変条件、常駐体と子プロセスの分担、複数 PC を git の CAS で調停する方式を扱う（旧 multi-node daemon 設計を統合済み）。
2. [`agent-flow-design.md`](./agent-flow-design.md) — git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。自己回復リトライ（4 層）とリトライ時の世代交代（`inherit_from`）も統合済み。
3. [`codd-gate-design.md`](./codd-gate-design.md) — ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツールの設計正典。agent-project 本体は無改造のまま、`schemas/` の共通データ契約と agent-project 側の汎用フック契約（E1〜E3）の2点で連携する独立ツール。
4. [`agent-tools-rename-design.md`](./agent-tools-rename-design.md) — 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard の移行は完了。`kiro-loop → agent-loop` もクローン移行済みで、ループ系の設計書は [`agent-loop-design.md`](./agent-loop-design.md) に統合済み。2026-08-08 に旧 `tools/kiro-loop/` の残置方針を撤回し、退役へ切り替えた（手順は [資源効率計画](../plans/2026-08-08-agent-tools-resource-efficiency-plan.md) の F13）。

> **補足**: agent-dashboard の画面ごとの詳細設計は `docs/plans/2026-07-1x-agent-dashboard-*-design.md` に日付つきで分散している。本ディレクトリには骨格の正典 [`agent-dashboard-design.md`](./agent-dashboard-design.md) を置く。

---

## カテゴリ別索引（全 20 件）

### 1. コンセプトと主要 4 設計

詳細な要旨は前掲「まず読むもの」を参照。[`agent-tools-concept.md`](./agent-tools-concept.md) ・ [`agent-project-design.md`](./agent-project-design.md) ・ [`agent-flow-design.md`](./agent-flow-design.md) ・ [`codd-gate-design.md`](./codd-gate-design.md) ・ [`agent-tools-rename-design.md`](./agent-tools-rename-design.md)

### 2. ループ拡張（agent-loop / kiro-loop）

> かつて adaptive-interval・agent-messaging・event-hook・gitlab-webhook の 4 件は kiro-loop 系と agent-loop 系で同名の設計が並存していたが、2026-08-06 に 8 文書すべてを [`agent-loop-design.md`](./agent-loop-design.md) へ統合し削除した。2026-08-09 には `slash` プロパティ設計も同書の機能 6 へ統合した。名称は移行先の `agent-loop` に統一、kiro-loop 系統との差分は同書の付録 B にある。

| ファイル | 要旨 |
|---|---|
| [`agent-loop-design.md`](./agent-loop-design.md) | agent-loop（旧 kiro-loop）の設計正典。イベントフック・Webhook・メッセージング・動的インターバル・CLI 差し替え・`slash` と、共通 dispatch gate 上の Phase 1 / Phase 2 実行基盤を扱う。旧ループ拡張 8 文書と `slash` 設計を統合済み。 |

### 3. 実装・運用設計（外部連携・インフラ・実行基盤）

| ファイル | 要旨 |
|---|---|
| [`agent-amigos-design.md`](./agent-amigos-design.md) | 役割ミッション表と design doc で公示したミッションに分散ノードがロールを claim して参加し、オーナーが指示した収束条件と予算（実質実行時間）の範囲で型付きメッセージをやり取りしながら 1 つの成果物をオーナーへ納品する協働基盤の設計正典。`tools/agent-amigos/` に実装済みで、残る欠落は同書 §9 に明記。中央は専用バスリポジトリ（ミッション別ブランチ、state_git の同期規律を流用）で、転送だけを担い調整はしない。1 ノードでも自己補充で完結し、定時シャットダウンには away プロトコルとターン原子性で耐える。チーム設計の自動化（team-builder）とオーケストレーションパターンの写像（旧 `agent-amigos-teambuilder-patterns.md`）も統合済み。 |
| [`agent-dashboard-design.md`](./agent-dashboard-design.md) | 別ホストで動く agent-project / agent-flow / agent-amigos / 定常業務を 1 つの Windows GUI から見渡し、人の判断だけを公式契約で返す操作面の設計正典。dashboard は状態の書き手にならない（読むのはファイル、書くのは `commands/` 等の投函だけ）・制御面はソースツリー分離と列挙合成・プロジェクトの発見は実行側の `engine/status.json` 1 枚、の 3 点が骨格。旧 `agent-dashboard-{feature-split,kiro-loop-terminal,project-ux-improvements}` を統合済み。 |
| [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md) | エージェント CLI の呼び出しを「CLI × モード（ヘッドレス/対話）× 権限（書き込み可/読み取り専用）」のデータ契約 `agents/<name>.json` で差し替える設計正典。同梱 9 定義、相対コスト、JSON 変種、実測 usage、セッションログ、待機判定と、quota/auth/env/transient の決定的トリアージを扱う。Python ローダは agentcore の 1 実装を共有する。 |
| [`agent-audit-design.md`](./agent-audit-design.md) | agent-project / agent-flow / agent-amigos / agent-loop の実行証跡とエージェント CLI 自身のセッションログを読み取り専用で収集・正規化し、トークン使用量の実測集計と知見・スキル改善点の蒸留を行う独立 CLI の設計正典。集計・相関・レポートは決定的（LLM 不使用）、LLM は extract（map・弱モデル可）/ distill（reduce）の 2 段に限定して段別にエージェント・モデルを選択できる。エンジン無改造・CLI 単独利用でも成立し、洞察は task.schema.json 形で agent-project の汎用 intake へ渡す。 |
| [`agent-ollama-design.md`](./agent-ollama-design.md) | Ollama のローカル推論をバックアップ兼恒常的な節約先として agent-* ファミリーへ接続する設計正典。plain / bash / read / TUI、JSON 文法制約、think、明示スキル、進捗 JSONL、無進捗監視、文脈実測、未完了契約を扱う。適用段 0〜3 は実装済み、edit / patch と走行中の ToolPolicy は着手条件つきで未実装。 |
| [`git-gitlab-circuit-breaker-pattern.md`](./git-gitlab-circuit-breaker-pattern.md) | git/GitLab へアクセスする任意ツール向けの汎用サーキットブレーカー＋監視パターン。 |
| [`git-worktree-cache-pattern.md`](./git-worktree-cache-pattern.md) | 同一 remote を繰り返し clone するツール向けに共有 bare ミラー＋使い捨て worktree へ置換する汎用パターン。 |
| [`gitlab-agent-sns-design.md`](./gitlab-agent-sns-design.md) | GitLab Issue＋Moltbook リポジトリでエージェント向け SNS を構築する moltbook-use の確定版設計。 |
| [`node-federation-design.md`](./node-federation-design.md) | ノードのローカル改善を中央へ集約する pull/push 設計。自ら実装済み・仕様リファレンスと明記。 |
| [`plan-a-local-gitlab-design.md`](./plan-a-local-gitlab-design.md) | ローカル GitLab CE 作業インスタンス（案A）の設計・運用正典。 |

### 4. 歴史的・比較検討

> agent-ollama の統合元 2 文書は日付つきの検討記録として
> [`2026-08-07-agent-ollama-tool-disclosure-design.md`](../plans/2026-08-07-agent-ollama-tool-disclosure-design.md) と
> [`2026-08-08-agent-ollama-expansion-design.md`](../plans/2026-08-08-agent-ollama-expansion-design.md) へ移動した。

| ファイル | 要旨 |
|---|---|
| [`ltm-use-v4-design.md`](./ltm-use-v4-design.md) | 類似記憶検知・ハイブリッド recall・自動タグ付けの提案設計（Draft のまま）。後継 `ltm-use-v5-brain-design.md` に前提として引き継がれた。 |
| [`ltm-use-v5-brain-design.md`](./ltm-use-v5-brain-design.md) | 脳構造になぞらえた記憶固定化・context-aware recall の提案設計（Draft のまま、前提: ltm-use v4.0.0）。 |
| [`ltm-use-embedding-recall-design.md`](./ltm-use-embedding-recall-design.md) | 埋め込み（bge-m3）を recall へ足す設計（Draft・実測済み）。合成ではなく段構え——TF-IDF が自信を持てないときだけ落とす。用語を忘れた検索で hit@5 35% → 60%、用語が出るケースは経路が変わらない。 |
| [`selfhost-forge-comparison.md`](./selfhost-forge-comparison.md) | セルフホスト構成 5 案（A〜E）を比較した資料。主推奨は案C（コードのみローカル、issues/MR は上流のまま）、issues/MR を必ずローカルに置く要件がある場合の代替は案A。実採用は案A（`plan-a-local-gitlab-design.md`）で、本書はその意思決定に至った経緯記録。 |
| [`gitea-gitlab-sync-design.md`](./gitea-gitlab-sync-design.md) | LAN 内 Gitea で Issue/MR 管理、コードは GitLab と双方向同期する設計正典（`selfhost-forge-comparison.md` の案Bに相当、実装は未着手と本文に明記）。比較の結果、実採用は案A（plan-a 側）で本案は不採用。 |

---

## 前提・スコープ外の事項

本 README は `docs/designs/` 配下の実ファイル一覧（25 件、2026-07-27 に実在確認済み。2026-08-03 に `agent-audit-design.md` を追加し 26 件）を基準に作成した。2026-08-06 にループ拡張の 8 件を `agent-loop-design.md` へ統合・削除し、同日に追加された `agent-loop-slash-property-design.md` とあわせて実ファイル 21 件。2026-08-07 に `agent-ollama-tool-disclosure-design.md` を、2026-08-08 に `agent-ollama-expansion-design.md` を追加し実ファイル 23 件。2026-08-09 に `agent-loop-slash-property-design.md` を統合・削除して実ファイル 22 件、`agent-ollama-design.md` を追加して実ファイル 23 件、統合元 2 文書を `docs/plans/` へ移して実ファイル 21 件（索引掲載 20 件。索引外の 1 件は [`agent-tools-concept.md`](./agent-tools-concept.md) の補助資料 `agent-tools-business-improvement-prompt.md`）。
