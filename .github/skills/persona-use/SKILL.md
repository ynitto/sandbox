---
name: persona-use
description: "ユーザーのペルソナ（コミュニケーションスタイル・技術嗜好・専門領域）を蓄積・活用して応答をパーソナライズするスキル。「ペルソナをロード/初期化/リセット/一括更新して」で発動。"
metadata:
  version: 2.0.0
  tier: core
  category: meta
  config_script: scripts/init_persona.py
  tags:
    - persona
    - personalization
    - user-model
    - emotion-valuation
---

# persona-use（User Persona Use）

ユーザーのプロンプトを分析してペルソナを蓄積・活用するスキル。
認知科学における Emotion / Valuation（感情・価値判断）層を担う。

---

## ペルソナの構造

```
<persona_home>/
├── profile.md              ← コミュニケーションスタイル・全体プロファイル
├── preferences.md          ← 技術嗜好・ツール・フォーマット好み
├── expertise.md            ← 専門領域・技術スタック・経験レベル
└── YYYY-MM-DD-update.md    ← 当日の観察ログ（batch-update で管理ファイルに反映後に削除）
```

`persona_home` は `skill-registry.json` の `skill_configs.persona-use.persona_home` で指定する。

---

## 設定の確認

```bash
python {skill_home}/persona-use/scripts/persona_utils.py config
```

---

## 操作一覧

| 操作 | トリガー例 | スクリプト |
|------|-----------|-----------|
| **init** | 「ペルソナを初期化して」「persona-useをセットアップして」 | `init_persona.py` |
| **load** | 「ペルソナをロードして」「ユーザーの好みを読み込んで」「プロファイルを適用して」 | `load_persona.py` |
| **update** | セッション中に新たな観察を検出したとき（自律） | `update_persona.py --log "..."` |
| **batch-update** | 「ペルソナを一括更新して」「更新ファイルを反映して」 | `batch_update_persona.py` |
| **reset** | 「ペルソナをリセットして」「プロファイルをクリアして」 | `init_persona.py --reset` |

どのコマンドを実行しても、当日より古い `YYYY-MM-DD-update.md` ファイルは自動的に削除される。

---

## セッション開始時の自律動作

persona_home が設定されていれば、セッション開始直後に以下を実行してペルソナをコンテキストにロードする:

```bash
python {skill_home}/persona-use/scripts/load_persona.py
```

ロードしたペルソナに基づいて以下を調整する:
- 応答の詳細さ（専門家→簡潔、初学者→丁寧に）
- 使用言語（日本語/英語の好み）
- コード量・コメントの量
- 好みのフレームワーク・ツールを優先提案

---

## セッション中の自律更新

以下を検出したら `update_persona.py --log` で当日の観察ファイルに記録する:

| 観察シグナル | 更新対象 | 例 |
|---|---|---|
| 特定言語・FWへの言及 | `expertise.md` | 「Rustを使っている」 |
| フォーマットへの指摘 | `preferences.md` | 「箇条書きより文章で」 |
| ユーザーが修正した内容 | `preferences.md` | コードスタイルの修正 |
| 繰り返し出てくる操作 | `profile.md` | 毎回「日本語で」と指示 |
| 専門的な用語を使う | `expertise.md` | 「メモリリーク」「DI」など |

---

## init（初期化）

```bash
# 対話モード
python {skill_home}/persona-use/scripts/init_persona.py

# 非対話モード
python {skill_home}/persona-use/scripts/init_persona.py \
  --non-interactive \
  --persona-home ~/Documents/persona

# リセット（既存ファイルを上書き）
python {skill_home}/persona-use/scripts/init_persona.py --reset
```

設定例（`skill-registry.json` の `skill_configs.persona-use`）:
```json
{
  "skill_configs": {
    "persona-use": {
      "persona_home": "~/.claude/persona"
    }
  }
}
```

---

## load（ペルソナロード）

ペルソナをコンテキストに取り込み、以降の振る舞いに反映させる。
実行すると古い `YYYY-MM-DD-update.md` ファイルも削除される。

```bash
python {skill_home}/persona-use/scripts/load_persona.py
# 特定セクションのみ
python {skill_home}/persona-use/scripts/load_persona.py --section profile
python {skill_home}/persona-use/scripts/load_persona.py --section preferences
python {skill_home}/persona-use/scripts/load_persona.py --section expertise
```

---

## update（観察ログ追記）

エージェントが自律的に呼び出す。ユーザーへの表示は不要。
観察は当日の `YYYY-MM-DD-update.md` に追記される。

```bash
python {skill_home}/persona-use/scripts/update_persona.py \
  --log "観察内容（1〜2文で簡潔に）"
```

---

## batch-update（一括更新）

`YYYY-MM-DD-update.md` の観察ログを読み込み、管理ファイルに反映する。
反映後、処理済みの更新ファイルは削除される。

```bash
python {skill_home}/persona-use/scripts/batch_update_persona.py

# 削除せずに内容確認のみ
python {skill_home}/persona-use/scripts/batch_update_persona.py --dry-run
```

スクリプトが出力した観察ログをもとに、以下をエージェントが直接編集する:
- 観察に基づいて `profile.md` / `preferences.md` / `expertise.md` の該当セクションを更新
- 既存記述と矛盾する場合は上書き、補完できる場合は追記
