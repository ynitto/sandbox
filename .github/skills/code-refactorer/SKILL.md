---
name: code-refactorer
description: 振る舞いを変えずにコード構造を改善する外科的リファクタリングスキル。関数の抽出・変数名の改善・神クラスの分解・型安全性の向上・コードスメルの除去・デザインパターンの適用など、保守性・可読性・設計品質を段階的に高める。「リファクタリングして」「コードを改善して」「設計を整理して」「クラスを分割して」「コードスメルを除去して」「デザインパターンを適用して」などのリクエストで発動する。code-simplifier より踏み込んだ構造的改善を行う。
metadata:
  version: 1.0.0
  tier: experimental
  category: refactoring
  tags:
    - refactoring
    - code-quality
    - design-patterns
    - clean-code
    - solid
---

# code-refactorer

振る舞いを変えずにコード構造を段階的に改善する。リファクタリングは革命ではなく、小さな変化の積み重ね。

## ゴールデンルール

1. **振る舞いを保つ** — リファクタリングは「何をするか」ではなく「どのようにするか」だけを変える
2. **小さなステップ** — 一度に一つの変更、変更後にテストを実行する
3. **バージョン管理を活用** — 安全な状態ごとにコミットする
4. **テストが必須** — テストなしのリファクタリングは「編集」に過ぎない
5. **一点集中** — リファクタリングと機能追加を同時に行わない

## リファクタリングしない場合

- 動いていて、今後変更しないコード
- テストのない重要な本番コード（先にテストを追加する）
- 締め切り直前の状況
- 「なんとなく」— 明確な目的がなければ手をつけない

---

## ワークフロー

### Step 1: 現状を把握する

1. ユーザーが対象ファイル・範囲を指定している場合はそれを使う
2. 指定がない場合は `git diff HEAD` で変更ファイルを確認する
3. 対象ファイルを読んで全体像と既存のテストを把握する
4. テストがない場合はリファクタリング前に追加を提案する

### Step 2: コードスメルを検出する

以下の10種類のコードスメルを診断する:

| # | コードスメル | 症状 | 対処法 |
|---|------------|------|--------|
| 1 | **長すぎる関数** | 50行超・複数の責務を持つ関数 | 関数を抽出して単一責務に分割 |
| 2 | **重複コード** | 同じロジックが複数箇所に存在 | 共通関数・ユーティリティに抽出 |
| 3 | **巨大クラス（神クラス）** | 1クラスが多くの責務を担う | 単一責務原則で複数クラスに分割 |
| 4 | **長すぎるパラメータリスト** | 5個以上の引数を持つ関数 | パラメータオブジェクトやビルダーパターンに変換 |
| 5 | **フィーチャーエンビー** | 他クラスのデータを多用するメソッド | ロジックをデータを持つクラスに移動 |
| 6 | **プリミティブ執着** | ドメイン概念を基本型で表現 | ドメイン型を作成して概念を明確化 |
| 7 | **マジックナンバー・文字列** | 説明なしのリテラル値が散在 | 意図を説明する名前付き定数に置き換え |
| 8 | **深いネスト（矢印コード）** | 多重に入れ子になった条件分岐 | ガード節・早期リターンで平坦化 |
| 9 | **デッドコード** | 使われていない関数・変数・コメントアウト | 削除する（バージョン管理が履歴を保持） |
| 10 | **過剰な親密さ** | クラスが他クラスの内部に深くアクセス | カプセル化を徹底し、操作の委譲に変える |

### Step 3: リファクタリング計画を立てる

検出したコードスメルを優先度付けする:

| 優先度 | 基準 | 例 |
|--------|------|-----|
| **高** | 理解・変更が困難な箇所 | 長い関数、深いネスト、重複コード |
| **中** | 設計上の問題がある箇所 | 神クラス、フィーチャーエンビー、長いパラメータリスト |
| **低** | 品質向上になる箇所 | マジックナンバー、命名改善、デッドコード除去 |

計画を1行ずつ説明してからユーザーに確認を取る。

### Step 4: 小さな変更を繰り返す

各変更は以下のサイクルで実施:

```
変更前にコミット → 1つの変更を実施 → テスト実行 → 動作確認 → コミット
```

- 変更内容を1行で説明してから実施する（例:「`processData`関数から検証ロジックを`validateInput`として抽出します」）
- 一度に複数の手法を混在させない
- テストが通らない場合はすぐに差し戻す

### Step 5: 結果をサマリーする

```
## code-refactorer 結果

### 実施したリファクタリング
- [手法名]: <何をどう変えたか>

### 改善された指標
- 変更前: <関数の行数・複雑度など>
- 変更後: <改善後の数値>

### 残課題（該当あれば）
- <今回対応しなかった問題と理由>
```

---

## 主要なリファクタリング手法

### 関数の抽出（Extract Method）

長い関数から意味のある処理のまとまりを関数として切り出す。

**Before:**
```typescript
function printReport(users: User[]) {
  // ヘッダー印刷（10行）
  console.log("=".repeat(40));
  console.log("ユーザーレポート");
  console.log(`生成日時: ${new Date().toLocaleDateString("ja-JP")}`);
  console.log("=".repeat(40));

  // ユーザー一覧印刷（15行）
  for (const user of users) {
    const status = user.active ? "有効" : "無効";
    console.log(`${user.name} (${user.email}) - ${status}`);
  }
  console.log(`合計: ${users.length}名`);
}
```

**After:**
```typescript
function printReport(users: User[]) {
  printHeader();
  printUserSection(users);
}

function printHeader() {
  console.log("=".repeat(40));
  console.log("ユーザーレポート");
  console.log(`生成日時: ${new Date().toLocaleDateString("ja-JP")}`);
  console.log("=".repeat(40));
}

function printUserSection(users: User[]) {
  for (const user of users) {
    const status = user.active ? "有効" : "無効";
    console.log(`${user.name} (${user.email}) - ${status}`);
  }
  console.log(`合計: ${users.length}名`);
}
```

### ガード節による平坦化（Replace Nested Conditional with Guard Clauses）

**Before:**
```typescript
function getDiscount(user: User): number {
  if (user !== null) {
    if (user.active) {
      if (user.membershipYears >= 5) {
        return 0.2;
      } else {
        return 0.1;
      }
    } else {
      return 0;
    }
  } else {
    return 0;
  }
}
```

**After:**
```typescript
function getDiscount(user: User): number {
  if (user === null || !user.active) return 0;
  if (user.membershipYears >= 5) return 0.2;
  return 0.1;
}
```

### 型安全性の向上（Introduce Type Safety）

**Before:**
```typescript
function createUser(name: string, role: string, age: number) {
  if (role !== "admin" && role !== "member") throw new Error("不正なロール");
  if (age < 0 || age > 150) throw new Error("不正な年齢");
  // ...
}
```

**After:**
```typescript
type Role = "admin" | "member";

class Age {
  constructor(readonly value: number) {
    if (value < 0 || value > 150) throw new Error("不正な年齢");
  }
}

function createUser(name: string, role: Role, age: Age) {
  // コンパイラが不正な値を排除してくれる
}
```

### Strategyパターン（条件分岐の置き換え）

**Before:**
```typescript
function calculateTax(amount: number, country: string): number {
  if (country === "JP") return amount * 0.1;
  if (country === "US") return amount * 0.08;
  if (country === "DE") return amount * 0.19;
  throw new Error(`未対応の国: ${country}`);
}
```

**After:**
```typescript
interface TaxStrategy {
  calculate(amount: number): number;
}

const taxStrategies: Record<string, TaxStrategy> = {
  JP: { calculate: (amount) => amount * 0.1 },
  US: { calculate: (amount) => amount * 0.08 },
  DE: { calculate: (amount) => amount * 0.19 },
};

function calculateTax(amount: number, country: string): number {
  const strategy = taxStrategies[country];
  if (!strategy) throw new Error(`未対応の国: ${country}`);
  return strategy.calculate(amount);
}
```

---

## リファクタリング操作リファレンス

| 操作名 | 概要 | 対象コードスメル |
|--------|------|----------------|
| 関数の抽出 | コードセグメントを別関数として切り出す | 長すぎる関数 |
| 変数の改名 | 意図を表す名前に変更する | 不明瞭な命名 |
| パラメータオブジェクトの導入 | 関連するパラメータをオブジェクトにまとめる | 長すぎるパラメータリスト |
| マジックナンバーの定数化 | リテラル値を名前付き定数に置き換える | マジックナンバー |
| デッドコードの削除 | 使われていない宣言を削除する | デッドコード |
| クラスの分割 | 1つのクラスを責務ごとに分ける | 神クラス |
| メソッドの移動 | ロジックを適切なオブジェクトに移動する | フィーチャーエンビー |
| ガード節の導入 | 早期リターンで条件の入れ子を平坦化する | 深いネスト |
| 条件の多態性への置き換え | if/switchをポリモーフィズムで代替する | 複雑な条件分岐 |
| ドメイン型の導入 | プリミティブをドメインクラスに昇格する | プリミティブ執着 |
| インターフェースの抽出 | 共通契約をインターフェースとして定義する | 過剰な親密さ |
| Strategyパターンの適用 | 交換可能な振る舞いをオブジェクトとして表現 | 長い条件分岐 |
| Chain of Responsibilityの適用 | 処理の連鎖を独立したハンドラーに分解 | 複雑なバリデーション処理 |
| Null Objectパターンの適用 | nullチェックをNull Objectで置き換える | null/undefined過剰チェック |
| 重複コードの抽出 | 複数箇所の共通ロジックを1か所に集約 | 重複コード |
| 依存性の注入 | 依存をコンストラクタ経由で受け取る | 緊密な結合 |

---

## リファクタリングチェックリスト

### コード品質
- [ ] すべての関数が単一の明確な責務を持つ
- [ ] 重複したロジックが存在しない
- [ ] 変数名・関数名が意図を説明している
- [ ] マジックナンバー・文字列が定数化されている
- [ ] コメントアウトされたコードが削除されている

### 構造
- [ ] クラス・モジュールが単一責務に従っている
- [ ] ガード節でネストが解消されている
- [ ] パラメータリストが適切（通常3個以下）
- [ ] クラス間の過剰な親密さがない

### 型安全性
- [ ] 関数シグネチャに明示的な型がある
- [ ] ドメイン概念にカスタム型が使われている
- [ ] インターフェースが契約を文書化している
- [ ] null/undefined の扱いが明示的

### テスト
- [ ] リファクタリング前にテストが通っている
- [ ] 各ステップ後にテストが通っている
- [ ] エッジケースとエラーパスがカバーされている
- [ ] リファクタリング中に新機能が追加されていない
