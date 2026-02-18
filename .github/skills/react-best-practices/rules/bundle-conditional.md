---
title: 条件付きモジュールのロード
impact: HIGH
impactDescription: 必要な場合にのみ大きなデータをロードします
tags: bundle, conditional-loading, lazy-loading
---

## 条件付きモジュールのロード

機能がアクティブ化されている場合にのみ、大きなデータまたはモジュールをロードします。

**例（アニメーション フレームの遅延読み込み）:**

```tsx
function AnimationPlayer({ enabled, setEnabled }: { enabled: boolean; setEnabled: React.Dispatch<React.SetStateAction<boolean>> }) {
  const [frames, setFrames] = useState<Frame[] | null>(null)

  useEffect(() => {
    if (enabled && !frames && typeof window !== 'undefined') {
      import('./animation-frames.js')
        .then(mod => setFrames(mod.frames))
        .catch(() => setEnabled(false))
    }
  }, [enabled, frames, setEnabled])

  if (!frames) return <Skeleton />
  return <Canvas frames={frames} />
}
```

`typeof window !== 'undefined'` チェックにより、SSR 用のこのモジュールのバンドルが防止され、サーバー バンドルのサイズとビルド速度が最適化されます。
