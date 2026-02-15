---
name: react-frontend-coder
description: React + TypeScriptで、要件・UI設計が既に確定している前提で、フロントエンド機能を実装する。Vite + Zustandをベースに、実装→検証に特化。テストコード実装は別スキルに委譲。
---

# react-frontend-coder

React + TypeScript（Vite + Zustand）で、**要件・UI設計が既に確定している前提**でフロントエンド機能を実装する。セットアップ → 実装 → 検証の3スプリントで進める。

## ワークフロー

### Sprint 1: セットアップ

プロジェクトテンプレートを準備し、開発環境を整える。

1. **テンプレートをコピー** - `assets/vite-react-ts-template/` を作業ディレクトリに複製
2. **依存をインストール** - `npm install`
3. **開発サーバー起動確認** - `npm run dev`

### Sprint 2: 実装

設計に従い、コード実装を進める。

1. **プロジェクトセットアップ**
   - assets テンプレートをベースに複製（Vite + React + TypeScript + Zustand）
   - 依存追加: `npm install [required-libs]`

2. **Zustand Store実装**
   - state形状を定義
   - action（setter, logic）を実装
   - 必要に応じてdevtools連携

3. **コンポーネント実装**
   - 分割されたコンポーネント群を実装
   - イベントハンドラー実装（onClick, onChange等）
   - store連携（useStore hook）

4. **スタイル・UI調整**
   - Tailwind / CSS で見た目を整える
   - レスポンシブ対応（必要に応じて）
   - ダークモード対応（必要に応じて）

### Sprint 3: 検証・調整

実装品質と要件充足度を確認。

1. **開発サーバー動作確認**
   - `npm run dev` で起動
   - UI表示、操作が意図通りに動作するか確認
   - ブラウザコンソールでエラーチェック

2. **機能検証**
   - 要件で定義した**すべての機能が実装されているか**
   - エッジケース（空入力、境界値等）の動作
   - 画面遷移・状態変化が正しいか

3. **ビルド確認**
   - `npm run build` で本番ビルドが成功するか
   - bundleサイズの確認（必要に応じて）

4. **コード品質**
   - TypeScript型チェック（エラーなし）
   - 不要な依存・デッドコードがないか

## 実装ガイダンス

### 実装Tips

詳細は references を参照：
- **component-patterns.md** - コンポーネント設計ガイド
- **zustand-guide.md** - 状態管理ガイド
- **styling-guide.md** - スタイリングガイド

## リソース

### assets/

- **Vite + React + TypeScript + Zustand テンプレート** (`vite-react-ts-template/`)
  - 最小構成の実行可能プロジェクト
  - Zustand store雛形
  - Tailwind CSS + Vite 統合済み

### references/

- **component-patterns.md** - React コンポーネント設計パターン
- **zustand-guide.md** - Zustand 使用ガイド・ベストプラクティス
- **styling-guide.md** - Tailwind CSS + 手書きCSS使い分け

## テストについて

テストコード実装は別スキルに委譲する。本スキルは実装までを対象とする。
