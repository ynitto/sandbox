# kiro-projects-viewer

kiro-projects のプロジェクト状態をダッシュボードとして可視化する Electron アプリ。
[gitlab-review-viewer](../gitlab-review-viewer/) と同じ構成（プレーン Electron・
ランタイム依存なし・main / preload / renderer の 3 層）で作られている。

```
┌ サイドバー ────────┐┌ メイン ─────────────────────────────────────────┐
│ コンテナ(--root)    ││ 概要      charter / acceptance 達成状況 / 統計    │
│  └ プロジェクト     ││ バックログ  タスク一覧（status / priority / verify）│
│     ● 稼働中        ││ 要対応     needs/（人の判断待ち・検収待ち）        │
│     [needs] [tasks] ││ フロー     kiro-flow run のタスクグラフ（DAG）     │
│                     ││ GitLab    委譲イシュー → レビューへ引き継ぎ       │
│                     ││ 履歴      run-log / 決定記録 / 納品 / journal     │
└─────────────────────┘└──────────────────────────────────────────────┘
```

## 何が見えるか（データソース）

すべて **読み取り専用**。kiro-projects / kiro-flow のファイルを直接読む
（両ツールの稼働は不要。稼働中なら自動更新で追従する）。

| タブ | データソース |
|------|-------------|
| 概要 | `charter.md`（goal / deliverables / acceptance）・`project.json`（acceptance PASS 履歴）・`backlog/` 集計・`policy.md`・`claims/`・`run-log.jsonl`・`DELIVERY.md` |
| バックログ | `backlog/<id>.md`（1 ファイル = 1 タスク。status / priority / verify / after 等）・`archive/<id>.md`（done） |
| 要対応 | `needs/<id>.md`（MADR 形式。blocked / review / milestone。「ファイルを開いて回答」でエディタへ） |
| フロー | `<project>/bus/runs/<run-id>/`（`graph.json` + `results/` + `claims/` からノード状態を導出し DAG を描画。`events/*.jsonl` のアクティビティ付き） |
| GitLab | kiro-flow gitlab executor が results に残した `{issue_iid, web_url, decision, merged_mrs}` ＋ `repos.json` の GitLab リポジトリのオープンイシュー（API 設定時） |
| 履歴 | `run-log.jsonl`・`decisions/<id>.md`（DR）・`DELIVERY.md`・`journal.md` |

プロジェクトの発見は次の 2 系統:

1. **設定の roots** — ⚙ 設定に `.kiro-projects` コンテナ（kiro-projects の `--root` に渡す値）を登録
2. **自動発見** — `~/.kiro-projects/instances/*.json`（稼働発見レコード）から稼働中コンテナを検出。
   heartbeat が新鮮なプロジェクトには ● 稼働中マークが付く

`<root>/projects/<name>/` の標準レイアウトと、`projects/` を持たない旧フラット構成の両方に対応。

## gitlab-review-viewer との連携（レビューの引き継ぎ）

GitLab タブの「**レビューで開く**」を押すと、そのイシューを gitlab-review-viewer で開く。

- 既定は **カスタム URL スキーム**: `gitlab-review-viewer://open?url=<イシューの web_url>` を
  OS 経由で開く。gitlab-review-viewer 側はディープリンク対応済み（シングルインスタンス化
  されており、起動済みならそのウィンドウで対象イシュー + 関連 MR を開く。未起動なら起動する）。
  プロトコル登録はインストーラ（NSIS）または初回起動時に行われる。
- プロトコルが使えない環境では ⚙ 設定で **コマンド起動** に切り替えられる:
  `"C:\Apps\GitLab Review Viewer.exe" "{url}"`（`{url}` `{projectPath}` `{type}` `{iid}` を置換）

逆方向として、本アプリ自身も `kiro-projects-viewer://open?root=<container>&project=<name>` の
ディープリンクを受け付ける（他ツールから特定プロジェクトのダッシュボードを直接開ける）。

## セットアップ

```bash
cd tools/kiro-projects-viewer
npm install
npm start                # 開発起動
npm run dist             # Windows 向けビルド（portable + NSIS → release/）
```

初回起動後、⚙ 設定で:

1. **コンテナのパス** を 1 行 1 つで登録（例 `C:\work\repo\.kiro-projects`）。
   kiro-projects が稼働していれば自動発見だけでも表示される。
2. （任意）**GitLab の Base URL / トークン**（read_api で十分）。イシューの最新状態
   （ラベル・関連 MR）の補完と、repos のイシュー一覧に使う。未設定でも bus 上の
   情報だけで動く。
3. （任意）自動更新間隔（既定 5 秒。0 で手動 ⟳ のみ）。

設定は `userData/config.json`（Windows: `%APPDATA%/kiro-projects-viewer/config.json`）に保存される。

## 実装メモ

- `src/main/kiro.js` … kiro-projects データ層。パース規則は kiro-projects.py の
  `HEAD_RE` / `FIELD_RE` / `parse_charter` / `parse_policy` と同じ（書式の正典は
  `tools/kiro-projects/backlog.md.example` / `charter.md.example`）
- `src/main/flow.js` … kiro-flow バスのリーダー。状態はファイル存在から導出
  （`results/` → done/failed、lease 内 `claims/` → claimed、依存未達 → waiting）。
  claim 勝者の決定的タイブレーク `(ts, who)` も kiro-flow 本体と同じ
- `src/main/gitlab.js` … GitLab REST v4 の読み取り専用クライアント（net.fetch・プロキシ対応）
- `src/main/review.js` … gitlab-review-viewer へのレビュー引き継ぎ（protocol / command）
- IPC は gitlab-review-viewer と同じ `{ok, data|error}` 形式・`window.api` 公開

## 制限事項

- 表示専用。タスクの編集・approve・needs への回答はファイル / kiro-projects CLI で行う
  （needs はボタンからファイルを開ける）
- `bus/` は kiro-projects が local run 後に掃除するため（`--no-cleanup` で保持）、
  フロータブは稼働中の run が主対象
- GitLab 書き込み操作は持たない（レビュー操作は gitlab-review-viewer の役割）
