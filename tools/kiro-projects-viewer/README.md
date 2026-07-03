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
| フロー | `<bus>/runs/<run-id>/`（`graph.json` + `results/` + `claims/` からノード状態を導出し DAG を描画。`events/*.jsonl` のアクティビティ付き）。バスは `<project>/bus` → `<container>/bus` → ⚙ 設定 → kiro-projects 設定ファイル（`.kiro/`）の `bus:` の順に自動発見。run の生存（orchestrator 応答なし）は `meta.json` の生存リース（`orch_lease_until`）から、daemon の稼働はロックファイル（`$TMPDIR/kiro-flow-locks/daemon-<sha1>.lock` の pid）から判定 — **kiro-flow CLI には一切聞かない** |
| GitLab | kiro-flow gitlab executor が results に残した `{issue_iid, web_url, decision, merged_mrs}` ＋ `repos.json` の GitLab リポジトリのオープンイシュー（API 設定時） |
| 履歴 | `run-log.jsonl`・`decisions/<id>.md`（DR）・`DELIVERY.md`・`journal.md` |

プロジェクトの発見は次の 2 系統:

1. **設定の roots** — ⚙ 設定に `.kiro-projects` コンテナ（kiro-projects の `--root` に渡す値）を登録
2. **自動発見** — `~/.kiro-projects/instances/*.json`（稼働発見レコード）から稼働中コンテナを検出。
   heartbeat が新鮮なプロジェクトには ● 稼働中マークが付く

`<root>/projects/<name>/` の標準レイアウトと、`projects/` を持たない旧フラット構成の両方に対応。

## 人のアクション（見るだけでなく、その場で判断を返せる）

kiro-projects の人間ループはこのアプリ内で完結できる。いずれも kiro-projects の
**公式な入力契約だけ**を使い、done の確定条件（verify のみが根拠）を迂回しない。

| 操作 | 場所 | 実装（入力契約） |
|------|------|-----------------|
| フィードバックして再開 | 要対応カード | `needs/<id>.md` の「## Decision Outcome」に記入 + `- [x]` 確定（`ingest_feedback` の正規ルート） |
| そのまま再実行 | 要対応カード（blocked） | 空記入で `- [x]` 確定 |
| 承認して done 確定 | 要対応カード（review / milestone） | `kiro-projects approve <id> --reason ...`（CLI 委譲・決定記録が残る） |
| 差し戻す | 要対応カード（review） | 修正方針の記入必須 → feedback として確定（手戻り扱い） |
| 保留（hold） | 要対応カード・タスク詳細 | `kiro-projects hold <id>`（policy.deny 追加） |
| 最優先へ / 後回し | タスク詳細 | `kiro-projects reprioritize <id> --pin/--defer` |
| ＋ タスクを追加 | バックログタブ | `inbox/<name>.json` ドロップ（E4 push 型取り込み口。verify / accept / priority / note 付き） |
| レビュー操作（承認/差し戻し/コメント） | GitLab タブ →「レビューで開く」 | gitlab-review-viewer へ引き継ぎ |

- 理由・方針の記入はすべて決定記録（`decisions/` の DR）や次 act への feedback として
  kiro-projects 側に残る
- ファイル書き込み（needs / inbox）は稼働中の kiro-projects の watch が自動で取り込む。
  CLI 操作は ⚙ 設定の「kiro-projects CLI」コマンドを使う（PATH に無ければ
  `python3 /path/to/kiro-projects.py` 形式で指定）
- 入力中は自動更新を一時停止する（書きかけのフィードバックが消えない）

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
  claim 勝者の決定的タイブレーク `(ts, who)` も kiro-flow 本体と同じ。
  run の生存判定は kiro-flow の `run_is_orphaned` と同じ導出（`orch_lease_until`
  のリース、無ければ `updated_at` の age）。daemon 稼働はロックパスの同一導出
  （`sha1("local::" + realpath(bus))`）＋記録 pid の生存確認（kiro-projects の
  fcntl 不在時フォールバックと同じ根拠）で、CLI を起動せずに判定する
- `src/main/toolconfig.js` … `.kiro/` の kiro-projects / kiro-flow 設定ファイルから
  `bus` / `lock_dir` などトップレベルのスカラだけを読む簡易リーダー
  （共有バス構成・ロック置き場の自動発見に使う）
- `src/main/gitlab.js` … GitLab REST v4 の読み取り専用クライアント（net.fetch・プロキシ対応）
- `src/main/review.js` … gitlab-review-viewer へのレビュー引き継ぎ（protocol / command）
- `src/main/actions.js` … 人のアクション層。needs 記入（Decision Outcome + `[x]`）・
  inbox JSON ドロップ・kiro-projects CLI（approve/hold/reprioritize）の 3 契約のみを使う
- IPC は gitlab-review-viewer と同じ `{ok, data|error}` 形式・`window.api` 公開

## 制限事項

- タスク本文（verify 等）の編集はファイルで行う（詳細ダイアログから開ける）。
  状態遷移を直接書き換える操作は意図的に持たない（done は verify のみが根拠、の
  不変条件をアプリから壊さないため）
- approve / hold / reprioritize は kiro-projects CLI が必要（旧フラット構成では
  --root/--project を組み立てられないため CLI 直接実行を案内する）
- `bus/` は kiro-projects が local run 後に掃除するため（`--no-cleanup` で保持）、
  フロータブは稼働中の run が主対象
- kiro-flow の状態（run 一覧・生存・daemon 稼働）はすべてファイルから判定するため
  kiro-flow CLI は不要。ただし daemon 稼働の pid 判定は同一ホスト上でのみ有効
  （Windows のビュアーから WSL 内の daemon は temp 領域が別のため見えない — その
  場合も run の生存リースによる「応答なし」判定は共有バスのファイルだけで機能する）
- GitLab 書き込み操作は持たない（レビュー操作は gitlab-review-viewer の役割）
