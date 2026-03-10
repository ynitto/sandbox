# ワークスペーススキル 改善案 v2 — 新規発見事項

> **作成日**: 2026-03-10  
> **対象**: `.github/skills/` 配下の 35 スキル  
> **前提**: `feature-proposals.md`、`2026-03-08-skill-ideas-design.md`、`2026-03-10-skill-improvement-proposals.md` で既出の改善案は除外

---

## 0. サマリ

既存レポートでは、依存管理・レビュースキル境界・React 偏重・バージョニング・新規スキル案・コンポジション等が提案済み。本レポートでは **既出案にない観点** に絞り、14 項目を提案する（うち 6 件実装済み ✅）。

| # | カテゴリ | 改善案 | 優先度 |
|---|---------|-------|--------|
| 1 | ガバナンス | ✅ スキル成熟度ティアシステムの導入 | 高 |
| 2 | ガバナンス | 非推奨化（Deprecation）ライフサイクルの定義 | 高 |
| 3 | メタデータ | ✅ YAML フロントマターへの category / tags 追加 | 高 |
| 4 | メタデータ | ライセンス・作者（provenance）メタデータの整備 | 中 |
| 5 | 入出力 | ✅ レビュー結果の横断出力フォーマット標準化 | 高 |
| 6 | 入出力 | スキル間 I/O コントラクトの形式化 | 高 |
| 7 | 入出力 | 共有リファレンス・共通テンプレートの導入 | 中 |
| 8 | 品質保証 | スキル間統合テストフレームワーク | 高 |
| 9 | 品質保証 | ✅ クロスリファレンス整合性検証 | 中 |
| 10 | 実行時 | ✅ プロセス系スキルへのスコーピングゲート追加 | 中 |
| 11 | 実行時 | エラーリカバリ・フォールバックパターンの標準化 | 中 |
| 12 | 実行時 | セッション起動シーケンスの最適化 | 低 |
| 13 | 発見性 | ✅ skill-selector の実体化と機械可読カタログ | 高 |
| 14 | 国際化 | SKILL.md 自体の多言語対応 | 低 |

---

## 1. ✅ スキル成熟度ティアシステムの導入 — **優先度: 高**

### 現状

v1.0.0 が大半を占め、production-ready な `scrum-master` (v1.4.0) と新設スキルが同列に並んでいる。ユーザーは品質レベルを判断できない。

### 改善案

YAML フロントマターに `tier` フィールドを追加:

```yaml
metadata:
  tier: core          # core | stable | experimental | draft | deprecated
```

| ティア | 条件 | 例 |
|--------|------|-----|
| `core` | オーケストレーターが依存。削除不可 | scrum-master, ltm-use, git-skill-manager |
| `stable` | ok 率 80%+、v1.0.0 以上、references/scripts 整備 | code-reviewer, patent-writer, react-frontend-coder |
| `experimental` | 動作するが ok 率未達 or テスト不十分 | dynamodb-designer, deep-research |
| `draft` | SKILL.md のみ or スタブ | algorithmic-art, canvas-design |
| `deprecated` | 廃止予定。代替スキルへの移行を推奨 | （現時点では該当なし） |

**skill-evaluator** の `quality_check.py` でティア判定を自動化し、昇格条件を満たしたスキルを通知する。

---

## 2. 非推奨化（Deprecation）ライフサイクルの定義 — **優先度: 高**

### 現状

- `security-auditor` → `security-reviewer` へ名称変更されたが、旧名で参照する文書が残存
- スキル削除・統合のプロセスが未定義

### 改善案

```
Active → Deprecated（notice 期間: 2 スプリント）→ Archived（references/ に移動）→ Removed
```

| ステージ | 対応 |
|---------|------|
| Deprecated 宣言 | SKILL.md に `tier: deprecated` + `deprecated_by: <代替スキル>` を追加 |
| Notice 期間 | skill-selector / scrum-master が「非推奨です。代わりに X を使用してください」と表示 |
| Archived | `.github/skills/_archived/<skill-name>/` へ移動。registry.json で追跡 |
| Removed | ディレクトリ削除。CHANGELOG にのみ痕跡を残す |

---

## 3. ✅ YAML フロントマターへの category / tags 追加 — **優先度: 高**

### 現状

2026-03-10 レポートの冒頭で 8 カテゴリに分類されているが、その情報は SKILL.md のフロントマターにはなく、人間が手動で維持している。skill-selector の不在と相まって、スキル検索が LLM の推論に完全依存している。

### 改善案

```yaml
metadata:
  category: review        # orchestration | meta | design | implementation | review | debug | research | data
  tags:
    - security
    - owasp
    - code-quality
```

**効果**:
- `skill-selector` やスキル検索の精度向上（タグでフィルタ可能）
- `git-skill-manager` の `--list` 出力でカテゴリ別一覧を表示
- `skill-evaluator` がカテゴリ別のカバレッジギャップを検出可能

---

## 4. ライセンス・作者（provenance）メタデータの整備 — **優先度: 中**

### 現状

| フィールド | 記載率 | 該当 |
|-----------|--------|------|
| `license` | 3/39 (8%) | react-best-practices, webapp-testing, doc-coauthoring |
| `author` | 1/39 (3%) | react-best-practices のみ |
| `source` | 2/39 (5%) | doc-coauthoring, react-best-practices |

`skill-recruiter` で外部取得したスキルの出自を追跡できない。マーケットプレイス構想（feature-proposals §2.7）の前提条件が欠落。

### 改善案

```yaml
metadata:
  author: "team-name"
  license: "MIT"
  source: "https://github.com/org/repo"  # 外部取得元（ローカル作成の場合は省略可）
```

- `skill-recruiter` が外部スキル取得時に自動でフィールドを埋める
- `skill-evaluator` が `license` 未記載を WARN として検出

---

## 5. ✅ レビュー結果の横断出力フォーマット標準化 — **優先度: 高**

### 現状

レビュー系 7 スキルは全て「LGTM / Request Changes」を出力するが、出力構造がスキルごとに異なる:

| スキル | 判定 | 出力フォーマット |
|--------|------|----------------|
| code-reviewer | LGTM / Request Changes | 11 次元の Markdown レポート |
| security-reviewer | PASS / FAIL | OWASP カテゴリ別テーブル |
| architecture-reviewer | LGTM / Request Changes | 6 軸評価 Markdown |
| design-reviewer | LGTM / Request Changes | SOLID 準拠テーブル |
| test-reviewer | LGTM / Request Changes | 8 観点チェックリスト |
| document-reviewer | Approved / Needs Revision | 5 観点の Markdown |
| sprint-reviewer | Sprint Passed / Failed | レトロスペクティブ形式 |

`sprint-reviewer` がレビュー結果を集約する際、各スキルの出力を手作業で解釈する必要がある。

### 改善案

共通レビュー出力スキーマを定義:

```json
{
  "skill": "code-reviewer",
  "version": "2.0.0",
  "verdict": "request_changes",
  "confidence": 0.85,
  "findings": [
    {
      "id": "CR-001",
      "severity": "high",
      "category": "security",
      "file": "src/auth.ts",
      "line": 42,
      "message": "SQL injection risk in user input handling",
      "suggestion": "Use parameterized queries"
    }
  ],
  "summary": "セキュリティ上の問題 1 件、パフォーマンス改善提案 2 件"
}
```

- 全レビュースキルに `--json` 出力モードを追加
- `sprint-reviewer` が JSON を自動パースして集計
- `scrum-master` Phase 6 で統一レポートを生成

---

## 6. スキル間 I/O コントラクトの形式化 — **優先度: 高**

### 現状

スキルの入出力が明確に定義されている度合いにばらつきがある:

| 明確度 | スキル | 入力 | 出力 |
|--------|--------|------|------|
| ★★★ | requirements-definer | 自由テキスト | `requirements.json`（F-/N-/P- スキーマ） |
| ★★★ | api-designer | 要件 + ドメインモデル | OpenAPI YAML |
| ★★★ | patent-writer | ヒアリングシート 8 項目 | JPO 様式 Markdown |
| ★☆☆ | brainstorming | 「アイデア」 | 「実装計画を作成する」（具体的なフォーマット不明） |
| ★☆☆ | deep-research | 「テーマ」 | 「引用付きで文書化」（構造未定義） |
| ★☆☆ | technical-writer | 「ドキュメント種別」 | 「5つのコア原則に従った文書」（スケルトン未定義） |

### 影響

- スキル連携時（例: brainstorming → requirements-definer）に出力→入力の受け渡しが不安定
- composite スキル（既提案）を実装する際、I/O の型が不明だとパイプラインが成立しない

### 改善案

SKILL.md に `io_contract` セクションを標準化:

```yaml
metadata:
  io_contract:
    input:
      - name: requirements
        format: json
        schema: "requirements.json (F-/N-/P- prefix)"
        required: true
      - name: domain_model
        format: markdown
        schema: "Mermaid classDiagram"
        required: false
    output:
      - name: api_spec
        format: yaml
        schema: "OpenAPI 3.x"
```

- `skill-evaluator` が I/O コントラクト未定義スキルを WARN として検出
- composite スキルの steps 定義時に、出力→入力の型互換性を自動検証

---

## 7. 共有リファレンス・共通テンプレートの導入 — **優先度: 中**

### 現状

11 スキルが独自の `references/` を持つが、スキル横断で共通利用できるパターンが重複管理されている:

- **コーディング規約**: code-reviewer と code-simplifier が別々に判断基準を持つ
- **Mermaid 記法**: domain-modeler、api-designer、git-skill-manager がそれぞれ独自に Mermaid 出力仕様を記述
- **LGTM/Request Changes テンプレート**: 6 つのレビュースキルが別々に判定フォーマットを持つ

### 改善案

`.github/skills/_shared/` ディレクトリを導入:

```
.github/skills/_shared/
  review-output-schema.json     # レビュースキル共通出力スキーマ
  mermaid-conventions.md        # Mermaid 記法の共通規約
  coding-standards-base.md      # 基本コーディング規約（スキルが extends する）
  severity-levels.md            # 重要度レベルの共通定義（Critical/High/Medium/Low）
```

- 各スキルは `_shared/` を参照し、スキル固有のオーバーライドのみをローカルに持つ
- `_` プレフィックスにより `git-skill-manager` がスキルとして誤認しない

---

## 8. スキル間統合テストフレームワーク — **優先度: 高**

### 現状

- `skill-evaluator` の `quality_check.py` は SKILL.md の構造検証のみ
- `requirements-definer` の `validate_requirements.py` はスキーマ検証のみ
- **スキル間連携の整合性テスト**（出力→入力パイプラインの検証）は存在しない

### 例: 検証されていない連携

| 上流 | 出力 | 下流 | 期待入力 | 検証状態 |
|------|------|------|---------|---------|
| requirements-definer | `requirements.json` | api-designer | 要件リスト | ❌ 未検証 |
| api-designer | OpenAPI YAML | react-frontend-coder | API 仕様 | ❌ 未検証 |
| brainstorming | Decision Record | ltm-use | save 可能な構造 | ❌ 未検証 |
| code-reviewer | レビュー結果 | sprint-reviewer | 集約可能な形式 | ❌ 未検証 |

### 改善案

`.github/skills/_tests/` に統合テストスイートを配置:

```python
# _tests/test_pipeline_contracts.py
def test_requirements_to_api_designer():
    """requirements-definer の出力が api-designer の入力として有効か"""
    sample_output = load_fixture("requirements-definer/output.json")
    assert validate_api_designer_input(sample_output)

def test_review_results_aggregation():
    """全レビュースキルの出力が sprint-reviewer で集約可能か"""
    for reviewer in REVIEW_SKILLS:
        sample = load_fixture(f"{reviewer}/output.json")
        assert validate_review_schema(sample)
```

- CI（`git-skill-manager` の `--health` 拡張）で自動実行
- スキル更新時にパイプライン破壊を検出

---

## 9. ✅ クロスリファレンス整合性検証 — **優先度: 中**

### 現状

スキル本文中で他スキルを名前で参照しているが、参照先の存在を検証する仕組みがない。

**検出された不整合の例**:

| 参照元 | 参照先 | 状態 |
|--------|--------|------|
| scrum-master (Phase 1) | `skill-selector` | ❌ ディレクトリ不在 |
| 2026-03-10 レポート概要 | `skill-selector (版なし)` | ❌ 実体なし |
| code-reviewer | `security-reviewer` への委譲 | ✅ 存在 |

### 改善案

`skill-evaluator` に参照整合性チェックを追加:

```python
# quality_check.py に追加
def check_cross_references(skill_md: str, all_skill_names: set) -> list:
    """SKILL.md 内で言及されている他スキル名が実在するか検証"""
    mentioned = extract_skill_references(skill_md)
    return [ref for ref in mentioned if ref not in all_skill_names]
```

- `XREF_BROKEN` (WARN): 参照先スキルが存在しない
- `XREF_DEPRECATED` (INFO): 参照先スキルが deprecated

---

## 10. ✅ プロセス系スキルへのスコーピングゲート追加 — **優先度: 中**

### 現状

レビュー系スキルは Step 0 で「対象外の場合は中断」を定義しているが、プロセス系スキルには明示的な事前条件・中断条件がない:

| スキル | スコーピングゲート | 問題 |
|--------|------------------|------|
| code-reviewer | ✅ Step 0: 対象ファイル種別チェック→中断 | — |
| test-reviewer | ✅ Step 0: テストコードか確認→中断 | — |
| brainstorming | ❌ なし | 既に設計が確定済みでも実施してしまう |
| requirements-definer | ❌ なし | 既に requirements.json があっても再定義してしまう |
| technical-writer | ❌ なし | 対象が SKILL.md なのか README なのかの判定がない |
| ui-designer | ❌ なし | CUI アプリに対しても UI 設計を開始してしまう |

### 改善案

プロセス系スキルに **Step 0: スコーピング** を追加:

```markdown
## Step 0: スコーピング（実行前確認）

以下を確認し、該当しない場合は中断して適切なスキルを提案する:

- [ ] このスキルの適用対象か（例: UI のないプロジェクトに ui-designer は不要）
- [ ] 既存の成果物が存在するか（存在する場合は「更新」モードで実行）
- [ ] 前提スキルの成果物が利用可能か（例: api-designer には要件定義が必要）
```

---

## 11. エラーリカバリ・フォールバックパターンの標準化 — **優先度: 中**

### 現状

スキル実行中に LLM エラー・ツール障害・予期しない入力が発生した場合の復旧手順が未定義。

| スキル | リカバリ対応 | 状態 |
|--------|------------|------|
| scrum-master | Phase チェックポイント（state.json に進捗保存） | ✅ 実装済み |
| tdd-executing | Red-Green-Refactor の各ステップで中間状態を保存 | ⚠️ 部分的 |
| その他 35 スキル | なし | ❌ 未対応 |

### 改善案

スキル共通のリカバリパターンを定義:

```markdown
## エラーリカバリ（全スキル共通）

### レベル 1: リトライ
- ツール呼び出しの一時的失敗→同一操作を 1 回リトライ

### レベル 2: チェックポイント復帰
- 複数ステップのスキルは各ステップ完了時にチェックポイントを保存
- 失敗時は最後のチェックポイントから再開

### レベル 3: フォールバック
- スキル自体が実行不能な場合、代替アプローチ（手動手順のガイド）を提示
- scrum-master 経由の場合は Phase のスキップ可否を判断
```

- `_shared/error-recovery.md` として共通化
- 各スキルは必要に応じてスキル固有のリカバリ手順を追加

---

## 12. セッション起動シーケンスの最適化 — **優先度: 低**

### 現状

`copilot-instructions.md` で 3 ステップの逐次実行が義務付けられている:

```
Step 1: auto_update.py check    （ネットワーク I/O あり）
Step 2: sync_copilot_memory.py  （ローカル I/O のみ）
Step 3: recall_memory.py        （ローカル I/O + タスク依存）
```

Step 1 と Step 2 は依存関係がなく並列実行可能だが、現在は逐次。

### 改善案

1. Step 1 と Step 2 を並列実行可能と明記（`&&` でバックグラウンド化、または `startup.py` に統合）
2. Step 1 の `interval_hours` チェックを先頭に移動し、不要時は即スキップ
3. Step 3 のキーワードをタスク内容から自動抽出（現在は手動指定）

```bash
# 改善後: 単一コマンドで 3 ステップを最適実行
python ~/.copilot/skills/session-bootstrap/scripts/startup.py --task "ユーザーのタスク"
```

---

## 13. ✅ skill-selector の実体化と機械可読カタログ — **優先度: 高**

### 現状

- `skill-selector` は 2026-03-10 レポートで「版なし」とリストされ、scrum-master Phase 1 で言及されるが **ディレクトリが存在しない**
- スキル選択は scrum-master の LLM 推論と copilot-instructions.md のスキル一覧（自然言語記述）に依存
- 43 ディレクトリ・39 個の SKILL.md を毎回 LLM が全文読解するのは非効率

### 改善案

1. **機械可読カタログ** (`skill-catalog.json`) を自動生成:

```json
{
  "skills": [
    {
      "name": "code-reviewer",
      "category": "review",
      "tier": "stable",
      "version": "2.0.0",
      "tags": ["code-quality", "security", "ai-code"],
      "triggers": ["コードをレビューして", "レビューお願い"],
      "io_contract": {
        "input": ["source_code", "diff"],
        "output": ["review_report_json"]
      }
    }
  ]
}
```

2. `git-skill-manager` の自動更新時にカタログを再生成
3. `skill-selector` スキルを実装し、カタログベースの高速マッチングを実現

---

## 14. SKILL.md 自体の多言語対応 — **優先度: 低**

### 現状

全 SKILL.md が日本語で記述されている。LLM は多言語を処理できるが、非日本語話者がスキルの内容を素早く理解するには障壁がある。

### 改善案

- SKILL.md フロントマターに `language: ja` を明示
- `references/` に英語版サマリ（`summary-en.md`）を配置する規約を定義
- ただし、全文翻訳は ROI が低いため、**トリガー例とサマリのみ**を対象とする

```yaml
metadata:
  language: ja
  i18n:
    en: references/summary-en.md
```

---

## 推奨着手順序

```
Phase A — 即効性の高い整理（低コスト・高効果）
  ├─ §3  category / tags フィールドの全スキル追加
  ├─ §9  クロスリファレンス検証スクリプトの追加
  └─ §10 プロセス系スキル 4 個に Step 0 追加

Phase B — 標準化・基盤整備（中コスト・高効果）
  ├─ §1  ティアシステムの定義と全スキルへの適用
  ├─ §2  Deprecation ライフサイクルの策定
  ├─ §5  レビュー出力スキーマの定義と 7 スキルへの適用
  ├─ §6  I/O コントラクトの形式定義（主要連携 10 ペア）
  └─ §13 skill-catalog.json の自動生成

Phase C — 品質・堅牢性の向上（中〜高コスト）
  ├─ §7  _shared/ ディレクトリの導入と共通テンプレート整備
  ├─ §8  統合テストフレームワークの構築
  ├─ §11 エラーリカバリパターンの策定
  └─ §4  provenance メタデータの整備

Phase D — 長期改善（低優先度）
  ├─ §12 セッション起動の最適化
  └─ §14 SKILL.md の多言語サマリ整備
```
