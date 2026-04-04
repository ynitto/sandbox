# Custom Hooks テスト

## 目次

- [renderHook 使用方法](#renderhook-使用方法)

## renderHook 使用方法

Custom hooks は `renderHook` 関数でテストする。

```typescript
import { renderHook, act } from '@testing-library/react'
```

## 基本パターン

### Hook の例（useCounter）

```typescript
// src/hooks/useCounter.ts
import { useState } from 'react'

export const useCounter = (initialValue = 0) => {
  const [count, setCount] = useState(initialValue)

  const increment = () => setCount((c) => c + 1)
  const decrement = () => setCount((c) => c - 1)
  const reset = () => setCount(initialValue)

  return { count, increment, decrement, reset }
}
```

### Hook テスト

```typescript
// src/hooks/useCounter.test.ts
import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useCounter } from './useCounter'

describe('useCounter', () => {
  it('初期値 0 からスタート', () => {
    const { result } = renderHook(() => useCounter())

    expect(result.current.count).toBe(0)
  })

  it('カスタム初期値に対応', () => {
    const { result } = renderHook(() => useCounter(10))

    expect(result.current.count).toBe(10)
  })

  it('increment で値を増加', () => {
    const { result } = renderHook(() => useCounter())

    act(() => {
      result.current.increment()
    })

    expect(result.current.count).toBe(1)
  })

  it('decrement で値を減少', () => {
    const { result } = renderHook(() => useCounter(5))

    act(() => {
      result.current.decrement()
    })

    expect(result.current.count).toBe(4)
  })

  it('reset で初期値に戻す', () => {
    const { result } = renderHook(() => useCounter(10))

    act(() => {
      result.current.increment()
      result.current.increment()
    })

    expect(result.current.count).toBe(12)

    act(() => {
      result.current.reset()
    })

    expect(result.current.count).toBe(10)
  })
})
```

## useEffect を含む Hook テスト

```typescript
// Hook
export const useFetch = (url: string) => {
  const [data, setData] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true)
      try {
        const response = await fetch(url)
        if (!response.ok) throw new Error('Failed to fetch')
        const json = await response.json()
        setData(json)
      } catch (err) {
        setError((err as Error).message)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [url])

  return { data, loading, error }
}

// テスト
import { vi, beforeEach } from 'vitest'

describe('useFetch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('データ取得中に loading=true', async () => {
    global.fetch = vi.fn(() =>
      new Promise(() => {}) // 永遠に待機
    ) as any

    const { result } = renderHook(() => useFetch('/api/data'))

    expect(result.current.loading).toBe(true)
  })

  it('データ取得成功時に data をセット', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ message: 'Hello' })
      } as Response)
    ) as any

    const { result } = renderHook(() => useFetch('/api/data'))

    // 非同期処理完了を待つ
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(result.current.data).toEqual({ message: 'Hello' })
    expect(result.current.loading).toBe(false)
  })

  it('取得失敗時に error をセット', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        json: () => Promise.resolve({})
      } as Response)
    ) as any

    const { result } = renderHook(() => useFetch('/api/data'))

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(result.current.error).toBe('Failed to fetch')
  })
})
```

## useContext を含む Hook テスト

```typescript
// Hook
import { useContext } from 'react'
import { MyContext } from './MyContext'

export const useMyValue = () => {
  const context = useContext(MyContext)
  if (!context) {
    throw new Error('useMyValue must be used within MyProvider')
  }
  return context
}

// テスト
describe('useMyValue', () => {
  it('Provider 外で呼ぶとエラー', () => {
    expect(() => {
      renderHook(() => useMyValue())
    }).toThrow('useMyValue must be used within MyProvider')
  })

  it('Provider 内で正しい値を返す', () => {
    const wrapper = ({ children }: any) => (
      <MyContext.Provider value={{ value: 'test' }}>
        {children}
      </MyContext.Provider>
    )

    const { result } = renderHook(() => useMyValue(), { wrapper })

    expect(result.current.value).toBe('test')
  })
})
```

## act() の使用

Hook の状態更新は `act()` でラップする：

```typescript
// ❌ 警告が出る
const { result } = renderHook(() => useCounter())
result.current.increment() // 直接呼び出し

// ✅ 正しい
const { result } = renderHook(() => useCounter())
act(() => {
  result.current.increment()
})
```

## ベストプラクティス

1. **常に act() を使用** - state更新・effect発火時
2. **wrapper で Provider を提供** - Context 使用時
3. **async/await で非同期を処理** - useEffect含む
4. **result.current で最新値にアクセス** - 参照が変更される可能性
5. **rerender() で props変更をシミュレート**

```typescript
const { result, rerender } = renderHook(
  ({ count }) => useEffect(() => {}, [count]),
  { initialProps: { count: 1 } }
)

rerender({ count: 2 })
// useEffect が新しい依存で再実行される
```
