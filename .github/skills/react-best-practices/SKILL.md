---
name: react-best-practices
description: Vercel Engineering の React / Next.js パフォーマンス最適化ガイドを日本語で利用するためのスキル。Reactコンポーネント実装、Next.jsページ開発、データ取得、バンドル最適化、リファクタ時の性能改善タスクで使用する。
license: MIT
metadata:
  author: vercel
  version: "1.0.0"
  source: "https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices"
  language: ja
---

# React ベストプラクティス（Vercel）

React / Next.js アプリケーション向けの包括的なパフォーマンス最適化ガイド。8カテゴリ・57ルールを影響度順に整理しており、実装・レビュー・自動リファクタリング時の判断基準として使う。

## 適用タイミング

次のような場面で参照する:
- 新しい React コンポーネントや Next.js ページを実装するとき
- クライアント / サーバーのデータ取得ロジックを設計するとき
- パフォーマンス観点でコードレビューするとき
- 既存の React / Next.js コードをリファクタリングするとき
- バンドルサイズや初期表示速度を改善したいとき

## ルールカテゴリ（優先度順）

| 優先度 | カテゴリ | 影響度 | プレフィックス |
|---|---|---|---|
| 1 | ウォーターフォール排除 | CRITICAL | `async-` |
| 2 | バンドルサイズ最適化 | CRITICAL | `bundle-` |
| 3 | サーバーサイド性能 | HIGH | `server-` |
| 4 | クライアントデータ取得 | MEDIUM-HIGH | `client-` |
| 5 | 再レンダー最適化 | MEDIUM | `rerender-` |
| 6 | レンダリング性能 | MEDIUM | `rendering-` |
| 7 | JavaScript性能 | LOW-MEDIUM | `js-` |
| 8 | 高度なパターン | LOW | `advanced-` |

## クイックリファレンス

### 1. ウォーターフォール排除（CRITICAL）

- `async-defer-await` - 実際に使う分岐まで `await` を遅延
- `async-parallel` - 独立処理は `Promise.all()` で並列化
- `async-dependencies` - 部分依存のある並列化に `better-all` を活用
- `async-api-routes` - APIルートで Promise を先に開始し await を後ろへ
- `async-suspense-boundaries` - `Suspense` で段階的にストリーミング

### 2. バンドルサイズ最適化（CRITICAL）

- `bundle-barrel-imports` - バレル経由を避けて直接 import
- `bundle-dynamic-imports` - 重いコンポーネントは `next/dynamic`
- `bundle-defer-third-party` - 分析/ログ系は hydration 後に読み込み
- `bundle-conditional` - 機能有効時だけモジュールをロード
- `bundle-preload` - hover/focus 起点で先読みして体感速度改善

### 3. サーバーサイド性能（HIGH）

- `server-auth-actions` - API ルート同様に Server Action を認証
- `server-cache-react` - リクエスト単位の重複排除に `React.cache()`
- `server-cache-lru` - リクエスト跨ぎキャッシュに LRU を利用
- `server-dedup-props` - RSC props の重複シリアライズを回避
- `server-serialization` - クライアントへ渡すデータを最小化
- `server-parallel-fetching` - フェッチが並列になるよう構成を見直す
- `server-after-nonblocking` - 非ブロッキング処理に `after()` を使う

### 4. クライアントデータ取得（MEDIUM-HIGH）

- `client-swr-dedup` - 自動重複排除に SWR を利用
- `client-event-listeners` - グローバルイベントリスナーを重複登録しない
- `client-passive-event-listeners` - スクロール系は passive listener を利用
- `client-localstorage-schema` - localStorage はスキーマ版管理＋最小化

### 5. 再レンダー最適化（MEDIUM）

- `rerender-defer-reads` - コールバックでしか使わない state を購読しない
- `rerender-memo` - 重い処理はメモ化コンポーネントへ分離
- `rerender-memo-with-default-value` - 非プリミティブのデフォルト値を外へ退避
- `rerender-dependencies` - effect 依存はオブジェクトよりプリミティブ
- `rerender-derived-state` - 生値ではなく派生ブール値を購読
- `rerender-derived-state-no-effect` - 派生状態は effect ではなく render 中に算出
- `rerender-functional-setstate` - 安定コールバックのため関数型 `setState`
- `rerender-lazy-state-init` - 重い初期値は `useState(() => ...)`
- `rerender-simple-expression-in-memo` - 単純なプリミティブ式に `useMemo` を使わない
- `rerender-move-effect-to-event` - 相互作用ロジックは event handler へ移動
- `rerender-transitions` - 非緊急更新は `startTransition`
- `rerender-use-ref-transient-values` - 一時的で高頻度な値は `useRef`

### 6. レンダリング性能（MEDIUM）

- `rendering-animate-svg-wrapper` - SVG本体ではなくラッパー要素をアニメーション
- `rendering-content-visibility` - 長いリストに `content-visibility`
- `rendering-hoist-jsx` - 静的 JSX をコンポーネント外へ退避
- `rendering-svg-precision` - SVG座標精度を適切に削減
- `rendering-hydration-no-flicker` - クライアント専用値は inline script でチラつき防止
- `rendering-hydration-suppress-warning` - 想定済み hydration 不一致を抑制
- `rendering-activity` - 表示/非表示には `Activity` コンポーネントを活用
- `rendering-conditional-render` - 条件分岐は `&&` より三項演算子を優先
- `rendering-usetransition-loading` - 読み込み状態は手動フラグより `useTransition`

### 7. JavaScript性能（LOW-MEDIUM）

- `js-batch-dom-css` - CSS変更は class / `cssText` 単位でまとめる
- `js-index-maps` - 繰り返し検索に `Map` インデックスを構築
- `js-cache-property-access` - ループ中のプロパティアクセスをキャッシュ
- `js-cache-function-results` - 関数結果をモジュールスコープ `Map` で再利用
- `js-cache-storage` - `localStorage` / `sessionStorage` 読み取りをキャッシュ
- `js-combine-iterations` - 複数 `filter/map` を単一ループへ統合
- `js-length-check-first` - 高コスト比較前に配列長を先に確認
- `js-early-exit` - 関数は早期 return を活用
- `js-hoist-regexp` - ループ外で RegExp を生成
- `js-min-max-loop` - min/max 算出に sort ではなくループ
- `js-set-map-lookups` - 検索は `Set/Map` で O(1)
- `js-tosorted-immutable` - 不変性維持に `toSorted()`

### 8. 高度なパターン（LOW）

- `advanced-event-handler-refs` - イベントハンドラを ref に保持
- `advanced-init-once` - アプリ初期化をロード単位で一度だけ実行
- `advanced-use-latest` - 安定コールバック参照に `useLatest`

## 使い方

詳細な解説とコード例は個別ルールファイルを参照する:

```
rules/async-parallel.md
rules/bundle-barrel-imports.md
```

各ルールファイルには次が含まれる:
- 重要性の短い説明
- 悪い実装例（Incorrect）
- 良い実装例（Correct）
- 補足コンテキストと参照リンク

## セクション構成

全カテゴリの一覧・影響度・説明は `rules/_sections.md` を参照。

