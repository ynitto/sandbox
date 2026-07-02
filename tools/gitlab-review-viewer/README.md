# gitlab-review-viewer

GitLab のイシューと MR を**並べて表示するレビュー専用ビュアー**。
Windows 用の exe としてビルドして起動できる Electron アプリ。

```
┌──────────────┬──────────────────────────┬──────────────────────────┐
│ 検索条件      │ [Issue #42] [MR !108]     │ [Issue #42] [MR !108]     │ ← タブで表示切替
│  グループ     │ URL: https://gitlab...↻⧉↗ │ URL: https://gitlab...↻⧉↗ │ ← URL バー
│  リポジトリ   │                          │                          │
│  ラベル       │   イシューページ          │   関連 MR ページ          │
│  種別/状態    │   （GitLab をそのまま     │   （webview 埋め込み）    │
│  キーワード   │     埋め込み表示）        │                          │
│ ─────────    │                          │                          │
│ 候補一覧      ├──────────────────────────┴──────────────────────────┤
│  Issue #42 …  │ 操作対象: [Issue #42 ▼]  opened  status:review-ready │
│  MR !108 …    │ [status:open Ctrl+1] [status:approved Ctrl+5] …      │
│  Issue #40 …  │ [コメント入力____________] [投稿][要約][マージ][クローズ]│
└──────────────┴──────────────────────────────────────────────────────┘
```

## 機能

- **条件を組み合わせた候補絞り込み** — グループ / リポジトリ（プロジェクト）/
  ラベル（複数・AND）/ 種別（イシュー・MR）/ 状態 / キーワードをすべて AND で
  組み合わせて検索し、候補一覧からユーザーが選択する
- **関連ページの並列表示** — 候補を選択すると、イシューなら関連 MR
  （`related_merge_requests` + `closed_by`）、MR ならクローズ対象イシュー
  （`closes_issues`）を自動で取得し、GitLab ページを 2 ペインに並べて表示。
  関連が複数ある場合は各ペインのタブで表示切替できる。各ペインに URL バーが
  あり、コピー / OS 既定ブラウザで開くことも可能
- **ローカル CLI エージェントによる要約** — Obsidian Web Clipper のように、
  表示中のイシュー / MR の本文・コメント・変更ファイル一覧を指定エージェント
  （既定は `kiro-cli`）に送って要約させる
- **コメント投稿・ラベル変更** — ビュアーで見た結果をそのままコメント投稿。
  要約結果をコメント欄へ挿入することもできる
- **ラベルショートカット + マージ / クローズ** — `Ctrl+1`〜で status ラベルを
  ワンタッチ変更（gitlab-idd スキルのラベル規約が既定）。マージ・クローズ・
  リオープンもボタン / ショートカットで実行
- **Obsidian エクスポート（オプション）** — 完了したイシュー / MR を要約付きの
  Markdown（frontmatter 付き）にして Vault のフォルダへ書き出す
- **kiro-autonomous needs 対応** — 自律開発ループが人へ差し出す判断待ち / 検収待ち
  （`needs/<id>.md`、MADR 互換 ADR）を一覧・表示し、フィードバック記入 → 確定（`[x]`）
  や `approve` をビュアーから実行できる

## セットアップ

```bash
cd tools/gitlab-review-viewer
npm install
npm start          # 開発起動
```

### Windows exe のビルド

Windows 上（または wine 環境）で:

```bash
npm run dist            # release/ に portable exe + NSIS インストーラ
npm run dist:portable   # portable exe のみ
```

`release/GitLab Review Viewer 0.1.0.exe`（portable）をそのまま配布・起動できる。

### 初回設定

1. 起動後、右上の **⚙ 設定** を開く
2. **GitLab URL**（例: `https://gitlab.com` やセルフホスト URL）と
   **アクセストークン**（`api` スコープ）を入力して保存
3. 埋め込みページ側は初回に GitLab へのログインが必要（セッションは
   `persist:gitlab` パーティションに保持され、次回以降は不要）

設定は `%APPDATA%/gitlab-review-viewer/config.json` に保存される。

## 使い方

1. サイドバーで条件を設定して **候補を検索**
   - グループ / プロジェクトは 🔍 で検索して選択（未指定なら自分が参加する全体から検索）
   - ラベルはカンマ区切りで複数指定（AND 条件）。プロジェクト選択後は入力補完が効く
2. 候補一覧からイシューまたは MR をクリック → 関連ページと合わせて 2 ペイン表示
3. 下部のアクションバーで操作
   - **操作対象** — 表示中ページのうちどれに対して操作するかを選択
   - **コメント** — 入力して `Ctrl+Enter` または「コメント投稿」
   - **ラベル** — プリセットボタンまたはショートカット。`status:*` は排他
     （他の status ラベルを外して付け替え）、`assignee:any` はトグル
   - **要約** — エージェントに送信し、結果をダイアログ表示。
     「コメント欄へ挿入」「Obsidian へ送る」が可能

## ショートカット（既定・すべて設定でカスタマイズ可能）

| キー | 動作 |
|------|------|
| `Ctrl+1`〜`Ctrl+8` | `status:open` / `blocked` / `in-progress` / `review-ready` / `approved` / `needs-rework` / `needs-clarification` / `done` |
| `Ctrl+Shift+1/2/3` | `priority:high` / `normal` / `low` |
| `Ctrl+Shift+0` | `assignee:any` トグル |
| `Ctrl+Enter` | コメント投稿 |
| `Ctrl+Shift+M` | マージ |
| `Ctrl+Shift+D` | クローズ |
| `Ctrl+Shift+R` | リオープン |
| `Ctrl+Shift+S` | 要約 |
| `Ctrl+Shift+E` | Obsidian へエクスポート |

※ webview（GitLab ページ内）にフォーカスがある間はアプリ側ショートカットは
効かない。アクションバーなどアプリ側 UI をクリックしてから使う。

## ラベルプリセットのカスタマイズ

既定は gitlab-idd スキルのラベル規約（`status:*` / `priority:*` /
`assignee:any`）。設定画面の「ラベルプリセット（JSON）」で自由に変更できる:

```json
[
  { "label": "status:approved", "exclusivePrefix": "status:", "shortcut": "Ctrl+5" },
  { "label": "needs-discussion", "toggle": true, "shortcut": "Ctrl+9" }
]
```

- `exclusivePrefix` — 同じ接頭辞の他ラベルを外してから付ける（排他グループ）
- `toggle` — 付いていれば外す / 無ければ付ける
- `shortcut` — `Ctrl+…` / `Ctrl+Shift+…` / `Alt+…` の形式。省略可

## エージェント（要約）の設定

設定画面の「エージェントコマンド」にコマンドテンプレートを指定する。

| プレースホルダ | 意味 |
|----------------|------|
| `{promptFile}` | プロンプト全文を書き出した一時ファイルのパス |
| `{prompt}` | プロンプト全文を argv で渡す（100KB 超は自動でファイル退避 + 参照渡し） |
| （どちらも無し） | 標準入力にプロンプトを流し込む |

既定（kiro-cli）:

```
kiro-cli chat --no-interactive --trust-all-tools "{promptFile} に要約タスクの指示があります。このファイルを読み込み、指示に従って要約だけを出力してください。"
```

他のエージェント例:

```
claude -p "{prompt}"
copilot --no-interactive {promptFile}
```

要約プロンプト本文（`{title}` `{url}` `{state}` `{labels}` `{description}`
`{notes}` `{changes}` `{typeLabel}` が使える）も設定画面で編集できる。

## kiro-autonomous needs（判断待ち / 検収待ち）

サイドバー上部の **Needs** タブに切り替えると、kiro-autonomous が人へ差し出した
案件（`<コンテナ>/projects/<プロジェクト>/needs/<id>.md`）を一覧できる。

1. 設定画面で **kiro-autonomous コンテナ**（例: `C:\work\repo\.kiro-autonomous`）を指定
2. 「Needs を更新」で走査 → 一覧から案件を選択するとペインに ADR を表示
3. 下部のアクションバーで操作
   - **フィードバック確定 [x]** — 入力欄の方針を Decision Outcome 欄に書き込み、
     確定チェックを `[x]` にする。kiro-autonomous が次パス（`--watch` なら次 poll）で
     自動取り込みして再開する。空のまま確定すると blocked は「そのまま再実行」、
     review は「承認」扱い
   - **approve** — `kiro-autonomous approve <id>` を実行（検収承認 = done 確定 /
     修正承認 = ready 積み直し）。コマンドは設定画面の `approve コマンド` で変更可能
     （`{id}` `{root}` `{project}` `{reason}` が使える）
   - **要約** — 案件の全文をエージェントに送り、判断ポイントを整理させる

### needs ファイルの形式（MADR 互換）

kiro-autonomous の needs は標準 ADR フォーマットの
[MADR](https://adr.github.io/madr/)（Markdown Any Decision Records）互換で生成される:

```markdown
---
status: proposed        # 確定すると accepted に更新される
date: 2026-07-02
decision-makers: [human]
task-id: T1
kind: blocked           # blocked / review / milestone
---

# 要対応: T1 — <タイトル>

## Context and Problem Statement

- なぜ: <理由>
- 状態: <状態>

## 判断材料（成果物の所在・差分・検証）

## Decision Outcome

- [ ] 確定（このボックスを [x] にして保存すると取り込みます）
```

旧形式（`## フィードバック` 欄）のファイルも表示・確定とも互換で扱える。

## Obsidian エクスポート

設定画面で **Obsidian Vault パス**（と任意のサブフォルダ）を指定すると、
「Obsidian へ」ボタン / `Ctrl+Shift+E` で以下の Markdown を書き出す:

- frontmatter: タイトル / URL / 種別 / 状態 / ラベル / 作成者 / 日時
- 直近の要約（あれば）、説明本文、変更ファイル一覧（MR）

「書き出し後に Obsidian を開く」を有効にすると `obsidian://` URI で
Obsidian が起動する。

## 構成

```
tools/gitlab-review-viewer/
  package.json          # electron + electron-builder のみ（実行時依存なし）
  src/
    main/
      main.js           # ウィンドウ生成・webview 制御
      config.js         # 設定の読み書き（既定値は gitlab-idd ラベル規約）
      gitlab.js         # GitLab REST API v4 クライアント（fetch のみ）
      kiro.js           # kiro-autonomous needs の一覧・フィードバック・approve
      agent.js          # ローカル CLI エージェント実行（要約・approve コマンド）
      obsidian.js       # Markdown 生成・Vault への書き出し
      ipc.js            # IPC ハンドラ
    preload.js          # contextBridge（renderer へ安全に API を公開）
    renderer/           # UI（フレームワーク非依存の素の JS/HTML/CSS）
```

## 制限事項

- ペイン内のタブ切替はページを再読み込みする（webview はペインごとに 1 つ）
- 埋め込み表示は GitLab の Web セッション、API 操作はアクセストークンと、
  認証が二系統ある（それぞれ初回に設定 / ログインが必要）
- 候補検索は各種別につき最新 50 件まで
