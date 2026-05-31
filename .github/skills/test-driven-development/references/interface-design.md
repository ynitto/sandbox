# テスタビリティのためのインターフェース設計

良いインターフェースはテストを自然にする:

1. **依存は受け取る。生成しない**

   ```typescript
   // テストしやすい
   function processOrder(order, paymentGateway) {}

   // テストしにくい
   function processOrder(order) {
     const gateway = new StripeGateway();
   }
   ```

2. **副作用を起こさず、結果を返す**

   ```typescript
   // テストしやすい
   function calculateDiscount(cart): Discount {}

   // テストしにくい
   function applyDiscount(cart): void {
     cart.total -= discount;
   }
   ```

3. **小さい表面積**
   - メソッドが少ない = 必要なテストが少ない
   - パラメータが少ない = テストセットアップが単純

これらは RED フェーズの前（Phase 0 の計画）で意識する。
テストしにくいインターフェースに気づいたら、テストを書く前に設計を見直す。
