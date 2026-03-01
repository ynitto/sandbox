# フィードバックループと record_feedback

スキル使用後にフィードバックを収集し、スキル品質の改良トリガーとスキル発見の起点にする仕組み。

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
