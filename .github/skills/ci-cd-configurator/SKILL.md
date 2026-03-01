---
name: ci-cd-configurator
description: GitLab CI / Jenkins (Pipeline) のパイプラインを構築・最適化する。「GitLab CIを設定して」「Jenkinsfileを作って」「CI/CDパイプラインを作って」「デプロイを自動化して」「ビルドパイプラインを最適化して」「ワークフローを作成して」などのリクエストで発動する。React/TS・Python・Go・Docker 対応、AWS インフラ専用。
metadata:
  version: "1.0"
---

# CI/CD Configurator

GitLab CI および Jenkins (Declarative Pipeline) 向けパイプラインを設計・生成する。インフラは **AWS に限定**。

## ワークフロー生成フロー

### Step 1: プロジェクト情報の収集

**ユーザープロンプトおよびワークスペースのドキュメントから以下を自動判定する**（不明な場合は質問せず推定値を採用）。

#### 実行ツールの検出ロジック

以下の順で優先度を付けて判定する:

1. **ユーザーの明示的な指定**（最優先）: 「ruffでlintして」「pytestでテストして」「セキュリティスキャンも追加して」など
2. **ワークスペースのドキュメント**: `README.md`, `CONTRIBUTING.md`, `docs/` 内を読み、開発手順・CI/CD 記述を抽出
3. **設定ファイルの存在確認**: 下表のファイルを検索して自動推定

| 検出対象 | 調査するファイル/パターン |
|---------|--------------------------|
| 言語/FW | `package.json`, `requirements*.txt`, `pyproject.toml`, `go.mod`, `pom.xml`, `build.gradle` |
| Linter | `.eslintrc*`, `biome.json`, `ruff.toml`, `pyproject.toml [tool.ruff]`, `.golangci.yml`, `checkstyle*.xml` |
| テスト | `jest.config.*`, `vitest.config.*`, `pytest.ini`, `pyproject.toml [tool.pytest]`, `*_test.go`, `*Test.java` |
| セキュリティ | `.trivyignore`, `bandit.yaml`, ユーザーが「セキュリティ」「脆弱性」「SAST」「SCA」に言及 |
| カバレッジ | `codecov.yml`, `.coveragerc`, ユーザーが「カバレッジ」「coverage」に言及 |

#### CI/CD 実現手段の選択

ユーザーに明示された場合はそちらを優先。不明な場合:

- **GitLab CI**: GitLab リポジトリ、または `.gitlab-ci.yml` が既に存在する場合
- **Jenkins**: Jenkins サーバーが言及されている、または `Jenkinsfile` が既に存在する場合

### Step 2: CI フェーズ設計

検出結果に基づき、実行するジョブを選択する:

```
[lint] → [test] → [build] → [security-scan]
```

| ジョブ | 含める条件 | 推奨ツール |
|--------|-----------|-----------|
| lint | Linter 設定ファイルあり、またはユーザー指定 | ESLint/Biome (JS/TS), ruff (Python), golangci-lint (Go) |
| test | テスト設定ファイルあり、またはユーザー指定 | Jest/Vitest, pytest, go test, JUnit |
| coverage | カバレッジ要求あり | Jest --coverage, pytest-cov, go test -coverprofile |
| build | 常時実行 | npm run build, pip wheel, go build, Docker build + ECR push |
| security-scan | セキュリティ設定・ユーザー指定あり | Trivy, npm audit, pip-audit, Bandit, OWASP Dependency-Check |

### Step 3: CD フェーズ設計（AWS 専用）

```
staging デプロイ → smoke test → production デプロイ → notify
```

デプロイ先は以下の AWS サービスから選択:

| デプロイ先 | 選択条件 |
|-----------|---------|
| AWS ECS (Fargate) | Docker コンテナアプリ |
| AWS Lambda | サーバーレス関数 |
| AWS S3 + CloudFront | 静的サイト (React/Vite 等) |
| AWS EC2 (CodeDeploy) | 従来型アプリケーション |

- `main` ブランチ → staging 自動デプロイ
- タグ/リリース → production デプロイ（手動承認必須）

### Step 4: YAML / Jenkinsfile 生成

詳細スニペットは [references/yaml-snippets.md](references/yaml-snippets.md) 参照。

### Step 5: レビューと調整

生成後に以下を提案する:
- 必要な AWS IAM ポリシー / ロール
- 必要なシークレット/環境変数一覧（GitLab CI Variables または Jenkins Credentials）
- キャッシュキー戦略
- ブランチ保護・パイプライン保護設定

---

## ベストプラクティス

### キャッシュ戦略

#### GitLab CI

```yaml
# Node.js
cache:
  key:
    files:
      - package-lock.json
  paths:
    - node_modules/
    - .npm/

# Python
cache:
  key:
    files:
      - requirements.txt
  paths:
    - .cache/pip/

# Go
cache:
  key:
    files:
      - go.sum
  paths:
    - .cache/go/
```

#### Jenkins

```groovy
// Pipeline オプションで古いビルドを保持しない
options {
  buildDiscarder(logRotator(numToKeepStr: '10'))
  skipDefaultCheckout(false)
}
// Node.js: node_modules をワークスペース間でキャッシュする場合は
// Jenkins Shared Library の withCache ステップを使用
```

### AWS 認証

#### GitLab CI (OIDC / Web Identity)

```yaml
variables:
  AWS_REGION: ap-northeast-1
  ROLE_ARN: arn:aws:iam::${AWS_ACCOUNT_ID}:role/gitlab-ci

before_script:
  - >
    export $(printf "AWS_ACCESS_KEY_ID=%s AWS_SECRET_ACCESS_KEY=%s AWS_SESSION_TOKEN=%s"
    $(aws sts assume-role-with-web-identity
    --role-arn ${ROLE_ARN}
    --role-session-name gitlab-ci
    --web-identity-token ${CI_JOB_JWT_V2}
    --duration-seconds 3600
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]'
    --output text))
```

#### Jenkins (AWS Credentials Plugin)

```groovy
withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                  credentialsId: 'aws-credentials',
                  accessKeyVariable: 'AWS_ACCESS_KEY_ID',
                  secretKeyVariable: 'AWS_SECRET_ACCESS_KEY']]) {
    sh 'aws sts get-caller-identity'
}
```

### マトリクスビルド

#### GitLab CI

```yaml
.test-matrix:
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.11", "3.12"]
        OS: ["ubuntu", "alpine"]
```

#### Jenkins

```groovy
matrix {
    axes {
        axis { name 'NODE_VERSION'; values '18', '20', '22' }
    }
    stages {
        stage('Test') {
            steps { sh "nvm use ${NODE_VERSION} && npm test" }
        }
    }
}
```

---

## 主要スタック別テンプレート

長大な実装例は参照ファイルに集約し、このファイルは判断ルールと運用要点に集中する。

- React/TypeScript + Vite: [references/yaml-snippets.md](references/yaml-snippets.md)
- Python + FastAPI: [references/yaml-snippets.md](references/yaml-snippets.md)
- Go: [references/yaml-snippets.md](references/yaml-snippets.md)
- Docker (ECR + ECS): [references/yaml-snippets.md](references/yaml-snippets.md)

## マルチ環境デプロイ設計

- staging: `main` ブランチで自動デプロイ
- production: タグリリース時のみ、手動承認を必須化
- GitLab CI は `when: manual`、Jenkins は `input` step を使用

## 詳細リファレンス

- YAML スニペット集: [references/yaml-snippets.md](references/yaml-snippets.md)
