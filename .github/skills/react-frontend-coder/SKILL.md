---
name: react-frontend-coder
description: React + TypeScriptで画面実装、最適化、ユニットテストまで一体で進める統合スキル。要件・UI設計が確定済みで、「React画面を作って」「Reactをテストして」などのときに使う。
metadata:
   version: 2.0.1
   tier: stable
   category: implementation
   tags:
      - react
      - typescript
      - vite
      - zustand
      - testing
      - performance
---

# react-frontend-coder

React + TypeScript（Vite + Zustand）で、**要件・UI設計が既に確定している前提**でフロントエンド機能を実装する統合スキル。旧 `react-best-practices` と旧 `react-frontend-unit-tester` の責務を吸収し、セットアップ → 実装 → 最適化 → テスト → 検証の流れで進める。

## ワークフロー

### Sprint 1: セットアップ

プロジェクトテンプレートを準備し、開発環境を整える。

1. **テンプレートをコピー** - `assets/vite-react-ts-template/` を作業ディレクトリに複製
2. **依存をインストール** - `npm install`。追加ライブラリが必要なら `npm install [required-libs]` で追加する
3. **開発サーバー起動確認** - `npm run dev`
4. **テスト基盤を確認**
   - Vite プロジェクトなら Vitest を優先する
   - 必要なら `vitest` / `@testing-library/react` / `@testing-library/jest-dom` / `jsdom` を導入する
   - `package.json` に `test` / `test:coverage` を追加する

### Sprint 2: 実装

設計に従い、コード実装を進める。

1. **Zustand Store実装**
   - state形状を定義
   - action（setter, logic）を実装
   - 必要に応じてdevtools連携

2. **コンポーネント実装**
   - 分割されたコンポーネント群を実装
   - イベントハンドラー実装（onClick, onChange等）
   - store連携（useStore hook）
   - テストしやすい props / state 境界を意識して責務を分離

3. **スタイル・UI調整**
   - Tailwind / CSS で見た目を整える
   - レスポンシブ対応（必要に応じて）
   - ダークモード対応（必要に応じて）

### Sprint 3: React ベストプラクティス適用

実装直後に、性能と保守性に効く React / Next.js 系ベストプラクティスを必要箇所へ適用する。

1. **非同期・データ取得の見直し**
   - 独立した非同期処理は `Promise.all()` などで並列化する
   - 不要なウォーターフォールを避け、`await` は必要な箇所まで遅延する

2. **再レンダーと描画の最適化**
   - 不要な購読を減らし、派生状態は render 中に算出する
   - 非緊急更新には `startTransition` を検討する
   - 単純な式に安易にメモ化を入れず、重い処理だけを分離する

3. **バンドルと依存の最適化**
   - 重いモジュールは遅延読み込みや条件付きロードを検討する
   - バレル import を避け、必要なモジュールを直接 import する
   - サードパーティ導入は初期表示コストを確認して最小化する

### Sprint 4: ユニットテスト実装

コンポーネント → Store → Hooks の順で、変更の要件を守るテストを追加する。

1. **Store テスト**
   - state 初期値
   - action 実行後の状態変化
   - selector の正確性

2. **コンポーネント テスト**
   - 主要 props の表示
   - ユーザー操作とイベント処理
   - store 連携やバリデーション結果

3. **Hooks テスト**
   - カスタム hook がある場合のみ追加
   - 分岐・非同期・エラー系を優先的に検証

### Sprint 5: 検証・調整

実装品質と要件充足度を確認。

1. **開発サーバー動作確認**
   - `npm run dev` で起動
   - UI表示、操作が意図通りに動作するか確認
   - ブラウザコンソールでエラーチェック

2. **機能検証**
   - 要件で定義した**すべての機能が実装されているか**
   - エッジケース（空入力、境界値等）の動作
   - 画面遷移・状態変化が正しいか

3. **テスト実行**
   - `npm run test` を実行
   - 必要なら `npm run test:coverage` を実行
   - 目標: statements 80% 以上を目安に不足ケースを補う

4. **ビルド確認**
   - `npm run build` で本番ビルドが成功するか
   - bundleサイズの確認（必要に応じて）

5. **コード品質**
   - TypeScript型チェック（エラーなし）
   - 不要な依存・デッドコードがないか
   - テストが実装詳細に過度依存していないか

## 実装ガイダンス

### 実装Tips

詳細は references を参照：
- **component-patterns.md** - コンポーネント設計ガイド
- **zustand-guide.md** - 状態管理ガイド
- **styling-guide.md** - スタイリングガイド

以下の統合済み資産も参照してよい：
- `rules/` - React ベストプラクティス集
- `references/testing-setup-guide.md` - テスト環境セットアップ
- `references/react-component-testing.md` - コンポーネントテスト
- `references/react-hooks-testing.md` - Custom Hooks テスト
- `references/zustand-store-testing.md` - Zustand Store テスト
- `references/testing-best-practices.md` - テストのベストプラクティス
- `assets/vite-react-ts-template/` - テスト設定例を組み込んだテンプレート
- `scripts/` - Vitest セットアップ補助スクリプト

## リソース

### assets/

- **Vite + React + TypeScript + Zustand テンプレート** (`vite-react-ts-template/`)
  - 最小構成の実行可能プロジェクト
  - Zustand store雛形
  - Tailwind CSS + Vite 統合済み
   - `vitest.config.example.ts` と `jest.config.example.js` を同梱

### references/

- **component-patterns.md** - React コンポーネント設計パターン
- **zustand-guide.md** - Zustand 使用ガイド・ベストプラクティス
- **styling-guide.md** - Tailwind CSS + 手書きCSS使い分け
- **testing-setup-guide.md** - Vitest / Jest セットアップガイド
- **react-component-testing.md** - React コンポーネント テスト
- **react-hooks-testing.md** - Custom Hooks テスト
- **zustand-store-testing.md** - Zustand Store テスト
- **testing-best-practices.md** - テスト ベストプラクティス

### rules/

- **React ベストプラクティス集** - 旧 `react-best-practices` 由来のルール群をローカル同梱

### scripts/

- **setup-vitest.sh** - macOS / Linux 向け Vitest セットアップ補助
- **setup-vitest.ps1** - Windows PowerShell 向け Vitest セットアップ補助

## 統合ポリシー

- React 実装、性能最適化、ユニットテストは原則として本スキルで完結させる
- E2E テストやブラウザ実検証が必要な場合のみ `webapp-testing` を追加で使う
- TDD を厳密に回す必要がある場合のみ `tdd-executing` を上位オーケストレーターとして併用する

