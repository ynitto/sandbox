---
name: ci-cd-configurator
description: GitHub Actions / GitLab CI のパイプラインを構築・最適化する。「GitHub Actionsを設定して」「CI/CDパイプラインを作って」「デプロイを自動化して」「ビルドパイプラインを最適化して」「ワークフローを作成して」などのリクエストで発動する。React/TS・Python・Go・Docker 対応。
depends_on: []
---

# CI/CD Configurator

GitHub Actions（優先）および GitLab CI 向けパイプラインを設計・生成する。

## スキルエコシステム内の位置づけ

- **scrum-master** がスプリントで生成した成果物を本番へ届けるラストワンマイル
- **react-frontend-coder** が生成した Vite/React アプリのビルド・デプロイ設定を担う
- **systematic-debugging** と連携してパイプライン失敗を診断する

## ワークフロー生成フロー

### Step 1: プロジェクト情報の確認

以下を確認する（不明な場合は質問せず合理的なデフォルトを採用）:

| 項目 | 確認内容 |
|------|---------|
| 言語/FW | React/TS, Python, Node.js, Go, Docker |
| テスト手法 | Jest, pytest, go test, etc. |
| デプロイ先 | GitHub Pages, Vercel, AWS, GCP, k8s, etc. |
| ブランチ戦略 | main/develop/feature or trunk-based |

### Step 2: CI フェーズ設計

```
lint → test → build → security-scan
```

- **lint**: ESLint/Biome（JS）, ruff/flake8（Python）, golangci-lint（Go）
- **test**: 単体・結合テスト、カバレッジレポート
- **build**: アーティファクト生成、Docker イメージビルド
- **security-scan**: Trivy（コンテナ）, npm audit / pip-audit（依存関係）

### Step 3: CD フェーズ設計

```
staging デプロイ → smoke test → production デプロイ → notify
```

- `main` ブランチ → staging 自動デプロイ
- タグ/リリース → production デプロイ（手動承認 `environment: production` 推奨）

### Step 4: YAML ファイル生成

詳細スニペットは [references/yaml-snippets.md](references/yaml-snippets.md) 参照。

### Step 5: レビューと調整

生成後に以下を提案する:
- ブランチ保護ルール設定手順
- 必要なシークレット一覧（`DEPLOY_TOKEN` 等）
- キャッシュキー戦略

---

## ベストプラクティス

### キャッシュ戦略

```yaml
# Node.js
- uses: actions/cache@v4
  with:
    path: ~/.npm
    key: ${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}

# Python
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: ${{ runner.os }}-pip-${{ hashFiles('**/requirements*.txt') }}

# Go modules
- uses: actions/cache@v4
  with:
    path: ~/go/pkg/mod
    key: ${{ runner.os }}-go-${{ hashFiles('**/go.sum') }}
```

### マトリクスビルド

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest]
    node-version: [18, 20, 22]
  fail-fast: false
```

### シークレット管理

- 環境変数は `${{ secrets.NAME }}` で参照
- Environment secrets（`production` / `staging`）で環境ごとに分離
- `actions/create-github-app-token` でトークン自動生成を推奨

### self-hosted vs GitHub-hosted の選定基準

| 条件 | 推奨 |
|------|------|
| パブリックリポジトリ | GitHub-hosted |
| 大規模ビルド（>30分） | self-hosted |
| プライベートネットワーク必要 | self-hosted |
| コスト最小化 | self-hosted |
| セットアップ工数ゼロ | GitHub-hosted |

---

## 主要スタック別テンプレート

### React/TypeScript + Vite

```yaml
name: CI/CD - React/Vite

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
      - run: npm ci
      - run: npm run lint
      - run: npm run test -- --coverage
      - run: npm run build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  deploy-staging:
    needs: ci
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      # デプロイ先に応じてステップを追加
```

### Python + FastAPI

```yaml
name: CI/CD - Python/FastAPI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: ruff check .
      - run: pytest --cov=app --cov-report=xml
      - uses: codecov/codecov-action@v4
```

### Go

```yaml
name: CI/CD - Go

on:
  push:
    branches: [main]
  pull_request:

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: stable
          cache: true
      - run: go vet ./...
      - run: go test -race -coverprofile=coverage.out ./...
      - run: go build -o bin/app ./...
```

### Docker コンテナ

```yaml
name: CI/CD - Docker

on:
  push:
    branches: [main]
  release:
    types: [published]

jobs:
  build-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

---

## マルチ環境デプロイ設計

```yaml
deploy:
  needs: ci
  runs-on: ubuntu-latest
  strategy:
    matrix:
      environment: [staging, production]
  environment:
    name: ${{ matrix.environment }}
    url: ${{ steps.deploy.outputs.url }}
  steps:
    - id: deploy
      run: echo "url=https://${{ matrix.environment }}.example.com" >> $GITHUB_OUTPUT
```

production environment には GitHub の `Required reviewers` を設定してデプロイを手動承認にする。

---

## 詳細リファレンス

- **YAML スニペット集**: [references/yaml-snippets.md](references/yaml-snippets.md)
