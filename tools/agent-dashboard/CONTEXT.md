# agent-dashboard

agent-project / agent-flow / agent-amigos を人が操作・監視するための Electron ダッシュボード。

## 用語

**設計書（Design Doc）**:
agent-flow の依頼として渡す Markdown。必須4節（目的・変更対象・受入基準・検証方法）を備えたものを完全とみなす。
_使わない_: 仕様書, タスク記述, プロンプト

**設計フロー（Design Flow）**:
`purpose: design` の設計書を成果として返す agent-flow のフロー。`purpose: implementation` の実装フローとは
一覧・選択・実行契約を分ける。human / split ノードは使わず、設計 run は workspace を持たない読み取り専用
run とする。対話型は成果に次の質問リストを含め、全自動型は 1 run で設計書を完成させる。
_使わない_: 設計ウィザード, 設計モード

**フローの用途と公開範囲**:
フロー定義の `purpose` は `implementation` または `design`、`libraryVisibility` は `library` または
`internal`。後者は通常の保存済み実装フロー一覧へ出さないための分類であり、同梱の設計フローは
`design/internal` とする。項目が無い旧定義は `implementation/library` として読む。

**設計フローカタログ（Design Flow Catalog）**:
対象 cwd に対して、登録済みリポジトリ共有 (`scope: repository`)、ユーザー共通 (`user`)、同梱 (`builtin`) の
順で設計用途だけを列挙するカタログ。`id` だけでは一意にせず、参照は `id + scope + repository` で固定する。
同じ id の別 scope は別項目として扱う。

**設計フロースナップショット（Design Flow Snapshot）**:
設計フロー選択時に main が解決した正規化済み定義、`origin.scope/repository`、`digest` を作業準備項目へ
保存したもの。後からカタログや元定義が変わっても、保存済みの設計 run / 実装 handoff の定義は変わらない。

**設計セッション（Design Session）**:
短文の要望から設計書へ詰める、dashboard 上のラウンドの連なり。状態（cwd・元要望・現行設計書・ラウンド履歴）は dashboard ローカルに持つ。
_使わない_: 設計チャット, 対話モード

**ラウンド（Round）**:
設計セッション内の 1 反復。1 ラウンド = 1 短命の設計 run。入力は元要望・現行設計書・前ラウンドの回答、成果は更新した設計書と次の質問リスト。

**設計 run**:
設計フローの実行。park せず数分で終端する。run の `workspace` は持たず、対象 cwd はカタログや材料の
参照にだけ使う。リポジトリのファイル変更、commit、push、ブランチ作成はしない。成果は result（sink ノード
出力）から取得し、`af/` ブランチを作らない。

**設計成果（Design Result）**:
実装へ渡せる Markdown は `## 目的`、`## 変更対象`、`## 受入基準`、`## 検証方法` の必須4節を持つ。
未決事項は任意の `## 質問` 節へ番号付きで残し、推奨する答えと理由を添える。検証方法に書いたコマンドは
設計 run では実行せず、実装 run の検証契約へ引き継ぐ。

**作業準備項目（Preparation Item）**:
一つの仕事を設計 run と実装 run の間で引き継ぐ dashboard ローカルの状態。経路は
`agent-design` / `external-design` / `direct` の3つで、設計結果は `design-result` 材料として実装へ渡す。
既存の `designMode` だけを持つ項目は一括移行せず、設計開始時に選択された同梱フローの snapshot を遅延補完する。

**保存・削除制約**:
Dashboard から新規作成・編集・削除できるカスタムフローは `~/.agents/workflows/` の自分用だけ。登録済み
リポジトリ共有版と同梱版は読み取り専用で、変更は別 id の自分用コピーとして行う。自分用の削除も物理削除ではなく
`.trash/` へ移動する。成果物 repository の commit / push / pull は dashboard の責務ではない。

**契約検証**:
設計/実装フローと作業準備の契約は `cd tools/agent-dashboard && node --test test/adhoc-flow.test.js test/preparation.test.js`
で確認し、全体は同じディレクトリの `npm test` で確認する。

**実装 run**:
設計書を依頼として実行する通常のワークフロー run。

**実行前チェック（Readiness Check）**:
依頼テキストへの決定的な必須節チェック。不足は警告表示のみで、実行はブロックしない。
_使わない_: バリデーション, lint
