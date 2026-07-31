# 仕様書テンプレート集

各テンプレートは章構成の雛形を提供する。Phase 2 のWBS作成時にこのファイルを参照して章構成を確定する。

## 目次

- [テンプレート1: Webアプリケーション仕様書](#テンプレート1-webアプリケーション仕様書)
- [テンプレート2: APIサービス仕様書](#テンプレート2-apiサービス仕様書)
- [テンプレート3: バッチ処理システム仕様書](#テンプレート3-バッチ処理システム仕様書)
- [テンプレート4: ライブラリ/SDK仕様書](#テンプレート4-ライブラリsdk仕様書)
- [テンプレート5: モノリシックシステム仕様書](#テンプレート5-モノリシックシステム仕様書)
- [章テンプレート: 個別章の記述フォーマット](#章テンプレート-個別章の記述フォーマット)

---

## テンプレート1: Webアプリケーション仕様書

**選択条件**: フロントエンド+バックエンド構成、ルーティングがある

```
00-metadata.md          - 生成メタデータ
01-overview.md          - システム概要・目的・対象ユーザー
02-architecture.md      - アーキテクチャ概要（構成図、技術スタック）
03-frontend.md          - フロントエンド仕様（画面一覧、コンポーネント）
04-backend.md           - バックエンド仕様（サービス層、ビジネスロジック）
05-api.md               - API仕様（エンドポイント一覧、リクエスト/レスポンス）
06-data-model.md        - データモデル（ER図、テーブル定義）
07-auth.md              - 認証・認可仕様
08-external-systems.md  - 外部システム連携
09-error-handling.md    - エラーハンドリング方針
10-deployment.md        - デプロイ・環境構成
99-unresolved.md        - 未確定事項
traceability.md         - トレーサビリティ表
```

---

## テンプレート2: APIサービス仕様書

**選択条件**: REST/GraphQL/gRPC エンドポイント中心

```
00-metadata.md          - 生成メタデータ
01-overview.md          - サービス概要・目的・想定クライアント
02-architecture.md      - アーキテクチャ（構成図、技術スタック）
03-authentication.md    - 認証・認可（APIキー、OAuth、JWT等）
04-endpoints.md         - エンドポイント一覧（メソッド、パス、説明）
05-request-response.md  - リクエスト/レスポンス仕様（スキーマ、例）
06-error-codes.md       - エラーコード一覧・ハンドリング
07-rate-limiting.md     - レート制限・スロットリング
08-data-model.md        - データモデル（スキーマ定義）
09-versioning.md        - バージョニング方針
10-deployment.md        - デプロイ・環境構成
99-unresolved.md        - 未確定事項
traceability.md         - トレーサビリティ表
```

---

## テンプレート3: バッチ処理システム仕様書

**選択条件**: ジョブ・スケジューラ・ETL構成

```
00-metadata.md          - 生成メタデータ
01-overview.md          - システム概要・目的・処理対象
02-architecture.md      - アーキテクチャ（構成図、処理フロー）
03-jobs.md              - ジョブ一覧（名称、説明、実行条件）
04-schedule.md          - スケジュール定義（Cron式、実行頻度）
05-data-flow.md         - データフロー（入力・変換・出力）
06-error-handling.md    - エラーハンドリング・リトライ仕様
07-monitoring.md        - 監視・アラート設計
08-data-model.md        - データモデル（入出力スキーマ）
09-external-systems.md  - 外部システム連携（DB、API、ストレージ）
10-deployment.md        - デプロイ・環境構成
99-unresolved.md        - 未確定事項
traceability.md         - トレーサビリティ表
```

---

## テンプレート4: ライブラリ/SDK仕様書

**選択条件**: npm/pip等でパッケージ配布される

```
00-metadata.md          - 生成メタデータ
01-overview.md          - ライブラリ概要・目的・想定ユーザー
02-installation.md      - インストール方法
03-quickstart.md        - クイックスタート
04-api-reference.md     - パブリックAPI一覧（クラス、関数、引数、戻り値）
05-configuration.md     - 設定オプション
06-error-handling.md    - エラー種別・ハンドリング
07-examples.md          - ユースケース別コード例
08-internals.md         - 内部アーキテクチャ（メンテナー向け）
09-changelog.md         - 変更履歴（既存CHANGELOGから抽出）
99-unresolved.md        - 未確定事項
traceability.md         - トレーサビリティ表
```

---

## テンプレート5: モノリシックシステム仕様書

**選択条件**: 大規模レガシー、複合構成

```
00-metadata.md          - 生成メタデータ
01-overview.md          - システム概要・歴史的背景・目的
02-architecture.md      - 全体アーキテクチャ（モジュール構成図）
03-modules.md           - モジュール一覧・責務分担
04-business-logic.md    - 主要業務ロジック（フロー、条件分岐）
05-data-model.md        - データモデル（ER図、テーブル定義）
06-api-internal.md      - 内部API・モジュール間インターフェース
07-external-systems.md  - 外部システム連携
08-auth.md              - 認証・認可仕様
09-error-handling.md    - エラーハンドリング方針
10-known-issues.md      - 既知の問題・技術的負債
11-deployment.md        - デプロイ・環境構成
99-unresolved.md        - 未確定事項
traceability.md         - トレーサビリティ表
```

---

## 章テンプレート: 個別章の記述フォーマット

各章は以下の構造で記述する:

```markdown
# [章タイトル]

> **信頼性**: [CONFIDENCE: HIGH/MED/LOW]
> **最終更新**: Phase 3 調査時

## 概要

[この章が扱う内容を2〜3文で説明する] [REF: ファイル:行番号]

## 詳細

[具体的な内容。各記述にREFマーカーを付ける]

[ASSUMED: 推測した内容がある場合に明示]
[ASK SME: 専門家確認が必要な点]

## 未確定事項

- [BLOCKED: 確認できなかった事項とその理由]
```
