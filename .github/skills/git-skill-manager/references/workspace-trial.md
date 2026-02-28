# ワークスペーストライアルフロー

VSCode チャット経由で作成されたスキルは `.github/skills/` に置かれる（ワークスペース領域）。
ユーザーホームの `~/.copilot/skills/` とは別の場所なので、まず試用してから昇格する。

## スキルのライフサイクル

```
【作成】 skill-creator → .github/skills/<name>/   (source_repo: "workspace")
   ↓ 使用するたびにフィードバック収集
【評価】 record_feedback.py が自動評価
   ├── ok × 2回以上、問題なし  → ✅ 昇格推奨
   ├── 問題あり (needs-improvement/broken)  → ⚠️ 要改良後昇格
   └── ok × 1回  → 🔄 試用継続
   ↓ 昇格推奨 or ユーザーが判断
【昇格】 promote → ~/.copilot/skills/<name>/   (source_repo: "local")
   ↓ 必要なら
【共有】 push → チームリポジトリ
```

## 評価基準

評価基準の詳細は `skill-evaluator` スキルの SKILL.md を参照してください。

## 評価の実行

**インライン（フィードバック記録時に自動トリガー）**

`record_feedback.py` がワークスペーススキルを検出すると `EVAL_RECOMMEND:` シグナルを出力する:

```
✅ my-skill: フィードバックを記録しました (ok)
EVAL_RECOMMEND: promote
```

エージェントはこのシグナルを受けて `skill-evaluator` サブエージェントを起動する（`promote` または `refine` の場合のみ）。

**バッチ（スプリント完了時）**

scrum-master の Phase 6 が `skill-evaluator` サブエージェントを起動して全ワークスペーススキルを一覧評価する:

```bash
python .github/skills/skill-evaluator/scripts/evaluate.py
```
