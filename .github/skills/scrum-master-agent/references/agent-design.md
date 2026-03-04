# scrum-master-agent 設計ドキュメント

## 概要

scrum-master スキルを確実に実行するためのカスタムエージェント設計。
**スクリプト駆動のフェーズ状態機械** によって、LLMの判断ミスに起因する3つの失敗パターンを防ぐ。

---

## 問題分析: scrum-master の信頼性の弱点

### 失敗パターン 1: フェーズスキップ

**症状**: 「REST APIにページネーションを実装して」のような一見明確なプロンプトで Phase 2/3 が省略される。

**根本原因**: LLMが「自分はゴールを理解している」と判断し、SKILL.md の「Phase 2/3 は省略不可」の指示を無視する。
この傾向は特にプロンプトが短く明確な場合に強く現れる。

**対策**: `phase_gate.py pre N` でフェーズ開始前に現在のフェーズを検証。
`phase_runner.py advance N` でフェーズ番号の連続性を強制（N+2へのジャンプを拒否）。

---

### 失敗パターン 2: 直接実行（委譲漏れ）

**症状**: Phase 5 でタスクを自分で実装し始める。Phase 3 でスキルのSKILL.mdを自分で書く。

**根本原因**: LLMは「やり方を知っている処理」を委譲せず自分で実行しようとする傾向がある。
サブエージェントを起動するコスト（プロンプト作成、待機）を避けるための近道思考。

**対策**: 各フェーズ開始時に `phase_gate.py delegation N` でチェックリストを表示。
SKILL.md の「委譲ルール」テーブルを明示的かつ簡潔に記述。

---

### 失敗パターン 3: 状態喪失

**症状**: plan.json が途中で不完全な状態になり、後続フェーズで「plan.jsonが見つからない」「backlogが空」などのエラーが発生。
または plan.json を読まずに記憶（コンテキスト）だけで進行してコンテキストが失われると崩壊。

**根本原因**: plan.json の読み書きが各フェーズの「手順」に分散しており、どこかで抜けると状態が不整合になる。
LLMのコンテキスト窓の消費によって初期の plan.json の内容を「忘れる」こともある。

**対策**: `phase_runner.py` が plan.json の単一の真実の源として機能。
フェーズ進行は必ず `phase_runner.py advance N` 経由で行い、現在フェーズを plan.json に永続化。
`recover` コマンドで破損状態から自動回復。

---

## アーキテクチャ

```
scrum-master-agent/
├── SKILL.md                    # スキル定義（エントリポイント）
├── scripts/
│   ├── phase_runner.py         # フェーズ状態機械（plan.json の管理）
│   └── phase_gate.py           # ゲート条件バリデーター
└── references/
    └── agent-design.md         # この設計ドキュメント

# 継承（読み取り専用）
${SKILLS_DIR}/scrum-master/
├── references/phase-1〜7-*.md  # フェーズ別詳細手順
├── references/subagent-templates.md
├── references/plan-schema.md
└── scripts/
    ├── discover_skills.py
    └── validate_plan.py
```

---

## フェーズ実行フロー（詳細）

```
┌─────────────────────────────────────────────────────────────┐
│ phase_runner.py status                                       │
│  → plan.json の有無と current_phase を確認                   │
└────────────────────────┬────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │ plan.json 存在?      │
              └──┬─────────────┬───┘
                 │ なし         │ あり
                 ▼             ▼
          phase_runner.py   current_phase
          init [goal]       から再開
                 │
                 ▼
    ┌────────────────────────────────────┐
    │ LOOP: Phase N (N = 1..7)           │
    │                                    │
    │  1. phase_gate.py pre N            │ ← フェーズスキップ検出
    │     → FAIL なら停止                │
    │                                    │
    │  2. scrum-master phase-N-*.md 実行 │ ← 実際の処理
    │     （委譲が必要なものはサブエージェント）│
    │                                    │
    │  3. phase_gate.py post N           │ ← ゲート条件検証
    │     → FAIL なら修正して再実行      │
    │                                    │
    │  4. phase_runner.py advance N+1    │ ← 状態を永続化
    │                                    │
    └───────────────┬────────────────────┘
                    │ Phase 7 完了
                    ▼
             最終レポート出力
```

---

## スクリプト設計の詳細

### phase_runner.py

**責務**: plan.json の読み書きとフェーズ遷移の制御

| コマンド | 動作 | 失敗パターン対策 |
|---|---|---|
| `status` | 現在フェーズ・ゴール・進捗を表示 | 状態喪失の早期発見 |
| `advance N` | current_phase を N に更新（連続性強制） | フェーズスキップ防止 |
| `init [goal]` | plan.json を新規作成して Phase 1 から開始 | 状態初期化 |
| `recover` | plan.json の欠損フィールドを自動修復 | 状態喪失からの回復 |
| `force-advance N` | ゲートを無視して強制進行（ユーザー指示時のみ） | 脱出ハッチ |
| `debug` | plan.json の全内容をダンプ | デバッグ |
| `retry skill\|validation` | リトライカウンターのインクリメントと上限チェック | ガードレール |

**フェーズ遷移バリデーション**:
- `advance N` は `N == current_phase + 1` のみ受け入れる
- 後退（`N < current_phase`）は拒否（`force-advance` を使用）
- スキップ（`N > current_phase + 1`）は拒否

### phase_gate.py

**責務**: フェーズ開始前・完了後のゲート条件検証

| コマンド | 動作 |
|---|---|
| `pre N` | Phase N 開始前に前提条件（plan.json の存在・フェーズ番号の連続性）を検証 |
| `post N` | Phase N 完了後にゲート条件（plan.json の内容）を検証 |
| `delegation N` | Phase N の委譲チェックリストを表示（リマインダー） |
| `all` | 全フェーズのゲート状態をサマリー表示 |

**ゲート条件の実装方針**:
- `post` チェックは plan.json の **構造と内容** を検証する
- LLMが「実行した」と主張しても、plan.json に証拠がなければ FAIL
- Phase 7 のような対話的フェーズは plan.json で完全に検証できないため、velocity の整合性のみチェック

---

## 設計判断の記録

### なぜスクリプトを「追加」するのか（scrum-master を修正しないのか）

scrum-master スキル本体を修正すると:
- 既存の scrum-master ユーザーへの影響が生まれる
- バージョン管理が複雑になる
- scrum-master の「シンプルな指示セット」という設計思想が壊れる

`scrum-master-agent` を別スキルとして作成することで:
- scrum-master は変更なし（後方互換性を保つ）
- 信頼性が必要なケースだけ `scrum-master-agent` を使用
- スクリプトによる検証層を独立して改善できる

### なぜ plan.json を状態の真実の源とするのか

LLMのコンテキストウィンドウは有限であり、長いスプリントでは初期の状態が「忘れられる」。
plan.json にフェーズ状態を永続化することで:
- コンテキストが失われても `phase_runner.py status` で再開できる
- 各フェーズの完了証拠がファイルとして残る
- 人間がデバッグ・介入できる

### なぜ force-advance を提供するのか

ゲートを完全に自動化すると、エッジケース（外部システムの失敗など）で詰まる可能性がある。
`force-advance` は「脱出ハッチ」として残すが、ユーザー指示時のみという制約を SKILL.md と plan.json の `_force_advanced` フラグで明示する。

---

## 既知の限界

1. **Phase 5 の委譲検証**: サブエージェントが実際に起動されたかをスクリプトで検証できない。
   plan.json の `result` フィールドが埋まっているかで間接的に確認するのみ。

2. **Phase 7 のゲート条件**: ユーザーの選択は対話的であり、plan.json に自動記録されない。
   スクラムマスターが手動で `plan.json` の velocity を更新する必要がある。

3. **サブエージェントの失敗**: サブエージェントが返す結果の形式が SKILL.md のテンプレートと異なる場合、
   `phase_gate.py post 5` が誤判定することがある。

---

## 将来の改善案

- **Phase 5 の委譲ログ**: サブエージェント起動の記録を plan.json の `_subagent_log` に保存
- **自動リカバリー**: `recover` コマンドで部分的に完了したフェーズを検出して再開
- **CI/CD 統合**: plan.json を git にコミットしてスプリント履歴を管理
