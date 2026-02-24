# GitHub Actions YAML スニペット集

## 目次

1. [再利用可能ワークフロー (Reusable Workflow)](#reusable-workflow)
2. [依存関係スキャン](#dependency-scan)
3. [セキュリティスキャン (Trivy)](#trivy)
4. [GitHub Pages デプロイ](#github-pages)
5. [AWS ECS デプロイ](#aws-ecs)
6. [Slack 通知](#slack-notify)
7. [PR ラベル自動付与](#pr-label)
8. [Dependabot 設定](#dependabot)

---

## Reusable Workflow

`.github/workflows/ci-base.yml`:

```yaml
on:
  workflow_call:
    inputs:
      node-version:
        type: string
        default: "20"
    secrets:
      NPM_TOKEN:
        required: false

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ inputs.node-version }}
          cache: npm
      - run: npm ci
      - run: npm test
```

呼び出し元:

```yaml
jobs:
  call-ci:
    uses: ./.github/workflows/ci-base.yml
    with:
      node-version: "22"
    secrets: inherit
```

---

## Dependency Scan

```yaml
- name: npm audit
  run: npm audit --audit-level=high

- name: pip-audit
  run: |
    pip install pip-audit
    pip-audit -r requirements.txt
```

---

## Trivy

```yaml
- uses: aquasecurity/trivy-action@master
  with:
    image-ref: ghcr.io/${{ github.repository }}:${{ github.sha }}
    format: sarif
    output: trivy-results.sarif
    severity: HIGH,CRITICAL

- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: trivy-results.sarif
```

---

## GitHub Pages デプロイ

```yaml
deploy-pages:
  needs: ci
  runs-on: ubuntu-latest
  permissions:
    pages: write
    id-token: write
  environment:
    name: github-pages
    url: ${{ steps.deployment.outputs.page_url }}
  steps:
    - uses: actions/configure-pages@v5
    - uses: actions/upload-pages-artifact@v3
      with:
        path: dist/
    - id: deployment
      uses: actions/deploy-pages@v4
```

---

## AWS ECS デプロイ

```yaml
deploy-ecs:
  needs: build-push
  runs-on: ubuntu-latest
  environment: production
  steps:
    - uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/github-actions
        aws-region: ap-northeast-1
    - id: task-def
      uses: aws-actions/amazon-ecs-render-task-definition@v1
      with:
        task-definition: .aws/task-definition.json
        container-name: app
        image: ghcr.io/${{ github.repository }}:${{ github.sha }}
    - uses: aws-actions/amazon-ecs-deploy-task-definition@v2
      with:
        task-definition: ${{ steps.task-def.outputs.task-definition }}
        service: my-service
        cluster: my-cluster
        wait-for-service-stability: true
```

---

## Slack 通知

```yaml
- name: Notify Slack on failure
  if: failure()
  uses: slackapi/slack-github-action@v2
  with:
    webhook: ${{ secrets.SLACK_WEBHOOK_URL }}
    webhook-type: incoming-webhook
    payload: |
      {
        "text": ":x: *${{ github.workflow }}* failed on `${{ github.ref_name }}`\n<${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}|View Run>"
      }
```

---

## PR ラベル自動付与

`.github/workflows/labeler.yml`:

```yaml
name: Label PRs

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  label:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: actions/labeler@v5
        with:
          repo-token: ${{ secrets.GITHUB_TOKEN }}
```

`.github/labeler.yml`:

```yaml
frontend:
  - changed-files:
    - any-glob-to-any-file: src/**/*

ci:
  - changed-files:
    - any-glob-to-any-file: .github/**/*
```

---

## Dependabot 設定

`.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
    groups:
      dev-dependencies:
        dependency-type: development
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```
