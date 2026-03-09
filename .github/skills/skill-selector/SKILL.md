---
name: skill-selector
description: 最適なスキルの組み合わせを選択・推薦するメタスキル。「どのスキルを使えばいい？」「スキルを選んで」「スキルの組み合わせを提案して」などのリクエストに加え、複数スキルにまたがる複合タスクと判断した場合も自律的に発動する。Windows/Copilot・macOS/Claude Code 両環境で動作する。
---

# Skill Selector

ユーザーのタスクを分析し、利用可能なスキルの中から最適な組み合わせを特定・提案するメタスキル。エージェントの標準スキル選択能力を補完し、複合タスクや複数フェーズにまたがる作業に対応する。

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、その親を `SKILLS_DIR` とする。

| このSKILL.mdのパス | SKILL_DIR | SKILLS_DIR |
|---|---|---|
| `~/.copilot/skills/skill-selector/SKILL.md` | `~/.copilot/skills/skill-selector` | `~/.copilot/skills` |
| `.github/skills/skill-selector/SKILL.md` | `.github/skills/skill-selector` | `.github/skills` |

スキルが両方の場所に存在する場合、ワークスペース側（`.github/skills/`）を優先する。

---

## 選択プロセス

### Step 1: タスクを分析する

ユーザーのリクエストから以下を読み取る:

- **ゴール**: 何を達成したいか（作る・直す・調べる・改善する・整理する）
- **フェーズ**: タスクが開発ライフサイクルのどの段階か
- **対象**: コード・ドキュメント・設計・データ・インフラのどれか
- **複雑度**: 単一スキルで完結するか、複数スキルの連携が必要か

### Step 2: 利用可能なスキルを探索する

`discover_skills.py` を実行して、現在使えるスキルを列挙する:

```
python <SKILL_DIR>/scripts/discover_skills.py
```

このスクリプトは `~/.copilot/skills/` と `.github/skills/` の両方を走査し、各スキルの `name` と `description` を出力する。**Windows・macOS 両対応**。

スクリプトが実行できない環境では、スキルリストを直接 `<SKILLS_DIR>/` のディレクトリ一覧から確認する。

### Step 3: タクソノミーで絞り込む

タスクのフェーズとカテゴリを以下のタクソノミーに照合し、候補スキルを絞る:

| フェーズ | カテゴリ | 代表スキル例 |
|---|---|---|
| 構想・設計 | ideation | brainstorming, requirements-definer, domain-modeler, api-designer, ui-designer |
| 実装 | implementation | react-frontend-coder, ci-cd-configurator, dynamodb-designer |
| テスト・検証 | testing | tdd-executing, react-frontend-unit-tester, webapp-testing |
| デバッグ | debugging | debug-mode, systematic-debugging |
| レビュー・品質 | review | code-reviewer, code-simplifier, design-reviewer, architecture-reviewer, security-reviewer, test-reviewer, document-reviewer |
| ドキュメント | documentation | technical-writer, doc-coauthoring, patent-coach, patent-writer |
| オーケストレーション | orchestration | scrum-master, skill-selector |
| スキル管理 | meta | skill-creator, skill-evaluator, skill-recruiter, git-skill-manager, ltm-use, generating-skills-from-copilot-logs |
| リサーチ | research | deep-research |

**重要**: このタクソノミーは参考例。実際に利用可能なスキルは Step 2 で探索した結果を正とする。新しいスキルが追加されても、`description` を読んでタスクへの適合性を判断できる。

### Step 4: 組み合わせを評価する

単一スキルで完結するか、複数スキルの連携が必要かを判断する:

- **単一スキル**: タスクが1つのカテゴリに収まり、description が一致する
- **シーケンシャル**: フェーズが複数にまたがる（設計→実装→テストなど）
- **並列**: 独立した側面を同時対応（例: フロントエンド実装と API 設計）

組み合わせパターンの詳細は [references/combinations.md](references/combinations.md) を参照。

### Step 5: 推薦を提示する

以下の形式でユーザーに提示する:

```
## 推薦スキル構成

**ゴール**: [ユーザーのタスク要約]

### プライマリスキル
- `skill-name` — 理由（このスキルが担う役割）

### 補助スキル（任意）
- `skill-name` — 理由（どの段階で使うか）

### 実行順序
1. skill-A → 2. skill-B → 3. skill-C

### 注意
- [スキルの重複・競合がある場合はここに記載]
```

---

## ギャップへの対応

適切なスキルが見つからない場合:

1. **description を再読する** — 一見無関係に見えるスキルが対応している場合がある
2. **skill-recruiter を使う** — 外部リポジトリからスキルを取得できる
3. **skill-creator を使う** — 新しいスキルを作成する
4. **エージェント標準機能で対応する** — スキルなしで進め、必要に応じて記憶に残す（ltm-use）

---

## アンチパターン

- **過剰選択**: 「念のため」で多くのスキルを選ばない。タスクに最小限のスキルを選ぶ
- **静的マッピング依存**: 新しいスキルは description を読んで判断する。古い固定マッピングに頼らない
- **スキル強制**: 既存スキルで対応できない場合はエージェント標準機能を使う
