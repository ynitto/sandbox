# 上級者ガイド — Agent Skills を極める

> **対象読者**: スキルの作成・フィードバック・チーム共有を日常的に行っている方、組織全体での運用設計や高度なカスタマイズに取り組む方
>
> **前提**: [中級者ガイド](guide-intermediate.md) の内容を実践できていること

---

## この資料でわかること

1. 高品質な SKILL.md の設計原則
2. マルチエージェントオーケストレーション
3. カスタムエージェントの設計と登録
4. LTM（長期記憶）の戦略的活用
5. クロスリポジトリマージとスキルの系統管理
6. CI/CD パイプラインとの統合
7. スキルのテスト戦略
8. コスト・レイテンシの最適化
9. セキュリティとガバナンス設計

---

## 1. 高品質な SKILL.md の設計原則

### エージェントが迷わない指示書を書く

SKILL.md は「人が読む仕様書」ではなく「エージェントが実行する手順書」です。曖昧さを徹底的に排除することが品質の鍵です。

#### アンチパターンと改善例

```markdown
# ❌ 曖昧な記述
コードをレビューして品質を確認すること。

# ✅ 実行可能な記述
## Step 1: 変更差分の取得
git diff で変更ファイルを列挙し、各ファイルについて以下を確認する。

## Step 2: チェック項目（各ファイル必須）
- [ ] OWASP Top 10 に該当する脆弱性パターンがないか
- [ ] N+1 クエリや不必要なループがないか
- [ ] エラーハンドリングが漏れていないか

## Step 3: 出力フォーマット
必ず以下の形式で出力すること：
**判定**: LGTM / Request Changes
**重大度別指摘リスト**: Critical > High > Medium > Low
```

### コンテキスト注入パターン

スキルが動的な情報を参照できるよう、ファイルを読み込む指示を明示します。

```markdown
## 前提情報の取得（毎回必ず実行）
1. `package.json` を読み、使用フレームワークとバージョンを確認する
2. `.github/copilot-instructions.md` を読み、プロジェクト固有のルールを把握する
3. 直近の git log（5件）を確認し、開発の文脈を理解する
```

### 終了条件の明示

スキルが「いつ完了したか」を明確に定義します。

```markdown
## 完了条件
- [ ] 全チェック項目を実行した
- [ ] 判定（LGTM / Request Changes）を出力した
- [ ] Request Changes の場合、修正方法を具体的に提示した
- [ ] ユーザーに次のアクションを提示した
```

### 再帰的スキル呼び出し

他のスキルを明示的に委譲することで、複合タスクを組み立てられます。

```markdown
## セキュリティチェック
セキュリティに関する指摘が1件以上ある場合、`security-reviewer` スキルに
詳細分析を委譲すること。
```

---

## 2. マルチエージェントオーケストレーション

### scrum-master の内部構造を理解する

`scrum-master` は以下のフェーズを順守してタスクを実行します：

```
Phase 1: バックログ作成（要件分解）
Phase 2: スプリント計画（スキル割り当て）
Phase 3: スプリント実行（サブエージェントへの委譲）
Phase 4: レビュー（sprint-reviewer による品質確認）
Phase 5: レトロスペクティブ
Phase 6: フィードバック収集
```

フェーズを飛ばさせたくない場合は `scrum-master-agent` を使います：

```
「確実にスクラムして。フェーズを飛ばさないで。
  要件: React ダッシュボードを実装し、テストとCI/CD設定まで完成させる」
```

### オーケストレーター自体をスキル化する

特定の作業フローを持つチームは、`scrum-master` を呼ぶ「上位スキル」を作れます。

```markdown
# SKILL.md 例：release-conductor（リリース指揮者）
## 役割
リリース作業を以下の順で自動実行するオーケストレーター。

## 実行順序
1. `code-reviewer` でリリース対象の差分をレビュー
2. `security-reviewer` で脆弱性スキャン
3. `technical-writer` でリリースノートを生成
4. `ci-cd-configurator` でデプロイコマンドを確認
5. ユーザーに最終承認を求める

## 中断条件
security-reviewer が Critical を検出した場合、即座に中断してユーザーに報告する。
```

### 並列実行の設計

スキル間に依存関係がない場合、並列実行を指示できます：

```
「code-reviewer、security-reviewer、test-reviewer を並列で実行して。
  すべて完了後に総合判定を出して」
```

依存関係のある場合は直列を明示します：

```
「まず requirements-definer で要件を固めて、完了後に domain-modeler を実行して。
  その後 api-designer に引き渡すこと」
```

---

## 3. カスタムエージェントの設計と登録

### エージェントとスキルの違い

| 項目 | スキル（SKILL.md） | エージェント |
|------|------------------|------------|
| 定義場所 | `.github/skills/` または `<AGENT_HOME>/skills/` | GitHub Copilot のエージェント設定 |
| 呼び出し方 | 「〇〇スキルを使って」 | `@agent-name` で直接メンション |
| 専門性 | 単一タスクに特化 | 複数スキルを束ねた役割 |
| 状態管理 | セッション内 | セッションをまたぐことも可能 |

### カスタムエージェントの定義例

`.github/copilot-instructions.md` に追記することで、プロジェクト固有のエージェント挙動を定義できます：

```markdown
## カスタムエージェント：@backend-guru

呼ばれたら以下のスキルを順に適用する：
1. `domain-modeler` でドメインモデルを確認
2. `api-designer` でエンドポイントを設計
3. `dynamodb-designer` でデータ設計を最適化

バックエンド以外の質問には「フロントエンドは @frontend-guru に聞いてください」と返す。
```

### エージェント間の引き継ぎプロトコル

複数エージェントが協調するとき、成果物の引き渡し形式を統一します。

```markdown
## 引き継ぎフォーマット（handoff.md）
---
from: requirements-definer
to: domain-modeler
artifact: requirements.json のパス
notes: |
  ユーザーが「注文管理」を最優先と明言。
  在庫との関係は Phase 2 以降に持ち越し。
---
```

---

## 4. LTM（長期記憶）の戦略的活用

### LTM の設計思想

LTM（Long-Term Memory）はセッションをまたいで知識を継続させる仕組みです。「いつ、何を、どの粒度で保存するか」の設計が品質を左右します。

### 保存すべき情報の分類

| カテゴリ | 例 | タグ例 |
|---------|-----|-------|
| バグと解決策 | 「DynamoDB の GSI クエリが遅い原因はキャパシティ設定」 | `bug,dynamodb,perf` |
| 設計の決定事項 | 「認証はCognitoではなくJWTを採用した理由」 | `decision,auth` |
| ユーザーの好み | 「コメントは日本語、コードは英語で書く」 | `style,user-pref` |
| 定型ワークフロー | 「このプロジェクトのPRフローは必ずレビュー2名」 | `workflow,pr` |

### 保存のタイミング

```bash
# 明示的に保存する場合
python <AGENT_HOME>/skills/ltm-use/scripts/save_memory.py \
  --title "DynamoDB GSI レイテンシ問題の解決" \
  --summary "ReadCapacityUnits を 5→100 に変更で解決。原因はバースト消費の上限" \
  --tags dynamodb,perf,solved

# セッション開始時に関連記憶を引き出す
python <AGENT_HOME>/skills/ltm-use/scripts/recall_memory.py "DynamoDB パフォーマンス"
```

### グローバル記憶とプロジェクト記憶の使い分け

```
グローバル記憶（全プロジェクト共通）
  → 言語・スタイルの好み、汎用的なバグ解決策

プロジェクト記憶（このリポジトリ専用）
  → アーキテクチャ決定、チーム固有ルール、ビジネスロジックの背景
```

`ltm-use` スキルでプロジェクト記憶を保存する場合は、パスにプロジェクト名を含めます：

```
「sandbox/auth/jwt-decision というメモリ名で覚えておいて。
  内容: Cognito は社内ポリシーで禁止のためカスタムJWTを採用」
```

### 記憶の定期整理

記憶が増えると検索ノイズになります。四半期ごとに整理を推奨します：

```
「ltm の記憶を整理して。6ヶ月以上アクセスされていないものをアーカイブして」
```

---

## 5. クロスリポジトリマージとスキルの系統管理

### クロスリポジトリマージとは

複数チームがそれぞれ独自に改善したスキルを統合する機能です。中央リポジトリへの `push` では解決できない、複数フォーク間のマージに使います。

```
チームA の react-frontend-coder（RSC 対応済み）
    ↓            ↘
                   merge-skills（競合解決）
    ↑            ↗
チームB の react-frontend-coder（Server Actions 対応済み）
```

```
「チームAのスキルリポジトリとチームBのスキルリポジトリをマージして」
「競合が発生した場合は差分を提示して、どちらを採用するか確認して」
```

### lineage を活用した品質追跡

`skill-registry.json` の `lineage` フィールドは、スキルの出自と変遷を追跡します。

```json
{
  "lineage": {
    "origin_repo": "team-skills",
    "origin_commit": "a1b2c3d",
    "origin_version": "1.2.0",
    "local_modified": true,
    "diverged_at": "2026-02-20T00:00:00Z",
    "local_changes_summary": "RSC対応 + Server Actions のサンプル追加",
    "merge_history": [
      { "from": "team-b-skills", "at": "2026-03-01", "changes": "Server Actions 対応" }
    ]
  }
}
```

### 系統ツリーの可視化

```
「react-frontend-coder の系統ツリーを見せて」
「このスキルはどのリポジトリを起源としているか確認して」
```

系統を管理することで、改善のどこが誰の貢献かを追跡でき、適切な `credit` の帰属が可能になります。

### 自動更新ポリシーの設計

チームの運用規模に応じて自動更新の頻度を設定します：

```json
{
  "auto_update": {
    "enabled": true,
    "interval_hours": 24,
    "repositories": ["team-skills"],
    "exclude": ["my-custom-skill"],
    "on_conflict": "prompt"
  }
}
```

| `on_conflict` 値 | 挙動 |
|-----------------|------|
| `prompt` | ユーザーに確認（デフォルト） |
| `keep_local` | ローカルを優先 |
| `keep_remote` | リモートを優先 |
| `auto_merge` | 自動マージを試みる |

---

## 6. CI/CD パイプラインとの統合

### スキルをパイプラインに組み込む

`ci-cd-configurator` スキルを使って、スキルの実行をパイプラインに統合できます。

#### GitLab CI 統合例

```yaml
# .gitlab-ci.yml
stages:
  - skill-review

skill-code-review:
  stage: skill-review
  script:
    - python .github/skills/code-reviewer/scripts/run_review.py --diff-only
    - python .github/skills/security-reviewer/scripts/run_scan.py
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  artifacts:
    reports:
      junit: review-results.xml
```

### フィードバックの自動収集

CI/CD 結果をスキルフィードバックに自動変換するスクリプトを組み込めます：

```bash
# テストが全通過 → ok フィードバック
if pytest --tb=short; then
  python <AGENT_HOME>/skills/git-skill-manager/scripts/feedback.py \
    --skill react-frontend-unit-tester \
    --verdict ok \
    --notes "CI で全テスト通過"
else
  python <AGENT_HOME>/skills/git-skill-manager/scripts/feedback.py \
    --skill react-frontend-unit-tester \
    --verdict needs-improvement \
    --notes "CI でテスト失敗: $(pytest --tb=line 2>&1 | tail -5)"
fi
```

### スキル品質ゲート

マージ前にスキルの `ok_rate` を確認するゲートを設定できます：

```python
# skill_quality_gate.py
import json, sys

with open(os.path.expanduser("<AGENT_HOME>/skill-registry.json")) as f:
    registry = json.load(f)

for skill_name, skill in registry["skills"].items():
    ok_rate = skill.get("metrics", {}).get("ok_rate", 1.0)
    if ok_rate < 0.7:
        print(f"❌ {skill_name} の ok_rate が低すぎます ({ok_rate:.0%})")
        sys.exit(1)

print("✅ 全スキルの品質ゲートを通過")
```

---

## 7. スキルのテスト戦略

### スキルテストの3層構造

```
Layer 3: E2E テスト
  → 実際のエージェントに実行させてアウトプットを検証

Layer 2: 統合テスト
  → スキルが参照するスクリプトの動作検証

Layer 1: ユニットテスト
  → SKILL.md の構造・必須フィールドの存在確認
```

### Layer 1: 構造チェック（自動化推奨）

```python
# test_skill_structure.py
import os, pathlib, pytest

SKILLS_DIR = pathlib.Path("<AGENT_HOME>/skills").expanduser()

@pytest.mark.parametrize("skill_dir", SKILLS_DIR.iterdir())
def test_skill_has_skill_md(skill_dir):
    assert (skill_dir / "SKILL.md").exists(), f"{skill_dir.name} に SKILL.md がない"

def test_skill_md_has_required_sections(skill_dir):
    content = (skill_dir / "SKILL.md").read_text()
    for section in ["## 役割", "## 完了条件"]:
        assert section in content, f"{skill_dir.name} の SKILL.md に {section} がない"
```

### Layer 2: スクリプトの統合テスト

```bash
# scripts/ 以下の補助スクリプトをモックデータで検証
pytest skills/code-reviewer/tests/ -v
```

### Layer 3: E2E テスト（Golden Output テスト）

```
「test-fixtures/pr-diff.txt を入力として code-reviewer を実行して。
  出力を tests/golden/code-review-output.txt と比較して差分を報告して」
```

Golden Output が変わった場合、意図的な変更か退行かを判断します：

| 変更の性質 | 対応 |
|-----------|------|
| 改善（より詳しい指摘） | Golden Output を更新 |
| 退行（指摘が減った） | フィードバックを `broken` で記録 |
| 出力形式の変化 | SKILL.md の出力フォーマット指定を見直す |

---

## 8. コスト・レイテンシの最適化

### コストの主な要因

| 要因 | 説明 | 対策 |
|------|------|------|
| トークン消費 | SKILL.md が長いほど消費増 | 不要なセクションを削除 |
| ファイル読み込み | コンテキスト注入で増加 | 必要なファイルのみ指定 |
| スキル連鎖 | 多数のスキルを直列実行 | 独立したものは並列実行 |
| 反復実行 | 同一スキルを複数回呼ぶ | 1回の実行で完結するよう設計 |

### SKILL.md の軽量化

```markdown
# ❌ 冗長な記述（1200 トークン）
このスキルはReactとTypeScriptを使ったフロントエンド開発を支援するスキルです。
Reactのベストプラクティスに従い、コンポーネントを設計し...（説明が続く）

# ✅ 簡潔な記述（300 トークン）
## 役割
React + TypeScript でコンポーネントを実装する。

## 手順
1. 要件確認 → 2. コンポーネント設計 → 3. 実装 → 4. 検証バリデーション

## 出力
- 実装ファイル（.tsx）
- 使用方法サンプル
```

### プロファイルによるコスト制御

利用スキルを絞ることで、エージェントが参照する SKILL.md の総量を削減できます：

```
「review プロファイルに切り替えて」
→ review 関連スキルのみ有効化されるため、他スキルの SKILL.md を読み込まない
```

### バッチ実行で往復コストを削減

```
# ❌ 個別実行（3回分のコンテキスト初期化コスト）
「code-reviewer を実行して」（完了後）
「security-reviewer を実行して」（完了後）
「test-reviewer を実行して」

# ✅ 一括指示（コンテキストを共有）
「code-reviewer、security-reviewer、test-reviewer を一度に実行して。
  終わったら総合判定を出して」
```

---

## 9. セキュリティとガバナンス設計

### スキルの信頼レベル

組織内でスキルの「信頼レベル」を定義して運用します：

| レベル | 説明 | 利用条件 |
|--------|------|---------|
| `trusted` | 中央でレビュー済み | 誰でも利用可 |
| `experimental` | 個人または小チームが作成 | レビュー前の試用 |
| `deprecated` | 廃止予定 | 新規利用禁止 |
| `blocked` | 問題が発覚・無効化 | 利用不可 |

```json
{
  "trust_level": "experimental",
  "review_required_before_promote": true,
  "blocked_actions": ["rm -rf", "DROP TABLE"]
}
```

### シークレットの取り扱いルール

SKILL.md に含めてはいけない情報を明示します：

```markdown
## セキュリティ制約（全スキル共通）
- API キー、パスワード、トークンを出力・ログに含めないこと
- 環境変数は `$ENV_VAR` 形式で参照し、値を展開しないこと
- `~/.aws/credentials` 等の認証情報ファイルを読まないこと
```

この共通制約を `.github/copilot-instructions.md` に記載することで、全スキルに横断適用できます。

### 監査ログの設計

```bash
# 誰がいつどのスキルを実行したか記録する
python <AGENT_HOME>/skills/git-skill-manager/scripts/audit_log.py \
  --skill security-reviewer \
  --user "$(git config user.email)" \
  --timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

### ガバナンスポリシーの例

| ポリシー | 設定 |
|---------|------|
| 本番デプロイ前に security-reviewer が必須 | CI ゲートで強制 |
| trusted 以外のスキルは本番ブランチで利用禁止 | ブランチ保護ルール |
| スキルの昇格には 2 名のレビュー承認が必要 | PR テンプレートで明示 |
| 月次でスキルの ok_rate レポートを生成 | cron ジョブ |

---

## 10. 上級ワークフロー例

### 例1：組織横断スキルライブラリの構築

```
1. 各チームがプロジェクト固有スキルを .github/skills/ で開発
   → skill-creator（モードB）でプロジェクト規約をスキル化

2. ok_rate ≥ 0.9 かつ 10 回以上の実績があるスキルを昇格候補にする
   → 「試用中スキルを確認して昇格判定して」（skill-evaluator）

3. 昇格候補を cross-repo-merge で統合
   → 「チームAとチームBのスキルをマージして。競合は差分提示で確認」

4. 統合済みスキルを組織の中央リポジトリに push
   → 「組織リポジトリにスキルをpushして」

5. 全チームが pull して最新スキルを取得
   → 自動更新ポリシーで 24 時間ごとに同期
```

### 例2：スキル駆動の新機能開発フロー

```
1. 要件定義
   「scrum-master を確実に使って。
    要件: ユーザーダッシュボードに通知機能を追加する」

2. scrum-master が自動実行：
   Sprint 1: requirements-definer → domain-modeler
   Sprint 2: api-designer → react-frontend-coder
   Sprint 3: react-frontend-unit-tester → ci-cd-configurator

3. 各スプリントの完了を sprint-reviewer が判定

4. 全スプリント完了後、LTM に設計決定を保存
   「今回の設計決定（WebSocket採用の理由）を覚えておいて」

5. フィードバックを一括収集
   → 次回以降のスプリント品質が向上
```

### 例3：スキルの健全性ダッシュボード

全スキルの状態を定期的にレポートするウィークリーレビュー：

```bash
#!/bin/bash
# weekly_skill_health.sh

echo "=== スキル健全性レポート $(date +%Y-%m-%d) ==="
python <AGENT_HOME>/skills/git-skill-manager/scripts/list_skills.py \
  --format json | python -c "
import json, sys
skills = json.load(sys.stdin)
print('ok_rate が低いスキル（要改善）:')
for s in sorted(skills, key=lambda x: x.get('ok_rate', 1)):
    if s.get('ok_rate', 1) < 0.7:
        print(f'  {s[\"name\"]}: {s[\"ok_rate\"]:.0%} ({s[\"total_executions\"]}回実行)')
"
```

---

## 付録 A: 上級者向け設定リファレンス

### skill-registry.json の全フィールド

```json
{
  "node": {
    "id": "node-abc123",
    "name": "tokyo-team-dev",
    "trust_level": "trusted"
  },
  "auto_update": {
    "enabled": true,
    "interval_hours": 24,
    "on_conflict": "prompt"
  },
  "profiles": {
    "default": ["*"],
    "ci": ["code-reviewer", "security-reviewer", "test-reviewer"]
  },
  "skills": {
    "my-skill": {
      "version": "1.3.0",
      "pinned": false,
      "trust_level": "experimental",
      "metrics": {
        "total_executions": 42,
        "ok_rate": 0.93,
        "last_executed_at": "2026-03-07T12:00:00Z"
      },
      "lineage": {
        "origin_repo": "team-skills",
        "local_modified": true,
        "local_changes_summary": "チーム固有の命名規則を追加"
      }
    }
  }
}
```

---

## 付録 B: トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| スキルが呼ばれない | SKILL.md の発動条件が曖昧 | description フィールドにキーワードを具体的に追加 |
| 毎回指示を途中で忘れる | SKILL.md が長すぎてコンテキスト圧迫 | 重要度の低いセクションを削除または外部ファイルに移動 |
| ok_rate が上がらない | 完了条件が未定義 | SKILL.md に「完了条件」セクションを追加 |
| クロスマージで無限競合 | 同一行を2チームが独立変更 | テンプレート化して競合しない構造に分割 |
| LTM の検索精度が低い | タグが不統一 | タグ一覧を標準化し SKILL.md に記載 |

---

## 次のステップ

- **スキルエコシステムの設計**に興味がある方 → [ノードフェデレーション設計](designs/node-federation-design.md) を熟読し、組織のトポロジーに合わせたリポジトリ構成を設計してください
- **自動化・CI/CD 統合**に取り組む方 → `ci-cd-configurator` スキルで GitLab CI / Jenkins パイプラインを構築してください
- **スキルのオープンソース化**を検討している方 → `skill-creator`（モードD）でスキルをパブリックリポジトリに公開するフローを確認してください
