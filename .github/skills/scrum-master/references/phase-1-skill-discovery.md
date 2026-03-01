# Phase 1: スキル探索

利用可能なスキルを把握する。

```bash
python .github/skills/scrum-master/scripts/discover_skills.py .github/skills --registry ~/.copilot/skill-registry.json
```

`--registry` を指定すると、無効化されたスキルやアクティブプロファイル外のスキルが除外される。レジストリが存在しない場合は全スキルが返される。

出力されたJSON一覧を記憶する。以降のタスク分解でスキルマッチングに使う。
