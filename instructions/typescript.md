# TypeScript / JavaScript コーディング指示

TypeScript / JavaScript プロジェクトに適用するコーディング規範。

## TypeScript 設定

- `strict: true` を必ず有効化する
- `noImplicitAny: true`、`strictNullChecks: true` を確認する
- `as any` は最後の手段。使う場合はコメントで理由を説明する

## 型定義

- `type` と `interface` を使い分ける
  - オブジェクト型の拡張が必要: `interface`
  - Union / Intersection / 計算型: `type`
- `any` より `unknown` を使い、適切にナローイングする
- 関数の戻り値型は明示する（推論できる場合でも複雑な関数では明記）

```typescript
// Union 型でリテラル型を絞り込む
type Status = "pending" | "fulfilled" | "rejected";

function handleStatus(status: Status): string {
  switch (status) {
    case "pending": return "処理中...";
    case "fulfilled": return "完了";
    case "rejected": return "失敗";
  }
}
```

## 非同期処理

- コールバックより `async / await` を優先する
- `Promise.all()` で独立した非同期処理を並列実行する
- エラーハンドリングは `try / catch` で行う（`.catch()` チェーンは可読性が低い）

## イミュータビリティ

- `const` を優先し `let` を最小化する（`var` は使わない）
- 配列・オブジェクトの変更は新しい参照を返すメソッドを使う（`map`, `filter`, `spread`）
- `readonly` 修飾子と `Readonly<T>` を活用する

## モジュール

- デフォルトエクスポートより名前付きエクスポートを優先する（リファクタリング時のトレーサビリティ）
- import は外部 → 内部の順に並べ、パスエイリアスを活用する
- バレルファイル（`index.ts`）は深いネストの場合のみ作成する

## エラー処理

- `Error` を継承したカスタムエラークラスを作成する
- エラーは型で区別する（文字列コードより `instanceof` による分岐）
- ユーザー向けエラーと開発者向けエラーを分ける

## リンタ・フォーマッタ

- ESLint（`@typescript-eslint`）+ Prettier を使用する
- `eslint-config-prettier` で ESLint と Prettier の競合を解消する
- CI で lint チェックを必須にする

## テスト

- Vitest（推奨）または Jest を使用する
- ユニットテストは `describe` / `it` でネストして整理する
- モックは最小限に。テスト容易な設計（依存注入）を優先する
