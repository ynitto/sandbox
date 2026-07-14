# 棚卸し: 外部連携・インフラ・記憶カテゴリ

対象: `docs/designs/` のうち t1（エンジン中核）・t2（一貫性ゲート・ループ拡張）が
挙げた計 12 ファイル（`codd-gate-design.md` は両方に計上）を除いた残り 11 ファイル。
実在確認は `/Users/nitto/Workspace/sandbox/docs/designs/`（本 worktree `sandbox-agent-state` に
`docs/designs` が存在しないため、同一 git リポジトリの main worktree を参照読み専用で確認。
書き込み・commit・checkout は一切行っていない）。

## 一覧表

| ファイル名 | 要旨 | 対象読者 | 現行/歴史的 |
|---|---|---|---|
| `agent-cli-plugin-design.md` | agent-project/agent-flow が呼ぶ LLM 実行 CLI（agent_cli）をプラグイン化し、`schemas/agent-cli.schema.json` を正典とするデータ契約のみで CLI 追加を可能にする。あわせて失敗を quota/auth/env/transient で決定的にトリアージし `[agent-error:<class>]` タグで全層に伝える設計。 | agent-project/agent-flow の実装者・新しい agent CLI（cursor/ollama 等）を追加する開発者 | 現行（最終更新2026-07-13、最終コミット2026-07-14） |
| `agent-flow-retry-inheritance-design.md` | agent-flow がリトライで新規 run を作る際、先行 run（timeout/failed）の結果・計画・中間成果物・作業ブランチを再利用して先行 run を安全に削除する設計。gitlab executor のような長時間委譲での「毎回ゼロからやり直し」を解消する。 | agent-flow の実装者（gitlab executor 等の長時間委譲を運用する開発者） | 現行（作成2026-07-05、旧 `kiro-flow` からの改称クローン日2026-07-14。t1 の棚卸しでは「エンジン中核の主要設計ではない衛星ドキュメント」として表から除外されており、本カテゴリでの計上は判断枝と位置づける。§末尾「採用した前提」参照） |
| `git-gitlab-circuit-breaker-pattern.md` | git/GitLab など外部エンドポイントへアクセスする任意のツール・スキルへ転用できる汎用サーキットブレーカー＋監視ログのパターン設計書。ホスト単位でリトライ・fail-fast・障害監視を1箇所（`tools/gitguard/`）に集約する。 | git/GitLab へアクセスする各ツール・スキルの実装者（`gitguard.py`/`gitlab-idd` 等の移植先開発者） | 現行（作成2026-06-29、最終コミット2026-07-15） |
| `git-worktree-cache-pattern.md` | 同一リモート repo を繰り返し clone するツール（agent-flow/agent-project 等）向けに、ホスト共有の bare ミラー＋使い捨て worktree に置き換えてネットワーク負荷を初回+増分に圧縮する汎用パターン設計書。 | 同一 repo を繰り返し扱うツール・スキルの実装者 | 現行（作成2026-06-29、最終コミット2026-07-15） |
| `gitea-gitlab-sync-design.md` | LAN 内 Gitea を issue/MR 管理面にしつつコードは GitLab（マスター）と双方向 fast-forward 同期する「同期ロボット（reconcile daemon）」構成の設計正典。実装は本書時点で未着手と明記。 | 自前フォージ導入・GitLab トラフィック削減を検討する運用担当者・同期ロボットの実装者 | 現行（最終更新2026-07-08、最終コミット2026-07-15） |
| `gitlab-agent-sns-design.md` | GitLab Issue（ホット層）と Moltbook リポジトリの `knowledge/*.md`（コールド層、GitLab CI が格納）でエージェント向け SNS を構築する `moltbook-use` スキルの確定版設計書（v1〜v5 の意思決定の到達点）。 | `moltbook-use` の実装者・ltm-use/wiki-use との連邦検索を設計する開発者 | 現行（作成2026-06-05、整理2026-06-06「確定事項に基づく統合版」、最終コミット2026-06-06） |
| `ltm-use-v4-design.md` | ltm-use に類似記憶の重複検知（TF-IDF）・ハイブリッドランキング recall・自動タグ付けを追加する提案設計書。ステータスは Draft のまま。 | ltm-use の実装者 | 歴史的（提案内容＝`similarity.py`/`auto_tagger.py` は実装済みで現行コードに存在するが、`docs/plans/2026-03-10-skill-improvement-proposals.md` が「v4.0.0 の SKILL.md で機能は記述済み、設計書は Draft のまま存在」と記す通り、現行の仕様正典は SKILL.md 側に移っており本書自体は当時の提案記録） |
| `ltm-use-v5-brain-design.md` | 人間の脳の記憶サブシステム（海馬・新皮質・前頭前皮質等）になぞらえ、ltm-use に記憶固定化（consolidate：エピソード→意味記憶への蒸留）・context-aware recall・importance 強度を導入する提案設計書。ステータスは Draft のまま。 | ltm-use の実装者 | 歴史的（提案内容＝`consolidate_memory.py` は実装済みで現行コードに存在するが、v4 同様ステータスは Draft のまま更新されておらず現行の仕様正典ではない） |
| `node-federation-design.md` | 各ノードでのローカルスキル改善を中央リポジトリへ安全に集約する pull（自動スナップショット＋ロールバック）／push（PR ベース）の仕組みの設計書。タイトルに「✅ 実装済み」と明記し、実装完了状態を反映した現行実装の仕様リファレンスとして使う旨を自ら宣言している。 | `git-skill-manager` の実装者・スキル配布運用を行う開発者 | 現行（実装済みだが「仕様リファレンスとして参照できる」と本文が明記しており生きた正典として維持） |
| `plan-a-local-gitlab-design.md` | 個人 Windows PC の WSL2+Docker にローカル GitLab CE を作業インスタンスとして立て、issues/MR/notes をローカルで完結させつつコードは上流 GitLab と双方向 fast-forward 同期する「案A」の設計・運用正典。既存 GitLab API v4 前提資産が無改修で動く点を要点とする。 | ローカル GitLab CE 運用の構築・運用担当者 | 現行（最終更新2026-07-08、最終コミット2026-07-15） |
| `selfhost-forge-comparison.md` | 上流 GitLab アクセス負荷削減のためのセルフホスト構成案（案A: ローカル GitLab CE／案B: ローカル Gitea／案C: コードのみローカル分離）を、既存 GitLab 前提資産の結合度から比較し案Aを推奨する比較資料。 | セルフホスト構成の意思決定者・アーキテクト | 現行（最終更新2026-07-08、最終コミット2026-07-15、`plan-a-local-gitlab-design.md` が採用案として参照） |

## 検証

- 対象11ファイルすべて `ls -la /Users/nitto/Workspace/sandbox/docs/designs/` で実在確認済み。
- 各ファイルの要旨・対象読者はヘッダ・概要節（先頭60行程度）を実読して記述した。
- 現行/歴史的の判定は各ファイルの自己申告（ステータス欄・「実装済み」等の注記）、`git log -1` の最終コミット日、
  および `ltm-use-v4/v5` については `grep` で本文中の関連キーワードと `docs/plans/2026-03-10-skill-improvement-proposals.md`
  からの参照実態を突き合わせて判定した。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本 worktree（`agent-state` ブランチ、`.agent-project`）には `docs/designs` が存在しないため、
  同一 git リポジトリの main worktree（`/Users/nitto/Workspace/sandbox`）を参照読み専用で確認した
  （書き込み・commit・checkout は一切行っていない）。
- **前提**: 本タスクの対象範囲は「t1・t2 が明示的に計上したファイル集合に属さない、docs/designs 配下の残り全ファイル」とし、
  カテゴリ名（GitLab/CI連携・ストレージ/バス・記憶層）は代表例であって網羅的な絞り込み条件ではないと解釈した。
  この結果、汎用インフラパターン（circuit-breaker/worktree-cache）・CLI プラグイン設計・ノード連携設計も本表に含めている。
- **未解決事項1**: `agent-flow-retry-inheritance-design.md` は内容的には agent-flow（エンジン中核＝t1 領域）の詳細設計だが、
  t1 の棚卸しでは「主要設計ではない衛星ドキュメント」として表から明示的に除外されている。本タスクの完了条件
  「上記2カテゴリに属さない現行設計書」を字義通り満たすため本表に計上したが、README の最終カテゴリ配置は
  gate タスクの判断に委ねる（t1 側の agent-flow 項目からのリンクとして扱う案もあり得る）。
- **未解決事項2**: `ltm-use-v4-design.md` / `ltm-use-v5-brain-design.md` の「歴史的」判定は、提案機能が実装済みである一方
  ドキュメント自身のステータスが Draft のまま更新されていないという間接証拠に基づく判断であり、t4（歴史的経緯カテゴリ）の
  定義「経緯の記録として残っている設計書」と完全に一致するかは gate の確認を要する。
- **範囲外で見つけた問題**: `gitea-gitlab-sync-design.md`（実装未着手と自己申告）と `plan-a-local-gitlab-design.md`
  ／`selfhost-forge-comparison.md`（案A採用・運用手順まで整備済み）は同じ「GitLab 負荷削減」というテーマで内容が重複気味
  （fast-forward 調停・reconcile daemon の説明が両方に登場）。README では `selfhost-forge-comparison.md` →
  `plan-a-local-gitlab-design.md`（案A詳細）→ `gitea-gitlab-sync-design.md`（Gitea 案の同期方式の元設計、案Aからも参照される）
  の順で読む導線を張ることを推奨する（本タスクでは修正・統合は行っていない）。
