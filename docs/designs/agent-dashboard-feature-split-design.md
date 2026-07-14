# agent-dashboard 制御面分離

> 日付: 2026-07-14  
> 対象: `tools/agent-dashboard/`  
> 関連: [`agent-tools-rename-design.md`](./agent-tools-rename-design.md)・
> [`tools/agent-dashboard/src/features/kiro-loop/README.md`](../../tools/agent-dashboard/src/features/kiro-loop/README.md)

## 目的

agent-dashboard を次の層に分け、**上流のダッシュボード更新を取り込みつつ、別グループが kiro-loop 制御面を独自に足せる**ようにする。

フルプラグイン（動的ロード・隔離・版管理）までは作らない。ソースツリー上の分離と、薄い合成点（feature 列挙）だけを置く。

## 構成

```
tools/agent-dashboard/src/
├── base/main/           # Electron シェル・config 合成・git・GitLab・共通 IPC
├── features/
│   ├── index.js         # 載せる制御面の列挙（ここだけが合成点）
│   ├── agent-stack/     # agent-project + agent-flow（本リポジトリ維持）
│   └── kiro-loop/       # 将来 / 他グループ差し込み（スタブ）
├── main/                # 旧パス互換シム（require の移行用）
├── preload.js           # base API + 各 feature の preloadApi を合成
└── renderer/            # UI（当面は agent-stack 画面が主体）
```

| 層 | 責務 | 所有者イメージ |
|----|------|----------------|
| `base` | 窓・プロトコル・設定マージ・git・汎用 GitLab・shell | upstream |
| `agent-stack` | charter/backlog/needs/flow・操作・オーサリング | upstream |
| `kiro-loop` | kiro-loop の可視化・操作 | フォーク拡張グループ |

agent-project と agent-flow は run-id 相互リンクや cancel/resubmit のタスク同期で結合が強いため、**ひとつの `agent-stack` にまとめる**。きりの良い単位は「制御スタック」であって「ツール 1 つ」ではない。

## 合成契約（feature 記述子）

各 feature は次を export する（`src/features/*/index.js`）:

```js
{
  id: 'agent-stack',                 // 識別子
  configDefaults: { ... },           // base DEFAULT_CONFIG へ deepMerge
  registerIpc(ctx) { ... },          // ctx.handle / loadConfig / shell / …
  preloadApi() { return {            // window.api メソッド工場
    foo: (invoke) => (a) => invoke('agentStack:foo', { a }),
  }; },
}
```

`src/features/index.js` の配列に並べるだけで、起動時に IPC・preload・設定既定へ反映される。

## UI

 renderer はバンドラなしの単一スクリプトのまま。分離初期では:

- 既存タブに `data-feature="agent-stack"` を付与
- kiro-loop 用の空き枠としてサイドバーに `data-feature="kiro-loop"` のプレースホルダを置く（非表示可）

大規模な renderer 分割は必要になった時点で行う（テストが `renderer.js` の関数を文字列抽出しているため、無理に同時分割しない）。

## 非目標

- npm ワークスペース化やパッケージ分割
- 実行時の feature ホットロード
- kiro-loop 本体の実装（スタブと手順書のみ）

## 互換

`src/main/*.js` は実体へのシムを残し、既存テストの `require('../src/main/…')` を壊さない。
新規コードは `src/base/…` / `src/features/…` を直接指す。

## 受け入れ目安

- 既存 `npm test` がグリーン
- `features/kiro-loop` が no-op のまま起動できる
- 設計どおり、kiro-loop 実装をそのディレクトリに閉じられることが README で追える
