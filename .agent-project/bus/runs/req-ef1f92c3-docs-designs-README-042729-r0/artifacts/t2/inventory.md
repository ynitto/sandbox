# 棚卸し: 一貫性ゲート・ループ拡張カテゴリ

対象: `docs/designs/codd-gate-design.md`、`kiro-loop-*-design.md` / `agent-loop-*-design.md`（4組の同名重複）。
実在確認は `/Users/nitto/Workspace/sandbox/docs/designs/`（本 worktree に `docs/designs` が無いため、同一 git リポジトリの main worktree を参照読み専用で確認。採用前提は末尾に記載）。

## 一覧表

| ファイル名 | 要旨 | 対象読者 | 現行/歴史的 |
|---|---|---|---|
| `codd-gate-design.md` | ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で常時維持する決定的ツール codd-gate の唯一の設計正典。agent-project に依存しない独立ツールとしての位置づけと、`schemas/` 経由の疎結合を規定する。 | codd-gate / agent-project の実装者・CI導入担当 | 現行（最終更新2026-07-02、本文に「唯一の設計正典」と明記、最終コミット2026-07-15） |
| `kiro-loop-adaptive-interval-design.md` | kiro-loop の固定インターバル方式（`interval_minutes`/`cron`）が抱える「活発時の反応遅延」「無風時のAPI浪費」を解消する動的インターバル方式の設計案。 | `tools/kiro-loop` の実装者 | 現行（実装対象 `tools/kiro-loop/kiro-loop.py`、最終コミット2026-07-05） |
| `agent-loop-adaptive-interval-design.md` | 上記の複製。冒頭に「由来: `kiro-loop-adaptive-interval-design.md` を置換せずクローンし `agent-loop` 名称へ改称」と明記され、本文は用語置換のみで実質同一。 | `tools/agent-loop` の実装者（将来移行時の参照） | **改名残骸**（`agent-tools-rename-design.md` 由来のクローン。判定根拠は次節） |
| `kiro-loop-agent-messaging-design.md` | kiro-loop を使ったエージェント間非同期メッセージング設計。エージェントごとの inbox に他エージェントがメッセージを投函し、kiro-cli へのプロンプトとして処理する仕組み。 | `tools/kiro-loop` の実装者・複数エージェント運用設計者 | 現行（作成日2026-05-23、最終コミット2026-05-23） |
| `agent-loop-agent-messaging-design.md` | 上記の複製。冒頭に同様の「由来」注記あり、本文は用語置換のみ。 | `tools/agent-loop` の実装者（将来移行時の参照） | **改名残骸** |
| `kiro-loop-event-hook-design.md` | kiro-loop のイベントフック拡張（`check()` フック）設計案。実装メモとしてフォールバック機能・同梱フック例（GitLab issue/MR hook）の確定事項を追記済み。 | `tools/kiro-loop` の実装者 | 現行（作成日2026-05-12、実装メモ追記2026-06-02、最終コミット2026-06-02） |
| `agent-loop-event-hook-design.md` | 上記の複製。冒頭に同様の「由来」注記あり、環境変数名などが `AGENT_LOOP_*` に置換されている以外は同一内容。 | `tools/agent-loop` の実装者（将来移行時の参照） | **改名残骸** |
| `kiro-loop-gitlab-webhook-design.md` | kiro-loop 向け汎用 inbound Webhook 設計案（具体例GitLab）。`WebhookServer` 追加や `PeriodicScheduler` 拡張など、参照フォークへの実装済み確定事項を記載。 | `tools/kiro-loop` の実装者 | 現行（作成日2026-07-09、実装メモ追記2026-07-10、最終コミット2026-07-10） |
| `agent-loop-gitlab-webhook-design.md` | 上記の複製。冒頭に同様の「由来」注記あり、本文は用語置換のみ。 | `tools/agent-loop` の実装者（将来移行時の参照） | **改名残骸** |

## 4組の重複判定: 根拠

**判定: `kiro-loop-*-design.md` が現行、`agent-loop-*-design.md` が改名残骸。**

1. **各ファイルの自己申告**: `agent-loop-*-design.md` 4件はいずれも冒頭に
   `> **由来**: docs/designs/kiro-loop-*-design.md を置換せずクローンし、agent-loop 名称へ改称した系統。`
   という注記があり、自身が複製であることを明記している（逆方向の注記は `kiro-loop-*` 側には無い）。
2. **`agent-tools-rename-design.md` 本文との整合**: 同設計書は §3「維持するもの」で
   「（未移行の旧系統として残置する）`kiro-loop`」、§6「非目標」で「`kiro-loop` の移行・削除」を明記しており、
   **kiro-loop → agent-loop の実移行は今回のリネーム対象外**と設計書自身が宣言している。
3. **実装側の裏付け**: `tools/kiro-loop/kiro-loop.py`（142KB 単一ファイル）は2026-05-23〜07-10にかけて
   複数コミットで有機的に育った実装。対して `tools/agent-loop/agent_loop/*.py` は
   2026-07-14の単一コミット「`feat: clone kiro-loop as agent-loop and modularize after rename`」で
   機械的に生成されたもの（設計書4件の更新日もこの日に揃って一致）。
4. **運用面の裏付け**: 現在有効なスキルは `kiro-loop-messaging`（`.github/skills/`）であり、
   `agent-loop-messaging` は並存はするが常用スキルとしては未採用。

## README 向け注記文案（両方を可視化する）

synth タスクが `docs/designs/README.md` に kiro-loop-* / agent-loop-* を列挙する際、
**agent-loop-* だけを黙って外したり、kiro-loop-* だけを載せたりしない**こと。以下を注記として両者の並びの直前に挿入する案:

```markdown
> **kiro-loop 系 / agent-loop 系の重複について**
> `kiro-loop-*-design.md` と `agent-loop-*-design.md` は adaptive-interval / agent-messaging /
> event-hook / gitlab-webhook の4件で同名の設計が並存します。`agent-loop-*` は
> [`agent-tools-rename-design.md`](./agent-tools-rename-design.md) のクローン方針に沿って
> `kiro-loop-*` を複製し名称のみ改称したもの（各ファイル冒頭に由来を明記）。ただし同設計書は
> kiro-loop 自体の移行・削除を明示的に非目標としており、`tools/kiro-loop` を現行系統として
> 残置すると定めています。**現行は `kiro-loop-*-design.md`**、`agent-loop-*-design.md` は
> 将来 agent-loop へ本移行する際の未統合クローンとして参考に載せています。
```

続く一覧では両ファイルを並記し、`agent-loop-*`側に「(clone, 未統合)」等の短い接尾ラベルを付けることを推奨する。

## 検証

- 対象9ファイルすべて `ls -la /Users/nitto/Workspace/sandbox/docs/designs/` で実在確認済み。
- 4組の重複判定は (a) 各ファイル冒頭の「由来」注記の実読、(b) `agent-tools-rename-design.md` 全文の実読、
  (c) `tools/kiro-loop/` と `tools/agent-loop/` の実ファイル一覧・`git log` 突き合わせ、の3系統の証拠が一致することを確認した。
- `diff` で `agent-loop-adaptive-interval-design.md` を `kiro-loop`/`kiro_loop` 表記へ逆置換した結果、
  由来注記1ブロックを除き本文が完全一致することを確認済み（機械的クローンであることの直接証拠）。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 本 worktree（`agent-state` ブランチ、`.agent-project`）には `docs/designs` が存在しないため、
  同一 git リポジトリの main worktree（`/Users/nitto/Workspace/sandbox`）を参照読み専用で確認した（書き込み・commit・checkout は一切行っていない）。
- **前提**: 本タスクの担当は棚卸し表と注記文案の作成のみと解釈し、`docs/designs/README.md` 自体の作成・編集は行っていない（synth タスクの責務）。
- **未解決事項**: `codd-gate-design.md` は t1（エンジン中核カテゴリ）の棚卸し表にも同一ファイルとして計上されている（t1の関連ファイル記載に含まれる）。本タスクの goal が明示的に本ファイルを対象と指定しているため両カテゴリに重複計上した。どちらのカテゴリに一本化するかは gate タスクの判断に委ねる。
- **範囲外で見つけた問題**: `agent-loop-*-design.md` 4件のうち `agent-messaging` と `gitlab-webhook` の関連リンク（`関連設計`）は `agent-loop-event-hook-design.md` 等、複製先同士を相互参照しており、`kiro-loop-*` 側の相互参照とは独立したリンクグラフになっている。README で両系統を並記する際、リンク先の取り違えに注意が必要（本タスクの範囲外のため修正はしていない）。
