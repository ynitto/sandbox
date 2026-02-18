# 根本原因トレーシング

## 概要

バグはコールスタックの深い位置で発現することが多い（誤ったディレクトリでのgit init、誤った場所でのファイル作成、誤ったパスでのデータベースオープン）。本能的にエラーが現れた場所で修正したくなるが、それは症状の治療に過ぎない。

**基本原則:** コールチェーンを逆方向にトレースして元のトリガーを見つけ、ソースで修正する。

## いつ使うか

**使用する場面:**
- エラーが実行の深い位置で発生（エントリポイントではない）
- スタックトレースが長いコールチェーンを示す
- 不正なデータの発生元が不明
- どのテスト/コードが問題を引き起こしているか特定が必要

## トレースプロセス

### 1. 症状を観察する
```
Error: git init failed in /Users/jesse/project/packages/core
```

### 2. 直接的な原因を見つける
**どのコードが直接これを引き起こしているか？**
```typescript
await execFileAsync('git', ['init'], { cwd: projectDir });
```

### 3. 「何がこれを呼び出したか？」を問う
```typescript
WorktreeManager.createSessionWorktree(projectDir, sessionId)
  → Session.initializeWorkspace() が呼び出し
  → Session.create() が呼び出し
  → Project.create() でのテスト
```

### 4. 遡り続ける
**どんな値が渡されたか？**
- `projectDir = ''`（空文字列！）
- `cwd` としての空文字列は `process.cwd()` に解決される
- それはソースコードのディレクトリ！

### 5. 元のトリガーを見つける
**空文字列はどこから来たか？**
```typescript
const context = setupCoreTest(); // { tempDir: '' } を返す
Project.create('name', context.tempDir); // beforeEach の前にアクセス！
```

## スタックトレースの追加

手動でトレースできない場合、計装を追加:

```typescript
// 問題のある操作の前に
async function gitInit(directory: string) {
  const stack = new Error().stack;
  console.error('DEBUG git init:', {
    directory,
    cwd: process.cwd(),
    nodeEnv: process.env.NODE_ENV,
    stack,
  });

  await execFileAsync('git', ['init'], { cwd: directory });
}
```

**重要:** テストでは `console.error()` を使用する（loggerではない — 表示されない場合がある）

**実行してキャプチャ:**

Unix/macOS:
```bash
npm test 2>&1 | grep 'DEBUG git init'
```

Windows (PowerShell):
```powershell
npm test 2>&1 | Select-String 'DEBUG git init'
```

**スタックトレースを分析:**
- テストファイル名を探す
- 呼び出しをトリガーした行番号を見つける
- パターンを特定する（同じテスト？同じパラメータ？）

## どのテストが汚染を引き起こしているか見つける

テスト中に何かが出現するが、どのテストか分からない場合:

このディレクトリの二分探索スクリプトを使用:

Unix/macOS:
```bash
./find-polluter.sh '.git' 'src/**/*.test.ts'
```

Windows (PowerShell):
```powershell
.\find-polluter.ps1 -PollutionCheck '.git' -TestPattern 'src/**/*.test.ts'
```

テストを1つずつ実行し、最初の汚染者で停止する。使い方はスクリプトを参照。

## 実例: 空の projectDir

**症状:** `.git` が `packages/core/`（ソースコード）に作成された

**トレースチェーン:**
1. `git init` が `process.cwd()` で実行 ← 空の cwd パラメータ
2. WorktreeManager が空の projectDir で呼び出された
3. Session.create() が空文字列を渡した
4. テストが beforeEach の前に `context.tempDir` にアクセス
5. setupCoreTest() は最初 `{ tempDir: '' }` を返す

**根本原因:** トップレベルの変数初期化が空の値にアクセス

**修正:** tempDir を、beforeEach の前にアクセスするとスローする getter にした

**さらに多層防御を追加:**
- レイヤー1: `Project.create()` がディレクトリを検証
- レイヤー2: `WorkspaceManager` が空でないことを検証
- レイヤー3: `NODE_ENV` ガードがテスト中に tmpdir 外での git init を拒否
- レイヤー4: git init 前のスタックトレースログ

## 核心原則

**症状が現れた場所だけで修正してはならない。** 逆方向にトレースして元のトリガーを見つける。

## スタックトレースのコツ

**テスト時:** logger ではなく `console.error()` を使用 — logger は抑制される場合がある
**操作前:** 失敗後ではなく、危険な操作の前にログを取る
**コンテキストを含める:** ディレクトリ、cwd、環境変数、タイムスタンプ
**スタックをキャプチャ:** `new Error().stack` で完全なコールチェーンを表示

## 実際の効果

デバッグセッション（2025-10-03）からの実績:
- 5レベルのトレースで根本原因を発見
- ソースで修正（getter バリデーション）
- 4レイヤーの防御を追加
- 1847テスト通過、汚染ゼロ
