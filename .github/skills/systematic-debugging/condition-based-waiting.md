# 条件ベースの待機

## 概要

不安定なテストは、任意の遅延でタイミングを推測していることが多い。これは競合状態を生み、速いマシンでは通過するが、負荷時やCIでは失敗するテストになる。

**基本原則:** 「どれくらいかかるか」の推測ではなく、関心のある実際の条件を待つ。

## いつ使うか

**使用する場面:**
- テストに任意の遅延がある（`setTimeout`、`sleep`、`time.sleep()`）
- テストが不安定（たまに通過し、負荷時に失敗）
- テストが並列実行時にタイムアウト
- 非同期操作の完了を待つ

**使用しない場面:**
- 実際のタイミング動作をテスト中（デバウンス、スロットルの間隔）
- 任意のタイムアウトを使う場合は常になぜかを文書化

## コアパターン

```typescript
// ❌ 変更前: タイミングを推測
await new Promise(r => setTimeout(r, 50));
const result = getResult();
expect(result).toBeDefined();

// ✅ 変更後: 条件を待つ
await waitFor(() => getResult() !== undefined);
const result = getResult();
expect(result).toBeDefined();
```

## クイックパターン

| シナリオ | パターン |
|---------|---------|
| イベントを待つ | `waitFor(() => events.find(e => e.type === 'DONE'))` |
| 状態を待つ | `waitFor(() => machine.state === 'ready')` |
| 件数を待つ | `waitFor(() => items.length >= 5)` |
| ファイルを待つ | `waitFor(() => fs.existsSync(path))` |
| 複合条件 | `waitFor(() => obj.ready && obj.value > 10)` |

## 実装

汎用ポーリング関数:
```typescript
async function waitFor<T>(
  condition: () => T | undefined | null | false,
  description: string,
  timeoutMs = 5000
): Promise<T> {
  const startTime = Date.now();

  while (true) {
    const result = condition();
    if (result) return result;

    if (Date.now() - startTime > timeoutMs) {
      throw new Error(`Timeout waiting for ${description} after ${timeoutMs}ms`);
    }

    await new Promise(r => setTimeout(r, 10)); // 10msごとにポーリング
  }
}
```

ドメイン固有のヘルパー（`waitForEvent`、`waitForEventCount`、`waitForEventMatch`）の完全な実装は、このディレクトリの `condition-based-waiting-example.ts` を参照。

## よくある間違い

**❌ ポーリングが速すぎる:** `setTimeout(check, 1)` - CPUを浪費
**✅ 修正:** 10msごとにポーリング

**❌ タイムアウトなし:** 条件が満たされないと無限ループ
**✅ 修正:** 明確なエラーを含むタイムアウトを常に含める

**❌ 古いデータ:** ループ前に状態をキャッシュ
**✅ 修正:** 最新データのためにループ内で getter を呼ぶ

## 任意のタイムアウトが正しい場合

```typescript
// ツールが100msごとにtick — 部分出力を検証するために2tickが必要
await waitForEvent(manager, 'TOOL_STARTED'); // まず: 条件を待つ
await new Promise(r => setTimeout(r, 200));   // その後: タイミング動作を待つ
// 200ms = 100ms間隔で2tick — 文書化され正当化されている
```

**要件:**
1. まずトリガー条件を待つ
2. 既知のタイミングに基づく（推測ではない）
3. なぜかを説明するコメント

## 実際の効果

デバッグセッション（2025-10-03）からの実績:
- 3ファイルにまたがる15の不安定なテストを修正
- 通過率: 60% → 100%
- 実行時間: 40%高速化
- 競合状態ゼロ
