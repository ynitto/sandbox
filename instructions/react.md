# React コーディング指示

React / Next.js フロントエンド開発に適用するコーディング規範。

## コンポーネント設計

- 関数コンポーネントのみ使う（クラスコンポーネントは使わない）
- 1 コンポーネント = 1 責務。肥大化したら分割する
- Props は明示的に型定義する

```tsx
type ButtonProps = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
};

function Button({ label, onClick, disabled = false }: ButtonProps) {
  return (
    <button onClick={onClick} disabled={disabled}>
      {label}
    </button>
  );
}
```

## フック

- カスタムフックでロジックをコンポーネントから分離する（`use` プレフィックス必須）
- `useEffect` の依存配列は正確に指定する（`eslint-plugin-react-hooks` で自動チェック）
- `useCallback` / `useMemo` は計測してから使う（早期最適化を避ける）

## 状態管理

- ローカル状態: `useState` / `useReducer`
- サーバー状態: TanStack Query（React Query）または SWR
- グローバル状態: Zustand（軽量）または Jotai（アトミック）
- Redux は大規模アプリのみ検討する

## レンダリング最適化

- `React.memo` は re-render が問題になってから適用する
- リスト要素の `key` には安定した一意の ID を使う（インデックスは避ける）
- 重い計算は `useMemo` で最適化する

## データフェッチ

- コンポーネント内に直接 `fetch` を書かない。カスタムフックやサービス層に分離する
- ローディング・エラー・空状態を常に処理する
- 楽観的更新（Optimistic Update）でユーザー体験を向上する

## スタイリング

- CSS Modules または Tailwind CSS を優先する
- インラインスタイルは動的な値のみに限定する
- グローバル CSS は最小限にし、コンポーネントスコープに閉じる

## アクセシビリティ (a11y)

- セマンティック HTML を使う（`div` クリックより `button`）
- フォーム要素には `label` を関連付ける
- `aria-*` 属性はセマンティクスで補えない場合のみ使う
- `eslint-plugin-jsx-a11y` でアクセシビリティを自動チェックする

## ファイル構成

```
src/
  components/       # 再利用可能な UI コンポーネント
    Button/
      Button.tsx
      Button.test.tsx
      index.ts      # 名前付き export
  features/         # 機能ドメイン別
    auth/
      useAuth.ts
      AuthForm.tsx
  hooks/            # グローバルカスタムフック
  lib/              # 外部ライブラリのラッパー・設定
  types/            # 共有型定義
```

## テスト

- Vitest + Testing Library でユニット・統合テストを書く
- ユーザーの操作視点でテストする（実装詳細をテストしない）
- スナップショットテストは UI の意図しない変更検知に限定する
