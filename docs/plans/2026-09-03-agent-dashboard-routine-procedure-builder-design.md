# 定型手順ビルダー設計 — 画面操作と AI の判断を並べて定型業務を組み立てる

> 作成 2026-09-03 ／ 対象: `tools/agent-dashboard`（cowork 制御面）
>
> 効く柱・原則: **柱2×柱3 / C1・C4・C7** — API や CLI の無い社内システムを相手にする定型作業を、
> 既存の定常業務（statemachine-use）の上に載せる。画面が組むのは工程列だけで、定義（YAML）は
> スキルの作成モードが書き、実行は既存の「今すぐ実行」／定期実行をそのまま使う。

## 背景

社内システムやローカルルールに特化した定型作業——「勤怠システムで月次集計を出力し、申請一覧を
ブラウザで読み、差し戻しが要るか判断して一覧にする」のような仕事——は、対象が API も CLI も
持たないことが多い。リポジトリには既に画面操作の道具が揃っている:

- ブラウザ: `@playwright/cli`（`install.py` が入れ、`playwright-cli` スキルとして展開）
- Windows アプリ: `tools/winauto`（pywinauto の CLI。`windows-app-automation` スキルが使い方を持つ。
  WSL からはラッパーで Windows 側 Python を呼ぶ）

定常業務（statemachine-use のステートマシン）も既にあり、`check`（ハーネスが実行する検査コマンドの
終了コード）と `condition_rule` で決定的な遷移を書ける
（[決定的検査設計](../designs/statemachine-deterministic-check-design.md)）。

欠けていたのは、**道具の使い分けとエージェント処理（生成・判断）の差し込みまで含めて、工程列を
簡単に組む入口**である。今は手順を自由文で書いて作成モードへ渡すだけで、「どの工程でどのスキルを
名指しするか」「どこに `check` を置くか」「分岐をどう出力契約と遷移で表すか」を毎回書き下ろす
ことになっていた。

## 調査結果（既存の足場）

- **作成の入口は 1 つ。** `cowork:generateStateMachine` が自由文の指示を statemachine-use の
  作成モードへ渡し、外部ターミナルの対話 CLI が `.statemachine/<machine>/` を書く。dashboard は
  YAML を組み立てない（[セッション流用設計](./2026-08-31-agent-session-reuse-rerun-design.md) §2、
  `adhoc-flow/main/reuse.js` の蒸留も同じ入口）。
- **ハーネスの制約。** ヘッドレスの `agent-loop statemachine` はシェルを介さず argv を直接実行し、
  `sh` / `bash` 等の起動を拒む。`check` にシェル記号があれば投入前にエラーになる。
  対話ペイン（クラウド CLI）はエージェント自身のシェルから CLI を呼べる。どちらでも
  **PATH 上の `playwright-cli` / `winauto` は argv で呼べる**。
- **スキルの名指し記法。** アクション本文に `` `skill-name` スキル `` と書かないとハーネスは
  スキルを読み込まない（SKILL.md「作成モード」原則 6）。
- **入力パラメータ**の検出・検証・置換は `base/main/template-parameters.js` の 1 実装で、
  実行時変数（`last_output` / `check_ok` 等）は予約語として入力にしない。
- 作業項目（`cowork.items`）は dashboard 設定に手動項目を持ち、`saveWork` が `stripRuntimeFields`
  で実行時フィールドだけを落として保存する。任意のフィールドを項目に持ち回れる。

## 検討した案

| アプローチ | 実装コスト | リスク | 保守性 | 拡張性 | 推奨度 |
|---|---|---|---|---|---|
| A. 工程列を画面で組み、main が**作成モードへ渡す指示文**へ変換する（YAML はスキルが書く） | 低〜中 | 低 | 高 | 高 | ★★★ |
| B. 画面で workflow.yaml を直接組み立てる | 中 | 高（書式が 2 実装になる） | 低 | 中 | ★☆☆ |
| C. 画面操作専用の実行系（RPA ランナー）を dashboard に新設する | 高 | 高 | 低 | 中 | ★☆☆ |

案 A を採用する。B は「書式の正典をスキルと画面の 2 か所に置かない」に反し、C は既存の
実行系（対話ペイン／ヘッドレスハーネス・`check`・定期実行・履歴）を捨てて作り直すことになる。

## 採用設計

### 1. 定型手順（工程列）の形

```js
{
  version: 1,                          // 正規形の版。形を変えるときに上げ、古い項目を読み分ける
  purpose: '目的（1〜2 文）',
  steps: [{
    kind: 'browser' | 'windows' | 'skill' | 'command' | 'agent',
    title: '短い名前（省略可）',
    detail: '何をするか',               // command は補足（省略可）
    target: 'URL | アプリ名 | スキル名 | argv',   // agent は持たない
    check: 'argv（省略可）',             // 完了の確認コマンド。終了コード 0 で通過
    outcomes: [{ label: 'APPROVED', to: 'next' | 'step:<n>' | 'done' | 'abort' }],
  }],
  finish: '終了条件（省略可）',
  notes: '注意事項（省略可）',
}
```

- `outcomes` が無い工程の出力契約は `OK / FAILED`（OK → 次へ、FAILED → 失敗で終了）。
  ある工程は第 1 行にラベルを書かせ、ラベルごとの行き先を遷移にする。ラベルの空白は `_` に
  畳む（`startswith` 比較の揺れを作らない）。
- `check` と command の `target` にシェル記号（`| & ; < > \` $(`）があれば投入前に断る。
  ハーネスの制約と同じ理由——黙って別物を実行させない。
- 本文の `{{key}}` は入力パラメータ。検出は template-parameters を共有し、予約語は除く。

### 1.1 種類の正典は main の種別カタログ 1 か所

工程の種類は `procedure.js` の `STEP_KINDS` に 1 項目ずつ宣言する。1 項目が持つのは、画面が
入力欄を描く材料（表示名・説明・対象欄・内容欄・確認コマンド欄）、指示文が名指しする移譲先
スキル（種類に固定か、工程が名前で指定するか）、指示文「使う道具」節の案内、道具の診断
（コマンド・引数・出力の読み方・未準備時の案内）。画面は `cowork:procedureCatalog` で
描画用の部分だけを受け取り、種類の写しを持たない（テストが固定する）。

**種類を足す手順は 1 手**——この配列へ 1 項目足す。画面のボタン・入力欄、指示文の案内、
道具の診断がそろって増える。「スキルに任せる」（`redmine-use` / `outlook-use` などリポジトリの
`*-use` スキルを名前で名指し）はこの形で足した最初の種類で、社内システムごとのスキルを
そのまま工程にできる。

### 2. 指示文への変換（main の 1 実装）

`features/cowork/main/procedure.js` が正規化（`normalizeProcedure`）と指示文生成
（`procedureInstruction`）を担う。指示文は Markdown で、スキルの分解原則に沿う:

- 「使う道具」節で、使う種類の案内だけを載せる（カタログの `guidance`。`playwright-cli` /
  `windows-app-automation` / 名指ししたスキルの SKILL.md に書かれた使い方だけを使う、等）。
  偵察してから操作する（snapshot / `winauto tree` → 操作 → 読み取り）、想定外の画面では
  FAILED を返して別の操作を試さない、秘密情報を本文に書かない、を書く。
- 「工程」節は 1 工程 1 見出し。対象・内容・移譲先スキル・`check` の宣言（宣言した工程からは
  `equals:check_ok:true` で進む）・出力契約・遷移を並べる。分岐はアクション本文ではなく
  transitions に書くことを明記する。
- 「入力パラメータ」「終了条件」「守ること」（同じ工程へ戻る遷移には回数上限、作成後の
  `--dry-run` 検証）を続ける。
- **YAML の骨組みは書かない。** テストが `initial_state:` / `states:` / `transitions:` の不在を固定する。

### 3. 入口と起動 — 増やさない

- 作業タブに「手順を組み立てる」を足す。設定変更ダイアログの手順付き作業からも同じ画面へ移れる。
  画面のコードは `renderer/sections/procedure.js` に独立させ、cowork 側に頼るのは作業項目の
  下書き・選択中フォルダ・描き直しだけにする（実行・保存の経路には触れない）。
- 「作成を開始」は `cowork:generateStateMachine` に `payload.procedure` を渡す。main が指示文へ
  変換し、従来どおり `stateMachineCreationPrompt` で作成モードを起動する。戻り値に指示文と
  正規化した工程列を含め、画面は作業項目に `procedure` と `instruction` を残す。
- 作成後は発見が `.statemachine/<machine>/` を拾い、「変更を保存」で `statemachine:` 宣言と予定を
  `agent-loop.yml` へ書く。実行は「今すぐ実行」／定期実行の既存経路そのまま。
- 「指示文を確認」（`cowork:procedurePreview`）は起動せずに指示文と入力パラメータを返す。
- 「道具を確認」（`cowork:procedureTools`）は `playwright-cli --version` と
  `winauto doctor --output json` だけを呼ぶ。LLM もデスクトップロックも使わない。win32 は他の
  実行と同じく wsl.exe 経由（道具は実行側と同じ側に要る）。

### 4. 作り直し

工程列は作業項目（`cowork.items[].procedure`）に残る。項目の「手順を組み立て直す」で同じ画面を
開き、作成を開始すると新しい指示文で作成モードを起こす。実体（workflow.yaml）は毎回スキルが
書き直すので、画面が YAML を差分編集することはない。

## 非目標

- 画面が workflow.yaml を読み戻して工程列へ逆変換すること（書式の正典をもう 1 か所に置かない）。
- 画面操作の記録（レコーダー）。要素の特定は作成モード／実行時のエージェントが偵察して行う。
  → **2026-09-04 に再評価し、記録を偵察の前段として採用した**（[検証と設計](./2026-09-04-agent-dashboard-routine-recording-feasibility.md)）。偵察（待機と読み取り）は残る。
- 新しい実行系・状態ストア・デーモン。
- 並列工程（Fan-out）。必要なら作成モードのパターン検出に任せる。

## 検証

```bash
cd tools/agent-dashboard
node test/routine-procedure.test.js
npm test
```

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-03 |
| 採用案 | 画面は工程列を組み、main が作成モードへ渡す指示文へ決定的に変換する。作成・実行は既存経路 |
| 却下案 | 画面での YAML 組み立て（2 実装）、画面操作専用ランナー（既存実行系の再発明） |
| 主な理由 | statemachine-use が `check` / `condition_rule` で決定的な遷移を持ち、`playwright-cli` / `winauto` が argv で呼べる CLI として揃っている。欠けていたのは組む入口だけ |
| トレードオフ | 生成される定義の質は作成モードのエージェントに依る（画面は指示文の質で担保する）。workflow.yaml の手直しは画面へ戻らない |
| 再評価条件 | 作成モードが指示文を読み違える事例が続く場合（指示文の節を増やすか、scaffold への直接指定を検討）、並列工程の需要が出た場合 |
