# agent-tools-rename-design.md — README導線抽出（t5）

**切り口**（他候補との差別化）: 要旨を「設計意図の説明」ではなく「読者が最初に知りたい移行状況（完了/未了）」中心に組み立てる。本設計書は移行が進行中（`kiro-loop → agent-loop` のみ未了）のため、README導線の一行要旨は現在時点の状態を主語にしないと読者が「もう終わった話」と誤読するリスクがある。この状態情報を一行要旨の先頭に置く形で候補を作成した。

## 成果（README導線用の抽出）

### 一行要旨（README掲載用）

> 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard の移行は完了、`kiro-loop → agent-loop` の移行のみ未了で現行の指針であり続けている。

### 相対リンク（`docs/designs/README.md` に置く前提）

```markdown
[`agent-tools-rename-design.md`](./agent-tools-rename-design.md) — 旧 `kiro-*` 系統を `agent-*` へクローン移行・改称する方針と新旧名称対応表。agent-project/agent-flow/agent-dashboard の移行は完了、`kiro-loop → agent-loop` の移行のみ未了で現行の指針であり続けている。
```

`docs/designs/README.md` は実装リポジトリ側なので、相対パスは同一ディレクトリ内（`./agent-tools-rename-design.md`）でよい。

### 対象読者

- `kiro-*` / `agent-*` 系ツール（agent-project・agent-flow・agent-dashboard・agent-loop）のコード・設定パス・env変数名を触る実装者。
- 新規に `tools/agent-*` 配下へ変更を入れようとして、旧 `kiro-*` 側との対応関係が分からず迷っている人。
- `docs/designs/README.md` から辿ってきて「なぜ2系統（kiro-* と agent-*）が併存しているか」を知りたい人。

### 主要見出し（設計書本体の構成、全85行）

1. `# agent-* ツール改称（クローン方針）設計書`（冒頭メタ: 作成日2026-07-14、関連ディレクトリ一覧）
2. `## 1. 目的` — 4系統の新旧対応表（kiro-project→agent-project 等）。移行完了後に旧系統削除の方針。
3. `## 2. クローン方針（置換しない理由）` — 置換でなくクローンを選んだ理由（既存運用を壊さない、段階移行、設計書も複製）。
4. `## 3. 名称対応表（プログラム内）` — ディレクトリ名・パッケージ名・CLI・設定ファイル・env・状態ブランチ等の詳細な新旧対応表。維持するもの（`kiro-cli`、`kiro-loop`、共有env）も明記。
5. `## 4. 設計書の扱い` — 旧設計書→新設計書のファイル名対応表。
6. `## 5. インストール` — 新系統の install コマンド例。
7. `## 6. 非目標（この改称ではやらないこと）` — `kiro-loop` 移行は非目標、`kiro-cli` 改称もしない。

### 関連設計（相互参照、t1棚卸しの実測結果を継承）

- 他5設計から改称根拠として参照される: `agent-loop-adaptive-interval-design.md` / `agent-loop-agent-messaging-design.md` / `agent-loop-event-hook-design.md` / `agent-loop-gitlab-webhook-design.md`（いずれも冒頭で「kiro-loop-* をクローンし改称した」と自己申告）、`agent-dashboard-feature-split-design.md`、`agent-cli-plugin-design.md`。
- 主要4設計の中では唯一「他の主要3設計（agent-project / agent-flow / codd-gate）を本文から直接参照しない」— 参照される側であり、参照する側ではない。
- `docs/designs/README.md`（実装リポジトリ側に既存）は本ファイルを「まず読むもの」節と「ループ拡張」節の両方から参照しており、`kiro-loop → agent-loop` 未移行の注記の出典としても引用している。

## 検証内容と結果

- ファイル実在・行数（85行）: `wc -l` で確認。
- 見出し構成: `Read` で全文（85行）を実読し、6節構成であることを目視確認（推測なし）。
- 相互参照: t1棚卸し成果物（`artifacts/t1/inventory.md`）の実測結果を再利用し、`grep -rl "agent-tools-rename-design.md" docs/designs/` を自ら再実行して同じ6件がヒットすることを再確認した。
- 一行要旨・見出し要約は本文からの直接引用・言い換えのみで、本文にない情報は付加していない。

## 採用した前提・未解決事項・範囲外の所見

- **前提**: README導線の設置先は t1 が報告した通り実装リポジトリ `/Users/nitto/Workspace/sandbox/docs/designs/README.md` とした。本 worktree（`.agent-project`）には `docs/designs` 自体が存在しないため、リンク先パスの妥当性はこの前提に依存する。
- **未解決事項（本タスクの範囲外）**: 実装リポジトリ側の `docs/designs/README.md` には本ファイルへの導線が既に存在し、内容も本候補の一行要旨とほぼ同旨（移行完了/未了の状態を含む）である。README を新規作成するか既存を維持するかの判断は synth タスクに委ねる。
- **範囲外で見つけた事実**: 本 worktree で完了条件コマンド（`test -f docs/designs/README.md && grep ...`）をそのまま実行すると `docs/designs` が無いため失敗する。完了条件の実行対象リポジトリ（本worktree／実装リポジトリのどちらを正とするか）の解決は本タスク（抽出のみ）の範囲外。
