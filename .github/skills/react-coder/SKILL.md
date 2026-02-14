---
name: react-coder
description: react typescript でVite + ZustandのToDo一覧サンプルをスクラム方式で段階的に作るときに使う。
---

# react-coder

React + TypeScriptでToDo一覧（固定データ、操作なし）を作る。
ViteテンプレートとZustand storeの雛形を使い、スプリント3段階で進める。

## ワークフロー

### Sprint 1: 設計
1. 仕様を固定する。
	- 一覧のみ、追加/編集/削除/切替なし
	- ToDo項目は `title` と `completed`（3件固定）
2. 画面構成を決める。
	- ヘッダー + 一覧カードの単一画面
3. ファイル構成を決める。
	- `src/App.tsx`, `src/store/todoStore.ts`, `src/index.css`

### Sprint 2: 実装
1. アセットのテンプレートをコピーする。
	- `assets/vite-react-ts-todo/` を作業ディレクトリにコピー
2. 依存をインストールする。
	- `npm install`
3. Zustand storeと表示を確認する。
	- `src/store/todoStore.ts` の固定データとアクション雛形
	- `src/App.tsx` で一覧を描画
4. 見た目を調整する。
	- `src/index.css` の軽いグレー系スタイル

### Sprint 3: 検証
1. 開発サーバーを起動して表示確認する。
	- `npm run dev`
2. ビルドが通ることを確認する。
	- `npm run build`
3. 仕様とのズレがないことを確認する。
	- 操作なし、固定3件、`title`/`completed`のみ

## リソース

- **scripts/**: 実行可能コード（決定論的な信頼性が必要な処理向け）
- **references/**: 必要時に読み込むドキュメント（スキーマ、API仕様等）
- **assets/**: 出力に使用するファイル（テンプレート、画像等）

### assets

- `assets/vite-react-ts-todo/`
  - Vite + React + TypeScriptの最小テンプレート
  - Zustand store雛形とToDo一覧のUIを含む
