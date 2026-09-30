# コンテキストマップ

エージェントツール群のモノレポ。ツールごとに独立したコンテキストを持つ。

## コンテキスト

- [agent-dashboard](./tools/agent-dashboard/CONTEXT.md) — agent-project / agent-flow / agent-amigos の操作・監視 UI
- [agent-app](./tools/agent-app/README.md) — ローカルリポジトリを登録し、エージェント CLI と tmux で会話する Electron アプリ。同じ画面のタスク（statemachine-use で動くステートマシン）とワークフロー（agent-flow）は、旧 statemachine-maker を統合したもの
- [web-test](./tools/web-test/README.md) — 条件からエージェントでテストケース（YAML）を作り、Playwright で実行してスクリーンショット付きの結果を出す Web アプリのテストツール

（他ツールのグロッサリは必要になった時点で追加する）
