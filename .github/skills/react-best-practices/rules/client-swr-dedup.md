---
title: SWR を使用した自動重複排除
impact: MEDIUM-HIGH
impactDescription: 自動重複排除
tags: client, swr, deduplication, data-fetching
---

## SWR を使用した自動重複排除

SWR により、コンポーネント インスタンス全体でリクエストの重複排除、キャッシュ、再検証が可能になります。

**誤り（重複排除なし、各インスタンスがフェッチされる）:**

```tsx
function UserList() {
  const [users, setUsers] = useState([])
  useEffect(() => {
    fetch('/api/users')
      .then(r => r.json())
      .then(setUsers)
  }, [])
}
```

**正しい例（複数のインスタンスが 1 つのリクエストを共有します）:**

```tsx
import useSWR from 'swr'

function UserList() {
  const { data: users } = useSWR('/api/users', fetcher)
}
```

**不変データの場合:**

```tsx
import { useImmutableSWR } from '@/lib/swr'

function StaticContent() {
  const { data } = useImmutableSWR('/api/config', fetcher)
}
```

**突然変異の場合:**

```tsx
import { useSWRMutation } from 'swr/mutation'

function UpdateButton() {
  const { trigger } = useSWRMutation('/api/user', updateUser)
  return <button onClick={() => trigger()}>Update</button>
}
```

参照: [https://swr.vercel.app](https://swr.vercel.app)
