# kiro-loop 制御面（差し込み口）

他グループがフォーク拡張した **kiro-loop** 向けのダッシュボード制御を、ここへ実装して差し込む想定のスタブ。

本リポジトリの upstream は `src/base/` と `src/features/agent-stack/` を維持する。
kiro-loop 側の変更はこのディレクトリに閉じれば、上流更新の取り込みがコンフリクトしにくい。

## やり方（プラグインではない）

動的ロードやマーケットプレイス型プラグインは作らない。次だけ守る:

1. **実装をこのツリーに置く**（`main/` / `preload.js` / `config.js`）
2. **`src/features/index.js` に列挙済み**（既に `require('./kiro-loop')` がある）
3. **IPC チャネル名は `kiroLoop:` プレフィックス**（agent-stack の `dashboard:` / `flow:` とぶつからないように）
4. **UI は `data-feature="kiro-loop"`** を付けてサイドバー／タブを追加する

## 実装チェックリスト

- [ ] `config.js` に既定設定（例: `kiroLoop.roots` / `command`）を書く
- [ ] `main/` に状態読取・操作モジュールを追加する
- [ ] `main/ipc.js` の `registerIpc(ctx)` でチャネルを登録する
  - `ctx.handle(channel, fn)` … `{ok,data|error}` 付きハンドラ
  - `ctx.loadConfig` / `ctx.saveConfig` … 設定
  - `ctx.shell` / `ctx.git` / `ctx.GitLabClient` … 共用インフラ
- [ ] `preload.js` に `(invoke) => (...args) => invoke('kiroLoop:...')` 形式で API を足す
- [ ] `src/renderer/index.html` にタブ／リスト枠を追加し、描画は `renderer.js` か
  別スクリプト（`<script src="../features/kiro-loop/renderer.js">`）へ
- [ ] テストを `test/kiro-loop-*.test.js` として追加する

## 上流マージのコツ

- agent-stack / base / renderer 本体へのパッチは最小にする
- 共有が必要なら `src/base/main/` へ汎用ヘルパだけ上げ、制御ロジックは上げない
- `features/index.js` の配列順だけ触る場合は、片方の追加を残すよう手で解消する
