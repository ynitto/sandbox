---
status: proposed
date: 2026-07-22
decision-makers: [human]
task-id: dashboard-163827
kind: blocked
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","target":"main","branch":"ap/dashboard-163827","ref":"origin/ap/dashboard-163827","files":["tools/agent-dashboard/package.json","tools/agent-dashboard/src/features/agent-project/main/project.js","tools/agent-dashboard/src/renderer/renderer.js","tools/agent-dashboard/src/renderer/sections/needs.js","tools/agent-dashboard/src/renderer/sections/overview.js","tools/agent-dashboard/test/consistency-gate-ui.test.js","tools/agent-dashboard/test/consistency-gate.test.js","tools/agent-dashboard/test/needs-diagnosis.test.js","tools/agent-dashboard/test/needs-gate-integration.test.js","tools/agent-dashboard/test/overview-ui.test.js"],"files_total":10,"diff_cmd":"git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/dashboard-163827","mr_url":""}]
---

# 要対応: dashboard-163827 — dashboard で一貫性ゲートの状態把握と有効化を支援する

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=4）: ュー結果（3 観点）と反映

**判別しやすさ** — 見出しバッジ 3 値（`有効`/`一部のみ`/`未結線`）、行ごとの `結線済み`/`未結線` バッジ ＋ 設定値の常時表示（`regression_cmd` は汎用フックなので値を隠すと誤読）、判定と派生 `wired` は main 1 箇所。穴なし。

**有効化導線の実行可能性** — 未結線キーの行だけを出す、`<r
- synth [failed]: 実行エラー: claude 失敗 (rc=1)
You've hit your weekly limit · resets Jul 24 at 7am (Asia/Tokyo)
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `ap/dashboard-163827`（10 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/dashboard-163827`
- 変更ファイル（10 件）:
    - tools/agent-dashboard/package.json
    - tools/agent-dashboard/src/features/agent-project/main/project.js
    - tools/agent-dashboard/src/renderer/renderer.js
    - tools/agent-dashboard/src/renderer/sections/needs.js
    - tools/agent-dashboard/src/renderer/sections/overview.js
    - tools/agent-dashboard/test/consistency-gate-ui.test.js
    - tools/agent-dashboard/test/consistency-gate.test.js
    - tools/agent-dashboard/test/needs-diagnosis.test.js
    - tools/agent-dashboard/test/needs-gate-integration.test.js
    - tools/agent-dashboard/test/overview-ui.test.js
- 実行先: local
- 到達工程: act（実装）
- 検証: `grep -nE 'regression_cmd|intake_cmd|一貫性ゲート' tools/agent-dashboard/src/renderer/renderer.js tools/agent-dashboard/src/features/agent-project/main/project.js && node tools/agent-dashboard/test/needs-diagnosis.test.js && node tools/agent-dashboard/test/overview-ui.test.js` → 未実行（実行が検証まで到達しなかったため、テストの成否は分かっていません）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve dashboard-163827`。 -->
