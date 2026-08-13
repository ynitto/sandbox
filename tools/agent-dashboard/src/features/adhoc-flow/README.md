# ワークフロー

既存の `adhoc-flow` feature を、agent-flow の実行とカスタムフロー管理を行う「ワークフロー」画面として使う。

## 実行

- Git 管理された cwd、依頼、フローを選ぶ。
- Windows の cwd は実行時だけ WSL の `/mnt/<drive>/...` に変換する。履歴は入力表記のまま最大20件保持する。
- フローは自動、`agent-flow patterns --json` の標準パターン、カスタムから選ぶ。カスタムは
  `~/.agents/workflows/*.json`（ユーザー共通）に加え、入力した Git リポジトリの
  `.agents/workflows/*.json`（リポジトリ共有）を**読み取り専用で**読む。後者は
  statemachine の `.statemachine/<name>/workflow.yaml` と同様に、通常のリポジトリ作業で
  commit し、各 clone の pull/checkout または CI による配布で共有する。
- 同じ id が両方にある場合はリポジトリ共有版を優先する。実行エンジンへ登録済みの
  フォルダだけを探索し、未登録フォルダの定義は読み込まない。
- 工程の「追加ルール」（ノードへ足すプロンプト）も、ユーザー共通の手法カタログに加えて
  `.agents/methods/*.json` を読み取り専用で探索する。同じ id はリポジトリ版を優先する。
  選択時に本文と source hash をワークフローのノードへ複製するため、後から手法ファイルが
  変わっても保存済みワークフローの振る舞いが暗黙に変わることはない。
- inbox の `workspace` に cwd のリポジトリと現在の branch/HEAD を固定する。成果は `af/<run-id>` branch に保存される。

## 設定

- カスタムフローは1フロー1JSONファイル。画面で新規作成・編集・削除できるのは従来どおり
  ユーザー共通だけ。リポジトリ共有版は dashboard から書き換えず、通常の Git 作業で管理する。
- 作業ルールも同じ規則とする。登録フォルダと同梱カタログは読み取り専用で、変更したい場合は
  「自分用にコピー」でユーザーホームの tuning へ別 ID の定義を作ってから編集する。
- ノードは `kind`、依存、位置、tier を持つ。
- 実行時に tier の利用可能な候補を `{agent_cli, model}` へ固定し、下位tierへは降格しない。
- 機能（kind）・オプション（continuation）ごとの実行可能レベルは orchestration の
  `flow-tiers.js` カタログが宣言する。範囲外の固定 tier は plan 生成で弾き、auto tier は
  実行方針が不適格な段を選びうる機能だけ今の段を適格範囲へ丸めて固定する（それ以外は
  従来どおり実行時の方針を継承する）。設計:
  `docs/plans/2026-08-12-agent-flow-tier-eligibility-strategy-design.md`。
- 旧 `adhocFlow.presets` は初回表示時にファイルへ移行する。

### Git 同期の責務

dashboard は `.agents/workflows/` と `.agents/methods/` にファイルを書かず、
git pull / commit / push もしない。
共有フローはソースコードと同じ成果物なので、取得は clone を更新する人・CI・既存の更新ツール、
公開は変更を作る人が通常のブランチと PR/MR で行う。agent-flow の `state_git` は run/bus の状態を
同期する仕組みであり、成果物リポジトリの設定配布には流用しない。agent-project の状態同期へ
載せる案も、任意の成果物リポジトリと状態リポジトリの所有権を混ぜるため採用しない。

## バックログ連携

実行前レビューと再実行で同じフロー一覧を選べる。選択は `backlog/<task-id>.flow.json` に固定し、agent-project が `--pattern` または `--plan-file` として agent-flow へ渡す。sidecar が無い場合は自動。

テスト: `node test/adhoc-flow.test.js`
