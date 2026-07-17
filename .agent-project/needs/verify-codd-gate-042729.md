---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: verify-codd-gate-042729
kind: blocked
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","branch":"ap/verify-codd-gate-042729","ref":"","files":[],"files_total":0,"diff_cmd":"","mr_url":""}]
---

# 要対応: verify-codd-gate-042729 — verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --debt --sync --repos repos.json --max-broken 1563 --max-undocumented 354 --max-untested 444` 失敗 — exit=1 失敗した工程: `codd-gate verify --debt --sync --repos repos.json --max-broken 1563 --max-undocumented 354 --max-untested 444` ub/skills/agent-cli-proxy/SKILL.md 行291: references/windows-setup.md が解決できない
  - src:.github/skills/agent-flow/SKILL.md 行101: .agent/agent-flow.yaml が解決できない
  - src:.github/skills/agent-loop-messaging/SKILL.md 行259: ~/.kiro/agents が解決できない
  - src:.github/skills/agent-project/SKILL.md 行59: ~/.agent-project/instances が解決できない
NG: 壊れた参照 1646 件 > 許容 1563
NG: 未文書化 377 件 > 許容 354
NG: 未テスト 465 件 > 許容 444
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `ap/verify-codd-gate-042729`（ローカルで ref 未解決・差分取得不可）
- 所在: /Users/nitto/Workspace/sandbox
- 注: 作業ブランチの ref を解決できなかったためローカル差分は省略（MR があればそちらを確認）
- 実行先: local
- 検証: `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'` → PASS（exit=0）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve verify-codd-gate-042729`。 -->
