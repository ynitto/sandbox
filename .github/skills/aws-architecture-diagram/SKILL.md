---
name: aws-architecture-diagram
description: Draw.io XML形式のAWSアーキテクチャ図を生成するスキル。「AWS構成図を作って」「アーキテクチャ図を描いて」「Draw.ioのAWS図を生成して」「構成図を書いて」などで発動する。正確なAWSサービスアイコンを使用してプロフェッショナルな図を作成する。日本語・Windows・GitHub Copilot対応。
metadata:
  version: 1.0.0
  tier: stable
  category: diagram
  tags:
    - aws
    - architecture
    - drawio
    - diagram
---

# aws-architecture-diagram

Draw.io XML形式のAWSアーキテクチャ図を、正確なAWSサービスアイコンで生成する。

## ワークフロー

### Step 1: リクエストを理解して確認する

- 必要なAWSサービスを特定する
- アーキテクチャパターンを判断する（三層構成、イベント駆動、データパイプライン、RAGなど）
- **対象読者を判断する**: 技術者向け（詳細ラベル、プロトコル名）vs 非技術者向け（ステップ番号、簡易説明）
- **ユーザーに確認する**:
  - **言語**: 日本語（デフォルト）または英語
  - その他リクエストから不明な点

### Step 2: アイコンを参照する

以下のリファレンスファイルを読み、正確なシェイプ名と色を取得する:

- `references/aws-icons-compute.md` — EC2, Lambda, ECS, EKS, Fargate, ELB
- `references/aws-icons-storage-database.md` — S3, EBS, RDS, DynamoDB, Aurora, ElastiCache
- `references/aws-icons-networking.md` — VPC, CloudFront, Route 53, API Gateway, Direct Connect
- `references/aws-icons-app-integration.md` — SNS, SQS, EventBridge, Step Functions, CloudWatch, CloudFormation
- `references/aws-icons-analytics-ml.md` — Athena, Glue, Kinesis, OpenSearch, Bedrock, SageMaker
- `references/aws-icons-security.md` — IAM, Cognito, WAF, Shield, KMS, GuardDuty
- `references/aws-icons-common.md` — Users, servers, internet, groups, arrows

**重要**: XMLを生成する前に必ずリファレンスファイルでアイコンを確認すること。アイコン名は絶対に推測しない。

### Step 3: レイアウトを計画する

`references/layout-guidelines.md` でスペーシング・ネスト・スタイルルールを確認する。

レイアウトの主な判断事項:
- グループのネスト: AWS Cloud → Region → VPC → Subnet
- 主要なフロー方向（通常は左から右）
- 補助サービスの配置

### Step 4: Draw.io XMLを生成する

`templates/base.drawio.xml` をスケルトンとして使用する。以下のパターンでXMLを構築する:

#### サービスアイコン（カラー背景・白グリフ）

```xml
<mxCell id="svc-lambda" value="Lambda" style="sketch=0;points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]];outlineConnect=0;fontColor=#232F3E;fillColor=#ED7100;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;" vertex="1" parent="grp-subnet">
  <mxGeometry x="100" y="50" width="48" height="48" as="geometry" />
</mxCell>
```

**必須**: resourceIconパターンには `strokeColor=#ffffff`（グリフを白くする）。

#### 専用シェイプ（ダークシルエット）

```xml
<mxCell id="res-lambda-fn" value="Lambda Function" style="sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor=#ED7100;strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;pointerEvents=1;shape=mxgraph.aws4.lambda_function;" vertex="1" parent="grp-subnet">
  <mxGeometry x="100" y="50" width="48" height="48" as="geometry" />
</mxCell>
```

#### グループコンテナ

```xml
<mxCell id="grp-vpc" value="VPC" style="sketch=0;outlineConnect=0;fontColor=#232F3E;fontStyle=0;container=1;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=none;verticalAlign=top;align=left;spacingLeft=30;fontColor=#AAB7B8;dashed=0;" vertex="1" parent="grp-region">
  <mxGeometry x="30" y="40" width="800" height="400" as="geometry" />
</mxCell>
```

#### エッジ（矢印）

```xml
<mxCell id="edge-1" value="HTTPS" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=block;elbow=vertical;startArrow=none;endFill=1;strokeColor=#545B64;rounded=0;fontSize=10;fontColor=#545B64;" edge="1" source="svc-cloudfront" target="svc-apigw" parent="1">
  <mxGeometry relative="1" as="geometry" />
</mxCell>
```

### Step 5: 出力する

図の内容を最もよく表す**説明的なkebab-caseスラッグ**を選ぶ（例: `realtime-data-pipeline`, `event-driven-orders`, `multi-tier-web-app`）。

#### 5a. Draw.ioファイルを書き出す

- `docs/<slug>.drawio` に書き出す（例: `docs/realtime-data-pipeline.drawio`）
- **Windows環境**: パスは `docs\<slug>.drawio` でも可（Draw.ioはどちらも対応）

#### 5b. コンパニオンガイドを書き出す

- `docs/<slug>.md` に書き出す
- 以下の内容を含める:
  - **概要**: このアーキテクチャが何をするかの1〜2文のまとめ
  - **コンポーネント一覧**: 図内の各AWSサービス・その役割・選択理由のテーブル
  - **データフロー**: 各フローのステップバイステップの説明（非技術者向けモードの場合はステップ番号に対応）
  - **主要な設計判断**: アーキテクチャの選択に関する簡単なメモ（例: サーバーレスにした理由、このDBを選んだ理由）
  - **コスト・スケーリングのメモ**（任意）: 計画に役立つ情報
- ガイドはユーザーが選んだ言語（デフォルト: 日本語）で書く
- ユーザーに両方のファイルパスを伝え、`.drawio` をDraw.ioアプリ（デスクトップまたは https://app.diagrams.net）で開くよう案内する

## ルール

1. **アイコンは必ずリファレンスで確認** — XMLを生成する前にリファレンスファイルを参照する
2. **resourceIconパターンには `strokeColor=#ffffff`** — サービスレベルアイコン用
3. **専用シェイプには `strokeColor=none`** — リソースレベルアイコン用
4. **fillColorはカテゴリに合わせる** — カテゴリ間で色を混ぜない
5. **デフォルト言語は日本語** — ラベル・タイトル・凡例・コンパニオンガイドで一貫して使用
6. **ラベルにHTMLは使わない** — プレーンテキストのみ
7. **1図あたり最大15〜20アイコン** — 可読性のため
8. **エッジの色**: `#545B64`（AWSデフォルトグレー）
9. **フォント**: アイコンラベル12px、エッジラベル10px
10. **アイコンサイズ**: サービス・リソースアイコンとも48×48
11. **AWS Cloudグループは1つ** — 図全体にわたる1つのAWS Cloudグループを使用する
12. **全体背景** — タイトル・レーン・凡例の後ろにライトグレー `#F5F5F5` の角丸rectを追加して、PNG書き出し時に黒背景が出ないようにする

## 非技術者向けモード

図の対象が非技術者（管理職・ステークホルダー・エンドユーザー）の場合:

### ステップ番号付きエッジ
技術的なラベル（HTTPS、REST API等）をサークル番号に置き換える:
- フローA: ① ② ③ ④（白丸）
- フローB: ❶ ❷ ❸ ❹（黒丸）
- フローごとに異なる丸スタイルで視覚的に区別する

### 簡易ラベル
- 技術用語の代わりに平易な日本語の説明を使う
- 例: 「REST API呼び出し」→「チケット取得」、「チャンク分割・埋め込み」→「AI学習用に変換」
- 技術的な修飾語を省く: 「OpenSearch Serverless」→「OpenSearch」、「Site-to-Site VPN」→「VPN接続」

### フローサマリー付きレーンレイアウト
複数のフロー（データ処理+検索など）がある場合はスイムレーンを使う:
- 各レーンヘッダーにステップバイステップのサマリーを追加: 例 `"① チケット取得 → ② データ保存 → ③ AI変換 → ④ 索引化"`
- レーン間の区切りは色付きブロックではなく縦の破線を使う
- 凡例は色付きレーンの外に配置する

## マネージドサービスとVPCエンドポイントパターン

VPCからAWSマネージドサービス（S3, Bedrock, OpenSearchなど）にアクセスする構成の場合:

### 2ボックス構成
- **VPCボックス**（左）: ユーザーがデプロイするリソース（EC2, Lambda, NAT Gatewayなど）
- **マネージドサービスボックス**（右）: VPC外にあるAWSマネージドサービス（S3, Bedrock KB, OpenSearch, Bedrock Claudeなど）の角丸rectグループ
- **VPCエンドポイントアイコン**: 2つのボックスの境界に配置 — VPCとマネージドサービスの「橋」を視覚的に表現
- 両方のボックスは単一のAWS Cloudグループ内に配置

### マネージドサービスグループスタイル
```
rounded=1;whiteSpace=wrap;fillColor=none;strokeColor=#879196;strokeWidth=1;dashed=1;dashPattern=4 4;fontColor=#232F3E;fontSize=12;fontStyle=1;verticalAlign=top;align=left;spacingLeft=10;spacingTop=8;container=1;collapsible=0;
```

### 矢印のルーティング
- 矢印はソースからターゲットへ**直接**引く（例: Lambda → S3, EC2 → Bedrock KB） — VPCエンドポイントアイコンを経由させない
- 2ボックス構成とアイコンの視覚的配置だけで「VPC境界をVPCエンドポイント経由で越える」ことが伝わる
- コンパニオンガイドでVPCエンドポイントの詳細を説明する

## カテゴリカラーリファレンス

| カテゴリ | fillColor |
|---|---|
| Compute & Containers | `#ED7100` |
| Storage | `#7AA116` |
| Database | `#C925D1` |
| Networking & CDN | `#8C4FFF` |
| Analytics | `#8C4FFF` |
| App Integration & Mgmt | `#E7157B` |
| AI / Machine Learning | `#01A88D` |
| Security | `#DD344C` |
| General | `#232F3E` |

## Windows環境での注意事項

- Draw.ioデスクトップアプリ（Windows）でファイルを開く場合: `docs\<slug>.drawio` をダブルクリックで開ける
- ファイルパスにスペースが含まれる場合はダブルクォートで囲む
- Draw.ioオンライン版（https://app.diagrams.net）でも同じXMLが使用可能
- PNG書き出し: Draw.ioの「ファイル > エクスポート > PNG」から書き出す（全体背景が設定されていれば黒背景は出ない）

## GitHub Copilot利用時の注意事項

- このスキルはGitHub Copilot（VS Code拡張）でも利用可能
- Copilotチャットで「AWS構成図を作って」と入力すると本スキルが発動する
- リファレンスファイルへのアクセスが必要なためワークスペースルートに `.github/skills/aws-architecture-diagram/` が存在すること
- 生成されたXMLは `docs/` ディレクトリに保存される（Windowsの場合も `docs/` フォルダとして認識される）
