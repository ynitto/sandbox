# agent-dashboard × agent-project 連携の改善案

> 日付: 2026-07-15
> 対象: `tools/agent-dashboard/src/features/agent-project/`・`src/renderer/`
> 関連: [`agent-dashboard-feature-split-design.md`](./agent-dashboard-feature-split-design.md)・
> [`agent-project-design.md`](./agent-project-design.md)・
> [`tools/agent-dashboard/README.md`](../../tools/agent-dashboard/README.md)

## 目的

agent-dashboard の agent-project 連携を「もっと使いやすく」する改善案を、次の 2 軸で整理する。

1. **自律性（人の省力化）** — 人が張り付いて監視・操作する量を減らす。気づく・判断する・
   反映する、の各ステップにかかる手間を削る。理想は「人が触る回数そのもの」を減らすこと。
2. **品質向上（人介在による手戻り削減）** — 人を **正しいタイミング** に **正しい文脈** で
   関与させ、1 回の介入で下流の retry を複数回ぶん未然に防ぐ。

この 2 軸はトレードオフではない。**判断をAIが下ごしらえして人に渡す**ことは、省力化と
手戻り削減を同時に効かせる中核レバーである。

## 護るべき不変条件（すべての案の前提）

現行設計が繰り返し守っている護りを、どの案も破らない。破らずに自律性を上げるのが本提案の肝。

- **done は verify のみが根拠** — 状態遷移を UI から直接書き換えない。
- **公式な入力契約だけを使う** — `needs/` 記入・`inbox/` 投入・`commands/` ドロップの 3 契約のみ。
- **AI はファイルを書かない** — エージェント応答はテキスト。ファイル確定は人の「作成/保存/承認」
  ボタン（＝人の確定操作）だけが行う（`authoring.js` / `actions.js` のホワイトリスト）。
- **viewer の GitLab は読み取り専用** — 書き込みは gitlab-review-viewer の役割。
- **タスク状態ファイル（`backlog/*.md` の status 等）は書き換えない**。

→ したがって「自動承認」も、人が事前に決めたポリシーに従って **公式の `commands/` approve を
自動でドロップする**形に限る。verify ゲートも done の不変条件も迂回しない。

## 現状の把握（すでにある機能）

車輪の再発明をしないため、連携面の現状を押さえる（`README.md` 準拠）。

- **人のアクションは公式契約で網羅済み**: plan-review / delivery-review の承認・差し戻し・却下、
  feedback 再開、revise（doing 中も）、replan、inbox 追加、pause/stop、reset、run cancel/削除。
- **関係性の可視化**: charter → backlog → run → issue のパンくずと相互リンク、リトライ系統の束ね。
- **Viewer アシスタント（AI）** は 2 用途のみ: charter の下書き/補完（フォーム流し込み）と、
  **読み取り専用 Doctor**（画面スナップショットを 1 回渡して助言）。`agent.js` が担う。
- **同期・稼働判定**: state_git 経由の pull/push、status.json / instances / lock による daemon 判定。

## 課題の所在（コードから確認したギャップ）

| # | ギャップ | 根拠 |
|---|---------|------|
| G1 | **通知が無い** — OS 通知・トレイ・バッジ・ウィンドウフラッシュのいずれも未実装。人が画面を見ていないと needs 出現に気づけない（既定 5 秒ポーリングの純プル型） | `grep -rniE "Notification\|Tray\|setBadge\|flashFrame"` が空。トーストは画面内のみ（72 箇所） |
| G2 | **AI が「助言」止まりで「下書き」しない** — Doctor は画面全体を 1 回説明するだけ。個別の needs カードに対する approve/reject/feedback の **文面下書き** も、検収物の **事前レビュー** も無い | `agent.js` は `completeCharter` と `completeDoctor` の 2 経路のみ |
| G3 | **人の介入回数を減らす仕組みが無い** — すべての plan-review / delivery-review が等しく人待ちになる。安全に自動化できる決裁も毎回人が触る | `actions.js` に承認は都度手動ドロップのみ。ポリシー駆動の自動決裁は無い |
| G4 | **要対応の優先度・経過時間・SLA が無い** — needs カードは順序付け・滞留時間表示・横断キューが無い | renderer に needs の sort/aging/priority 表示なし。バッチ操作（select-all/bulk）も無し |
| G5 | **計器（メトリクス）が無い** — 手戻り率・retry 分布・blocked 滞留・lead time を見る面が無い。どこで手戻りが多いかが分からない | `run-log.jsonl`・DR・retries は持つが集計ビューは未実装 |
| G6 | **acceptance の品質を早期に検査しない** — 自然文 accept（曖昧 verify）が手戻りの根本原因になりうるが、作成/編集時に警告しない | `CHARTER_RULES` は AI 補完のプロンプト内規約のみ。UI リンタは無い |

## 改善案

各案に **課題 / 提案 / 実装の当たり / 効く軸 / 規模感** を付す。規模感は S（数百行・既存層内）/
M（新規モジュール 1〜2）/ L（横断・複数ツール協調）。

### A. 気づく前に届く — 通知と省力トリアージ（自律性）

**A1. OS 通知・タスクバーバッジ・ウィンドウフラッシュ**（G1｜軸1｜S）✅ 実装済み
新規 needs 出現を検知したら Electron の `Notification` / `app.setBadgeCount` / `win.flashFrame`
で OS 側に出す。クリックで該当プロジェクトへディープリンク（`agent-dashboard://` は既にある）。
状態差分はポーリング結果（`discover()` の `needsCount`）の前後比較で取れるので、`base/main` に
薄い通知層を足すだけ。**張り付き監視の解消がそのまま省力化になる**、最小コスト・最大効果の一手。

> **実装（このリポジトリ）**: `src/base/main/notify.js`（汎用 OS 通知プリミティブ）＋
> `app:notify` IPC ＋ renderer の増分検知（純関数 `computeNeedsDelta` ＋ `checkNeedsNotifications`）。
> ⚙ 設定トグル `notifications.enabled`（既定 on）。観測済みプロジェクトで `needsCount` が増えた
> ときだけ通知し、起動直後の既存分・減少・新規発見では通知しない。フォーカス中はポップアップと
> フラッシュを抑制しバッジだけ更新。クリックは既存の `app:openTarget` ディープリンク経路を再利用。
> テスト: `test/needs-notify.test.js`。トレイ常駐は今回は入れていない（通知＋バッジで十分なため）。

**A2. 外部通知ルーティング（任意・Slack/汎用 webhook）**（G1｜軸1｜M）
在席していない/複数人運用のとき、A1 の同じイベントを webhook にも流す（⚙ 設定でオプトイン、
URL・しきい値・対象イベントを指定）。送るのは要約（本文・URL）だけで、書き込み権限は持たない。

**A3. 横断「要対応キュー」ビュー**（G4｜軸1｜M）
プロジェクト横断で needs を 1 つのキューに集約し、**緊急度 × 滞留時間**でソート。キーボードで
上から順に消化（下記 E1）。「自分がアクセスできる clone を足すだけで全プロジェクトを 1 画面で
束ねる」既存思想の自然な延長。担当者が朝一に人待ちを一掃する動線になる。

**A4. 経過時間・SLA バッジ**（G4｜軸1+2｜S）✅ 実装済み
各 needs カードに「待ち時間（needs の最終更新 mtime からの経過）」バッジを付け、未対応は
滞留の長い順に並べる。長時間放置＝下流が止まっている、を一目で。SLA しきい値超過は色で警告
（手戻りではなく **停滞** の可視化）。

> **実装（このリポジトリ）**: 純関数 `humanizeAge` / `needAgeInfo`（renderer）で mtime→待ち時間
> ラベル・SLA レベルを導出。`needsViewModel` の未対応バケットを mtime 昇順（＝停滞の長い順）で
> 並べ、既定選択も最も停滞したカードにして最優先へ誘導。しきい値 `projects.needsSlaHours`
> （既定 24h・⚙ 設定）超で赤、1/3 超で黄。テスト: `test/needs-sla.test.js`。

### B. 人の判断をAIが下ごしらえ — pre-digested decisions（自律性 + 品質）

現行 Doctor（画面全体を 1 回説明）を、**個別決裁の下書き役**へ拡張する。いずれも
「AI は文面を下書きするだけ、確定は人のボタン」の護りを保つ。

**B1. needs カードごとの AI 推薦（approve / 差し戻し / feedback の下書き）**（G2｜軸1+2｜M）✅ 一部実装
plan-review / delivery-review 向けに Doctor モードを拡張し、**推薦と差し戻し文面案**を返す。
人は「差し戻し文面を回答欄へ」で下書きを入れ、確定ボタンは人が押す。失敗診断（failure-diagnosis）
も同系統。汎用 blocked カードの approve/hold 推薦までは今回対象外。

**B2. 検収物の AI プレフライトレビュー**（G2｜軸2+1｜M）✅ 実装済み
検収ダイアログ／カードの「変更理由を説明」（`delivery-rationale`）で、**差分 × acceptance ×
charter** を突き合わせ、変更意図・acceptance 対応・リスク・承認推薦を返す。加えて
「フォローアップ案」（`followup-suggest`）で次タスク案を JSON 提案し、タスク追加フォームへ
流し込める（inbox 投入は人の確定操作）。

**B3. plan-review の AI 批評**（G2｜軸2｜M）✅ 実装済み
plan-review カードの「AIで計画を批評」（`plan-critique`）。提案タスクを charter の
goal/acceptance と兄弟 proposed と突き合わせ、取りこぼし・重複・依存欠落・acceptance 未対応を
指摘。差し戻し文面案も返す。

### C. ポリシー駆動の自律 — 触る回数そのものを減らす（自律性）

省力化の本丸は「各操作を速く」より「**人が触る回数を減らす**」こと。ただし護りは保つ。

**C1. 条件付き自動承認（人が事前設定した安全条件のみ）**（G3｜軸1｜M）
⚙ 設定で「自動承認ポリシー」を人が定義（既定オフ・オプトイン）。例:
`verify PASS ∧ 差分行数 < N ∧ 変更ファイルが test/docs のみ ∧ B2 の AI リスク=低`。
条件成立の delivery-review は **公式の `commands/` approve を自動ドロップ**し、成立しないものだけ
人へエスカレーション。**verify ゲートも done の不変条件も迂回しない**（承認契約を自動で押すだけ）。
監査のため自動承認は DR と通知（A1）に必ず残す。「全部人が見る」から「リスクのあるものだけ人が
見る」への転換で、省力化が桁で効く。

**C2. 決定メモリ / 学習**（G3｜軸2+1｜M）
過去の approve/reject と理由（既に `decisions/` の DR に残る）を索引し、新しい needs に対して
「**以前これに類似した検収を X の理由で差し戻した**」を提示。さらに繰り返し差し戻す理由を
`policy.md` の avoid ルールへ **昇格提案**（人が承認して反映）。同じ手戻りを二度させない。

### D. 手戻りを断つ品質ゲート（品質）

**D1. acceptance 品質リンティング**（G6｜軸2｜S）
charter 作成/編集ダイアログで、`## acceptance` の各行を検査し **自然文 accept（シェルコマンド化
できていない行）を警告**。曖昧な受け入れ条件 → 曖昧な verify → 「PASS したはずが人の期待と違う」
手戻りの根本原因を、最上流（入力時）で潰す。`CHARTER_RULES` を UI リンタとして実体化するだけ。

**D2. 再発失敗のクラスタリング**（G5｜軸2｜M）
needs / 失敗ノードを **失敗シグネチャ**（同一 verify コマンド・同種エラー・同一ファイル）で
クラスタ化。「同じ verify が 4 タスクで落ちている」等の systemic な問題を可視化し、**per-task の
retry でなく charter/policy レベルで一度に直す**動線へ（replan / policy 追記へのショートカット）。

**D3. メトリクス / 分析ビュー**（G5｜軸2｜M）
`run-log.jsonl`・retries・DR・needs 生成/解消時刻から集計: **手戻り率**（差し戻し÷検収）・
**retry 分布**・**blocked 滞留時間**・**lead time**（inbox→done）・自動承認率。プロジェクト別・
charter 別に。品質改善は計器がないと回らない。「どこで手戻りが多いか」を見て C/D の効き先を決める。

### E. UI の細かな使いやすさ（両軸）

- **E1. キーボードショートカット**（軸1｜S）— 要対応キューで `a`=承認 / `r`=差し戻し /
  `h`=保留 / `j/k`=次/前。人待ち一掃を高速化。
- **E2. 差分の可読性向上**（軸2｜S）— 検収レビューで **acceptance 行 ↔ 変更箇所の対応**を並べ、
  ファイル別にリスク（サイズ・テスト有無）を色付け。判断の精度を上げる。
- **E3. 「前回見てから何が変わったか」ダイジェスト**（軸1｜S）— 起動時/再表示時に、前回閲覧
  以降の新規 needs・完了・失敗を 1 枚に要約。復帰コストを下げる。
- **E4. バッチ操作**（軸1｜S）— 同種の複数 needs を選択して一括承認/保留（誤操作防止の確認付き）。

## 優先順位とロードマップ

効果／コスト比と依存関係で 3 段階に。

**Phase 1（即効・低コスト、まず監視負荷と停滞を消す）**
A1 通知/バッジ/フラッシュ・A4 経過時間 SLA・D1 acceptance リンタ・E1 キーボード・E3 ダイジェスト。
→ いずれも既存層（`base/main` と renderer）に閉じ、外部依存なし。**張り付き監視の解消**が最速で効く。

**Phase 2（下ごしらえ・計器、判断の質と速度を上げる）**
B1 needs 推薦・B2 プレフライトレビュー・B3 plan 批評・D3 メトリクス・A3 横断キュー。
→ `agent.js` に読み取り専用の推薦経路を足すのが中心。Doctor の実績パターンを踏襲できる。

**Phase 3（自律・学習、触る回数そのものを減らす）**
C1 条件付き自動承認・C2 決定メモリ・D2 再発クラスタ。
→ B2/D3 の土台（AI リスク評価・メトリクス）が前提。護りの検証を厚めに。

## 非目標

- done / タスク状態を UI から直接書く機能は作らない（verify のみが根拠）。
- viewer からの GitLab 書き込みは持たない（gitlab-review-viewer の役割）。
- 完全自動運転（人ゼロ）は目標にしない。C1 も「人が事前に許した範囲だけ」を自動化する。

## 次アクション（提案）

Phase 1 の **A1 / A4** と Phase 2 の **B2 / B3**（および B1 の plan/delivery 部分）を実装済み。
次は Phase 1 の **D1（acceptance リンティング）** / **E1（キーボードショートカット）**、
または B1 の blocked 一般化・C1（条件付き自動承認）が候補。
