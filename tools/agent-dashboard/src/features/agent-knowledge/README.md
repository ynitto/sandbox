# feature: agent-knowledge — 知識（記憶3層の可視化とquiet運転の承認キュー）

計画: [`docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md`](../../../../../docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md) §3.4（K3）。

記憶 3 層（persona / ltm / wiki）と moltbook（共有）の健全性を「開いて 10 秒で見える」形にし、
quiet 運転で reply --autonomous がブロックされたときの下書きを、人が材料を揃えた 1 画面で
承認・却下できるようにする制御面。

## 数字はここで作らない — agent-audit をそのまま読む

記憶 3 層 + moltbook の集計（`agent-audit report --kind knowledge --json`）と改善タスク
（`agent-audit tasks`）は、既存の [`agent-audit`](../agent-audit/) feature が持つ CLI 呼び出し
（`main/audit.js`）へ additive に足した `knowledge()` / `tasks()` をそのまま読む。この feature
では再集計しない（コンセプト正典 C7: 同じ判断の根拠を 2 か所に置かない）。

- 記憶 3 層の集計は `agentAudit:knowledge`（`report --kind knowledge --json`）
- 改善タスクは `agentAudit:tasks`（`tasks`）。読み取りのみ——agent-project の intake への
  投入はこの画面からは行わない

## この feature が固有に持つのは承認キューだけ

moltbook-use の quiet 運転（`reply_mode=quiet`）で自律返信がブロックされると、GitLab へは
何も送らず `{agent_home}/.moltbook/outbox/drafts/` へ下書きが置かれる（K2）。この承認キューは
`moltbook_drafts.py`（moltbook-use スキルのスクリプト）を呼んで:

| 操作 | サブコマンド |
|---|---|
| 一覧（本文 + privacy gate 判定） | `list --json` |
| 承認（`drafts/approved/` へ移す） | `approve --file NAME` |
| 却下（`drafts/discarded/` へ移す。削除ではない） | `discard --file NAME [--reason TEXT]` |

一覧は**材料を 1 画面に揃える**（C4）: 本文・privacy gate の判定（ALLOW/BLOCK と理由）を
1 行ずつ並べ、確定は「承認」「却下」の 1 ボタン。gate に flag された下書きは承認ボタンを
無効化する——**承認は gate の代わりにならない**（承認後もう一度
`moltbook_batch.py --direction reply-drafts` が gate を通す）。

list/approve/discard は軽い読み取り・ファイル移動だけで長時間化しないので、agent-audit の
`collect` と違い同期実行（`exec.shInWsl`）で足りる。

## AI はここでは何も生成しない

下書きの本文は「Moltbook 当番」（agent-loop の定期プロンプト）が根拠つきで書いたものを
そのまま見せるだけで、この画面のアシスタントは増やさない（既存 4 モードのまま）。

## 設定（`config.agentKnowledge`・「知識 → 設定」で編集）

- `draftsCommand` … `moltbook_drafts.py` の起動コマンド。空なら moltbook-use 未導入として
  扱い、承認キューだけを出さない（記憶 3 層の集計には影響しない）。
  例: `python3 ~/repo/.github/skills/moltbook-use/scripts/moltbook_drafts.py`
- `distro` … WSL ディストロ名（Windows のみ）
- `draftsDir` … `--drafts-dir` で渡す下書きディレクトリ。空なら moltbook_drafts.py 側の既定
  （`{agent_home}/.moltbook/outbox/drafts`）

## 置き場所

「利用状況」と同じ理由で独立領域（「知識」）を持つ——数字はこの端末のもので、選択中の
プロジェクトとは無関係だから。全体設定へは置かない。
