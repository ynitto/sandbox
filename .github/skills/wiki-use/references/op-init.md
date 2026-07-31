# init — Wiki を初期化する

パス規約:
- 生成・表示するローカルパスは `wiki_root` 起点の相対パスで扱う（例: `wiki/meta/hot.md`, `index.md`）
- 絶対パスは使わない

```bash
python scripts/wiki_init.py
```

実行内容:
1. `<agent_home>/skill-registry.json` の `skill_configs.wiki-use` が未設定の場合、ユーザーに `wiki_root` を確認する
2. `wiki_root` 配下に標準ディレクトリ構造を作成する
3. `SCHEMA.md`・`index.md`・`log.md`・`wiki/meta/hot.md`・`wiki/meta/queries.md` を初期テンプレートで生成する
4. 完了後に構造を表示する

設定例（`<agent_home>/skill-registry.json` の `skill_configs.wiki-use`）:
```json
{
  "skill_configs": {
    "wiki-use": {
      "wiki_root": "~/Documents/wiki"
    }
  }
}
```

