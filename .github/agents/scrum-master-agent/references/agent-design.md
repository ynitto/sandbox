# scrum-master-agent 設計ドキュメント

## 設計方針

**エージェントは「確実な呼び出し側」に徹し、スキルに「何をするか」を委ねる。**

| 担当 | 内容 |
|---|---|
| このエージェント | フェーズ順守の強制・状態永続化・委譲の徹底 |
| scrum-master スキル | 各フェーズで何をするか・サブエージェントへのプロンプト内容 |

---

## 解決する失敗パターン

### 1. フェーズスキップ

**症状**: 明確なプロンプトで Phase 2/3 が省略される。
**対策**: `phase_gate.py pre N` がフェーズ番号の連続性を機械的に検証する。スキップは即座に FAIL。

### 2. 直接実行（委譲漏れ）

**症状**: Phase 5 でタスクを自分で実装し始める。
**対策**: `phase_gate.py delegation N` でフェーズ開始前に委譲チェックリストを表示。エージェント定義の委譲ルールで「直接実行禁止」を明示。

### 3. 状態喪失

**症状**: plan.json の読み書き漏れでフェーズ進行が崩壊する。
**対策**: `phase_runner.py` が plan.json を単一の真実の源として管理。`advance` はフェーズの連続性を強制（スキップ・後退を拒否）。

---

## ファイル構成

```
.github/agents/
├── scrum-master-agent.md          ← Copilot カスタムエージェント定義（システムプロンプト）
└── scrum-master-agent/
    ├── scripts/
    │   ├── phase_runner.py        ← フェーズ状態機械（plan.json 管理）
    │   └── phase_gate.py          ← ゲート条件バリデーター
    └── references/
        └── agent-design.md        ← この設計ドキュメント

# スキル（参照のみ・変更なし）
${SKILLS_DIR}/scrum-master/
├── SKILL.md                       ← 各フェーズの詳細手順（エージェントが読む）
├── references/phase-1〜7-*.md
├── references/subagent-templates.md
└── scripts/discover_skills.py, validate_plan.py
```

---

## エージェント vs スキルの役割分担

| 処理 | 担当 |
|---|---|
| フェーズを守って進む | エージェント（phase_runner.py / phase_gate.py） |
| 各フェーズで何をするか | scrum-master SKILL.md → phase-N-*.md |
| サブエージェントへのプロンプト | scrum-master/references/subagent-templates.md |
| タスク実行 | サブエージェント（`#tool:agent/runSubagent`） |
| plan.json のスキーマ | scrum-master/references/plan-schema.md |

---

## フェーズ実行フロー

```
起動
 ├─ phase_runner.py status        ← 現在フェーズ確認
 └─ scrum-master/SKILL.md を読む  ← 全体手順を把握

Phase N（1〜7 繰り返し）
 ├─ phase_gate.py pre N           ← スキップ検出・委譲チェックリスト表示
 ├─ phase-N-*.md を読む            ← スキルが「何をするか」を定義
 ├─ スキルの指示に従って実行       ← 委譲は runSubagent へ
 ├─ phase_gate.py post N          ← plan.json でゲート条件を検証
 │   PASS → phase_runner.py advance N+1
 │   FAIL → エラー解消して post を再実行
 └─ 繰り返し
```

---

## スクリプト設計

### phase_runner.py（状態機械）

| コマンド | 役割 |
|---|---|
| `status` | 現在フェーズ・進捗・ガードレール警告を表示 |
| `init [goal]` | plan.json を新規作成して Phase 1 から開始 |
| `advance N` | current_phase を N に更新（N == current+1 のみ許可） |
| `recover` | 欠損・破損した plan.json を自動修復 |
| `force-advance N` | ゲートを無視して強制進行（ユーザー指示時のみ） |
| `debug` | plan.json の全内容をダンプ |
| `retry skill\|validation` | リトライカウンターをインクリメントして上限チェック |

### phase_gate.py（バリデーター）

| コマンド | 役割 |
|---|---|
| `pre N` | Phase N 開始前：フェーズ番号の連続性・委譲チェックリストを表示 |
| `post N` | Phase N 完了後：plan.json の構造・内容を検証してゲート判定 |
| `delegation N` | Phase N で runSubagent に委譲すべき処理を表示（リマインダー） |
| `all` | 全フェーズのゲート状態サマリーを表示 |

---

## 設計判断の記録

### なぜスキルを修正しないのか

scrum-master スキル本体を修正すると後方互換性が壊れる。
エージェントを別ファイルとして定義することで、スキルは無変更のまま信頼性を追加できる。

### なぜシステムプロンプトをコンパクトにするのか

エージェント定義に詳細ロジックを書くと、スキルとの二重管理になる。
エージェントは「HOW（確実な呼び出し方）」だけを持ち、「WHAT（各フェーズの詳細）」はスキルに委ねる。
これにより scrum-master スキルが更新されても、エージェントは修正不要になる。

### force-advance の存在理由

外部システム障害など、ゲートが通らない正当な理由がある場合の脱出ハッチ。
`_force_advanced` フラグを plan.json に記録することで、強制進行の履歴を残す。

---

## 既知の限界

1. **Phase 5 の委譲検証**: `runSubagent` が実際に呼ばれたかをスクリプトで検証できない。
   plan.json の `result` フィールドの有無で間接的に確認するのみ。

2. **Phase 7 のゲート**: ユーザーの選択は対話的であり、plan.json に自動記録されない。
   `velocity.remaining` の整合性チェックで代替している。
