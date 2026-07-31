# Phase 1: 偵察とテンプレート選定

**目的**: コードベース全体像を素早く把握し、仕様書テンプレートを決定する。深掘りはしない。

---

## 手順

### 1-1. 浅い偵察（深さ3〜4）

```bash
# ファイルツリー（深さ4、ビルド生成物・依存を除外）
find . -maxdepth 4 \
  -not -path '*/.git/*' \
  -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/vendor/*' \
  -not -path '*/.next/*' \
  -not -path '*/dist/*' \
  -not -path '*/build/*' \
  | sort

# パッケージマネージャ・設定ファイルの確認
ls -la | grep -E '\.(json|yaml|yml|toml|ini|env|lock)$'
```

以下の観点でコードベースを把握する:

| 観点 | 確認するもの |
|---|---|
| 言語・FW | `package.json`, `requirements.txt`, `pom.xml`, `go.mod`, `composer.json` 等 |
| エントリポイント | `main.py`, `index.ts`, `app.js`, `Main.java`, `cmd/` 等 |
| ルーティング | `routes/`, `controllers/`, `pages/`, `app/` ディレクトリ |
| データ層 | `models/`, `entities/`, `schema/`, マイグレーションファイル |
| 設定 | `.env.example`, `config/`, `docker-compose.yml` |
| 既存ドキュメント | `docs/`, `README.md`, `ARCHITECTURE.md` |

### 1-2. テンプレートの選択

偵察結果から仕様書テンプレートを1つ選ぶ（詳細章構成は `templates.md` 参照）:

| テンプレート | 選択条件 |
|---|---|
| **Webアプリケーション仕様書** | フロント+バック構成、ルーティングがある |
| **APIサービス仕様書** | REST/GraphQL/gRPC エンドポイント中心 |
| **バッチ処理システム仕様書** | ジョブ・スケジューラ・ETL構成 |
| **ライブラリ/SDK仕様書** | npm/pip等でパッケージ配布される |
| **モノリシックシステム仕様書** | 大規模レガシー、複合構成 |

判断に迷う場合はユーザーに確認する。

### 1-3. 大局的疑問の登録

偵察中に浮かんだ「コードを読んだだけでは答えられない大局的な疑問」を Question Bank に登録する。典型的な疑問例:

- このシステムはどんな業務課題を解決するために作られたか？（`business_rule`）
- なぜこのアーキテクチャ（モノリス/マイクロサービス等）が選ばれたか？（`architecture_decision`）
- 外部連携先のシステムはどんな組織が管理しているか？（`external_integration`）

### 1-4. 偵察レポートの保存

`.specs-work/recon-report.md` に以下を記録する:

```markdown
# 偵察レポート

## 基本情報
- 言語: Python 3.11
- フレームワーク: FastAPI
- DB: PostgreSQL（SQLAlchemy ORM）
- フロントエンド: なし（API専用）
- テスト: pytest

## ディレクトリ構造の概要
（主要ディレクトリの説明）

## 選択したテンプレート
APIサービス仕様書

## 選択理由
REST エンドポイントが中心で、フロントエンドは別リポジトリ

## 大局的疑問（Question Bank登録済み）
- Q-001: このAPIを利用する主要クライアントは？（external_integration / important）
```

---

## 完了条件

- [ ] `.specs-work/recon-report.md` が保存されている
- [ ] テンプレートが確定している
- [ ] 大局的疑問が `questions.json` に登録されている
- [ ] `state.json` の `template` と `language` を更新する
- [ ] ユーザーに「Phase 1 完了。選択テンプレート: [名称]。Phase 2（計画）に進みます」と伝える
