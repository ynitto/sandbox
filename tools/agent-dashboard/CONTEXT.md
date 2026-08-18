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
実装へ渡せる Markdown は `## 目的`、`## 変更対象`、`## 受入基準`、`## 検証方法` の必須4節と、
`## 変更対象` の中の強制レイヤーを持つ。
未決事項は任意の `## 質問` 節へ番号付きで残し、推奨する答えと理由を添える。検証方法に書いたコマンドは
設計 run では実行せず、実装 run の検証契約へ引き継ぐ。

**作業ルール（Method / Rule）**:
エージェントへの依頼文へ足す短い指示。カタログ（`methods/<id>.json`）が正典で、選ばれ方で 2 つに分かれる。
`selection: auto`（既定）は実行条件——役割・工程種別・実行レベル・料金区分——だけで決まるもので、
設定画面のトグルで自動適用する。`selection: per-task` は実行時にも機械判定できない「その工程への指示」で、
工程ごとに人（または planner）が選ぶ。トグルの対象にはしない。
_使わない_: プロンプト断片, 手法パック

**成果物の契約（Contract）**:
カタログの `kind: contract` を宣言した項目。成果物の形式そのものを定義し、エージェントへの指示
（`fragments`）と、機械で数える構造（`format`: 必須節と節内の必須項目）を同じ 1 ファイルに持つ。
ON/OFF せず、成果物の種類ごとに 1 つ決まる（同梱は設計書の書式 `design-document-format`）。
差し替えは対象フォルダの `.agents/methods/` へ同じ id を置くことで行う。
_使わない_: バリデーションルール, スキーマ

**既定 ON の作業ルール**:
同梱カタログが `enabled: true` を宣言したもの。触らない工程では無害な自己条件づけの文面に限る
（同梱は画面の一貫性とテストの緑の証跡）。端末設定（tuning）の宣言が常に優先し、無効化すれば効かない。

**統合検証（Integration Verify）**:
並列で作った変更をまとめた状態で対象パッケージのテストスイート全体を CI と同じ系統で実行し、
失敗したら修正して再検証する検証工程（`kind: verify` + `continuation: retry`）。工程を置くのは
フローを作る人（または planner）で、画面が勝手に足すことはしない。検証のやり方は作業ルール
`integration-verify` が実行時に verify ロールへ足し、登録リポジトリの `.agents/methods/` が
同 id で上書きできる。
run の完了は「全ノード done」ではなく、この終端検証が緑であることで表す。判定の正典は agent-flow が
`final.json` の `verification` へ記録したもの（`state` / `nodes` / `failed`）で、赤なら run 自体が
failed で終端する。dashboard は記録を読むだけで、成果テキストからの読み直しは記録の無い旧 run に限る。
_使わない_: 最終テスト, 総合テスト

**強制レイヤー（Enforcement Layer）**:
設計書の「変更対象」に書く、契約ごとの「実行時にどの層で強制されるか」。プロンプト上の約束（文言）と、
起動引数・スキーマ検査・ゲートによる強制を書き分けるための必須項目で、これが無い設計成果は実装へ渡さない。
_使わない_: 実装箇所, 責務

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
