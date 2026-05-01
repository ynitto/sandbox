# wiki-use 設定リファレンス

## 設定の保存先

wiki-use の設定は `skill-registry.json` の `skill_configs.wiki-use` セクションに統合されています。

```
{agent_home}/skill-registry.json
```

`agent_home` は `wiki_utils.py` の `get_agent_home()` で決定される（`.claude`、`.copilot`、`.kiro`、`.codex` のいずれか）。

---

## 設定例

`skill-registry.json` 内の該当セクション:

```json
{
  "skill_configs": {
    "wiki-use": {
      "wiki_root": "~/Documents/wiki"
    }
  }
}
```

---

## フィールド説明

| フィールド | 必須 | デフォルト | 説明 |
|-----------|------|-----------|------|
| `wiki_root` | ✓ | なし | Wiki のルートディレクトリ。`~/` 形式のパスを使用可。 |

---

## Obsidian Vault と統合する場合

Obsidian Vault 内に wiki_root を設定すると、Obsidian でそのまま閲覧・編集できる。

```json
{
  "skill_configs": {
    "wiki-use": {
      "wiki_root": "~/Documents/ObsidianVault/llm-wiki"
    }
  }
}
```

`[[ページ名]]` 形式のリンクは Obsidian のウィキリンクとして機能する。

---

## デフォルト動作（設定未作成時）

`skill-registry.json` の `skill_configs.wiki-use` が存在しない場合、`wiki_utils.py config` はエラーを返す。
`wiki_init.py` を実行して設定を作成してから使用すること。

---

## `wiki_utils.py config` の出力例

```
registry_path : ~/.copilot/skill-registry.json
wiki_root     : ~/Documents/wiki

wiki_root exists : True
```

---

## ディレクトリ構造の詳細

`wiki_init.py` が以下の構造を作成する:

```
<wiki_root>/
├── wiki/
│   ├── atoms/                ← 個別トピックページ（概念・用語・人物・製品・組織）
│   │   └── .gitkeep
│   ├── topics/               ← テーマ別まとめページ
│   │   └── .gitkeep
│   └── meta/
│       └── hot.md            ← 最近のコンテキストキャッシュ（直近20件）
├── SCHEMA.md                 ← このWikiの構造・規約定義（LLMと共に育てる）
├── index.md                  ← 全ページの目録（1ページ1行の箇条書き）
└── log.md                    ← 操作ログ（追記専用）
```

ソースデータは wiki_root 外で管理する（取り込み前に保全済みであることを前提とする）。
