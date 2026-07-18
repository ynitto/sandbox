---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: codd-gate-163827
kind: blocked
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","target":"main","branch":"ap/codd-gate-163827","ref":"origin/ap/codd-gate-163827","files":["docs/designs/codd-gate-design.md","tools/agent-project/README.md"],"files_total":2,"diff_cmd":"git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/codd-gate-163827","mr_url":""}]
---

# 要対応: codd-gate-163827 — codd-gate 連携の目標境界を設計書に固定する

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos ./repos.json` 失敗 — exit=2 失敗した工程: `codd-gate verify --base 350d6121de099dc880cb7b0e138271d57451aa6e --repos ./repos.json` [codd-gate] エラー: スキャン可能な repo がありません（--repo-dir <name>=<dir> か --sync を指定）
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `ap/codd-gate-163827`（2 ファイル変更・target `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/codd-gate-163827`
- 変更ファイル（2 件）:
    - docs/designs/codd-gate-design.md
    - tools/agent-project/README.md
- 実行先: local
- 検証: `grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md && grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md && test -f docs/designs/codd-gate-design.md && grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md` → PASS

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve codd-gate-163827`。 -->
