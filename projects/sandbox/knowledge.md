# 決定・知識

## DR-0001  2026-07-23  actor: nitto

- context : agent_project-codd_gate-163827（agent_project を codd_gate 非依存の汎用フックへ整理する）の実行を承認
- action  : plan-approve
- reason  : agent-dashboard から操作
- affects : agent_project-codd_gate-163827 → ready

#### DR-0002  2026-07-26  actor: nitto
- context : agent_project-codd_gate-163827（agent_project を codd_gate 非依存の汎用フックへ整理する）を検収承認
- action  : approve-done
- reason  : 成果を確認して完了を承認
- affects : agent_project-codd_gate-163827 → done
- learn: agent_project を codd_gate 非依存の汎用フックへ整理する :: 成果を確認して完了を承認

出典: decisions/agent_project-codd_gate-163827.md

---

## DR-0001  2026-07-18  actor: nitto

- context : codd-gate-163827（codd-gate 連携の目標境界を設計書に固定する）の実行を承認
- action  : plan-approve
- reason  : agent-dashboard から操作
- affects : codd-gate-163827 → ready

#### DR-0002  2026-07-24  actor: nitto
- context : codd-gate-163827（codd-gate 連携の目標境界を設計書に固定する）に人のフィードバック
- action  : feedback-resume
- reason  : 成果物ブランチをrebaseして
- affects : codd-gate-163827 → ready
- learn: codd-gate 連携の目標境界を設計書に固定する :: 成果物ブランチをrebaseして

#### DR-0003  2026-07-24  actor: nitto
- context : codd-gate-163827（codd-gate 連携の目標境界を設計書に固定する）を検収承認
- action  : approve-done
- reason  : 成果を確認して完了を承認
- affects : codd-gate-163827 → done
- learn: codd-gate 連携の目標境界を設計書に固定する :: 成果を確認して完了を承認

出典: decisions/codd-gate-163827.md

---

## DR-0001  2026-07-23  actor: nitto

- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）の実行を承認
- action  : plan-approve
- reason  : agent-dashboard から操作
- affects : dashboard-163827 → ready

#### DR-0002  2026-07-26  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）に人のフィードバック
- action  : feedback-resume
- reason  : コンフリクトを解消して
- affects : dashboard-163827 → ready
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: コンフリクトを解消して

#### DR-0003  2026-07-26  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r2 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r2)

#### DR-0004  2026-07-26  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r2 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r2)

#### DR-0005  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）に人のフィードバック
- action  : feedback-resume
- reason  : 現状のmainブランチが大幅に変わっているためrebaseして再度作業する。
- affects : dashboard-163827 → ready
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: 現状のmainブランチが大幅に変わっているためrebaseして再度作業する。

#### DR-0006  2026-08-01  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r5 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r5)

#### DR-0007  2026-08-01  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r5 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r5)

#### DR-0008  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）に人のフィードバック
- action  : feedback-resume
- reason  : マージ先の main とコンフリクトしているため最新をpullして解消して
- affects : dashboard-163827 → ready
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: マージ先の main とコンフリクトしているため最新をpullして解消して

#### DR-0009  2026-08-01  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r8 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r8)

#### DR-0010  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）に人のフィードバック
- action  : feedback-resume
- reason  : コンフリクトを解消する
- affects : dashboard-163827 → ready
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: コンフリクトを解消する

#### DR-0011  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）を人が修正（revise）
- action  : revise
- reason  : base-syncの末尾空白誤判定を修正済みのため新規試行
- affects : feedback 注入; dashboard-163827 → ready
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: 最新 main を統合し、6ファイルの競合を解消して全検証をやり直す。main由来のMarkdown末尾空白は競合として扱わない。

#### DR-0012  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）を人が修正（revise）
- action  : revise
- reason  : 旧run継承とok:false誤完了の修正を適用した新世代へ切替
- affects : feedback 注入
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: 競合解決済み commit 59ccf49e を起点に、旧 run の done ノードを継承せず新規計画で全検証する。work の terminal ok:false は失敗として扱う。

#### DR-0013  2026-08-01  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r11-v2 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r11-v2)

#### DR-0014  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）を人が修正（revise）
- action  : revise
- reason  : 要対応画面で検証コマンドを変更
- affects : verify: echo "done"; dashboard-163827 → ready

#### DR-0015  2026-08-01  actor: nitto
- context : dashboard-163827 を新 run でやり直し（req-48d24769-dashboard-163827-r11-v2 は done）
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (retries=15)

#### DR-0016  2026-08-01  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r15-v2 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r15-v2)

#### DR-0017  2026-08-01  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）に人のフィードバック
- action  : feedback-resume
- reason  : チェックで承認
- affects : dashboard-163827 → ready

#### DR-0018  2026-08-02  actor: nitto
- context : dashboard-163827 を run req-48d24769-dashboard-163827-r15-v2 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-48d24769-dashboard-163827-r15-v2)

#### DR-0019  2026-08-02  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）を検収承認
- action  : approve-done
- reason  : 成果を確認して完了を承認
- affects : dashboard-163827 → done
- learn: dashboard で一貫性ゲートの状態把握と有効化を支援する :: 成果を確認して完了を承認

出典: decisions/dashboard-163827.md

---

## DR-0001  2026-08-02  actor: nitto

- context : document-msbqiswv-1（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）の実行を承認
- action  : plan-approve
- reason  : 計画レビューで内容を確認し、一括承認
- affects : document-msbqiswv-1 → ready

#### DR-0002  2026-08-08  actor: nitto
- context : document-msbqiswv-1 を run req-48d24769-document-msbqiswv-1-r0 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : document-msbqiswv-1 → ready (last_run=req-48d24769-document-msbqiswv-1-r0)

#### DR-0003  2026-08-08  actor: nitto
- context : document-msbqiswv-1 を新 run でやり直し（req-48d24769-document-msbqiswv-1-r0 は done）
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : document-msbqiswv-1 → ready (retries=1)

#### DR-0004  2026-08-09  actor: nitto
- context : document-msbqiswv-1（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）を人が修正（revise）
- action  : revise
- reason  : agent-dashboard から手動キャンセル
- affects : feedback 注入
- learn: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む :: agent-dashboard が run req-48d24769-document-msbqiswv-1-r1 をキャンセル

#### DR-0005  2026-08-09  actor: nitto
- context : document-msbqiswv-1（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）を人が修正（revise）
- action  : revise
- reason  : agent-dashboard から手動キャンセル
- affects : feedback 注入
- learn: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む :: agent-dashboard が run req-48d24769-document-msbqiswv-1-r2-v1 をキャンセル

#### DR-0006  2026-08-09  actor: nitto
- context : document-msbqiswv-1（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）を人が修正（revise）
- action  : revise
- reason  : agent-dashboard から手動キャンセル
- affects : feedback 注入
- learn: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む :: agent-dashboard が run req-48d24769-document-msbqiswv-1-r3-v2 をキャンセル

#### DR-0007  2026-08-09  actor: nitto
- context : document-msbqiswv-1 を保留（denylist 化）
- action  : hold(deny)
- reason  : フロー画面から保留（実行を止めて人が決める）
- affects : document-msbqiswv-1 → blocked, policy.deny += document-msbqiswv-1
- avoid: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む :: フロー画面から保留（実行を止めて人が決める）

#### DR-0008  2026-08-09  actor: nitto
- context : document-msbqiswv-1 を保留（denylist 化）
- action  : hold(deny)
- reason  : フロー画面から保留（実行を止めて人が決める）
- affects : document-msbqiswv-1 → blocked, policy.deny += document-msbqiswv-1
- avoid: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む :: フロー画面から保留（実行を止めて人が決める）

#### DR-0009  2026-08-09  actor: nitto
- context : document-msbqiswv-1（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）を人の判断で強制完了（verify 未実施）
- action  : force-complete
- reason  : 完了のため
- affects : document-msbqiswv-1 → done（検収 FORCED）

出典: decisions/document-msbqiswv-1.md

---

## DR-0001  2026-08-02  actor: nitto

- context : document-msbqiswx-2（node-budget 集約実装を agentcore に一本化する）の実行を承認
- action  : plan-approve
- reason  : 計画レビューで内容を確認し、一括承認
- affects : document-msbqiswx-2 → ready

出典: decisions/document-msbqiswx-2.md

---

## DR-0001  2026-08-02  actor: nitto

- context : document-msbqiswy-3（割当・claim 判定に budget_summary.can_accept と鮮度判定を追加し reason_codes を出力する）の実行を承認
- action  : plan-approve
- reason  : 計画レビューで内容を確認し、一括承認
- affects : document-msbqiswy-3 → ready

#### DR-0002  2026-08-08  actor: nitto
- context : document-msbqiswy-3 を run req-48d24769-document-msbqiswy-3-r0 の続きから再開
- action  : resume-run
- reason  : 実行画面から再実行（req-48d24769-document-msbqiswy-3-r0 の続きから・失敗ノードのみやり直し）
- affects : document-msbqiswy-3 → ready (last_run=req-48d24769-document-msbqiswy-3-r0)

出典: decisions/document-msbqiswy-3.md

---

## DR-0001  2026-08-02  actor: nitto

- context : document-msbqiswz-4（observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する）の実行を承認
- action  : plan-approve
- reason  : 計画レビューで内容を確認し、一括承認
- affects : document-msbqiswz-4 → ready

#### DR-0002  2026-08-08  actor: nitto
- context : document-msbqiswz-4（observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する）に人のフィードバック
- action  : feedback-resume
- reason  : 成果物ブランチがリモートにpushされていない
- affects : document-msbqiswz-4 → ready
- learn: observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する :: 成果物ブランチがリモートにpushされていない

#### DR-0003  2026-08-09  actor: nitto
- context : document-msbqiswz-4 を run req-48d24769-document-msbqiswz-4-r2 の続きから再開
- action  : resume-run
- reason  : 実行画面から再実行（req-48d24769-document-msbqiswz-4-r2 の続きから・失敗ノードのみやり直し）
- affects : document-msbqiswz-4 → ready (last_run=req-48d24769-document-msbqiswz-4-r2)

#### DR-0004  2026-08-09  actor: nitto
- context : document-msbqiswz-4（observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する）を人が修正（revise）
- action  : revise
- reason  : 停滞した試行（req-48d24769-document-msbqiswz-4-r3）を打ち切る。作り直しのため却下へ回す
- affects : feedback 注入; document-msbqiswz-4 → ready
- learn: observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する :: 停滞した試行（req-48d24769-document-msbqiswz-4-r3）を打ち切る。作り直しのため却下へ回す

#### DR-0005  2026-08-09  actor: nitto
- context : document-msbqiswz-4（observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する）を却下（廃止）
- action  : reject
- reason  : 検証不合格の差分（捏造パス run/brief/archives 等・0バイトのテスト）が作業ブランチに残っており、リトライは同一ブランチへ積み増す設計のため、退避タグを残して作り直す
- affects : document-msbqiswz-4 → rejected ／ 依存先を再審査へ: （なし）
- avoid: observation envelope（観測 sidecar）を導入し観測の idempotent 取込を実装する :: 検証不合格の差分（捏造パス run/brief/archives 等・0バイトのテスト）が作業ブランチに残っており、リトライは同一ブランチへ積み増す設計のため、退避タグを残して作り直す

出典: decisions/document-msbqiswz-4.md

---

## DR-0001  2026-08-02  actor: nitto

- context : document-msbqisx2-5（共有禁止項目（redaction）を契約テスト化する）の実行を承認
- action  : plan-approve
- reason  : 計画レビューで内容を確認し、一括承認
- affects : document-msbqisx2-5 → ready

#### DR-0002  2026-08-04  actor: nitto
- context : document-msbqisx2-5（共有禁止項目（redaction）を契約テスト化する）を検収承認
- action  : approve-done
- reason  : 成果を確認して完了を承認
- affects : document-msbqisx2-5 → done
- learn: 共有禁止項目（redaction）を契約テスト化する :: 成果を確認して完了を承認

出典: decisions/document-msbqisx2-5.md

---

## DR-0001  2026-08-02  actor: nitto

- context : document-msbqisx3-6（dashboard に fleet/knowledge ビュー列を追加し状態リポジトリと突合可能にする）の実行を承認
- action  : plan-approve
- reason  : 計画レビューで内容を確認し、一括承認
- affects : document-msbqisx3-6 → ready

#### DR-0002  2026-08-08  actor: nitto
- context : document-msbqisx3-6 を run req-48d24769-document-msbqisx3-6-r0 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : document-msbqisx3-6 → ready (last_run=req-48d24769-document-msbqisx3-6-r0)

#### DR-0003  2026-08-08  actor: nitto
- context : document-msbqisx3-6（dashboard に fleet/knowledge ビュー列を追加し状態リポジトリと突合可能にする）に人のフィードバック
- action  : feedback-resume
- reason  : 成果物ブランチがリモートにpushされていない
- affects : document-msbqisx3-6 → ready
- learn: dashboard に fleet/knowledge ビュー列を追加し状態リポジトリと突合可能にする :: 成果物ブランチがリモートにpushされていない

#### DR-0004  2026-08-29  actor: nitto
- context : document-msbqisx3-6（dashboard に fleet/knowledge ビュー列を追加し状態リポジトリと突合可能にする）を人が修正（revise）
- action  : revise
- reason  : 要対応画面からフローを選択して再実行
- affects : flow: 更新; document-msbqisx3-6 → ready

出典: decisions/document-msbqisx3-6.md

---

## DR-0001  2026-07-23  actor: nitto

- context : enq-20260718-163827（プロジェクト受入の統合検証を通す）の実行を承認
- action  : plan-approve
- reason  : agent-dashboard から操作
- affects : enq-20260718-163827 → ready

#### DR-0002  2026-08-02  actor: nitto
- context : enq-20260718-163827（プロジェクト受入の統合検証を通す）を検収承認
- action  : approve-done
- reason  : 成果を確認して完了を承認
- affects : enq-20260718-163827 → done
- learn: プロジェクト受入の統合検証を通す :: 成果を確認して完了を承認

出典: decisions/enq-20260718-163827.md

---

## DR-0001  2026-08-09  actor: nitto

- context : node-budget-summary-stat-212846（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）の実行を承認
- action  : plan-approve
- reason  : 受入基準・スコープ・制約を確認して承認（旧タスクの検証不合格を踏まえ、配置とスコープを明示した作り直し）
- affects : node-budget-summary-stat-212846 → ready

#### DR-0002  2026-08-09  actor: nitto
- context : node-budget-summary-stat-212846（node-budget-summary スキーマを追加し status/<node>.json へ埋め込む）を人の判断から復帰
- action  : approve-and-fix
- reason  : ollama サーバを復旧したため再開
- affects : node-budget-summary-stat-212846 → ready
- learn: node-budget-summary スキーマを追加し status/<node>.json へ埋め込む :: ollama サーバを復旧したため再開

出典: decisions/node-budget-summary-stat-212846.md

---

## DR-0001  2026-08-02  actor: auto

- context : cycle 1: acceptance 0/2 PASS
- action  : project-evaluate
- reason  : 分解待ち（自動起票なし）
- affects : 改善 0 件 / findings 0

#### DR-0002  2026-08-02  actor: auto
- context : cycle 1: acceptance 0/2 PASS
- action  : project-evaluate
- reason  : 分解待ち（自動起票なし）
- affects : 改善 0 件 / findings 0

#### DR-0003  2026-08-02  actor: auto
- context : cycle 1: acceptance 0/2 PASS
- action  : project-evaluate
- reason  : 分解待ち（自動起票なし）
- affects : 改善 0 件 / findings 0

出典: decisions/sandbox-project-v1.md

---

## DR-0001  2026-07-23  actor: nitto

- context : sibling-163827（sibling 自動検出レイヤと利用手順を新境界へ追随させる）の実行を承認
- action  : plan-approve
- reason  : 成果を確認して完了を承認
- affects : sibling-163827 → ready

#### DR-0002  2026-07-23  actor: nitto
- context : sibling-163827（sibling 自動検出レイヤと利用手順を新境界へ追随させる）を人の判断から復帰
- action  : approve-and-fix
- reason  : agent-dashboard から操作
- affects : sibling-163827 → ready
- learn: sibling 自動検出レイヤと利用手順を新境界へ追随させる :: agent-dashboard から操作

#### DR-0003  2026-07-26  actor: nitto
- context : sibling-163827（sibling 自動検出レイヤと利用手順を新境界へ追随させる）に人のフィードバック
- action  : feedback-resume
- reason  : codd関連のコードの置き場所を考えてほしい。厳密なプラグイン機構は要らないが、フォルダ構成は意識する。
- affects : sibling-163827 → ready
- learn: sibling 自動検出レイヤと利用手順を新境界へ追随させる :: codd関連のコードの置き場所を考えてほしい。厳密なプラグイン機構は要らないが、フォルダ構成は意識する。

#### DR-0004  2026-07-26  actor: nitto
- context : sibling-163827（sibling 自動検出レイヤと利用手順を新境界へ追随させる）を検収承認
- action  : approve-done
- reason  : 成果を確認して完了を承認
- affects : sibling-163827 → done
- learn: sibling 自動検出レイヤと利用手順を新境界へ追随させる :: 成果を確認して完了を承認

出典: decisions/sibling-163827.md

---

## DR-0001  2026-08-09  actor: nitto

- context : sidecar-schema-observati-212920（観測 sidecar の schema 化と observation ID による冪等取込を実装する）の実行を承認
- action  : plan-approve
- reason  : 受入基準・スコープ・制約を確認して承認（旧タスクの検証不合格を踏まえ、配置とスコープを明示した作り直し）
- affects : sidecar-schema-observati-212920 → ready

#### DR-0002  2026-08-10  actor: nitto
- context : sidecar-schema-observati-212920 を run req-48d24769-sidecar-schema-observati-212920-r0 の続きから再開
- action  : resume-run
- reason  : 実行画面から再実行（req-48d24769-sidecar-schema-observati-212920-r0 の続きから・失敗ノードのみやり直し）
- affects : sidecar-schema-observati-212920 → ready (last_run=req-48d24769-sidecar-schema-observati-212920-r0)

出典: decisions/sidecar-schema-observati-212920.md

---

## note-20260801024624.md

docs/plans/2026-07-29-agent-tools-distributed-credit-knowledge-plan.md を実行したい

出典: notes/note-20260801024624.md
