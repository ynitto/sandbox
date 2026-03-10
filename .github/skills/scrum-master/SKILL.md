---
name: scrum-master
description: ユーザーのプロンプトをタスク分解し、サブエージェントにスキルを委譲してスプリント単位で実行するオーケストレーター。「スクラムして」「スプリントで進めて」「タスク分解して実行して」「チームで開発して」「バックログを作って進めて」「要件整理してから開発して」「何を作るか一緒に考えて」などで発動。
metadata:
  version: 1.4.0
  tier: core
  category: orchestration
  tags:
    - sprint
    - task-decomposition
    - delegation
---

# scrum-master

ユーザーのプロンプトをバックログに分解し、スプリント単位でサブエージェントに委譲して実行するオーケストレーター。

---

## 鉄則（この3つを絶対に破るな）

### 鉄則 1: フェーズ順守 — 飛ばすな

Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 を **この順番で必ず実行する**。

- Phase 2 と Phase 3 は **省略不可**。プロンプトが明確でも飛ばしてはならない
- `plan.json` なしで Phase 3 以降に進むことは **禁止**
- 各フェーズ開始時に `=== PHASE [N]: [フェーズ名] 開始 ===` を出力すること

### 鉄則 2: サブエージェント委譲 — 自分でやるな

**⚠️ アクションを起こす前に毎回この問いに答えよ:**

> 「今から自分が実行しようとしていることは、タスク実行・スキル実行・要件定義・レビュー・評価のいずれかか？」
>
> - **YES** → **即座に `runSubagent` を呼び出せ。自分で処理を続けてはならない。**
> - **NO** (ファイル確認・plan.json 更新・スクリプト実行・ユーザー報告など) → 直接実行してよい

**委譲必須アクション（具体例）**:

| ❌ 自分でやってはいけない | 代わりに委譲先 |
|---|---|
| 要件を定義する・整理する | requirements-definer |
| スキルファイルを作成・編集する | skill-creator / codebase-to-skill |
| ドキュメント・README を書く | Phase 5 サブエージェント（各スキル） |
| コードを書く・ファイルを実装する | Phase 5 サブエージェント（各スキル） |
| レビューを行う | Phase 5 サブエージェント（レビュースキル） |
| テストを実行・修正する | Phase 5 サブエージェント（テストスキル） |
| スプリントレビューを実施する | sprint-reviewer |
| スキル評価・フィードバック収集を実行する | skill-evaluator |

**直接実行してよいアクション（具体例）**:
- `plan.json` / `requirements.json` の読み書き・更新
- `discover_skills.py` / `validate_plan.py` などスクリプトの実行
- ファイルの存在確認・内容確認
- ユーザーへの報告・選択肢の提示・承認待ち
- ウェーブ完了後の進捗チェック出力

### 鉄則 3: サブエージェント起動方法

| 環境 | 起動方法 |
|------|---------|
| **GitHub Copilot (VSCode)** | `#tool:agent/runSubagent` を使用。自分で処理を続けてはならない |
| **Claude Code** | `Task` ツール（`subagent_type: "general-purpose"`）を使用 |

- プロンプトテンプレート: [references/subagent-templates.md](references/subagent-templates.md) を参照
- SKILL.md の内容をプロンプトに埋め込むな。ファイルパスだけ渡せ
- 並列実行: 複数のサブエージェント起動を単一メッセージに並べる

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、その親ディレクトリを `SKILLS_DIR` とする。

| このSKILL.mdのパス | SKILL_DIR | SKILLS_DIR |
|---|---|---|
| `~/.copilot/skills/scrum-master/SKILL.md` | `~/.copilot/skills/scrum-master` | `~/.copilot/skills` |
| `.github/skills/scrum-master/SKILL.md` | `.github/skills/scrum-master` | `.github/skills` |

- スクリプトは `${SKILL_DIR}/scripts/` から実行する
- 他スキルのSKILL.mdは `${SKILLS_DIR}/[skill-name]/SKILL.md` で解決する
- 該当スキルが `SKILLS_DIR` に存在しない場合は、もう一方の場所（`~/.copilot/skills/` または `.github/skills/`）を確認する

---

## フェーズ実行手順

**各フェーズは以下の手順で実行する**:
1. `=== PHASE [N] 開始 ===` を出力する
2. 対応する `references/phase-N-*.md` を **読んでその手順に従う**
3. ゲート条件をクリアしてから次のフェーズへ進む

| # | フェーズ | やること | ゲート条件（次へ進む前に満たすこと） | 詳細手順 |
|---|----------|---------|--------------------------------------|----------|
| 1 | スキル探索 | `discover_skills.py` を実行しスキル一覧を取得 | スキル一覧JSON取得済み | [phase-1](references/phase-1-skill-discovery.md) |
| 2 | バックログ作成 | 曖昧度判定 → 要件定義（委譲）→ プロダクトゴール/DoD設定 → `plan.json` 生成 | `plan.json` がルートに保存済み（`product_goal`・`definition_of_done` 含む） | [phase-2](references/phase-2-backlog.md) |
| 3 | スキルギャップ解決 | スキル不足・改良を検出し解消（委譲） | スキルギャップなし。`current_phase` = 3 | [phase-3](references/phase-3-skill-gap.md) |
| 4 | スプリントプランニング | タスク選出 → ウェーブ分割 → ユーザー承認 | ユーザーがスプリントプランを承認済み | [phase-4](references/phase-4-sprint-planning.md) |
| 5 | タスク実行 | ウェーブ単位でサブエージェント並列起動（委譲） | 全ウェーブ実行完了（または中断選択） | [phase-5](references/phase-5-task-execution.md) |
| 6 | スプリントレビュー | インクリメント確認（DoD）→ レビュー（委譲）→ フィードバック・スキル評価 | インクリメント確認・レビュー・フィードバック収集完了 | [phase-6](references/phase-6-sprint-review.md) |
| 7 | 進捗レポート | ユーザーに報告し次アクションを確認 | ユーザーが選択肢を選択済み | [phase-7](references/phase-7-progress-report.md) |

**ガードレール**:
- スキル作成リトライ: Phase 3 内で最大2回
- バリデーション: Phase 4 で最大3回
- スプリント総数: 最大5回

plan.json のスキーマ詳細 → [references/plan-schema.md](references/plan-schema.md)

**Phase 7 選択肢別の遷移先**:
- 「次スプリント」→ スキル作成があった場合は Phase 1 → Phase 4、なければ直接 Phase 4
- 「バックログ見直し」→ Phase 4
- 「完了」→ 最終レポート出力して終了

---

## エラーハンドリング

| 状況 | 対応 |
|------|------|
| discover_skills.py 失敗 | `${SKILLS_DIR}/` の存在確認。なければ作成提案 |
| validate_plan.py 失敗 | エラーに従い修正。最大3回で超えたらユーザーに相談 |
| サブエージェント失敗 | リトライ / スキップ / 中断をユーザーに提示 |
| 全タスク失敗 | ゴール実現可能性をユーザーと再検討 |

## 記憶連携（ltm-use コアスキル）

`ltm-use` はコアスキルとして常に利用可能。以下のフックを**必ず実行**して重複調査・決定を防ぎ知見を蓄積する。

`LTM=${SKILLS_DIR}/ltm-use/scripts`

### フック定義

| フェーズ | タイミング | 操作 | 対象 |
|---------|-----------|------|------|
| Phase 1 | スキル探索完了後 | **recall** | 過去の類似スプリントの知見 |
| Phase 2 | バックログ作成前 | **recall** | 過去の決定・調査結果 |
| Phase 5 | サブエージェント起動時 | **recall + save**（テンプレートに組み込み済み） | 実装知見 |
| Phase 6 | レビュー完了後 | **save + rate** | レトロスペクティブの学び |
| Phase 7 | 完了選択時 | **promote** | 価値ある知見をホームへ昇格 |

### recall（Phase 1, 2）

```bash
python ${LTM}/recall_memory.py "[タスクキーワード]"
# 0件なら ~/.copilot/memory/home/ → ~/.copilot/memory/shared/ を自動検索
```

- 0件 → 記憶なし、通常通り続行
- 1件以上 → summary 確認 → 関連があれば全文読み込み → 計画に反映

### save（Phase 6）

```bash
python ${LTM}/save_memory.py \
  --category [カテゴリ] --title "[タイトル]" --summary "[要約]" \
  --content "[詳細]" --conclusion "[学び]" --tags [タグ]
```

### promote（Phase 7 完了時）

```bash
python ${LTM}/promote_memory.py --list   # 昇格候補確認
python ${LTM}/promote_memory.py --auto   # share_score >= 85 を自動昇格
```

---

## 動作環境

- **GitHub Copilot Chat** / **Claude Code** で動作
- **scrum-master-agent** エージェント経由での起動にも対応（フェーズ順守・委譲を機械的に強制するラッパー）
- スクリプト: `python`（環境によっては `python3`）
- パス区切り: `/`、文字コード: UTF-8 without BOM
