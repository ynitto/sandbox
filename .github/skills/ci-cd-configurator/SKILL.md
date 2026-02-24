---
name: ci-cd-configurator
description: GitLab CI / Jenkins (Pipeline) のパイプラインを構築・最適化する。「GitLab CIを設定して」「Jenkinsfileを作って」「CI/CDパイプラインを作って」「デプロイを自動化して」「ビルドパイプラインを最適化して」「ワークフローを作成して」などのリクエストで発動する。React/TS・Python・Go・Docker 対応、AWS インフラ専用。
---

# CI/CD Configurator

GitLab CI および Jenkins (Declarative Pipeline) 向けパイプラインを設計・生成する。インフラは **AWS に限定**。

## スキルエコシステム内の位置づけ

- **scrum-master** がスプリントで生成した成果物を本番へ届けるラストワンマイル
- **react-frontend-coder** が生成した Vite/React アプリのビルド・デプロイ設定を担う
- **systematic-debugging** と連携してパイプライン失敗を診断する

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

### React/TypeScript + Vite

#### GitLab CI (`.gitlab-ci.yml`)

```yaml
stages:
  - lint
  - test
  - build
  - deploy

variables:
  NODE_VERSION: "20"

default:
  image: node:${NODE_VERSION}-alpine
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/

lint:
  stage: lint
  script:
    - npm ci
    - npm run lint

test:
  stage: test
  script:
    - npm ci
    - npm run test -- --coverage
  coverage: '/Lines\s*:\s*(\d+\.?\d*)%/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml

build:
  stage: build
  script:
    - npm ci
    - npm run build
  artifacts:
    paths:
      - dist/
    expire_in: 1 hour

deploy-staging:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: staging
    url: https://${CF_DOMAIN_STAGING}
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - aws s3 sync dist/ s3://${S3_BUCKET_STAGING}/ --delete --region ${AWS_REGION}
    - aws cloudfront create-invalidation --distribution-id ${CF_DIST_ID_STAGING} --paths "/*"

deploy-production:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: production
    url: https://${CF_DOMAIN_PROD}
  rules:
    - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/
      when: manual
  script:
    - aws s3 sync dist/ s3://${S3_BUCKET_PROD}/ --delete --region ${AWS_REGION}
    - aws cloudfront create-invalidation --distribution-id ${CF_DIST_ID_PROD} --paths "/*"
```

#### Jenkins (`Jenkinsfile`)

```groovy
pipeline {
    agent { label 'nodejs' }
    tools { nodejs 'node-20' }
    environment {
        AWS_REGION = 'ap-northeast-1'
    }
    stages {
        stage('Lint') {
            steps { sh 'npm ci && npm run lint' }
        }
        stage('Test') {
            steps {
                sh 'npm run test -- --coverage'
            }
            post {
                always {
                    junit 'coverage/junit.xml'
                    publishHTML target: [
                        reportDir: 'coverage/lcov-report',
                        reportFiles: 'index.html',
                        reportName: 'Coverage Report'
                    ]
                }
            }
        }
        stage('Build') {
            steps {
                sh 'npm run build'
                archiveArtifacts artifacts: 'dist/**', fingerprint: true
            }
        }
        stage('Deploy to Staging') {
            when { branch 'main' }
            steps {
                withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                                  credentialsId: 'aws-credentials']]) {
                    sh '''
                        aws s3 sync dist/ s3://${S3_BUCKET_STAGING}/ --delete --region ${AWS_REGION}
                        aws cloudfront create-invalidation \
                            --distribution-id ${CF_DIST_ID_STAGING} --paths "/*"
                    '''
                }
            }
        }
        stage('Deploy to Production') {
            when { tag pattern: /v\d+\.\d+\.\d+/, comparator: 'REGEXP' }
            input { message "Deploy ${TAG_NAME} to production?" }
            steps {
                withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                                  credentialsId: 'aws-credentials']]) {
                    sh '''
                        aws s3 sync dist/ s3://${S3_BUCKET_PROD}/ --delete --region ${AWS_REGION}
                        aws cloudfront create-invalidation \
                            --distribution-id ${CF_DIST_ID_PROD} --paths "/*"
                    '''
                }
            }
        }
    }
    post {
        failure {
            slackSend channel: '#ci-alerts', color: 'danger',
                message: ":x: *${env.JOB_NAME}* #${env.BUILD_NUMBER} failed on `${env.BRANCH_NAME}`\n<${env.BUILD_URL}|View>"
        }
    }
}
```

---

### Python + FastAPI

#### GitLab CI

```yaml
stages:
  - lint
  - test
  - security
  - build
  - deploy

default:
  image: python:3.12-slim

lint:
  stage: lint
  script:
    - pip install ruff
    - ruff check .

test:
  stage: test
  script:
    - pip install -r requirements.txt -r requirements-dev.txt
    - pytest --cov=app --cov-report=xml --junitxml=test-results.xml
  coverage: '/TOTAL.+?(\d+)%/'
  artifacts:
    reports:
      junit: test-results.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

security:
  stage: security
  script:
    - pip install pip-audit bandit
    - pip-audit -r requirements.txt
    - bandit -r app/ -f json -o bandit-results.json
  artifacts:
    paths:
      - bandit-results.json
  allow_failure: false

build-push:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - aws ecr get-login-password --region ${AWS_REGION} |
        docker login --username AWS --password-stdin ${ECR_REGISTRY}
    - docker build -t ${ECR_REGISTRY}/${ECR_REPO}:${CI_COMMIT_SHORT_SHA} .
    - docker push ${ECR_REGISTRY}/${ECR_REPO}:${CI_COMMIT_SHORT_SHA}

deploy-ecs:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: staging
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - !reference [.ecs-deploy, script]
```

#### Jenkins

```groovy
pipeline {
    agent { label 'python' }
    stages {
        stage('Lint') {
            steps { sh 'pip install ruff && ruff check .' }
        }
        stage('Test') {
            steps {
                sh 'pip install -r requirements.txt -r requirements-dev.txt'
                sh 'pytest --cov=app --cov-report=xml --junitxml=test-results.xml'
            }
            post { always { junit 'test-results.xml' } }
        }
        stage('Security') {
            steps {
                sh 'pip install pip-audit bandit'
                sh 'pip-audit -r requirements.txt'
                sh 'bandit -r app/ -f xml -o bandit-results.xml'
            }
            post { always { archiveArtifacts 'bandit-results.xml' } }
        }
        stage('Build & Push to ECR') {
            steps {
                withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                                  credentialsId: 'aws-credentials']]) {
                    sh '''
                        aws ecr get-login-password --region ${AWS_REGION} |
                            docker login --username AWS --password-stdin ${ECR_REGISTRY}
                        docker build -t ${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER} .
                        docker push ${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER}
                    '''
                }
            }
        }
        stage('Deploy to ECS') {
            when { branch 'main' }
            steps {
                withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                                  credentialsId: 'aws-credentials']]) {
                    sh '''
                        TASK_DEF=$(aws ecs describe-task-definition \
                            --task-definition ${ECS_TASK_FAMILY} --query taskDefinition --output json)
                        NEW_TASK_DEF=$(echo $TASK_DEF | jq \
                            --arg IMAGE "${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER}" \
                            '.containerDefinitions[0].image = $IMAGE
                             | del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy)')
                        TASK_ARN=$(aws ecs register-task-definition \
                            --cli-input-json "$NEW_TASK_DEF" \
                            --query taskDefinition.taskDefinitionArn --output text)
                        aws ecs update-service \
                            --cluster ${ECS_CLUSTER} --service ${ECS_SERVICE} --task-definition ${TASK_ARN}
                        aws ecs wait services-stable \
                            --cluster ${ECS_CLUSTER} --services ${ECS_SERVICE}
                    '''
                }
            }
        }
    }
}
```

---

### Go

#### GitLab CI

```yaml
stages:
  - lint
  - test
  - build
  - deploy

default:
  image: golang:1.23-alpine

lint:
  stage: lint
  script:
    - go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
    - golangci-lint run

test:
  stage: test
  script:
    - go test -race -coverprofile=coverage.out ./...
    - go tool cover -func=coverage.out
  coverage: '/total:\s+\(statements\)\s+(\d+\.\d+)%/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

build:
  stage: build
  script:
    - go build -o bin/app ./...
  artifacts:
    paths:
      - bin/
```

#### Jenkins

```groovy
pipeline {
    agent { label 'golang' }
    environment { GOPATH = "${WORKSPACE}/.go" }
    stages {
        stage('Lint') {
            steps {
                sh 'go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest'
                sh 'golangci-lint run'
            }
        }
        stage('Test') {
            steps {
                sh 'go test -race -coverprofile=coverage.out ./...'
                sh 'go tool cover -func=coverage.out'
            }
        }
        stage('Build') {
            steps {
                sh 'go build -o bin/app ./...'
                archiveArtifacts artifacts: 'bin/**', fingerprint: true
            }
        }
    }
}
```

---

### Docker コンテナ (ECR + ECS)

#### GitLab CI

```yaml
stages:
  - build
  - security
  - deploy

variables:
  AWS_REGION: ap-northeast-1
  ECR_REGISTRY: ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
  IMAGE_TAG: ${CI_COMMIT_SHORT_SHA}

build-push:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - aws ecr get-login-password --region ${AWS_REGION} |
        docker login --username AWS --password-stdin ${ECR_REGISTRY}
    - docker build -t ${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG} .
    - docker push ${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}

trivy-scan:
  stage: security
  image:
    name: aquasec/trivy:latest
    entrypoint: [""]
  script:
    - trivy image --exit-code 1 --severity HIGH,CRITICAL
        --format json --output trivy-results.json
        ${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}
  artifacts:
    paths:
      - trivy-results.json
  allow_failure: false

deploy-staging:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: staging
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - |
      TASK_DEF=$(aws ecs describe-task-definition \
        --task-definition ${ECS_TASK_FAMILY} --query taskDefinition --output json)
      NEW_TASK_DEF=$(echo $TASK_DEF | jq \
        --arg IMAGE "${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}" \
        '.containerDefinitions[0].image = $IMAGE
         | del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy)')
      TASK_ARN=$(aws ecs register-task-definition \
        --cli-input-json "$NEW_TASK_DEF" \
        --query taskDefinition.taskDefinitionArn --output text)
      aws ecs update-service \
        --cluster ${ECS_CLUSTER} --service ${ECS_SERVICE} --task-definition ${TASK_ARN}
      aws ecs wait services-stable --cluster ${ECS_CLUSTER} --services ${ECS_SERVICE}

deploy-production:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: production
  rules:
    - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/
      when: manual
  script:
    - !reference [deploy-staging, script]
```

---

## マルチ環境デプロイ設計

### GitLab CI 環境設定

```yaml
.deploy-ecs-template:
  image: amazon/aws-cli
  script:
    - |
      TASK_ARN=$(aws ecs register-task-definition \
        --cli-input-json "$(aws ecs describe-task-definition \
          --task-definition ${ECS_TASK_FAMILY} --query taskDefinition --output json |
          jq --arg IMAGE "${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}" \
          '.containerDefinitions[0].image = $IMAGE
           | del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy)')" \
        --query taskDefinition.taskDefinitionArn --output text)
      aws ecs update-service \
        --cluster ${ECS_CLUSTER} --service ${ECS_SERVICE} --task-definition ${TASK_ARN}
      aws ecs wait services-stable --cluster ${ECS_CLUSTER} --services ${ECS_SERVICE}

deploy-staging:
  extends: .deploy-ecs-template
  environment:
    name: staging
  rules:
    - if: $CI_COMMIT_BRANCH == "main"

deploy-production:
  extends: .deploy-ecs-template
  environment:
    name: production
  rules:
    - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/
      when: manual
```

### Jenkins マルチ環境

```groovy
stage('Deploy') {
    matrix {
        axes {
            axis {
                name 'ENVIRONMENT'
                values 'staging', 'production'
            }
        }
        when {
            anyOf {
                allOf {
                    expression { ENVIRONMENT == 'staging' }
                    branch 'main'
                }
                allOf {
                    expression { ENVIRONMENT == 'production' }
                    tag pattern: /v\d+\.\d+\.\d+/, comparator: 'REGEXP'
                }
            }
        }
        stages {
            stage('Approval') {
                when { expression { ENVIRONMENT == 'production' } }
                steps { input "Deploy to production?" }
            }
            stage('ECS Deploy') {
                steps {
                    withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                                      credentialsId: 'aws-credentials']]) {
                        sh "echo Deploying to ${ENVIRONMENT}"
                        // ECS deploy script here
                    }
                }
            }
        }
    }
}
```

production 環境では GitLab の `when: manual` または Jenkins の `input` step で手動承認を必須にする。

---

## 詳細リファレンス

- **YAML スニペット集**: [references/yaml-snippets.md](references/yaml-snippets.md)
