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
2. **依存をインストール** - `npm install`。追加ライブラリが必要なら `npm install [required-libs]` で追加する
3. **開発サーバー起動確認** - `npm run dev`

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

3. **スタイル・UI調整**
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

## 実行後フィードバック（必須）

> **scrum-master 経由の場合**: このセクションをスキップしてください。フィードバックはスプリント終了時に一括で収集されます。

スキルの手順を全て完了したら、ユーザーに確認する:

「react-frontend-coder の実行はいかがでしたか？
 1. 問題なかった (ok)
 2. 改善点がある (needs-improvement)
 3. うまくいかなかった (broken)」

回答に応じて以下を実行する（git-skill-manager がない環境ではスキップ）:
```
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'react-frontend-coder','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"
```

スクリプトの出力に「EVAL_RECOMMEND: promote」または「EVAL_RECOMMEND: refine」が含まれる場合は、
skill-evaluator サブエージェントを起動して評価・昇格フローを進める:
```
skill-evaluator スキルで react-frontend-coder を評価する。
手順: まず .github/skills/skill-evaluator/SKILL.md を読んで手順に従ってください。
対象スキル: react-frontend-coder
```
