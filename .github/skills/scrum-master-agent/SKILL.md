---
name: scrum-master-agent
description: scrum-masterスキルを確実に実行するカスタムエージェント。フェーズスキップ・直接実行・状態喪失の3つの失敗パターンをスクリプト駆動で防ぐ。「確実にスクラムして」「フェーズを飛ばさずに進めて」「堅牢にタスク分解して」などで発動。通常のscrum-masterが不安定な場合のフォールバックとしても使用可能。
metadata:
  version: "1.0.0"
  extends: scrum-master
---

# scrum-master-agent

scrum-master の7フェーズを**スクリプト駆動の状態機械**で確実に実行するカスタムエージェント。
LLMの判断に頼る部分を最小化し、`phase_runner.py` と `phase_gate.py` でフェーズ遷移を機械的に制御する。

---

## このエージェントが解決する3つの失敗パターン

| 失敗パターン | 原因 | このエージェントの対策 |
|---|---|---|
| フェーズスキップ | LLMが「明確なプロンプト」と判断してPhase 2/3を省略 | `phase_gate.py` でゲート条件を機械的に検証 |
| 直接実行 | サブエージェント委譲すべき処理を自分で実行 | delegation_check を各フェーズ前に必須実行 |
| 状態喪失 | plan.json の読み書きが不完全でフェーズ進行が崩壊 | `phase_runner.py` が plan.json を単一の真実の源として管理 |

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、その親を `SKILLS_DIR` とする。

| このSKILL.mdのパス | SKILL_DIR | SKILLS_DIR |
|---|---|---|
| `~/.copilot/skills/scrum-master-agent/SKILL.md` | `~/.copilot/skills/scrum-master-agent` | `~/.copilot/skills` |
| `.github/skills/scrum-master-agent/SKILL.md` | `.github/skills/scrum-master-agent` | `.github/skills` |

- オリジナルの scrum-master 参照先: `${SKILLS_DIR}/scrum-master/`
- スクリプト: `${SKILL_DIR}/scripts/`

---

## 実行手順（鉄則）

### ステップ 0: フェーズランナー初期化

```bash
python ${SKILL_DIR}/scripts/phase_runner.py status
```

- plan.json が存在しない → `init` モードで Phase 1 から開始
- plan.json が存在する → 表示された `current_phase` から再開
- **この出力を必ず確認してから次に進むこと**

---

### ステップ 1〜7: フェーズ実行ループ

各フェーズは以下の **3アクション** で実行する:

```
[1] phase_gate.py で事前チェック
[2] scrum-master の対応フェーズ手順を実行（サブエージェント委譲）
[3] phase_runner.py で状態を更新してゲート条件を確認
```

#### フェーズ実行テンプレート

```bash
# 事前チェック（フェーズN開始前に必ず実行）
python ${SKILL_DIR}/scripts/phase_gate.py pre N

# ... フェーズNの処理（scrum-master/references/phase-N-*.md に従う） ...

# 事後チェック（次のフェーズに進む前に必ず実行）
python ${SKILL_DIR}/scripts/phase_gate.py post N
# → PASS なら advance
# → FAIL なら表示されたエラーを解消してから再実行

# フェーズ進行（post が PASS のときのみ実行可）
python ${SKILL_DIR}/scripts/phase_runner.py advance $((N+1))
```

---

### フェーズ別の委譲ルール（絶対に自分でやらない）

各フェーズで **自分でやること** と **サブエージェントに委譲すること** を明確に分ける。

| フェーズ | 自分でやること | 必ず委譲すること |
|---|---|---|
| Phase 1 | `discover_skills.py` 実行・LTM recall | なし |
| Phase 2 | 曖昧度判定・簡易インタビュー・plan.json 保存 | requirements-definer（曖昧な場合） |
| Phase 3 | スキルギャップ検出・ユーザー確認 | skill-creator / codebase-to-skill / skill-recruiter |
| Phase 4 | タスク選出・ウェーブ分割・ユーザー承認 | なし（ただし validate_plan.py は自分で実行） |
| Phase 5 | ウェーブ管理・結果集約 | **全タスク実行**（例外なし） |
| Phase 6 | フィードバック収集（ユーザーから） | sprint-reviewer / skill-evaluator / フィードバック記録 |
| Phase 7 | 進捗レポート出力・次アクション確認 | promote_memory（LTM） |

> **delegation_check**: Phase 5 でタスクを自分で実行しようとしていると気づいたら即座に止まり、
> `phase_gate.py delegation Phase5` を実行してサブエージェント起動に切り替えること。

---

### ガードレール（phase_runner.py が自動適用）

| 制限 | 値 | 超えた場合 |
|---|---|---|
| スキル作成リトライ | 最大2回 | ユーザーに相談してスキップ判断 |
| plan.json バリデーション | 最大3回 | ユーザーに提示して手動修正 |
| スプリント総数 | 最大5回 | ユーザー確認後のみ継続 |
| サブエージェント失敗リトライ | 最大1回 | リトライ→スキップ→中断の3択をユーザーに提示 |

---

## エラーリカバリー

```bash
# plan.json が破損・欠損している場合
python ${SKILL_DIR}/scripts/phase_runner.py recover

# 特定フェーズから強制再開（ユーザー指示がある場合のみ）
python ${SKILL_DIR}/scripts/phase_runner.py force-advance N

# フェーズ状態の完全表示（デバッグ用）
python ${SKILL_DIR}/scripts/phase_runner.py debug
```

---

## scrum-master との差分

このエージェントは scrum-master の **実行エンジンを強化したもの** であり、
フェーズ定義・テンプレート・スキーマはすべて scrum-master を参照する。

独自に持つもの:
- `scripts/phase_runner.py` — フェーズ状態機械
- `scripts/phase_gate.py` — ゲート条件バリデーター
- `references/agent-design.md` — 設計ドキュメント

scrum-master から継承するもの（ここには書かない）:
- `references/phase-*.md` → `${SKILLS_DIR}/scrum-master/references/phase-*.md`
- `references/subagent-templates.md` → `${SKILLS_DIR}/scrum-master/references/subagent-templates.md`
- `references/plan-schema.md` → `${SKILLS_DIR}/scrum-master/references/plan-schema.md`
- `scripts/discover_skills.py` → `${SKILLS_DIR}/scrum-master/scripts/discover_skills.py`
- `scripts/validate_plan.py` → `${SKILLS_DIR}/scrum-master/scripts/validate_plan.py`

---

## 動作環境

- **Claude Code** で動作（`Task` ツールでサブエージェント起動）
- スクリプト: `python`（環境によっては `python3`）
- パス区切り: `/`、文字コード: UTF-8 without BOM
