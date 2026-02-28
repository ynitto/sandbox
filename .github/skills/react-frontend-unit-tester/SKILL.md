---
name: react-frontend-unit-tester
description: ReactコンポーネントとZustand storeのテストコード実装に特化。Jest/Vitest + React Testing Libraryを使用し、ユニットテストからカバレッジ確認まで段階的に進める。フロントエンド実装完了後のテスト投入フロー。「テストコードを実装して」「Reactコンポーネントをテストして」「Zustandストアのテストを書いて」などのリクエストで発動する。
metadata:
   version: "1.0"
---

# react-frontend-unit-tester

React + TypeScript（Vite + Zustand）プロジェクトで、コンポーネント・ストアのユニットテストを実装する。**フロントエンド実装完了後に投入される**。

## 責務範囲

✅ **対象:**
- コンポーネント単位テスト（props検証、イベント処理）
- Zustand store テスト（state・action検証）
- Custom hooks テスト
- Jest/Vitest 設定と実行

❌ **除外:**
- E2Eテスト（Playwrightなど）
- パフォーマンステスト
- ビジュアル回帰テスト

## ワークフロー

### Step 1: テスト環境セットアップ

1. **テストフレームワーク確認または導入**
   - Vite プロジェクトか確認 → Vitest推奨（Viteネイティブ対応）
   - Jestの場合は別途設定（references参照）

2. **テスト依存をインストール**
   ```
   npm install -D vitest @testing-library/react @testing-library/jest-dom
   npm install -D jsdom  # DOM simulation用
   ```

3. **vite.config.ts または vitest.config.ts を設定**
   - テスト環境をjsdomに設定
   - アライアスやカバレッジ設定（references参照）

4. **package.json にスクリプト追加**
   ```json
   "test": "vitest",
   "test:coverage": "vitest --coverage"
   ```

### Step 2: テスト実装

コンポーネント → Store → Hooks の順に実装。

1. **Store テスト** （最初に実装）
   - state初期値検証
   - action実行による状態変化
   - selector正確性
   - 詳細：[zustand-testing.md](references/zustand-testing.md)

2. **コンポーネント テスト**
   - Props検証（render時の表示）
   - ユーザーイベント処理（userEvent/fireEvent）
   - Store連携確認（mock store使用）
   - 詳細：[component-testing.md](references/component-testing.md)

3. **Custom Hooks テスト** （必要に応じて）
   - renderHookでテスト
   - 詳細：[hooks-testing.md](references/hooks-testing.md)

### Step 3: 実行・カバレッジ確認

1. **テスト実行**
   ```bash
   npm run test
   ```

2. **カバレッジ確認**
   ```bash
   npm run test:coverage
   ```
   目標: **>= 80% statements**

3. **失敗分析・修正**
   - テスト失敗 → 仕様確認 → 実装側を修正
   - 不十分なカバレッジ → テストケース追加

## フロントエンド実装との連携

|フェーズ|役割|
|--|--|
|Plan|要件・UI設計確定|
|実装フェーズ|フロントエンド実装: コンポーネント・ストア実装|
|テスト投入フェーズ|react-frontend-unit-tester: テスト投入|
|テスト失敗時|react-frontend-unit-tester で問題箇所特定 → 実装側を修正|

## リソース

### references/

- **setup-guide.md** - Vitest/Jest セットアップ詳細
- **zustand-testing.md** - Store テスト パターン・ベストプラクティス
- **component-testing.md** - コンポーネント テスト パターン
- **hooks-testing.md** - Custom hooks テスト方法
- **best-practices.md** - アサーション・テストデータ・async処理のおすすめ

### assets/

- **vitest.config.example.ts** - Vite + Vitest 最小設定テンプレート
- **jest.config.example.js** - Jest（Vite非使用時）設定テンプレート

### scripts/

- **setup-vitest.sh** - 依存インストール・基本設定の自動化（macOS / Linux）
- **setup-vitest.ps1** - 依存インストール・基本設定の自動化（Windows PowerShell）

