# GraphQL 設計ガイド

## 目次

- [スキーマ設計（Query / Mutation / Subscription）](#スキーマ設計query--mutation--subscription)
- [N+1 問題対策（DataLoader パターン）](#n1-問題対策dataloader-パターン)
- [認可設計（フィールドレベル）](#認可設計フィールドレベル)

## スキーマ設計（Query / Mutation / Subscription）

```graphql
type Query {
  user(id: ID!): User
  users(filter: UserFilter, pagination: PaginationInput): UserConnection!
}

type Mutation {
  createUser(input: CreateUserInput!): CreateUserPayload!
  updateUser(id: ID!, input: UpdateUserInput!): UpdateUserPayload!
  deleteUser(id: ID!): DeleteUserPayload!
}

type Subscription {
  orderStatusChanged(orderId: ID!): Order!
}

# Relay スタイルの Cursor Connection
type UserConnection {
  edges: [UserEdge!]!
  pageInfo: PageInfo!
  totalCount: Int!
}
```

## N+1 問題対策（DataLoader パターン）

- リゾルバで直接 DB アクセスしない
- `DataLoader` でバッチ・キャッシュを実装する
- `@dataLoader` ディレクティブ等でリゾルバに宣言的に紐づける

## 認可設計（フィールドレベル）

```graphql
type User {
  id: ID!
  name: String!
  email: String! @auth(requires: OWNER_OR_ADMIN)
  internalNotes: String @auth(requires: ADMIN)
}
```
