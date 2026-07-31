# Mermaid 記法ミニリファレンス

各図タイプの最小テンプレートと頻出パターン。詳細は公式（https://mermaid.js.org/）参照。

## 目次

- [シーケンス図](#シーケンス図)
- [ER図](#er図)
- [フローチャート](#フローチャート)
- [状態遷移図](#状態遷移図)
- [ガントチャート](#ガントチャート)
- [クラス図（簡易・DDD は domain-modeler 優先）](#クラス図簡易ddd-は-domain-modeler-優先)
- [マインドマップ](#マインドマップ)
- [よくあるエラー回避](#よくあるエラー回避)

## シーケンス図

```mermaid
sequenceDiagram
    autonumber
    actor U as ユーザー
    participant FE as フロント
    participant API as APIサーバー
    participant DB as DB
    U->>FE: ログイン入力
    FE->>API: POST /login
    API->>DB: ユーザー照会
    DB-->>API: ユーザー情報
    alt 認証成功
        API-->>FE: 200 + トークン
    else 認証失敗
        API-->>FE: 401
    end
    FE-->>U: 結果表示
```

- `->>` 同期, `-->>` 戻り（破線）, `-)` 非同期
- 制御: `alt/else/end`, `opt/end`, `loop/end`, `par/and/end`
- `Note over A,B: 補足`

## ER図

```mermaid
erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ ORDER_ITEM : contains
    PRODUCT ||--o{ ORDER_ITEM : "ordered in"
    CUSTOMER {
        int id PK
        string name
        string email
    }
    ORDER {
        int id PK
        int customer_id FK
        datetime ordered_at
    }
```

- 基数: `||`(1) `o{`(0..多) `|{`(1..多) `o|`(0..1)
- 例: `A ||--o{ B` = A 1 に対し B 0個以上

## フローチャート

```mermaid
flowchart TD
    Start([開始]) --> Input[/入力受付/]
    Input --> Check{バリデーションOK?}
    Check -->|Yes| Save[(DB保存)]
    Check -->|No| Error[エラー表示]
    Save --> End([終了])
    Error --> Input
```

- 方向: `TD`(上→下) `LR`(左→右)
- 形状: `[]`処理 `()` 角丸 `([])`端点 `{}`判断 `[()]`DB `[//]`入出力
- `subgraph 名称 ... end` でグループ化

## 状態遷移図

```mermaid
stateDiagram-v2
    [*] --> 未着手
    未着手 --> 進行中: 着手
    進行中 --> レビュー: 提出
    レビュー --> 進行中: 差し戻し
    レビュー --> 完了: 承認
    完了 --> [*]
```

## ガントチャート

```mermaid
gantt
    title プロジェクト計画
    dateFormat YYYY-MM-DD
    section 設計
    要件定義      :done,    a1, 2026-06-01, 5d
    基本設計      :active,  a2, after a1, 7d
    section 実装
    実装          :         a3, after a2, 14d
    テスト        :         a4, after a3, 7d
```

- `done`/`active`/`crit` でステータス, `after <id>` で依存

## クラス図（簡易・DDD は domain-modeler 優先）

```mermaid
classDiagram
    class Order {
        +int id
        +addItem(item) void
        +total() Money
    }
    Order "1" --> "*" OrderItem
    Order ..|> Aggregate
```

- 関係: `<|--`継承, `*--`コンポジション, `o--`集約, `-->`関連, `..|>`実現

## マインドマップ

```mermaid
mindmap
  root((新機能))
    UI
      画面遷移
      コンポーネント
    API
      認証
      データ取得
    非機能
      性能
      セキュリティ
```

## よくあるエラー回避

- ラベルに `()` `:` `,` を含むときは `"..."` で囲む: `A["処理 (重要)"]`
- 日本語ノード ID は避け、ID は英数字・表示名はラベルで指定する
- 予約語（`end` 等）を素の ID に使わない
- インデント崩れに注意（特に `mindmap`/`gantt` は階層をスペースで表現）
