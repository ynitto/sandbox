# ワークフロー

既存の `adhoc-flow` feature を、agent-flow の実行とカスタムフロー管理を行う「ワークフロー」画面として使う。

## 実行

- Git 管理された cwd、依頼、フローを選ぶ。
- Windows の cwd は実行時だけ WSL の `/mnt/<drive>/...` に変換する。履歴は入力表記のまま最大20件保持する。
- フローは自動、`agent-flow patterns --json` の標準パターン、カスタムから選ぶ。カスタムは
  `~/.agents/workflows/*.json`（ユーザー共通）に加え、入力した Git リポジトリの
  `.agent-flow/workflows/*.json`（リポジトリ共有）を読む。後者は statemachine の
  `.statemachine/<name>/workflow.yaml` と同様にリポジトリへ commit して共有できる。
- 同じ id が両方にある場合はリポジトリ共有版を優先する。フォルダ入力を確定すると、その
  リポジトリのカスタムフローが選択肢へ加わる。
- inbox の `workspace` に cwd のリポジトリと現在の branch/HEAD を固定する。成果は `af/<run-id>` branch に保存される。

## 設定

- カスタムフローは1フロー1JSONファイル。画面で新規作成したものは従来どおりユーザー共通へ
  保存する。リポジトリ共有ファイルを読み込んで編集した場合は同じリポジトリへ書き戻す。
- ノードは `kind`、依存、位置、tier を持つ。
- 実行時に tier の利用可能な候補を `{agent_cli, model}` へ固定し、下位tierへは降格しない。
- 旧 `adhocFlow.presets` は初回表示時にファイルへ移行する。

## バックログ連携

実行前レビューと再実行で同じフロー一覧を選べる。選択は `backlog/<task-id>.flow.json` に固定し、agent-project が `--pattern` または `--plan-file` として agent-flow へ渡す。sidecar が無い場合は自動。

テスト: `node test/adhoc-flow.test.js`
