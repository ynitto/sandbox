---
title: 配列比較のための初期の長さチェック
impact: MEDIUM-HIGH
impactDescription: 長さが異なる場合にコストのかかる操作を回避します
tags: javascript, arrays, performance, optimization, comparison
---

## 配列比較のための初期の長さチェック

負荷の高い操作（ソート、深い等価性、直列化) を伴う配列を比較する場合は、最初に長さを確認してください。長さが異なる場合、配列を等しくすることはできません。

実際のアプリケーションでは、この最適化は、比較がホット パス（イベント ハンドラー、レンダー ループ) で実行される場合に特に役立ちます。

**誤り（常に高価な比較が実行されます）:**

```typescript
function hasChanges(current: string[], original: string[]) {
  // Always sorts and joins, even when lengths differ
  return current.sort().join() !== original.sort().join()
}
```

`current.length` が 5 で `original.length` が 100 の場合でも、2 つの O(n log n) ソートが実行されます。配列の結合と文字列の比較のオーバーヘッドもあります。

**正しい例（最初に O(1) 長さチェック）:**

```typescript
function hasChanges(current: string[], original: string[]) {
  // Early return if lengths differ
  if (current.length !== original.length) {
    return true
  }
  // Only sort when lengths match
  const currentSorted = current.toSorted()
  const originalSorted = original.toSorted()
  for (let i = 0; i < currentSorted.length; i++) {
    if (currentSorted[i] !== originalSorted[i]) {
      return true
    }
  }
  return false
}
```

この新しいアプローチは、次の理由によりより効率的です。
- 長さが異なる場合の配列のソートと結合のオーバーヘッドを回避します。
- 結合された文字列のためのメモリの消費を回避します（特に大きな配列の場合に重要です)。
- 元の配列の変更を回避します
- 差分が見つかった場合は早く戻ります
