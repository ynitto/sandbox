# agent-app 共有Editor Workbench設計

## 決定

タスク／ワークフロー編集は、Statemachine Makerの共有ソースをagent-appへ直接組み込む。両アプリは別々にビルドし、アプリ固有のpreload、ナビゲーション、レイアウトだけをAdapterで切り替える。

iframeとrendererの文字列変換は使用しない。agent-appへの複製移植もしない。

## 構造

```text
statemachine-maker shared renderer
├── editor-host.js          preload差を吸収するHost Adapter
├── workbench-element.js    Shadow DOMへ編集面をmount
├── renderer.js             タスク編集と共有オーケストレーション
├── teaching.js             タスク教示
├── flow.js                 agent-flow編集・教示・実行・DAG描画
└── styles.css              standalone／Shadow DOM共通スタイル
        │
        ├── Statemachine Maker index.html
        └── agent-app index.html → automation-workbench.css
```

agent-appは`statemachine-workbench.navigate(payload)`で選択状態を渡す。共有Workbenchからの変更通知は`statemachine:changed`と`statemachine:teaching-started`のDOMイベントで受ける。

## UX方針

- agent-appのサイドバーがリポジトリとタスク／ワークフロー一覧を所有する。
- 共有Workbenchは選択項目の詳細、編集、教示、実行を所有する。
- agent-flowは固定タスク型へ寄せず、拡張可能なノード、エッジ、入力、実行ポリシーを維持する。
- DAGの差し戻しは循環依存ではなく、通常エッジと分離した有限の再作業ポリシーとして共有側で描画する。
- 戻る線の表現改善は`flow.js`と共有スタイルへ一度だけ実装し、両アプリへ反映する。

## 検証

- Statemachine Maker: 全121テスト成功（Electron実機E2Eを含む）
- agent-app: 全106テスト成功（Electron実機E2E、tmux統合を含む）
- `git diff --check`成功

## 今後の変更規則

- agent-app側へ編集ロジックを複製しない。
- Host差を共有renderer内の条件分岐として増やさず、Host Adapterかhost stylesheetへ閉じ込める。
- 新しいDAG表現は通常依存と差し戻しを別レイヤーで検証する。
- 共有ファイル追加時は`tools/agent-app/scripts/vendor.js`とscript読込契約テストを同時に更新する。
