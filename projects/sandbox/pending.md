# 未完了タスク

## document-msbqisx3-6: dashboard に fleet/knowledge ビュー列を追加し状態リポジトリと突合可能にする

- 受入基準: fleet 画面に新列が追加され、status/<node>.json の値と一致する
- 受入基準: knowledge 画面で観測 provenance と適用統計が確認できる
- 受入基準: dashboard-163827 の範囲外の二次書き込みは行わない（dashboard は書き手にならない）
- 背景: Phase5 のユーザビリティ要件。UI で一度に判断できることが運用の前提になる。
- 内容: fleet 画面に capacity bucket・鮮度(updated_iso+fresh_after_sec)・reservation・reason_code を追加、knowledge 画面に provenance・適用数・PASS/FAIL/rollback 集計・状態遷移を表示する。データ源は status/<node>.json（board はオプション）に限定する。
- charter: v1
- assess: c=3 r=2 a=2
- feedback: 成果物ブランチがリモートにpushされていない
- verification: {"pass": 1, "fail": 0, "unverifiable": 4, "report": "verifications/document-msbqisx3-6/9020b856f636227ab0b1ced21ef91c45283fee55.md", "receipt": true, "plan_digest": "sha256:3bd84b6782aa27087b740e30fa0a46a2d4938b37fa58212773d0dff3b413d9fc"}
- env_block_kind: unverifiable
- env_block_count: 1
- needs_reason: [agent-error:env] 検証不能: このノードでは確かめられない基準があります（fleet 画面に新列が追加され、status/<node>.json の値と一致する — 自然文基準の判定は agent-flow runner の receipt が必要（local runner は固定コマンドのみ実行） ／ knowledge 画面で観測 provenance と適用統計が確認できる — 自然文基準の判定は agent-flow runner の receipt が必要（local runner は固定コマンドのみ実行） ／ dashboard-163827 の範囲外の二次書き込
- needs_dr: DR-0003
- edited: human
- rev: 1
- last_run: req-48d24769-document-msbqisx3-6-r0-v1
- flow_run: req-48d24769-document-msbqisx3-6-r0-v1

出典: backlog/document-msbqisx3-6.md

---

## node-budget-summary-stat-212846: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む

- 受入基準: schemas/node-budget-summary.schema.json がリポジトリに追加されている
- 受入基準: status/<node>.json の budget キーが schema に準拠して出力される fixture がある
- 受入基準: 旧ビューが optional な新フィールドで壊れない契約テストが通る
- repos: agent-project
- 背景: Phase1 の前提である射影 schema を先に固定し、互換性テストで安全に出すため。
- 内容: schemas/node-budget-summary.schema.json を追加し、status/<node>.json の budget block に additive に埋める仕様を定義。reader が optional で壊れない互換性を担保する契約テストを追加する。
- 対象範囲: tools/agent-project 配下と、リポジトリルートの schemas/ のみ
- 対象外: 他ツール（agent-dashboard / agent-loop 等）のファイル。書式だけの整形（末尾改行の削除など）
- 制約: 同じ役割のファイルを複数の場所に作らない（配置は 1 か所に決めてから書く）。中身の無い 0 バイトのファイルを成果に数えない。テストは実行して結果を報告に貼る
- charter: v1
- assess: c=2 r=2 a=2
- needs_reason: [agent-error:env] 環境の問題（実行環境の問題）: 実行環境の問題です（モデル名・CLI の導入・PATH・argv_limit を確認してください） タスクの内容の問題ではないため、リトライ回数は消費していません。環境を直してから approve すると、同じ run の続き（失敗した工程だけ）から再開します。
- needs_dr: DR-0001
- last_run: req-48d24769-node-budget-summary-stat-212846-r0
- flow_run: req-48d24769-node-budget-summary-stat-212846-r0

出典: backlog/node-budget-summary-stat-212846.md

---

## sidecar-schema-observati-212920: 観測 sidecar の schema 化と observation ID による冪等取込を実装する

- 受入基準: observation sidecar フォーマットが schema として追加されている
- 受入基準: 同一 observation ID を複数回取り込んでも候補集合と hit 集計が変わらない E2E fixture がある
- 受入基準: provenance（発生 node→run→receipt）を辿れるサンプルが確認できる
- repos: agent-project
- 背景: Phase3 の基盤。観測の追跡性と重複耐性が無いと rule ライフサイクルが破綻する。
- 内容: 観測（observation）の共通 sidecar（identity, input, outcome, candidate, privacy）を schema として定義し、観測 ID をキーに冪等に取り込めるようにする。git のマージ順に依存せず同じ集計結果になることを保証する。
- 対象範囲: tools/agent-project 配下と、リポジトリルートの schemas/ のみ
- 対象外: 他ツールのファイル。書式だけの整形
- 制約: 『run の brief / archive / decisions』は agent-project の状態ディレクトリ（プロジェクトフォルダ側）の論理名であり、リポジトリ内に同名フォルダを作ってはいけない。schema の置き場は既存の schemas/ に合わせる。実装とテストは実行して結果を報告に貼る
- 補足: リポジトリ側の既存配置: schema は schemas/*.schema.json、実装は tools/agent-project/agent_project/ 配下、テストは tools/agent-project/tests/ 配下。状態ディレクトリ（brief/ archive/ decisions/ verifications/）はリポジトリの外（プロジェクトフォルダ）にある。
- charter: v1
- assess: c=3 r=2 a=2
- last_run: req-48d24769-sidecar-schema-observati-212920-r0
- cancel_count: 1

出典: backlog/sidecar-schema-observati-212920.md
