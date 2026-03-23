---
name: domain-modeler
description: DDD に基づくドメインモデルを設計し Mermaid classDiagram として出力する。また既存コードからドメインモデルを逆引き抽出するリバースエンジニアリングも行う。「ドメインモデルを設計して」「DDDで設計して」「集約を設計して」「境界コンテキストを整理して」「ドメインモデル図を作って」「クラス図を描いて」「Entityとバリューオブジェクトを整理して」「ユビキタス言語をまとめて」などで発動（設計モード）。「既存コードからドメインモデルを抽出して」「コードを解析してクラス図を作って」「リバースエンジニアリングして」「実装からドメインを整理して」などで発動（逆引きモード）。DDD・非DDD 両対応。
metadata:
  version: 2.0.0
  tier: experimental
  category: design
  tags:
    - ddd
    - mermaid
    - entity
    - aggregate
    - reverse-engineering
---

# domain-modeler

DDD（Domain-Driven Design）に基づいてドメインモデルを設計し、Mermaid `classDiagram` として出力するスキル。
**2つのモード**を持つ:

| モード | 方向 | 用途 |
|--------|------|------|
| **設計モード** | ドメイン知識 → Mermaid 図 | 新規システム・ドメイン再設計 |
| **逆引きモード** | 既存コード → Mermaid 図 + DDD 評価 | リファクタ計画・技術負債可視化 |

---

## モード判定

```
ユーザーの依頼を確認:
  コード/ファイル/ディレクトリを指定している？
    YES → 逆引きモード（Reverse Engineering Mode）へ
    NO  → 「設計からですか？既存コードからですか？」と確認
  既存コードに言及している（「解析」「抽出」「既存」「実装から」）？
    YES → 逆引きモード（Reverse Engineering Mode）へ
    NO  → 設計モード（Design Mode）へ
```

---

## 【設計モード】Design Mode

## 設計フロー

```
Step 1: ドメイン理解         ← ビジネスドメイン・ユースケース・ユビキタス言語
Step 2: 戦略的設計           ← Bounded Context・Context Map
Step 3: 戦術的設計           ← Entity / Value Object / Aggregate
Step 4: ドメインサービス特定  ← Entity/VOに収まらない操作
Step 5: ドメインイベント設計  ← 集約間通信・副作用
Step 6: 図として表現         ← Mermaid classDiagram
```

---

### Step 1: ドメイン理解

以下を確認する（不明な場合はユーザーに質問する）:

- ビジネスドメインの概要（ECサイト・予約システム・医療など）
- 主要なユースケース（3〜5個）
- DDD 採用有無（集約・境界コンテキストの厳密な適用が必要か）
- 既存ドキュメント（要件定義書など）があれば読み込んで活用する

ユーザーの説明からドメイン語彙を抽出する:

| 抽出元 | 候補の種類 |
|--------|-----------|
| 名詞 | Entity / Value Object / Aggregate |
| 動詞・出来事 | Domain Event / Domain Service |
| 「〜は〜でなければならない」 | 不変条件（Invariant） |

**原則**: ドメインエキスパートが使う言葉をそのまま使う。技術用語（`UserRecord`、`OrderDTO` など）に翻訳しない。

### Step 2: 戦略的設計

Bounded Context と Context Map を設計する:

- 同じ言葉が異なる意味を持つ場所に Bounded Context の境界を引く
- Context Map でコンテキスト間の連携パターンを選択する（ACL・OHS・Customer-Supplier など）
- Core / Supporting / Generic サブドメインを分類し、投資優先度を判断する

詳細 → [references/bounded-context.md](references/bounded-context.md)

### Step 3: 戦術的設計

各候補を以下の基準で分類し、表形式でユーザーに確認を取る:

| クラス名 | 分類 | 理由 |
|---------|------|------|
| Order | Aggregate Root | 注文ライフサイクル全体を管理、外部から参照される |
| OrderItem | Entity | 注文内で ItemId で識別される |
| Money | Value Object | 金額+通貨の組み合わせで定義、不変 |
| Address | Value Object | 配送先は属性値で識別、交換可能 |

各クラス間の関係を決める際に確認すること:

1. **ライフサイクルは共有されるか**（コンポジション `*--` vs 関連 `-->`）
2. **参照の方向**: 双方向は本当に必要か（単方向を優先する）
3. **多重度**: 1対1 / 1対多 / 多対多
4. **集約間の参照**: 集約境界を越える参照は ID のみ（オブジェクト直接参照不可）

詳細 → [references/core-concepts.md](references/core-concepts.md) / [references/aggregate-design.md](references/aggregate-design.md) / [references/relationships.md](references/relationships.md)

### Step 4: ドメインサービス特定

以下の場合に Domain Service を設計する:

- 複数集約をまたぐドメインロジック
- 外部サービスとの協調
- ステートレスで Entity/VO に自然に属さない操作

### Step 5: ドメインイベント設計

以下の場合に Domain Event を設計する:

- 集約の状態変化を他集約・コンテキストに伝播する
- メール送信・在庫更新などの副作用を疎結合で実行する
- Eventual Consistency を実現する

詳細 → [references/domain-events.md](references/domain-events.md)

### Step 6: Mermaid classDiagram の生成

以下の規則に従って図を出力する。

#### ステレオタイプ

```
<<Aggregate Root>>  集約ルート
<<Entity>>          集約内エンティティ
<<Value Object>>    値オブジェクト
<<Domain Event>>    ドメインイベント
<<Domain Service>>  ドメインサービス
```

#### 関係記号

| 記号 | 種別 | 用途 |
|------|------|------|
| `A "1" *-- "1..*" B` | コンポジション | 集約内のエンティティ（ライフサイクル共有） |
| `A o-- B` | 集約 | 参照するが独立したライフサイクルを持つ |
| `A --> B` | 関連 | 方向付き参照（A が B を知っている） |
| `A ..> B` | 依存 | イベント発行・一時的な使用 |
| `A <\|-- B` | 継承 | B が A の is-a 関係 |

#### 図に含めるもの・含めないもの

含める:
- ビジネス的に意味のある属性（`status`、`totalAmount` など）
- ドメインロジックを表すメソッド（`place()`、`cancel()` など）
- 集約境界をコメントで明示（`%% ── Aggregate: Order ──`）

含めない:
- `createdAt` / `updatedAt` などの監査フィールド
- getter / setter
- インフラ依存の実装詳細（`@Column`、DB の型など）

#### 出力例（ECサイト：注文集約）

```
classDiagram
  %% ── Aggregate: Order ──
  class Order {
    <<Aggregate Root>>
    +OrderId id
    +CustomerId customerId
    +Money totalAmount
    +OrderStatus status
    +place() void
    +cancel() void
  }
  class OrderItem {
    <<Entity>>
    +OrderItemId id
    +ProductId productId
    +Quantity quantity
    +Money unitPrice
    +subtotal() Money
  }
  class Money {
    <<Value Object>>
    +Decimal amount
    +Currency currency
    +add(Money) Money
  }
  class OrderStatus {
    <<Value Object>>
    PLACED
    CONFIRMED
    SHIPPED
    CANCELLED
  }
  class OrderPlaced {
    <<Domain Event>>
    +OrderId orderId
    +DateTime occurredAt
  }

  Order "1" *-- "1..*" OrderItem : contains
  OrderItem *-- Money : unitPrice
  Order *-- Money : totalAmount
  Order *-- OrderStatus : status
  Order ..> OrderPlaced : raises
```

#### 図のレビュー

図をユーザーに提示し、以下を確認する:

- ドメインエキスパートの言葉と一致しているか
- 1トランザクションで変更される範囲が1集約に収まっているか
- 欠落しているエンティティ・関係はないか
- 双方向関連を単方向に簡素化できないか
- 集約が大きすぎないか（3〜7クラスが目安）

Mermaid 記法の詳細 → [references/mermaid-notation.md](references/mermaid-notation.md)

---

## 【逆引きモード】Reverse Engineering Mode

既存コードからドメインモデルを抽出し、DDD 観点で評価・改善提案を行う。

詳細 → [references/reverse-engineering.md](references/reverse-engineering.md)

### 逆引きフロー

```
Step R1: コード収集          ← 対象ファイル・ディレクトリを特定
Step R2: 要素抽出            ← クラス・フィールド・メソッド・依存関係を解析
Step R3: DDD 分類            ← Entity / Value Object / Service / Repository を識別
Step R4: 集約境界の推定      ← ライフサイクル・不変条件・トランザクション境界を推定
Step R5: Mermaid 図生成      ← 現状の構造を As-Is 図として出力
Step R6: DDD ギャップ評価    ← 問題点を列挙し To-Be 改善案を提示
```

### Step R1: コード収集

対象を確認する:

- ユーザーが指定したファイル・ディレクトリを読み込む
- 指定がない場合: `src/domain`, `src/model`, `src/entity`, `domain/`, `models/` などを探索する
- 複数言語対応: TypeScript / JavaScript / Python / Java / Kotlin / Go / Ruby / C# など

読み込む優先順位:
1. `domain/`, `model/`, `entity/` フォルダ配下（ドメイン層が明確な場合）
2. 命名パターンで判断: `*Entity.ts`, `*Model.py`, `*Aggregate.java`, `*VO.*` など
3. 上記がない場合: `src/` 全体を走査してクラス定義を収集

### Step R2: 要素抽出

各ファイルから以下を抽出する:

```
クラス/インターフェース名
  ├── フィールド（名前・型・可視性）
  ├── メソッド（名前・シグネチャ・可視性）
  ├── アノテーション/デコレータ（@Entity, @Column, @Aggregate など）
  └── 継承・実装関係（extends / implements / : BaseClass）

依存関係
  ├── import / require / using の解析
  ├── コンストラクタ引数の型
  └── フィールド型・メソッド引数の型
```

言語別の抽出ヒント:

| 言語 | Entity の手がかり | VO の手がかり | Repository の手がかり |
|------|------------------|--------------|----------------------|
| TypeScript | `@Entity()`, `extends BaseEntity`, `id: string` フィールド | イミュータブル・`readonly` フィールドのみ・`equals()` | `Repository<T>`, `findById()`, `save()` |
| Java/Kotlin | `@Entity`, `@Id`, `extends AbstractEntity` | `@Embeddable`, `final` フィールドのみ | `extends JpaRepository`, `findById()` |
| Python | `class *Model(BaseModel)`, `class *Entity` | `@dataclass(frozen=True)`, `NamedTuple` | `class *Repository`, `def find_by_id()` |
| Go | `type *Entity struct`, `ID` フィールド | `type * struct` + 変換メソッド | `type *Repository interface`, `FindByID()` |
| C# | `[Entity]`, `: Entity`, `Guid Id` | `record`, `struct`, `sealed class` + `Equals()` | `: IRepository<T>`, `GetById()` |
| Ruby | `< ActiveRecord::Base`, `< ApplicationRecord` | `include Comparable`, `attr_reader` のみ | `class *Repository`, `def find()` |

### Step R3: DDD 分類

抽出した要素を以下の基準で分類する:

**Entity の判定基準**
- ID フィールドを持つ（`id`, `userId`, `orderId` など）
- 状態が変化するメソッドを持つ（`cancel()`, `approve()`, `update*()` など）
- ライフサイクルを管理するアノテーションがある（`@Entity`, `@Document` など）

**Value Object の判定基準**
- ID フィールドを持たない
- すべてのフィールドが `readonly` / `final` / `val` など不変
- `equals()` / `==` の実装が値の比較になっている
- `with*()` / `copy()` など新しいインスタンスを返すメソッドを持つ
- 型が「概念」を表す名前（`Money`, `Address`, `Email`, `PhoneNumber` など）

**Aggregate Root の推定基準**
- 他のクラスから直接参照・生成されている
- Repository が存在する（`OrderRepository` → `Order` が集約ルート）
- ライフサイクルを管理するビジネスメソッドを最も多く持つ

**Domain Service の判定基準**
- ステートレス（フィールドを持たない、または依存性注入のみ）
- 複数の Entity / Aggregate を引数に取る
- `*Service`, `*DomainService`, `*Calculator`, `*Policy` などの命名

**Repository の判定基準**
- `find*()`, `get*()`, `save()`, `delete()` などの CRUD メソッドを持つ
- `*Repository`, `*Store`, `*Dao` などの命名
- インターフェース定義のみ（実装はインフラ層に分離されている）

**判定が困難な場合**
- 「不明」として出力し、ユーザーに確認する
- コードコメント・命名の意図を補足として記載する

### Step R4: 集約境界の推定

以下の手がかりから集約境界を推定する:

```
トランザクション境界
  ├── @Transactional / transaction.begin~commit の範囲
  ├── Unit of Work パターン
  └── 同一 Repository で save() されるオブジェクト群

ライフサイクルの共有
  ├── 親オブジェクトが削除されると子も削除される（CASCADE DELETE）
  ├── 子オブジェクトが単独で Repository を持たない
  └── 子オブジェクトが親の ID を外部キーとして持つのみ

参照パターン
  ├── 直接オブジェクト参照 → 同一集約の可能性が高い
  └── ID 参照のみ → 別集約の可能性が高い
```

### Step R5: Mermaid 図生成（As-Is）

現状のコード構造をそのまま図にする（DDD 的に正しくなくても現実を反映する）:

```
classDiagram
  %% ⚠️ As-Is: 現状のコード構造（DDD 評価前）

  %% ── 推定集約: Order ──
  class Order {
    <<Aggregate Root?>>
    +String id
    +String customerId
    +List~OrderItem~ items
    +String status
    +place() void
    +cancel() void
  }
  class OrderItem {
    <<Entity?>>
    +String id
    +String productId
    +int quantity
    +BigDecimal price
  }
  class Customer {
    <<Entity?>>
    +String id
    +String email
    +String name
  }

  Order "1" *-- "1..*" OrderItem : contains
  Order --> Customer : references directly ⚠️
```

注記（⚠️）: DDD 上の問題点を図内にコメントとして記載する。

### Step R6: DDD ギャップ評価

As-Is の図を基に、以下の観点で問題を評価し改善案（To-Be）を提示する:

#### 評価チェックリスト

```
□ 集約境界の妥当性
  - 1トランザクションで変更される範囲が1集約に収まっているか
  - 集約が大きすぎないか（目安: 3〜7クラス）

□ 参照の方向と形式
  - 集約間でオブジェクト直接参照をしていないか（ID 参照が望ましい）
  - 双方向参照が多用されていないか

□ Entity vs Value Object の正確さ
  - ID を持たない Entity になっているものはないか
  - 変更可能な Value Object はないか（VO はイミュータブルにする）

□ 貧血ドメインモデルの有無
  - ビジネスロジックがすべてサービス層に流出していないか
  - Entity / VO がデータのみでメソッドを持たないか

□ Repository の配置
  - 集約ルート以外に Repository が定義されていないか
  - ドメイン層の Repository がインフラ詳細を漏洩していないか

□ ユビキタス言語の一貫性
  - クラス名・メソッド名にドメイン用語が使われているか
  - 技術用語（UserRecord, OrderDTO）がドメイン層に混在していないか
```

#### 出力形式

```markdown
## As-Is ドメインモデル図

[Mermaid classDiagram - 現状]

## DDD ギャップ評価

| # | 問題 | 該当クラス | 深刻度 | 改善方針 |
|---|------|-----------|--------|---------|
| 1 | 集約間の直接オブジェクト参照 | Order → Customer | 高 | Customer を CustomerId 参照に変更 |
| 2 | 貧血ドメインモデル | OrderItem | 中 | subtotal() などのビジネスメソッドを移動 |
| 3 | 変更可能な Value Object | Money.amount を直接変更 | 高 | Money を immutable に変更 |

## To-Be ドメインモデル図（改善案）

[Mermaid classDiagram - 改善後]

## リファクタリング優先度

1. **即対応**: [深刻度:高の問題点]
2. **次スプリント**: [深刻度:中の問題点]
3. **将来的に検討**: [深刻度:低の問題点]
```

---

## 参照ドキュメント

- **全体の設計原則・判断基準**: [references/core-concepts.md](references/core-concepts.md)
- **Aggregate設計の詳細と失敗パターン**: [references/aggregate-design.md](references/aggregate-design.md)
- **Bounded Context・Context Map**: [references/bounded-context.md](references/bounded-context.md)
- **関係性の種類と使い分け**: [references/relationships.md](references/relationships.md)
- **Domain Events 設計ガイド**: [references/domain-events.md](references/domain-events.md)
- **Mermaid図の記法と表現方法**: [references/mermaid-notation.md](references/mermaid-notation.md)
- **DDD パターン総合ガイド**: [references/ddd-patterns.md](references/ddd-patterns.md)
- **逆引きエンジニアリングガイド**: [references/reverse-engineering.md](references/reverse-engineering.md)

---

## クイックリファレンス：判断フローチャート

### Entity vs Value Object

```
「この概念は追跡が必要か（ライフサイクルがあるか）？」
  YES → Entity（識別子を持つ）
  NO  → 「値として等価判定が自然か？」
          YES → Value Object（イミュータブルにする）
          NO  → 再検討（ドメイン知識が足りない可能性）
```

### Aggregate 境界の決め方

```
「このオブジェクト群は、常に一貫した状態でなければならないか？」
  YES → 同じAggregate
  NO  → 別のAggregate（IDで参照する）

「整合性が必要なのはいつか？」
  即時（同一トランザクション）  → 同じAggregateを検討
  最終的整合性でよい            → 別Aggregateにして Domain Event で連携
```

### Domain Event を使うか判断する

```
「集約の状態変化を他の集約・コンテキストに伝える必要があるか？」
  YES → Domain Event を発行する

「副作用（メール・在庫更新・ログ）を集約から分離したいか？」
  YES → Domain Event で疎結合にする
```

### Bounded Context の境界

```
「同じ言葉が異なるチームで異なる意味を持っているか？」
  YES → 別の Bounded Context

「このチームの変更が別のチームの変更を強制するか？」
  YES → 境界が必要 → Context Map でパターンを選択
```

---

## よくある失敗パターン（必読）

1. **God Aggregate**: Order が Cart・Payment・Shipping・Inventory をすべて含む
   解決: ドメインイベントで集約間連携に分割（Vernon の原則2: 小さな集約）

2. **貧血ドメインモデル**: ドメインオブジェクトが getter/setter のみ、ロジックはすべてサービス層
   解決: 不変条件の保護・状態遷移をエンティティ自身に移動

3. **DBスキーマ思考のドメインモデル**: テーブル設計をそのままクラスにしたモデル
   解決: ドメイン概念から設計し、Repository で永続化を分離

4. **Bounded Context 未設定のまま単一モデル**: "Product" が在庫・EC・物流で同じクラス
   解決: コンテキストごとに独立したモデルを定義

5. **双方向参照の多用**: Order ↔ Customer ↔ OrderItem が相互参照
   解決: 主たる方向を一方向に固定し、逆方向はクエリで取得

6. **イミュータブルでない Value Object**: Money の amount を直接変更している
   解決: VO は新しいオブジェクトを返す（`money.add(other)` → 新しい `Money` を返す）

7. **集約間で直接オブジェクト参照**: `order.customer.email` のようなアクセス
   解決: 別集約への参照は ID のみ（`order.customerId`）

8. **技術的 ID をドメインイベントに含める**: DB のサロゲートキーをそのままイベントに
   解決: ドメインの識別子（`OrderId` 型等）を使う

詳細な設計原則・具体例・Mermaid 記法は上記参照ドキュメントを読み込む。

---

## 出力テンプレート

設計結果は以下の形式でまとめる:

```markdown
## ユビキタス言語

| 用語 | 定義 | 文脈 |
|------|------|------|
| 注文 | 顧客が確定した購入意思 | Order Context |

## Bounded Context

| Context | 責務 |
|---------|------|
| Order Management | 注文の作成から完了まで |

## Context Map

[Mermaid graph で BC 間の関係を表現]

## ドメインモデル図

[Mermaid classDiagram で集約・Entity・VO を表現]

## 設計判断の根拠

- なぜ X を Entity にしたか
- なぜ Y を VO にしたか
- なぜ Z を別集約にしたか
```
