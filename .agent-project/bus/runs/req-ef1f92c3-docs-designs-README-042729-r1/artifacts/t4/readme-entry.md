# t4 成果物 — codd-gate-design.md の README 導線抽出

**切り口**: 一行要旨の核を「連携点（agent-project との結合）」に置く。charter v1 の目標が
「codd-gateと連携できること」である以上、README 導線は「codd-gate とは何か」だけでなく
「agent-project とどう繋がるか」を読者が一目で掴める形にすべき、という立場で抽出した。

## (a) 成果 — README 掲載用の抽出結果

### 相対リンクと一行要旨（README 掲載形）

```markdown
[`codd-gate-design.md`](./codd-gate-design.md) — ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツールの設計正典。agent-project 本体は無改造のまま、フック契約（E1〜E3）3点で結線される独立ツール（結合点は `schemas/` の共通データ契約のみ）。
```

- 相対パス: `./codd-gate-design.md`（README と同一ディレクトリ `docs/designs/` に配置されている前提）。
- 一行要旨は「独立ツールであること」と「連携の実体（フック契約 E1〜E3 で結線）」を両立させ、
  charter の「codd-gate と連携できること」を裏付ける記述を含めた。

### 要旨（3〜4文, README冒頭サマリーやカテゴリ表向け）

codd-gate は、ドキュメント・コード・テストの一貫性を「差分ゲート」（変更のたび）と
「負債ラチェット」（プロジェクト受入時）で常時維持する決定的ツール（CoDD の翻案）。
agent-project には依存しない独立ツールで、単体でも CI・git hook から使える。
agent-project との連携はオプションで、公式フック契約（`agent-project-design.md` §4.1 の
E1 verify/acceptance・E2 regression_cmd・E3 intake_cmd）3点にのみ差し込み、外せば元に戻る。

### 対象読者

1. **codd-gate 自体の実装・保守担当者** — 本書は「唯一の設計正典」（冒頭に明記）であり、
   実装と差が出たら本書を更新する運用。
2. **agent-project 側で連携を組む担当者** — §4「agent-project との結合点」が E1〜E3 の
   差し込み方・妥当性検証を規定しており、charter の「連携」目標を実現する担当者の一次資料。
3. **codd-gate をスタンドアロンで使いたい担当者**（CI・git hook・手元点検） — agent-project 抜きでも
   §3 の全ステージが完結する設計であることが本文で明示されている。

### 主要見出し（本文の章立て、行番号は codd-gate-design.md 時点）

| # | 見出し | 内容の要点 |
|---|---|---|
| 1 | `## 1. 全体像`（+ `### 不変条件`） | charter.md ではなく repos.json 経由で連携する全体図。6か条の不変条件（no fake green・未解決は黙って PASS にしない・ブラウンフィールド前提・決定的/stdlib のみ・安全ゲートは足す/止める方向のみ・単発有界） |
| 2 | `## 2. データモデル` | ノード＝成果物（identity は url/path/base）、kind 分類（doc>test>code>other）、接続推定の優先順位（注釈＞構文） |
| 3 | `## 3. 処理フロー（ステージ別）` | scan / impact・verify / verify --debt / tasks / check / git アクセス原則の各ステージ |
| 4 | `## 4. agent-project との結合点（オプション連携・プラグイン境界）` | **charter の「連携」目標に直結する中核節**。E1〜E3 フック契約への差し込み方・妥当性の検証（なぜ E2 に差分ゲートを置くか等）を明記 |
| 5 | `## 5. codd-dev からの主な翻案（差分）` | 元ネタ CoDD からの設計差分の一覧表 |
| 6 | `## 6. 制約と将来拡張` | ノード粒度はファイル単位（v1）、import 解決は Python のみ、など既知の制約 |

### 関連設計（本文中の明示参照）

- **`agent-project-design.md`**（同ディレクトリ） — §4 の結合点はこの設計書の「§4.1 フック契約カタログ（E1〜E6）」を正典として参照。連携の相手方であり、README でも隣接掲載が妥当。
- **`git-worktree-cache-pattern.md`**（同ディレクトリ） — §3「git アクセスの原則」内、`--sync` opt-in 時の repo 実体化パターンとして参照。
- **`schemas/repos.schema.json` / `schemas/task.schema.json`**（`docs/designs/` 外、リポジトリ直下 `schemas/`） — 設計書ではなくデータ契約だが、codd-gate と agent-project を疎結合に保つ結節点として本文で繰り返し参照される。README には直接リンクせず、両設計書の本文内リンクに委ねるのが妥当と判断。
- 外部参照: [CoDD (Coherence-Driven Development)](https://github.com/yohey-w/codd-dev) — 翻案元。README 導線としては対象外（本設計書内のリンクで足りる）。

### charter 目標との整合確認

charter `charters/v1.md` の goal:
- 「codd-gateと連携できること」
- 「設計書を整理して人間にとって読みやすくすること」

および acceptance:
- 「検証コマンドに codd-gate が組み込める」
- 「設計書と実装に乖離がない」

上記の一行要旨・要旨は、codd-gate を「独立ツール」とだけ紹介せず「フック契約 E1〜E3 で
agent-project と結線される」という連携の実体を明記しており、README を読んだ人間が
「codd-gate と連携できること」の実現方法（どこに差し込むか）まで一歩で辿れる。
これにより charter の「連携」目標に沿う導線になっていることを確認した。

## (b) 検証内容と結果

1. **見出し抽出の正確性**: `grep -n '^#' docs/designs/codd-gate-design.md` の実行結果（実装リポジトリ
   `/Users/nitto/Workspace/sandbox` に対して読み取り専用で実行）と本文の目視読み合わせで、上表の
   6見出しが本文の実見出しと一致することを確認した。
2. **関連設計の参照有無**: `agent-project-design.md` と `git-worktree-cache-pattern.md` への言及は
   本文中の実際のリンク記法・文言（`[agent-project-design.md](agent-project-design.md)` 等）を
   `sed`/読み取りで直接確認し、推測を含めていない。
3. **charter 目標の照合**: `charters/v1.md`（本 worktree `.agent-project/charters/v1.md`）を直接読み、
   goal/acceptance の文言をそのまま引用した（意訳や記憶による再構成はしていない）。
4. **完了条件との関係**: 本タスク（t4）は「抽出・整形」までが範囲であり、
   `docs/designs/README.md` への実際の書き込みは行っていない（後述の前提を参照）。
   そのため run 全体の完了条件コマンド（`test -f docs/designs/README.md && grep -q ...`）を
   本タスク単体では実行・確認していない — この判定は synth 系タスクの責務。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

### 採用した前提

- 本タスク（t4）の範囲は「codd-gate-design.md 1件分の抽出・README 導線用整形」に限定し、
  README.md ファイル自体の作成・編集は行っていない（依存タスク t1 の判断・報告と同じ立場を踏襲）。
- 抽出対象は charter が指す実装リポジトリ `/Users/nitto/Workspace/sandbox/docs/designs/codd-gate-design.md`
  とした（本 worktree `.agent-project` には `docs/` が存在しないため）。読み取り専用でアクセスし、
  書き込みは行っていない。
- 「対象読者」は本文中に明示のセクションが無いため、章立て・記述のトーン（設計正典としての
  規定文体、E1〜E3 差し込み点の妥当性検証など）から合理的に推定した。推定である旨をここに明記する。

### 未解決事項

- README への実際の反映（相対リンクパス、他3設計との並び順・体裁統一）は synth タスクの判断に委ねる。
- 実装リポジトリ側 `docs/designs/README.md` には既に本要旨とほぼ同内容の codd-gate-design.md 用
  エントリが存在する（t1 の報告と同様に確認済み）。synth 側で「既存エントリをそのまま採用する」か
  「本成果物の連携重視の文言で更新する」かの選択が必要（本タスクの範囲外のため判断はしていない）。

### 範囲外で見つけた問題

- t1 の報告と同じ所見を再確認: 本 worktree（`.agent-project`）には `docs/designs` 自体が存在せず、
  run の完了条件コマンドをこの worktree でそのまま実行すると失敗する。README.md の配置先
  （実装リポジトリか本 worktree か）の決定は本タスクの範囲外。
