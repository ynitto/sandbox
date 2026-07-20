# t4 成果 — プロジェクト情報ペイロードの `consistencyGate`

表示タスク（UI 側）への申し送り。producer 側は完了済み。

## ペイロード契約

`readProject()` の返り値（`project.js`）に 1 キー追加。IPC / preload はスキーマ非依存の素通しなので
`state.project.consistencyGate` でそのまま renderer に届く。

```js
consistencyGate: {
  configFile: '/abs/path/.agents/agent-project.yaml' | null, // 有効化導線で開く先。未検出なら null
  regressionWired: true | false,   // 判定にはこちらを使う
  intakeWired: true | false,
  regressionCmd: '<コマンド文字列>' | null,  // 表示用のみ
  intakeCmd: '<コマンド文字列>' | null,
}
```

- `*Wired` は「設定 yaml に当該キーが空でない値で書かれているか」だけ。コマンドは実行しないし、
  値が正しい codd-gate 呼び出しかは判定しない。
- `*Cmd` はクォートを剥がした素の値（`stripQuotes` 後）。null になるのは未設定時のみ。
- `configFile` はワークスペース配下で見つかった設定の実パス。**未結線でも設定ファイル自体はあり得る**
  （`configFile != null` かつ `regressionWired === false`）。有効化導線はこのパスを外部エディタで
  開く用途を想定（dashboard 内蔵エディタの allowlist は触っていない ＝ UI からの状態書換経路なし）。

## 実装位置

| 位置 | 内容 |
|---|---|
| `src/features/agent-project/main/project.js` `_configFromWorkspace(cfg, ws)` | 設定がワークスペース配下かの判定。`resolveProjectRoot` のインライン判定を関数化して共用 |
| 同 `consistencyGateStatus(cfg, workspace)` | 上記オブジェクトを組む |
| 同 `readProject()` の返り値リテラル | `consistencyGate:` を `autonomy` の次に追加。設定は既存の `projectCfg` を再利用（yaml の読み直しなし） |

## 未結線時に UI が出すべき導線（README `tools/agent-project/README.md:262-300` が正典）

1. `configFile` を編集して 2 行足す（`regression_cmd` / `intake_cmd`）。
2. `regression_cmd` のみ注入 CLI あり: `python3 codd_gate_regression.py --config <configFile>`。
   `intake_cmd` に対応する CLI は無く、yaml 直接編集のみ。
