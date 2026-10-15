# コンテキストマップ

エージェントツール群のモノレポ。ツールごとに独立したコンテキストを持つ。

## コンテキスト

- [agent-dashboard](./tools/agent-dashboard/CONTEXT.md) — agent-project / agent-flow / agent-amigos の操作・監視 UI
- [agent-app](./tools/agent-app/README.md) — ローカルリポジトリを登録し、エージェント CLI と tmux で会話する Electron アプリ。同じ画面のタスク（statemachine-use で動くステートマシン）とワークフロー（agent-flow）は、旧 statemachine-maker を統合したもの
- [webui-test](./tools/webui-test/README.md) — 条件からエージェントでテストケース（YAML）を作り、Playwright で実行してスクリーンショット付きの結果を出す Web アプリのテストツール
- [testcase-lint](./tools/testcase-lint/README.md) — 自然文のテストケース（JSON / YAML）を、テストケースとしての妥当性（曖昧さ・重複・矛盾・漏れ・不備、判定の強さ、実行できるか、条件の組み合わせ、仕様・実装との突き合わせ）で点検する CLI。ふだんはオプション無しで実行する。LLM は使わない

（他ツールのグロッサリは必要になった時点で追加する）
