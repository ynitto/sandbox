# AWSアーキテクチャ図 レイアウトガイドライン

## 基本原則

1. **1図あたり最大15〜20アイコン** — 図を集中させて読みやすくする
2. **主要なデータフローは水平軸**（左から右）
3. **補助サービスはメインフローの上下に配置**
4. **ラベルはデフォルト日本語**
5. **ラベルはプレーンテキストのみ** — HTMLは使わない
6. **コンパクトレイアウト必須** — 不要な余白を排除し、グループはコンテンツにぴったり合わせる

## タイポグラフィ

- **アイコンラベル**: 12px、fontColor=#232F3E
- **エッジラベル**: 10px
- **グループラベル**: 12px、太字（fontStyle=1）
- **ラベル位置**: アイコンの下（`verticalLabelPosition=bottom;verticalAlign=top;`）

## アイコンサイズ

- **サービスアイコン（resourceIcon）**: 48×48
- **リソースアイコン（専用シェイプ）**: 48×48
- **ラベル込みの実効高さ**: 48（アイコン）+ 4（隙間）+ 16（ラベル）= 68px — 縦方向のスペーシング計算に使う
- **グループ最小サイズ**: 幅130×高さ110

## ネスト順序（外側→内側）

1. AWS Cloud グループ
2. Region グループ
3. VPC グループ
4. Subnet グループ（パブリック/プライベート）
5. 個別リソース

## スペーシング（コンパクト — 以下の数値を厳守し、上限を超えない）

- **アイコン間隔**: 水平70px、垂直50px
- **ラベルのクリアランス**: 下ラベルのアイコンは重なりを防ぐため縦方向に20px追加（アイコン48px＋ラベル約16px＝実効高さ64px）
- **グループパディング**: グループ境界からアイコンまで20px（上部はグループタイトルを避けるため40px）
- **グループ間隔**: 30px
- **グループサイズ**: コンテンツから逆算 — `幅 = 左パディング + アイコン + 隙間 + 右パディング`、余分な空白を加えない

### 間延び防止ルール
- 「念のため」の余分なパディングを追加しない — 測って合わせる
- グループ内に空行・空列を残さない
- 視覚的な整列目的がない限り、兄弟グループに合わせてグループを広げない
- アイコン同士・ラベル同士が重ならないこと — 上記の間隔を必ず維持する

## エッジ（矢印）ルール

- 色: `strokeColor=#545B64`
- スタイル: `edgeStyle=orthogonalEdgeStyle`
- 角は `rounded=0` でシャープに
- データフローの説明が役立つ場合はエッジにラベルを付ける
- エッジラベルスタイル: `fontSize=10;fontColor=#545B64;`

## よく使うレイアウトパターン

### 三層構成
```
[ユーザー] → [CloudFront/ALB] → [EC2/Lambda] → [RDS/DynamoDB]
```

### イベント駆動
```
[ソース] → [EventBridge/SNS/SQS] → [Lambda] → [ターゲット]
```

### データパイプライン
```
[ソース] → [Kinesis/S3] → [Lambda/Glue] → [S3/Redshift] → [QuickSight]
```

### RAGアーキテクチャ（閉域網 + Transit Gateway）
```
取り込み: [EC2] → [Transit GW] → [データソース] → [S3] → [Bedrock KB] → [OpenSearch]
検索:     [ユーザー] → [VPN] → [EC2] ↔ [Bedrock KB] ↔ [OpenSearch]、[EC2] ↔ [Bedrock Claude]
```

## PNG書き出しの注意

- **全体背景を必ず追加**: タイトル・図・凡例の後ろにライトグレー `#F5F5F5` の角丸rectを配置する。これがないとグループ外のコンテンツでPNG書き出し時に黒背景が出る
- 背景スタイル: `rounded=1;whiteSpace=wrap;fillColor=#F5F5F5;strokeColor=#E0E0E0;arcSize=2;`
- 凡例とタイトルは背景rectの内側に入れる（外に浮かせない）

## マルチフロー図（スイムレーン）

複数の明確なフローがある場合:

1. **AWS Cloudグループは1つ** — 全フローにまたがる（フローごとに分けない）
2. **レーンヘッダー**にステップバイステップのサマリーを入れる: `"① チケット取得 → ② データ保存 → ③ AI変換 → ④ 索引化"`
3. **縦の破線で区切る** — 色付きブロックの分割は使わない
4. **ステップ番号付きエッジ** — 技術ラベルの代わりにサークル数字（① ② ③ または ❶ ❷ ❸）を使う
5. フローごとに異なる丸スタイルで視覚的に区別する（白丸 vs 黒丸）

### レーンヘッダースタイル
```
rounded=1;whiteSpace=wrap;fillColor=#DBEAFE;strokeColor=none;fontColor=#1E40AF;fontSize=13;fontStyle=1;verticalAlign=top;spacingTop=8;
```

### 破線区切りスタイル
```
strokeColor=#94A3B8;strokeWidth=1;dashed=1;dashPattern=8 4;
```

## マネージドサービスとVPCエンドポイントのレイアウト

VPCリソースがVPCエンドポイント経由でAWSマネージドサービスにアクセスする場合:

### 2ボックス構成
```
┌─── AWS Cloud ───────────────────────────────────────────────────┐
│                                                                  │
│  ┌─── VPC ──────────────┐  🔌  ┌─── マネージドサービス ────────┐ │
│  │ EC2 など              │ VPC  │ S3, Bedrock KB, OpenSearch  │ │
│  │ （ユーザーデプロイ）  │ EP   │ （AWSマネージド）            │ │
│  └───────────────────────┘      └─────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

- **VPCボックス**: サブネットとユーザーデプロイリソースを含む標準VPCグループ
- **マネージドサービスボックス**: VPC外のAWSマネージドサービスを入れる破線ボーダーグループ
- **VPCエンドポイントアイコン**: 2つのボックスの境界に配置
- 矢印はソースからターゲットへ直接引く — VPCエンドポイントアイコンを経由させない
- 視覚的な配置だけで「VPC境界を越える」ことが伝わる

### マネージドサービスグループスタイル
```
rounded=1;whiteSpace=wrap;fillColor=none;strokeColor=#879196;strokeWidth=1;dashed=1;dashPattern=4 4;fontColor=#232F3E;fontSize=12;fontStyle=1;verticalAlign=top;align=left;spacingLeft=10;spacingTop=8;container=1;collapsible=0;
```

### マネージドサービスグループのID規則
- グループ: `grp-managed`
- 内部アイコン: `svc-s3`、`svc-bedrock-kb`、`svc-opensearch` など

## コンパクトレイアウトの計算例

3アイコンを横一列に並べたサブネットの場合:
```
左パディング(20) + アイコン(48) + 隙間(70) + アイコン(48) + 隙間(70) + アイコン(48) + 右パディング(20) = 幅324px
上パディング(40) + アイコン(48) + ラベルクリアランス(20) + 下パディング(20) = 高さ128px
```

2つのサブネットを横に並べたVPCの場合:
```
左パディング(20) + サブネット幅 + 隙間(30) + サブネット幅 + 右パディング(20)
上パディング(40) + サブネット高さ + 下パディング(20)
```

グループのサイズは常にコンテンツから逆算する — 推測や余分なスペースの追加は禁止。

## セルIDの規則

保守性のために説明的なIDを使う:
- グループ: `grp-cloud`、`grp-region`、`grp-vpc`、`grp-subnet-pub`、`grp-subnet-priv`
- アイコン: `svc-lambda`、`svc-s3`、`svc-bedrock` など
- エッジ: `edge-1`、`edge-2` など

## XML構造テンプレート

```xml
<mxCell id="svc-lambda" value="Lambda" style="...シェイプスタイル..."
        vertex="1" parent="grp-subnet-priv">
  <mxGeometry x="100" y="50" width="48" height="48" as="geometry" />
</mxCell>
```

## スタイルテンプレート早見表

### サービスアイコン（resourceIconパターン）
```
sketch=0;points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]];outlineConnect=0;fontColor=#232F3E;fillColor=<カテゴリ色>;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.<サービスアイコン名>;
```

### 専用シェイプ（リソースレベル）
```
sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor=<カテゴリ色>;strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;pointerEvents=1;shape=mxgraph.aws4.<シェイプ名>;
```

### グループコンテナ
```
sketch=0;outlineConnect=0;fontColor=#232F3E;fontStyle=0;container=1;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.<グループアイコン名>;...
```

### エッジ
```
edgeStyle=orthogonalEdgeStyle;html=1;endArrow=block;elbow=vertical;startArrow=none;endFill=1;strokeColor=#545B64;rounded=0;fontSize=10;fontColor=#545B64;
```
