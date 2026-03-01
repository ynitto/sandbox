# Phase 1: スキル探索

> **開始時出力**: `=== PHASE 1: スキル探索 開始 ===`

## やること

利用可能なスキルを把握する。

## 手順

1. パスを解決する（SKILL.md の「パス解決」セクション参照）:
   - `SKILL_DIR` = このSKILL.mdが置かれているディレクトリ
   - `SKILLS_DIR` = `SKILL_DIR` の親ディレクトリ
2. 以下のコマンドを実行する:
   ```bash
   python ${SKILL_DIR}/scripts/discover_skills.py ${SKILLS_DIR} --registry ~/.copilot/skill-registry.json
   ```
   - `--registry` を指定すると、無効化されたスキルやアクティブプロファイル外のスキルが除外される
   - レジストリが存在しない場合は全スキルが返される
   - ユーザーホーム（`~/.copilot/skills/`）とワークスペースのスキル領域（`<workspace-skill-dir>/`）の両方にスキルがある場合は、`SKILLS_DIR` 以外のディレクトリに対しても追加で実行し、同名スキルはユーザーホーム優先でマージする
3. 出力されたJSON一覧を記憶する（以降のフェーズでスキルマッチングに使用する）

## ゲート条件（Phase 2 に進む前に確認）

- [ ] スキル一覧JSONを取得済みである
- [ ] 取得したスキル一覧を記憶した

→ 条件を満たしたら **Phase 2: バックログ作成** へ進む
