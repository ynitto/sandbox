# 判断クイックリファレンスとよくある失敗パターン

Entity / Value Object・集約境界・Domain Event・Bounded Context の判断フローチャートと、
設計でよく見る失敗パターンをまとめる。設計モードの Step 3〜6 で迷ったときに読む。

## 判断フローチャート

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

## よくある失敗パターン

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
