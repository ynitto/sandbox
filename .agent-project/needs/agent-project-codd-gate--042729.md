---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: agent-project-codd-gate--042729
kind: blocked
delivery: [{"name":"sandbox","role":"reference","url":"https://github.com/ynitto/sandbox","path":"","base":"main","branch":"","ref":"","files":[],"files_total":0,"diff_cmd":"","mr_url":""}]
---

# 要対応: agent-project-codd-gate--042729 — agent-projectにcodd-gate自動検出とregression/intake結線を完成させ連携を有効化する

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --debt --sync --repos repos.json --max-broken 1563 --max-undocumented 354 --max-untested 444` 失敗 — exit=1 失敗した工程: `codd-gate verify --debt --sync --repos repos.json --max-broken 1563 --max-undocumented 354 --max-untested 444` ub/skills/agent-cli-proxy/SKILL.md 行291: references/windows-setup.md が解決できない
  - src:.github/skills/agent-flow/SKILL.md 行101: .agent/agent-flow.yaml が解決できない
  - src:.github/skills/agent-loop-messaging/SKILL.md 行259: ~/.kiro/agents が解決できない
  - src:.github/skills/agent-project/SKILL.md 行59: ~/.agent-project/instances が解決できない
NG: 壊れた参照 1630 件 > 許容 1563
NG: 未文書化 374 件 > 許容 354
NG: 未テスト 462 件 > 許容 444
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: commit ef1f92c3
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: 1 ファイル
    - .agent-project/backlog/agent-project-codd-gate--042729.md
- 検証: `echo "ok"` → PASS（exit=0 ok）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve agent-project-codd-gate--042729`。 -->
