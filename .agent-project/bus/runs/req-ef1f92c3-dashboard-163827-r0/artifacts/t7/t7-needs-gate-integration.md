# t7 成果 — needs の回帰失敗要約をゲート状態と統合表示

## 実装位置

| ファイル | 変更 |
|---|---|
| `src/renderer/sections/needs.js` | `needGateFailure(failure, n)` 追加 ＋ `renderNeedFacts(n)` → `renderNeedFacts(p, n)`（ゲート統合ブロックを facts 末尾に追加）。呼び出し 1 行と再描画 sig に `p.consistencyGate` を追加 |
| `test/needs-gate-integration.test.js` | 新規。実画面相当の DOM（依存ゼロのタグスタック walker 同梱）で 8 ケース |
| `package.json` | `test` に 1 本追加 |

## やったこと（統合表示）

ゲート由来の検証失敗（`failureContext.command` が `codd-gate` を含む、または summary/why が
`回帰検知`/`一貫性ゲート`/`codd-gate`）のとき、既存の検証失敗要約の**後ろに** `.need-resolution.need-gate`
ブロックを足す。概要タブの `consistencyGateHtml`（t5/t6）と**同じ語彙・視覚言語**で結線状態を示す:

- ラベルチップ `一貫性ゲート`（概要節と同一名称 ＝ 語彙統合）
- `regression_cmd` / `intake_cmd` を `.mono` で、intake 結線を `.badge info|warn` で（概要節と同一）
- intake 未結線時は「直してもドリフトは自動起票されない」→ 概要で有効化を促す
- 未結線＋`configFile` 有りなら `data-open`（`bindNeedDetail` 配線済み）で設定ファイルを開く導線
- `p.consistencyGate` 未提供でもブロックは出し、概要節への文言誘導だけにフォールバック

**追加は末尾のみ・既存ブロックは無改変**なので可読性は落ちない:
- 見出し: `検証失敗` ラベルチップ据え置き（潰していない）
- 要約行: `summary` の `<strong>` 据え置き
- context: `need-failure-context` の `<dl>` 据え置き
- 折り畳み: `renderNeedDetail` の `<details>判断材料を見る</details>` 据え置き

推測してよい場所の分離（t2 §6.3）は維持: 断定側 `needFailureViewModel` は無改変。ゲート判定は
`canDiagnoseNeed` と同じ「外れても害の小さい推測」レイヤに新設。

## CSS 追加ゼロ

`.need-resolution`（既存・アクセント枠）で描画。`.need-gate` はテスト用フックで CSS 規則を持たない。
`.label-chip` / `.mono` / `.badge info|warn` / `.summary-actions` / `.summary-link.secondary` は既存。

## 検証（実画面相当の DOM）

`node test/needs-gate-integration.test.js` → **8 passed**。DOM ライブラリが依存に無いため、
本物の `renderNeedFacts` / `renderNeedDetail`（スタブでごまかさず合成）が吐く画面 HTML を
タグスタック walker でノード木へ起こし、要素の入れ子・クラス・テキストで検証:

- 統合: `need-gate` が facts(`need-facts`) 内に出る／`一貫性ゲート` チップ／`regression_cmd`・
  `intake_cmd` mono／intake 未結線 `badge warn`／`data-open` で設定ファイルを開くボタン
- 可読性: `need-diag` 内の `検証失敗` チップ・`<strong>` 要約行・`need-failure-context` dl が残る／
  詳細カードで `<details class="need-detail"><summary>判断材料を見る</summary>` と `状況`/`判断すること`
  の h3 がゲート節と共存
- 分岐: intake 結線済みは「起票されます」＋開くボタン無し／`consistencyGate` 無しは概要誘導のみ／
  非ゲート失敗はゲート節を出さず要約は従来どおり／失敗無しはどちらも出さない
- walker 健全性: 未閉じ・入れ子不整合を openDepth で棄却することを別途確認（no-op でない）

回帰確認（`node test/<name>.test.js`）: needs-diagnosis 11 / needs-command-failure 4 /
needs-layout-ui / consistency-gate-ui / consistency-gate 4 / overview-ui / needs-notify / needs-sla
すべて PASS。`node --check src/renderer/sections/needs.js` PASS。

## 採用した前提

1. 完了条件の「ゲート状態セクション」= t5/t6 の `consistencyGateHtml`（概要タブ「一貫性ゲート」節）と解釈。
   「統合表示」は**同じ語彙・視覚言語の共有 ＋ 概要節への明示導線 ＋ regression/intake 結線状態を失敗の
   その場で提示**とし、概要節そのものを need カードへ丸ごと埋め込む（`<h2>` ごと）ことは
   見出し階層を崩すため採らなかった（可読性優先）。
2. codd-gate 専用 kind は無い（t2 §4.4）。`kind: blocked` の回帰失敗を command/文面で識別。
3. 「実画面相当の DOM」= 本物の描画関数を合成した画面 HTML をノード木で検証（孤立した純関数を
   スタブで叩くのではなく）。jsdom 等は依存に無く、新規依存追加はスコープ外なので同梱 walker で実現。
4. `p.consistencyGate` を needs 再描画 sig に追加（t2 §6.1）— 結線を変えたときに need 側も追従させる。

## 範囲外で見つけた問題（未修正・報告のみ）

- @followup `test/detail-tabs-ui.test.js` が `ReferenceError: commandFailureHtml is not defined` で
  失敗（t4/t5 と同一の既存不具合）。原因は当該テストの `renderNeedDetail` 抽出（`:448-476`）が
  自由変数 `commandFailureHtml` を注入していないこと。本タスクの変更前後で**エラーは不変**
  （signature 変更は同ファイルのスタブ `() => ''` が引数を無視するため無影響）。`npm test` は
  この地点で停止するため full green にできない。修正は 1 行（注入リストへ `commandFailureHtml` と
  スタブ追加）だが、テストハーネスの自由変数手動列挙という根因は別タスク相当。
