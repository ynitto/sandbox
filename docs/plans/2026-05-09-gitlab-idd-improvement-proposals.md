# gitlab-idd 改善提案（2026-05-09）

## 背景

gitlab-idd v4.3.0 のスキーマ（SKILL.md の構造・ロール定義・ラベル規約）を変更せずに、
既存スキルの補強または新規スキルの導入によって実装・レビュー精度を高める案を検討した。

---

## 現状のボトルネック分析

| # | ボトルネック | 影響フェーズ | 深刻度 |
|---|---|---|---|
| B-1 | **受け入れ条件の品質がバラつく** — リクエスターがアドホックに書くため「検証できない条件」「範囲が曖昧な条件」が混入する | Requester Post / Worker clarity check | ★★★ |
| B-2 | **ワーカーの成果物が DoD（Done の定義）を見落とす** — イシューごとの AC は確認するが、プロジェクト横断の規約（テスト網羅率・セキュリティ・ドキュメント更新）を見落としやすい | Worker Phase 4-5 | ★★★ |
| B-3 | **agent-reviewer が汎用 perspective で判定する** — `functional / ai-antipattern / architecture` は固定的で、イシューの受け入れ条件に特化した検証が弱い | Reviewer ステップ 3 | ★★☆ |
| B-4 | **依存解除が手動** — `status:blocked` のイシューは依存先が完了しても自動で `status:open` に戻らない | Worker Phase 2 / 全体の流れ | ★★☆ |
| B-5 | **フィードバックループがない** — 過去のイシューの成功/失敗パターンが蓄積されず、skill-selector の精度が改善されない | Worker Phase 5 / Requester Post | ★★☆ |
| B-6 | **非リクエスターレビューが孤立する** — 助言コメントがリクエスターに拾われないまま承認が進むことがある | Non-requester review | ★☆☆ |

---

## 補強案（スキーマ変更なし）

### 案 A: `issue-quality-gate` 新規スキル（B-1 解消）

**何をするか**: リクエスターがイシュー作成前に受け入れ条件を自動評価する検証ゲート。

Requester Post フェーズ 3（内容整理）のあと、フェーズ 4（作成）の前に挟む。

評価項目:
- AC の各項目が「テスト可能か」（Given-When-Then に変換できるか）
- 影響範囲（ファイル・API）が特定されているか
- 外部依存・技術制約が明記されているか
- サイズ見積もりと AC 数が整合するか

**参考トレンド**: BDD の "Three Amigos" 事前品質チェック。AI を使ったイシュー品質スコアリングは
GitHub Copilot Issues や Linear AI などで 2025 年以降に実用化が進んでいる。

**実装コスト**: 中（新規 SKILL.md + references/ac-rubric.md）

---

### 案 B: プロジェクト横断 DoD リファレンス（B-2 解消）

**何をするか**: `references/project-dod.md` をリポジトリに置き、agent-reviewer と
worker-role.md の Phase 5 でそれを参照する。

```markdown
# project-dod.md（リポジトリオーナーが定義）

## テスト要件
- [ ] ユニットテストカバレッジ 80% 以上
- [ ] 新規 API エンドポイントに統合テストあり

## セキュリティ
- [ ] 入力バリデーションあり
- [ ] 機密情報のログ出力なし

## ドキュメント
- [ ] 公開 API の JSDoc/docstring あり
```

**実装コスト**: 低（参照ファイルの追加 + worker-role.md に一行追記）

---

### 案 C: AC 特化レビュー perspective（B-3 解消）

**何をするか**: `agent-reviewer` に `acceptance-criteria` という新 perspective を追加し、
イシューの受け入れ条件との対応を専用チェックする。

```
perspective: acceptance-criteria
  → イシューの受け入れ条件チェックリストを一覧化
  → 各項目の充足/未充足を diff から判定
  → 充足度を数値スコアで返す
```

**参考トレンド**: Requirements Traceability Matrix（RTM）の自動化。
acceptance test driven development (ATDD) の考え方と一致。

**実装コスト**: 中（agent-reviewer に perspective を 1 つ追加）

---

### 案 D: 依存解除の自動化（B-4 解消）

**何をするか**: worker-role.md Phase 5（完了報告）に
「完了したイシューを依存として持つ `status:blocked` イシューを検索し、
他の依存がすべて完了していれば `status:open,assignee:any` に自動解除する」ステップを追加。

**実装コスト**: 低（worker-role.md のステップ追加のみ。gl.py は既存コマンドで対応可能）

---

### 案 E: LTM フィードバックループ（B-5 解消）

**何をするか**: Worker Phase 5 で完了報告と同時に `ltm-use` に
「このイシューで有効だったスキル組み合わせ」を保存し、
次回 skill-selector がより精度高く推薦できるようにする。

**参考トレンド**: Continual/Reinforcement Learning from feedback in AI agents。
GitHub Copilot Workspace のフィードバック収集機能と類似。

**実装コスト**: 低（worker-role.md Phase 5 に ltm save ステップ追加）

---

## 優先度マトリクス（初回評価）

| 案 | ボトルネック解消 | 実装コスト | 推奨優先度 |
|---|---|---|---|
| A issue-quality-gate | B-1 | 中 | P2 |
| B project-dod | B-2 | 低 | **P1** |
| C AC perspective | B-3 | 中 | P2 |
| D 依存自動解除 | B-4 | 低 | **P1** |
| E LTM feedback | B-5 | 低 | P2 |

---

## 未解決の問いかけ

1. B-1 解消に向けて: 新規スキル `issue-quality-gate` vs 既存 `requirements-definer` のインライン呼び出しか
2. 案 C（AC perspective）: agent-reviewer の SKILL.md を変えることは「スキーマ変更」に当たるか
3. 優先着手順の合意
