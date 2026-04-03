# フィードバックループと record_feedback

スキル使用後にフィードバックを収集し、スキル品質の改良トリガーとスキル発見の起点にする仕組み。

## 目次

- [pending_refinement トリガーのしきい値](#pending_refinement-トリガーのしきい値)
- [フィードバックの記録](#フィードバックの記録)
- [discover_skills.py のソート順](#discover_skillspy-のソート順)
- [feedback 操作の詳細フロー](#feedback-操作の詳細フロー)
- [evaluate 操作の詳細フロー](#evaluate-操作の詳細フロー)

## pending_refinement トリガーのしきい値

スキルの種別ごとに `pending_refinement` が立つまでの未改良問題数が異なる。頻度を下げることで、安定稼働しているインストール済みスキルへの過剰な改良提案を防ぐ。

| スキル種別 | source_repo | デフォルトしきい値 |
|---|---|---|
| ワークスペーススキル | `workspace` | **1件**（即トリガー）|
| インストール済みスキル | `local` / リポジトリ名 | **3件**（蓄積してトリガー）|

`mark_refined()` 実行後は未改良カウントがリセットされるため、1サイクルの改良後に再び N 件蓄積するまで提案されない。
スキルエントリに `refine_threshold` フィールドを設定すれば個別に上書き可能。

## フィードバックの記録

使用後フィードバックは `feedback` 操作または `record_feedback.py` スクリプトで直接行う。

### feedback 操作（ユーザー向け）

スキルを直接実行した後（scrum-master 経由でない場合）にフィードバックを記録したいときに使う:

```
「git-skill-manager で [skill-name] のフィードバックを記録して」
「[skill-name] に ok のフィードバックを付けて」
「[skill-name] に needs-improvement を記録して。[改善点の説明]」
```

処理フロー:
1. ユーザーにスキル名と verdict（ok / needs-improvement / broken）を確認する
2. `record_feedback.py` を実行して記録する
3. 出力に `EVAL_RECOMMEND: promote` または `EVAL_RECOMMEND: refine` が含まれる場合は、`evaluate` 操作を実行する（ワークスペース・インストール済み両方に対応）

### record_feedback.py（スクリプト直接呼び出し）

```bash
# 問題なく動作した
python <SKILLS_BASE>/git-skill-manager/scripts/record_feedback.py <skill-name> --verdict ok

# 改善余地あり
python <SKILLS_BASE>/git-skill-manager/scripts/record_feedback.py <skill-name> --verdict needs-improvement --note "改善点の説明"

# 動作しなかった
python <SKILLS_BASE>/git-skill-manager/scripts/record_feedback.py <skill-name> --verdict broken --note "壊れている箇所"
```

## discover_skills.py のソート順

`discover_skills.py` はスキル一覧を以下の優先度でソートして返す:

1. **コアスキル** (`core_skills` に含まれるスキル) → 常に先頭
2. **改良待ちなし + 直近 ok** (`pending_refinement=false` かつ最新 verdict が ok) → 信頼済み
3. **改良待ちあり** (`pending_refinement=true`) → 後ろに配置
4. **フィードバックなし** → アルファベット順

→ 実装: `scripts/manage.py` — `sort_key(skill, core_skills, registry)`

---

## feedback 操作の詳細フロー

直前に実行したスキルの満足度をユーザーに確認し、レジストリに記録する。
スキル単体起動後に `copilot-instructions.md` の指示で自動的に呼ばれる。

→ 実装: `scripts/record_feedback.py`

1. 対象スキル名を確認（不明な場合はユーザーに確認）
2. 次の選択肢をユーザーに提示し、**ユーザーの回答を受け取るまで待機する**（ターンを終えてユーザー入力を待つこと。回答前に次のステップへ進んではいけない）:
   ```
   「[スキル名] の実行はいかがでしたか？
    1. 問題なかった (ok)
    2. 改善点がある (needs-improvement)
    3. うまくいかなかった (broken)」
   ```
3. ユーザーの選択を `<verdict>` に使って `python record_feedback.py <name> --verdict <verdict> --note <note>` を実行。実行時間が分かる場合は `--duration <秒>` を追加する
4. 出力に応じて次のアクションを取る:
   - `EVAL_RECOMMEND: promote` または `EVAL_RECOMMEND: refine` → `evaluate` 操作へ進む（ワークスペース・インストール済み両方に対応）
   - `EVAL_RECOMMEND: continue` → 「試用継続中です（あと N 回の ok フィードバックで昇格候補になります）」とユーザーに伝えて終了

---

## evaluate 操作の詳細フロー

ワークスペーススキル（試用中）とインストール済みスキル（ホーム領域）の両方の推奨アクションを評価する。`skill-evaluator` スキルを呼び出して実行する。

### トリガー

| トリガー | 説明 |
|---|---|
| `record_feedback.py` の `EVAL_RECOMMEND: promote\|refine` 出力 | フィードバック記録後にインラインで自動起動 |
| scrum-master Phase 6 | スプリント完了時のバッチ棚卸し |
| ユーザー直接 | 「スキルを評価して」など |

### 処理フロー

→ 実装: `<SKILLS_BASE>/skill-evaluator/scripts/evaluate.py`（skill-evaluator スキルが管理、`<SKILLS_BASE>` は `<AGENT_HOME>/skills` または `<workspace-skill-dir>`）

1. `skill-evaluator` サブエージェントを起動する:
   ```
   skill-evaluator スキルでスキルを評価する。
   手順: まず <SKILLS_BASE>/skill-evaluator/SKILL.md を読んで手順に従ってください。
   ```
2. skill-evaluator が評価結果を提示し、promote / refine のアクションをユーザーに確認する
3. 「昇格する」→ `promote` 操作を実行する
4. 「改良する」→ `refine` 操作を実行する
