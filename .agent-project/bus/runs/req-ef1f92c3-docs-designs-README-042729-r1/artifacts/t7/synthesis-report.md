# synthesis report (t7)

## 対象ファイルの特定（矛盾の解消）

依存タスク（t2〜t5）はいずれも README の実配置先を「範囲外」として保留していたが、`[[agent-project-verify-location]]` の記録どおり、この run の完了条件は **`/Users/nitto/Workspace/sandbox`（main worktree）側の `docs/designs/README.md`** に対して評価される（r0 の `verify-command-log.txt` で `cd /Users/nitto/Workspace/sandbox && ...` を実行して確認済み）。`.agent-project` 側に存在する同名ファイルは sparse worktree 用の参照スタブであり、本 run のスコープ外として扱った。

同ファイルは既に主要4設計への相対リンク・一行要旨・「まず読むもの」節・カテゴリ別索引・読む順序の案内を備えた完成形が存在していた（t1〜t5 が各所で報告した「実装リポジトリ側に既に同旨の README がある」という所見と一致）。したがって本タスクは新規作成ではなく、**t6（verify）が指摘した2件の minor issue の是正**を実施した。

## 適用した修正

1. **codd-gate 一行要旨の是正**（t6 issue 1, minor）
   - Before: 「agent-project に依存しない独立ツールで、結合点は `schemas/` の共通データ契約のみ。」
   - After: 「agent-project 本体は無改造のまま、`schemas/` の共通データ契約と agent-project 側の汎用フック契約（E1〜E3）の2点で連携する独立ツール。」
   - 根拠: t4 の抽出および codd-gate-design.md §4「agent-project との結合点」が E1〜E3 フック契約を明記しており、「schemas のみ」は連携経路の記述として不完全だった。

2. **読む順序の明示**（t6 issue 2, minor）
   - 「まず読むもの」冒頭に一文を追加: 基本順序は 1→2→3（制御層→実行層→品質ゲート）、`kiro-*`/`agent-*` 併存に迷う読者は 4（`agent-tools-rename-design.md`）を先読み可、という案内。
   - 根拠: t5 が指摘した「移行状況を主語にしないと読者が誤読する」リスクと、t6 issue 2 の提案を統合。

## 検証

- `cd /Users/nitto/Workspace/sandbox && test -f docs/designs/README.md && grep -q 'agent-project-design.md' ... && grep -q 'agent-tools-rename-design.md'` → exit 0（完了条件を実際に再実行して確認）。
- 相対リンク解決チェック（r0 と同じ Python スクリプトを再実行）: `REL_LINKS 30, BROKEN []` — 修正による新規リンク破損なし。
- `grep -n '結合点は'` で旧い言い回しが他所に残っていないことを確認（1箇所のみで重複なし）。
- 本タスクで変更したのは `/Users/nitto/Workspace/sandbox/docs/designs/README.md` の該当2箇所のみ。`.agent-project` 側のスタブ・他の設計書ファイルには一切触れていない。

## 未解決事項（このrunの範囲外）

- `.agent-project/docs/designs/README.md`（未追跡ファイル）が sandbox 側と別内容で存在する。sparse worktree 用の参照スタブとして無害だが、実体は main worktree 側の1点に統一されているため、将来的にこのスタブの要否を整理してもよい。
- sandbox 側リポジトリの commit/push は `[[kiro-state-single-writer]]` の方針どおり、sandbox 自身の state-sync ループに委ね、本タスクからは行っていない（working tree の変更のみ）。
