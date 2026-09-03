# feature: documents — ドキュメント（文書ルールに沿った文書作成）

エージェント CLI に文書（Word / PowerPoint / Excel / Markdown / draw.io の SVG）を作らせ、
検証し、改訂履歴をサイドカーに残す制御面。領域ナビの「ドキュメント」で、タブは
**文書**（一覧・詳細・作成／続き／検証／フィードバックの入口）、**文書ルール**（ルールの
一覧・本文・作成・編集）、**設定**（置き場）の 3 つ。

## モジュール構成（依存は一方向）

| ファイル | 役割 | 他の制御面への依存 |
|---|---|---|
| `main/formats.js` | 対応形式のカタログ（id・表示名・拡張子・依頼文の手掛かり）。**形式を足すのはここだけ** | なし |
| `main/rules.js` | 文書ルール（1 物理ファイル）の書式と読み書き | なし |
| `main/sidecar.js` | 改訂履歴の書式（見出しの正典）と追記。書き手（dashboard）と依頼文の雛形を同じ表から作る | なし |
| `main/store.js` | 文書フォルダ・定義（document.json）・入力の写し・成果物の走査 | なし（`base/agent-home` のみ） |
| `main/prompts.js` | 依頼文（決定的） | なし |
| `main/launcher.js` | **他の制御面へのアダプタ**（対話ウィンドウ起動・ヘッドレス助言）。agent-project / cowork / loopProvider への依存はここに閉じる | あり |
| `main/documents.js` | 用途層。操作の表 `SESSION_KINDS`（作成・続き・検証）と、ルール化の 3 経路 | launcher 経由のみ |
| `main/ipc.js` / `preload.js` | IPC の入口 | — |

- 対話セッションを起こす操作を足すときは `SESSION_KINDS` に 1 行（表示名・履歴名・タイトル・
  依頼文・先に残す履歴項目）を足し、`prompts.js` に依頼文を足す。画面は `overview.actions` /
  `overview.modes` / `overview.formats` から表示名を受け取るので、画面側に表を複製しない。
- テストは `launcher.launchWindow` / `launcher.advise` を差し替えて、他の制御面なしで用途層を
  検証できる（`test/documents.test.js`）。

## 置き場（1 文書 = 1 フォルダ、1 ルール = 1 ファイル）

```
<workspaceDir>/<id>/                 文書フォルダ（既定 ~/.agents/documents/<id>/）
  document.json                      文書の定義: 名前・形式・進め方・使ったルール・依頼・入力・成果物の一覧
  <id>.history.md                    改訂履歴のサイドカー（変更・利用者の意図・指摘事項）
  inputs/                            入力ファイルの写し（エージェントは inputs/… を相対パスで読む）
  <成果物>.docx / .pptx / .xlsx / .md / .drawio.svg
<rulesDir>/<slug>.md                 文書ルール（既定 ~/.agents/document-rules/）
```

- 成果物の一覧は**フォルダの実ファイル**から数える（`scanOutputs`。サブフォルダも見る。
  `inputs/` と隠しフォルダは除く）。`document.json` の `outputs` はエージェントが書く補足
  （役割・関係）で、無くても一覧は出る——記録漏れで成果物が見えなくなるのを避ける。
- 文書ルールのコピー・削除は OS のファイル操作で行う。アプリは作成・更新・閲覧だけ
  （「ルールのフォルダを開く」で辿り着ける）。
- 文書フォルダの削除も同じく OS で行う。dashboard は成果物を書かず、消しもしない。

## 文書ルールの書式（`main/rules.js` が正典）

```
---
name: 提案書
formats: docx, pptx
---
# 文書ルール: 提案書

## 対象と目的      … 誰に何のために読ませるか
## テンプレート    … 雛形・既存文書の場所と使い方、構成の骨子
## 定型と体裁      … 書式・分量・用字用語・図表の扱い
## 記述内容        … 各部分に何を書くか、書かないか
## 注意点          … 過去の指摘・つまずき・レビュー観点
## 区分            … 意味的区分（章立て）。「- 名前 — 説明」1 行 1 区分
```

「区分」は**区分ごと作成**の単位。節が欠けたファイルも読み（欠けは `missing` に返し画面で
警告）、保存時は `normalizeRuleText` が front matter と 6 節を補う。外部で書いたルールを
弾かない。

## 2 種類のエージェント起動

| 操作 | 起動 | 権限 | 書くもの |
|---|---|---|---|
| 作成 / 続き / 検証 | 文書フォルダを cwd にした**対話ウィンドウ**（`runChatWindow`。interactive を持たない CLI は `runHeadlessRoutine`） | 書き込み可 | 成果物・`document.json` の `outputs`・サイドカーの追記 |
| ルールの下書き（原案から／改訂履歴から／フィードバックから） | **ヘッドレスの助言**（`resolveDashboardAgent` → `runAgent`） | 読み取り専用 | 何も書かない。本文を画面へ返し、人が編集して保存（dashboard が `rulesDir` へ書く） |

作成・続き・検証を外部ターミナルにするのは、**徹底的な質問**（読者・目的・範囲・用語・
構成・体裁・根拠・禁止事項・合否基準・納期）・区分ごとの確認・指摘の取捨が人との対話そのもの
だから。dashboard 内で往復を作らず、定常業務のアドホック起動と同じ部品（CLI/モデルの解決・
共通指示の前置・セッション開始コマンド）で起こす。使う CLI は実行制御の workload
`documents` で指定でき、無ければ通常の解決順（設定 → 既定）に従う。

dashboard の LLM 呼び出しは助言のみ（読み取り専用）が原則だが、この制御面の作成・検証は
**文書フォルダの中だけ**を書く対話セッションとして例外にする。状態リポジトリ・プロジェクト・
ルールファイルはそのセッションからも書かない約束を依頼文に含める。

## 依頼文（`main/prompts.js`、決定的）

- `createPrompt` … 作業の約束（フォルダの外を書かない・捏造しない）・ルール全文・依頼・入力・
  形式ごとの手掛かり（presenter / xlsx-report-builder / doc-coauthoring スキルがあれば使う）・
  **まず徹底的に質問する**手順・一気に作る／区分ごとに作る・サイドカーと `outputs` の記録規則
- `verifyPrompt` … 汎用の検証観点（用語のブレ・整合性・論理性・つながり・人間のわかりやすさ・
  AI 臭の排除・ルール適合）＋利用者が入力した**ドメイン固有のレビュー結果**。直す前に確認を取る
- `resumePrompt` … 改訂履歴を先に読ませてから今回の指示を渡す（区分ごと作成の「次の区分へ」も同じ）
- `ruleDraftPrompt` / `ruleFromHistoryPrompt` / `feedbackRulePrompt` … 出力はルール本文だけ

## 改訂履歴（サイドカー）の形

```
## 2026-09-03 10:00 — 作成の依頼（利用者）

### 変更
### 利用者の意図
### 指摘事項
```

dashboard は人が起こした行（作成・続き・検証・フィードバックの依頼と、その本文）を書き、
エージェントには成果物を書き換えるたびに同じ形で追記させる。**変更だけでなく意図と指摘を
残す**のは、これが次の文書ルールの元になるため（「改訂履歴からルールを起こす」「完成後の
フィードバックを既存／新規ルールにする」の材料）。

## 設定（`config.documents`）

- `workspaceDir` … 文書フォルダの置き場。空なら共有ホームの `~/.agents/documents`
- `rulesDir` … 文書ルールの置き場。空なら `~/.agents/document-rules`

## 検証

`cd tools/agent-dashboard && node --test test/documents.test.js test/documents-ui.test.js`
