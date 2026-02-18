# 多層防御バリデーション

## 概要

不正なデータが原因のバグを修正するとき、1箇所にバリデーションを追加すれば十分に感じる。しかしその単一チェックは、異なるコードパス、リファクタリング、またはモックによってバイパスされ得る。

**基本原則:** データが通過する全レイヤーでバリデーションを行う。バグを構造的に不可能にする。

## なぜ複数レイヤーか

単一バリデーション:「バグを修正した」
複数レイヤー:「バグを不可能にした」

異なるレイヤーが異なるケースをキャッチ:
- エントリバリデーションがほとんどのバグをキャッチ
- ビジネスロジックがエッジケースをキャッチ
- 環境ガードがコンテキスト固有の危険を防止
- デバッグログが他のレイヤーが失敗した時に助ける

## 4つのレイヤー

### レイヤー1: エントリポイントバリデーション
**目的:** API境界で明らかに不正な入力を拒否

```typescript
function createProject(name: string, workingDirectory: string) {
  if (!workingDirectory || workingDirectory.trim() === '') {
    throw new Error('workingDirectory cannot be empty');
  }
  if (!existsSync(workingDirectory)) {
    throw new Error(`workingDirectory does not exist: ${workingDirectory}`);
  }
  if (!statSync(workingDirectory).isDirectory()) {
    throw new Error(`workingDirectory is not a directory: ${workingDirectory}`);
  }
  // ... 続行
}
```

### レイヤー2: ビジネスロジックバリデーション
**目的:** この操作に対してデータが意味を成すことを保証

```typescript
function initializeWorkspace(projectDir: string, sessionId: string) {
  if (!projectDir) {
    throw new Error('projectDir required for workspace initialization');
  }
  // ... 続行
}
```

### レイヤー3: 環境ガード
**目的:** 特定のコンテキストで危険な操作を防止

```typescript
async function gitInit(directory: string) {
  // テスト時、一時ディレクトリ外での git init を拒否
  if (process.env.NODE_ENV === 'test') {
    const normalized = normalize(resolve(directory));
    // Windows/Unix両対応: os.tmpdir() を使用
    const tmpDir = normalize(resolve(tmpdir()));

    if (!normalized.startsWith(tmpDir)) {
      throw new Error(
        `Refusing git init outside temp dir during tests: ${directory}`
      );
    }
  }
  // ... 続行
}
```

### レイヤー4: デバッグ計装
**目的:** フォレンジクスのためのコンテキストをキャプチャ

```typescript
async function gitInit(directory: string) {
  const stack = new Error().stack;
  logger.debug('About to git init', {
    directory,
    cwd: process.cwd(),
    stack,
  });
  // ... 続行
}
```

## パターンの適用

バグを見つけたら:

1. **データフローをトレース** - 不正な値はどこで発生し、どこで使用されるか？
2. **全チェックポイントをマップ** - データが通過する全ポイントをリスト化
3. **各レイヤーにバリデーションを追加** - エントリ、ビジネス、環境、デバッグ
4. **各レイヤーをテスト** - レイヤー1をバイパスしてみて、レイヤー2がキャッチすることを検証

## セッションからの実例

バグ: 空の `projectDir` がソースコード内で `git init` を実行

**データフロー:**
1. テストセットアップ → 空文字列
2. `Project.create(name, '')`
3. `WorkspaceManager.createWorkspace('')`
4. `git init` が `process.cwd()` で実行

**追加した4レイヤー:**
- レイヤー1: `Project.create()` が空でない/存在する/書き込み可能を検証
- レイヤー2: `WorkspaceManager` が projectDir が空でないことを検証
- レイヤー3: `WorktreeManager` がテスト中に tmpdir 外での git init を拒否
- レイヤー4: git init 前のスタックトレースログ

**結果:** 全1847テスト通過、バグの再現が不可能

## 重要な洞察

4レイヤーすべてが必要だった。テスト中、各レイヤーが他のレイヤーが見逃したバグをキャッチ:
- 異なるコードパスがエントリバリデーションをバイパス
- モックがビジネスロジックチェックをバイパス
- 異なるプラットフォームのエッジケースに環境ガードが必要
- デバッグログが構造的な誤用を特定

**1つのバリデーションポイントで止めない。** 全レイヤーにチェックを追加する。
