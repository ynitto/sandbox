# GitLab CI / Jenkins スニペット集

GitLab CI (`.gitlab-ci.yml`) および Jenkins (Declarative Pipeline `Jenkinsfile`) 向けの再利用可能なスニペット集。AWS インフラ専用。

## 目次

1. [再利用可能テンプレート](#reusable-template)
2. [依存関係スキャン](#dependency-scan)
3. [セキュリティスキャン (Trivy)](#trivy)
4. [AWS S3 + CloudFront デプロイ (静的サイト)](#s3-cloudfront)
5. [AWS ECS デプロイ](#aws-ecs)
6. [AWS Lambda デプロイ](#aws-lambda)
7. [Slack 通知](#slack-notify)
8. [AWS ECR ログイン共通処理](#ecr-login)

---

## Reusable Template

### GitLab CI - `extends` を使った共通定義

```yaml
# .gitlab-ci.yml

.base-node:
  image: node:20-alpine
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/
  before_script:
    - npm ci

lint:
  extends: .base-node
  stage: lint
  script:
    - npm run lint

test:
  extends: .base-node
  stage: test
  script:
    - npm run test -- --coverage
```

### Jenkins - 共有ライブラリ (Shared Library) 呼び出し

```groovy
// Jenkinsfile
@Library('my-shared-library') _

pipeline {
    agent any
    stages {
        stage('CI') {
            steps {
                // 共有ライブラリで定義した nodeCI ステップを呼び出す
                nodeCI(nodeVersion: '20')
            }
        }
    }
}
```

---

## Dependency Scan

### GitLab CI

```yaml
dependency-scan:
  stage: security
  script:
    # Node.js
    - npm audit --audit-level=high
    # Python (コメントアウトして用途に合わせて切り替え)
    # - pip install pip-audit && pip-audit -r requirements.txt
  allow_failure: false
  artifacts:
    when: always
    paths:
      - npm-audit-results.json
```

### Jenkins

```groovy
stage('Dependency Scan') {
    steps {
        // Node.js
        sh 'npm audit --audit-level=high --json > npm-audit-results.json || true'
        // Python
        // sh 'pip install pip-audit && pip-audit -r requirements.txt'
    }
    post {
        always {
            archiveArtifacts artifacts: 'npm-audit-results.json', allowEmptyArchive: true
        }
    }
}
```

---

## Trivy

コンテナイメージの脆弱性スキャン (HIGH / CRITICAL のみ検出してパイプラインを停止)。

### GitLab CI

```yaml
trivy-scan:
  stage: security
  image:
    name: aquasec/trivy:latest
    entrypoint: [""]
  variables:
    TRIVY_EXIT_CODE: "1"
    TRIVY_SEVERITY: "HIGH,CRITICAL"
  script:
    - trivy image
        --exit-code ${TRIVY_EXIT_CODE}
        --severity ${TRIVY_SEVERITY}
        --format json
        --output trivy-results.json
        ${ECR_REGISTRY}/${ECR_REPO}:${CI_COMMIT_SHORT_SHA}
  artifacts:
    when: always
    paths:
      - trivy-results.json
  allow_failure: false
```

### Jenkins

```groovy
stage('Trivy Scan') {
    steps {
        sh '''
            trivy image \
                --exit-code 1 \
                --severity HIGH,CRITICAL \
                --format json \
                --output trivy-results.json \
                ${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER}
        '''
    }
    post {
        always {
            archiveArtifacts artifacts: 'trivy-results.json', allowEmptyArchive: true
        }
    }
}
```

---

## S3 + CloudFront

React/Vite 等の静的サイトを AWS S3 にデプロイし、CloudFront のキャッシュを無効化する。

### GitLab CI

```yaml
deploy-s3:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: staging
    url: https://${CF_DOMAIN_STAGING}
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - aws s3 sync dist/ s3://${S3_BUCKET}/ --delete --region ${AWS_REGION}
    - aws cloudfront create-invalidation
        --distribution-id ${CF_DIST_ID}
        --paths "/*"

deploy-s3-production:
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
    - aws cloudfront create-invalidation
        --distribution-id ${CF_DIST_ID_PROD}
        --paths "/*"
```

### Jenkins

```groovy
stage('Deploy to S3') {
    when { branch 'main' }
    steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                          credentialsId: 'aws-credentials']]) {
            sh """
                aws s3 sync dist/ s3://${S3_BUCKET}/ --delete --region ${AWS_REGION}
                aws cloudfront create-invalidation \
                    --distribution-id ${CF_DIST_ID} --paths "/*"
            """
        }
    }
}
```

---

## AWS ECS

Docker コンテナアプリを AWS ECS (Fargate) にデプロイする。ECR のイメージタグを更新してサービスを再起動。

### GitLab CI

```yaml
.ecs-deploy:
  image: amazon/aws-cli
  script:
    - |
      TASK_DEF=$(aws ecs describe-task-definition \
        --task-definition ${ECS_TASK_FAMILY} \
        --query taskDefinition --output json)
      NEW_TASK_DEF=$(echo $TASK_DEF | jq \
        --arg IMAGE "${ECR_REGISTRY}/${ECR_REPO}:${IMAGE_TAG}" \
        '.containerDefinitions[0].image = $IMAGE
         | del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy)')
      TASK_ARN=$(aws ecs register-task-definition \
        --cli-input-json "$NEW_TASK_DEF" \
        --query taskDefinition.taskDefinitionArn --output text)
      aws ecs update-service \
        --cluster ${ECS_CLUSTER} \
        --service ${ECS_SERVICE} \
        --task-definition ${TASK_ARN}
      aws ecs wait services-stable \
        --cluster ${ECS_CLUSTER} \
        --services ${ECS_SERVICE}

deploy-ecs-staging:
  extends: .ecs-deploy
  stage: deploy
  environment:
    name: staging
  variables:
    IMAGE_TAG: ${CI_COMMIT_SHORT_SHA}
    ECS_CLUSTER: my-cluster-staging
    ECS_SERVICE: my-service-staging
  rules:
    - if: $CI_COMMIT_BRANCH == "main"

deploy-ecs-production:
  extends: .ecs-deploy
  stage: deploy
  environment:
    name: production
  variables:
    IMAGE_TAG: ${CI_COMMIT_SHORT_SHA}
    ECS_CLUSTER: my-cluster-prod
    ECS_SERVICE: my-service-prod
  rules:
    - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/
      when: manual
```

### Jenkins

```groovy
stage('Deploy to ECS') {
    when { branch 'main' }
    steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                          credentialsId: 'aws-credentials']]) {
            sh '''
                TASK_DEF=$(aws ecs describe-task-definition \
                    --task-definition ${ECS_TASK_FAMILY} \
                    --query taskDefinition --output json)
                NEW_TASK_DEF=$(echo $TASK_DEF | jq \
                    --arg IMAGE "${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER}" \
                    '.containerDefinitions[0].image = $IMAGE
                     | del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy)')
                TASK_ARN=$(aws ecs register-task-definition \
                    --cli-input-json "$NEW_TASK_DEF" \
                    --query taskDefinition.taskDefinitionArn --output text)
                aws ecs update-service \
                    --cluster ${ECS_CLUSTER} \
                    --service ${ECS_SERVICE} \
                    --task-definition ${TASK_ARN}
                aws ecs wait services-stable \
                    --cluster ${ECS_CLUSTER} \
                    --services ${ECS_SERVICE}
            '''
        }
    }
}
```

---

## AWS Lambda

サーバーレス関数を ZIP パッケージとして Lambda にデプロイする。

### GitLab CI

```yaml
deploy-lambda:
  stage: deploy
  image: amazon/aws-cli
  environment:
    name: staging
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - zip -r function.zip . -x "*.git*" "node_modules/.cache/*"
    - aws lambda update-function-code
        --function-name ${LAMBDA_FUNCTION_NAME}
        --zip-file fileb://function.zip
        --region ${AWS_REGION}
    - aws lambda wait function-updated
        --function-name ${LAMBDA_FUNCTION_NAME}
    - aws lambda publish-version
        --function-name ${LAMBDA_FUNCTION_NAME}
```

### Jenkins

```groovy
stage('Deploy to Lambda') {
    when { branch 'main' }
    steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                          credentialsId: 'aws-credentials']]) {
            sh '''
                zip -r function.zip . -x "*.git*"
                aws lambda update-function-code \
                    --function-name ${LAMBDA_FUNCTION_NAME} \
                    --zip-file fileb://function.zip \
                    --region ${AWS_REGION}
                aws lambda wait function-updated \
                    --function-name ${LAMBDA_FUNCTION_NAME}
                aws lambda publish-version \
                    --function-name ${LAMBDA_FUNCTION_NAME}
            '''
        }
    }
}
```

---

## Slack 通知

パイプラインの成功・失敗を Slack に通知する。

### GitLab CI

```yaml
notify-slack-failure:
  stage: .post
  image: curlimages/curl:latest
  when: on_failure
  script:
    - |
      curl -s -X POST "${SLACK_WEBHOOK_URL}" \
        -H 'Content-Type: application/json' \
        -d "{
          \"text\": \":x: *${CI_PROJECT_NAME}* pipeline failed on \`${CI_COMMIT_REF_NAME}\`\n<${CI_PIPELINE_URL}|View Pipeline>\"
        }"

notify-slack-success:
  stage: .post
  image: curlimages/curl:latest
  when: on_success
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - |
      curl -s -X POST "${SLACK_WEBHOOK_URL}" \
        -H 'Content-Type: application/json' \
        -d "{
          \"text\": \":white_check_mark: *${CI_PROJECT_NAME}* deployed successfully from \`${CI_COMMIT_REF_NAME}\`\"
        }"
```

### Jenkins (Slack Notification Plugin)

```groovy
post {
    failure {
        slackSend(
            channel: '#ci-alerts',
            color: 'danger',
            message: ":x: *${env.JOB_NAME}* #${env.BUILD_NUMBER} failed on `${env.BRANCH_NAME}`\n<${env.BUILD_URL}|View Build>"
        )
    }
    success {
        slackSend(
            channel: '#ci-alerts',
            color: 'good',
            message: ":white_check_mark: *${env.JOB_NAME}* #${env.BUILD_NUMBER} succeeded"
        )
    }
}
```

---

## ECR Login

ECR へのログイン処理を共通化する。

### GitLab CI

```yaml
# 各ジョブの before_script で参照
.ecr-login:
  before_script:
    - aws ecr get-login-password --region ${AWS_REGION} |
        docker login --username AWS --password-stdin ${ECR_REGISTRY}

# 使用例
build-push:
  extends: .ecr-login
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t ${ECR_REGISTRY}/${ECR_REPO}:${CI_COMMIT_SHORT_SHA} .
    - docker push ${ECR_REGISTRY}/${ECR_REPO}:${CI_COMMIT_SHORT_SHA}
```

### Jenkins

```groovy
def ecrLogin() {
    withCredentials([[$class: 'AmazonWebServicesCredentialsBinding',
                      credentialsId: 'aws-credentials']]) {
        sh """
            aws ecr get-login-password --region ${AWS_REGION} | \
                docker login --username AWS --password-stdin ${ECR_REGISTRY}
        """
    }
}

// 使用例
stage('Build & Push') {
    steps {
        script { ecrLogin() }
        sh "docker build -t ${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER} ."
        sh "docker push ${ECR_REGISTRY}/${ECR_REPO}:${BUILD_NUMBER}"
    }
}
```

---

## 必要な GitLab CI Variables / Jenkins Credentials

| 変数名 | 説明 | 機密 |
|--------|------|------|
| `AWS_REGION` | AWS リージョン (例: `ap-northeast-1`) | No |
| `AWS_ACCOUNT_ID` | AWS アカウント ID | Yes |
| `ECR_REGISTRY` | ECR レジストリ URL | No |
| `ECR_REPO` | ECR リポジトリ名 | No |
| `ECS_CLUSTER` | ECS クラスター名 | No |
| `ECS_SERVICE` | ECS サービス名 | No |
| `ECS_TASK_FAMILY` | ECS タスク定義ファミリー名 | No |
| `S3_BUCKET` | S3 バケット名 | No |
| `CF_DIST_ID` | CloudFront ディストリビューション ID | No |
| `LAMBDA_FUNCTION_NAME` | Lambda 関数名 | No |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL | Yes |
